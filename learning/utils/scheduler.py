"""Spaced repetition scheduler for user-saved words (WordNote).

Uses a Leitner-style fixed-interval schedule keyed by the number of successful
reviews. Simpler than full SM-2 (no ease factor tuning) but captures the core
benefit: intervals grow multiplicatively so each successful review buys
exponentially more retention time.

Psychology: Cepeda et al. (2008) meta-analysis — spaced practice yields
200%+ retention improvement over massed practice. The intervals below are
tuned around Anki defaults: 1d, 3d, 7d, 2w, 5w, 3mo, 6mo.

On imperfect review: step back one bucket and schedule 1 day out. Mistakes
drop you down the ladder but don't reset to zero — that would be too harsh
for language learners who routinely slip on a single dimension (pronunciation,
spelling) without losing the word entirely.
"""

from datetime import timedelta

from django.utils import timezone

# Days between reviews, indexed by review_count AFTER the current review is
# recorded. Bucket 0 = brand new (never reviewed). Bucket 7+ clamps to 180d.
INTERVALS_DAYS = [1, 3, 7, 14, 35, 90, 180]


def _bucket_to_days(bucket: int) -> int:
    if bucket < 0:
        return 1
    if bucket >= len(INTERVALS_DAYS):
        return INTERVALS_DAYS[-1]
    return INTERVALS_DAYS[bucket]


def compute_next_review(current_count: int, perfect: bool):
    """Return (new_count, interval_days, next_review_datetime).

    current_count: the word's existing review_count (0 = never reviewed).
    perfect: True if the learner passed every round of the Challenge.
    """
    if perfect:
        new_count = current_count + 1
        # Index by prior count — so 1st review → 1d, 2nd → 3d, 3rd → 7d, …
        # Matches Ebbinghaus "1 day after first exposure" finding.
        days = _bucket_to_days(current_count)
    else:
        # Slip one bucket back, re-expose tomorrow. Not a hard reset.
        new_count = max(0, current_count - 1)
        days = 1
    next_at = timezone.now() + timedelta(days=days)
    return new_count, days, next_at


def is_due(note) -> bool:
    """A WordNote is due if never reviewed or its next_review has passed."""
    if note.next_review is None:
        return True
    return note.next_review <= timezone.now()
