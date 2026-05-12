"""Activity tracking + streak/XP accumulation.

Called from the quiz submit flow. Every answer contributes XP; every day
of activity advances the streak (continues previous if consecutive, resets
otherwise). Words that level up are counted for the end-of-session celebration.
"""

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from learning.models import DailyActivity

# Fields that count as "real learning" — incrementing any of these
# qualifies the day for the streak. page_visits / session_count alone
# do NOT count (you can navigate without studying).
STREAK_QUALIFYING_FIELDS = frozenset(
    {
        "words_saved",
        "words_reviewed",
        "quiz_attempts",
        "build_attempts",
        "shadow_sessions",
        "grammar_attempts",
        "flashcard_reviews",
        "lookups",
        "watch_minutes",
        "shadow_minutes",
        "total_minutes",
    }
)


def compute_streak_from_activity(user, today=None):
    """Recompute the user's current streak from real DailyActivity rows.

    This is the SINGLE source of truth for the streak number. The stored
    `user.streak_days` field can drift (older code paths failed to bump
    on every activity), so we recompute from raw data instead. Cheap
    (one query) — safe to call on every page load.

    Logic:
      • Find every day with at least one qualifying learning action
        (saves, reviews, quiz, build, shadow, grammar, flashcards, lookups).
      • Walk back from today (with a 2-day grace window — taking one
        weekend off shouldn't break a 30-day run) to find the most recent
        active day.
      • Count consecutive active days backward from there.
      • Return 0 if no activity in the last 3 days (truly broken).

    Returns: int (streak length, 0 if broken).
    """
    from datetime import date as _date

    from learning.models import DailyActivity

    if today is None:
        today = timezone.localdate()

    active_dates = set()
    for row in DailyActivity.objects.filter(user=user).values(
        "date",
        "words_saved",
        "words_reviewed",
        "quiz_attempts",
        "build_attempts",
        "shadow_sessions",
        "grammar_attempts",
        "flashcard_reviews",
        "lookups",
    ):
        if (
            (row["words_saved"] or 0)
            + (row["words_reviewed"] or 0)
            + (row["quiz_attempts"] or 0)
            + (row["build_attempts"] or 0)
            + (row["shadow_sessions"] or 0)
            + (row["grammar_attempts"] or 0)
            + (row["flashcard_reviews"] or 0)
            + (row["lookups"] or 0)
        ) > 0:
            active_dates.add(row["date"])

    end = None
    for offset in range(0, 3):
        candidate = today - timedelta(days=offset)
        if candidate in active_dates:
            end = candidate
            break

    if end is None:
        return 0
    streak = 0
    cursor = end
    while cursor in active_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def bump_streak_for_today(user):
    """Update user.streak_days based on today's activity.

    Idempotent: safe to call many times per day — only mutates state
    when last_active_date != today. Atomic: uses select_for_update to
    prevent double-increment at day boundaries when concurrent requests
    land simultaneously.

    Returns the (possibly unchanged) current streak_days.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    with transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        if u.last_active_date == today:
            # Already counted today — nothing to do.
            user.streak_days = u.streak_days
            return u.streak_days

        if u.last_active_date == yesterday:
            u.streak_days = (u.streak_days or 0) + 1
        else:
            # Either no prior activity, or a gap of >=1 day → reset.
            u.streak_days = 1
        u.last_active_date = today
        if u.streak_days > (u.longest_streak or 0):
            u.longest_streak = u.streak_days
        u.save(update_fields=["streak_days", "longest_streak", "last_active_date"])

        # Keep the in-memory object the caller holds in sync.
        user.streak_days = u.streak_days
        user.longest_streak = u.longest_streak
        user.last_active_date = u.last_active_date
        return u.streak_days


# Per-quiz-type wrong-answer penalty. Smaller than the correct-answer reward
# so net XP still grows on a ~70% accuracy session, but wrong answers DO cost.
_WRONG_PENALTY_BY_TYPE = {
    # Recognition (easy types) — small penalty, you "should've known"
    "flash": 1,
    "smart_mcq": 1,
    "usage_check": 1,
    "match": 1,
    "listen": 1,
    # Pattern recall — moderate
    "pattern_notice": 2,
    "sentence_cloze": 2,
    "cloze": 2,
    "define": 2,
    # Production — no penalty on wrong (trying to produce is the whole point)
    "produce": 2,
    "quote_dash": 2,
    "translate_back": 1,
}


def _compute_xp(
    correct: bool, quiz_type: str, combo: int, elapsed_ms: int, bonus_multiplier: float = 1.0, skipped: bool = False
) -> int:
    """Points awarded (or deducted) for a single answer.
    Returns positive on correct, negative on wrong, 0 on honest skip.
    """
    if skipped:
        return 0  # "I don't know" → no XP loss; rating still drops small via compute_rating_delta
    if not correct:
        return -_WRONG_PENALTY_BY_TYPE.get(quiz_type, 2)
    BASE = 10
    TYPE_BONUS = {
        "flash": 0,
        "smart_mcq": 3,
        "usage_check": 5,
        "pattern_notice": 5,
        "sentence_cloze": 6,
        "translate_back": 8,
        "quote_dash": 8,
        "define": 3,
        "cloze": 4,
        "produce": 6,
        "listen": 2,
        "match": 2,
        "build": 5,
        "speed": 2,
    }
    COMBO_STEP = 2
    TIME_BONUS = 3
    pts = BASE + TYPE_BONUS.get(quiz_type, 0) + min(max(combo - 1, 0), 5) * COMBO_STEP
    if elapsed_ms and elapsed_ms < 5000:
        pts += TIME_BONUS
    return round(pts * max(1.0, float(bonus_multiplier or 1.0)))


# CEFR level thresholds — cumulative lifetime XP required to reach each level.
# Chosen so a committed learner (100-150 XP/day) takes ~2 weeks per lower level,
# ~6 weeks per upper level, matching real CEFR learning curves.
CEFR_THRESHOLDS = [
    ("A1", 0),
    ("A2", 800),
    ("B1", 2500),
    ("B2", 6000),
    ("C1", 13000),
    ("C2", 25000),
]


def cefr_from_xp(total_xp: int) -> str:
    """Return the CEFR label matching a given lifetime XP."""
    level = "A1"
    for name, threshold in CEFR_THRESHOLDS:
        if total_xp >= threshold:
            level = name
        else:
            break
    return level


def cefr_progress(total_xp: int) -> dict:
    """Return progress within the current CEFR level."""
    current = "A1"
    current_start = 0
    next_name = None
    next_threshold = None
    for i, (name, threshold) in enumerate(CEFR_THRESHOLDS):
        if total_xp >= threshold:
            current = name
            current_start = threshold
            if i + 1 < len(CEFR_THRESHOLDS):
                next_name, next_threshold = CEFR_THRESHOLDS[i + 1]
            else:
                next_name, next_threshold = None, None
        else:
            break
    if next_threshold is None:
        return {
            "current": current,
            "next": None,
            "pct": 100,
            "xp_into_level": total_xp - current_start,
            "xp_needed_for_next": 0,
        }
    span = next_threshold - current_start
    into = total_xp - current_start
    return {
        "current": current,
        "next": next_name,
        "pct": min(100, round(into / span * 100)) if span else 100,
        "xp_into_level": into,
        "xp_needed_for_next": next_threshold - total_xp,
    }


def compute_rating_delta(correct: bool, p_expected: float, quiz_type: str, used_hint: bool = False) -> int:
    """Elo-ish rating change for one answer — tiered by cognitive effort.

    Design (per senior-gaming-design review):
      • MCQ / recognition  : ±1-2   (5-10s effort, high guess rate)
      • Pattern recall     : ±2-4   (type a word)
      • Production         : ±3-7   (type a sentence, hardest)

    Size scales with how UNEXPECTED the outcome was:
      • Hard correct (low p_known) → top of range
      • Easy correct (high p_known) → bottom of range (floor +1)
      • Easy wrong  → top of wrong range (you should've known this)
      • Hard wrong  → floor -1 (fair miss)

    Floor at +1 on correct so every success feels like progress.
    """
    p = max(0.05, min(0.95, float(p_expected or 0.15)))

    # (K_correct, K_wrong) per quiz type — calibrated for small, meaningful numbers.
    # Max typical session swing ≈ ±20-30. No single question dominates.
    K = {
        # Tier 1 — Recognition (MCQ, coin-flip floor)
        "flash": (2, 1),
        "smart_mcq": (2, 1),
        "usage_check": (2, 1),
        "match": (2, 1),
        "listen": (2, 1),
        # Tier 2 — Pattern recall (type a word in context)
        "pattern_notice": (3, 2),
        "sentence_cloze": (4, 2),
        "cloze": (3, 2),
        "define": (3, 2),
        # Tier 3 — Production (free-form typing, hardest)
        "produce": (5, 3),
        "quote_dash": (6, 3),
        "translate_back": (7, 3),
    }
    k_c, k_w = K.get(quiz_type, (3, 2))

    if correct:
        raw = k_c * (1 - p)
        if used_hint:
            raw *= 0.5
        return max(1, round(raw))  # always at least +1 on a correct answer
    else:
        raw = -k_w * p
        return min(-1, round(raw))  # always at least -1 on a wrong answer


def record_quiz_activity(
    user,
    *,
    correct: bool,
    quiz_type: str,
    combo: int,
    elapsed_ms: int,
    leveled_up: bool,
    bonus_multiplier: float = 1.0,
    p_expected: float = 0.15,
    used_hint: bool = False,
    skipped: bool = False,
) -> dict:
    """Update today's DailyActivity + user streak.
    Returns the current streak/XP state so the API can surface it to the client.
    """
    from django.contrib.auth import get_user_model
    from django.db import transaction

    User = get_user_model()

    today = timezone.localdate()
    xp = _compute_xp(correct, quiz_type, combo, elapsed_ms, bonus_multiplier=bonus_multiplier, skipped=skipped)
    cefr_level_before = None
    rating_delta = compute_rating_delta(correct, p_expected, quiz_type, used_hint)

    # Streak is bumped via the shared helper below (single source of truth).
    # The atomic block here only handles XP + rating writes.
    bump_streak_for_today(user)

    with transaction.atomic():
        u = User.objects.select_for_update().get(pk=user.pk)
        dirty_fields = []

        # Lifetime XP + CEFR progression
        # Correct answers add positive xp; wrong answers deduct a small penalty.
        # CEFR level-up detection only fires on positive growth.
        if xp != 0:
            old_total = u.total_xp or 0
            new_total = max(0, old_total + xp)  # floor at 0, never negative
            cefr_level_before = cefr_from_xp(old_total)
            u.total_xp = new_total
            new_cefr = cefr_from_xp(new_total)
            u.proficiency_level = new_cefr.lower() if new_cefr.startswith(("A", "B", "C")) else u.proficiency_level
            # Only announce a level-up on forward crossings (going up a tier)
            if xp <= 0 or new_cefr == cefr_level_before:
                cefr_level_before = None
            dirty_fields += ["total_xp", "proficiency_level"]

        # Rating (chess.com-style, every answer)
        old_rating = u.rating or 1000
        new_rating = max(100, old_rating + rating_delta)
        if new_rating != old_rating:
            u.rating = new_rating
            if new_rating > (u.peak_rating or 1000):
                u.peak_rating = new_rating
                dirty_fields.append("peak_rating")
            dirty_fields.append("rating")

        if dirty_fields:
            u.save(update_fields=dirty_fields)

        # Sync the in-memory user object the caller holds
        user.streak_days = u.streak_days
        user.longest_streak = u.longest_streak
        user.last_active_date = u.last_active_date
        user.total_xp = u.total_xp
        user.rating = u.rating
        user.peak_rating = u.peak_rating
        user.proficiency_level = u.proficiency_level

        # DailyActivity row (atomic F() increments — safe under concurrency)
        activity, _ = DailyActivity.objects.get_or_create(user=u, date=today)
        # xp_earned is a PositiveIntegerField, so only add NON-negative XP to it.
        # Wrong answers still count as attempts; the penalty hits total_xp separately.
        DailyActivity.objects.filter(pk=activity.pk).update(
            xp_earned=F("xp_earned") + max(0, xp),
            quiz_attempts=F("quiz_attempts") + 1,
            quiz_correct=F("quiz_correct") + (1 if correct else 0),
            words_mastered=F("words_mastered") + (1 if leveled_up else 0),
        )
        activity.refresh_from_db()

    goal = user.daily_xp_goal
    prog = cefr_progress(user.total_xp or 0)
    return {
        "xp_earned": xp,
        "today_xp": activity.xp_earned,
        "daily_goal_xp": goal,
        "goal_pct": min(100, round(activity.xp_earned / goal * 100)) if goal else 0,
        "goal_met_today": activity.xp_earned >= goal,
        "streak_days": user.streak_days,
        "longest_streak": user.longest_streak,
        "words_mastered_today": activity.words_mastered,
        "total_xp": user.total_xp or 0,
        "cefr_level": prog["current"],
        "cefr_next": prog["next"],
        "cefr_pct": prog["pct"],
        "cefr_level_up": cefr_level_before is not None,  # True if user just crossed a CEFR threshold
        # Chess.com-style rating
        "rating": new_rating,
        "rating_delta": rating_delta,
        "peak_rating": user.peak_rating,
    }
