# Follow-up issues

Project-tracker convention. New entries get appended at the bottom with a date
heading. Each entry is self-contained — title, severity, body, resolution path.

---

## 2026-04-26 — Smart MCQ distractors lack scenario coherence (Tier 2/3 fallback)

**Severity:** Medium
**Origin:** Discovered while shipping Issue 1 of the quiz audit (particle-swap fix).
**Affected:** `learning/utils/quiz_distractors.py`, ~36% of phrasal verbs at runtime
(those with empty/sparse `confusable_with` lists in the lexicon).

### Problem

When `confusable_with` is empty (~36% of phrasal verbs) or has only one entry,
Tier 2 (category_pool) and Tier 3 (antonym) distractors are pulled from OTHER
words' canonical sentences. Those sentences carry their *own* scenarios, so the
final 3-option set has topic-mismatched distractors — and a learner can pick
the right answer by topic-match without engaging with verb meaning.

Example (`pull_out`):

```
Scenario: "A magician reaches into his hat and removes a rabbit."
A) The package arrived at the distribution center this morning.   ← Tier 2
B) The child looked down in shame.                                ← Tier 2
C) He pulled out a rabbit from the hat.                           ← correct
```

The learner can pick C because it's the only option mentioning a magician/hat/
rabbit. The original particle-swap bug (three options sharing root verb `pull`)
is gone, but a new shallow-heuristic mode is in its place. Less damaging than
particle-swap (different verbs force the learner past the first word) but still
allows fake mastery.

### Resolution path

Content authoring, not code. For each of the ~408 words with `confusable_with`
populated, regenerate `option_b` and `option_c` in `media/translation/piw/lexicon.yaml`
with scenario-coherent distractors using LLM-based authoring + a human review pass.
Estimated 1-2 weeks of content work. Out of scope for code-fix PRs; needs a
separate content sprint.

### Detection

- Track per-MCQ which tier each distractor came from (already done — see
  `_distractors_meta` when `augment_smart_mcq(..., include_telemetry=True)`).
- Track student answer time on smart_mcq questions. Topic-match wins tend to
  be unusually fast — anomalously low response time is a soft signal.
- Add an analytics dashboard query showing average response time per
  (word_id, tier-mix) bucket. If `mostly_tier_2_or_3` items have median time
  <2s, content quality is the bottleneck.

### Verifying the issue exists

Run the demo from the Issue 1 PR description (5 specific words: `pull_out`,
`go_through`, `drift_apart`, `have_it_on`, `impending_doom`). Cases with empty
`confusable_with` (e.g. `have_it_on`) show the issue most clearly.

---

## 2026-04-26 — free_production gate misses irregular-verb past tenses

**Severity:** Medium
**Origin:** Discovered while writing tests for free_production (T8 quiz_type).
**Affected:** `learning/utils/ai_quiz.py:_word_used_in_reply` — used as a
server-side gate by `grade_free_production` and `grade_personal_sentence`.

### Problem

The cheap-morphology candidate generator covers regular inflections (-s/-es/
-ed/-ing/-ies/-ied/-er/-est) but knows nothing about irregular verbs. So a
learner who writes "I broke up with my boyfriend last year" for the target
phrase "break up" hits the gate's "word not used" branch and gets `score=0`
without an AI call.

For `grade_personal_sentence` (existing) this is mild: the function is
generous, the worst case is the learner sees "you forgot to use the word"
and re-submits. For `grade_free_production` (new) it's more damaging
because the gate sits BEFORE the AI grader, and the result feeds directly
into BKT mastery — a false negative is a real mastery hit.

The grading test suite worked around this by using regular-morphology
phrases ("walk through", "figure out") instead of irregular-past forms.
That avoidance leaks into a real learner gap when they naturally write
"went through" / "broke up" / "drove home" / "gave up".

### Resolution path

Two reasonable options:

1. **Small irregular-verb table** in `_inflect_lemma`. Cover the common
   ~50 head verbs that appear in phrasal verbs (go/went/gone, break/broke/
   broken, drive/drove/driven, take/took/taken, etc.). Single dict + a
   lookup before the regular-morphology branch. Low effort, covers ~95%
   of everyday irregular use.

2. **Drop the server-side gate for free_production** — let the AI decide.
   The AI prompt already has "phrase_used_correctly" as one of the five
   axes; it sees the full text and the target phrase. Cost: every empty/
   off-topic submission costs an AI call. Acceptable given the per-session
   cap of 2 free_production items.

Recommend option 1 — keeps the cheap fail-fast for actually-empty submissions
while admitting the most common irregular forms.

### Detection

Add logging in `_word_used_in_reply` for the multi-word path: log each
"failed-to-find" event with `word=` and `reply_first_30_chars=`. Grep the
logs after a week of free_production traffic — if "broke up" / "went through"
patterns dominate, ship option 1.
