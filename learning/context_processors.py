"""Template context processors for the learning app."""

from learning.utils.risk import count_words_at_risk


def addiction_layer(request):
    """Expose streak/goal/at-risk/CEFR to every template — drives retention UI widgets."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    # Lazy imports to avoid circular on app startup
    from datetime import date as date_mod

    from learning.models import DailyActivity
    from learning.utils.activity import cefr_progress, compute_streak_from_activity

    today = date_mod.today()
    activity = DailyActivity.objects.filter(user=user, date=today).first()
    today_xp = activity.xp_earned if activity else 0
    words_mastered_today = activity.words_mastered if activity else 0

    # Always recompute streak from real DailyActivity — the stored value
    # can drift if older bump paths missed an activity type. Single source
    # of truth across the entire app.
    streak_days = compute_streak_from_activity(user, today)
    # Persist write-back so other reads (admin, API, etc.) see the same value.
    if (user.streak_days or 0) != streak_days:
        try:
            type(user).objects.filter(pk=user.pk).update(streak_days=streak_days)
            user.streak_days = streak_days
        except Exception:
            pass

    goal = user.daily_xp_goal
    cefr = cefr_progress(user.total_xp or 0)
    return {
        "al_streak_days": streak_days,
        "al_longest_streak": user.longest_streak or 0,
        "al_today_xp": today_xp,
        "al_daily_goal_xp": goal,
        "al_goal_pct": min(100, round(today_xp / goal * 100)) if goal else 0,
        "al_goal_met": today_xp >= goal if goal else False,
        "al_words_at_risk": count_words_at_risk(user),
        "al_words_mastered_today": words_mastered_today,
        "al_total_xp": user.total_xp or 0,
        "al_cefr_current": cefr["current"],
        "al_cefr_next": cefr["next"],
        "al_cefr_pct": cefr["pct"],
        "al_cefr_xp_into": cefr["xp_into_level"],
        "al_cefr_xp_needed": cefr["xp_needed_for_next"],
        "al_rating": user.rating or 1000,
        "al_peak_rating": user.peak_rating or 1000,
    }
