"""Own the Word — focused acquisition mode (translation-pair version, 2026-04-26).

A 60-90s ritual on ONE saved word. The learner does NOT have to recall personal
memories. Instead they translate two AI-generated Uzbek sentences (using the
target word) into English, by speaking or typing.

Three AI calls per session:

    Pre-flight             — generate two Uzbek sentences (easy + harder) using
                             the target word, plus reference English for each.
                             Done server-side at page render so the learner
                             lands on a fully populated screen.
    Turn 1 grade           — short Uzbek peer reaction to the learner's first
                             English translation. Reveals the reference.
    Turn 2 grade + close   — same shape, but also writes ceremony copy
                             (summary + keep_phrase + confidence bump 5-15).

Design choices:
    - All AI output in Uzbek. Learner answers in English (spoken or typed).
    - Tone: peer, warm, not teacher-correction.
    - Does NOT ask the learner about their own life or memories — see
      feedback_no_personal_thoughts (memory) for the rationale.
    - Translation tolerance: paraphrase fine, contractions fine, the AI
      grades "is this a faithful English rendering of the Uzbek?" not
      "does this match my reference word-for-word?".
    - Reuses _call_gemini from ai_grammar for the key chain + retries.
    - Uses Flash for speed.

Word selection: see pick_word_for_ownership — confidence 35-80 + has clip.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from learning.utils.ai_grammar import _call_gemini, _sanitize_learner_text
from learning.utils.ai_quiz import _word_used_in_reply

log = logging.getLogger(__name__)

_OWN_MODEL_NAME = "gemini-2.5-flash"
_OWN_FALLBACK_MODEL_NAME = "gemini-2.5-flash-lite"


# ── Word selection ──────────────────────────────────────────────────────────


def pick_word_for_ownership(user):
    """Pick the right WordNote for an ownership session.

    Priority order:
      1. Confidence in [35, 80] — "almost yours" sweet spot.
      2. Has a transcript (so we can play the original clip).
      3. Most recently due / least recently practiced.

    Falls back to any saved word with a transcript, then to any saved word.
    Returns None if the user has no saved words.
    """
    from vocab.models import WordNote

    base_qs = WordNote.objects.filter(user=user).exclude(word="").exclude(translation="", definition="")
    sweet = base_qs.filter(confidence__gte=35, confidence__lte=80, transcript__isnull=False).order_by(
        "next_review", "-last_reviewed_at", "-created_at"
    )
    note = sweet.first()
    if note:
        return note
    has_clip = base_qs.filter(transcript__isnull=False).order_by("next_review", "-last_reviewed_at", "-created_at")
    note = has_clip.first()
    if note:
        return note
    return base_qs.order_by("next_review", "-last_reviewed_at", "-created_at").first()


# ── Pre-flight: generate the translation pair ──────────────────────────────


def generate_translation_pair(
    *,
    word: str,
    translation: str,
    kind: str = "",
    category: str = "",
    source_line_en: str = "",
    user_level: str = "B1",
    interests: list[str] | None = None,
    user_name: str = "",
) -> dict:
    """Generate two Uzbek sentences (easy + harder) using the target word.

    Personalised via the learner's interests, name, and CEFR level so
    sentences feel like they're about the learner's world without ever
    asking the learner to introspect.

    Returns:
        {
            "easy":   { "uz": str, "en_reference": str },
            "harder": { "uz": str, "en_reference": str },
            "ai_unavailable": bool,
        }
    """
    source_block = ""
    if source_line_en:
        source_block = (
            f'\nOriginally saved from: "{source_line_en}"\n' "Same general theme is fine, but write fresh sentences.\n"
        )

    interests = (interests or [])[:6]
    interest_block = ""
    if interests:
        joined = ", ".join(interests)
        interest_block = (
            f"\nLearner interests (use ONE of these as the scenario subject "
            f"where it fits naturally — football match, a Marvel film, "
            f"cooking palov, etc — but never force it):\n  {joined}\n"
        )

    name_block = ""
    if user_name:
        name_block = (
            f"\nLearner's name (you may use it as a sentence subject in "
            f'the harder one for warmth, e.g. "{user_name} hech qachon..."):\n'
            f"  {user_name}\n"
        )

    prompt = f"""You are designing a focused acquisition session for ONE English
target word. The learner will translate two short Uzbek sentences into English.

Target word: "{word}"
Uzbek meaning: "{translation}"
Word kind: {kind or "unspecified"}
Category: {category or "general"}
Learner CEFR level: {user_level}
{source_block}{interest_block}{name_block}
Generate TWO Uzbek sentences that USE the meaning of "{word}". Each sentence
describes a concrete real-world scenario — NOT the learner's life, NOT
"imagine when you felt X." A normal observable situation.

Constraints:
  - Both sentences must be naturally translatable to English using "{word}".
  - The "easy" sentence: short (≤9 Uzbek words), straightforward usage,
    high-frequency surrounding vocabulary.
  - The "harder" sentence: 8-14 Uzbek words, slightly more nuanced —
    different subject, different tense, or a collocation that adds
    naturalness. Must still be {user_level}-appropriate.
  - When interests are provided, anchor at least ONE of the two sentences in
    one of those interests (the football team, the favourite film, the
    cooking dish, etc.) — concrete details, not generic "sport" / "movies".
  - Topics: everyday — home, friends, weather, food, hobbies, situations
    from films. AVOID politics, religion, anything sensitive.
  - The Uzbek must be natural Uzbek — not literal calques from English.
  - For each sentence, provide ONE natural English reference translation
    that USES "{word}" (any inflection). The reference is what a fluent
    speaker would say; the learner doesn't have to match it word-for-word.

Respond with ONLY a JSON object (no markdown, no commentary):
{{
  "easy":   {{ "uz": "...", "en_reference": "..." }},
  "harder": {{ "uz": "...", "en_reference": "..." }}
}}
"""
    data = _call_gemini(
        prompt,
        op="own_word.pair",
        model=_OWN_MODEL_NAME,
        fallback_model=_OWN_FALLBACK_MODEL_NAME,
    )
    if not data:
        # AI down — give the learner a workable fallback they can still translate.
        # Single-sentence pair using the bare word + its translation.
        return {
            "easy": {
                "uz": f"Bu so'z \"{translation}\" ma'nosini bildiradi.",
                "en_reference": f'This word means "{word}".',
            },
            "harder": {
                "uz": f'"{translation}" — bu "{word}" so\'zining ma\'nosi.',
                "en_reference": f'"{translation}" is the meaning of "{word}".',
            },
            "ai_unavailable": True,
        }

    def _coerce_pair(side):
        sub = data.get(side) or {}
        return {
            "uz": str(sub.get("uz", "")).strip(),
            "en_reference": str(sub.get("en_reference", "")).strip(),
        }

    easy = _coerce_pair("easy")
    harder = _coerce_pair("harder")
    if not easy["uz"] or not easy["en_reference"]:
        easy = {
            "uz": f'"{translation}" — siz buni eshitganmisiz?',
            "en_reference": f'Have you heard "{word}" before?',
        }
    if not harder["uz"] or not harder["en_reference"]:
        harder = {
            "uz": f'Bu so\'zni "{translation}" deb tarjima qilamiz.',
            "en_reference": f'We translate this word as "{word}".',
        }
    return {"easy": easy, "harder": harder, "ai_unavailable": False}


# ── Turn-grade: instant server-side scoring (no AI call) ────────────────


# Function/closed-class words we discount when computing word overlap. These
# are usually identical regardless of translation quality, so they shouldn't
# be the dominant signal.
_FUNC = {
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "it",
    "me",
    "my",
    "your",
    "his",
    "her",
    "our",
    "their",
    "us",
    "him",
    "them",
    "a",
    "an",
    "the",
    "to",
    "of",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "am",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "this",
    "that",
    "these",
    "those",
    "there",
    "and",
    "or",
    "but",
    "if",
    "not",
    "no",
    "yes",
    "so",
    "just",
    "very",
    "up",
    "down",
    "out",
}


def _normalize_for_grade(text: str) -> list[str]:
    """Lowercase, strip punctuation, expand a few common contractions, split."""
    import re

    t = (text or "").lower()
    contractions = {
        "can't": "cannot",
        "won't": "will not",
        "n't": " not",
        "'re": " are",
        "'ve": " have",
        "'ll": " will",
        "'d": " would",
        "'m": " am",
        "'s": " is",
    }
    for k, v in contractions.items():
        t = t.replace(k, v)
    t = re.sub(r"[^a-z'\s]", " ", t)
    return [w for w in re.split(r"\s+", t) if w]


def _stem4(w: str) -> str:
    return w[:4] if len(w) >= 4 else w


def grade_translation(
    *,
    word: str,
    reference_en: str,
    user_translation_en: str,
    user_level: str = "B1",
) -> dict:
    """AI translation grader with naturalness as its own axis.

    Returns a 0-3 score where:
      0 — wrong / off-topic
      1 — meaning roughly there but key errors
      2 — CORRECT but NOT NATURAL (the band the user explicitly named —
          translation works but sounds textbook / non-native)
      3 — correct AND natural (native-sounding)

    Plus two booleans (is_correct, is_natural), a peer-tone Uzbek feedback
    line, and an optional suggestion_uz when the answer is correct-but-
    unnatural — a tiny "a more natural way" hint.

    Server-side gate first: empty or <3 words → score 0, no AI call.
    """
    text = (user_translation_en or "").strip()
    word_used = bool(text) and _word_used_in_reply(word, text)

    if not text or len(text.split()) < 3:
        return {
            "score": 0,
            "is_correct": False,
            "is_natural": False,
            "word_used": False,
            "feedback_uz": "Biroz qisqa bo'ldi — to'liq gap aytib ko'ring.",
            "suggestion_uz": "",
            "ai_unavailable": False,
        }

    prompt = f"""You are grading a learner's English translation of an Uzbek
sentence. The target word being practiced is "{word}".

Reference English (one fluent rendering — not the only correct one):
  "{reference_en}"

Learner's English translation:
<translation>
{_sanitize_learner_text(text)}
</translation>

Grade on TWO independent axes:

  is_correct (bool) — Does it translate the meaning of the source faithfully?
    Be GENEROUS with paraphrase, contractions, word order. Different
    synonyms / phrasings around the target are fine.

  is_natural (bool) — Does it sound like something a fluent native English
    speaker would actually say in this context? Robotic / textbook /
    word-by-word translations score is_natural=false even when correct.
    Examples of correct-but-unnatural:
      - "I am wanting some water" (technically correct, not natural)
      - "It matters to my heart very much" (correct meaning, awkward)
    Examples of correct-and-natural:
      - "Just give me a sec." (idiomatic, real)
      - "That actually really matters to me." (natural emphasis pattern)

Map to a 0-3 score:
  3 — is_correct AND is_natural
  2 — is_correct but NOT is_natural    ← the named "good but not natural" band
  1 — meaning roughly there with key errors (is_correct=false but close)
  0 — wrong / off-topic

feedback_uz: ONE short Uzbek peer line (max 14 words). Warm, NOT teacher-
correction. The line should match the score:
  3: "Tabiiy chiqdi — xuddi mahalliy odam aytgandek."
  2: "Ma'no to'g'ri, lekin biroz tarjima qilingandek eshitiladi."
  1: "Ma'noga yaqin, lekin asosiy bir narsa tushib qoldi."
  0: "Bu mazmunni qamrab olmadi — qaytadan o'qib ko'raylik."
NEVER write English in feedback_uz.

suggestion_uz: ONLY for score 2 (correct but not natural). ONE Uzbek line
(max 16 words) suggesting how a native would phrase it, possibly quoting
a short English fragment in quotes. Empty string for score 0/1/3.
Examples:
  - "Tabiiyroq: \"that really matters to me\" desangiz qulayroq."
  - "Mahalliy odam: \"I'm pulling out my keys\" deydi — \"taking out\" ham yaxshi."

Respond with ONLY a JSON object:
{{
  "is_correct":    true | false,
  "is_natural":    true | false,
  "score":         0 | 1 | 2 | 3,
  "feedback_uz":   "...one Uzbek line...",
  "suggestion_uz": "...Uzbek line for score=2 only, else empty..."
}}
"""
    data = _call_gemini(
        prompt,
        op="own_word.grade",
        model=_OWN_MODEL_NAME,
        fallback_model=_OWN_FALLBACK_MODEL_NAME,
    )
    if not data:
        # AI down — fall back to deterministic word-overlap grading.
        # We can't judge naturalness without AI, so we give correct-only
        # feedback and explicitly mark is_natural as null-ish (False).
        return _grade_translation_offline(
            word=word,
            reference_en=reference_en,
            user_translation_en=text,
            word_used=word_used,
        )

    # Coerce + clamp the AI's response.
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(3, score))
    is_correct = bool(data.get("is_correct"))
    is_natural = bool(data.get("is_natural"))

    return {
        "score": score,
        "is_correct": is_correct,
        "is_natural": is_natural,
        "word_used": word_used,
        "feedback_uz": str(data.get("feedback_uz", "")).strip() or "Yaxshi urinish.",
        "suggestion_uz": str(data.get("suggestion_uz", "")).strip(),
        "ai_unavailable": False,
    }


def _grade_translation_offline(
    *,
    word: str,
    reference_en: str,
    user_translation_en: str,
    word_used: bool,
) -> dict:
    """Fallback grader when AI is unavailable. No naturalness signal."""
    text = (user_translation_en or "").strip()
    user_tokens = _normalize_for_grade(text)
    ref_tokens = _normalize_for_grade(reference_en or "")
    ref_content = [w for w in ref_tokens if w not in _FUNC and len(w) >= 3] or ref_tokens
    user_stems = {_stem4(w) for w in user_tokens}
    overlap = sum(1 for w in ref_content if _stem4(w) in user_stems) / len(ref_content) if ref_content else 0.0
    if overlap >= 0.65:
        score, is_correct = 2, True
        fb = "Yaxshi tarjima — saqladik (AI band, tabiiyligini hozir baholab bo'lmadi)."
    elif overlap >= 0.45:
        score, is_correct = 1, False
        fb = "Mazmun yaqin — keyingi gal aniqroq ifoda qiling."
    else:
        score, is_correct = 0, False
        fb = f'Mazmun yetib bormadi — "{word}" iborasini ishlating.'
    return {
        "score": score,
        "is_correct": is_correct,
        "is_natural": False,
        "word_used": word_used,
        "feedback_uz": fb,
        "suggestion_uz": "",
        "ai_unavailable": True,
    }


# Kept for callers that still want an AI peer-tone grade. Not used by the
# default Own the Word flow anymore (latency cost too high) but available
# for analytics / A-B testing if we ever want richer feedback offline.
def grade_translation_ai(
    *,
    word: str,
    reference_en: str,
    user_translation_en: str,
    user_level: str = "B1",
) -> dict:
    """Grade ONE translation. Returns score + Uzbek peer feedback.

    Server-side gate first: if the user's text is empty / under 3 words /
    doesn't contain the target word, short-circuit (score 0, no AI call).

    AI grade beyond that:
      2 = faithful translation, target word used naturally
      1 = the gist is there but a meaningful piece is off (wrong tense,
          missing subject, target word used awkwardly)
      0 = nonsense or didn't really translate it

    Returns:
        {
            "score":         0 | 1 | 2,
            "word_used":     bool,
            "feedback_uz":   str,    # one short peer line
            "ai_unavailable": bool,
        }
    """
    text = (user_translation_en or "").strip()

    if not text or len(text.split()) < 3:
        return {
            "score": 0,
            "word_used": False,
            "feedback_uz": ("Biroz qisqaroq bo'ldi — to'liq gap aytishga harakat qiling."),
            "ai_unavailable": False,
        }

    # Word-used judgment is delegated to the AI. Multi-word phrases with
    # placeholder pronouns (e.g. "it matters to me" → "his help matters to
    # me") fail naive gates but are valid in the reference; the AI sees the
    # reference and judges faithfulness directly.
    word_used = bool(text) and _word_used_in_reply(word, text)

    prompt = f"""You are checking a learner's English translation of an Uzbek
sentence in a focused vocab session. The target word is "{word}".

Reference English (one fluent rendering, not the only correct one):
  "{reference_en}"

Learner's English translation:
<translation>
{_sanitize_learner_text(text)}
</translation>

Grade on a 0-2 scale. Be GENEROUS with paraphrase, contractions, word order:

  2 — Faithful meaning, target word "{word}" used naturally. Different
      synonyms / phrasing for surrounding words is fine.
  1 — Meaning roughly there but something is off — wrong tense, missing
      a key element, awkward use of "{word}".
  0 — Mistranslation, nonsense, or doesn't address the source.

feedback_uz — ONE Uzbek peer-tone line (max 14 words). Examples:
    score 2: "Zo'r, juda tabiiy chiqdi."
    score 2: "Yaxshi tarjima — fluent ko'rinadi."
    score 1: "Ma'no to'g'ri, faqat zamon biroz boshqacha edi."
    score 1: "Kichik bir tafsilot tushib qoldi — qaytadan o'qib ko'ring."
    score 0: "Bu mazmuni bilan mos kelmadi — yana ko'rib chiqaylik."
NEVER write English in feedback_uz. Warm, encouraging, NOT teacher-correction.

Respond with ONLY a JSON object:
{{
  "score":       0 | 1 | 2,
  "feedback_uz": "..."
}}
"""
    data = _call_gemini(
        prompt,
        op="own_word.grade",
        model=_OWN_MODEL_NAME,
        fallback_model=_OWN_FALLBACK_MODEL_NAME,
    )
    if not data:
        # AI down + server gates passed → conservative score 2 (the word was
        # used in a reasonable-length sentence; better to err generous).
        return {
            "score": 2,
            "word_used": True,
            "feedback_uz": "Yaxshi tarjima — saqladik.",
            "ai_unavailable": True,
        }

    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(2, score))

    return {
        "score": score,
        "word_used": True,
        "feedback_uz": str(data.get("feedback_uz", "")).strip() or "Yaxshi urinish.",
        "ai_unavailable": False,
    }


# ── Close: combine both turn scores + ceremony copy ────────────────────────


def close_session(
    *,
    word: str,
    translation: str,
    score_1: int,
    score_2: int,
    user_translation_1: str = "",
    user_translation_2: str = "",
    reference_1: str = "",
    reference_2: str = "",
    kind: str = "",
    category: str = "",
    user_level: str = "B1",
) -> dict:
    """Final close. Combines the two turn-grades (each 0-3) into ceremony copy.

    Final score = round(avg(score_1, score_2)). Maps roughly:
      sum 0-1 → 0 (revisit)
      sum 2-3 → 1 (close)
      sum 4-5 → 2 (owned)
      sum 6   → 3 (fully owned, both natural)

    Confidence bump derived from final score; AI writes the ceremony copy
    (summary_uz + keep_phrase_uz) — no re-grading.

    Returns:
        {
            "final_score":     int,    # 0-3
            "confidence_bump": int,    # 5-15
            "summary_uz":      str,
            "keep_phrase_uz":  str,
            "owned":           bool,   # final_score >= 2
            "ai_unavailable":  bool,
        }
    """
    s1 = max(0, min(3, int(score_1)))
    s2 = max(0, min(3, int(score_2)))
    total = s1 + s2
    if total <= 1:
        final_score = 0
    elif total <= 3:
        final_score = 1
    elif total <= 5:
        final_score = 2
    else:
        final_score = 3

    # Bump table — final_score → confidence_bump.
    bump_table = {0: 5, 1: 7, 2: 11, 3: 14}
    bump = bump_table[final_score]

    prompt = f"""You are writing the closing line for an acquisition session for
the English word "{word}" (Uzbek: "{translation}").

The learner translated two Uzbek sentences. The final score is {final_score}/3
on this rubric:
  3 — both translations faithful, word used naturally in both
  2 — overall faithful, word used in at least one with minor slips
  1 — partial — the meaning was there but the word was missed or misused
  0 — translations didn't land

Their translations (English, possibly transcribed from speech):
  Reference 1: "{reference_1}"
  Theirs 1:    "{_sanitize_learner_text(user_translation_1)}"
  Reference 2: "{reference_2}"
  Theirs 2:    "{_sanitize_learner_text(user_translation_2)}"

Write TWO Uzbek lines:

  summary_uz: ONE sentence (max 16 Uzbek words) acknowledging what they did.
    Match the score energy honestly — DON'T fake celebrate a 0 or 1.
    Examples by score:
      3: "Ikki tarjimani ham aniq va ravon qilib oldingiz — zo'r ish."
      2: "Yaxshi tarjima qildingiz — \"{word}\" tilingizga yaqin."
      1: "Mazmun yaqin, lekin \"{word}\" so'zini yana mashq qilamiz."
      0: "Hali bu so'z bilan biroz vaqt o'tkazish kerak — qaytib kelamiz."

  keep_phrase_uz: SHORT Uzbek line (max 6 words) for the ceremony.
    Examples by score:
      3: "\"{word}\" — endi sizniki."
      2: "Yaxshi tutdingiz — eslab qoling."
      1: "Yana bir bor mashq qilamiz."
      0: "Davom etamiz — har gal yaqinlashasiz."
    Match tone to the score. NEVER write English in either line.

Respond with ONLY a JSON object:
{{
  "summary_uz":     "...",
  "keep_phrase_uz": "..."
}}
"""
    data = _call_gemini(
        prompt,
        op="own_word.close",
        model=_OWN_MODEL_NAME,
        fallback_model=_OWN_FALLBACK_MODEL_NAME,
    )
    if not data:
        # Static fallbacks per score — same energy levels as the AI prompt asks for.
        fallback = {
            3: ("Ikki tarjimani ham yaxshi qildingiz.", f'"{word}" — endi sizniki.'),
            2: ("Yaxshi tarjima — saqlandi.", "Yaxshi tutdingiz."),
            1: (f'"{word}" so\'zini yana mashq qilamiz.', "Yana bir bor."),
            0: ("Davom etamiz — har gal yaqinlashasiz.", "Yaqinda qaytamiz."),
        }
        s, k = fallback[final_score]
        return {
            "final_score": final_score,
            "confidence_bump": bump,
            "summary_uz": s,
            "keep_phrase_uz": k,
            "owned": final_score >= 2,
            "ai_unavailable": True,
        }

    return {
        "final_score": final_score,
        "confidence_bump": bump,
        "summary_uz": str(data.get("summary_uz", "")).strip() or "Yaxshi mashg'ulot bo'ldi.",
        "keep_phrase_uz": str(data.get("keep_phrase_uz", "")).strip() or f'"{word}" — sizniki.',
        "owned": final_score >= 2,
        "ai_unavailable": False,
    }


# ── Per-day cap ──────────────────────────────────────────────────────────────


_DAILY_CAP = 2


def own_sessions_today(user) -> int:
    """Distinct words touched in the rolling-24h window via Own the Word."""
    from quiz.models import QuizAttempt

    cutoff = timezone.now() - timezone.timedelta(hours=24)
    return (
        QuizAttempt.objects.filter(user=user, quiz_type="own_the_word", created_at__gte=cutoff)
        .values("note_id")
        .distinct()
        .count()
    )


def daily_cap_remaining(user) -> int:
    return max(0, _DAILY_CAP - own_sessions_today(user))
