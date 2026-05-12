import base64
import json
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from clips.models import Quote, Transcript
from learning.models import (
    DailyActivity,
    FavoriteQuote,
    FlashcardAttempt,
    GrammarPracticeLog,
    LearningProgress,
    LineVocab,
    LookupLog,
    OnboardingResult,
    OnboardingSession,
    PageVisit,
    QuoteMastery,
    ShadowingLog,
    SourceProgress,
    SuggestedWord,
    UserInterest,
    VocabWord,
    WordNote,
)
from learning.utils.ai_sentence import check_sentence as ai_check_sentence
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success


def _get_practice_words(user, limit=20):
    """Fetch enriched WordNotes with video for practice modes."""
    notes = list(
        WordNote.objects.filter(user=user)
        .exclude(quote=None, transcript=None)
        .select_related(
            "quote",
            "quote__source",
            "quote__episode",
            "transcript",
            "transcript__source",
            "transcript__episode",
        )
        .order_by("confidence", "?")[:limit]
    )
    for note in notes:
        _enrich_note_with_video(note)
    return [n for n in notes if n.video_url]


def _build_learner_context(user):
    """Collect the personalization payload every grammar-AI view needs.

    Shared between GrammarAIQuestionsView and the check views so they all
    pass identical context to Gemini.

    Returns: (interest_pool, user_name, user_level)
      - interest_pool: list[str] of individual interests (the forever-movie
        is guaranteed first if set). Callers pick ONE per generation call so
        each AI question is built around a single coherent subject.
    """
    forever_label = ""
    interest_labels = []
    for ui in UserInterest.objects.filter(user=user):
        if ui.category == "forever_movie":
            if ui.items:
                forever_label = (ui.items[0].get("label") or "").strip()
            continue
        for item in (ui.items or [])[:5]:
            label = (item.get("label") or "").strip()
            if label:
                interest_labels.append(label)

    # Forever-rewatch first — it's the strongest emotional anchor.
    interest_pool = []
    if forever_label:
        interest_pool.append(forever_label)
    interest_pool.extend(interest_labels[:12])

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
    latest = (
        OnboardingSession.objects.filter(
            user=user,
            completed_at__isnull=False,
        )
        .order_by("-completed_at")
        .first()
    )
    if latest and latest.level:
        user_level = level_map.get(latest.level.lower(), "")
    if not user_level and getattr(user, "proficiency_level", ""):
        user_level = level_map.get(user.proficiency_level.lower(), "")
    if not user_level:
        user_level = "A2"

    return interest_pool, user_name, user_level


def _pick_interests(pool: list[str], already_used: list[str]) -> str:
    """Pick ONE interest from the pool, avoiding recent picks where possible.

    Returns the chosen interest (or "" if pool is empty). Mutates already_used.
    """
    import random

    if not pool:
        return ""
    unused = [x for x in pool if x not in already_used]
    if not unused:
        already_used.clear()  # all used — reset the avoidance list
        unused = pool[:]
    pick = random.choice(unused)
    already_used.append(pick)
    return pick


# ── Per-user rate limits for Gemini-backed endpoints ────────────────────────
# Protects the shared Gemini quota: one misbehaving user can't burn everyone's
# requests. Limits are loose enough that a normal session never hits them.

_AI_RATE_LIMITS = {
    # endpoint_key: (max_requests, window_seconds, friendly_label)
    "ai_questions": (12, 3600, "AI questions"),  # 12/hour — ~2 full sessions
    "check_bridge": (40, 3600, "translation checks"),
    "check_voice": (40, 3600, "voice checks"),
    "explain": (60, 3600, "answer explanations"),
    "quiz_scene": (20, 3600, "quiz scene questions"),
    "quiz_scene_check": (40, 3600, "quiz scene checks"),
    "free_production_grade": (40, 3600, "free production checks"),
    "own_the_word": (12, 3600, "Own the Word turns"),  # 4 per session × 3 sessions
    # Mastered-word bridge: generation cached 24h per (word,level,interest) so
    # a normal session never hits this. The check endpoint scales with words
    # the learner submits — 30/hour is plenty.
    # Bumped 20→100 — during prompt iteration the cache gets cleared often
    # and 20/hour was hitting users' own throttle inside a single dev session.
    # 100/hour is still cost-safe (Flash at $0.0001/call = $0.01/hour cap).
    "word_bridge_generate": (100, 3600, "Mastered-word prompts"),
    "word_bridge_check": (60, 3600, "Mastered-word checks"),
}


def _rate_limited(user_id: int, endpoint_key: str):
    """Return (is_limited: bool, remaining: int, retry_after: int).

    Uses Django's cache as a simple fixed-window counter. If the cache backend
    is LocMem (dev default) this is per-process — still a good local safety net.
    For production, pair with a Redis cache backend.
    """
    from django.core.cache import cache

    limit, window, _label = _AI_RATE_LIMITS.get(endpoint_key, (None, None, ""))
    if limit is None:
        return False, 0, 0

    key = f"rl:{endpoint_key}:{user_id}"
    try:
        current = cache.get(key, 0)
        if current >= limit:
            return True, 0, window
        # Atomic-ish increment. add() sets the key only if absent (with TTL),
        # then incr() bumps without resetting the TTL.
        if cache.add(key, 1, window):
            return False, limit - 1, window
        new_count = cache.incr(key, 1)
        return False, max(0, limit - new_count), window
    except Exception:
        # Cache errors should never block the request — fail-open.
        return False, 0, 0


def _rate_limit_response(endpoint_key: str, retry_after: int):
    """Standardized 429 response payload for the AI endpoints."""
    _limit, _window, label = _AI_RATE_LIMITS.get(endpoint_key, (0, 0, "this endpoint"))
    resp = error(
        f"Too many {label} — try again in a little while.",
        429,
    )
    resp["Retry-After"] = str(max(1, int(retry_after)))
    return resp
