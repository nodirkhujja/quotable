"""Tests for the smart_mcq distractor augmenter (Issue 1 of quiz audit).

Two tests:

1. `test_no_particle_only_distractors` — acceptance criterion from the audit
   spec. Generates MCQs for 50 phrasal-verb LineVocab rows, asserts no two
   options in any single MCQ share their head verb. The original bug was
   YAML-shipped option_b/option_c being particle-variants of option_a's
   verb (pull_off / pull_up / pull_out). This test ensures the augmenter
   never lets that pattern reach the quiz.

2. `test_distractor_tier_distribution` — regression detector. Checks the
   *generator logic*, not content quality. Confirms Tier 1 (`confusable_with`)
   stays the dominant source. If Tier 2/3 fallbacks start dominating, the
   topic-mismatch follow-up issue (TODO.md, 2026-04-26 entry) silently
   gets worse — this test catches that drift.

Both tests are isolated and don't hit external services.
"""

from __future__ import annotations

from django.test import TestCase

from learning.models import LineVocab
from learning.utils.quiz_distractors import _content_verbs_in, _root_verbs_collide, augment_smart_mcq


class SmartMcqDistractorTests(TestCase):
    """Run against the dev-DB sample of LineVocab. Tests assume the dev DB
    has a representative sample of phrasal verbs imported from the lexicon
    YAML — both the augmenter and these tests fail open if there aren't
    enough rows to sample (skip with a clear message).
    """

    SAMPLE_SIZE = 50

    @classmethod
    def setUpTestData(cls):
        # Pull a fixed sample of phrasal verbs that have YAML smart_mcq
        # data attached. Order by vocab_id for determinism; dedupe by
        # vocab_id in Python (Postgres-only DISTINCT ON isn't usable on
        # SQLite, which the test DB defaults to).
        seen = set()
        sample = []
        qs = (
            LineVocab.objects.filter(kind="phrase")
            .exclude(smart_mcq={})
            .exclude(vocab_id="")
            .order_by("vocab_id", "id")
        )
        for lv in qs.iterator():
            if lv.vocab_id in seen:
                continue
            seen.add(lv.vocab_id)
            sample.append(lv)
            if len(sample) >= cls.SAMPLE_SIZE:
                break
        cls.sample = sample

    def test_no_particle_only_distractors(self):
        """For each MCQ, no two options may share any content verb lemma.

        This is the strict acceptance criterion: a same-root collision
        between any pair of A/B/C means a particle-swap distractor leaked
        through (or some other shared-verb form). The expected count is 0
        across the entire sample.
        """
        if len(self.sample) < 5:
            self.skipTest(
                f"Dev DB only has {len(self.sample)} phrasal-verb LineVocab rows "
                f"with smart_mcq data; need ≥5 for a meaningful sample. "
                f"Run `make import-vocab` against the lexicon YAML first."
            )

        collisions = []
        for lv in self.sample:
            mcq = augment_smart_mcq(lv, lv.smart_mcq)
            opts = [mcq.get("option_a", ""), mcq.get("option_b", ""), mcq.get("option_c", "")]
            for i in range(3):
                for j in range(i + 1, 3):
                    if _root_verbs_collide(opts[i], opts[j]):
                        collisions.append((lv.vocab_id, i, j, opts[i], opts[j]))

        if collisions:
            msg_lines = [
                f"{len(collisions)} same-root-verb collisions across " f"{len(self.sample)} MCQs (expected 0):"
            ]
            for vid, i, j, oi, oj in collisions[:10]:  # cap output
                msg_lines.append(f"  {vid}: opt[{i}]={oi!r} vs opt[{j}]={oj!r}")
            self.fail("\n".join(msg_lines))

    def test_distractor_tier_distribution(self):
        """Generator-logic regression detector.

        Confirms Tier 1 (confusable_with) stays the dominant distractor
        source. If Tier 2/3 starts dominating, either YAML coverage of
        confusable_with regressed or the generator started preferring
        fallbacks. Either way, the topic-mismatch issue (see TODO.md,
        2026-04-26) gets worse silently — this test catches that.

        NOT a content-quality gate. Content quality (scenario coherence
        of distractors) is tracked separately as a follow-up issue.
        """
        if len(self.sample) < 5:
            self.skipTest(
                f"Dev DB only has {len(self.sample)} phrasal-verb LineVocab rows "
                f"with smart_mcq data; need ≥5 for a meaningful sample."
            )

        tier_counts = {
            "confusable_with": 0,
            "category_pool": 0,
            "antonym_pool": 0,
            "yaml_fallback": 0,
        }
        for lv in self.sample:
            mcq = augment_smart_mcq(lv, lv.smart_mcq, include_telemetry=True)
            for d in mcq.get("_distractors_meta", []):
                tier = d.get("source_tier", "unknown")
                tier_counts[tier] = tier_counts.get(tier, 0) + 1

        total = sum(tier_counts.values())
        self.assertGreater(total, 0, "no distractors generated — augmenter broken")

        # Print the distribution for visibility (test runner shows on -v 2).
        print(f"\nTier distribution across {total} distractors " f"({len(self.sample)} MCQs): {tier_counts}")

        # Floor: ≥30% from confusable_with. The lexicon stat says 64% of
        # phrasal verbs have ≥2 confusables — 30% is a conservative floor
        # accounting for runtime DB coverage gaps (some confusable IDs
        # aren't in this dev DB) and the same-root-verb filter pruning
        # confusables that share the target's verb (e.g. pull_out's
        # confusables both use "pull" → all rejected → falls to Tier 2).
        confusable_pct = tier_counts["confusable_with"] / total
        self.assertGreaterEqual(
            confusable_pct,
            0.30,
            f"Only {confusable_pct:.0%} of distractors come from "
            f"confusable_with. Either YAML coverage regressed or generator "
            f"started preferring fallbacks. Investigate before topic-match "
            f"issue dominates. See TODO.md 2026-04-26.",
        )


class ContentVerbExtractionTests(TestCase):
    """Unit-tests for _content_verbs_in — the core of the same-root check.

    Catches the regressions we've already seen:
      - "going" not lemmatizing to "go" (length threshold too strict)
      - "thing" mistaken for a verb (suffix-only path with no lemma anchor)
      - "I'd" / "let's" / "he's" producing weird tokens
      - The original bug: a verb deep in a sentence ("...that photo
        pulled me back in") not being detected because we only looked
        at the head.
    """

    def test_basic_phrasal_verbs(self):
        cases = [
            ("He pulled out a rabbit from the hat.", {"pull"}),
            ("They drifted apart after school.", {"drift"}),
            ("She broke up with him.", {"break"}),
        ]
        for sentence, expected in cases:
            with self.subTest(sentence=sentence):
                self.assertEqual(_content_verbs_in(sentence), expected)

    def test_progressive_aspect_lemmatizes(self):
        """The "going → go" regression. Length threshold needs to allow 5-char ing-words."""
        self.assertEqual(_content_verbs_in("He is going through hell."), {"go"})
        self.assertEqual(_content_verbs_in("She was waiting for the bus."), {"wait"})
        self.assertEqual(_content_verbs_in("They are working on it."), {"work"})

    def test_e_drop_irregulars_lemmatize(self):
        """The "loved → love" regression. E-restore in _stem."""
        self.assertEqual(_content_verbs_in("I loved that movie."), {"love"})
        self.assertEqual(_content_verbs_in("She lived alone for years."), {"live"})

    def test_nouns_with_verb_suffixes_are_not_verbs(self):
        """The "thing" regression. -ing/-ed alone don't make a verb — the
        lemma must be in our common-verb set OR the irregular table.
        """
        # "thing" → stem "th" → not in COMMON_VERB → rejected (good).
        self.assertEqual(_content_verbs_in("That thing on the table is mine."), set())
        # "king" → stem "k" → not in COMMON_VERB → rejected.
        self.assertEqual(_content_verbs_in("The king sat on the throne."), {"sit"})  # only 'sat'

    def test_contractions_dont_break(self):
        """The 'i'd / let's / he's → garbage roots' regression."""
        # No assertion on exact set — just that it doesn't return weird
        # roots like "i'd" or "let'". Run the extractor and confirm output
        # is either a clean lemma or empty.
        for s in ("I'd like the lasagna.", "Let's split the bill.", "He's been working."):
            verbs = _content_verbs_in(s)
            for v in verbs:
                self.assertNotIn("'", v, f"contraction leaked into verb {v!r} from {s!r}")

    def test_collision_detection_catches_deep_verb(self):
        """The original "pulled in a clause" regression — verb deep in a
        subordinate clause was missed when we only looked at the head.
        """
        a = "He pulled out a rabbit from the hat."
        b = "Just when I thought I was over it, that photo pulled me back in."
        self.assertTrue(_root_verbs_collide(a, b), "should detect both sentences contain 'pull'")

    def test_aux_only_sentences_dont_collide(self):
        """An aux-only sentence has empty verb set → no collision possible
        (and importantly, no false-positive collisions either).
        """
        a = "Do you have your jacket on?"  # only aux verbs
        b = "He pulled out his keys."  # has 'pull'
        # Empty set & {pull} = empty → no collision. This is the desired
        # behavior because aux-only sentences don't carry a meaningful
        # collision target.
        self.assertFalse(_root_verbs_collide(a, b))


class FrontendPayloadStrippingTests(TestCase):
    """Diagnostic fields (`_distractors_meta`, `_root_verbs`) MUST NOT
    appear in the default augmenter output — only when telemetry is
    explicitly requested for tests/analytics.
    """

    @classmethod
    def setUpTestData(cls):
        cls.lv = LineVocab.objects.filter(kind="phrase").exclude(smart_mcq={}).exclude(vocab_id="").first()

    def test_default_output_has_no_telemetry(self):
        if not self.lv:
            self.skipTest("dev DB has no phrasal-verb LineVocab to test against")
        out = augment_smart_mcq(self.lv, self.lv.smart_mcq)
        # Frontend-visible keys only.
        self.assertNotIn("_distractors_meta", out)
        self.assertNotIn("_root_verbs", out)
        self.assertNotIn("_distractor_sources", out)
        # Required keys still present.
        for k in ("scenario", "option_a", "option_b", "option_c", "correct"):
            self.assertIn(k, out)

    def test_telemetry_flag_exposes_meta(self):
        if not self.lv:
            self.skipTest("dev DB has no phrasal-verb LineVocab to test against")
        out = augment_smart_mcq(self.lv, self.lv.smart_mcq, include_telemetry=True)
        self.assertIn("_distractors_meta", out)
        meta = out["_distractors_meta"]
        self.assertEqual(len(meta), 2, "should be 2 distractors (excluding correct)")
        for d in meta:
            self.assertIn("text", d)
            self.assertIn("source_tier", d)
            self.assertIn(d["source_tier"], {"confusable_with", "category_pool", "antonym_pool", "yaml_fallback"})
            # source_word_id is optional (None for yaml_fallback)
            self.assertIn("source_word_id", d)
