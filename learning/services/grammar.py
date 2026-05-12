"""Grammar service helpers — learner context + interest selection."""

from __future__ import annotations

import random

from learning.models import OnboardingSession, UserInterest


def build_learner_context(user) -> tuple[list[str], str, str]:
    """Return (interest_pool, user_name, user_level) for Gemini prompts.

    interest_pool: list of interest labels, forever-movie first.
    user_level: CEFR string (A1–C2), defaults to A2 when unknown.
    """
    forever_label = ""
    interest_labels: list[str] = []
    for ui in UserInterest.objects.filter(user=user):
        if ui.category == "forever_movie":
            if ui.items:
                forever_label = (ui.items[0].get("label") or "").strip()
            continue
        for item in (ui.items or [])[:5]:
            label = (item.get("label") or "").strip()
            if label:
                interest_labels.append(label)

    interest_pool = ([forever_label] if forever_label else []) + interest_labels[:12]
    user_name = (user.first_name or user.username or "").strip()

    level_map = {
        "beginner": "A2",
        "elementary": "A2",
        "intermediate": "B1",
        "upper_intermediate": "B2",
        "advanced": "C1",
        "fluent": "C2",
        "a1": "A1",
        "a2": "A2",
        "b1": "B1",
        "b2": "B2",
        "c1": "C1",
        "c2": "C2",
    }
    user_level = ""
    latest = OnboardingSession.objects.filter(user=user, completed_at__isnull=False).order_by("-completed_at").first()
    if latest and latest.level:
        user_level = level_map.get(latest.level.lower(), "")
    if not user_level and getattr(user, "proficiency_level", ""):
        user_level = level_map.get(user.proficiency_level.lower(), "")
    if not user_level:
        user_level = "A2"

    return interest_pool, user_name, user_level


def pick_interest(pool: list[str], already_used: list[str]) -> str:
    """Pick ONE interest from pool, avoiding recently used ones. Mutates already_used."""
    if not pool:
        return ""
    unused = [x for x in pool if x not in already_used]
    if not unused:
        already_used.clear()
        unused = pool[:]
    pick = random.choice(unused)
    already_used.append(pick)
    return pick
