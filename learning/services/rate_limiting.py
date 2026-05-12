"""Shared AI endpoint rate limiting.

Fixed-window counter backed by Django's cache. Works with LocMem in dev;
pair with Redis in production for cross-worker enforcement.
"""

from __future__ import annotations

from learning.utils.responses import error

# (max_requests, window_seconds, friendly_label)
_LIMITS: dict[str, tuple[int, int, str]] = {
    "ai_questions": (12, 3600, "AI questions"),
    "check_bridge": (40, 3600, "translation checks"),
    "check_voice": (40, 3600, "voice checks"),
    "explain": (60, 3600, "answer explanations"),
    "quiz_scene": (20, 3600, "quiz scene questions"),
    "quiz_scene_check": (40, 3600, "quiz scene checks"),
    "free_production_grade": (40, 3600, "free production checks"),
    "own_the_word": (12, 3600, "Own the Word turns"),
    "word_bridge_generate": (100, 3600, "Mastered-word prompts"),
    "word_bridge_check": (60, 3600, "Mastered-word checks"),
}


def is_rate_limited(user_id: int, endpoint_key: str) -> tuple[bool, int, int]:
    """Return (limited, remaining, retry_after_seconds)."""
    from django.core.cache import cache

    entry = _LIMITS.get(endpoint_key)
    if entry is None:
        return False, 0, 0

    limit, window, _ = entry
    key = f"rl:{endpoint_key}:{user_id}"
    try:
        current = cache.get(key, 0)
        if current >= limit:
            return True, 0, window
        if cache.add(key, 1, window):
            return False, limit - 1, window
        new_count = cache.incr(key, 1)
        return False, max(0, limit - new_count), window
    except Exception:
        return False, 0, 0  # fail-open: cache errors never block requests


def rate_limit_response(endpoint_key: str, retry_after: int):
    """Standardised 429 JsonResponse for AI endpoints."""
    _, _, label = _LIMITS.get(endpoint_key, (0, 0, "this endpoint"))
    resp = error(f"Too many {label} — try again in a little while.", 429)
    resp["Retry-After"] = str(max(1, int(retry_after)))
    return resp
