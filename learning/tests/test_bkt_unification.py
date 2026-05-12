"""BKT unification + spacing-effect tests.

Three things this verifies, all critical:

1. **Saved-mode submit uses BKT math.** A correct answer at p_known≈0.5
   produces a much bigger confidence bump than the old +5 ladder did,
   because BKT credits the strong evidence — that's the *point* of using it.

2. **Spacing-effect protection still works.** A fresh WordNote (created
   `now()`) hit with 5 consecutive correct answers MUST NOT reach the
   `mastered` stage, even when the BKT math takes confidence well past 85.
   The age-gate (>=24h) blocks it. This is the "BKT obliterates spacing
   effect" regression we already saw once — this test is its watchdog.

3. **mastery_state() is the single source of truth.** Returns the same
   float regardless of how the word is identified (note_id / vocab_id /
   word string).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from learning.models import VocabMastery, WordNote, bkt_update
from learning.utils.quiz_engine import _submit_answer_inner, mastery_state

User = get_user_model()


def _make_user(suffix=""):
    return User.objects.create_user(
        username=f"bkt_test{suffix}",
        email=f"bkt{suffix}@test.local",
        password="x",
    )


def _make_note(user, *, word="grab", confidence=0, stage="inbox", age_hours=0):
    note = WordNote.objects.create(
        user=user,
        word=word,
        translation="yulqib ushlamoq",
        confidence=confidence,
        stage=stage,
    )
    if age_hours:
        # Backdate created_at so age-gate tests can simulate a real-world word.
        new_created = timezone.now() - timedelta(hours=age_hours)
        WordNote.objects.filter(pk=note.pk).update(created_at=new_created)
        note.refresh_from_db()
    return note


def _submit(user, note, *, correct, quiz_type="translate_back"):
    """Run one saved-mode submit through the real path."""
    from learning.models import UserLearningProfile

    profile, _ = UserLearningProfile.objects.get_or_create(user=user)
    return _submit_answer_inner(
        user=user,
        profile=profile,
        vocab_id=None,
        note_id=note.id,
        quiz_type=quiz_type,
        correct=correct,
        user_answer="x" if correct else "",
        chosen_wrong="",
        response_time_ms=0,
        direction="l2_l1",
        bonus_multiplier=1.0,
        used_hint=False,
        skipped=False,
    )


class BKTReplacesLadderTests(TestCase):
    """The math is now BKT, not the +8/+5/+3/+2 logistic ladder."""

    def test_correct_answer_uses_bkt_math(self):
        """A correct translate_back at confidence 50 should jump well past
        +5 (the old ladder gain at that band) — that's the whole point of
        switching to BKT, which credits strong evidence."""
        user = _make_user("_a")
        note = _make_note(user, confidence=50, stage="learning", age_hours=48)
        _submit(user, note, correct=True, quiz_type="translate_back")
        note.refresh_from_db()
        # Old ladder at c=50: gain=5 → confidence 55.
        # BKT at p=0.5, translate_back: posterior + transition produces
        # ~96–98 confidence. We assert at least 80, far above ladder's 55.
        self.assertGreater(
            note.confidence,
            80,
            f"BKT bump should be much larger than the old ladder's +5; got {note.confidence} (was 50, expected >80).",
        )

    def test_wrong_answer_uses_bkt_math(self):
        """A wrong answer drops confidence per Bayes, not a flat -5."""
        user = _make_user("_b")
        note = _make_note(user, confidence=80, stage="learning")
        _submit(user, note, correct=False, quiz_type="translate_back")
        note.refresh_from_db()
        # Old ladder: 80 - 5 = 75. BKT drops more aggressively at high p
        # because a wrong answer is strong evidence the word ISN'T known.
        self.assertLess(
            note.confidence,
            75,
            f"BKT wrong-answer drop should exceed the old -5 flat; got {note.confidence} (was 80, expected <75).",
        )


class SpacingEffectGateTests(TestCase):
    """The math gives belief; the gate enforces conservatism. A fresh word
    must NOT fast-track to mastered in one session, no matter how high BKT
    drives confidence.
    """

    def test_fresh_word_5_corrects_does_not_reach_mastered(self):
        """The single regression target: BKT must not break this."""
        user = _make_user("_c")
        note = _make_note(user, confidence=0, stage="inbox", age_hours=0)
        for _ in range(5):
            _submit(user, note, correct=True, quiz_type="translate_back")
        note.refresh_from_db()
        # BKT will drive confidence well past 85 by the second or third correct.
        # The age-gate (≥24h since created_at) MUST keep stage at "learning".
        self.assertGreaterEqual(
            note.confidence, 85, "BKT should drive confidence past 85 quickly — if this fails, the math regressed."
        )
        self.assertEqual(
            note.stage,
            "learning",
            "Spacing-effect gate failed — fresh word reached 'mastered' "
            "in one session. The age + anti-grind gates are the protection; "
            "if this fails, the gates were removed or BKT bypassed them.",
        )

    def test_aged_word_high_confidence_does_reach_mastered(self):
        """The other side: when the gates are SATISFIED, mastery WORKS.
        Without this we'd have a bug where mastered is unreachable."""
        user = _make_user("_d")
        # Word is 48h old, currently at confidence 80, stage learning.
        note = _make_note(user, confidence=80, stage="learning", age_hours=48)
        # One more correct should push past 85 and the age-gate is satisfied;
        # anti-grind allows up to 4 corrects in 15min — first attempt is ≤4.
        _submit(user, note, correct=True, quiz_type="translate_back")
        note.refresh_from_db()
        self.assertEqual(
            note.stage,
            "mastered",
            "Aged word + age_ok + anti-grind_ok + confidence>=85 should "
            "have transitioned to mastered. Got stage=" + note.stage,
        )


class MasteryStateTests(TestCase):
    """mastery_state() is the canonical lookup. Same float regardless of
    how the word is identified."""

    def test_returns_default_when_no_record(self):
        from learning.models import BKT_P_INIT

        user = _make_user("_e")
        self.assertEqual(mastery_state(user, word="never_seen"), BKT_P_INIT)
        self.assertEqual(mastery_state(user, vocab_id=99999), BKT_P_INIT)
        self.assertEqual(mastery_state(user, note_id=99999), 0.0)
        # note_id miss returns 0.0 (no record means confidence=0/100).
        # vocab_id and word fall to BKT_P_INIT (same default the BKT model uses).

    def test_note_id_lookup(self):
        user = _make_user("_f")
        note = _make_note(user, confidence=72)
        self.assertAlmostEqual(mastery_state(user, note_id=note.id), 0.72)

    def test_word_string_lookup_matches_note_id_lookup(self):
        user = _make_user("_g")
        note = _make_note(user, word="grab", confidence=63)
        from_note_id = mastery_state(user, note_id=note.id)
        from_word = mastery_state(user, word="grab")
        self.assertAlmostEqual(
            from_note_id,
            from_word,
            msg="word-string lookup must agree with note_id lookup; "
            "this is the whole point of mastery_state being canonical.",
        )
