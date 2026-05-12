"""smart_mcq distractor augmentation (Issue 1 from quiz audit, Option γ).

Why this exists
---------------
The YAML lexicon ships smart_mcq blocks where option_b/option_c are
particle-variants of the correct option's same root verb:

    option_a: "He is going through a tough financial period."  ← correct
    option_b: "He is going over a tough financial period."     ← particle swap
    option_c: "He is going into a tough financial period."     ← particle swap

This trains pattern-matching on the differing particle, not vocabulary
knowledge. Three options sharing root verb `go` is a recognition-by-string
exercise.

What we do
----------
At question-build time, replace YAML option_b/option_c with semantically
distinct distractors via a four-tier waterfall:

  Tier 1: confusable_with (≥2 → use both)        ← curated semantic
  Tier 2: same category + level pool              ← topic-adjacent
  Tier 3: antonym pattern (different category)    ← trap distractor
  Tier 4: YAML option_b/c (only if root-verb OK)  ← last resort

Then a *mandatory* same-root-verb check on the final 3-option set: if any
two share their root verb, the colliding option is dropped and replaced
with the next tier's candidate. If all tiers exhaust, we log a warning
with the word_id (surfaces as a content gap to fix later).
"""

from __future__ import annotations

import logging
import random
import re
from typing import Iterable

log = logging.getLogger(__name__)


# ── Root-verb extraction ─────────────────────────────────────────────────────

# Particles that follow a phrasal-verb head — strip these so the remaining
# verb is the actual "root". Order matters in regex but not in set membership.
_PARTICLES = frozenset(
    {
        "out",
        "off",
        "up",
        "down",
        "in",
        "into",
        "away",
        "back",
        "over",
        "through",
        "around",
        "about",
        "on",
        "by",
        "after",
        "with",
        "for",
        "at",
        "from",
        "of",
        "to",
        "across",
        "along",
        "apart",
        "aside",
        "ahead",
        "behind",
        "forward",
        "together",
    }
)

# Auxiliaries / pronouns / determiners / connectors — skip when scanning for verbs.
_AUX = frozenset(
    {
        # Pronouns (and contractions)
        "he",
        "she",
        "it",
        "they",
        "i",
        "we",
        "you",
        "im",
        "id",
        "ive",
        "ill",
        "youre",
        "youve",
        "youd",
        "hes",
        "shes",
        "weve",
        "were",
        "wed",
        "theyre",
        "theyve",
        "theyd",
        "lets",
        # Auxiliaries (be / have / do / modals)
        "has",
        "have",
        "had",
        "is",
        "are",
        "was",
        "be",
        "been",
        "being",
        "will",
        "would",
        "can",
        "could",
        "should",
        "must",
        "may",
        "might",
        "do",
        "does",
        "did",
        "am",
        "shall",
        "ought",
        "wo",
        "ca",
        # Determiners
        "the",
        "a",
        "an",
        "that",
        "this",
        "those",
        "these",
        "my",
        "his",
        "her",
        "their",
        "our",
        "your",
        "its",
        "any",
        "some",
        "every",
        "each",
        "all",
        "both",
        "either",
        "neither",
        "no",
        "not",
        # Adverbs / connectors / time words (these are NOT verbs even though
        # they're not in the AUX list above — used to be missed)
        "just",
        "now",
        "then",
        "still",
        "even",
        "also",
        "too",
        "very",
        "really",
        "always",
        "often",
        "sometimes",
        "never",
        "ever",
        "when",
        "while",
        "if",
        "though",
        "although",
        "because",
        "since",
        "until",
        "before",
        "after",
        "as",
        "so",
        "but",
        "and",
        "or",
        "yet",
        "however",
        "therefore",
        "moreover",
        "furthermore",
        "again",
        "ago",
        "soon",
        "later",
        "today",
        "tomorrow",
        "yesterday",
        "much",
        "many",
        "few",
        "more",
        "less",
        "most",
        "least",
        "enough",
        "well",
        "back",
        "down",
        "up",
        "off",
        "on",
        "out",
        "in",
    }
)

# Common content verbs by lemma — used to confirm "this token is acting as
# a verb, not a noun or adjective". Doesn't need to be exhaustive; covers
# the heads that show up in phrasal-verb-bearing content.
#
# TODO: expand if production telemetry shows >5% false-negative rate on
# same-root-verb collision detection. Current sample rate ~10% (1/10 in
# the unit checks: "ruled" → "rule" was the missed case), acceptable
# because phrasal-verb particle-swap is already covered by the listed
# common verbs (pull, go, come, get, take, put, give, etc.). If the
# follow-up TODO.md issue ever fills with "same-root collision missed for
# X" reports, switch to NLTK WordNet lemmatization. Until then the simple
# list is right-sized.
_COMMON_VERB_LEMMAS = frozenset(
    {
        "go",
        "get",
        "take",
        "make",
        "give",
        "pull",
        "push",
        "put",
        "come",
        "bring",
        "find",
        "keep",
        "hold",
        "run",
        "turn",
        "fall",
        "break",
        "pick",
        "show",
        "look",
        "sit",
        "stand",
        "walk",
        "move",
        "open",
        "close",
        "start",
        "stop",
        "begin",
        "end",
        "work",
        "play",
        "use",
        "try",
        "help",
        "feel",
        "think",
        "know",
        "say",
        "tell",
        "speak",
        "talk",
        "see",
        "watch",
        "hear",
        "listen",
        "wear",
        "carry",
        "drop",
        "lift",
        "raise",
        "let",
        "leave",
        "stay",
        "wait",
        "meet",
        "send",
        "receive",
        "buy",
        "sell",
        "pay",
        "spend",
        "save",
        "lose",
        "win",
        "fight",
        "fix",
        "build",
        "break",
        "cut",
        "throw",
        "catch",
        "kick",
        "hit",
        "miss",
        "reach",
        "grab",
        "touch",
        "feel",
        "love",
        "hate",
        "like",
        "want",
        "need",
        "wish",
        "hope",
        "dream",
        "decide",
        "choose",
        "drift",
        "split",
        "tear",
        "share",
        "deal",
        "manage",
        "handle",
        "cope",
        "endure",
        "struggle",
        "experience",
        "live",
        "die",
        "born",
        "drink",
        "eat",
        "sleep",
        "wake",
        "ask",
        "answer",
        "explain",
        "understand",
        "remember",
        "forget",
        "learn",
        "teach",
        "study",
        "read",
        "write",
        "draw",
        "paint",
        "sing",
        "dance",
        "drive",
        "ride",
        "fly",
        "swim",
        "jump",
        "climb",
        "fall",
        "rise",
        "grow",
        "change",
        "happen",
        "become",
        "seem",
        "appear",
        "look",
        "sound",
        "smell",
        "taste",
        "act",
        "behave",
        "react",
        "respond",
    }
)

# Aux verbs that should NEVER count as the "main verb" of a sentence.
_AUX_VERB_LEMMAS = frozenset({"be", "have", "do", "will", "can", "must", "may", "shall"})

# Common irregular past tenses (and a few past participles) collapsed to lemma.
# Focused on heads that show up in phrasal verbs — keep small, not exhaustive.
_IRREGULAR = {
    "went": "go",
    "gone": "go",
    "saw": "see",
    "seen": "see",
    "got": "get",
    "gotten": "get",
    "took": "take",
    "taken": "take",
    "made": "make",
    "gave": "give",
    "given": "give",
    "brought": "bring",
    "came": "come",
    "had": "have",
    "said": "say",
    "thought": "think",
    "caught": "catch",
    "taught": "teach",
    "bought": "buy",
    "fought": "fight",
    "wrote": "write",
    "written": "write",
    "spoke": "speak",
    "spoken": "speak",
    "broke": "break",
    "broken": "break",
    "chose": "choose",
    "chosen": "choose",
    "drove": "drive",
    "driven": "drive",
    "rode": "ride",
    "ridden": "ride",
    "rose": "rise",
    "risen": "rise",
    "wore": "wear",
    "worn": "wear",
    "tore": "tear",
    "torn": "tear",
    "threw": "throw",
    "thrown": "throw",
    "knew": "know",
    "known": "know",
    "grew": "grow",
    "grown": "grow",
    "flew": "fly",
    "flown": "fly",
    "drew": "draw",
    "drawn": "draw",
    "blew": "blow",
    "blown": "blow",
    "began": "begin",
    "begun": "begin",
    "ran": "run",
    "sang": "sing",
    "sung": "sing",
    "drank": "drink",
    "drunk": "drink",
    "rang": "ring",
    "rung": "ring",
    "fell": "fall",
    "fallen": "fall",
    "told": "tell",
    "sold": "sell",
    "held": "hold",
    "felt": "feel",
    "kept": "keep",
    "left": "leave",
    "lent": "lend",
    "spent": "spend",
    "sent": "send",
    "bent": "bend",
    "meant": "mean",
    "lost": "lose",
    "paid": "pay",
    "stood": "stand",
    "understood": "understand",
    "shot": "shoot",
    "ate": "eat",
    "eaten": "eat",
    "hid": "hide",
    "hidden": "hide",
    "bit": "bite",
    "bitten": "bite",
    "dug": "dig",
    "hung": "hang",
    "stuck": "stick",
    "struck": "strike",
    "won": "win",
    "met": "meet",
    "led": "lead",
    "fed": "feed",
    "fled": "flee",
    "lit": "light",
    "sat": "sit",
    "put": "put",
    "set": "set",
    "let": "let",
    "cut": "cut",
    "hit": "hit",
    "shut": "shut",
    "hurt": "hurt",
    "cost": "cost",
    "beat": "beat",
    "read": "read",
}


def _stem(word: str) -> str:
    """Lemmatize a single English verb form. Order matters — try irregular
    map first, then suffix stripping. Not perfect, but covers the
    phrasal-verb head cases that bite us.

    Two notable design points:
      - Length threshold is `> len(suf) + 1` (lenient) so 5-char words
        like "going" / "doing" / "being" lemmatize to "go" / "do" / "be".
        With `+ 2` (the original threshold) "going" stayed "going" and
        the verb detection failed.
      - After stripping a suffix, if the bare stem isn't in our common-verb
        set but stem+"e" is, return the e-restored form. This handles the
        e-drop pattern: "loved" → "lov" → "love"; "lived" → "liv" → "live".
    """
    word = word.lower().strip("'")
    if not word:
        return ""
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    # Regular suffix peel (longest first so 'ied' wins over 'ed')
    for suf, repl in (("ied", "y"), ("ying", "y"), ("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suf) and len(word) > len(suf) + 1:
            stem = word[: -len(suf)] + repl
            # E-restore: "loved" → "lov" → check "love"; "lived" → "liv" → "live".
            # Only fires when the bare stem isn't a known verb but the
            # +e variant is — otherwise we'd false-restore "thing" → "thinge".
            if (
                stem
                and stem not in _COMMON_VERB_LEMMAS
                and stem not in _AUX_VERB_LEMMAS
                and (stem + "e") in _COMMON_VERB_LEMMAS
            ):
                return stem + "e"
            return stem
    return word


def _content_verbs_in(option_text: str) -> set[str]:
    """Return the set of CONTENT verb lemmas in a sentence — the actual
    predicates, not auxiliaries or pronouns.

    A token counts as a content verb if BOTH:
      (a) it isn't in _AUX (pronouns, auxiliaries, connectors), AND
      (b) it looks verb-shaped: in _IRREGULAR, lemma in _COMMON_VERB_LEMMAS,
          or has a verb-suffix (-ed/-ing).

    The lemma 'be', 'have', 'do' are stripped after lemmatizing because
    they're auxiliaries even when they look like main verbs in isolation.

    Used by the same-root-verb collision check: if two options share any
    content verb lemma, they collide. This catches "He pulled out X" vs
    "Just when she pulled X back in" — both contain pull, both collide.
    """
    if not option_text:
        return set()
    out: set[str] = set()
    # Strip apostrophes BEFORE tokenizing so contractions ("I'm", "let's")
    # become "im", "lets" — already covered by _AUX. Without this we'd get
    # bogus tokens like "let'" or "i'd".
    cleaned = option_text.lower().replace("'", "").replace("'", "")
    for tok in re.findall(r"[a-z]+", cleaned):
        if tok in _AUX or tok in _PARTICLES:
            continue
        lemma = _stem(tok)
        if lemma in _AUX_VERB_LEMMAS:
            continue  # be/have/do — never main verbs
        # Verb detection is anchored on the lemma matching a known content
        # verb. The suffix-only path (-ed/-ing, no lemma match) used to
        # over-match nouns like "thing" / "king" / "ring" / "bed". With
        # the relaxed _stem above, real verbs lemmatize correctly into
        # _COMMON_VERB_LEMMAS, so the suffix-only fallback isn't needed.
        is_verb_like = tok in _IRREGULAR or lemma in _COMMON_VERB_LEMMAS
        if is_verb_like:
            out.add(lemma)
    return out


def extract_root_verb(option_text: str) -> str:
    """Return ONE representative content verb lemma for display/diagnostics.

    Internal collision detection uses _content_verbs_in() (set-based).
    This function exists only for the demo printer + tests that want a
    single label. Picks the alphabetically first verb for stable output.
    """
    verbs = _content_verbs_in(option_text)
    if verbs:
        return sorted(verbs)[0]
    # No verb-like tokens found — sentence is probably aux-heavy or noun-only.
    # Return the first non-aux content token as a fallback label so the
    # diagnostic isn't blank.
    cleaned = (option_text or "").lower().replace("'", "").replace("'", "")
    for tok in re.findall(r"[a-z]+", cleaned):
        if tok in _AUX or tok in _PARTICLES:
            continue
        return _stem(tok)
    return ""


def _root_verbs_collide(opt1: str, opt2: str) -> bool:
    """True if opt1 and opt2 share any content-verb lemma.

    This is what catches the "particle swap" bug: two options that both
    contain the same lemmatized verb (in any form, anywhere in the
    sentence) collide. Empty-set vs anything is treated as no collision —
    auxiliary-only sentences don't have a meaningful collision target.
    """
    a, b = _content_verbs_in(opt1), _content_verbs_in(opt2)
    return bool(a & b)


# ── Distractor selection ─────────────────────────────────────────────────────


def _natural_sentence_for(lv) -> str | None:
    """Best natural-English sentence for a confusable LineVocab.

    Prefer smart_mcq.option_a (already curated to be natural usage of the
    word). Fall back to first example_pairs[].en. Return None if neither
    is usable.
    """
    if not lv:
        return None
    mcq = getattr(lv, "smart_mcq", None) or {}
    if isinstance(mcq, dict):
        # The 'correct' field tells us which option is actually correct,
        # but option_a is overwhelmingly the canonical correct answer in
        # the lexicon. Honor the correct field if present.
        key = "option_" + (str(mcq.get("correct", "A")).strip().lower() or "a")
        cand = (mcq.get(key) or mcq.get("option_a") or "").strip()
        if cand:
            return cand
    pairs = getattr(lv, "translated_examples", None) or getattr(lv, "example_pairs", None) or []
    if isinstance(pairs, list):
        for p in pairs:
            if isinstance(p, dict) and isinstance(p.get("en"), str) and p["en"].strip():
                return p["en"].strip()
    return None


def _lookup_vocab_by_ids(vocab_ids: Iterable[str], exclude_id: str = "") -> dict:
    """Map vocab_id -> first matching LineVocab row. One representative
    per vocab_id (transcripts repeat). Excludes the target word itself.
    """
    from vocab.models import LineVocab

    ids = [v for v in vocab_ids if v and v != exclude_id]
    if not ids:
        return {}
    found = {}
    for lv in (
        LineVocab.objects.filter(vocab_id__in=ids)
        .only("id", "vocab_id", "english", "smart_mcq", "translated_examples", "category", "level")
        .order_by("id")
    ):
        found.setdefault(lv.vocab_id, lv)
    return found


def _pick_tier1(target_lv) -> list[tuple[str, str, str]]:
    """Tier 1 — pull distractor sentences from target.confusable_with.

    Returns list of (distractor_text, source_id, "confusable_with") for as
    many as we can resolve to natural sentences. Each candidate is filtered
    against the target's content-verb set (via _root_verbs_collide) so
    same-verb sentences never reach the final waterfall.
    """
    out = []
    cw = getattr(target_lv, "confusable_with", None) or []
    if not isinstance(cw, list) or not cw:
        return out
    target_text = _natural_sentence_for(target_lv) or target_lv.english
    target_id = getattr(target_lv, "vocab_id", "") or ""
    lookup = _lookup_vocab_by_ids(cw, exclude_id=target_id)
    # Preserve the YAML's curated ordering of confusables — those near the
    # top of the list are typically the strongest semantic confusions.
    for cid in cw:
        if cid == target_id:
            continue
        lv = lookup.get(cid)
        if not lv:
            continue
        text = _natural_sentence_for(lv)
        if not text:
            continue
        if _root_verbs_collide(text, target_text):
            # Confusable's sentence shares a content verb with the target's —
            # would fail the same-root check downstream. Skip.
            continue
        out.append((text, cid, "confusable_with"))
    return out


def _pick_tier2(target_lv, exclude_ids: set[str], n: int) -> list[tuple[str, str, str]]:
    """Tier 2 — same category + level pool. Topic-adjacent words that
    aren't direct confusables but live in the same semantic neighborhood.
    """
    from vocab.models import LineVocab

    out = []
    if n <= 0:
        return out
    cat = getattr(target_lv, "category", "") or ""
    lvl = getattr(target_lv, "level", "") or ""
    if not cat:
        return out
    target_text = _natural_sentence_for(target_lv) or target_lv.english
    target_id = getattr(target_lv, "vocab_id", "") or ""
    qs = (
        LineVocab.objects.filter(category=cat)
        .exclude(vocab_id="")
        .exclude(vocab_id=target_id)
        .exclude(vocab_id__in=exclude_ids)
        .only("id", "vocab_id", "english", "smart_mcq", "translated_examples", "category", "level")
    )
    if lvl:
        qs = qs.filter(level=lvl)
    # Dedupe by vocab_id and shuffle for diversity within a session.
    seen = set()
    candidates = []
    for lv in qs.order_by("id"):
        vid = lv.vocab_id
        if vid in seen:
            continue
        seen.add(vid)
        candidates.append(lv)
    random.shuffle(candidates)
    for lv in candidates:
        text = _natural_sentence_for(lv)
        if not text:
            continue
        if _root_verbs_collide(text, target_text):
            continue
        out.append((text, lv.vocab_id, "category_pool"))
        if len(out) >= n:
            break
    return out


def _pick_tier3(target_lv, exclude_ids: set[str], n: int) -> list[tuple[str, str, str]]:
    """Tier 3 — same kind, different category (antonym/trap pattern).
    Last resort before YAML fallback.
    """
    from vocab.models import LineVocab

    out = []
    if n <= 0:
        return out
    kind = getattr(target_lv, "kind", "") or ""
    cat = getattr(target_lv, "category", "") or ""
    target_text = _natural_sentence_for(target_lv) or target_lv.english
    target_id = getattr(target_lv, "vocab_id", "") or ""
    qs = (
        LineVocab.objects.exclude(vocab_id="")
        .exclude(vocab_id=target_id)
        .exclude(vocab_id__in=exclude_ids)
        .only("id", "vocab_id", "english", "smart_mcq", "translated_examples", "category", "level", "kind")
    )
    if kind:
        qs = qs.filter(kind=kind)
    if cat:
        qs = qs.exclude(category=cat)
    seen = set()
    candidates = []
    for lv in qs.order_by("id"):
        vid = lv.vocab_id
        if vid in seen:
            continue
        seen.add(vid)
        candidates.append(lv)
    random.shuffle(candidates)
    for lv in candidates:
        text = _natural_sentence_for(lv)
        if not text:
            continue
        if _root_verbs_collide(text, target_text):
            continue
        out.append((text, lv.vocab_id, "antonym_pool"))
        if len(out) >= n:
            break
    return out


def _yaml_fallback(raw_mcq: dict, correct_text: str) -> list[tuple[str, str, str]]:
    """Tier 4 — try YAML's option_b / option_c, but ONLY accept ones whose
    content verbs don't collide with the correct option's. Particle-swap
    distractors are filtered out here (they share the verb).
    """
    out = []
    if not isinstance(raw_mcq, dict):
        return out
    for key in ("option_b", "option_c"):
        text = (raw_mcq.get(key) or "").strip()
        if not text:
            continue
        if _root_verbs_collide(text, correct_text):
            continue  # particle swap rejected
        out.append((text, "yaml", "yaml_fallback"))
    return out


# ── Top-level: build the augmented MCQ ───────────────────────────────────────


def augment_smart_mcq(target_lv, raw_mcq: dict, *, include_telemetry: bool = False) -> dict:
    """Replace particle-swap distractors with semantically diverse ones.

    Args:
        target_lv: a LineVocab row for the target word being quizzed.
        raw_mcq:   the YAML-sourced smart_mcq dict (scenario, option_a/b/c,
                   correct, feedback_uz). Must contain at least scenario +
                   option_a + correct.
        include_telemetry: when True, the returned dict carries underscore-
                   prefixed diagnostic fields (`_distractors_meta`,
                   `_root_verbs`) for tests + analytics. The frontend never
                   sets this — it always gets the stripped dict.

    Returns:
        New mcq dict with options shuffled and `correct` updated. With
        `include_telemetry=True` it also includes:
          - `_distractors_meta`: list of {text, source_tier, source_word_id}
            for the 2 distractor options (excluding the correct option).
          - `_root_verbs`: per-letter content-verb sets.
        These are stripped by default so the frontend sees a clean payload.

    Behavior:
        - Always preserves YAML's correct option (option_a by default, or
          whichever option_X is named in `correct`) verbatim.
        - Distractors come from the four-tier waterfall (Tier 1-4 above).
        - Final same-content-verb check: no two options in the returned set
          share any content verb lemma (set intersection). Colliding options
          are replaced from the next available tier.
        - Shuffles before returning so position bias is killed across
          sessions.
        - If raw_mcq is malformed (no scenario or no option_a), returns it
          unchanged so we never crash a quiz on bad data.
    """
    if not isinstance(raw_mcq, dict):
        return raw_mcq
    scenario = (raw_mcq.get("scenario") or "").strip()
    if not scenario:
        return raw_mcq

    correct_letter = str(raw_mcq.get("correct", "A")).strip().lower() or "a"
    correct_text = (raw_mcq.get(f"option_{correct_letter}") or raw_mcq.get("option_a") or "").strip()
    if not correct_text:
        return raw_mcq

    # Walk the tiers, accumulating distractors until we have 2 distinct
    # ones whose content-verb sets don't collide with each other or with
    # correct. We track the union of accepted content-verb lemmas so far;
    # any new candidate sharing ANY verb with that union is rejected.
    needed = 2
    chosen: list[tuple[str, str, str]] = []  # (text, source_id, tier_label)
    accepted_verbs = set(_content_verbs_in(correct_text))
    used_texts = {correct_text.lower().strip()}
    used_ids: set[str] = set()

    def _try_add(cands):
        for text, src, tier in cands:
            if len(chosen) >= needed:
                return
            t_norm = text.lower().strip()
            if t_norm in used_texts:
                continue
            cand_verbs = _content_verbs_in(text)
            if cand_verbs and accepted_verbs and (cand_verbs & accepted_verbs):
                # Shares a content verb with correct or a previously-accepted
                # distractor → particle-swap class. Skip.
                continue
            chosen.append((text, src, tier))
            used_texts.add(t_norm)
            accepted_verbs.update(cand_verbs)
            if src and src != "yaml":
                used_ids.add(src)

    # Run the waterfall.
    _try_add(_pick_tier1(target_lv))
    if len(chosen) < needed:
        _try_add(_pick_tier2(target_lv, used_ids, needed - len(chosen)))
    if len(chosen) < needed:
        _try_add(_pick_tier3(target_lv, used_ids, needed - len(chosen)))
    if len(chosen) < needed:
        _try_add(_yaml_fallback(raw_mcq, correct_text))

    if len(chosen) < needed:
        # Couldn't find enough distinct distractors. Surface as a content
        # gap and return original YAML so the quiz still functions —
        # better to ship a slightly-weak question than crash.
        log.warning(
            "quiz_distractors.shortage word=%s found=%d need=%d",
            getattr(target_lv, "vocab_id", "?"),
            len(chosen),
            needed,
        )
        return raw_mcq

    # Compose the final 3-option set, shuffle, track new correct letter.
    options = [(correct_text, "yaml", "correct")] + chosen
    random.shuffle(options)
    correct_idx = next(i for i, (t, _, _) in enumerate(options) if t == correct_text)

    out = {
        "scenario": scenario,
        "option_a": options[0][0],
        "option_b": options[1][0],
        "option_c": options[2][0],
        "correct": ["A", "B", "C"][correct_idx],
        "feedback_uz": raw_mcq.get("feedback_uz", ""),
    }
    # Telemetry is OFF by default — frontend never sees these fields.
    # Tests + analytics pass include_telemetry=True to read tier
    # distribution and per-distractor source words.
    if include_telemetry:
        # _distractors_meta lists ONLY the distractor options (not the
        # correct one), in the per-distractor shape laid out in the audit
        # follow-up: {text, source_tier, source_word_id}.
        distractors_meta = [
            {
                "text": text,
                "source_tier": tier,
                "source_word_id": src if src and src != "yaml" else None,
            }
            for (text, src, tier) in options
            if tier != "correct"
        ]
        out["_distractors_meta"] = distractors_meta
        out["_root_verbs"] = {
            "A": extract_root_verb(options[0][0]),
            "B": extract_root_verb(options[1][0]),
            "C": extract_root_verb(options[2][0]),
        }
    return out
