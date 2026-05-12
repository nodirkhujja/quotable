"""Seed VocabMastery records from a completed OnboardingSession.

Pedagogical intent: advanced learners shouldn't grind Stage 1 for words they
already know. The placement test gave us per-word signal — use it.

Mapping policy:
    placement word marked KNOWN     →  p_known = 0.55  (Stage 3 — "good")
    placement word marked UNKNOWN   →  p_known = 0.05  (Stage 1 — focus here)
    word not in placement test      →  p_known unchanged (default BKT_P_INIT)

A single placement word (e.g., "fixate") may match many LineVocab phrases
(e.g., "Fixate on [something]") — all matches receive the same seed.
"""

import re

from learning.models import BKT_P_INIT, LineVocab, OnboardingResult, OnboardingSession, VocabMastery

P_KNOWN_KNOWN = 0.55  # Stage 3 — skip recognition, jump to judgment
P_KNOWN_UNKNOWN = 0.05  # Stage 1 — needs deep focus


def _normalize_word(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    s = (s or "").lower()
    s = re.sub(r"[\[\]()/.,!?\"']", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _word_matches_phrase(placement_word: str, phrase: str) -> bool:
    """Does the placement single-word appear as a token in the LineVocab phrase?"""
    pw = _normalize_word(placement_word)
    if not pw:
        return False
    tokens = _normalize_word(phrase).split()
    return pw in tokens


def seed_mastery_from_onboarding(user) -> dict:
    """Seed VocabMastery for `user` based on their completed onboarding.

    Returns: {"created": int, "updated": int, "matched_lv": int, "skipped": int}
    Idempotent — safe to re-run. Only WRITES when the new p_known would meaningfully
    change the existing record (avoids stomping on real practice data).
    """
    stats = {"created": 0, "updated": 0, "matched_lv": 0, "skipped": 0}

    session = OnboardingSession.objects.filter(user=user, completed_at__isnull=False).first()
    if not session:
        return stats

    results = list(OnboardingResult.objects.filter(session=session).select_related("word"))
    if not results:
        return stats

    # Pre-fetch all LineVocab once (small table — ~900 rows)
    all_lv = list(LineVocab.objects.only("id", "english"))

    # Pre-existing masteries — don't overwrite real practice data
    existing = {
        m.vocab_id: m for m in VocabMastery.objects.filter(user=user).only("id", "vocab_id", "p_known", "attempts")
    }

    # Track LineVocab IDs already seeded this run — multiple placement words may
    # match the same LineVocab (e.g., "go" + "through" both match "Go through").
    # When that happens, "known" wins (placement evidence trumps unknown).
    pending = {}  # vocab_id -> target_p
    for r in results:
        placement_word = r.word.word
        target_p = P_KNOWN_KNOWN if r.known else P_KNOWN_UNKNOWN
        for lv in all_lv:
            if not _word_matches_phrase(placement_word, lv.english):
                continue
            stats["matched_lv"] += 1
            # If multiple placement words match this LV, prefer the higher p (KNOWN > UNKNOWN)
            if lv.id not in pending or target_p > pending[lv.id]:
                pending[lv.id] = target_p

    for vocab_id, target_p in pending.items():
        mastery = existing.get(vocab_id)
        if mastery is None:
            VocabMastery.objects.create(
                user=user,
                vocab_id=vocab_id,
                p_known=target_p,
                mastery_score=target_p * 100,
                overall_score=target_p * 100,
                level="good" if target_p >= P_KNOWN_KNOWN else "weak",
            )
            stats["created"] += 1
        else:
            # Only nudge if user hasn't actually practiced AND p_known is at default
            if mastery.attempts == 0 and abs(mastery.p_known - BKT_P_INIT) < 0.01:
                mastery.p_known = target_p
                mastery.mastery_score = target_p * 100
                mastery.overall_score = target_p * 100
                mastery.level = "good" if target_p >= P_KNOWN_KNOWN else "weak"
                mastery.save(update_fields=["p_known", "mastery_score", "overall_score", "level"])
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

    return stats
