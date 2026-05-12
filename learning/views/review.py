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
from vocab.models import LineTranslation, LineVocab, SuggestedWord, VocabMastery, VocabWord, WordNote, WordTranslation


class FavoriteToggleView(LoginRequiredMixin, View):
    def post(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        data = json_body(request)
        favorite = FavoriteQuote.objects.filter(user=request.user, quote=quote).first()

        if favorite:
            favorite.delete()
            SourceProgress.objects.filter(user=request.user, source=quote.source).update(
                quotes_favorited=models.F("quotes_favorited") - 1
            )
            return success(
                {
                    "favorited": False,
                    "total_favorites": FavoriteQuote.objects.filter(quote=quote).count(),
                }
            )
        else:
            favorite = FavoriteQuote.objects.create(
                user=request.user,
                quote=quote,
                emotion_tag=data.get("emotion_tag", ""),
                personal_note=data.get("personal_note", ""),
            )
            QuoteMastery.objects.get_or_create(user=request.user, quote=quote, defaults={"status": "saved"})
            sp, _ = SourceProgress.objects.get_or_create(user=request.user, source=quote.source)
            SourceProgress.objects.filter(pk=sp.pk).update(
                quotes_favorited=models.F("quotes_favorited") + 1,
                last_watched=timezone.now(),
            )
            return success(
                {
                    "favorited": True,
                    "total_favorites": FavoriteQuote.objects.filter(quote=quote).count(),
                    "emotion_tag": favorite.emotion_tag,
                },
                status=201,
            )


class FavoriteListView(LoginRequiredMixin, View):
    def get(self, request):
        # 1. Asosiy favorite'larni olish
        favorites = FavoriteQuote.objects.filter(user=request.user).select_related(
            "quote", "quote__source", "quote__episode"
        )

        # 2. Filtrlar
        source_id = request.GET.get("source_id")
        emotion = request.GET.get("emotion")
        if source_id:
            favorites = favorites.filter(quote__source_id=source_id)
        if emotion:
            favorites = favorites.filter(emotion_tag=emotion)

        # 3. N+1 Muammosini yechish (Prefetching logic)
        # Barcha kerakli Mastery statuslarini bitta so'rovda olamiz
        quote_ids = [f.quote_id for f in favorites]
        masteries = QuoteMastery.objects.filter(user=request.user, quote_id__in=quote_ids).in_bulk(
            field_name="quote_id"
        )

        # 4. Ma'lumotlarni yig'ish
        data = []
        for fav in favorites:
            q = fav.quote
            ep = q.episode

            # Bazaga murojaat qilmasdan xotiradan (dict) olamiz
            mastery = masteries.get(q.id)

            data.append(
                {
                    "id": fav.id,
                    "quote_id": q.id,
                    "text": q.text,
                    "character": getattr(q, "character", ""),
                    "source": q.source.title,
                    "season": ep.season if ep else None,
                    "episode": ep.episode_number if ep else None,
                    "start_time": float(q.start_time),
                    "thumbnail": q.thumbnail.url if q.thumbnail else None,
                    "emotion_tag": fav.emotion_tag,
                    "personal_note": fav.personal_note,
                    "mastery_status": mastery.status if mastery else "saved",
                    "created_at": fav.created_at.isoformat(),
                }
            )

        return success({"favorites": data, "count": len(data)})


class FavoriteUpdateView(LoginRequiredMixin, View):
    def patch(self, request, quote_id):
        favorite = get_object_or_404(FavoriteQuote, user=request.user, quote_id=quote_id)
        data = json_body(request)
        if "emotion_tag" in data:
            favorite.emotion_tag = data["emotion_tag"]
        if "personal_note" in data:
            favorite.personal_note = data["personal_note"]
        favorite.save()
        return success({"emotion_tag": favorite.emotion_tag, "personal_note": favorite.personal_note})


# ─────────────────────────────────────────────
# MASTERY VIEWS
# ─────────────────────────────────────────────

MASTERY_ORDER = ["saved", "learning", "mastered"]
SPACED_REPETITION_INTERVALS = {"saved": 1, "learning": 3, "mastered": 14}


class MasteryUpdateView(LoginRequiredMixin, View):
    """
    Spaced Repetition mantiqi va foydalanuvchi progressini
    atomar tarzda yangilash uchun View.
    """

    def post(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        data = json_body(request)

        # 1. Obyektni olish va "old_status"ni xavfsiz belgilash
        mastery, created = QuoteMastery.objects.get_or_create(
            user=request.user, quote=quote, defaults={"status": "saved"}
        )
        old_status = mastery.status

        # 2. Statusni yangilash logikasi
        if "status" in data:
            new_status = data["status"]
            if new_status not in MASTERY_ORDER:
                return error("Invalid status")
            mastery.status = new_status
        elif data.get("advance"):
            try:
                current_index = MASTERY_ORDER.index(mastery.status)
                if current_index < len(MASTERY_ORDER) - 1:
                    mastery.status = MASTERY_ORDER[current_index + 1]
            except ValueError:
                mastery.status = "learning"  # Fallback
        else:
            return error('Provide "status" or "advance": true')

        # 3. Spaced Repetition parametrlarini hisoblash
        interval = SPACED_REPETITION_INTERVALS.get(mastery.status, 1)
        mastery.interval_days = interval
        mastery.review_count += 1
        mastery.last_reviewed = timezone.now()
        mastery.next_review = timezone.now() + timezone.timedelta(days=interval)

        # 4. LearningProgress counterlarini tayyorlash
        progress, _ = LearningProgress.objects.get_or_create(user=request.user)
        progress.total_quotes_reviewed += 1

        # Faqat status o'zgargandagina counterlarni manipulyatsiya qilamiz
        if not created and old_status != mastery.status:
            count_field_map = {"saved": "saved_count", "learning": "learning_count", "mastered": "mastered_count"}
            old_field = count_field_map.get(old_status)
            new_field = count_field_map.get(mastery.status)

            if old_field:
                setattr(progress, old_field, max(0, getattr(progress, old_field) - 1))
            if new_field:
                setattr(progress, new_field, getattr(progress, new_field) + 1)

        # Agar yangi yaratilgan bo'lsa, default fieldni oshiramiz
        elif created:
            progress.saved_count += 1

        # 5. ATOMIC SAVE (Ma'lumotlar yaxlitligi uchun)
        with transaction.atomic():
            mastery.save()
            progress.save()

        # 6. SourceProgress yangilash (Faqat Mastered bo'lganda)
        if mastery.status == "mastered" and old_status != "mastered":
            SourceProgress.objects.filter(user=request.user, source=quote.source).update(
                quotes_mastered=models.F("quotes_mastered") + 1
            )

        return success(
            {
                "status": mastery.status,
                "review_count": mastery.review_count,
                "interval_days": mastery.interval_days,
                "next_review": mastery.next_review.isoformat(),
            }
        )


class MasteryStatusView(LoginRequiredMixin, View):
    def get(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        mastery = QuoteMastery.objects.filter(user=request.user, quote=quote).first()
        if not mastery:
            return success({"status": None, "review_count": 0})
        return success(
            {
                "status": mastery.status,
                "review_count": mastery.review_count,
                "last_reviewed": mastery.last_reviewed.isoformat() if mastery.last_reviewed else None,
                "next_review": mastery.next_review.isoformat() if mastery.next_review else None,
                "interval_days": mastery.interval_days,
            }
        )


# ─────────────────────────────────────────────
# REVIEW QUEUE VIEW
# ─────────────────────────────────────────────


class ReviewQueueView(LoginRequiredMixin, View):
    def get(self, request):
        now = timezone.now()
        queue = (
            QuoteMastery.objects.filter(user=request.user, next_review__lte=now)
            .select_related("quote", "quote__source", "quote__episode")
            .order_by("next_review")
        )

        data = []
        for mastery in queue:
            q = mastery.quote
            ep = q.episode

            video_url = None
            try:
                if ep and ep.video_file and ep.video_file.name:
                    video_url = ep.video_file.url
                elif q.source and q.source.video_file and q.source.video_file.name:
                    video_url = q.source.video_file.url
            except (ValueError, AttributeError):
                video_url = None

            data.append(
                {
                    "id": mastery.id,
                    "quote_id": q.id,
                    "text": q.text,
                    "video_url": video_url,
                    "start_time": float(q.start_time),
                    "end_time": float(q.end_time) if q.end_time else None,
                    "mastery_status": mastery.status,
                    "review_count": mastery.review_count,
                    "next_review": mastery.next_review.isoformat(),
                }
            )

        return success({"queue": data, "count": len(data)})


# ─────────────────────────────────────────────
# VOCABULARY ONBOARDING VIEWS
# ─────────────────────────────────────────────
