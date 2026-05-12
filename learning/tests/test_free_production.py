"""Tests for the free_production quiz_type (Webb's active-vocabulary metric).

Two test groups:

1. AI grader smoke test (mocked) — confirms grade_free_production parses
   the AI's JSON response correctly, applies server-side gates (word used,
   minimum length), and returns the expected score/correct/axes/feedback
   shape. Network is fully mocked; no Gemini calls.

2. Stage routing + cap — confirms the queue-builder cap of 2
   free_production items per session works: extras get downgraded to a
   different production type, none are silently dropped.

Both groups isolated, no external services, no DB seeding required.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase

from learning.utils.ai_quiz import build_free_production_payload, grade_free_production
from learning.utils.quiz_engine import _cap_free_production

# ── Group 1: AI grader smoke (mocked) ────────────────────────────────────────


class GradeFreeProductionTests(TestCase):
    """Mocks _call_gemini so no network. Verifies the grader's contract."""

    def _mock_ai(self, **overrides):
        """Build a default mock payload + apply overrides."""
        payload = {
            "phrase_used_correctly": True,
            "phrase_semantic": True,
            "personal": True,
            "grammar_ok": True,
            "collocations_used": True,
            "score": 3,
            "feedback_uz": "Zo'r! Mukammal gap.",
        }
        payload.update(overrides)
        return payload

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_full_pass_returns_score_3(self, mock_call):
        mock_call.return_value = self._mock_ai()
        result = grade_free_production(
            word="pull out",
            translation="chiqarib olmoq",
            user_answer=(
                "Yesterday my friend pulled out his keys to open the door. I was waiting outside in the cold."
            ),
            user_level="B1",
            kind="phrasal_verb",
        )
        self.assertEqual(result["score"], 3)
        self.assertTrue(result["correct"])
        self.assertFalse(result["ai_unavailable"])
        self.assertEqual(result["feedback_uz"], "Zo'r! Mukammal gap.")
        for axis in ("phrase_used_correctly", "phrase_semantic", "personal", "grammar_ok", "collocations_used"):
            self.assertTrue(result["axes"][axis], f"axis {axis} should be true")

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_score_2_is_correct(self, mock_call):
        mock_call.return_value = self._mock_ai(
            score=2,
            collocations_used=False,
            feedback_uz="Yaxshi — kichik tabiiy ifoda kamchiligi bor.",
        )
        result = grade_free_production(
            word="drift apart",
            translation="bir-biridan uzoqlashmoq",
            user_answer="My cousin and I drifted apart after he moved to another city last year.",
        )
        self.assertEqual(result["score"], 2)
        self.assertTrue(result["correct"], "score 2 must be 'correct' for mastery")

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_score_1_is_wrong(self, mock_call):
        mock_call.return_value = self._mock_ai(score=1, phrase_semantic=False)
        # Use a regular-morphology phrase ("walking through" inflects "walk")
        # so the cheap gate finds it; irregular past tense (e.g. "went through")
        # is a known gap — the AI grader handles it generously when reachable.
        result = grade_free_production(
            word="walk through",
            translation="ko'rib chiqmoq",
            user_answer="My teacher walked through the lesson plan with me yesterday morning carefully.",
        )
        self.assertEqual(result["score"], 1)
        self.assertFalse(result["correct"], "score < 2 must NOT be correct")

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_word_not_used_short_circuits_no_ai_call(self, mock_call):
        result = grade_free_production(
            word="pull out",
            translation="chiqarib olmoq",
            user_answer="I went to the store yesterday and bought some milk.",
        )
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["correct"])
        self.assertFalse(result["ai_unavailable"])
        mock_call.assert_not_called()

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_too_short_short_circuits_no_ai_call(self, mock_call):
        result = grade_free_production(
            word="pull out",
            translation="chiqarib olmoq",
            user_answer="I pulled out.",  # only 3 words
        )
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["correct"])
        mock_call.assert_not_called()

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_ai_unavailable_returns_lenient_pass(self, mock_call):
        # All keys exhausted / model down — _call_gemini returns None.
        mock_call.return_value = None
        result = grade_free_production(
            word="figure out",
            translation="topib olmoq",
            user_answer="I am figuring out which course to take next semester at university right now.",
        )
        self.assertTrue(result["ai_unavailable"])
        self.assertEqual(result["score"], 2)
        self.assertTrue(result["correct"], "AI down + server gates passed → lenient pass")
        mock_call.assert_called_once()

    @patch("learning.utils.ai_quiz._call_gemini")
    def test_garbage_score_clamps_to_range(self, mock_call):
        # Defensive: Gemini sometimes returns score as string or out-of-range.
        mock_call.return_value = self._mock_ai(score="99")
        result = grade_free_production(
            word="pull out",
            translation="chiqarib olmoq",
            user_answer="I pulled out my phone to check the time at the meeting yesterday.",
        )
        self.assertEqual(result["score"], 3, "score should clamp to max 3")

        mock_call.return_value = self._mock_ai(score=-5)
        result = grade_free_production(
            word="pull out",
            translation="chiqarib olmoq",
            user_answer="I pulled out my phone to check the time at the meeting yesterday.",
        )
        self.assertEqual(result["score"], 0, "score should clamp to min 0")


# ── Group 2: prompt-template dispatch ────────────────────────────────────────


class FreeProductionPromptTests(TestCase):
    """Per-category prompt templates surface the right life-context anchor."""

    def test_idiom_uses_phrase_template(self):
        p = build_free_production_payload(
            word="break the ice",
            translation="muloqotni boshlamoq",
            kind="idiom",
            category="general",
        )
        self.assertIn("break the ice", p["prompt_uz"])
        self.assertIn("muloqotni boshlamoq", p["prompt_uz"])
        self.assertIn("mos kelgan", p["prompt_uz"])

    def test_emotions_uses_introspective_template(self):
        p = build_free_production_payload(
            word="overwhelmed",
            translation="bosh aylanmoq",
            kind="adj",
            category="emotions",
        )
        self.assertIn("his", p["prompt_uz"].lower())  # "his" = feeling

    def test_relationships_uses_people_template(self):
        p = build_free_production_payload(
            word="catch up",
            translation="ko'rishmoq",
            kind="phrasal_verb",
            category="relationships",
        )
        # phrasal_verb wins over relationships category — first-match.
        self.assertIn("catch up", p["prompt_uz"])

    def test_unknown_category_falls_back(self):
        p = build_free_production_payload(
            word="figure out",
            translation="topib olmoq",
            kind="phrasal_verb",
            category="zzz_made_up_category",
        )
        # phrasal_verb hits first, so we still get the idiom/phrasal template.
        self.assertIn("figure out", p["prompt_uz"])

    def test_pure_default_fallback(self):
        # No matching kind, no matching category — must hit the default.
        p = build_free_production_payload(
            word="hello",
            translation="salom",
            kind="",
            category="",
        )
        self.assertTrue(p["prompt_uz"])  # non-empty
        self.assertIn("hello", p["prompt_uz"])
        self.assertIn("salom", p["prompt_uz"])

    def test_payload_has_word_count_bounds(self):
        p = build_free_production_payload(
            word="x",
            translation="y",
            kind="",
            category="",
        )
        self.assertEqual(p["min_words"], 15)
        self.assertEqual(p["max_words"], 100)
        self.assertTrue(p["hint_uz"])


# ── Group 3: per-session cap ─────────────────────────────────────────────────


class FreeProductionCapTests(TestCase):
    """The cap is the audit-spec acceptance criterion: max 2 free_production
    per session, others downgraded in-place. Without it, a learner with 8
    saved-words at PRODUCTION stage could face 8 cognitively-heavy AI
    grading rounds in one go.
    """

    def _item(self, qt, **extras):
        """Minimal item shape — what the cap function actually inspects."""
        item = {"quiz_type": qt}
        item.update(extras)
        return item

    def test_cap_at_two_with_downgrade_to_translate_back(self):
        queue = [
            self._item("free_production", translate_back={"uz": "X", "en": "Y"}),
            self._item("flash"),
            self._item("free_production", translate_back={"uz": "X", "en": "Y"}),
            self._item("free_production", translate_back={"uz": "X", "en": "Y"}),
            self._item("free_production", translate_back={"uz": "X", "en": "Y"}),
        ]
        out = _cap_free_production(queue, max_per_session=2)
        types = [i["quiz_type"] for i in out]
        self.assertEqual(types.count("free_production"), 2, "cap should leave exactly 2 free_production items")
        self.assertEqual(
            types.count("translate_back"), 2, "extras should downgrade to translate_back when payload is present"
        )
        # First two free_production items survive (FIFO order).
        self.assertEqual(out[0]["quiz_type"], "free_production")
        self.assertEqual(out[2]["quiz_type"], "free_production")

    def test_cap_strips_unused_payload(self):
        queue = [
            self._item("free_production", free_production={"prompt_uz": "1"}, translate_back={"uz": "X", "en": "Y"}),
            self._item("free_production", free_production={"prompt_uz": "2"}, translate_back={"uz": "X", "en": "Y"}),
            self._item("free_production", free_production={"prompt_uz": "3"}, translate_back={"uz": "X", "en": "Y"}),
        ]
        out = _cap_free_production(queue, max_per_session=2)
        # Third item downgraded → its free_production payload should be stripped
        # so the wire is clean.
        self.assertEqual(out[2]["quiz_type"], "translate_back")
        self.assertNotIn("free_production", out[2])
        # Surviving items keep their payload.
        self.assertIn("free_production", out[0])
        self.assertIn("free_production", out[1])

    def test_cap_falls_through_to_sentence_cloze(self):
        queue = [
            self._item("free_production", sentence_cloze={"blanked": "x", "answer": "y"}),
            self._item("free_production", sentence_cloze={"blanked": "x", "answer": "y"}),
            self._item("free_production", sentence_cloze={"blanked": "x", "answer": "y"}),
        ]
        out = _cap_free_production(queue, max_per_session=2)
        self.assertEqual(
            out[2]["quiz_type"], "sentence_cloze", "no translate_back available → fall through to sentence_cloze"
        )

    def test_cap_falls_through_to_flash_when_no_payload(self):
        queue = [
            self._item("free_production"),
            self._item("free_production"),
            self._item("free_production"),  # no production payload at all
        ]
        out = _cap_free_production(queue, max_per_session=2)
        self.assertEqual(
            out[2]["quiz_type"], "flash", "no production payloads available → final fall-through is flash"
        )

    def test_cap_no_op_when_under_limit(self):
        queue = [
            self._item("free_production", translate_back={"uz": "X", "en": "Y"}),
            self._item("flash"),
            self._item("free_production", translate_back={"uz": "X", "en": "Y"}),
        ]
        out = _cap_free_production(queue, max_per_session=2)
        types = [i["quiz_type"] for i in out]
        self.assertEqual(types, ["free_production", "flash", "free_production"])

    def test_cap_handles_empty_queue(self):
        self.assertEqual(_cap_free_production([], 2), [])
        self.assertEqual(_cap_free_production(None, 2), None)
