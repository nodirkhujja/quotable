from __future__ import annotations

import base64
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

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

from clips.models import Episode, Quote, Source, Transcript
from learning.models import (
    BuildAttempt,
    ConfusionPair,
    DailyActivity,
    FavoriteQuote,
    FlashcardAttempt,
    GrammarAIExplanation,
    GrammarPracticeLog,
    LearningProgress,
    LookupLog,
    OnboardingResult,
    OnboardingSession,
    PageVisit,
    PatternMastery,
    PronunciationAssessment,
    QuoteMastery,
    SceneCoverage,
    SentencePattern,
    ShadowingLog,
    SourceProgress,
    UserInterest,
    UserLearningProfile,
)
from learning.services.rate_limiting import is_rate_limited as _rate_limited
from learning.services.rate_limiting import rate_limit_response as _rate_limit_response
from learning.utils.ai_sentence import check_sentence as ai_check_sentence
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success
from vocab.models import LineTranslation, LineVocab, SuggestedWord, VocabMastery, VocabWord, WordNote, WordTranslation


class ProgressView(LoginRequiredMixin, View):
    """GET /learning/progress/ — adaptive progress dashboard.

    Treats the page as a *trust surface*, not a stats dashboard:
      • Hero is state-aware — surfaces the action (slipping words) or
        a celebration (caught-up) and anchors both with proof: a real
        transcript line where the user's known words are highlighted.
        No comprehension % is rendered anywhere — proof can't be faked.
      • Trajectory reports a concrete weekly delta (mastered this week
        vs last week), not abstract activity.
      • Classic stats (donut, heatmap, totals) move into a collapsed
        Activity block for users who want them.
    """

    def get(self, request):
        from datetime import date, timedelta

        from django.db.models import Count, Sum
        from django.utils import timezone

        from clips.models import Transcript
        from learning.models import DailyActivity, SceneCoverage

        user = request.user
        today = date.today()
        now = timezone.now()

        # ── Word pipeline ──────────────────────────────────────────────────
        stage_qs = WordNote.objects.filter(user=user).values("stage").annotate(c=Count("id"))
        stages = {row["stage"]: row["c"] for row in stage_qs}
        inbox_count = stages.get("inbox", 0)
        learning_count = stages.get("learning", 0)
        mastered_count = stages.get("mastered", 0)
        total_words = inbox_count + learning_count + mastered_count

        # ── Difficulty breakdown — Easy / Medium / Hard ─────────────────
        # Classify each saved word by VocabWord tier when matched, else by
        # word length as a frequency proxy (short words ≈ common ≈ easy).
        # T1+T2 → Easy, T3 → Medium, T4+T5 → Hard.
        # Tier map is cached for an hour — VocabWord rarely changes.
        from django.core.cache import cache as _cache

        vw_tier_map = _cache.get("vw_tier_map")
        if vw_tier_map is None:
            vw_tier_map = dict(VocabWord.objects.values_list("word", "tier"))
            _cache.set("vw_tier_map", vw_tier_map, 3600)
        easy_count = medium_count = hard_count = 0
        for w in WordNote.objects.filter(user=user).values_list("word", flat=True):
            wl = (w or "").lower().strip()
            tier = vw_tier_map.get(wl)
            if tier is None:
                ln = len(wl)
                bucket = "easy" if ln <= 5 else "medium" if ln <= 8 else "hard"
            elif tier <= 2:
                bucket = "easy"
            elif tier == 3:
                bucket = "medium"
            else:
                bucket = "hard"
            if bucket == "easy":
                easy_count += 1
            elif bucket == "medium":
                medium_count += 1
            else:
                hard_count += 1

        # ── Slipping words: knew-it-once + overdue by >24h ────────────────
        # The strongest learning motivator we're not using is decay-fear.
        # Stage filter excludes inbox (never tested = not slipping).
        slipping_qs = WordNote.objects.filter(
            user=user,
            next_review__lt=now - timedelta(hours=24),
        ).exclude(stage="inbox")
        slipping_count = slipping_qs.count()
        slipping_words_sample = list(slipping_qs.order_by("next_review").values_list("word", flat=True)[:3])

        # ── Proof scene: a real transcript line where the user already
        # knows enough words. The marks ARE the metric — no number is
        # fakeable. We pick from SceneCoverage where coverage is high
        # AND the line has enough length to feel like a real line.
        proof = self._pick_proof_scene(user)

        # ── Trajectory: solid words this week vs last week ────────────────
        # "Solid" = stage in {learning, mastered} AND last_reviewed_at recent.
        # We can only honestly report what changed via review activity.
        week_start = now - timedelta(days=7)
        prev_week_start = now - timedelta(days=14)
        solid_this_week = WordNote.objects.filter(
            user=user,
            stage="mastered",
            last_reviewed_at__gte=week_start,
        ).count()
        solid_prev_week = WordNote.objects.filter(
            user=user,
            stage="mastered",
            last_reviewed_at__gte=prev_week_start,
            last_reviewed_at__lt=week_start,
        ).count()
        trajectory_delta = solid_this_week - solid_prev_week

        # ── Streak & today ─────────────────────────────────────────────────
        # Use the single source of truth. context_processor already
        # recomputed and persisted the corrected value, so user.streak_days
        # is now accurate after the request reaches this view.
        from learning.utils.activity import compute_streak_from_activity

        streak = compute_streak_from_activity(user, today)
        active_days_count = DailyActivity.objects.filter(user=user).count()

        da_today = DailyActivity.objects.filter(user=user, date=today).first()
        today_words = (da_today.words_saved + da_today.words_reviewed) if da_today else 0
        today_minutes = round(da_today.total_minutes) if da_today else 0

        # Words remaining to hit the daily goal (5).
        DAILY_GOAL = 5
        today_remaining = max(0, DAILY_GOAL - today_words)

        # ── All-time totals (collapsed Activity card) ────────────────────
        all_time = DailyActivity.objects.filter(user=user).aggregate(
            total_quiz=Sum("quiz_attempts"),
            total_minutes=Sum("total_minutes"),
        )
        total_quiz_attempts = all_time["total_quiz"] or 0
        total_minutes_all = round(all_time["total_minutes"] or 0)
        # Humanized: "7454 min" → "124h 14m"
        if total_minutes_all >= 60:
            _h, _m = divmod(total_minutes_all, 60)
            total_minutes_h = f"{_h}h {_m}m"
        else:
            total_minutes_h = f"{total_minutes_all}m"

        # ── Today's Plan — concrete daily prescription ──────────────────
        # Sums new-words-needed (to hit goal) + reviews-due (next_review
        # past now) and gives an honest minutes estimate. The answer to
        # "what should I do RIGHT NOW?" — replaces the vanity time stat.
        reviews_due_count = (
            WordNote.objects.filter(
                user=user,
                next_review__lte=now,
            )
            .exclude(stage="inbox")
            .count()
        )
        # ~30s per new save, ~30s per review action
        plan_seconds = (today_remaining + reviews_due_count) * 30
        plan_minutes = max(1, round(plan_seconds / 60)) if plan_seconds else 0
        if reviews_due_count > 0:
            plan_cta_url = reverse("learning:review")
            plan_cta_label = "Review now"
        elif today_remaining > 0:
            plan_cta_url = reverse("clips:home")
            plan_cta_label = "Browse clips"
        else:
            plan_cta_url = None
            plan_cta_label = None
        today_plan = {
            "new_target": today_remaining,
            "reviews_due": reviews_due_count,
            "minutes": plan_minutes,
            "cta_url": plan_cta_url,
            "cta_label": plan_cta_label,
            "done": (today_remaining == 0 and reviews_due_count == 0),
        }

        # ── At-risk words — concrete review targets with scene context ──
        # Top 5 most-overdue words. The most actionable info on the page,
        # surfaced as a hero list (not buried in a percentage subtitle).
        at_risk_qs = (
            WordNote.objects.filter(user=user, next_review__lt=now - timedelta(hours=24))
            .exclude(stage="inbox")
            .select_related("transcript", "transcript__episode", "transcript__source")
            .order_by("next_review")[:5]
        )
        at_risk_words = []
        for w in at_risk_qs:
            if w.next_review:
                delta = now - w.next_review
                if delta.days >= 1:
                    overdue_text = f"{delta.days}d overdue"
                else:
                    hrs = max(1, int(delta.total_seconds() // 3600))
                    overdue_text = f"{hrs}h overdue"
            else:
                overdue_text = "overdue"
            scene_show = ""
            scene_code = ""
            if w.transcript and w.transcript.episode:
                ep = w.transcript.episode
                scene_show = (w.transcript.source.title if w.transcript.source else "") or ""
                scene_code = f"S{ep.season:02d}E{ep.episode_number:02d}"
            elif w.transcript and w.transcript.source:
                scene_show = w.transcript.source.title or ""
            at_risk_words.append(
                {
                    "id": w.id,
                    "word": w.word,
                    "translation": w.translation,
                    "scene_show": scene_show,
                    "scene_code": scene_code,
                    "overdue": overdue_text,
                    "stage": w.stage,
                }
            )

        # ── Heatmap (LeetCode-style, range adapts to user's history) ───
        # Counts EVERY kind of learning activity. Range starts at the
        # user's FIRST active day (so a new user doesn't see an empty
        # year before their first study), capped at 53 weeks max.
        HEATMAP_WEEKS = 53
        days_count_max = HEATMAP_WEEKS * 7
        first_active = DailyActivity.objects.filter(user=user).order_by("date").values_list("date", flat=True).first()
        if first_active:
            # Snap back to the Monday of that first active week.
            first_monday = first_active - timedelta(days=first_active.weekday())
            days_from_first = (today - first_monday).days + 1
            days_count = min(days_count_max, max(days_from_first, 28))
        else:
            days_count = 28  # Brand-new user: just show ~4 weeks
        cutoff = today - timedelta(days=days_count - 1)
        raw = DailyActivity.objects.filter(user=user, date__gte=cutoff).values(
            "date",
            "words_saved",
            "words_reviewed",
            "quiz_attempts",
            "build_attempts",
            "shadow_sessions",
            "grammar_attempts",
            "flashcard_reviews",
            "lookups",
        )
        # cell_count includes lookups so even passive browsing lights up the
        # heatmap; learning_count excludes them so the headline stat only
        # reflects intentional study actions (saves, reviews, quiz, etc.).
        activity = {}
        for row in raw:
            learning = (
                (row["words_saved"] or 0)
                + (row["words_reviewed"] or 0)
                + (row["quiz_attempts"] or 0)
                + (row["build_attempts"] or 0)
                + (row["shadow_sessions"] or 0)
                + (row["grammar_attempts"] or 0)
                + (row["flashcard_reviews"] or 0)
            )
            total = learning + (row["lookups"] or 0)
            activity[str(row["date"])] = (total, learning)

        heatmap_days = []
        heatmap_total_actions = 0
        heatmap_active_days = 0
        heatmap_max_streak = 0
        _streak_run = 0
        for i in range(days_count - 1, -1, -1):
            d = today - timedelta(days=i)
            entry = activity.get(str(d))
            cnt, learning_cnt = entry if entry else (0, 0)
            heatmap_days.append({"date": str(d), "count": min(cnt, 9)})
            heatmap_total_actions += learning_cnt
            if cnt > 0:
                heatmap_active_days += 1
                _streak_run += 1
                if _streak_run > heatmap_max_streak:
                    heatmap_max_streak = _streak_run
            else:
                _streak_run = 0

        # Last 7 days — used by the streak strip and the weekly sparkline
        # tiles. Order: oldest → today.
        recent_7days = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            _entry = activity.get(str(d))
            recent_7days.append(
                {
                    "date": str(d),
                    "count": _entry[0] if _entry else 0,
                    "weekday_short": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()][0],
                    "is_today": (i == 0),
                }
            )

        # ── Word census — every word as a dot, ordered by created_at ─────
        # The visual census: each saved word is one dot in a tight grid,
        # colored by stage. A novel visualization for a vocab progress
        # page — most apps show aggregate counts, not the population.
        word_census = list(
            WordNote.objects.filter(user=user)
            .order_by("created_at")
            .values_list("word", "stage")[:500]  # cap to avoid massive grids
        )
        word_census_dots = [{"word": w, "stage": s or "inbox"} for (w, s) in word_census]

        # Milestone prediction is computed below after `active_days_total`
        # is built by the weekday-insight block.
        next_milestone = None
        days_to_next = None

        # ── Time-depth — how long words have been with the user ──────────
        # Replaces the old stage-segment ring (which collapses to one
        # uninformative segment when the user has all words in one stage).
        # Always meaningful: even at 100% mastered, this shows the *depth*
        # of the vocabulary — how much is freshly saved vs settled.
        recent_cutoff = now - timedelta(days=7)
        settling_cutoff = now - timedelta(days=30)
        depth_recent_count = WordNote.objects.filter(user=user, created_at__gte=recent_cutoff).count()
        depth_settling_count = WordNote.objects.filter(
            user=user, created_at__gte=settling_cutoff, created_at__lt=recent_cutoff
        ).count()
        depth_deep_count = WordNote.objects.filter(user=user, created_at__lt=settling_cutoff).count()

        # ── Scene backdrop — the user's most-saved-from show, used as the
        # cinematic blurred background (matches practice page treatment).
        from clips.models import Source as _Source

        scene_source_id = (
            WordNote.objects.filter(user=user, transcript__source__isnull=False)
            .values("transcript__source_id")
            .annotate(c=Count("id"))
            .order_by("-c")
            .values_list("transcript__source_id", flat=True)
            .first()
        )
        scene_source = _Source.objects.filter(id=scene_source_id).first() if scene_source_id else None

        # ── Top episodes — where the user saves words most ────────────────
        # Like LeetCode's "Skills" panel: which categories you grind in.
        # Limit to top 5; only count episodes (not standalone or movies).
        top_episodes = list(
            WordNote.objects.filter(user=user, transcript__episode__isnull=False)
            .values(
                "transcript__episode__id",
                "transcript__episode__season",
                "transcript__episode__episode_number",
                "transcript__episode__title",
                "transcript__source__title",
            )
            .annotate(c=Count("id"))
            .order_by("-c")[:5]
        )
        top_episodes = [
            {
                "title": (row["transcript__episode__title"] or "").strip(),
                "code": f"S{row['transcript__episode__season']:02d}E{row['transcript__episode__episode_number']:02d}",
                "show": row["transcript__source__title"] or "",
                "count": row["c"],
            }
            for row in top_episodes
        ]

        # ── Recent mastered words — the wins to celebrate ────────────────
        # Each carries scene context (episode + show) for emotional
        # anchoring. Words tied to scenes stick in memory better than
        # abstract counts.
        recent_mastered_qs = (
            WordNote.objects.filter(user=user, stage="mastered")
            .select_related("transcript", "transcript__episode", "transcript__source", "quote", "quote__source")
            .order_by("-last_reviewed_at", "-created_at")[:8]
        )
        recent_mastered = []
        for w in recent_mastered_qs:
            ts = w.last_reviewed_at or w.created_at
            delta = now - ts
            secs = int(delta.total_seconds())
            if secs < 60:
                age = "just now"
            elif secs < 3600:
                age = f"{secs // 60}m ago"
            elif secs < 86400:
                age = f"{secs // 3600}h ago"
            else:
                age = f"{secs // 86400}d ago"
            # Scene context: prefer transcript (full episode line), fallback to quote
            scene_show = ""
            scene_code = ""
            scene_quote = ""
            if w.transcript and w.transcript.episode:
                ep = w.transcript.episode
                scene_show = (w.transcript.source.title if w.transcript.source else "") or ""
                scene_code = f"S{ep.season:02d}E{ep.episode_number:02d}"
                if w.transcript.text:
                    line = w.transcript.text.strip()
                    # Cap at ~140 chars so the cinematic card doesn't get crowded
                    if len(line) > 140:
                        line = line[:140].rsplit(" ", 1)[0] + "…"
                    scene_quote = line
            elif w.quote and w.quote.source:
                scene_show = w.quote.source.title or ""
            # Scene image — the visual anchor. Prefer the per-word frame
            # (the actual moment they saved the word), fall back to the
            # quote thumbnail, then the show poster.
            scene_image = None
            try:
                if w.scene_frame and w.scene_frame.name:
                    scene_image = w.scene_frame.url
                elif w.quote and w.quote.thumbnail and w.quote.thumbnail.name:
                    scene_image = w.quote.thumbnail.url
                elif (
                    w.transcript
                    and w.transcript.source
                    and w.transcript.source.thumbnail
                    and w.transcript.source.thumbnail.name
                ):
                    scene_image = w.transcript.source.thumbnail.url
            except Exception:
                scene_image = None
            recent_mastered.append(
                {
                    "word": w.word,
                    "age": age,
                    "scene_show": scene_show,
                    "scene_code": scene_code,
                    "scene_quote": scene_quote,
                    "scene_image": scene_image,
                }
            )

        # ── All mastered words — for the typographic word wall ─────────
        # The actual proof of effort. Number stats say "28 mastered" but
        # users never see the words — they want to see what they earned.
        all_mastered_words = list(
            WordNote.objects.filter(user=user, stage="mastered")
            .order_by("-last_reviewed_at", "-created_at")
            .values_list("word", flat=True)[:60]
        )

        # ── Top show — for the "mostly from X" line ──────────────────────
        # Aggregate top_episodes by source title to find the dominant show.
        from collections import defaultdict

        _show_buckets = defaultdict(lambda: {"words": 0, "episodes": 0})
        for ep in top_episodes:
            s = (ep.get("show") or "").strip()
            if not s:
                continue
            _show_buckets[s]["words"] += ep.get("count", 0)
            _show_buckets[s]["episodes"] += 1
        top_show = None
        if _show_buckets:
            best = max(_show_buckets.items(), key=lambda kv: kv[1]["words"])
            from clips.models import Source as _Src

            poster = None
            try:
                src = _Src.objects.filter(title=best[0]).first()
                if src and src.thumbnail and src.thumbnail.name:
                    poster = src.thumbnail.url
            except Exception:
                poster = None
            top_show = {
                "title": best[0],
                "words": best[1]["words"],
                "episodes": best[1]["episodes"],
                "poster": poster,
            }

        # ── Word of the Week — one standout for variable reward ─────────
        # Picks the most-reviewed mastered word from the past week, or
        # falls back to the most recently mastered. Variable reward
        # (Hooked / Eyal): something different appears here as the user's
        # journey changes, creating anticipation rather than predictability.
        word_of_week = None
        wow_candidate = (
            WordNote.objects.filter(
                user=user,
                stage="mastered",
                last_reviewed_at__gte=now - timedelta(days=7),
            )
            .select_related("transcript", "transcript__episode", "transcript__source")
            .order_by("-review_count", "-last_reviewed_at")
            .first()
        )
        if not wow_candidate:
            wow_candidate = (
                WordNote.objects.filter(user=user, stage="mastered")
                .select_related("transcript", "transcript__episode", "transcript__source")
                .order_by("-last_reviewed_at")
                .first()
            )
        if wow_candidate:
            ep_str = ""
            scene_line = ""
            if wow_candidate.transcript:
                if wow_candidate.transcript.episode:
                    ep = wow_candidate.transcript.episode
                    src_title = wow_candidate.transcript.source.title if wow_candidate.transcript.source else ""
                    ep_str = f"{src_title} · S{ep.season:02d}E{ep.episode_number:02d}"
                if wow_candidate.transcript.text:
                    line = wow_candidate.transcript.text.strip()
                    # Truncate long lines for the card
                    if len(line) > 140:
                        line = line[:140].rsplit(" ", 1)[0] + "…"
                    scene_line = line
            word_of_week = {
                "word": wow_candidate.word,
                "translation": wow_candidate.translation,
                "definition": wow_candidate.definition,
                "ep": ep_str,
                "scene_line": scene_line,
                "review_count": wow_candidate.review_count,
            }

        # ── Recent saves feed (kept for backward compatibility) ──────────
        recent_qs = WordNote.objects.filter(user=user).order_by("-created_at")[:5]
        recent_words = []
        for w in recent_qs:
            delta = now - w.created_at
            secs = int(delta.total_seconds())
            if secs < 60:
                age = "just now"
            elif secs < 3600:
                age = f"{secs // 60}m ago"
            elif secs < 86400:
                age = f"{secs // 3600}h ago"
            elif secs < 86400 * 30:
                d = secs // 86400
                age = f"{d}d ago"
            else:
                d = secs // 86400
                age = f"{d}d ago"
            recent_words.append(
                {
                    "word": w.word,
                    "stage": w.stage,
                    "age": age,
                }
            )

        # ── CEFR Level — language identity (the killer card) ─────────────
        # Bands tuned for app context: thresholds reflect mastered count
        # in this product's spaced-repetition system, not raw vocab size.
        CEFR_BANDS = [
            ("A1-", 0, "Just starting"),
            ("A1", 25, "Beginner"),
            ("A2", 100, "Elementary"),
            ("B1", 300, "Intermediate"),
            ("B2", 700, "Upper-Intermediate"),
            ("C1", 1500, "Advanced"),
            ("C2", 3000, "Mastery"),
        ]
        cefr_level = CEFR_BANDS[0][0]
        cefr_label = CEFR_BANDS[0][2]
        cefr_threshold = 0
        cefr_next = CEFR_BANDS[1][0] if len(CEFR_BANDS) > 1 else None
        cefr_next_threshold = CEFR_BANDS[1][1] if len(CEFR_BANDS) > 1 else None
        for i, (lvl, threshold, label) in enumerate(CEFR_BANDS):
            if mastered_count >= threshold:
                cefr_level = lvl
                cefr_label = label
                cefr_threshold = threshold
                if i + 1 < len(CEFR_BANDS):
                    cefr_next = CEFR_BANDS[i + 1][0]
                    cefr_next_threshold = CEFR_BANDS[i + 1][1]
                else:
                    cefr_next = None
                    cefr_next_threshold = None
        if cefr_next_threshold:
            cefr_words_to_next = cefr_next_threshold - mastered_count
            denom = cefr_next_threshold - cefr_threshold
            cefr_progress_pct = round(((mastered_count - cefr_threshold) / denom) * 100) if denom > 0 else 0
            cefr_progress_pct = max(0, min(100, cefr_progress_pct))
        else:
            cefr_words_to_next = 0
            cefr_progress_pct = 100

        # ── Accuracy trend — quiz_correct / quiz_attempts, last 8 weeks ──
        # ONE query fetches 8 weeks of daily quiz data; we bucket in Python.
        # Used to be 8 separate aggregate queries — now collapsed to 1.
        eight_weeks_ago = today - timedelta(days=56)
        daily_quiz_rows = DailyActivity.objects.filter(
            user=user,
            date__gte=eight_weeks_ago,
        ).values("date", "quiz_attempts", "quiz_correct")
        # Index 0 = oldest week, 7 = current week (matches the loop above).
        weeks_buckets = [{"attempts": 0, "correct": 0} for _ in range(8)]
        for row in daily_quiz_rows:
            days_back = (today - row["date"]).days
            week_offset = 7 - (days_back // 7)
            if 0 <= week_offset < 8:
                weeks_buckets[week_offset]["attempts"] += row["quiz_attempts"] or 0
                weeks_buckets[week_offset]["correct"] += row["quiz_correct"] or 0
        accuracy_weekly = [
            {
                "pct": round((b["correct"] / b["attempts"]) * 100) if b["attempts"] else None,
                "attempts": b["attempts"],
            }
            for b in weeks_buckets
        ]
        # Current + change for the headline number on the card
        accuracy_current = accuracy_weekly[-1]["pct"]
        prev = accuracy_weekly[-2]["pct"] if len(accuracy_weekly) > 1 else None
        if accuracy_current is not None and prev is not None:
            accuracy_change = accuracy_current - prev
        else:
            accuracy_change = None

        # ── Retention rate — % of mastered words NOT slipping ────────────
        # Trust signal: proves mastery sticks. Computed from the SRS
        # schedule (mastered words overdue by >24h are slipping).
        mastered_slipping = WordNote.objects.filter(
            user=user,
            stage="mastered",
            next_review__lt=now - timedelta(hours=24),
        ).count()
        if mastered_count > 0:
            retention_rate = round(((mastered_count - mastered_slipping) / mastered_count) * 100)
        else:
            retention_rate = None

        # ── Practice breakdown — which ACTIVE practice modes dominate ──
        # Aggregates lifetime counts of true practice modes (active
        # recall, production, speaking, scheduled review). Lookups are
        # excluded — clicking a word for its translation is browsing,
        # not practice, and would otherwise dwarf real practice counts.
        _ptotals = DailyActivity.objects.filter(user=user).aggregate(
            quiz=Sum("quiz_attempts"),
            grammar=Sum("grammar_attempts"),
            shadow=Sum("shadow_loops"),
            flashcards=Sum("flashcard_reviews"),
            build=Sum("build_attempts"),
            reviews=Sum("words_reviewed"),
        )
        _practice_raw = [
            {"label": "Quizzes", "key": "quiz", "count": _ptotals.get("quiz") or 0, "color": "#FF6B35"},
            {"label": "Grammar", "key": "grammar", "count": _ptotals.get("grammar") or 0, "color": "#a855f7"},
            {"label": "Shadowing", "key": "shadow", "count": _ptotals.get("shadow") or 0, "color": "#58CC02"},
            {"label": "Flashcards", "key": "flashcards", "count": _ptotals.get("flashcards") or 0, "color": "#f59e0b"},
            {"label": "Sentences", "key": "build", "count": _ptotals.get("build") or 0, "color": "#3b82f6"},
            {"label": "Word reviews", "key": "reviews", "count": _ptotals.get("reviews") or 0, "color": "#10b981"},
        ]
        practice_modes = [m for m in _practice_raw if m["count"] > 0]
        practice_modes.sort(key=lambda m: -m["count"])
        practice_max = max((m["count"] for m in practice_modes), default=1)
        # Compute per-row width percentage so the template doesn't have to
        for m in practice_modes:
            m["pct"] = round((m["count"] / practice_max) * 100) if practice_max else 0
        practice_total = sum(m["count"] for m in practice_modes)
        top_practice = practice_modes[0] if practice_modes else None

        # Practice balance coach — detects what's missing or under-practiced
        # and tells the learner specifically what to do next. Order is
        # priority-of-impact: speaking > review hygiene > production >
        # grammar > flashcards. Only fires once the user has some volume
        # so we don't lecture brand-new users.
        practice_balance_tip = None
        _quiz_t = _ptotals.get("quiz") or 0
        _gram_t = _ptotals.get("grammar") or 0
        _shad_t = _ptotals.get("shadow") or 0
        _flash_t = _ptotals.get("flashcards") or 0
        _build_t = _ptotals.get("build") or 0
        _rev_t = _ptotals.get("reviews") or 0
        if practice_total >= 20:
            if _shad_t < 5:
                practice_balance_tip = {
                    "missing": "shadowing",
                    "text": "You haven't been speaking the words aloud — shadowing on the watch page is when accent and recall lock in. Even 2-3 lines a day makes a difference.",
                    "cta_label": "Try shadowing",
                    "cta_url": reverse("clips:home"),
                }
            elif mastered_count > 20 and _rev_t < 5:
                practice_balance_tip = {
                    "missing": "reviews",
                    "text": "You're saving and quizzing well, but skipping spaced-repetition reviews — without them, words you mastered will start slipping back.",
                    "cta_label": "Review now",
                    "cta_url": reverse("learning:review"),
                }
            elif _build_t < 5 and (_quiz_t + _flash_t) > 50:
                practice_balance_tip = {
                    "missing": "sentence building",
                    "text": "You recognise words well — try building sentences too. Producing your own beats just spotting answers in a multiple-choice list.",
                    "cta_label": "Build sentences",
                    "cta_url": reverse("learning:practice"),
                }
            elif _gram_t < 10 and _quiz_t > _gram_t * 6:
                practice_balance_tip = {
                    "missing": "grammar",
                    "text": "Heavy on vocabulary quizzes, light on grammar — patterns are what let you assemble new sentences instead of reciting saved ones.",
                    "cta_label": "Drill grammar",
                    "cta_url": reverse("learning:practice"),
                }
            elif _flash_t < 10:
                practice_balance_tip = {
                    "missing": "flashcards",
                    "text": "Today's-Words flashcards are a 60-second daily fix — they drill the 5 newest words you saved while they're still warm.",
                    "cta_label": "Today's words",
                    "cta_url": reverse("learning:practice"),
                }
            else:
                practice_balance_tip = {
                    "missing": None,
                    "text": "Nice mix — you're balancing recognition, recall, production, and speaking. That's the rare combo that actually compounds.",
                    "cta_label": None,
                    "cta_url": None,
                }

        # ── Shadowing — speaking practice from the watch page ───────────
        # ShadowingLog records each session; we surface lifetime totals
        # plus a "no-subs" milestone count (proof you spoke a line solo).
        from learning.models import ShadowingLog

        shadow_logs = ShadowingLog.objects.filter(user=user)
        shadow_sessions_total = shadow_logs.count()
        shadow_minutes_total = round((shadow_logs.aggregate(s=Sum("duration_sec"))["s"] or 0) / 60)
        shadow_no_subs_count = shadow_logs.filter(reached_no_subs=True).count()
        shadow_unique_lines = shadow_logs.values("transcript_id").distinct().count()
        # Each loop = one full speaking pass. This is the "speaking turns"
        # metric — every time the mic actively heard you say the line.
        shadow_loops_total = shadow_logs.aggregate(s=Sum("total_loops"))["s"] or 0
        # Top show by shadowing activity (used for context)
        shadow_top_show = (
            shadow_logs.filter(source__isnull=False)
            .values("source__title")
            .annotate(c=Count("id"))
            .order_by("-c")
            .first()
        )
        shadow_top_show_title = shadow_top_show["source__title"] if shadow_top_show else None
        # Recent shadowed lines (5)
        recent_shadow_lines = list(
            shadow_logs.select_related("source", "episode")
            .order_by("-created_at" if False else "-id")[:5]
            .values(
                "source__title",
                "episode__season",
                "episode__episode_number",
                "line_text",
                "phases_completed",
                "reached_no_subs",
            )
        )
        recent_shadowed = []
        for r in recent_shadow_lines:
            line = (r.get("line_text") or "").strip()
            if len(line) > 80:
                line = line[:80].rsplit(" ", 1)[0] + "…"
            ep_code = ""
            if r.get("episode__season") and r.get("episode__episode_number"):
                ep_code = f"S{r['episode__season']:02d}E{r['episode__episode_number']:02d}"
            recent_shadowed.append(
                {
                    "line": line,
                    "show": r.get("source__title") or "",
                    "ep_code": ep_code,
                    "phases": r.get("phases_completed") or 0,
                    "no_subs": r.get("reached_no_subs") or False,
                }
            )

        # ── Onboarding level ──────────────────────────────────────────────
        level = level_display = ""
        ob = OnboardingSession.objects.filter(user=user, completed_at__isnull=False).first()
        if ob:
            level = ob.level
            level_display = {
                "beginner": "Beginner",
                "intermediate": "Intermediate",
                "upper_intermediate": "Upper-Intermediate",
                "advanced": "Advanced",
            }.get(level, level)

        # ── Passport-style identity fields ─────────────────────────────
        # First/last name + formatted issue date for the bio-data block.
        passport_holder = (user.first_name or "").strip().upper() or user.username.upper()
        passport_lastname = (user.last_name or "").strip().upper()
        if user.date_joined:
            passport_issued = user.date_joined.strftime("%d %b %Y").upper()
        else:
            passport_issued = ""
        passport_country = "QTB"
        level_code = {
            "beginner": "BEG",
            "intermediate": "INT",
            "upper_intermediate": "UPI",
            "advanced": "ADV",
        }.get(level, "—")

        # Machine-readable zone — fake but characterful, encodes real data.
        def _mrz_pad(s, n):
            return (s.replace(" ", "<")[:n] + ("<" * n))[:n]

        line1 = (
            f"P<{passport_country}"
            f"{_mrz_pad(passport_lastname or 'LEARNER', 30)}"
            f"<<{_mrz_pad(passport_holder, 14)}"
        )[:44]
        line2 = (
            f"{str(total_words).zfill(6)}"
            f"<{level_code:<3}"
            f"{(user.date_joined.strftime('%y%m%d') if user.date_joined else '000000')}"
            f"<M{(today.strftime('%y%m%d'))}"
            f"<{str(streak).zfill(3)}"
            f"<{str(mastered_count).zfill(4)}"
            f"<<<<<<<<<<<<<<<<<<<<<"
        )[:44]
        passport_mrz = [line1, line2]

        # ── Hero state ────────────────────────────────────────────────────
        if total_words < 5:
            hero_state = "new"
        elif slipping_count > 0:
            hero_state = "slipping"
        else:
            hero_state = "solid"

        # ── Coach line — state-aware nudge that adapts to where the
        # user actually is. Specificity over cheerleading: every line
        # has a number, every CTA has a verb. Order matters — first
        # match wins, so the most actionable state surfaces first.
        REVIEW_SECONDS_PER_WORD = 30  # ~30s per cloze + answer

        def _est_minutes(n):
            return max(1, round(n * REVIEW_SECONDS_PER_WORD / 60))

        # Default fallback
        coach = {
            "headline": "Save your first words",
            "sub": "Words you save show up here as you build your notebook.",
            "cta": "Browse episodes",
            "url": reverse("clips:home"),
            "tone": "neutral",
        }

        if total_words < 5:
            need = 5 - total_words
            coach = {
                "headline": f"Save {need} more word{'' if need == 1 else 's'} to unlock review",
                "sub": "Pick a clip, tap a word — it'll land in your notebook.",
                "cta": "Browse episodes",
                "url": reverse("clips:home"),
                "tone": "neutral",
            }
        elif streak >= 1 and today_words == 0:
            coach = {
                "headline": f"Day {streak} streak — don't break it",
                "sub": "5 words takes about 3 minutes. Keep the chain alive.",
                "cta": "Open notebook",
                "url": reverse("learning:word_note_list"),
                "tone": "streak",
            }
        else:
            # Caught up + active. Suggest discovery.
            milestones = [25, 50, 100, 200, 500, 1000]
            target = next((m for m in milestones if m > total_words), None)
            if target:
                gap = target - total_words
                coach = {
                    "headline": f"Caught up — discover {gap} more to hit {target}",
                    "sub": f"You're {round((total_words / target) * 100)}% of the way to your next milestone.",
                    "cta": "Browse episodes",
                    "url": reverse("clips:home"),
                    "tone": "go",
                }
            else:
                coach = {
                    "headline": "Caught up — keep the momentum",
                    "sub": "Discover 10 new words this week to stay sharp.",
                    "cta": "Browse episodes",
                    "url": reverse("clips:home"),
                    "tone": "go",
                }

        # ── The story so far — origin moment ──────────────────────────────
        # "Started 47 days ago. First word: matters."
        # Surprising in a personal way: most users forget when they began
        # OR what their first save was. Re-anchoring those two facts is
        # the smallest possible piece of personal narrative.
        journey_days = max(0, (today - user.date_joined.date()).days) if user.date_joined else 0
        first_word_row = WordNote.objects.filter(user=user).order_by("created_at").values("word").first()
        first_word = (first_word_row or {}).get("word") or ""

        # ── Day-of-week insight — "Your Tuesdays are 2.3× your average" ──
        # Group DailyActivity by weekday, compute mean activity per weekday,
        # find the strongest weekday, compare to the overall mean. Only show
        # when we have ≥7 active days AND the multiplier is meaningful (≥1.5).
        best_weekday_name = None
        best_weekday_multiplier = None
        from collections import defaultdict

        wd_buckets: dict[int, list[int]] = defaultdict(list)
        active_days_total = 0
        active_actions_total = 0
        for da in DailyActivity.objects.filter(user=user).only("date", "words_saved", "quiz_attempts"):
            actions = (da.words_saved or 0) + (da.quiz_attempts or 0)
            if actions <= 0:
                continue
            wd_buckets[da.date.weekday()].append(actions)
            active_days_total += 1
            active_actions_total += actions
        if active_days_total >= 7 and len(wd_buckets) >= 3:
            wd_avgs = {wd: sum(vs) / len(vs) for wd, vs in wd_buckets.items()}
            overall_avg = active_actions_total / active_days_total
            best_wd, best_avg = max(wd_avgs.items(), key=lambda kv: kv[1])
            if overall_avg > 0:
                ratio = best_avg / overall_avg
                if ratio >= 1.5:
                    best_weekday_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
                        best_wd
                    ]
                    best_weekday_multiplier = round(ratio, 1)

        # ── Predicted next milestone — small "at this rate" insight ──────
        # Always compute the next milestone (even without pace data).
        # Goal-gradient psychology: visible target ahead increases motivation.
        milestones = [25, 50, 100, 200, 500, 1000, 2000]
        target = next((m for m in milestones if m > total_words), None)
        milestone_remaining = (target - total_words) if target else 0
        if target:
            next_milestone = target
        if active_days_total >= 3 and total_words >= 5:
            avg_per_day = active_actions_total / max(active_days_total, 1)
            if target and avg_per_day > 0:
                gap = target - total_words
                pace_days = round(gap / avg_per_day)
                if 1 <= pace_days <= 365:
                    days_to_next = pace_days

        return render(
            request,
            "learning/progress.html",
            {
                # Hero state
                "hero_state": hero_state,
                "slipping_count": slipping_count,
                "slipping_words_sample": slipping_words_sample,
                # Coach line — state-aware nudge above the ring
                "coach": coach,
                # The story so far
                "journey_days": journey_days,
                "first_word": first_word,
                # Day-of-week insight (None when not enough data to be honest)
                "best_weekday_name": best_weekday_name,
                "best_weekday_multiplier": best_weekday_multiplier,
                # Proof scene
                "proof_scene": proof,
                # Trajectory
                "solid_this_week": solid_this_week,
                "solid_prev_week": solid_prev_week,
                "trajectory_delta": trajectory_delta,
                # Pipeline / activity (collapsed)
                "inbox_count": inbox_count,
                "learning_count": learning_count,
                "mastered_count": mastered_count,
                "total_words": total_words,
                # Difficulty breakdown — counts only, no fake pool denominators
                "easy_count": easy_count,
                "medium_count": medium_count,
                "hard_count": hard_count,
                "streak": streak,
                "active_days_count": active_days_count,
                "today_words": today_words,
                "today_minutes": today_minutes,
                "today_remaining": today_remaining,
                "today_plan": today_plan,
                "at_risk_words": at_risk_words,
                "total_minutes_h": total_minutes_h,
                "total_quiz_attempts": total_quiz_attempts,
                "total_minutes": total_minutes_all,
                "heatmap_days": heatmap_days,
                "heatmap_total_actions": heatmap_total_actions,
                "heatmap_active_days": heatmap_active_days,
                "heatmap_max_streak": heatmap_max_streak,
                "recent_7days": recent_7days,
                "top_episodes": top_episodes,
                "recent_words": recent_words,
                "recent_mastered": recent_mastered,
                "word_of_week": word_of_week,
                "scene_source": scene_source,
                # CEFR level
                "cefr_level": cefr_level,
                "cefr_label": cefr_label,
                "cefr_next": cefr_next,
                "cefr_threshold": cefr_threshold,
                "cefr_next_threshold": cefr_next_threshold,
                "cefr_words_to_next": cefr_words_to_next,
                "cefr_progress_pct": cefr_progress_pct,
                # Quality metrics
                "accuracy_current": accuracy_current,
                "accuracy_change": accuracy_change,
                "accuracy_weekly": accuracy_weekly,
                "retention_rate": retention_rate,
                "mastered_slipping": mastered_slipping,
                # Shadowing (speaking practice from watch page)
                "shadow_sessions_total": shadow_sessions_total,
                "shadow_minutes_total": shadow_minutes_total,
                "shadow_no_subs_count": shadow_no_subs_count,
                "shadow_unique_lines": shadow_unique_lines,
                "shadow_loops_total": shadow_loops_total,
                # Practice breakdown — which mode dominates
                "practice_modes": practice_modes,
                "practice_total": practice_total,
                "top_practice": top_practice,
                "practice_balance_tip": practice_balance_tip,
                # Top show for the "mostly from X" identity line
                "top_show": top_show,
                # The actual word collection (not just counts)
                "all_mastered_words": all_mastered_words,
                "shadow_top_show_title": shadow_top_show_title,
                "recent_shadowed": recent_shadowed,
                "depth_recent_count": depth_recent_count,
                "depth_settling_count": depth_settling_count,
                "depth_deep_count": depth_deep_count,
                "word_census_dots": word_census_dots,
                "next_milestone": next_milestone,
                "milestone_remaining": milestone_remaining,
                "days_to_next": days_to_next,
                "passport_holder": passport_holder,
                "passport_lastname": passport_lastname,
                "passport_issued": passport_issued,
                "passport_country": passport_country,
                "passport_mrz": passport_mrz,
                "level_code": level_code,
                "level": level,
                "level_display": level_display,
                "daily_word_goal": 5,
            },
        )

    @staticmethod
    def _pick_proof_scene(user):
        """Pick a real transcript line + mark the user's known words.

        Returns a dict with `tokens` (list of {text, known} in order) and
        source/episode info, or None if we can't find one. The line has
        to be long enough to feel like a real moment (≥6 tokens) and have
        ≥80% coverage so the marks dominate the unknowns.
        """
        from clips.models import Transcript
        from learning.models import SceneCoverage
        from learning.services.coverage import COMMON_WORDS, _tokenize, _user_known_words

        # Pull from SceneCoverage if we have it (cheap), otherwise compute
        # directly from a sampling of transcripts (fallback).
        sc = (
            SceneCoverage.objects.filter(user=user, coverage__gte=0.80, total_tokens__gte=6, total_tokens__lte=18)
            .select_related("transcript", "transcript__episode", "transcript__source")
            .order_by("-coverage", "-last_calculated")
            .first()
        )
        if sc and sc.transcript:
            tr = sc.transcript
        else:
            # Fallback: scan transcripts the user has interacted with via
            # WordNote → transcript FK. Cheap and likely to hit a familiar line.
            tr = (
                Transcript.objects.filter(word_notes__user=user)
                .exclude(text__isnull=True)
                .exclude(text="")
                .select_related("episode", "source")
                .order_by("-id")
                .first()
            )
        if not tr or not tr.text:
            return None

        text = tr.text.strip()
        # Build per-token mark map. Tokenize the same way the coverage
        # engine does so the mark set matches the scoring.
        known_set = _user_known_words(user) | COMMON_WORDS
        # Walk the original text preserving punctuation; emit a list of
        # word/space pieces so the template can render with structure.
        import re as _re

        pieces = []
        for chunk in _re.findall(r"[A-Za-z']+|[^A-Za-z']+", text):
            if _re.match(r"^[A-Za-z']+$", chunk):
                norm = chunk.lower().strip("'").replace("'", "")
                pieces.append(
                    {
                        "text": chunk,
                        "is_word": True,
                        "known": norm in known_set,
                    }
                )
            else:
                pieces.append({"text": chunk, "is_word": False, "known": False})

        ep = tr.episode
        src = tr.source if tr.source else (ep.source if ep else None)
        source_title = src.title if src else ""
        ep_code = f"S{ep.season:02d}E{ep.episode_number:02d}" if ep else ""
        # Optional source thumbnail — used as a moody backdrop on the hero.
        thumb_url = ""
        if src is not None:
            try:
                if src.thumbnail and src.thumbnail.name:
                    thumb_url = src.thumbnail.url
            except (ValueError, AttributeError):
                pass
        return {
            "pieces": pieces,
            "source_title": source_title,
            "ep_code": ep_code,
            "thumbnail_url": thumb_url,
        }


class ReviewPageView(LoginRequiredMixin, View):
    """GET /learning/review/ — Cloze fill-in-the-blank review page."""

    def get(self, request):
        words = _get_practice_words(request.user)
        return render(request, "learning/review.html", {"words": words})


class CollectionPageView(LoginRequiredMixin, View):
    """GET /learning/collection/ — Visual shelf of mastered words grouped by show."""

    def get(self, request):
        from vocab.models import LineVocab, VocabMastery

        user = request.user

        # All sources the user has ANY mastery on, with per-source counts
        mastered_qs = VocabMastery.objects.filter(
            user=user,
            level__in=("strong", "mastered"),
        ).select_related("vocab", "vocab__source", "vocab__transcript", "vocab__transcript__episode")

        # Group masteries by source
        by_source = {}
        for m in mastered_qs:
            src = m.vocab.source
            by_source.setdefault(src.id, {"source": src, "masteries": []})
            by_source[src.id]["masteries"].append(m)

        # For each source, compute total vocab count for progress bar
        source_groups = []
        for group in by_source.values():
            src = group["source"]
            total = LineVocab.objects.filter(source=src).count()
            mastered_count = len(group["masteries"])
            # Sort masteries by most recently advanced
            group["masteries"].sort(key=lambda m: m.last_reviewed or m.id, reverse=True)
            source_groups.append(
                {
                    "source": src,
                    "mastered_count": mastered_count,
                    "total": total,
                    "pct": round(mastered_count / total * 100) if total else 0,
                    "masteries": group["masteries"],
                }
            )
        # Sort by most-mastered first
        source_groups.sort(key=lambda g: -g["mastered_count"])

        # Overall stats
        total_mastered = sum(g["mastered_count"] for g in source_groups)
        total_attempts = sum(g["total"] for g in source_groups)

        # Next-up: closest-to-mastered words (good level, highest p_known)
        next_up = (
            VocabMastery.objects.filter(
                user=user,
                level="good",
            )
            .select_related("vocab")
            .order_by("-p_known")[:6]
        )

        return render(
            request,
            "learning/collection.html",
            {
                "source_groups": source_groups,
                "total_mastered": total_mastered,
                "total_tracked": total_attempts,
                "overall_pct": round(total_mastered / total_attempts * 100) if total_attempts else 0,
                "next_up": next_up,
            },
        )


class StreakPageView(LoginRequiredMixin, View):
    """GET /learning/streak/ — Calendar view showing daily activity and streak."""

    def get(self, request):
        from datetime import date as date_mod
        from datetime import timedelta

        from learning.models import DailyActivity

        user = request.user
        today = date_mod.today()
        # Last 12 weeks (84 days) for a GitHub-style contribution grid
        start = today - timedelta(days=83)
        activity_qs = DailyActivity.objects.filter(user=user, date__gte=start).order_by("date")
        activity_by_date = {a.date: a for a in activity_qs}

        goal = user.daily_xp_goal
        days = []
        for offset in range(84):
            d = start + timedelta(days=offset)
            a = activity_by_date.get(d)
            xp = a.xp_earned if a else 0
            pct = min(100, round(xp / goal * 100)) if goal else 0
            # Intensity bucket for heatmap
            if xp == 0:
                level = 0
            elif pct < 50:
                level = 1
            elif pct < 100:
                level = 2
            elif pct < 150:
                level = 3
            else:
                level = 4
            days.append(
                {
                    "date": d,
                    "xp": xp,
                    "level": level,
                    "is_today": d == today,
                    "words_mastered": a.words_mastered if a else 0,
                }
            )

        # Total stats
        total_days_practiced = sum(1 for d in days if d["xp"] > 0)
        total_xp = sum(d["xp"] for d in days)
        total_mastered = sum(d["words_mastered"] for d in days)

        return render(
            request,
            "learning/streak.html",
            {
                "streak_days": user.streak_days or 0,
                "longest_streak": user.longest_streak or 0,
                "daily_goal_xp": goal,
                "today_xp": activity_by_date.get(today).xp_earned if today in activity_by_date else 0,
                "days": days,
                "total_days_practiced": total_days_practiced,
                "total_xp": total_xp,
                "total_mastered": total_mastered,
            },
        )


class StatsView(LoginRequiredMixin, View):
    """Detailed stats page — grammar, performance, confusion pairs, dimensions."""

    def get(self, request):
        from learning.utils.coach_engine import build_coach_context

        ctx = build_coach_context(request.user)
        ctx["week_data_json"] = json.dumps(ctx["week_data"])
        return render(request, "learning/stats.html", ctx)
