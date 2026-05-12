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
from learning.utils.ai_sentence import check_sentence as ai_check_sentence
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success
from quiz.models import ReviewSession
from vocab.models import LineTranslation, LineVocab, SuggestedWord, VocabMastery, VocabWord, WordNote, WordTranslation


class SessionStartView(LoginRequiredMixin, View):
    """POST /learning/session/start/ — Create a new review/quiz session."""

    def post(self, request):
        data = json_body(request)
        session_type = data.get("session_type", "mixed")
        valid_types = ["cloze", "shadow", "review", "quiz", "mixed"]
        if session_type not in valid_types:
            return error("Invalid session_type")

        session = ReviewSession.objects.create(
            user=request.user,
            session_type=session_type,
        )
        return success({"session_id": session.id}, status=201)


class SessionEndView(LoginRequiredMixin, View):
    """POST /learning/session/end/ — Finalize a session with results."""

    def post(self, request):
        data = json_body(request)
        session_id = data.get("session_id")
        if not session_id:
            return error("session_id required")

        session = get_object_or_404(ReviewSession, id=session_id, user=request.user)
        if session.ended_at:
            return error("Session already ended")

        session.ended_at = timezone.now()
        session.quotes_reviewed = data.get("total", 0)
        session.correct_answers = data.get("correct", 0)
        session.score = data.get("score", 0)
        session.best_combo = data.get("best_combo", 0)
        session.save()

        # Update LearningProgress
        progress, _ = LearningProgress.objects.get_or_create(user=request.user)
        progress.total_cloze_attempts += session.quotes_reviewed
        progress.total_cloze_correct += session.correct_answers
        duration = session.duration_minutes
        progress.total_session_minutes += int(duration)
        progress.save()

        return success(
            {
                "session_id": session.id,
                "accuracy": session.accuracy,
                "duration_minutes": duration,
            }
        )


class ShadowingLogView(LoginRequiredMixin, View):
    """POST /learning/shadow/log/ — Record a shadowing session completion."""

    def post(self, request):
        data = json_body(request)
        transcript_id = data.get("transcript_id")
        if not transcript_id:
            return error("transcript_id required", 400)

        try:
            tr = Transcript.objects.select_related("source", "episode").get(id=transcript_id)
        except Transcript.DoesNotExist:
            return error("Transcript not found", 404)

        phases = int(data.get("phases_completed", 0))
        total_loops = int(data.get("total_loops", 0))
        max_speed = float(data.get("max_speed", 0.5))
        duration_sec = int(data.get("duration_sec", 0))
        reached_no_subs = bool(data.get("reached_no_subs", False))

        ShadowingLog.objects.create(
            user=request.user,
            transcript=tr,
            source=tr.source,
            episode=tr.episode,
            phases_completed=phases,
            total_loops=total_loops,
            max_speed=max_speed,
            reached_no_subs=reached_no_subs,
            duration_sec=duration_sec,
            line_text=tr.text,
        )

        # Update daily activity
        DailyActivity.increment(
            request.user,
            shadow_sessions=1,
            shadow_loops=total_loops,
            shadow_minutes=round(duration_sec / 60, 1),
            total_minutes=round(duration_sec / 60, 1),
        )

        return success({"logged": True})


class PronunciationAssessView(LoginRequiredMixin, View):
    """POST /learning/pronunciation/assess/ — Score a recorded shadow attempt.

    Premium feature (with N free trials). Accepts:
      audio          — recorded audio (multipart file, webm/wav/mp3)
      transcript_id  — the line they were shadowing
      shadow_log_id  — (optional) link to the parent shadow session

    Returns the score breakdown for the front-end to render. Side effects:
      • creates a PronunciationAssessment row
      • updates PhonemeWeakness rolling stats for any weak phonemes
      • bumps user.pronunciation_trials_used (free tier only)
      • returns 402 with upgrade payload if free trials exhausted
    """

    def post(self, request):
        from clips.models import Transcript
        from learning.models import PronunciationAssessment, ShadowingLog
        from learning.utils.pronunciation import assess_pronunciation, update_phoneme_weakness

        user = request.user
        if not user.has_pronunciation:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "trial_exhausted",
                    "message": "You've used your free pronunciation assessments.",
                    "upgrade_url": "/learning/premium/",
                },
                status=402,
            )

        transcript_id = request.POST.get("transcript_id")
        shadow_log_id = request.POST.get("shadow_log_id") or None
        audio = request.FILES.get("audio")
        if not transcript_id or not audio:
            return error("transcript_id and audio required", 400)

        try:
            tr = Transcript.objects.select_related("source", "episode").get(id=transcript_id)
        except Transcript.DoesNotExist:
            return error("Transcript not found", 404)

        reference = (tr.text or "").strip()
        if not reference:
            return error("This line has no reference text to assess against", 400)

        # Save the audio first so we have it on disk for the SDK.
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        ext = (audio.name.rsplit(".", 1)[-1] or "webm").lower()
        save_path = default_storage.save(
            f"pronunciation/tmp/{user.id}_{transcript_id}.{ext}",
            ContentFile(audio.read()),
        )
        audio_full_path = default_storage.path(save_path) if hasattr(default_storage, "path") else save_path

        # Run the assessment.
        result = assess_pronunciation(audio_full_path, reference)

        # Persist.
        shadow_log = None
        if shadow_log_id:
            shadow_log = ShadowingLog.objects.filter(id=shadow_log_id, user=user).first()

        assessment = PronunciationAssessment.objects.create(
            user=user,
            shadow_log=shadow_log,
            transcript=tr,
            audio_file=save_path,
            audio_duration_ms=int(audio.size / 16),  # rough; real value from SDK if needed
            accuracy_score=result.accuracy_score,
            fluency_score=result.fluency_score,
            completeness_score=result.completeness_score,
            prosody_score=result.prosody_score,
            overall_score=result.overall_score,
            word_scores=[
                {"word": w.word, "accuracy": w.accuracy, "error_type": w.error_type} for w in result.word_scores
            ],
            weak_phonemes=result.weak_phonemes,
            raw_response=result.raw_response,
        )

        # Update rolling phoneme weakness tracker.
        update_phoneme_weakness(user, result.weak_phonemes, result.overall_score)

        # Bump free-trial counter (premium = unlimited).
        if not user.is_premium:
            type(user).objects.filter(pk=user.pk).update(
                pronunciation_trials_used=user.pronunciation_trials_used + 1,
            )
            user.pronunciation_trials_used += 1

        return success(
            {
                "id": assessment.id,
                "scores": {
                    "overall": round(result.overall_score, 1),
                    "accuracy": round(result.accuracy_score, 1),
                    "fluency": round(result.fluency_score, 1),
                    "completeness": round(result.completeness_score, 1),
                    "prosody": round(result.prosody_score, 1) if result.prosody_score else None,
                },
                "words": [
                    {"word": w.word, "accuracy": round(w.accuracy, 0), "error_type": w.error_type}
                    for w in result.word_scores
                ],
                "weak_phonemes": result.weak_phonemes,
                "trials_remaining": user.pronunciation_trials_remaining,
                "is_premium": user.is_premium,
            }
        )


class GrammarPracticeLogView(LoginRequiredMixin, View):
    """POST /learning/grammar/log/ — Record grammar unit practice results."""

    def post(self, request):
        data = json_body(request)
        unit_id = data.get("unit_id")
        unit_title = data.get("unit_title", "")
        results = data.get("results", [])

        if not unit_id or not results:
            return error("unit_id and results required", 400)

        logs = []
        correct_count = 0
        for r in results:
            is_correct = bool(r.get("correct", False))
            if is_correct:
                correct_count += 1
            logs.append(
                GrammarPracticeLog(
                    user=request.user,
                    unit_id=int(unit_id),
                    unit_title=unit_title,
                    sentence_index=int(r.get("index", 0)),
                    sentence_text=r.get("sentence", ""),
                    correct=is_correct,
                    attempts_on_sentence=int(r.get("attempts", 1)),
                    response_time_ms=int(r.get("time_ms", 0)),
                )
            )

        GrammarPracticeLog.objects.bulk_create(logs)

        # Update daily activity
        DailyActivity.increment(
            request.user,
            grammar_attempts=len(results),
            grammar_correct=correct_count,
            total_minutes=round(len(results) * 0.5, 1),  # ~30s per sentence estimate
        )

        return success({"logged": len(results)})


class WatchTimeLogView(LoginRequiredMixin, View):
    """POST /learning/watch/log/ — Record cumulative watch minutes for today."""

    def post(self, request):
        data = json_body(request)
        minutes = float(data.get("minutes", 0))
        if minutes <= 0:
            return error("minutes must be positive", 400)

        DailyActivity.increment(
            request.user,
            watch_minutes=round(minutes, 1),
            total_minutes=round(minutes, 1),
        )
        return success({"logged": True})


class TTSView(LoginRequiredMixin, View):
    """GET /learning/tts/?text=hello — Generate TTS audio using Edge TTS, cached to disk."""

    def get(self, request):
        import asyncio
        import hashlib
        from pathlib import Path

        from django.conf import settings
        from django.http import FileResponse

        text = request.GET.get("text", "").strip()
        if not text or len(text) > 200:
            return error("text required (max 200 chars)")

        # Cache directory
        cache_dir = Path(settings.MEDIA_ROOT) / "tts_cache"
        cache_dir.mkdir(exist_ok=True)

        # Hash-based filename for caching
        text_hash = hashlib.md5(text.lower().encode()).hexdigest()
        cache_file = cache_dir / f"{text_hash}.mp3"

        if not cache_file.exists():
            # Generate with edge-tts
            import edge_tts

            voice = "en-US-ChristopherNeural"  # clear male voice

            async def _generate():
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(cache_file))

            asyncio.run(_generate())

        return FileResponse(open(cache_file, "rb"), content_type="audio/mpeg")


class FlashcardLogView(LoginRequiredMixin, View):
    """POST /learning/flashcard/log/ — Record Today's Words flashcard results."""

    def post(self, request):
        from datetime import date as date_mod

        data = json_body(request)
        results = data.get("results", [])
        if not results:
            return error("results required", 400)

        today = date_mod.today()
        logs = []
        known_count = 0
        for r in results:
            knew = bool(r.get("knew_it", False))
            if knew:
                known_count += 1
            note_id = r.get("note_id")
            if not note_id:
                continue
            logs.append(
                FlashcardAttempt(
                    user=request.user,
                    word_note_id=int(note_id),
                    word=r.get("word", ""),
                    knew_it=knew,
                    time_on_card_ms=int(r.get("time_ms", 0)),
                    flips=int(r.get("flips", 1)),
                    batch_date=today,
                )
            )

        if logs:
            FlashcardAttempt.objects.bulk_create(logs)
            total_card_ms = sum(int(r.get("time_ms", 0)) for r in results)
            DailyActivity.increment(
                request.user,
                flashcard_reviews=len(logs),
                flashcard_known=known_count,
                total_minutes=round(max(total_card_ms, len(logs) * 15000) / 60000, 2),
            )

        return success({"logged": len(logs)})


# ─────────────────────────────────────────────────────
# PAGE VISIT LOG — Track page opens & time spent
# ─────────────────────────────────────────────────────


class PageVisitLogView(LoginRequiredMixin, View):
    """POST /learning/page/log/ — Record a page visit."""

    def post(self, request):
        data = json_body(request)
        page = data.get("page", "")
        if not page:
            return error("page required", 400)

        visit = PageVisit.objects.create(
            user=request.user,
            page=page,
            path=data.get("path", ""),
            source_id=data.get("source_id") or None,
            duration_sec=int(data.get("duration_sec", 0)),
            referrer_page=data.get("referrer", ""),
        )
        DailyActivity.increment(request.user, page_visits=1)
        return success({"visit_id": visit.id})


class PageVisitUpdateView(LoginRequiredMixin, View):
    """PATCH /learning/page/log/<id>/ — Update duration when user leaves page."""

    def patch(self, request, visit_id):
        data = json_body(request)
        duration = int(data.get("duration_sec", 0))
        if duration <= 0:
            return error("duration_sec required", 400)

        updated = PageVisit.objects.filter(id=visit_id, user=request.user).update(duration_sec=duration)

        if not updated:
            return error("Visit not found", 404)
        return success({"updated": True})
