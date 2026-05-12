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
from learning.services.practice import get_practice_words as _get_practice_words
from learning.services.rate_limiting import is_rate_limited as _rate_limited
from learning.services.rate_limiting import rate_limit_response as _rate_limit_response
from learning.utils.ai_sentence import check_sentence as ai_check_sentence
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success
from quiz.models import QuizAttempt, ReviewSession
from vocab.models import LineTranslation, LineVocab, SuggestedWord, VocabMastery, VocabWord, WordNote, WordTranslation


class PracticeHubView(LoginRequiredMixin, View):
    """GET /learning/practice/ — Practice hub launchpad."""

    def get(self, request):
        user = request.user

        # Word counts by stage
        user_words = WordNote.objects.filter(user=user)
        total_words = user_words.count()
        words_with_video = user_words.exclude(quote=None, transcript=None).count()

        # Stage breakdown for badges
        stage_counts = dict(user_words.values_list("stage").annotate(c=models.Count("id")).values_list("stage", "c"))
        mastered = stage_counts.get("mastered", 0)
        inbox = stage_counts.get("inbox", 0)
        learning = stage_counts.get("learning", 0)

        weak_count = user_words.filter(confidence__lt=40).exclude(stage="mastered").count()

        # Today's activity
        from datetime import date

        today = date.today()
        today_sessions = ReviewSession.objects.filter(
            user=user,
            ended_at__isnull=False,
            started_at__date=today,
        )
        today_count = today_sessions.count()
        today_best = today_sessions.order_by("-score").values_list("score", flat=True).first()

        # Words saved today
        today_words = user_words.filter(created_at__date=today).count()

        from learning.models import SentencePattern

        total_sentences = SentencePattern.objects.count()

        # ── Cinematic context: surface the user's "current show" ──
        # Most recently saved word's source → fall back to first source with content.
        from clips.models import Source

        current_source = None
        recent_word = (
            user_words.exclude(quote=None).order_by("-created_at").first()
            or user_words.exclude(transcript=None).order_by("-created_at").first()
        )
        if recent_word:
            if recent_word.quote and recent_word.quote.source:
                current_source = recent_word.quote.source
            elif recent_word.transcript and recent_word.transcript.episode:
                current_source = recent_word.transcript.episode.source
        if not current_source:
            current_source = Source.objects.filter(thumbnail__isnull=False).exclude(thumbnail="").first()

        return render(
            request,
            "learning/practice.html",
            {
                "total_words": total_words,
                "words_with_video": words_with_video,
                "mastered": mastered,
                "streak": user.streak_days,
                "today_count": today_count,
                "today_best": today_best or 0,
                "today_words": today_words,
                "total_sentences": total_sentences,
                "weak_count": weak_count,
                "current_source": current_source,
            },
        )


class CoachView(LoginRequiredMixin, View):
    """GET /learning/coach/ — Data-driven daily learning coach.

    Uses coach_engine.py which combines:
      - Math/Data Science (BKT decay, Elo gaps, forgetting curves)
      - Psychology (motivation, fatigue, streak protection)
      - Expert Teaching (skill sequencing, weakness targeting)
    """

    def get(self, request):
        from learning.utils.coach_engine import build_coach_context

        ctx = build_coach_context(request.user)
        ctx["week_data_json"] = json.dumps(ctx["week_data"])
        return render(request, "learning/coach.html", ctx)


class ShadowingView(LoginRequiredMixin, View):
    """Redirect to My Words — shadowing is now integrated there."""

    def get(self, request):
        return redirect("learning:word_note_list")


class OwnTheWordPageView(LoginRequiredMixin, View):
    """GET /learning/own/

    Renders the focused acquisition page for ONE saved word.

    Server-side picks the word, generates the opening AI question, and
    bakes everything into the template render — so the user lands on a
    page that's already showing the question, not a loading spinner.

    Daily cap (rolling 24h) is enforced here. Past the cap the page
    renders a soft "come back tomorrow" message, not the ritual.
    """

    def get(self, request):
        from learning.utils.ai_own_word import daily_cap_remaining, generate_translation_pair, pick_word_for_ownership

        remaining = daily_cap_remaining(request.user)
        if remaining <= 0:
            return render(
                request,
                "learning/own_the_word.html",
                {
                    "capped": True,
                    "note": None,
                    "pair": None,
                },
            )

        note = pick_word_for_ownership(request.user)
        if not note:
            return render(
                request,
                "learning/own_the_word.html",
                {
                    "capped": False,
                    "no_words": True,
                    "note": None,
                    "pair": None,
                },
            )

        # Source line context — original transcript line if present.
        source_line_en = ""
        clip_url = ""
        clip_start = None
        clip_end = None
        if note.transcript_id:
            try:
                tr = note.transcript
                source_line_en = (tr.text or "").strip()
                clip_start = float(tr.start_time) if tr.start_time is not None else None
                clip_end = float(tr.end_time) if tr.end_time is not None else None
                ep = tr.episode
                if ep and ep.video_file:
                    clip_url = ep.video_file.url
            except Exception:
                pass

        # Pull kind/category AND description from the originating LineVocab.
        # WordNote.definition is often empty (only filled when user opened the
        # micro-definition card on save). LineVocab.description is the
        # lexicon-curated explanation — better fallback than nothing.
        kind = ""
        category = ""
        lv_description = ""
        if note.transcript_id:
            from vocab.models import LineVocab

            lv = (
                LineVocab.objects.filter(transcript_id=note.transcript_id, english__iexact=note.word)
                .only("kind", "category", "description")
                .first()
            )
            if lv:
                kind = lv.kind or ""
                category = lv.category or ""
                lv_description = lv.description or ""

        # Display definition: prefer WordNote.definition, fall back to lexicon.
        display_definition = (note.definition or "").strip() or lv_description.strip()

        interest_pool, user_name, user_level = _build_learner_context(request.user)
        pair = generate_translation_pair(
            word=note.word,
            translation=note.translation or note.definition or "",
            kind=kind,
            category=category,
            source_line_en=source_line_en,
            user_level=user_level,
            interests=interest_pool,
            user_name=user_name,
        )

        # Shadowing eligibility — only if we have a clip AND the source line
        # actually contains the target word (otherwise asking the learner to
        # echo a line that doesn't contain the word makes no sense).
        from learning.utils.ai_quiz import _word_used_in_reply

        shadow_eligible = bool(
            clip_url
            and clip_start is not None
            and clip_end is not None
            and source_line_en
            and _word_used_in_reply(note.word, source_line_en)
        )

        return render(
            request,
            "learning/own_the_word.html",
            {
                "capped": False,
                "remaining": remaining,
                "note": note,
                "kind": kind,
                "category": category,
                "user_level": user_level,
                "clip_url": clip_url,
                "clip_start": clip_start,
                "clip_end": clip_end,
                "source_line_en": source_line_en,
                "shadow_eligible": shadow_eligible,
                "display_definition": display_definition,
                "pair": pair,
            },
        )


class OwnTheWordGradeView(LoginRequiredMixin, View):
    """POST /learning/own/grade/

    Grades ONE translation. Body:
      { note_id, reference_en, user_translation_en }

    Returns:
      { score (0-2), word_used, feedback_uz, ai_unavailable }
    """

    def post(self, request):
        limited, _remaining, retry_after = _rate_limited(request.user.id, "own_the_word")
        if limited:
            return _rate_limit_response("own_the_word", retry_after)

        data = json_body(request)
        note_id = data.get("note_id")
        reference_en = (data.get("reference_en") or "").strip()
        user_translation = (data.get("user_translation_en") or "").strip()

        if not note_id:
            return error("note_id required")
        if not reference_en:
            return error("reference_en required")

        note = WordNote.objects.filter(id=note_id, user=request.user).only("word", "translation", "definition").first()
        if not note:
            return error("Word not found", 404)

        _i, _n, user_level = _build_learner_context(request.user)

        from learning.utils.ai_own_word import grade_translation

        result = grade_translation(
            word=note.word,
            reference_en=reference_en,
            user_translation_en=user_translation,
            user_level=user_level,
        )
        return success(result)


class OwnTheWordCloseView(LoginRequiredMixin, View):
    """POST /learning/own/close/

    Final close. Body:
      { note_id, score_1, score_2,
        reference_1, user_translation_1,
        reference_2, user_translation_2 }

    Combines the two turn-grades into the final score, applies the confidence
    bump, records a QuizAttempt for the daily cap, returns ceremony payload.
    """

    def post(self, request):
        limited, _remaining, retry_after = _rate_limited(request.user.id, "own_the_word")
        if limited:
            return _rate_limit_response("own_the_word", retry_after)

        data = json_body(request)
        note_id = data.get("note_id")
        try:
            score_1 = int(data.get("score_1", 0))
            score_2 = int(data.get("score_2", 0))
        except (TypeError, ValueError):
            score_1 = score_2 = 0
        reference_1 = (data.get("reference_1") or "").strip()
        reference_2 = (data.get("reference_2") or "").strip()
        ut_1 = (data.get("user_translation_1") or "").strip()
        ut_2 = (data.get("user_translation_2") or "").strip()
        answer_1 = ut_1
        answer_2 = ut_2

        if not note_id:
            return error("note_id required")

        note = WordNote.objects.filter(id=note_id, user=request.user).first()
        if not note:
            return error("Word not found", 404)

        kind, category = "", ""
        if note.transcript_id:
            from vocab.models import LineVocab

            lv = (
                LineVocab.objects.filter(transcript_id=note.transcript_id, english__iexact=note.word)
                .only("kind", "category")
                .first()
            )
            if lv:
                kind = lv.kind or ""
                category = lv.category or ""

        _interests, _name, user_level = _build_learner_context(request.user)

        from learning.utils.ai_own_word import close_session

        result = close_session(
            word=note.word,
            translation=note.translation or note.definition or "",
            score_1=score_1,
            score_2=score_2,
            user_translation_1=ut_1,
            user_translation_2=ut_2,
            reference_1=reference_1,
            reference_2=reference_2,
            kind=kind,
            category=category,
            user_level=user_level,
        )

        from datetime import timedelta

        from django.utils import timezone as _tz

        from quiz.models import QuizAttempt
        from vocab.models import bkt_update

        # ── Mastery update via BKT (one math across the whole app) ──
        # Run two bkt_update steps — one per turn — using translate_back BKT
        # params (UZ→EN sentence retranslation, the closest match for what an
        # Own-the-Word turn actually is). Each turn's "correct" is score >= 2,
        # consistent with the AI grader's own threshold.
        #
        # We FLOOR the resulting confidence at the score-driven bump table so
        # an Own-the-Word session always grants a meaningful boost — the
        # ritual is pedagogically heavier than a normal quiz answer, and BKT
        # at high p_known produces tiny gains. Floor preserves the cinematic
        # feel; BKT does the math everywhere else.
        confidence_before = note.confidence or 0
        p_known = confidence_before / 100.0
        for s in (score_1, score_2):
            turn_correct = int(s) >= 2
            p_known = bkt_update(p_known, "translate_back", turn_correct, 0)
        bkt_confidence = int(round(p_known * 100))

        # Score-driven floor — Own the Word always nets at least N points.
        final_score = int(result.get("final_score", 0))
        days_by_score = {0: 1, 1: 2, 2: 4, 3: 7}
        floor_bumps = {0: 5, 1: 7, 2: 11, 3: 14}
        floor_confidence = min(100, confidence_before + floor_bumps.get(final_score, 7))
        note.confidence = max(bkt_confidence, floor_confidence)

        # Stage gates — UNCHANGED. Spacing-effect protection lives here.
        if note.stage == "inbox":
            note.stage = "learning"
        elif note.confidence >= 85 and note.stage == "learning":
            age_ok = bool(note.created_at) and (_tz.now() - note.created_at >= timedelta(hours=24))
            recent_correct = QuizAttempt.objects.filter(
                user=request.user,
                note=note,
                correct=True,
                created_at__gte=_tz.now() - timedelta(minutes=15),
            ).count()
            if age_ok and recent_correct <= 4:
                note.stage = "mastered"

        # ── Spaced-repetition bridge ──
        # Score-driven next_review interval. The practice queue filters notes
        # by next_review__lte=now(), so this directly governs when the word
        # resurfaces in the regular quiz. (final_score and days_by_score are
        # already declared above — reuse them here.)
        sr_days = days_by_score.get(final_score, 1)
        now = _tz.now()
        note.next_review = now + timedelta(days=sr_days)
        note.last_reviewed_at = now
        note.review_count = (note.review_count or 0) + 1
        note.save()

        # Record one QuizAttempt for the daily-cap counter + history.
        QuizAttempt.objects.create(
            user=request.user,
            note=note,
            quiz_type="own_the_word",
            direction="l2_l1",
            correct=bool(result.get("owned")),
            user_answer=f"{answer_1} || {answer_2}"[:1000],
            chosen_wrong="",
            response_time_ms=0,
        )

        result["confidence_before"] = confidence_before
        result["confidence_after"] = note.confidence
        result["stage"] = note.stage
        result["word"] = note.word
        result["next_review_days"] = sr_days
        result["next_review_iso"] = note.next_review.isoformat()
        return success(result)


class ReadyToWatchPageView(LoginRequiredMixin, View):
    """GET /learning/ready-to-watch/

    Renders the Ready to Watch test page. Pure HTML shell — the JS on
    the page hits ReadyToWatchDataView for scene data after mount.
    """

    def get(self, request):
        return render(request, "learning/ready_to_watch.html", {})


class ReadyToWatchDataView(LoginRequiredMixin, View):
    """GET /learning/ready-to-watch/data/

    Returns the scenes the user can comprehend right now (≥88% coverage).
    Pure read; no side effects.

    Response shape:
        {
            "ok": True,
            "total_ready": int,        # total scenes ≥ threshold for this user
            "scenes": [                # picked subset for this session
                {
                    "id": int,                  # transcript id
                    "episode_title": str,
                    "season": int|None,
                    "episode_number": int|None,
                    "video_url": str,
                    "clip_start": float,        # seconds (incl. small lead-in)
                    "clip_end": float,
                    "transcript_text": str,
                    "total_words": int,
                    "known_words": int,
                    "coverage_percent": int,
                    "known_word_lower": [str],  # for client-side highlight
                },
                ...
            ],
            "next_unlock": {            # only when total_ready == 0
                "words_needed": int,
                "current_coverage": int,
            } | None
        }
    """

    COVERAGE_THRESHOLD = 0.88
    SESSION_SCENES = 5
    LEAD_IN_SECONDS = 0.4
    MIN_TOKENS = 6  # filter out trivial "Oh, yeah." 1-3 word lines

    def get(self, request):
        from clips.models import Transcript
        from learning.models import SceneCoverage
        from learning.services.coverage import COMMON_WORDS, _user_known_words

        user = request.user

        # Ready scenes filter: high coverage AND substantive length.
        # Lines under MIN_TOKENS are technically "100% comprehensible"
        # but emotionally trivial ("Well..." or "Oh, yeah.") — they
        # don't deliver the "I understood real Friends" payoff.
        ready_qs = (
            SceneCoverage.objects.filter(
                user=user,
                coverage__gte=self.COVERAGE_THRESHOLD,
                total_tokens__gte=self.MIN_TOKENS,
            )
            .select_related("transcript", "transcript__episode", "transcript__episode__source")
            .order_by("-coverage", "transcript_id")
        )
        total_ready = ready_qs.count()

        # Sample selection: instead of always the same top-5 (boring),
        # take the first 50 (highest-coverage), shuffle, slice 5. The
        # learner gets a fresh playlist each visit but every scene is
        # still safely ≥88% comprehensible AND meaningfully long.
        import random

        candidates = list(ready_qs[:50])
        random.shuffle(candidates)
        picks = candidates[: self.SESSION_SCENES]

        # Precompute the "known set" once for client-side highlighting.
        known_set = _user_known_words(user) | COMMON_WORDS

        scenes = []
        for sc in picks:
            t = sc.transcript
            ep = t.episode
            video_url = ""
            if ep and getattr(ep, "video_file", None):
                try:
                    video_url = ep.video_file.url
                except Exception:
                    video_url = ""
            if not video_url:
                continue  # can't show a scene without a video

            clip_start = float(t.start_time) - self.LEAD_IN_SECONDS
            if clip_start < 0:
                clip_start = 0.0
            clip_end = float(t.end_time) + 0.2  # small tail so the line finishes

            scenes.append(
                {
                    "id": t.id,
                    "episode_title": getattr(ep, "title", "") or "",
                    "season": getattr(ep, "season_number", None),
                    "episode_number": getattr(ep, "episode_number", None),
                    "video_url": video_url,
                    "clip_start": round(clip_start, 2),
                    "clip_end": round(clip_end, 2),
                    "transcript_text": t.text or "",
                    "total_words": sc.total_tokens,
                    "known_words": sc.known_tokens,
                    "coverage_percent": round(sc.coverage * 100),
                    # Client uses this set to highlight known tokens green.
                    # Lowercased; client tokenizes the same way (regex letters).
                    "known_word_lower": sorted(known_set),
                }
            )

        result = {
            "total_ready": total_ready,
            "scenes": scenes,
            "next_unlock": None,
        }

        # Empty state: tell them how close they are to their first ready scene.
        if total_ready == 0:
            closest = SceneCoverage.objects.filter(user=user).order_by("-coverage").first()
            if closest:
                words_needed = max(1, closest.total_tokens - closest.known_tokens)
                result["next_unlock"] = {
                    "words_needed": min(words_needed, 15),
                    "current_coverage": round(closest.coverage * 100),
                }

        return success(result)


class SceneRecallPageView(LoginRequiredMixin, View):
    """GET /learning/scene-recall/

    Voice-first production drill. Renders the HTML shell — the JS
    fetches today's due-words bundle from SceneRecallDataView.
    """

    def get(self, request):
        return render(request, "learning/scene_recall.html", {})


class SceneRecallDataView(LoginRequiredMixin, View):
    """GET /learning/scene-recall/data/

    Returns the user's words due for production-style review, enriched
    with video playback data. The client plays the scene MUTED, shows
    the Uzbek translation, and the user speaks the English word from
    memory. SR is the engine that decides which words are due — this
    view is a pure read.

    Selection strategy:
      1. Filter WordNotes where next_review <= now AND a transcript
         is attached (no transcript = no scene = nothing to play).
      2. Order by next_review ASC (most overdue first).
      3. Limit to SESSION_SIZE (5 oldest-due, capped to keep first
         experience a confident win).
      4. Skip notes whose enrichment yields no video_url.

    Response shape:
      {
        "ok": true,
        "total_due": int,
        "words": [
          {
            "id": int,
            "word": str,                 // THE ANSWER — client must hide until graded
            "translation": str,          // Uzbek meaning shown to user
            "definition": str,           // English def, shown AFTER answer
            "pos": str,
            "transcript_text": str,
            "episode_title": str,
            "season": int|None,
            "episode_number": int|None,
            "video_url": str,
            "video_start": float,        // clip in (with 1s lead-in)
            "video_end": float,          // clip out
            "slow_start": float,         // tight window around target
            "slow_end": float,
            "scene_frame": str,
            "review_count": int,
            "confidence": int,
          }, ...
        ],
        "next_due_at": str|None          // ISO of next-soonest review if no due now
      }
    """

    SESSION_SIZE = 5

    def get(self, request):
        from vocab.models import WordNote

        user = request.user
        now = timezone.now()

        due_qs = (
            WordNote.objects.filter(user=user, next_review__lte=now, transcript__isnull=False)
            .select_related("transcript", "transcript__episode", "transcript__source")
            .order_by("next_review")
        )
        total_due = due_qs.count()

        words = []
        # Pull a few extra in case some lack video — _enrich_note_with_video
        # handles transcript/episode missing gracefully but returns "" url.
        for note in due_qs[: self.SESSION_SIZE + 5]:
            if len(words) >= self.SESSION_SIZE:
                break
            _enrich_note_with_video(note)
            video_url = getattr(note, "video_url", "") or ""
            if not video_url:
                continue
            ep = note.transcript.episode if note.transcript else None
            words.append(
                {
                    "id": note.id,
                    "word": note.word,
                    "translation": note.translation or "",
                    "definition": note.definition or "",
                    "pos": note.pos or "",
                    "transcript_text": (note.transcript.text or "") if note.transcript else "",
                    "episode_title": getattr(ep, "title", "") if ep else "",
                    "season": getattr(ep, "season_number", None) if ep else None,
                    "episode_number": getattr(ep, "episode_number", None) if ep else None,
                    "video_url": video_url,
                    "video_start": float(getattr(note, "video_start", 0) or 0),
                    "video_end": float(getattr(note, "video_end", 0) or 0),
                    "slow_start": float(getattr(note, "slow_start", 0) or 0),
                    "slow_end": float(getattr(note, "slow_end", 0) or 0),
                    "scene_frame": getattr(note, "scene_frame_url", "") or "",
                    "review_count": note.review_count or 0,
                    "confidence": note.confidence or 0,
                }
            )

        # Empty-state hint: when nothing is due, tell the user when the
        # next-soonest review is so they have something concrete to wait for.
        next_due_at = None
        if total_due == 0:
            soonest = (
                WordNote.objects.filter(user=user, transcript__isnull=False, next_review__gt=now)
                .order_by("next_review")
                .first()
            )
            if soonest and soonest.next_review:
                next_due_at = soonest.next_review.isoformat()

        return success(
            {
                "total_due": total_due,
                "words": words,
                "next_due_at": next_due_at,
            }
        )


class SceneRecallAnswerView(LoginRequiredMixin, View):
    """POST /learning/scene-recall/answer/

    Records the result of a Scene Recall attempt and updates the SR
    schedule via the existing `compute_next_review` engine. Sharing one
    SR engine across drills (quiz + scene recall) keeps `next_review`
    coherent — both features push the same field, the user can't get
    out of sync.

    Request body (JSON):
      {
        "note_id": int,
        "correct": bool,         // user produced the word
        "attempts": int          // 1 = first try (clean win) · 2 = second-try win · 3 = revealed
      }

    Outcomes:
      • attempts=1 + correct → perfect=True (advance the bucket)
      • attempts=2 + correct → perfect=False (slip one back, 1d out)
      • attempts=3 (revealed) → custom 10-minute interval (same field
        but bypassing the scheduler — failed words need to come back
        in MINUTES, not days)

    Returns the new schedule for client display.
    """

    def post(self, request):
        from learning.utils.scheduler import compute_next_review
        from vocab.models import WordNote

        data = json_body(request)
        note_id = data.get("note_id")
        correct = bool(data.get("correct", False))
        attempts = int(data.get("attempts", 1) or 1)

        if not note_id:
            return error("note_id required", 400)

        note = WordNote.objects.filter(id=note_id, user=request.user).first()
        if not note:
            return error("Note not found", 404)

        now = timezone.now()
        note.last_reviewed_at = now

        if attempts >= 3 or not correct:
            # Failed / revealed — short re-expose. NOT routed through the
            # scheduler because its minimum is 1 day; we want 10 minutes
            # for failed-now retention practice (Cepeda's massed-then-
            # spaced effect — repeat in minutes, then space out).
            note.review_count = max(0, (note.review_count or 0) - 1)
            note.confidence = max(0, (note.confidence or 0) - 10)
            note.next_review = now + timedelta(minutes=10)
            interval_label = "10 minutes"
        else:
            # Got it right (in 1 or 2 attempts). attempts=2 = imperfect:
            # slip a bucket so the schedule doesn't drift forward on a
            # near-miss. attempts=1 = perfect: advance.
            perfect = attempts == 1
            new_count, days, next_at = compute_next_review(
                current_count=(note.review_count or 0),
                perfect=perfect,
            )
            note.review_count = new_count
            note.confidence = min(100, (note.confidence or 0) + (15 if perfect else 5))
            note.next_review = next_at
            interval_label = f"{days} day{'s' if days != 1 else ''}"

        note.save(
            update_fields=[
                "review_count",
                "confidence",
                "next_review",
                "last_reviewed_at",
            ]
        )
        return success(
            {
                "note_id": note.id,
                "new_confidence": note.confidence,
                "review_count": note.review_count,
                "next_review": note.next_review.isoformat() if note.next_review else None,
                "interval_label": interval_label,
            }
        )
