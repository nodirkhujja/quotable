"""
Coach Engine — Data-driven learning advisor.

Combines:
  1. Math/Data Science  — BKT decay, Elo gaps, forgetting curves, trend analysis
  2. Psychology          — motivation, fatigue, streak protection, progress framing
  3. Expert Teaching     — skill sequencing, balanced practice, weakness targeting

Output: a single `coach_context` dict ready for template rendering.
"""

import math
from datetime import date, timedelta

from django.db import models
from django.utils import timezone

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

# Ebbinghaus half-life base (hours). Grows with streak.
HALF_LIFE_BASE = 24.0
HALF_LIFE_GROWTH = 0.5  # half_life = BASE * 2^(streak * GROWTH)

# Priority action weights
W_FORGETTING = 40  # words about to be forgotten
W_REVIEW_DUE = 35  # spaced repetition overdue
W_WEAKNESS = 30  # targeted weakness drilling
W_NEW_INPUT = 25  # watching / saving new words
W_GRAMMAR = 20  # grammar gaps
W_SHADOWING = 15  # pronunciation practice
W_CONFUSION = 25  # confusion pair resolution

# Health score weights
HEALTH_W_VOCAB = 0.30
HEALTH_W_ACTIVITY = 0.20
HEALTH_W_RETENTION = 0.20
HEALTH_W_GRAMMAR = 0.15
HEALTH_W_CONSISTENCY = 0.15

# Psychology: engagement thresholds
BURNOUT_MINUTES = 45  # above this in a day → suggest break
SESSION_SWEET_SPOT = 15  # optimal session length
STREAK_DANGER_ZONE = 1  # at risk if only 1 day left in streak


def build_coach_context(user):
    """Build the full coaching context dict for a user."""
    from clips.models import WatchHistory
    from learning.models import (
        GRAMMAR_PATTERNS,
        BuildAttempt,
        ConfusionPair,
        DailyActivity,
        FlashcardAttempt,
        GrammarPracticeLog,
        LookupLog,
        OnboardingSession,
        PageVisit,
        PatternMastery,
        ShadowingLog,
        UserLearningProfile,
        VocabMastery,
        WordNote,
    )
    from quiz.models import QuizAttempt

    now = timezone.now()
    today = date.today()
    hour = now.hour

    # ═══════════════════════════════════════════
    # 1. RAW DATA COLLECTION
    # ═══════════════════════════════════════════

    # -- Word Notes pipeline --
    user_words = WordNote.objects.filter(user=user)
    total_words = user_words.count()
    stage_counts = dict(user_words.values_list("stage").annotate(c=models.Count("id")).values_list("stage", "c"))
    wn_inbox = stage_counts.get("inbox", 0)
    wn_learning = stage_counts.get("learning", 0)
    wn_mastered = stage_counts.get("mastered", 0)

    # -- Vocab Mastery (BKT) --
    vm_qs = VocabMastery.objects.filter(user=user)
    vm_level_counts = dict(vm_qs.values_list("level").annotate(c=models.Count("id")).values_list("level", "c"))
    vm_weak = vm_level_counts.get("weak", 0)
    vm_shaky = vm_level_counts.get("shaky", 0)
    vm_good = vm_level_counts.get("good", 0)
    vm_strong = vm_level_counts.get("strong", 0)
    vm_mastered = vm_level_counts.get("mastered", 0)
    vm_total = sum(vm_level_counts.values())

    # Weakest words with decay prediction
    weakest_raw = list(vm_qs.filter(level__in=["weak", "shaky"]).select_related("vocab").order_by("p_known")[:8])
    weakest_words = []
    for vm in weakest_raw:
        decay = _predict_decay(vm)
        weakest_words.append(
            {
                "word": vm.vocab.english,
                "level": vm.level,
                "p": round(vm.p_known * 100),
                "decay": round(decay * 100),
                "hours_until_forget": _hours_until_forget(vm),
            }
        )

    # Words due for review (spaced repetition)
    due_count = vm_qs.filter(next_review__lte=now).count()

    # Words about to decay (within 6 hours)
    decaying_soon = _count_decaying_soon(vm_qs, hours=6)

    # -- Dimension analysis (translation vs context vs production) --
    dimension_gap = _analyze_skill_dimensions(vm_qs)

    # -- Today's activity --
    da, _ = DailyActivity.objects.get_or_create(user=user, date=today)
    today_data = {
        "words_saved": da.words_saved,
        "quiz_attempts": da.quiz_attempts,
        "quiz_correct": da.quiz_correct,
        "build_attempts": da.build_attempts,
        "build_correct": da.build_correct,
        "shadow_sessions": da.shadow_sessions,
        "shadow_loops": da.shadow_loops,
        "grammar_attempts": da.grammar_attempts,
        "grammar_correct": da.grammar_correct,
        "lookups": da.lookups,
        "flashcard_reviews": da.flashcard_reviews,
        "flashcard_known": da.flashcard_known,
        "watch_minutes": round(da.watch_minutes),
        "total_minutes": round(da.total_minutes),
    }

    daily_goal = user.daily_goal_minutes or 15
    goal_pct = min(100, round(da.total_minutes / daily_goal * 100))

    # -- Weekly activity --
    week_start = today - timedelta(days=6)
    week_activities = {r.date: r for r in DailyActivity.objects.filter(user=user, date__gte=week_start)}
    week_data = []
    active_days = 0
    total_week_minutes = 0
    day_labels = ["M", "T", "W", "T", "F", "S", "S"]
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        da_row = week_activities.get(d)
        count = 0
        mins = 0
        if da_row:
            count = (
                da_row.quiz_attempts
                + da_row.build_attempts
                + da_row.words_saved
                + da_row.shadow_sessions
                + da_row.grammar_attempts
            )
            mins = round(da_row.total_minutes)
            if mins >= 2:
                active_days += 1
            total_week_minutes += mins
        week_data.append(
            {
                "label": day_labels[d.weekday()],
                "count": count,
                "minutes": mins,
                "is_today": d == today,
            }
        )
    max_week = max((w["count"] for w in week_data), default=1) or 1

    # -- Grammar patterns --
    pattern_masteries = {pm.pattern: pm for pm in PatternMastery.objects.filter(user=user)}
    grammar_progress = []
    grammar_weakest = None
    grammar_weakest_p = 100
    for pid, pinfo in GRAMMAR_PATTERNS.items():
        pm = pattern_masteries.get(pid)
        p_known = round((pm.p_known if pm else 0.1) * 100)
        unlocked = pm.unlocked if pm else (pid == "because_so_but")
        attempts = pm.attempts if pm else 0
        grammar_progress.append(
            {
                "id": pid,
                "label": pinfo["label"],
                "level": pinfo["level"],
                "stars": pm.stars if pm else 0,
                "p_known": p_known,
                "unlocked": unlocked,
                "attempts": attempts,
            }
        )
        if unlocked and attempts > 0 and p_known < grammar_weakest_p:
            grammar_weakest_p = p_known
            grammar_weakest = pinfo["label"]

    grammar_total = len(grammar_progress)
    grammar_unlocked = sum(1 for g in grammar_progress if g["unlocked"])
    grammar_3star = sum(1 for g in grammar_progress if g["stars"] >= 3)

    # -- Accuracy trends (7-day vs previous 7-day) --
    recent_qa = QuizAttempt.objects.filter(user=user, created_at__date__gte=today - timedelta(days=7))
    recent_total = recent_qa.count()
    recent_correct = recent_qa.filter(correct=True).count()
    recent_accuracy = round(recent_correct / recent_total * 100) if recent_total else 0

    prev_qa = QuizAttempt.objects.filter(
        user=user,
        created_at__date__gte=today - timedelta(days=14),
        created_at__date__lt=today - timedelta(days=7),
    )
    prev_total = prev_qa.count()
    prev_correct = prev_qa.filter(correct=True).count()
    prev_accuracy = round(prev_correct / prev_total * 100) if prev_total else 0
    accuracy_trend = recent_accuracy - prev_accuracy

    # -- Confusion pairs (filter out long phrases — only real word confusions) --
    confusions_raw = ConfusionPair.objects.filter(user=user, resolved=False).order_by("-confusion_count")[:12]
    confusion_display = []
    for cp in confusions_raw:
        if len(cp.word_a.split()) <= 3 and len(cp.word_b.split()) <= 3 and cp.confusion_count >= 2:
            confusion_display.append({"a": cp.word_a, "b": cp.word_b, "count": cp.confusion_count})
        if len(confusion_display) >= 4:
            break

    # -- Learning profile --
    try:
        profile = UserLearningProfile.objects.get(user=user)
        elo = round(profile.ability_rating)
        learning_rate = round(profile.learning_rate, 1)
        retention_rate = round(profile.retention_rate * 100) if profile.retention_rate else 0
        overall_accuracy = profile.accuracy
        weaknesses = []
        for field, label in [
            ("weakness_phrasal_verb", "Phrasal verbs"),
            ("weakness_idiom", "Idioms"),
            ("weakness_single_word", "Vocabulary"),
            ("weakness_abstract", "Abstract words"),
            ("weakness_phrase", "Phrases"),
        ]:
            score = getattr(profile, field, 0)
            if score < -0.15:
                weaknesses.append({"label": label, "score": round(score, 2)})
        weaknesses.sort(key=lambda w: w["score"])
    except UserLearningProfile.DoesNotExist:
        elo = 1000
        learning_rate = 0
        retention_rate = 0
        overall_accuracy = 0
        weaknesses = []

    # -- Watch resume --
    last_watched = (
        WatchHistory.objects.filter(user=user, position_sec__gte=60)
        .select_related("source", "episode")
        .order_by("-updated_at")
        .first()
    )
    resume_info = None
    if last_watched and not last_watched.is_complete:
        mins = int(last_watched.position_sec // 60)
        secs = int(last_watched.position_sec % 60)
        ep_label = ""
        if last_watched.episode:
            ep = last_watched.episode
            ep_label = f"S{ep.season}E{ep.episode_number}"
        resume_info = {
            "source_title": last_watched.source.title,
            "ep_label": ep_label,
            "time_label": f"{mins}:{secs:02d}",
            "progress_pct": round(last_watched.progress_pct * 100),
            "url": f"/watch/{last_watched.source.id}/",
        }

    # -- Shadowing stats (last 7 days) --
    shadow_7d = ShadowingLog.objects.filter(user=user, created_at__date__gte=today - timedelta(days=7))
    shadow_total = shadow_7d.count()
    shadow_completed = shadow_7d.filter(phases_completed__gte=4).count()
    shadow_completion_rate = round(shadow_completed / shadow_total * 100) if shadow_total else 0

    # -- Flashcard stats (today) --
    fc_today = FlashcardAttempt.objects.filter(user=user, batch_date=today)
    fc_done_today = fc_today.exists()

    # -- Lookup frequency (last 7 days) --
    lookups_7d = LookupLog.objects.filter(user=user, created_at__date__gte=today - timedelta(days=7)).count()
    lookup_save_rate = _calc_lookup_save_rate(user, days=7)

    # -- Onboarding level --
    onboarding_level = ""
    ob = (
        OnboardingSession.objects.filter(user=user, completed_at__isnull=False).values_list("level", flat=True).first()
    )
    if ob:
        onboarding_level = ob

    # ═══════════════════════════════════════════
    # 2. MATHEMATICAL ANALYSIS
    # ═══════════════════════════════════════════

    # -- Health score (0-100) --
    health = _compute_health(
        vm_weak,
        vm_shaky,
        vm_good,
        vm_strong,
        vm_mastered,
        vm_total,
        goal_pct,
        retention_rate,
        grammar_3star,
        grammar_total,
        active_days,
    )

    # -- Engagement pattern --
    engagement = _analyze_engagement(today_data, week_data, active_days, total_week_minutes)

    # -- Learning phase detection --
    phase = _detect_learning_phase(
        total_words,
        vm_total,
        wn_mastered,
        recent_total,
        active_days,
        onboarding_level,
    )

    # ═══════════════════════════════════════════
    # 3. PSYCHOLOGY-INFORMED COACHING
    # ═══════════════════════════════════════════

    # -- Greeting with emotional awareness --
    greeting = _build_greeting(hour, user, today_data, engagement, phase)

    # -- Priority actions (weighted, psychology-aware) --
    actions = _build_priority_actions(
        total_words=total_words,
        due_count=due_count,
        decaying_soon=decaying_soon,
        vm_weak=vm_weak,
        vm_shaky=vm_shaky,
        weakest_words=weakest_words,
        today_data=today_data,
        resume_info=resume_info,
        grammar_weakest=grammar_weakest,
        grammar_weakest_p=grammar_weakest_p,
        confusions=confusion_display,
        dimension_gap=dimension_gap,
        engagement=engagement,
        phase=phase,
        fc_done_today=fc_done_today,
        words_with_video=user_words.exclude(quote=None, transcript=None).count(),
        wn_inbox=wn_inbox,
        daily_goal=daily_goal,
    )

    # Pick top 3 actions
    actions.sort(key=lambda a: a["score"], reverse=True)
    primary_action = actions[0] if actions else None
    next_actions = actions[1:3]

    # -- Coach insight (positive, student-friendly) --
    insight = _generate_insight(
        health,
        engagement,
        phase,
        accuracy_trend,
        retention_rate,
        weakest_words,
        dimension_gap,
        decaying_soon,
        confusion_display,
        vm_mastered,
        total_words,
        today_data,
    )

    # -- Motivational framing --
    motivation = _frame_motivation(
        user,
        today_data,
        goal_pct,
        engagement,
        phase,
        vm_mastered,
        total_words,
    )

    # ═══════════════════════════════════════════
    # 4. COMPILE CONTEXT
    # ═══════════════════════════════════════════

    return {
        # Header
        "greeting": greeting,
        "streak": user.streak_days,
        "longest_streak": user.longest_streak,
        "phase": phase,
        "insight": insight,
        "motivation": motivation,
        # Primary coaching action
        "primary_action": primary_action,
        "next_actions": next_actions,
        # Today's progress
        "today": today_data,
        "daily_goal": daily_goal,
        "goal_pct": goal_pct,
        "fc_done_today": fc_done_today,
        # Health & scores
        "health": health,
        "engagement": engagement,
        # Vocabulary
        "total_words": total_words,
        "wn_inbox": wn_inbox,
        "wn_learning": wn_learning,
        "wn_mastered": wn_mastered,
        "vm_weak": vm_weak,
        "vm_shaky": vm_shaky,
        "vm_good": vm_good,
        "vm_strong": vm_strong,
        "vm_mastered": vm_mastered,
        "vm_total": vm_total,
        "vm_weak_pct": round(vm_weak / vm_total * 100, 1) if vm_total else 0,
        "vm_shaky_pct": round(vm_shaky / vm_total * 100, 1) if vm_total else 0,
        "vm_good_pct": round(vm_good / vm_total * 100, 1) if vm_total else 0,
        "vm_strong_pct": round(vm_strong / vm_total * 100, 1) if vm_total else 0,
        "vm_mastered_pct": round(vm_mastered / vm_total * 100, 1) if vm_total else 0,
        "due_count": due_count,
        "decaying_soon": decaying_soon,
        "weakest_words": weakest_words,
        "dimension_gap": dimension_gap,
        # Grammar
        "grammar_progress": grammar_progress,
        "grammar_unlocked": grammar_unlocked,
        "grammar_3star": grammar_3star,
        "grammar_total": grammar_total,
        # Performance
        "recent_accuracy": recent_accuracy,
        "accuracy_trend": accuracy_trend,
        "overall_accuracy": overall_accuracy,
        "elo": elo,
        "learning_rate": learning_rate,
        "retention_rate": retention_rate,
        "weaknesses": weaknesses,
        # Confusions
        "confusions": confusion_display,
        # Watch
        "resume_info": resume_info,
        # Shadowing
        "shadow_completion_rate": shadow_completion_rate,
        # Week
        "week_data": week_data,
        "max_week": max_week,
        "active_days": active_days,
    }


# ─────────────────────────────────────────────
# FORGETTING CURVE MATH
# ─────────────────────────────────────────────


def _predict_decay(vm):
    """Predict current retention using Ebbinghaus decay model.

    retention(t) = 2^(-hours / half_life)
    half_life grows with streak: 24h * 2^(streak * 0.5)
    """
    if not vm.last_reviewed:
        return 0.5  # unknown → assume 50%

    hours_since = (timezone.now() - vm.last_reviewed).total_seconds() / 3600
    half_life = HALF_LIFE_BASE * (2 ** (vm.streak * HALF_LIFE_GROWTH))
    retention = 2 ** (-hours_since / half_life)
    return max(0, min(1, retention))


def _hours_until_forget(vm, threshold=0.5):
    """Hours until this word drops below threshold retention."""
    if not vm.last_reviewed:
        return 0

    half_life = HALF_LIFE_BASE * (2 ** (vm.streak * HALF_LIFE_GROWTH))
    # Solve: threshold = 2^(-t/half_life)  →  t = -half_life * log2(threshold)
    t_total = -half_life * math.log2(max(threshold, 0.01))

    hours_since = (timezone.now() - vm.last_reviewed).total_seconds() / 3600
    remaining = t_total - hours_since
    return max(0, round(remaining, 1))


def _count_decaying_soon(vm_qs, hours=6):
    """Count words that will drop below 50% retention within `hours`."""
    count = 0
    cutoff = timezone.now() - timedelta(hours=48)  # only check recently reviewed
    for vm in vm_qs.filter(last_reviewed__gte=cutoff, level__in=["good", "shaky"]):
        if _hours_until_forget(vm) <= hours:
            count += 1
    return count


# ─────────────────────────────────────────────
# SKILL DIMENSION ANALYSIS
# ─────────────────────────────────────────────


def _analyze_skill_dimensions(vm_qs):
    """Find gap between recognition (translation) vs production scores.

    Returns dict with weakest dimension and gap size.
    """
    agg = vm_qs.aggregate(
        avg_trans=models.Avg("translation_score"),
        avg_ctx=models.Avg("context_score"),
        avg_prod=models.Avg("production_score"),
    )
    trans = (agg["avg_trans"] or 0.5) * 100
    ctx = (agg["avg_ctx"] or 0.5) * 100
    prod = (agg["avg_prod"] or 0.5) * 100

    scores = {"recognition": trans, "context": ctx, "production": prod}
    weakest = min(scores, key=scores.get)
    strongest = max(scores, key=scores.get)
    gap = scores[strongest] - scores[weakest]

    quiz_type_map = {
        "recognition": "flash",
        "context": "cloze",
        "production": "produce",
    }

    return {
        "weakest": weakest,
        "strongest": strongest,
        "gap": round(gap),
        "scores": {k: round(v) for k, v in scores.items()},
        "recommended_quiz": quiz_type_map.get(weakest, "flash"),
    }


# ─────────────────────────────────────────────
# HEALTH SCORE
# ─────────────────────────────────────────────


def _compute_health(
    vm_weak,
    vm_shaky,
    vm_good,
    vm_strong,
    vm_mastered,
    vm_total,
    goal_pct,
    retention_rate,
    grammar_3star,
    grammar_total,
    active_days,
):
    """Compute 0-100 health score.

    Vocab health:  (good+strong+mastered) / total
    Activity:      daily goal completion
    Retention:     7-day retention rate
    Grammar:       patterns mastered / total
    Consistency:   active_days / 7
    """
    if vm_total > 0:
        vocab_h = ((vm_good + vm_strong + vm_mastered) / vm_total) * 100
    else:
        vocab_h = 50  # neutral for new users

    activity_h = min(100, goal_pct * 1.2)
    retention_h = retention_rate  # already 0-100
    grammar_h = (grammar_3star / grammar_total * 100) if grammar_total else 50
    consistency_h = (active_days / 7) * 100

    score = round(
        vocab_h * HEALTH_W_VOCAB
        + activity_h * HEALTH_W_ACTIVITY
        + retention_h * HEALTH_W_RETENTION
        + grammar_h * HEALTH_W_GRAMMAR
        + consistency_h * HEALTH_W_CONSISTENCY
    )
    return max(0, min(100, score))


# ─────────────────────────────────────────────
# ENGAGEMENT ANALYSIS
# ─────────────────────────────────────────────


def _analyze_engagement(today_data, week_data, active_days, total_week_minutes):
    """Detect engagement patterns for psychology-aware coaching."""
    today_mins = today_data["total_minutes"]

    # Momentum: are they building up or winding down today?
    if today_mins >= BURNOUT_MINUTES:
        momentum = "burnout_risk"
    elif today_mins >= SESSION_SWEET_SPOT:
        momentum = "in_flow"
    elif today_mins >= 5:
        momentum = "warming_up"
    else:
        momentum = "fresh"

    # Weekly pattern
    if active_days >= 6:
        pattern = "daily_habit"
    elif active_days >= 4:
        pattern = "consistent"
    elif active_days >= 2:
        pattern = "sporadic"
    else:
        pattern = "rare"

    # Activity balance (what % of effort goes where)
    total_actions = (
        today_data["quiz_attempts"]
        + today_data["build_attempts"]
        + today_data["shadow_sessions"]
        + today_data["grammar_attempts"]
        + today_data["flashcard_reviews"]
    )
    balance = {}
    if total_actions > 0:
        balance = {
            "quiz": round(today_data["quiz_attempts"] / total_actions * 100),
            "build": round(today_data["build_attempts"] / total_actions * 100),
            "shadow": round(today_data["shadow_sessions"] / total_actions * 100),
            "grammar": round(today_data["grammar_attempts"] / total_actions * 100),
        }

    # Average daily minutes this week
    avg_daily = round(total_week_minutes / max(active_days, 1))

    return {
        "momentum": momentum,
        "pattern": pattern,
        "active_days": active_days,
        "avg_daily_minutes": avg_daily,
        "balance": balance,
        "total_actions_today": total_actions,
    }


# ─────────────────────────────────────────────
# LEARNING PHASE DETECTION
# ─────────────────────────────────────────────


def _detect_learning_phase(
    total_words,
    vm_total,
    wn_mastered,
    recent_total,
    active_days,
    onboarding_level,
):
    """Detect where the student is in their learning journey.

    Phases:
      discovery  — brand new, < 10 words
      building   — actively saving words, 10-50 words
      practicing — has vocabulary, doing quizzes
      advancing  — consistent, mastering words
      fluent     — high mastery, maintaining
    """
    if total_words < 10:
        return {
            "id": "discovery",
            "label": "Discovery",
            "desc": "You're exploring — save words that catch your attention",
            "color": "#f59e0b",
        }
    elif total_words < 50 and vm_total < 20:
        return {
            "id": "building",
            "label": "Building",
            "desc": "You're building your word bank — keep watching and saving",
            "color": "#3b82f6",
        }
    elif wn_mastered < total_words * 0.3:
        return {
            "id": "practicing",
            "label": "Practicing",
            "desc": "Your vocabulary is growing — quiz yourself to lock it in",
            "color": "#8b5cf6",
        }
    elif active_days >= 4:
        return {
            "id": "advancing",
            "label": "Advancing",
            "desc": "You're making real progress — consistency is key",
            "color": "#10b981",
        }
    else:
        return {
            "id": "maintaining",
            "label": "Maintaining",
            "desc": "Strong foundation — focus on retention and new patterns",
            "color": "#14b8a6",
        }


# ─────────────────────────────────────────────
# GREETING (Psychology-aware)
# ─────────────────────────────────────────────


def _build_greeting(hour, user, today_data, engagement, phase):
    """Context-aware greeting — always lead with warmth, celebrate effort."""
    name = user.first_name or user.username

    if hour < 5:
        time_greeting = "Burning the midnight oil"
    elif hour < 12:
        time_greeting = "Good morning"
    elif hour < 17:
        time_greeting = "Good afternoon"
    elif hour < 21:
        time_greeting = "Good evening"
    else:
        time_greeting = "Late night study"

    # Celebrate what they've done today
    if engagement["momentum"] == "burnout_risk":
        return f"Impressive effort today, {name}! Time for a well-earned break."
    elif today_data["quiz_attempts"] >= 10:
        return f"{time_greeting}, {name}! {today_data['quiz_attempts']} questions answered today."
    elif today_data["total_minutes"] >= 5:
        return f"{time_greeting}, {name}! Nice progress today."
    else:
        return f"{time_greeting}, {name}"


# ─────────────────────────────────────────────
# PRIORITY ACTIONS
# ─────────────────────────────────────────────


def _build_priority_actions(**kw):
    """Build recommendations mapped to the real activities.

    Activities the student can actually do:
      1. My Words    — flashcards + shadowing  (/learning/words/)
      2. Quiz        — multiple choice recall   (/learning/quiz/)
      3. Grammar     — rules + practice         (/learning/grammar/)
      4. Watch       — continue a show          (/watch/...)

    Every recommendation must point to one of these.
    """
    actions = []
    td = kw["today_data"]

    # --- BURNOUT (overrides all) ---
    if kw["engagement"]["momentum"] == "burnout_risk":
        actions.append(
            {
                "score": 90,
                "type": "rest",
                "icon": "pause",
                "title": "Great session! Time for a break",
                "subtitle": "You've been studying hard — come back fresh tomorrow.",
                "btn_text": "Done for today",
                "btn_url": "/",
                "urgency": "low",
            }
        )
        return actions

    # --- START (no words yet) ---
    if kw["total_words"] == 0:
        actions.append(
            {
                "score": 100,
                "type": "watch",
                "icon": "play",
                "title": "Start watching a show",
                "subtitle": "Tap any word you don't know — that's how it begins!",
                "btn_text": "Browse shows",
                "btn_url": "/",
                "urgency": "high",
            }
        )
        return actions

    # ── SHADOWING (top priority — it's half the app) ──
    if td["shadow_sessions"] == 0 and kw["words_with_video"] >= 3:
        score = 45  # high base
        if td["quiz_attempts"] > 0:
            score += 10  # they've done quiz, now do shadowing
        actions.append(
            {
                "score": score,
                "type": "shadow",
                "icon": "mic",
                "title": "Shadowing practice",
                "subtitle": "Listen and repeat — the best way to improve pronunciation.",
                "btn_text": "Start shadowing",
                "btn_url": "/learning/shadowing/",
                "urgency": "medium",
            }
        )

    # ── QUIZ ──
    if td["quiz_attempts"] == 0 and kw["words_with_video"] >= 5:
        score = 40
        if kw["due_count"] >= 3:
            score += 15
        actions.append(
            {
                "score": score,
                "type": "quiz",
                "icon": "brain",
                "title": f"Quiz — {kw['due_count']} words to review" if kw["due_count"] >= 3 else "Take today's quiz",
                "subtitle": f"{kw['words_with_video']} words ready. Just 5 minutes.",
                "btn_text": "Start quiz",
                "btn_url": "/learning/quiz/",
                "urgency": "high" if kw["due_count"] >= 5 else "medium",
            }
        )
    elif kw["due_count"] >= 5 and td["quiz_attempts"] < 10:
        actions.append(
            {
                "score": 35 + min(kw["due_count"] * 2, 20),
                "type": "quiz",
                "icon": "brain",
                "title": f"{kw['due_count']} words due for review",
                "subtitle": "A quick quiz keeps them fresh.",
                "btn_text": "Review quiz",
                "btn_url": "/learning/quiz/",
                "urgency": "high",
            }
        )

    # ── MY WORDS (flashcards) ──
    if not kw["fc_done_today"] and kw["total_words"] >= 5:
        score = 30
        if kw["wn_inbox"] >= 5:
            score += 10
        actions.append(
            {
                "score": score,
                "type": "flashcard",
                "icon": "cards",
                "title": f"Review your flashcards" if kw["wn_inbox"] < 5 else f"{kw['wn_inbox']} new words to study",
                "subtitle": "Flip through your words — takes 2 minutes.",
                "btn_text": "My Words",
                "btn_url": "/learning/words/",
                "urgency": "medium" if kw["wn_inbox"] >= 5 else "low",
            }
        )

    # ── GRAMMAR ──
    if td["grammar_attempts"] == 0:
        score = 20
        label = "Practice grammar"
        if kw["grammar_weakest"] and kw["grammar_weakest_p"] < 50:
            label = f"Practice: {kw['grammar_weakest']}"
            score += 10
        actions.append(
            {
                "score": score,
                "type": "grammar",
                "icon": "book",
                "title": label,
                "subtitle": "Learn the rules that make sentences work.",
                "btn_text": "Grammar",
                "btn_url": "/learning/grammar/",
                "urgency": "low",
            }
        )

    # ── WATCH (continue a show) ──
    if kw["resume_info"]:
        score = 20
        if td["watch_minutes"] == 0:
            score += 10
        if td["words_saved"] == 0:
            score += 5
        src = kw["resume_info"]
        actions.append(
            {
                "score": score,
                "type": "watch",
                "icon": "play",
                "title": "Continue watching",
                "subtitle": f"{src['source_title']}" f"{' · ' + src['ep_label'] if src['ep_label'] else ''}",
                "btn_text": "Watch",
                "btn_url": src["url"],
                "urgency": "low",
            }
        )

    # Fallback
    if not actions:
        actions.append(
            {
                "score": 10,
                "type": "watch",
                "icon": "play",
                "title": "Watch something new",
                "subtitle": "Find a show and start saving words.",
                "btn_text": "Browse shows",
                "btn_url": "/",
                "urgency": "low",
            }
        )

    return actions


# ─────────────────────────────────────────────
# COACH INSIGHT
# ─────────────────────────────────────────────


def _generate_insight(
    health,
    engagement,
    phase,
    accuracy_trend,
    retention_rate,
    weakest_words,
    dimension_gap,
    decaying_soon,
    confusions,
    vm_mastered,
    total_words,
    today_data,
):
    """Generate a positive, student-friendly insight.

    Lead with progress, then gently suggest next step.
    """
    # Always try to lead with something positive
    if accuracy_trend >= 10:
        return f"Your accuracy improved {accuracy_trend}% this week — nice work!"

    if today_data["quiz_attempts"] >= 10 and today_data["quiz_correct"] >= 8:
        pct = round(today_data["quiz_correct"] / today_data["quiz_attempts"] * 100)
        return f"{pct}% correct today across {today_data['quiz_attempts']} questions. Keep it up!"

    if vm_mastered >= 5:
        return f"You've mastered {vm_mastered} words so far. Every word counts!"

    if total_words >= 10 and decaying_soon >= 3:
        return f"You have {total_words} words saved. A quick review keeps them fresh."

    if engagement["pattern"] in ("daily_habit", "consistent"):
        return "Your consistency is paying off — that's the #1 factor in learning."

    if total_words >= 5:
        return f"{total_words} words in your collection. Practice a few to make them stick."

    return "Every word you save is a step forward. Keep exploring!"


# ─────────────────────────────────────────────
# MOTIVATION FRAMING
# ─────────────────────────────────────────────


def _frame_motivation(user, today_data, goal_pct, engagement, phase, vm_mastered, total_words):
    """Frame progress positively — celebrate what's done, make next step small."""
    if total_words == 0:
        return None

    # Always celebrate completed goal
    if goal_pct >= 100:
        return {
            "type": "complete",
            "text": "Daily goal reached!",
            "detail": f"{round(today_data['total_minutes'])}m of practice today. Well done!",
        }

    # Celebrate quiz effort
    if today_data["quiz_attempts"] >= 10 and today_data["quiz_correct"] >= 7:
        pct = round(today_data["quiz_correct"] / today_data["quiz_attempts"] * 100)
        return {
            "type": "streak",
            "text": f"{pct}% correct today!",
            "detail": f"{today_data['quiz_correct']} out of {today_data['quiz_attempts']} — your knowledge is growing.",
        }

    # Celebrate mastery milestone
    if vm_mastered >= 5:
        return {
            "type": "milestone",
            "text": f"{vm_mastered} words mastered!",
            "detail": "These words are in your long-term memory now.",
        }

    # Gentle nudge — but never "your words are waiting" (sounds like guilt)
    if today_data["total_minutes"] < 1 and total_words >= 5:
        return {
            "type": "start",
            "text": f"{total_words} words in your collection",
            "detail": "A quick 5-minute session goes a long way.",
        }

    # Progress toward goal
    daily_goal = user.daily_goal_minutes or 15
    remaining = max(0, round(daily_goal - today_data["total_minutes"]))
    return {
        "type": "progress",
        "text": f"{round(today_data['total_minutes'])}m done today",
        "detail": f"{remaining} more minutes to reach your daily goal.",
    }


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────


def _calc_lookup_save_rate(user, days=7):
    """What percentage of dictionary lookups lead to a word being saved?"""
    from learning.models import LookupLog

    total = LookupLog.objects.filter(
        user=user,
        created_at__date__gte=date.today() - timedelta(days=days),
    ).count()
    if total == 0:
        return 0
    saved = LookupLog.objects.filter(
        user=user,
        created_at__date__gte=date.today() - timedelta(days=days),
        saved_to_notebook=True,
    ).count()
    return round(saved / total * 100)
