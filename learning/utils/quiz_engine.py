"""Quiz recommendation engine — BKT-based urgency scoring, weakness targeting,
drill escalation, direction selection, and confusion pair detection."""

import random
import re


def _split_examples(example_field):
    """Return a list of non-empty example sentences (split by ' / ')."""
    if not example_field:
        return []
    return [p.strip() for p in example_field.split(" / ") if p.strip()]


def _make_pattern_notice(example_field, english):
    """Build a pattern-notice payload: up to 3 sentences with the target phrase blanked.
    Returns {sentences: [{blanked, answer_shown}, ...], answer_canonical} or None.

    SKIPS phrases with slot placeholders ([someone], (something), etc.). The
    pattern_notice conceit is "one string fills every blank" — but for a
    slot phrase like 'ask [someone] out' the literal blank fillers differ
    across sentences (ask HER out / asks HIM out / ask THEM out), and the
    verb conjugation also varies. Asking the learner to type one string
    that fills all three is misleading even though the underlying pattern
    is real. Better to use sentence_cloze (one slot, one answer) for these
    and reserve pattern_notice for verbatim-uniform phrases like 'wind up'.
    """
    if english and re.search(r"[\[\(]", english):
        return None
    examples = _split_examples(example_field)
    if len(examples) < 2:
        return None
    built = []
    for ex in examples[:3]:
        blanked, shown = _make_sentence_cloze(ex, english)
        if blanked and shown:
            built.append({"blanked": blanked, "answer_shown": shown})
    if len(built) < 2:
        return None
    return {"sentences": built, "answer_canonical": english}


def _make_parallel_produce(example_field, english):
    """Build a parallel-produce payload: one model example shown, two held back as references.
    Returns {model, references, english} or None.
    """
    examples = _split_examples(example_field)
    if len(examples) < 2:
        return None
    return {
        "model": examples[0],
        "references": examples[1:3],
        "english": english,
    }


def _make_translate_back(translated_examples, english):
    """Build a translate-back payload: pick one paired (uz, en) sentence at random.
    Returns {uz, en, target} or None if no usable pair is available.
    """
    if not translated_examples or not isinstance(translated_examples, list):
        return None
    pairs = [p for p in translated_examples if isinstance(p, dict) and p.get("uz") and p.get("en")]
    if not pairs:
        return None
    pair = random.choice(pairs)
    return {
        "uz": pair["uz"],
        "en": pair["en"],
        "target": english,
    }


def _make_quote_dash(transcript_text, english, clip_start, clip_end, video_url):
    """Build a Quote Dash payload: clip + subtitle with target word blanked.
    Returns payload dict or None if required data is missing.
    """
    if not transcript_text or not video_url or clip_start is None or clip_end is None:
        return None
    blanked, answer = _make_sentence_cloze(transcript_text, english)
    if not blanked or not answer:
        return None
    return {
        "blanked": blanked,
        "answer": answer,
        "full_transcript": transcript_text.strip(),
        "clip_start": float(clip_start),
        "clip_end": float(clip_end),
        "video_url": video_url,
    }


def _normalize_usage_check(uc):
    """Auto-correct inverted correct/wrong labels.

    Some lexicon entries have the `correct` and `wrong` fields swapped — the
    AI generated the right reasoning but stored the sentences under the wrong
    keys. We detect this by parsing the Uzbek reasoning_uz for "X is wrong"
    markers (`noto'g'ri`, `xato`) and "the correct form is X" markers
    (`to'g'risi`, `to'g'ri shakl`). If a "wrong-marked" phrase appears in
    the `correct` field (and not in the wrong field), labels are flipped —
    swap them.

    Returns: (normalized_uc_dict, was_swapped: bool).
    """
    import re as _re

    if not isinstance(uc, dict):
        return uc, False
    correct = (uc.get("correct") or "").strip()
    wrong = (uc.get("wrong_sentence") or "").strip()
    if not wrong:
        wv = uc.get("wrong")
        if isinstance(wv, str) and wv.strip() and wv not in ("sentence_1", "sentence_2", "sentence_3"):
            wrong = wv.strip()
    reasoning = (uc.get("reasoning_uz") or "").strip()
    if not (correct and wrong and reasoning):
        return uc, False

    # Phrases marked as WRONG in reasoning: "X" noto'g'ri / "X" xato /
    # "X" noto'g'ri shakl. Capture the quoted English phrase before the marker.
    wrong_marks = _re.findall(
        r"['\"]([a-zA-Z][a-zA-Z\s\-]{2,40})['\"][\s.,:;]*" r"(?:noto['’]g['’]ri|xato)",
        reasoning,
        flags=_re.IGNORECASE,
    )
    # Phrases marked as CORRECT: To'g'risi: "Y" / to'g'ri shakl: "Y"
    # Be conservative — only match when "to'g'risi" or "to'g'ri shakl"
    # is followed by the quoted form. Plain "to'g'ri" is too ambiguous.
    correct_marks = _re.findall(
        r"(?:to['’]g['’]risi|to['’]g['’]ri\s+shakl)" r"[\s.,:;]*['\"]([a-zA-Z][a-zA-Z\s\-\.]{2,40})['\"]",
        reasoning,
        flags=_re.IGNORECASE,
    )

    correct_lower = correct.lower()
    wrong_lower = wrong.lower()

    def _in(phrase, hay):
        return phrase.lower() in hay

    # Cast a vote: each marker that's misplaced votes for SWAP.
    swap_votes = 0
    keep_votes = 0
    for phrase in wrong_marks:
        in_correct = _in(phrase, correct_lower)
        in_wrong = _in(phrase, wrong_lower)
        if in_correct and not in_wrong:
            swap_votes += 1
        elif in_wrong and not in_correct:
            keep_votes += 1
    for phrase in correct_marks:
        in_correct = _in(phrase, correct_lower)
        in_wrong = _in(phrase, wrong_lower)
        if in_wrong and not in_correct:
            swap_votes += 1
        elif in_correct and not in_wrong:
            keep_votes += 1

    if swap_votes > keep_votes:
        # Swap correct/wrong, preserve all other keys.
        new_uc = dict(uc)
        new_uc["correct"] = wrong
        if "wrong_sentence" in new_uc:
            new_uc["wrong_sentence"] = correct
        if isinstance(new_uc.get("wrong"), str) and new_uc["wrong"] not in ("sentence_1", "sentence_2", "sentence_3"):
            new_uc["wrong"] = correct
        return new_uc, True
    return uc, False


def _has_usable_usage_check(uc, target_word=""):
    """Validate usage_check has the data the renderer can actually display.

    Three schemas exist in the DB across the project's history — accept any:
      A. Modern:        {correct: "...", wrong_sentence: "..."}
      B. Common (PIW):  {correct: "...", wrong: "..."} where `wrong` is a sentence
      C. Legacy:        {sentence_1, sentence_2, sentence_3, wrong: "sentence_N"}
                        where `wrong` POINTS at one of the sentence_N keys.

    Also runs a CONTENT-QUALITY check: if reasoning_uz quotes specific
    English words/phrases (e.g. the supposedly-wrong term) that appear in
    NEITHER sentence, the AI mismatched its reasoning to the example pair —
    common bug from the early lexicon-generation runs. Reject those so the
    quiz engine downgrades the card to a flash card instead of asking the
    learner to choose between two perfectly natural sentences.
    """
    if not isinstance(uc, dict):
        return False

    # Resolve correct + wrong sentences across the three schemas.
    correct = (uc.get("correct") or "").strip()
    wrong = ""
    if correct:
        wrong = (uc.get("wrong_sentence") or "").strip()
        if not wrong:
            wv = uc.get("wrong")
            if isinstance(wv, str) and wv.strip() and wv not in ("sentence_1", "sentence_2", "sentence_3"):
                wrong = wv.strip()
    if not (correct and wrong):
        wk = uc.get("wrong")
        if isinstance(wk, str) and wk in ("sentence_1", "sentence_2", "sentence_3") and (uc.get(wk) or "").strip():
            wrong = (uc.get(wk) or "").strip()
            for k in ("sentence_1", "sentence_2", "sentence_3"):
                if k != wk and (uc.get(k) or "").strip():
                    correct = (uc.get(k) or "").strip()
                    break

    if not correct or not wrong:
        return False
    # Sanity: both sentences must be non-trivial.
    if len(correct) < 10 or len(wrong) < 10:
        return False

    # Content-quality check: reasoning typically quotes specific English
    # terms that explain the error. Filter out the target word itself
    # (always quoted as the topic), then verify at least one of the
    # remaining quoted words appears in either sentence. If none do, the
    # reasoning is from a DIFFERENT example pair — data is mismatched.
    reasoning = (uc.get("reasoning_uz") or "").strip()
    if reasoning:
        import re as _re

        quoted = _re.findall(r'"([a-zA-Z][a-zA-Z\s\-]{1,40})"', reasoning)
        quoted += _re.findall(r"'([a-zA-Z][a-zA-Z\s\-]{1,40})'", reasoning)
        target_lower = (target_word or "").lower().strip()
        combined = (correct + " " + wrong).lower()
        # Keep quoted phrases that are 4+ chars and NOT the target word.
        # (Phrases of 3 chars or fewer are usually noise like 'is', 'am'.)
        signals = [q.lower().strip() for q in quoted if len(q.strip()) >= 4 and q.lower().strip() != target_lower]
        if signals and not any(s in combined for s in signals):
            return False

    return True


def _make_continue_video(transcript_text, english, clip_start, clip_end, video_url):
    """Build a Continue Video payload: video plays unmuted, pauses BEFORE the
    target word is spoken; learner fills the blank; on correct, video resumes
    and plays through to the end (the audio reward).

    Adds `pause_at` (seconds, absolute) and `resume_after` (the same timestamp
    — front-end uses it to seek-and-play after the correct answer) to the
    standard cloze payload.

    Pause point estimate: the word position in the line scaled by the line's
    duration, with a small lead-in shave (0.25 of the per-word slice) so the
    learner doesn't hear the start of the target.
    """
    base = _make_quote_dash(transcript_text, english, clip_start, clip_end, video_url)
    if not base:
        return None

    line = (transcript_text or "").strip()
    if not line:
        return None

    # Find the target word's position by word index, not char index — words
    # are more time-uniform than characters in spoken speech.
    line_words = re.findall(r"\S+", line)
    if not line_words:
        return None

    target = (english or "").strip().lower()
    target_first = target.split()[0] if target else ""
    if not target_first:
        return None

    # Match the first non-skip word of the target against the line words.
    # Strip trailing punctuation for comparison so "me." matches "me".
    norm = lambda w: re.sub(r"[^a-z']+", "", w.lower())
    target_norm = norm(target_first)
    target_idx = None
    for i, w in enumerate(line_words):
        if norm(w) == target_norm:
            target_idx = i
            break
    if target_idx is None:
        # Fallback: substring match (handles e.g. "matters" inside a longer word).
        for i, w in enumerate(line_words):
            if target_norm and target_norm in norm(w):
                target_idx = i
                break
    if target_idx is None:
        return None

    duration = max(0.001, float(clip_end) - float(clip_start))
    n = len(line_words)
    per_word = duration / n
    # Pause at the START of the target word, with a 0.25 × per_word lead-in
    # shaved so we don't hear the first phoneme. Floor at clip_start + 0.2s
    # so we always show at least a beat of context.
    raw_pause = float(clip_start) + (target_idx * per_word) - (0.25 * per_word)
    pause_at = max(float(clip_start) + 0.2, raw_pause)
    pause_at = min(pause_at, float(clip_end) - 0.5)  # leave room for resume + audio

    base["pause_at"] = round(pause_at, 2)
    base["target_word_index"] = target_idx
    base["target_word_count"] = n
    return base


_CLOZE_SKIP = {
    "the",
    "a",
    "an",
    "to",
    "of",
    "is",
    "be",
    "it",
    "are",
    "was",
    "were",
    "there",
    "this",
    "that",
    "these",
    "those",
    "has",
    "have",
    "had",
    "do",
    "does",
    "did",
    "in",
    "on",
    "at",
    "by",
    "for",
    "with",
    "as",
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "my",
    "your",
    "his",
    "her",
    "our",
    "their",
    "me",
    "him",
    "us",
    "them",
    "and",
    "or",
    "but",
    "if",
}


def _strip_slot_placeholders(phrase):
    """Remove [slot] and (slot) placeholders from a saved phrase.
    'wind up [doing something]' → 'wind up'
    'Get [someone] (something)'  → 'Get'
    """
    cleaned = re.sub(r"[\[\(][^\]\)]*[\]\)]", " ", phrase or "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _make_sentence_cloze(example, english):
    """Blank the target phrase (as stored, minus [slot]/(slot) placeholders) in the example.
    Multi-word phrases ("wind up", "drift apart") are blanked as a SINGLE span so the
    user types the whole chunk — matching what they saved.

    Returns (blanked_sentence, answer) or (None, None) if nothing matches safely.
    """
    if not example or not english:
        return None, None
    first = example.split(" / ")[0].strip()
    if not first:
        return None, None

    cleaned = _strip_slot_placeholders(english)
    if not cleaned:
        return None, None

    phrase_words = [w for w in re.findall(r"[a-zA-Z']+", cleaned) if w]
    content_words = [w for w in phrase_words if w.lower() not in _CLOZE_SKIP]

    # ── Strategy A: blank the whole phrase as one span ──
    # Matches consecutive words with any inflection: wind/winding/wound, up/up.
    # Tries the full phrase first, then progressively shorter SUFFIXES so
    # idioms saved with a leading aux/copula ("Be on a roll", "Get over it")
    # still match real-world usage where the leader is dropped ("you're on
    # a roll", "I'm over it"). Each suffix must still contain ≥1 content
    # word — otherwise we'd blank pure function-word chunks like "on a".
    def _build_phrase_pattern(words):
        parts = []
        for w in words:
            stem = w[: min(len(w), 5)] if len(w) > 3 else w
            parts.append(re.escape(stem) + r"\w*")
        return re.compile(
            r"\b" + r"(?:\s+\w+){0,2}\s+".join(parts) + r"\b",
            re.IGNORECASE,
        )

    if len(phrase_words) >= 2 and content_words:
        for start in range(len(phrase_words) - 1):
            suffix = phrase_words[start:]
            if len(suffix) < 2:
                break
            # Suffix must carry at least one content word.
            if not any(w.lower() not in _CLOZE_SKIP for w in suffix):
                continue
            phrase_pattern = _build_phrase_pattern(suffix)
            m = phrase_pattern.search(first)
            if m:
                # ANSWER is the cleaned phrase (just the suffix words joined),
                # NOT the full matched span. The match span may include slack
                # words like pronouns ("ask HER out") that the visible blank
                # hides — making them required would punish the learner who
                # can only see "He wants to _____.". The cleaned phrase is
                # what the learner saved; that's what they should type.
                # `_matchCloze` is stem-tolerant, so "asked out" / "asks out"
                # also match against answer="ask out".
                answer = " ".join(suffix)
                blanked = first[: m.start()] + "_____" + first[m.end() :]
                return blanked, answer

    # ── Strategy B: fall back to blanking a single content word ──
    tried = []
    for raw in phrase_words:
        word = raw.lower()
        if len(word) < 2 or word in tried or word in _CLOZE_SKIP:
            continue
        tried.append(word)
        stem = word if len(word) <= 3 else word[: min(len(word), 5)]
        pattern = re.compile(r"\b(" + re.escape(stem) + r"\w*)\b", re.IGNORECASE)
        m = pattern.search(first)
        if m:
            answer = m.group(1)
            if answer.lower() in _CLOZE_SKIP:
                continue
            blanked = first[: m.start()] + "_____" + first[m.end() :]
            return blanked, answer
    return None, None


from django.db.models import Q
from django.utils import timezone

from learning.models import BKT_P_INIT, ConfusionPair, LineVocab, UserLearningProfile, VocabMastery
from quiz.models import QuizAttempt

# ─────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────


def mastery_state(user, *, vocab_id=None, note_id=None, word=None):
    """Return a single float p_known ∈ [0.0, 1.0] for any word identifier.

    The "what does this user know about this word?" question, answered
    canonically. Every recommendation surface in the app should call this
    so the answer is consistent across paths:

      - Practice queue ordering (lowest p_known due-for-review first)
      - SR scheduling (low p_known → tomorrow, high → next week)
      - Quiz_type selection (low p_known → recognition, high → production)
      - Own the Word eligibility (sweet spot 0.35-0.80)
      - Notifications ("this word is fading", "ready for production")

    Resolution order:
      1. note_id  → WordNote.confidence / 100.0
      2. vocab_id → VocabMastery.p_known (if exists, else BKT_P_INIT)
      3. word str → first-match WordNote, else first-match VocabMastery, else BKT_P_INIT

    Returns BKT_P_INIT (0.1) when no record exists — same default the
    BKT model assumes for never-seen items.
    """
    from vocab.models import WordNote

    if note_id is not None:
        c = WordNote.objects.filter(id=note_id, user=user).values_list("confidence", flat=True).first()
        return (c or 0) / 100.0
    if vocab_id is not None:
        m = VocabMastery.objects.filter(user=user, vocab_id=vocab_id).first()
        return float(m.p_known) if m else BKT_P_INIT
    if word:
        n = WordNote.objects.filter(user=user, word__iexact=word).order_by("-confidence", "-id").first()
        if n:
            return (n.confidence or 0) / 100.0
        # Fall through to vocab-mastery via the LineVocab the word maps to.
        lv_ids = list(LineVocab.objects.filter(english__iexact=word).values_list("id", flat=True)[:5])
        if lv_ids:
            m = VocabMastery.objects.filter(user=user, vocab_id__in=lv_ids).order_by("-p_known").first()
            if m:
                return float(m.p_known)
    return BKT_P_INIT


def get_quiz_queue(
    user, source=None, episode=None, batch_size=15, mode="all", focus=None, words=None, note_ids=None, mood=None
):
    """Build a prioritized quiz queue.
    mode="all"     — all LineVocab for this source
    mode="saved"   — user's saved WordNotes only
    mode="session" — words saved during learn session (note_ids preferred, words fallback)
    mode="rescue"  — ONLY at-risk words (due-for-review, p_known<0.85) — focused review session
    focus=None   — normal escalation
    words        — list of word strings (session fallback)
    note_ids     — list of WordNote IDs (session primary — most reliable)
    mood         — restrict saved-mode queue to one mood (mood-congruent retrieval practice)
    """
    if mode == "session":
        queue = _build_session_queue(user, note_ids=note_ids, words=words)
    elif mode == "saved":
        queue = _build_saved_queue(user, source, batch_size, words=words, mood=mood)
    elif mode == "rescue":
        queue = _build_rescue_queue(user, batch_size)
    else:
        queue = _build_general_queue(user, source, episode, batch_size)

    # Apply dimension focus override
    if focus and queue:
        focus_map = {
            "recognition": ["flash", "match", "listen"],
            "context": ["cloze", "flash"],
            "production": ["produce", "cloze"],
        }
        focus_types = focus_map.get(focus, [])
        if focus_types:
            for item in queue:
                # Override 70% of items to focus type, keep 30% natural for variety
                if random.random() < 0.7:
                    item["quiz_type"] = random.choice(focus_types)

    # Adaptive difficulty: shift quiz types to target ~80% accuracy
    if queue and mode != "session":
        queue = _apply_adaptive_difficulty(user, queue)

    # Session arc: easy→hard→easy (skip session mode — it has its own progression)
    if queue and mode != "session":
        queue = _apply_session_arc(queue)

    # Cap free_production at 2 per session — cognitively heavy. The first 2
    # stand; later free_production items are downgraded in-place to a less
    # demanding production type (translate_back if data available, else
    # sentence_cloze, else flash).
    queue = _cap_free_production(queue, max_per_session=2)

    # FINAL STRUCTURING — runs LAST so prior passes (adaptive_difficulty,
    # session_arc) can't undo the end-positioning. Pulls up to 3
    # mastered_bridge items to the very end of the queue and 3
    # continue_video items right before them. Gives the prefetcher a
    # long runway to fire AI calls in parallel while the user is still
    # working through the body of the quiz.
    queue = _enforce_quiz_structure(queue)

    return queue


def _cap_free_production(queue, max_per_session=2):
    """Downgrade extra free_production items past the session cap.

    Returns the same list with later free_production items rewritten to a
    different quiz_type. We don't drop items — the queue length stays the
    same, just the type shifts. Order matters: the FIRST free_production
    items survive (they tend to be the higher-priority words by sort).
    """
    if not queue:
        return queue
    seen = 0
    for item in queue:
        if item.get("quiz_type") != "free_production":
            continue
        seen += 1
        if seen <= max_per_session:
            continue
        # Past the cap. Pick the best available downgrade target. Prefer
        # translate_back since it's still production-flavored; fall through
        # to sentence_cloze; flash is the last-resort.
        if item.get("translate_back"):
            item["quiz_type"] = "translate_back"
        elif item.get("sentence_cloze"):
            item["quiz_type"] = "sentence_cloze"
        elif item.get("usage_check"):
            item["quiz_type"] = "usage_check"
        else:
            item["quiz_type"] = "flash"
        # Strip the unused free_production payload to keep the wire clean.
        item.pop("free_production", None)
    return queue


def submit_answer(
    user,
    vocab_id=None,
    note_id=None,
    quiz_type="flash",
    correct=False,
    user_answer="",
    chosen_wrong="",
    response_time_ms=0,
    direction="l2_l1",
    bonus_multiplier=1.0,
    used_hint=False,
    skipped=False,
):
    """Record an answer → BKT update → Elo update → weakness update → confusion detection.
    Wrapped in a single transaction so partial writes can't corrupt streak/XP/BKT state.
    """
    from django.db import transaction

    from learning.models import DailyActivity
    from vocab.models import WordNote

    with transaction.atomic():
        profile, _ = UserLearningProfile.objects.get_or_create(user=user)
        profile.total_attempts += 1
        if correct:
            profile.total_correct += 1

        # Track time spent (DailyActivity.total_minutes); counters are handled
        # by record_quiz_activity() below so they aren't double-counted.
        DailyActivity.increment(
            user,
            total_minutes=round(max(response_time_ms, 30000) / 60000, 2),
        )

        return _submit_answer_inner(
            user,
            profile,
            vocab_id,
            note_id,
            quiz_type,
            correct,
            user_answer,
            chosen_wrong,
            response_time_ms,
            direction,
            bonus_multiplier,
            used_hint,
            skipped,
        )


def _submit_answer_inner(
    user,
    profile,
    vocab_id,
    note_id,
    quiz_type,
    correct,
    user_answer,
    chosen_wrong,
    response_time_ms,
    direction,
    bonus_multiplier,
    used_hint,
    skipped=False,
):
    from learning.utils.activity import record_quiz_activity
    from vocab.models import WordNote, bkt_update

    # ── Saved-word quiz (note_id) ──
    if note_id and not vocab_id:
        note = WordNote.objects.get(id=note_id, user=user)

        # Record attempt
        QuizAttempt.objects.create(
            user=user,
            note=note,
            quiz_type=quiz_type,
            direction=direction,
            correct=correct,
            user_answer=user_answer,
            chosen_wrong=chosen_wrong,
            response_time_ms=response_time_ms,
        )

        # ── BKT mastery update (single math across all paths) ──
        # WordNote.confidence is now a *display* of p_known × 100; the math
        # is the same Bayesian Knowledge Tracing posterior + transition that
        # VocabMastery uses. This makes belief comparable across saved words
        # and source-vocab words — a prerequisite for any honest recommendation.
        # Per-attempt swings can be larger than the old logistic ladder; the
        # SPACING EFFECT is protected by the stage gates below (age + anti-grind),
        # not by the math. Math = belief; gates = conservatism.
        c_before = note.confidence or 0
        p_known_before = c_before / 100.0
        p_known_after = bkt_update(
            p_known_before,
            quiz_type,
            correct,
            response_time_ms or 0,
        )
        # Round-back to int storage. Tiny per-attempt rounding loss; bounded.
        note.confidence = max(0, min(100, int(round(p_known_after * 100))))

        # Stage transitions — gates UNCHANGED. Spacing-effect protection lives
        # here, not in the math. Threshold + age + anti-grind together prevent
        # a fresh word from fast-tracking to mastered in one session.
        if correct:
            if note.stage == "inbox":
                note.stage = "learning"
            elif note.confidence >= 85 and note.stage == "learning":
                # Mastered gates: confidence alone isn't proof of retention.
                # (1) Word must be at least 24h old (first forgetting curve).
                # (2) Cap recent-correct in 15min window — anti-grind guard
                #     so a learner can't spam one word to master it.
                from datetime import timedelta

                from django.utils import timezone as _tz

                age_ok = bool(note.created_at) and (_tz.now() - note.created_at >= timedelta(hours=24))
                recent_correct = QuizAttempt.objects.filter(
                    user=user,
                    note=note,
                    correct=True,
                    created_at__gte=_tz.now() - timedelta(minutes=15),
                ).count()
                grind_ok = recent_correct <= 4
                if age_ok and grind_ok:
                    note.stage = "mastered"
        note.save()

        # Update weakness + aggregate stats
        kind = _detect_kind_from_word(note.word)
        category = _kind_to_category(kind)
        profile.update_weakness(category, correct)
        _update_profile_stats(profile)
        profile.save()

        # Confusion detection
        if not correct and chosen_wrong:
            _record_confusion(user, note.word, chosen_wrong)

        # ── Streak / XP / rating update (same as general path) ──
        # Saved-word answers earn XP and advance streak just like general ones.
        p_known_before = (note.confidence or 0) / 100
        activity = record_quiz_activity(
            user,
            correct=correct,
            quiz_type=quiz_type,
            combo=0,  # saved-word path doesn't track per-word streak
            elapsed_ms=response_time_ms or 0,
            leveled_up=False,  # no BKT level on saved words
            bonus_multiplier=bonus_multiplier,
            p_expected=p_known_before,
            used_hint=used_hint,
            skipped=skipped,
        )

        return {
            "level": _stage_to_level(note.stage, note.confidence),
            "level_up": False,
            "overall_score": note.confidence,
            "translation_score": note.confidence,
            "context_score": note.confidence,
            "production_score": note.confidence,
            "streak": 0,
            "correct": correct,
            "p_known": note.confidence / 100,
            "streak_days": activity["streak_days"],
            "today_xp": activity["today_xp"],
            "daily_goal_xp": activity["daily_goal_xp"],
            "goal_pct": activity["goal_pct"],
            "goal_met_today": activity["goal_met_today"],
            "words_mastered_today": activity["words_mastered_today"],
            "xp_earned": activity["xp_earned"],
            "total_xp": activity["total_xp"],
            "cefr_level": activity["cefr_level"],
            "cefr_next": activity["cefr_next"],
            "cefr_pct": activity["cefr_pct"],
            "cefr_level_up": activity["cefr_level_up"],
            "rating": activity["rating"],
            "rating_delta": activity["rating_delta"],
            "peak_rating": activity["peak_rating"],
        }

    # ── General quiz (vocab_id) — full BKT ──
    vocab = LineVocab.objects.get(id=vocab_id)

    QuizAttempt.objects.create(
        user=user,
        vocab=vocab,
        quiz_type=quiz_type,
        direction=direction,
        correct=correct,
        user_answer=user_answer,
        chosen_wrong=chosen_wrong,
        response_time_ms=response_time_ms,
    )

    mastery, _ = VocabMastery.objects.get_or_create(
        user=user,
        vocab=vocab,
        defaults={"next_review": timezone.now()},
    )

    # Elo update (before BKT so we use pre-update difficulty)
    profile.update_elo(mastery, correct)

    # Capture pre-update p_known — used as the "expected outcome" for rating math.
    # Rating delta scales with how unexpected the outcome is (Elo-style).
    p_known_before = mastery.p_known if mastery.pk else BKT_P_INIT

    # BKT update — capture level before so we can detect a level-up
    level_before = mastery.level
    mastery.record_answer(quiz_type, correct, response_time_ms=response_time_ms)
    level_after = mastery.level
    _LEVEL_ORDER = {"weak": 0, "shaky": 1, "good": 2, "strong": 3, "mastered": 4}
    leveled_up = _LEVEL_ORDER.get(level_after, 0) > _LEVEL_ORDER.get(level_before, 0)

    # Weakness update
    category = _kind_to_category(vocab.kind)
    profile.update_weakness(category, correct)

    # Recompute aggregate stats
    _update_profile_stats(profile)
    profile.save()

    # Confusion detection
    if not correct and chosen_wrong:
        _record_confusion(user, vocab.english, chosen_wrong)

    # ── Streak / XP / rating update (persistent state) ──
    # mastery.streak = per-word consecutive-correct run (used as combo proxy).
    activity = record_quiz_activity(
        user,
        correct=correct,
        quiz_type=quiz_type,
        combo=mastery.streak if correct else 0,
        elapsed_ms=response_time_ms or 0,
        leveled_up=leveled_up,
        bonus_multiplier=bonus_multiplier,
        p_expected=p_known_before,
        used_hint=used_hint,
        skipped=skipped,
    )

    return {
        "level": mastery.level,
        "level_up": leveled_up,
        "overall_score": round(mastery.mastery_score, 1),
        "translation_score": round(mastery.translation_score * 100, 1),
        "context_score": round(mastery.context_score * 100, 1),
        "production_score": round(mastery.production_score * 100, 1),
        "streak": mastery.streak,
        "correct": correct,
        "p_known": round(mastery.p_known, 3),
        # Addiction layer — lets the client update streak/goal UI in real time
        "streak_days": activity["streak_days"],
        "today_xp": activity["today_xp"],
        "daily_goal_xp": activity["daily_goal_xp"],
        "goal_pct": activity["goal_pct"],
        "goal_met_today": activity["goal_met_today"],
        "words_mastered_today": activity["words_mastered_today"],
        # CEFR + XP for variable reward / progression
        "xp_earned": activity["xp_earned"],
        "total_xp": activity["total_xp"],
        "cefr_level": activity["cefr_level"],
        "cefr_next": activity["cefr_next"],
        "cefr_pct": activity["cefr_pct"],
        "cefr_level_up": activity["cefr_level_up"],
        # Chess.com-style rating
        "rating": activity["rating"],
        "rating_delta": activity["rating_delta"],
        "peak_rating": activity["peak_rating"],
    }


# ─────────────────────────────────────────────────────
# GENERAL QUEUE (All Vocab — LineVocab)
# ─────────────────────────────────────────────────────


def _build_rescue_queue(user, batch_size=15):
    """Rescue mode — drill ONLY words at risk of being forgotten.
    At-risk = due for review now AND not yet fully mastered AND user has practiced it.
    Falls back to empty list when no words are at risk (handled by caller).
    """
    at_risk_qs = (
        VocabMastery.objects.filter(
            user=user,
            next_review__lte=timezone.now(),
            p_known__lt=0.85,
            attempts__gt=0,
        )
        .select_related("vocab", "vocab__transcript", "vocab__episode", "vocab__source")
        .order_by("p_known")[: batch_size * 2]
    )
    at_risk_vocabs = [m.vocab for m in at_risk_qs]
    if not at_risk_vocabs:
        return []
    # Delegate to the general builder with only the at-risk vocab set.
    # Pass any source (not used when vocab_override is set).
    return _build_general_queue(user, source=None, episode=None, batch_size=batch_size, vocab_override=at_risk_vocabs)


def _build_general_queue(user, source, episode, batch_size, vocab_override=None):
    """Standard queue. `vocab_override` (list of LineVocab) bypasses source/episode filters."""
    if vocab_override is not None:
        all_vocab = list(vocab_override)
    else:
        vocab_qs = (
            LineVocab.objects.filter(source=source)
            .exclude(english="")
            .exclude(translation="")  # need both sides to render any quiz_type
            .select_related("transcript", "episode")
        )
        if episode:
            vocab_qs = vocab_qs.filter(episode=episode)
        all_vocab = list(vocab_qs)
    # Defensive: drop overrides that violate the same invariant (empty english
    # or translation) — otherwise a flash card has nothing to show.
    all_vocab = [v for v in all_vocab if (v.english or "").strip() and (v.translation or "").strip()]
    if not all_vocab:
        return []

    # Get mastery records + user profile for weakness multiplier
    existing = {vm.vocab_id: vm for vm in VocabMastery.objects.filter(user=user, vocab__in=all_vocab)}
    profile = UserLearningProfile.objects.filter(user=user).first()

    # Get recent attempts for drill escalation
    recent_attempts = _get_recent_attempts(user)

    scored = []
    for v in all_vocab:
        mastery = existing.get(v.id)
        if not mastery:
            mastery = VocabMastery(user=user, vocab=v, p_known=BKT_P_INIT)

        # Apply decay before scoring
        if mastery.pk:
            mastery.apply_decay()

        w_mult = profile.get_weakness_multiplier(v.kind) if profile else 1.0
        urgency = mastery.get_urgency(current_episode=episode, weakness_multiplier=w_mult)
        scored.append((v, mastery, urgency))

    scored.sort(key=lambda x: -x[2])

    # ── Cumulative mixing: 40% new/weak + 60% reviewed/strong ──
    # Research: cumulative testing (mixing old words) = 2-3x better retention
    # New/weak: p_known < 0.60 (still learning)
    # Reviewed/strong: p_known >= 0.60 (learned before, reinforce)
    new_weak = [(v, m, u) for v, m, u in scored if (m.p_known if m.pk else BKT_P_INIT) < 0.60]
    reviewed = [(v, m, u) for v, m, u in scored if (m.p_known if m.pk else BKT_P_INIT) >= 0.60]

    # New words sorted by urgency (most urgent first), old words shuffled
    new_weak.sort(key=lambda x: -x[2])
    random.shuffle(reviewed)

    n_new = max(int(batch_size * 0.40), 1)
    n_old = batch_size - n_new

    queue_items = new_weak[:n_new] + reviewed[:n_old]

    # Fill remainder if not enough of either category
    seen_ids = {v.id for v, m, u in queue_items}
    for v, m, u in scored:
        if len(queue_items) >= batch_size:
            break
        if v.id not in seen_ids:
            queue_items.append((v, m, u))
            seen_ids.add(v.id)

    queue_items = queue_items[:batch_size]
    random.shuffle(queue_items)

    all_translations = list({v.translation for v in all_vocab if v.translation})
    all_english = list({v.english for v in all_vocab if v.english})
    confusion_map = _load_confusion_map(user)

    result = []
    for vocab, mastery, urgency in queue_items:
        has_desc = bool(vocab.description and vocab.description.strip())
        has_smart_mcq = bool(vocab.smart_mcq)
        has_usage_check = bool(vocab.usage_check)
        has_example = bool(vocab.example and vocab.example.strip())
        has_multi = len(_split_examples(vocab.example)) >= 2

        clip_start, clip_end, video_url = _get_clip_data(vocab)
        has_video = bool(video_url and clip_start is not None and clip_end is not None and vocab.transcript)
        has_translated = bool(
            vocab.translated_examples
            and isinstance(vocab.translated_examples, list)
            and len(vocab.translated_examples) > 0
        )

        quiz_type = _pick_quiz_type(
            mastery,
            recent_attempts.get(vocab.id, []),
            has_description=has_desc,
            has_smart_mcq=has_smart_mcq,
            has_usage_check=has_usage_check,
            has_example=has_example,
            has_multi_examples=has_multi,
            has_video=has_video,
            has_translated=has_translated,
        )
        direction = _pick_direction(mastery)

        # Pick a short build sentence: first example from PIW, else transcript if ≤ 10 words
        raw_transcript = vocab.transcript.text if vocab.transcript else ""
        first_example = vocab.example.split(" / ")[0].strip() if vocab.example else ""
        if first_example and 4 <= len(first_example.split()) <= 10:
            build_sentence = first_example
        elif raw_transcript and 4 <= len(raw_transcript.split()) <= 10:
            build_sentence = raw_transcript
        else:
            build_sentence = ""

        item = {
            "vocab_id": vocab.id,
            "note_id": None,
            "english": vocab.english,
            "translation": vocab.translation,
            "context": vocab.description,
            "pattern": vocab.pattern or "",
            "kind": vocab.kind,
            "quiz_type": quiz_type,
            "direction": direction,
            "transcript_text": raw_transcript,
            "transcript_id": vocab.transcript_id,
            "build_sentence": build_sentence,
            "clip_start": clip_start,
            "clip_end": clip_end,
            "video_url": video_url,
            "level": mastery.level if mastery.pk else "weak",
            "overall_score": round(mastery.mastery_score, 1) if mastery.pk else 0,
            "p_known": round(mastery.p_known, 3) if mastery.pk else BKT_P_INIT,
            # Elaboration payload — shown on wrong-answer to turn failures into teaching moments.
            "tavsif": vocab.tavsif or "",
            "example": vocab.example or "",
        }

        # Resolve the quiz type to a type whose payload we can actually build.
        # Chain fallbacks (e.g. pattern_notice → sentence_cloze → flash) keep
        # iterating until we have an attachable payload.
        _MAX_HOPS = 5
        for _ in range(_MAX_HOPS):
            if quiz_type == "smart_mcq" and vocab.smart_mcq:
                # Issue 1 (audit): replace YAML particle-swap distractors with
                # semantically diverse ones (confusable_with → category pool →
                # antonym → YAML fallback). See quiz_distractors.augment_smart_mcq.
                from learning.utils.quiz_distractors import augment_smart_mcq

                item["smart_mcq"] = augment_smart_mcq(vocab, vocab.smart_mcq)
                break
            if quiz_type == "usage_check" and _has_usable_usage_check(vocab.usage_check, vocab.english):
                # Auto-correct any flipped correct/wrong labels.
                normalized_uc, _ = _normalize_usage_check(vocab.usage_check)
                item["usage_check"] = normalized_uc
                break
            if quiz_type == "usage_check":
                # Truthy but unusable — downgrade.
                quiz_type = "sentence_cloze" if has_example else "flash"
                continue
            if quiz_type == "sentence_cloze":
                blanked, answer = _make_sentence_cloze(vocab.example, vocab.english)
                if blanked and answer:
                    item["sentence_cloze"] = {"blanked": blanked, "answer": answer}
                    break
                quiz_type = "flash"
                continue
            if quiz_type == "pattern_notice":
                payload = _make_pattern_notice(vocab.example, vocab.english)
                if payload:
                    item["pattern_notice"] = payload
                    break
                quiz_type = "sentence_cloze" if has_example else "flash"
                continue
            if quiz_type == "parallel_produce":
                payload = _make_parallel_produce(vocab.example, vocab.english)
                if payload:
                    item["parallel_produce"] = payload
                    break
                quiz_type = "sentence_cloze" if has_example else "flash"
                continue
            if quiz_type == "quote_dash":
                transcript_text = vocab.transcript.text if vocab.transcript else ""
                payload = _make_quote_dash(transcript_text, vocab.english, clip_start, clip_end, video_url)
                if payload:
                    item["quote_dash"] = payload
                    break
                quiz_type = "sentence_cloze" if has_example else "flash"
                continue
            if quiz_type == "continue_video":
                transcript_text = vocab.transcript.text if vocab.transcript else ""
                payload = _make_continue_video(transcript_text, vocab.english, clip_start, clip_end, video_url)
                if payload:
                    item["continue_video"] = payload
                    break
                # If pause_at can't be computed (e.g. word not found in line),
                # downgrade to quote_dash since the data IS there for that.
                quiz_type = "quote_dash"
                continue
            if quiz_type == "translate_back":
                # REMOVED from rotation per user dislike. Downgrade to
                # sentence_cloze (closest production-flavored alternative).
                quiz_type = "sentence_cloze" if has_example else "flash"
                continue
            if quiz_type == "free_production":
                # REMOVED from rotation — see feedback_no_personal_thoughts.
                # If anything still asks for this type, downgrade to a
                # translation-style production instead of asking for thoughts.
                quiz_type = "translate_back" if has_translated else ("sentence_cloze" if has_example else "flash")
                continue
            # Unknown or un-attachable → fall back to flash
            quiz_type = "flash"
            break
        item["quiz_type"] = quiz_type

        if quiz_type == "flash":
            confused = confusion_map.get(vocab.english.lower(), [])
            if direction == "l1_l2":
                item["options"] = _build_flash_options(vocab.english, all_english, confused_with=confused)
            else:
                # Map confused english words to their translations for l2_l1
                confused_tl = []
                for cw in confused:
                    match = next((v.translation for v in all_vocab if v.english.lower() == cw), None)
                    if match:
                        confused_tl.append(match)
                item["options"] = _build_flash_options(vocab.translation, all_translations, confused_with=confused_tl)

        result.append(item)

    # Diversify monotonous queues (e.g., first-time user: all flash l1_l2)
    result = _diversify_monotonous(result, all_english, all_translations, confusion_map, all_vocab)

    return _inject_special_rounds(result)


# ─────────────────────────────────────────────────────
# SESSION QUEUE (words saved during learn session)
# Progression per word: flash (MCQ) → match → cloze → listen → build
# ─────────────────────────────────────────────────────


def _build_session_queue(user, note_ids=None, words=None):
    """Build a structured intro queue for words just saved in learn mode.

    4-level mastery progression (no listen):
      Round 1 — flash:  show Uzbek, pick English (MCQ, l1_l2 — easiest direction)
      Round 3 — match:  match all session words as pairs (one group)
      Round 4 — cloze:  fill in the blank from the transcript sentence
      Round 5 — listen: hear the word, pick or type it
    """
    from vocab.models import WordNote

    # Primary: look up by note IDs (most reliable — these were just saved)
    if note_ids:
        notes_qs = WordNote.objects.filter(id__in=note_ids, user=user).select_related(
            "transcript", "transcript__episode"
        )
    elif words:
        notes_qs = WordNote.objects.filter(user=user, word__in=words).select_related(
            "transcript", "transcript__episode"
        )
    else:
        return []

    # No translation filter — notes were just saved, translation may still be empty
    valid = list(notes_qs)

    if not valid:
        return []

    all_translations = [n.translation or n.definition or n.word for n in valid]

    # Build distractor pool — user's saved words first, then global word DB as fallback
    valid_ids = [n.id for n in valid]
    session_words_set = {n.word.lower() for n in valid}

    extra_english = list(
        WordNote.objects.filter(user=user).exclude(id__in=valid_ids).values_list("word", flat=True).order_by("?")[:20]
    )

    # Always ensure at least 15 unique distractors — pull from global WordTranslation DB
    if len(extra_english) < 15:
        from vocab.models import WordTranslation

        fallback = list(
            WordTranslation.objects.exclude(word__in=session_words_set)
            .values_list("word", flat=True)
            .order_by("?")[:30]
        )
        extra_english += fallback

    all_english = [n.word for n in valid] + extra_english

    def _base_item(note):
        clip_start = clip_end = None
        video_url = context_text = ""
        if note.transcript:
            context_text = note.transcript.text
            clip_start = float(note.transcript.start_time)
            clip_end = float(note.transcript.end_time)
            ep = note.transcript.episode
            if ep and ep.video_file:
                video_url = ep.video_file.url
        display_translation = note.translation or note.definition or note.word
        return {
            "vocab_id": None,
            "note_id": note.id,
            "english": note.word,
            "translation": display_translation,
            "context": note.definition if note.translation else "",
            "kind": _detect_kind_from_word(note.word),
            "direction": "l1_l2",
            "transcript_text": context_text,
            "clip_start": clip_start,
            "clip_end": clip_end,
            "video_url": video_url,
            "level": "inbox",
            "overall_score": 0.0,
            "p_known": 0.0,
            "options": [],
            "build_sentence": "",
            "pattern": "",
        }

    queue = []

    # Round 1 — flash MCQ: show Uzbek → pick English
    for note in valid:
        item = _base_item(note)
        item["quiz_type"] = "flash"
        item["direction"] = "l1_l2"
        # l1_l2: question = translation (Uzbek), options = English words
        item["options"] = _build_flash_options(
            note.word,
            all_english,
        )
        queue.append(item)

    # Round 3 — match (only when 3+ words — 2 pairs is too trivial)
    if len(valid) >= 3:
        match_pairs = [{"english": n.word, "translation": n.translation or n.definition or n.word} for n in valid]
        queue.append(
            {
                **_base_item(valid[0]),
                "quiz_type": "match",
                "match_pairs": match_pairs,
                "note_id": None,
            }
        )

    # Round 4 — cloze (fill in the blank)
    for note in valid:
        item = _base_item(note)
        item["quiz_type"] = "cloze"
        queue.append(item)

    # Round 5 — listen / TTS spelling check (words and phrases only, not idioms)
    for note in valid:
        if _detect_kind_from_word(note.word) == "idiom":
            continue
        item = _base_item(note)
        item["quiz_type"] = "listen"
        item["options"] = _build_flash_options(note.word, all_english)
        queue.append(item)

    # Round 6 — build (sentence scramble)
    # Use short example_usage first; fall back to transcript line if short enough
    for note in valid:
        sentence = (note.example_usage or "").strip()
        if not sentence and note.usage_examples:
            ex = note.usage_examples[0] if isinstance(note.usage_examples, list) else None
            if ex and isinstance(ex, dict):
                sentence = ex.get("en", "").strip()
        if not sentence and note.transcript:
            t = note.transcript.text.strip()
            if len(t.split()) <= 10:
                sentence = t
        words = sentence.split()
        if 4 <= len(words) <= 10:
            item = _base_item(note)
            item["quiz_type"] = "build"
            item["build_sentence"] = sentence
            queue.append(item)

    return queue


# SAVED QUEUE (My Words — WordNote)
# ─────────────────────────────────────────────────────


def _build_saved_queue(user, source, batch_size, words=None, mood=None):
    from vocab.models import WordNote

    notes_qs = (
        WordNote.objects.filter(user=user).exclude(translation="", definition="").select_related("transcript", "quote")
    )
    if words:
        # Session quiz — only test the specific words seen during the session
        notes_qs = notes_qs.filter(word__in=words)
    elif source:
        notes_qs = notes_qs.filter(Q(transcript__source=source) | Q(quote__source=source))
    if mood:
        notes_qs = notes_qs.filter(mood=mood)

    notes = list(notes_qs.order_by("?")[: batch_size * 3])
    if not notes:
        return []

    all_translations = list({n.translation for n in notes if n.translation})
    all_definitions = list({n.definition for n in notes if n.definition})
    distractor_pool = all_translations or all_definitions
    all_english = list({n.word for n in notes if n.word})
    confusion_map = _load_confusion_map(user)

    # ── Cumulative mixing: 40% active (inbox/learning) + 60% mastered ──
    active = [n for n in notes if n.stage in ("inbox", "learning")]
    mastered = [n for n in notes if n.stage == "mastered"]

    # Active sorted by confidence (lowest = most urgent), mastered shuffled
    active.sort(key=lambda n: n.confidence)
    random.shuffle(mastered)

    n_active = max(int(batch_size * 0.40), 1)
    n_mastered = batch_size - n_active

    selected = active[:n_active] + mastered[:n_mastered]

    # Fill remainder if not enough of either
    seen_ids = {n.id for n in selected}
    for n in notes:
        if len(selected) >= batch_size:
            break
        if n.id not in seen_ids:
            selected.append(n)
            seen_ids.add(n.id)

    notes = selected[:batch_size]
    random.shuffle(notes)

    # Batch-load tavsif + example from LineVocab for elaboration on wrong-answer.
    # Keyed by (transcript_id, word_lower) because one transcript can hold many vocabs.
    # lv_obj_map keeps the actual LineVocab row so smart_mcq augmentation can
    # access confusable_with / category / level / kind without a re-query.
    elab_map = {}
    lv_obj_map = {}
    transcript_ids = {n.transcript_id for n in notes if n.transcript_id}
    if transcript_ids:
        for lv in LineVocab.objects.filter(transcript_id__in=transcript_ids).only(
            "id",
            "transcript_id",
            "english",
            "tavsif",
            "example",
            "usage_check",
            "smart_mcq",
            "translated_examples",
            "vocab_id",
            "category",
            "level",
            "kind",
            "confusable_with",
        ):
            key = (lv.transcript_id, lv.english.lower())
            elab_map[key] = {
                "tavsif": lv.tavsif or "",
                "example": lv.example or "",
                "usage_check": lv.usage_check or {},
                "smart_mcq": lv.smart_mcq or {},
                "translated_examples": lv.translated_examples or [],
            }
            lv_obj_map[key] = lv

    result = []
    for note in notes:
        p_approx = note.confidence / 100
        has_desc = bool(note.definition and note.definition.strip())
        elab_entry = elab_map.get((note.transcript_id, note.word.lower()), {})
        fallback_example = elab_entry.get("example") or (note.example_usage or "").strip()
        has_example_local = bool(fallback_example)
        has_usage_check_local = bool(elab_entry.get("usage_check"))
        has_smart_mcq_local = bool(elab_entry.get("smart_mcq"))
        has_multi_local = len(_split_examples(fallback_example)) >= 2
        has_translated_local = bool(elab_entry.get("translated_examples"))
        # Compute has_video early — needed for continue_video routing AND
        # for the resolve loop to attach the payload below. A note has
        # video if its transcript belongs to an episode with a video file
        # AND the transcript timing (start/end) is usable.
        has_video_local = False
        cv_clip_start = cv_clip_end = None
        cv_video_url = ""
        cv_transcript_text = ""
        if note.transcript:
            cv_transcript_text = note.transcript.text or ""
            cv_clip_start = float(note.transcript.start_time) if note.transcript.start_time is not None else None
            cv_clip_end = float(note.transcript.end_time) if note.transcript.end_time is not None else None
            ep = note.transcript.episode
            if ep and getattr(ep, "video_file", None):
                cv_video_url = ep.video_file.url
            has_video_local = bool(
                cv_video_url and cv_clip_start is not None and cv_clip_end is not None and cv_transcript_text
            )
        quiz_type = _pick_quiz_type_from_confidence(
            note.stage,
            note.confidence,
            has_description=has_desc,
            has_smart_mcq=has_smart_mcq_local,
            has_usage_check=has_usage_check_local,
            has_example=has_example_local,
            has_multi_examples=has_multi_local,
            has_translated=has_translated_local,
            has_video=has_video_local,
        )
        # Mastered-word bridge override: words at "mastered" stage with a
        # usable Uzbek translation get routed to AI-generated translation
        # drills.
        #
        # CAPPED AT 3 PER SESSION so the queue stays varied and matches the
        # structured end-positioning (Q13-15 in a 15-item queue).
        _bridges_so_far = sum(1 for r in result if r.get("quiz_type") == "mastered_bridge")
        if note.stage == "mastered" and note.translation and _bridges_so_far < 3 and random.random() < 0.85:
            quiz_type = "mastered_bridge"

        # Continue-video promotion: notes with usable video data get routed
        # to continue_video roughly half the time. Skipped if the item is
        # already mastered_bridge — bridges win because they're rarer.
        _videos_so_far = sum(1 for r in result if r.get("quiz_type") == "continue_video")
        if quiz_type != "mastered_bridge" and has_video_local and _videos_so_far < 3 and random.random() < 0.55:
            quiz_type = "continue_video"

        # Cap enforcement — also demote naturally-routed continue_video
        # items past the cap of 3. Without this, the underlying router
        # (which also returns continue_video on its own) can over-produce
        # and we end up with 5+ video cards in a 15-question quiz.
        if quiz_type == "continue_video" and _videos_so_far >= 3:
            quiz_type = "sentence_cloze" if has_example_local else "flash"
        direction = "l1_l2" if p_approx < 0.4 else "l2_l1"

        answer = note.translation or note.definition
        clip_start, clip_end, video_url = None, None, ""
        context_text = ""
        if note.transcript:
            context_text = note.transcript.text
            clip_start = float(note.transcript.start_time)
            clip_end = float(note.transcript.end_time)
            ep = note.transcript.episode
            if ep and ep.video_file:
                video_url = ep.video_file.url
        elif note.quote:
            context_text = note.quote.text

        # Short sentence for build quiz: example_usage first, then capped transcript
        ex = (note.example_usage or "").strip()
        if not ex and note.usage_examples:
            first = note.usage_examples[0] if isinstance(note.usage_examples, list) else None
            if first and isinstance(first, dict):
                ex = first.get("en", "").strip()
        if ex and 4 <= len(ex.split()) <= 10:
            build_sentence = ex
        elif context_text and 4 <= len(context_text.split()) <= 10:
            build_sentence = context_text
        else:
            build_sentence = ""

        item = {
            "vocab_id": None,
            "note_id": note.id,
            "english": note.word,
            "translation": answer,
            "context": note.definition if note.translation else "",
            "kind": _detect_kind_from_word(note.word),
            "quiz_type": quiz_type,
            "direction": direction,
            "transcript_text": context_text,
            "transcript_id": note.transcript_id,
            "build_sentence": build_sentence,
            "pattern": "",
            "clip_start": clip_start,
            "clip_end": clip_end,
            "video_url": video_url,
            "level": _stage_to_level(note.stage, note.confidence),
            "overall_score": note.confidence,
            "p_known": p_approx,
            # Elaboration payload — from LineVocab lookup; falls back to note's own example_usage.
            "tavsif": elab_entry.get("tavsif", ""),
            "example": fallback_example,
        }

        # Resolve the quiz type to a type whose payload we can attach.
        # Chain fallbacks keep iterating until we have a valid payload or hit flash.
        _MAX_HOPS = 5
        for _ in range(_MAX_HOPS):
            # mastered_bridge is the AI translation drill — its payload is
            # generated at render time (lazy). The queue item just needs
            # note_id (already on `item`) so the frontend can hit
            # /word-bridge/generate/. No precomputed payload to attach here.
            if quiz_type == "mastered_bridge":
                break
            # continue_video — pre-build the payload from the saved note's
            # transcript + episode video file. _make_continue_video computes
            # the pause point deterministically (no AI call). If the note
            # somehow lacks video data here, fall through to the same
            # cascade as quote_dash.
            if quiz_type == "continue_video":
                if has_video_local:
                    payload = _make_continue_video(
                        cv_transcript_text,
                        note.word,
                        cv_clip_start,
                        cv_clip_end,
                        cv_video_url,
                    )
                    if payload:
                        item["continue_video"] = payload
                        # Also surface clip metadata at the top level for
                        # any code that reads from item.* directly.
                        item["clip_start"] = cv_clip_start
                        item["clip_end"] = cv_clip_end
                        item["video_url"] = cv_video_url
                        break
                quiz_type = "sentence_cloze" if has_example_local else "flash"
                continue
            if quiz_type == "usage_check" and _has_usable_usage_check(
                elab_entry.get("usage_check"), elab_entry.get("english", "")
            ):
                normalized_uc, _ = _normalize_usage_check(elab_entry["usage_check"])
                item["usage_check"] = normalized_uc
                break
            if quiz_type == "usage_check":
                quiz_type = "sentence_cloze" if has_example_local else "flash"
                continue
            if quiz_type == "smart_mcq" and elab_entry.get("smart_mcq"):
                # Issue 1 (audit): augment particle-swap distractors. The LV
                # row carries confusable_with / category / level / kind for
                # the tier waterfall. lv_obj_map was populated above.
                from learning.utils.quiz_distractors import augment_smart_mcq

                lv_obj = lv_obj_map.get((note.transcript_id, note.word.lower()))
                item["smart_mcq"] = (
                    augment_smart_mcq(lv_obj, elab_entry["smart_mcq"]) if lv_obj else elab_entry["smart_mcq"]
                )
                break
            if quiz_type == "sentence_cloze":
                blanked, answer_word = _make_sentence_cloze(fallback_example, note.word)
                if blanked and answer_word:
                    item["sentence_cloze"] = {"blanked": blanked, "answer": answer_word}
                    break
                quiz_type = "flash"
                continue
            if quiz_type == "pattern_notice":
                payload = _make_pattern_notice(fallback_example, note.word)
                if payload:
                    item["pattern_notice"] = payload
                    break
                quiz_type = "sentence_cloze" if has_example_local else "flash"
                continue
            if quiz_type == "parallel_produce":
                payload = _make_parallel_produce(fallback_example, note.word)
                if payload:
                    item["parallel_produce"] = payload
                    break
                quiz_type = "sentence_cloze" if has_example_local else "flash"
                continue
            if quiz_type == "translate_back":
                # REMOVED from rotation per user dislike — downgrade in place.
                quiz_type = "sentence_cloze" if has_example_local else "flash"
                continue
            if quiz_type == "free_production":
                # REMOVED from rotation — see feedback_no_personal_thoughts.
                # Downgrade to translation-style production rather than ask
                # the learner to write 2-3 sentences about their own life.
                quiz_type = (
                    "translate_back" if has_translated_local else ("sentence_cloze" if has_example_local else "flash")
                )
                continue
            # Unknown or un-attachable → fall back to flash
            quiz_type = "flash"
            break
        item["quiz_type"] = quiz_type

        if quiz_type == "flash":
            confused = confusion_map.get(note.word.lower(), [])
            if direction == "l1_l2":
                item["options"] = _build_flash_options(note.word, all_english, confused_with=confused)
            else:
                # Map confused words to their translations
                confused_tl = []
                for cw in confused:
                    match = next((n.translation for n in notes if n.word.lower() == cw and n.translation), None)
                    if match:
                        confused_tl.append(match)
                item["options"] = _build_flash_options(answer, distractor_pool, confused_with=confused_tl)

        result.append(item)

    # Diversify monotonous queues for saved words too
    result = _diversify_monotonous(result, all_english, distractor_pool, confusion_map, None)

    return _inject_special_rounds(result)


# ─────────────────────────────────────────────────────
# MASTERY LADDER — a learner's psychological journey
# ─────────────────────────────────────────────────────
#
#  Stage 1  p < 0.15   INPUT:      flash L1→L2            (just see it, no pressure)
#  Stage 2  p < 0.35   SCAFFOLD:   smart_mcq 60% if data  (apply with 3 options + Uzbek feedback)
#                                  flash L2→L1 otherwise  (recognition, now reversed)
#  Stage 3  p < 0.65   JUDGMENT:   usage_check 40% if data (pick the natural sentence)
#                                  sentence_cloze 25% if ex (fill word into real example)
#                                  define 20% if desc     (type from meaning, no L1 crutch)
#                                  cloze 15%              (fill blank in transcript)
#  Stage 4  p ≥ 0.65   USAGE IN CONTEXT:
#                                  sentence_cloze 45% if ex (produce word IN a sentence)
#                                  usage_check 35% if data  (natural-vs-unnatural judgment)
#                                  produce 20%              (type the bare word — minority)
#
#  Why these bands:
#   - <0.15 new word: pure input. Quiz too early = failure = learned helplessness.
#   - 0.15–0.35: ZPD. smart_mcq is THE ideal task here — scaffolded application with
#     Uzbek feedback explaining WHY. Triggered heavily (60%) because this is its moment.
#   - 0.35–0.65: learner knows the word; now test USAGE. usage_check forces error-
#     detection which transfers to production better than pure comprehension.
#   - ≥0.65: production stage. Sprinkle usage_check (20%) so judgment doesn't decay.
#
#  Streak: 3 correct → level up, 2 wrong → level down.
# ─────────────────────────────────────────────────────


def _quiz_type_and_direction(
    p_known,
    has_description=True,
    has_smart_mcq=False,
    has_usage_check=False,
    has_example=False,
    has_multi_examples=False,
    has_video=False,
    has_translated=False,
):
    """Pure 6-type system. Picks (quiz_type, direction) from p_known.

    Active types: flash, usage_check, pattern_notice, sentence_cloze,
                  quote_dash, continue_video.
    Removed 2026-04-29: smart_mcq. Real-world cards routinely shipped
    with empty scenario/option_a/b/c (LineVocab.smart_mcq JSON missing
    or partial), producing an empty 3-option card with no visible text
    — a hard dead-end for the learner. The 2-option usage_check covers
    the same judgment-style task with denser data and no failure mode.
    All else (define/cloze/produce/listen/build/pattern) is dead code, never selected.
    """
    # Stage 1: INPUT — pure recognition, no pressure
    if p_known < 0.15:
        return "flash", "l1_l2"

    # Stage 2: BRIDGE — usage_check ("Pick the correct one") leads now that
    # smart_mcq is retired; flash fills when no usage_check data exists.
    if p_known < 0.35:
        if has_usage_check and random.random() < 0.65:
            return "usage_check", "l2_l1"
        return "flash", "l2_l1"

    # Stage 3: JUDGMENT + first taste of production
    # usage_check ("Pick the correct one") is the dominant judgment task here —
    # error-detection at this level transfers to production better than pure
    # comprehension. translate_back removed previously per user feedback.
    if p_known < 0.65:
        r = random.random()
        if has_video and r < 0.18:
            return "continue_video", "l2_l1"
        if has_video and r < 0.28:
            return "quote_dash", "l2_l1"
        if has_usage_check and r < 0.65:
            return "usage_check", "l2_l1"
        # pattern_notice REMOVED from rotation 2026-05-02 — pedagogically
        # redundant for saved-mode learners (mostly mastered words).
        # Slots that would have routed here fall through to sentence_cloze.
        if has_example:
            return "sentence_cloze", "l2_l1"
        return "flash", "l2_l1"

    # Stage 4: PRODUCTION — cinematic + targeted cloze + judgment.
    #
    # Routing target (after data-availability fallbacks):
    #   20% continue_video — video pauses at the blank
    #   15% quote_dash     — clip + cloze
    #   25% usage_check    — "Pick the correct one" (judgment doesn't decay)
    #   40% sentence_cloze — type missing word in real example
    #
    # usage_check promoted from last-resort fallback to a real share per user
    # feedback ("add usage_check to the quiz"). At Stage 4, judgment skill
    # still matters — natural-vs-awkward sense is part of ownership.
    # translate_back REMOVED — user explicit dislike.
    r = random.random()
    if has_video and r < 0.20:
        return "continue_video", "l2_l1"
    if has_video and r < 0.35:
        return "quote_dash", "l2_l1"
    if has_usage_check and r < 0.60:
        return "usage_check", "l2_l1"
    if has_example:
        return "sentence_cloze", "l2_l1"
    # Final fallbacks if specific payloads aren't available.
    if has_usage_check:
        return "usage_check", "l2_l1"
    return "flash", "l2_l1"


def _pick_quiz_type(
    mastery,
    recent_attempts,
    has_description=True,
    has_smart_mcq=False,
    has_usage_check=False,
    has_example=False,
    has_multi_examples=False,
    has_video=False,
    has_translated=False,
):
    """Pick quiz type from mastery level, adjusted by consecutive streak."""
    p = mastery.p_known if mastery.pk else BKT_P_INIT
    quiz_type, direction = _quiz_type_and_direction(
        p, has_description, has_smart_mcq, has_usage_check, has_example, has_multi_examples, has_video, has_translated
    )

    # Check last 3 attempts for consecutive streak adjustment (within active 6-type system)
    last_3 = (recent_attempts or [])[-3:]
    if len(last_3) >= 3:
        # 3 consecutive correct → bump up one level toward production
        if all(last_3[-3:]):
            if quiz_type in ("flash", "smart_mcq"):
                if has_translated:
                    quiz_type = "translate_back"
                elif has_example:
                    quiz_type = "sentence_cloze"
            elif quiz_type in ("usage_check", "pattern_notice", "sentence_cloze"):
                if has_translated:
                    quiz_type = "translate_back"
        # 2 consecutive wrong → drop down to easier judgment/recognition
        elif not last_3[-1] and not last_3[-2]:
            if quiz_type in ("translate_back", "sentence_cloze", "quote_dash"):
                quiz_type = "usage_check" if has_usage_check else "flash"
            elif quiz_type in ("usage_check", "pattern_notice"):
                quiz_type, direction = "flash", "l2_l1"
            elif direction == "l2_l1":
                direction = "l1_l2"

    return quiz_type


def _pick_quiz_type_from_confidence(
    stage,
    confidence,
    has_description=True,
    has_smart_mcq=False,
    has_usage_check=False,
    has_example=False,
    has_multi_examples=False,
    has_translated=False,
    has_video=False,
):
    """Map saved-word confidence to quiz type via same 4-level table.

    has_video is forwarded so saved notes attached to a transcript with a
    video file can route to continue_video. Previously hardcoded to False,
    which silently disabled continue_video in saved-mode quizzes.
    """
    p = confidence / 100
    quiz_type, _ = _quiz_type_and_direction(
        p, has_description, has_smart_mcq, has_usage_check, has_example, has_multi_examples, has_video, has_translated
    )
    return quiz_type


def _pick_direction(mastery):
    """Direction from mastery level. has_description irrelevant here — only affects quiz_type."""
    if not mastery.pk:
        return "l1_l2"
    p = mastery.p_known
    if p < 0.30:
        return "l1_l2"
    return "l2_l1"


def _get_recent_attempts(user, limit=50):
    """Get last N attempts grouped by vocab_id → list of correct booleans."""
    attempts = QuizAttempt.objects.filter(user=user, vocab__isnull=False).order_by("-created_at")[:limit]
    grouped = {}
    for a in attempts:
        grouped.setdefault(a.vocab_id, []).append(a.correct)
    # Reverse each list so oldest is first
    for k in grouped:
        grouped[k] = list(reversed(grouped[k]))
    return grouped


# ─────────────────────────────────────────────────────
# CONFUSION PAIR DETECTION
# ─────────────────────────────────────────────────────


def _record_confusion(user, word_a, chosen_wrong_text):
    """Record or increment a confusion pair.

    Only tracks single-word or short-phrase confusions (max 3 words each).
    Long phrases are quiz distractors, not real vocabulary confusions.
    """
    from vocab.models import LineVocab, WordNote

    # Search by translation match to find the actual word
    match = LineVocab.objects.filter(translation=chosen_wrong_text).first()
    word_b = match.english if match else chosen_wrong_text

    # Skip if same word
    if word_a.lower() == word_b.lower():
        return

    # Skip long phrases — real confusion pairs are single words or short phrases
    if len(word_a.split()) > 3 or len(word_b.split()) > 3:
        return

    # Skip very short words (articles, single letters)
    if len(word_a) < 2 or len(word_b) < 2:
        return

    # Normalize order (alphabetical)
    a, b = sorted([word_a.lower(), word_b.lower()])

    pair, created = ConfusionPair.objects.get_or_create(user=user, word_a=a, word_b=b, defaults={"confusion_count": 1})
    if not created:
        pair.confusion_count += 1
        pair.resolved = False
        pair.save()


# ─────────────────────────────────────────────────────
# SPECIAL ROUNDS + HELPERS
# ─────────────────────────────────────────────────────


def _inject_special_rounds(result):
    """Post-build pass: dedupe only.

    The end-of-queue structure (continue_video then mastered_bridge) is
    enforced LATER in get_quiz_queue, AFTER `_apply_adaptive_difficulty`
    and `_apply_session_arc` finish reordering — otherwise those passes
    undo the structure.
    """
    return _deduplicate_queue(result)


def _enforce_quiz_structure(result, *, end_videos=3, end_bridges=3):
    """Reorder a quiz queue so the slow/cinematic items land at fixed
    end positions:

      positions [n-3..n-1]            → mastered_bridge (AI; slow)
      positions [n-6..n-4]            → continue_video  (cinematic, video reward)
      positions [0 .. n-7]            → everything else (random order from shuffle)

    Why fixed end positions:
      • Bridges take ~25-30s to generate. Putting them at the end gives
        the prefetcher (in quiz.html) a long runway to fire all of them
        in parallel while the learner is still working on q1-q9.
      • continue_video sits just before the AI section so the cinematic
        moment leads into the production drill — pedagogically clean.
      • Predictable ordering also makes the experience legible to the
        learner: "the videos come, then the AI section, then I'm done."

    Falls back gracefully:
      • Fewer than 3 of either type → cap shrinks to whatever's available.
      • Total queue length < 6 → return unchanged (not enough room to
        carve out an end section).
      • All same type (degenerate case) → return unchanged.
    """
    if not result or len(result) < 6:
        return result

    bridges = [it for it in result if it.get("quiz_type") == "mastered_bridge"]
    videos = [it for it in result if it.get("quiz_type") == "continue_video"]
    if not bridges and not videos:
        return result

    take_bridges = bridges[:end_bridges]
    take_videos = videos[:end_videos]

    # Avoid moving the same item twice — match by python identity.
    moved = {id(x) for x in take_bridges + take_videos}
    body = [it for it in result if id(it) not in moved]

    # End-section comes last in the natural reading order: body → videos → bridges.
    return body + take_videos + take_bridges


# ─────────────────────────────────────────────────────
# ADAPTIVE DIFFICULTY (target ~80% accuracy)
# ─────────────────────────────────────────────────────
# Research: 80-85% success rate = optimal learning zone.
# Too easy (>90%) = boredom, no learning. Too hard (<70%) = frustration.
# We check the user's recent accuracy and shift quiz types accordingly.


def _diversify_monotonous(result, all_english, all_translations, confusion_map, all_vocab=None):
    """When >70% of items share the same quiz type, diversify to prevent boredom.

    Typical trigger: first-time user with no mastery → all flash l1_l2.
    Converts ~40% of items to cloze, define, or listen while keeping majority
    as flash for recognition practice.
    """
    if len(result) < 5:
        return result

    from collections import Counter

    type_counts = Counter(item["quiz_type"] for item in result)
    dominant_type, dominant_count = type_counts.most_common(1)[0]
    if dominant_count / len(result) < 0.70:
        return result  # already diverse

    # Mix distribution: keep 60% dominant, convert 40% to variety types.
    # `define` (English→type-the-word) and `listen` (type-what-you-hear)
    # were here for variety but were rejected by the user. Variety pool
    # is now confined to types in active rotation.
    variety_types = ["sentence_cloze", "flash"]
    n_to_convert = int(len(result) * 0.40)
    converted = 0

    indices = list(range(len(result)))
    random.shuffle(indices)

    for i in indices:
        if converted >= n_to_convert:
            break
        item = result[i]
        if item["quiz_type"] != dominant_type:
            continue

        # Pick next variety type (round-robin)
        new_type = variety_types[converted % len(variety_types)]

        # sentence_cloze needs an example sentence to blank — fall back to flash.
        if new_type == "sentence_cloze" and not item.get("example"):
            new_type = "flash"

        item["quiz_type"] = new_type

        # Rebuild flash options when we downgrade to flash (the variety
        # type no longer fits the data).
        if new_type == "flash" and not item.get("options"):
            item["options"] = _build_flash_options(
                item["english"], all_english, confused_with=confusion_map.get(item["english"].lower(), [])
            )

        converted += 1

    return result


# Difficulty ladder for adaptive shifting — ACTIVE types only.
# Was: ["flash", "listen", "match", "define", "cloze", "pattern", "build", "produce"]
# Those legacy types leaked into queues whenever accuracy was off the 80-90% band
# (which is most sessions). Now confined to types the router actually uses.
# smart_mcq removed 2026-04-29 (see _quiz_type_and_direction docstring) so the
# adaptive shifter can't promote items into the empty-data failure mode.
_DIFFICULTY_LADDER = ["flash", "usage_check", "pattern_notice", "sentence_cloze", "quote_dash", "continue_video"]


def _apply_adaptive_difficulty(user, queue):
    """Shift quiz types up/down based on recent session accuracy to target ~80%.

    - Accuracy > 90%: bump ~40% of items UP one difficulty level (more challenge)
    - Accuracy 80-90%: sweet spot — no changes
    - Accuracy 70-80%: bump ~30% of items DOWN one level (ease off)
    - Accuracy < 70%: bump ~50% of items DOWN one level (struggling)
    """
    # Get accuracy from last 20 attempts (roughly last 1-2 sessions)
    recent = QuizAttempt.objects.filter(user=user).order_by("-created_at")[:20]
    if len(recent) < 5:
        return queue  # not enough data to adapt

    correct_count = sum(1 for a in recent if a.correct)
    accuracy = correct_count / len(recent)

    if 0.80 <= accuracy <= 0.90:
        return queue  # already in sweet spot

    if accuracy > 0.90:
        # Too easy — bump up
        fraction, direction = 0.40, 1
    elif accuracy >= 0.70:
        # Slightly hard — ease off a bit
        fraction, direction = 0.30, -1
    else:
        # Struggling — ease off more
        fraction, direction = 0.50, -1

    for item in queue:
        qt = item.get("quiz_type", "flash")
        if qt in ("speed", "match") or random.random() > fraction:
            continue  # skip special rounds and unselected items

        idx = _DIFFICULTY_LADDER.index(qt) if qt in _DIFFICULTY_LADDER else -1
        if idx < 0:
            continue

        new_idx = max(0, min(len(_DIFFICULTY_LADDER) - 1, idx + direction))
        item["quiz_type"] = _DIFFICULTY_LADDER[new_idx]

    return queue


# ─────────────────────────────────────────────────────
# SESSION ARC (easy → hard → easy)
# ─────────────────────────────────────────────────────
# Research: 80-85% success rate = optimal engagement.
# Start easy to build confidence, peak mid-session,
# end easy so user finishes feeling good (peak-end rule).

_QUIZ_DIFFICULTY = {
    "teach": 0,
    "flash": 1,
    "speed": 1,
    "listen": 2,
    "match": 3,
    "smart_mcq": 3,
    "pattern_notice": 3,
    "usage_check": 4,
    "define": 4,
    "cloze": 5,
    "sentence_cloze": 5,
    "pattern": 5,
    "quote_dash": 5,
    "translate_back": 6,
    "build": 6,
    "produce": 7,
    "parallel_produce": 7,
}

# High-load types: require typing free-form English OR sustained focus on long content.
# Should never appear back-to-back (sandwich rule, per Gemini Pro).
_HIGH_LOAD_TYPES = {
    "produce",
    "parallel_produce",
    "define",
    "cloze",
    "sentence_cloze",
    "build",
    "translate_back",
    "quote_dash",  # added: full-sentence production / video focus
}

# Light-load types: judgment, recognition, brief noticing — safe between heavy items.
_LIGHT_LOAD_TYPES = {"flash", "smart_mcq", "usage_check", "pattern_notice", "match", "listen"}


def _apply_session_arc(result):
    """Plan the session as a 5-act arc with deliberate sequencing.

    Act 1: Warmup (items 1-2) — easiest content, guaranteed wins to build momentum
    Act 2: Input (items 3-5) — new/weak words, scaffolded types
    Act 3: Practice (items 6-10) — mixed load, interleaved difficulty
    Act 4: Challenge (items 11-N-2) — hardest content at peak attention
    Act 5: Cooldown (last 2) — easy win, peak-end rule guarantees positive exit

    Post-ordering rules:
    - No same vocab within 3 positions (interleaving, Bjork)
    - No two high-load types back-to-back (cognitive pacing)
    - Alternate direction when possible (reduce context switching cost)
    """
    if len(result) < 5:
        return result

    # Pull out speed rounds — they stay at their injected position
    speed_items = [(i, item) for i, item in enumerate(result) if item.get("quiz_type") == "speed"]
    regular = [item for item in result if item.get("quiz_type") != "speed"]

    if len(regular) < 5:
        return result

    # Score each item by combined difficulty (type + word unfamiliarity)
    for item in regular:
        type_diff = _QUIZ_DIFFICULTY.get(item["quiz_type"], 3)
        mastery_diff = 1.0 - (item.get("p_known") or 0)
        item["_diff"] = type_diff + mastery_diff * 3  # mastery weight bumped: hard words matter more

    regular.sort(key=lambda x: x["_diff"])
    n = len(regular)

    # Act sizing — scales with batch size
    n_warmup = min(2, max(1, n // 8))
    n_cooldown = min(2, max(1, n // 8))

    # For warmup + cooldown we want LOW-LOAD type AND high p_known (guaranteed wins).
    # Sort candidates by: low-load-first (0 before 1), then by p_known descending.
    def _win_score(item):
        is_hard_load = 1 if item["quiz_type"] in _HIGH_LOAD_TYPES else 0
        return (is_hard_load, -(item.get("p_known") or 0))

    win_candidates = sorted(regular, key=_win_score)

    # Act 1 — Warmup: top N win-candidates (low-load, high p_known).
    # Prefer FLASH for item 1 if available — pure recognition primes dopamine fastest.
    flash_candidates = [x for x in win_candidates if x["quiz_type"] == "flash"]
    if flash_candidates:
        first = flash_candidates[0]
        rest = [x for x in win_candidates if id(x) != id(first)][: n_warmup - 1]
        warmup = [first] + rest
    else:
        warmup = win_candidates[:n_warmup]

    # Act 5 — Cooldown: peak-end win guarantee.
    # Final item should be FLASH on highest p_known if possible (Gemini Pro: pure victory lap).
    used_ids = set(id(x) for x in warmup)
    cooldown_pool = [x for x in win_candidates if id(x) not in used_ids]
    if cooldown_pool:
        # Find best flash candidate for the FINAL slot
        flash_pool = [x for x in cooldown_pool if x["quiz_type"] == "flash"]
        if flash_pool:
            final_item = flash_pool[0]
        else:
            final_item = cooldown_pool[0]  # fall back to easiest available win-candidate
        used_ids.add(id(final_item))
        # Penultimate slot = light-load type (usage_check/smart_mcq), randomized for variety
        next_best = [x for x in cooldown_pool if id(x) != id(final_item) and x["quiz_type"] not in _HIGH_LOAD_TYPES]
        random.shuffle(next_best)
        cooldown = next_best[: n_cooldown - 1] + [final_item]
        for x in cooldown:
            used_ids.add(id(x))
    else:
        cooldown = []

    # Middle — actively interleave heavy and light items to avoid back-to-back high-load.
    # Strategy: sort each pool by difficulty, then weave them together so that no
    # high-load item lands next to another whenever a light item is available.
    middle_pool = [x for x in regular if id(x) not in used_ids]
    middle_heavy = [x for x in middle_pool if x["quiz_type"] in _HIGH_LOAD_TYPES]
    middle_light = [x for x in middle_pool if x["quiz_type"] not in _HIGH_LOAD_TYPES]
    # Sort by difficulty so harder ones land in peak-attention zone (mid-late)
    middle_heavy.sort(key=lambda x: x["_diff"])
    middle_light.sort(key=lambda x: x["_diff"])
    random.shuffle(middle_light)  # light items get random ordering for variety

    # Weave: alternate heavy and light, starting with the lighter side if more lights.
    # If heavies > lights, we'll have unavoidable doubles — cluster them in the late-middle (peak attention).
    middle_items = []
    h_idx = 0
    l_idx = 0
    expect = "light" if len(middle_light) >= len(middle_heavy) else "heavy"
    while h_idx < len(middle_heavy) or l_idx < len(middle_light):
        if expect == "light" and l_idx < len(middle_light):
            middle_items.append(middle_light[l_idx])
            l_idx += 1
            expect = "heavy"
        elif expect == "heavy" and h_idx < len(middle_heavy):
            middle_items.append(middle_heavy[h_idx])
            h_idx += 1
            expect = "light"
        elif l_idx < len(middle_light):
            middle_items.append(middle_light[l_idx])
            l_idx += 1
        else:
            middle_items.append(middle_heavy[h_idx])
            h_idx += 1

    planned = warmup + middle_items + cooldown

    # ── Post-ordering rules ──────────────────────────────────────
    planned = _enforce_no_repeat_word(planned, min_gap=3)
    planned = _enforce_no_high_load_streak(planned)

    # Clean up temp key
    for item in planned:
        item.pop("_diff", None)

    # Re-insert speed rounds at their original positions
    for orig_idx, speed_item in speed_items:
        insert_at = min(orig_idx, len(planned))
        planned.insert(insert_at, speed_item)

    return planned


def _item_key(item):
    """Stable key for same-word detection."""
    return (item.get("vocab_id"), item.get("note_id"), (item.get("english") or "").lower())


def _enforce_no_repeat_word(items, min_gap=3):
    """Re-order so the same word never appears within `min_gap` positions.
    Preserves overall act structure as much as possible — only swaps adjacent repeats.
    """
    if len(items) <= min_gap:
        return items
    out = list(items)
    for i in range(len(out)):
        key = _item_key(out[i])
        # Look back min_gap positions for a collision
        window = [_item_key(out[j]) for j in range(max(0, i - min_gap), i)]
        if key in window:
            # Find a swap candidate later in the list that isn't in the window
            for j in range(i + 1, len(out)):
                cand_key = _item_key(out[j])
                if cand_key not in window and cand_key != key:
                    # Check the swap doesn't create a new collision at position j
                    j_window = [_item_key(out[k]) for k in range(max(0, j - min_gap), j) if k != i]
                    if key not in j_window:
                        out[i], out[j] = out[j], out[i]
                        break
    return out


def _enforce_no_high_load_streak(items):
    """Ensure no two high-load (free-form typing) items appear back-to-back.
    Swaps a high-load item with the nearest non-high-load one (forward or backward)
    to break streaks. Protects the final item (peak-end win).
    """
    if len(items) < 2:
        return items
    out = list(items)
    last_idx = len(out) - 1
    # Iterate multiple passes — one swap can create a new streak elsewhere
    for _ in range(3):
        changed = False
        for i in range(1, len(out)):
            if out[i]["quiz_type"] in _HIGH_LOAD_TYPES and out[i - 1]["quiz_type"] in _HIGH_LOAD_TYPES:
                # Search outward for a low-load to swap in — prefer forward (keep cooldown intact)
                swap_j = None
                for j in range(i + 1, len(out)):
                    if j == last_idx:
                        continue  # don't disturb the final-item win guarantee
                    if out[j]["quiz_type"] not in _HIGH_LOAD_TYPES:
                        swap_j = j
                        break
                if swap_j is None:
                    for j in range(i - 2, 0, -1):
                        if out[j]["quiz_type"] not in _HIGH_LOAD_TYPES:
                            swap_j = j
                            break
                if swap_j is not None:
                    out[i], out[swap_j] = out[swap_j], out[i]
                    changed = True
        if not changed:
            break
    return out


def _deduplicate_queue(result):
    seen = set()
    deduped = []
    for item in result:
        key = item["english"].lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _build_flash_options(correct, pool, n=4, confused_with=None):
    """Build MCQ options, prioritizing words the user has confused before."""
    # Deduplicate pool (case-insensitive) — prevents repeated options and infinite loops
    seen_lower = set()
    unique_pool = []
    for t in pool:
        if t.lower() not in seen_lower:
            seen_lower.add(t.lower())
            unique_pool.append(t)

    distractors = [t for t in unique_pool if t.lower() != correct.lower()]
    # No padding — only use genuinely unique distractors

    chosen = []
    # Prioritize confused words as distractors (max 2 to keep it fair)
    if confused_with:
        for cw in confused_with[:2]:
            if any(d.lower() == cw.lower() for d in distractors) and cw not in chosen:
                chosen.append(cw)

    # Fill remaining slots randomly
    remaining = [d for d in distractors if d not in chosen]
    need = min(n - 1, len(distractors)) - len(chosen)
    if need > 0 and remaining:
        chosen += random.sample(remaining, min(need, len(remaining)))

    options = chosen + [correct]
    random.shuffle(options)
    return options


def _load_confusion_map(user):
    """Load user's confusion pairs into a dict: word → [confused_with_words]."""
    pairs = ConfusionPair.objects.filter(user=user, resolved=False).values_list("word_a", "word_b")
    cmap = {}
    for a, b in pairs:
        cmap.setdefault(a, []).append(b)
        cmap.setdefault(b, []).append(a)
    return cmap


def _get_clip_data(vocab):
    clip_start, clip_end, video_url = None, None, ""
    if vocab.transcript:
        clip_start = float(vocab.transcript.start_time)
        clip_end = float(vocab.transcript.end_time)
        ep = getattr(vocab.transcript, "episode", None) or vocab.episode
        if ep and ep.video_file:
            video_url = ep.video_file.url
    return clip_start, clip_end, video_url


def _stage_to_level(stage, confidence):
    if stage == "mastered":
        return "mastered"
    if confidence >= 70:
        return "strong"
    if confidence >= 50:
        return "good"
    if confidence >= 30:
        return "shaky"
    return "weak"


def _detect_kind_from_word(word):
    if len(word.split()) >= 2:
        return "phrase"
    return "word"


def _kind_to_category(kind):
    """Map LineVocab/WordNote kind to weakness category field name."""
    return {
        "word": "single_word",
        "phrase": "phrase",
        "idiom": "idiom",
    }.get(kind, "single_word")


def _update_profile_stats(profile):
    """Recompute words_known, learning_rate, retention_rate from VocabMastery data.
    Called after every quiz answer to keep profile fresh."""
    import datetime

    now = timezone.now()

    # Words known: count of words with P(L) > 0.5
    profile.words_known = VocabMastery.objects.filter(user=profile.user, p_known__gt=0.5).count()

    # Learning rate: words that crossed P>0.5 in the last 7 days
    seven_days_ago = now - datetime.timedelta(days=7)
    recently_learned = VocabMastery.objects.filter(
        user=profile.user, p_known__gt=0.5, last_reviewed__gte=seven_days_ago
    ).count()
    profile.learning_rate = round(recently_learned / 7, 1)

    # Retention rate: of words that reached P>0.5 more than 7 days ago,
    # how many still have P>0.5?
    old_learned = VocabMastery.objects.filter(user=profile.user, last_reviewed__lt=seven_days_ago)
    total_old = old_learned.count()
    if total_old > 0:
        still_known = old_learned.filter(p_known__gt=0.5).count()
        profile.retention_rate = round(still_known / total_old, 2)
    else:
        profile.retention_rate = 0.0
