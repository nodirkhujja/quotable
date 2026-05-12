"""Words-at-risk counter — drives the red-dot notification badge.

A word is "at risk" when:
    - It's due for review now OR overdue, AND
    - It's not yet mastered (so losing it to forgetting has a real cost), AND
    - The user has actually practiced it (attempts > 0).

Used by the context processor to show "⚠ X words need review" on the nav.
"""

from django.utils import timezone

from vocab.models import VocabMastery


def count_words_at_risk(user) -> int:
    if not user or not user.is_authenticated:
        return 0
    return VocabMastery.objects.filter(
        user=user,
        next_review__lte=timezone.now(),
        p_known__lt=0.85,
        attempts__gt=0,
    ).count()
