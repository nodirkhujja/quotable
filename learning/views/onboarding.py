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


class OnboardingWelcomeView(LoginRequiredMixin, View):
    """
    GET /learning/onboarding/
    Renders the placement test (2-page: vocab tap-grid -> grammar MCQ).
    If already completed, redirects to the app.
    """

    def get(self, request):
        existing = OnboardingSession.objects.filter(user=request.user, completed_at__isnull=False).first()
        if existing:
            return redirect("clips:home")

        OnboardingSession.objects.get_or_create(user=request.user)

        from learning.placement_test import test_content_for_template

        return render(
            request,
            "learning/onboarding.html",
            {
                "test_content": test_content_for_template(),
            },
        )


class PlacementSubmitView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/placement/
    Scores the 2-page placement test and stores the result.

    Body JSON:
        {
            "easy":    ["hungry", "borrow", ...],
            "medium":  ["awkward", ...],
            "expert":  ["peculiar", ...],
            "phrases": ["p1", "p3", ...],
            "grammar": {"g1": 1, "g2": 1, "g3": 1, "g4": 1}
        }
    """

    def post(self, request):
        from django.utils import timezone

        from learning.placement_test import compute_placement

        data = json_body(request)
        if not isinstance(data, dict):
            return error("Invalid payload")

        easy = data.get("easy") or []
        medium = data.get("medium") or []
        expert = data.get("expert") or []
        phrases = data.get("phrases") or []
        grammar = data.get("grammar") or {}

        if not all(isinstance(x, list) for x in [easy, medium, expert, phrases]):
            return error("Invalid payload shape")
        if not isinstance(grammar, dict):
            return error("Invalid grammar payload")

        # Coerce grammar answer indices to ints
        grammar_answers = {}
        for gid, idx in grammar.items():
            try:
                grammar_answers[str(gid)] = int(idx)
            except (TypeError, ValueError):
                continue

        result = compute_placement(
            checked_easy=[str(w) for w in easy],
            checked_medium=[str(w) for w in medium],
            checked_expert=[str(w) for w in expert],
            checked_phrase_ids=[str(p) for p in phrases],
            grammar_answers=grammar_answers,
        )

        session, _ = OnboardingSession.objects.get_or_create(user=request.user)
        session.completed_at = timezone.now()
        session.words_shown = 47  # 43 vocab + 4 grammar
        session.words_known = (
            result["breakdown"]["easy"]
            + result["breakdown"]["medium"]
            + result["breakdown"]["expert"]
            + result["breakdown"]["phrases"]
            + result["breakdown"]["grammar_correct"]
        )
        session.projected_total = result["total"]
        session.level = result["level"]
        session.tier_breakdown = result["breakdown"]
        session.grammar_breakdown = {
            "correct_ids": result["breakdown"]["grammar_correct_ids"],
            "raw_score": result["grammar_raw"],
        }
        session.phrases_breakdown = {
            "checked_ids": phrases,
        }
        session.save()

        # Sync user's coarse proficiency field for downstream consumers.
        level_map = {
            "beginner": "beginner",
            "intermediate": "intermediate",
            "upper_intermediate": "intermediate",
            "advanced": "advanced",
        }
        user = request.user
        user.proficiency_level = level_map.get(result["level"], "beginner")
        user.save(update_fields=["proficiency_level"])

        return success(
            {
                "level": result["level"],
                "label": result["label"],
                "cefr": result["cefr"],
                "total": result["total"],
                "max": 111,
                "vocab_raw": result["vocab_raw"],
                "grammar_raw": result["grammar_raw"],
            }
        )


class OnboardingDataView(LoginRequiredMixin, View):
    """
    GET /learning/onboarding/data/
    Returns the full vocab word pool as JSON so the client-side adaptive
    algorithm can build and re-order the assessment queue.
    """

    def get(self, request):
        session = get_object_or_404(OnboardingSession, user=request.user)
        words = list(
            VocabWord.objects.all().values(
                "id",
                "word",
                "pos",
                "tier",
                "uzbek_translation",
                "uzbek_synonyms",
                "example_sentence",
                "gif_url",
                "frequency_rank",
            )
        )
        return JsonResponse({"words": words, "session_id": session.id})


class OnboardingProgressView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/progress/
    Saves a single word result mid-session (fire-and-forget; duplicates ignored).
    Body: {session_id, word_id, known, response_time_ms}
    """

    def post(self, request):
        data = json_body(request)
        session = get_object_or_404(OnboardingSession, id=data.get("session_id"), user=request.user)
        word = get_object_or_404(VocabWord, id=data.get("word_id"))
        OnboardingResult.objects.get_or_create(
            session=session,
            word=word,
            defaults={
                "known": bool(data.get("known", False)),
                "response_time_ms": int(data.get("response_time_ms", 0)),
            },
        )
        return success({"ok": True})


class OnboardingCompleteView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/complete/
    Finalises the session: bulk-saves results, computes per-tier accuracy,
    projects vocabulary size, assigns level.
    Body: {session_id, results: [{word_id, known, response_time_ms}, ...]}
    """

    def post(self, request):
        data = json_body(request)
        session = get_object_or_404(OnboardingSession, id=data.get("session_id"), user=request.user)

        # Bulk-save results (skip duplicates)
        for r in data.get("results", []):
            OnboardingResult.objects.get_or_create(
                session=session,
                word_id=r.get("word_id"),
                defaults={
                    "known": bool(r.get("known", False)),
                    "response_time_ms": int(r.get("response_time_ms", 0)),
                },
            )

        # Compute per-tier accuracy
        tier_breakdown = {}
        for r in OnboardingResult.objects.filter(session=session).select_related("word"):
            t = str(r.word.tier)
            if t not in tier_breakdown:
                tier_breakdown[t] = {"shown": 0, "known": 0}
            tier_breakdown[t]["shown"] += 1
            if r.known:
                tier_breakdown[t]["known"] += 1

        # Project vocabulary size (each tier represents 200 words)
        projected_total = 0
        weak_tiers, strong_tiers = [], []
        for tier_str, counts in tier_breakdown.items():
            accuracy = counts["known"] / counts["shown"] if counts["shown"] else 0
            projected_total += round(accuracy * 200)
            t = int(tier_str)
            if accuracy < 0.50:
                weak_tiers.append(t)
            elif accuracy >= 0.75:
                strong_tiers.append(t)

        # Assign level
        if projected_total >= 800:
            level = "advanced"
        elif projected_total >= 600:
            level = "upper_intermediate"
        elif projected_total >= 400:
            level = "intermediate"
        else:
            level = "beginner"

        level_display = {
            "beginner": "Beginner",
            "intermediate": "Intermediate",
            "upper_intermediate": "Upper-Intermediate",
            "advanced": "Advanced",
        }

        total_shown = sum(c["shown"] for c in tier_breakdown.values())
        total_known = sum(c["known"] for c in tier_breakdown.values())

        session.words_shown = total_shown
        session.words_known = total_known
        session.projected_total = projected_total
        session.level = level
        session.tier_breakdown = tier_breakdown
        session.weak_tiers = sorted(weak_tiers)
        session.strong_tiers = sorted(strong_tiers)
        session.completed_at = timezone.now()
        session.save()

        # ── Seed VocabMastery from placement results ──
        # Skip Stage 1 grind for words the user already knew; deep-focus on unknowns.
        try:
            from learning.utils.onboarding_seed import seed_mastery_from_onboarding

            seed_mastery_from_onboarding(request.user)
        except Exception:
            # Seeding is best-effort; never block onboarding completion on it
            pass

        return success(
            {
                "projected_total": projected_total,
                "level": level,
                "level_display": level_display.get(level, level),
                "tier_breakdown": tier_breakdown,
                "weak_tiers": sorted(weak_tiers),
                "strong_tiers": sorted(strong_tiers),
            }
        )


class OnboardingGrammarCompleteView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/grammar-complete/
    Saves grammar assessment results per unit.
    Body: {session_id, grammar_results: {unit_1: {correct, total}, ...}}
    """

    def post(self, request):
        data = json_body(request)
        session = get_object_or_404(OnboardingSession, id=data.get("session_id"), user=request.user)

        grammar_results = data.get("grammar_results", {})

        # Compute overall grammar score
        total_correct = sum(u.get("correct", 0) for u in grammar_results.values())
        total_questions = sum(u.get("total", 0) for u in grammar_results.values())
        overall_pct = (total_correct / total_questions * 100) if total_questions else 0

        # Assign grammar level
        if overall_pct >= 80:
            grammar_level = "advanced"
        elif overall_pct >= 60:
            grammar_level = "upper_intermediate"
        elif overall_pct >= 40:
            grammar_level = "intermediate"
        else:
            grammar_level = "beginner"

        grammar_display = {
            "beginner": "Beginner",
            "intermediate": "Intermediate",
            "upper_intermediate": "Upper-Intermediate",
            "advanced": "Advanced",
        }

        # Also store the detailed question log if provided
        question_log = data.get("question_log", [])

        session.grammar_breakdown = {
            "per_unit": grammar_results,
            "overall_pct": round(overall_pct),
            "total_correct": total_correct,
            "total_questions": total_questions,
            "question_log": question_log,
        }
        session.grammar_level = grammar_level
        session.save(update_fields=["grammar_breakdown", "grammar_level"])

        return success(
            {
                "grammar_breakdown": grammar_results,
                "grammar_level": grammar_level,
                "grammar_level_display": grammar_display.get(grammar_level, grammar_level),
                "grammar_pct": round(overall_pct),
            }
        )


class OnboardingPhrasesCompleteView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/phrases-complete/
    Saves phrasal verb self-assessment results.
    Body: {session_id, unknown_phrases: ["wake up", ...], total_phrases: 100}
    """

    def post(self, request):
        data = json_body(request)
        session = get_object_or_404(OnboardingSession, id=data.get("session_id"), user=request.user)

        unknown = data.get("unknown_phrases", [])
        total = data.get("total_phrases", 100)
        phrases_with_tiers = data.get("phrases_with_tiers", [])
        unknown_set = set(unknown)

        known_count = total - len(unknown)
        pct = round((known_count / total) * 100) if total else 0

        # Recompute tier breakdown server-side from phrases_with_tiers
        tier_breakdown = {}
        for p in phrases_with_tiers:
            t = str(p.get("tier", 1))
            if t not in tier_breakdown:
                tier_breakdown[t] = {"total": 0, "known": 0, "pct": 0}
            tier_breakdown[t]["total"] += 1
            if p["phrase"] not in unknown_set:
                tier_breakdown[t]["known"] += 1
        # Compute pct per tier
        for t, tb in tier_breakdown.items():
            tb["pct"] = round((tb["known"] / tb["total"]) * 100) if tb["total"] else 0

        # Fallback to client-sent breakdown if no phrases_with_tiers
        if not tier_breakdown:
            tier_breakdown = data.get("tier_breakdown", {})

        session.phrases_breakdown = {
            "unknown_phrases": unknown,
            "total": total,
            "known": known_count,
            "pct": pct,
            "tier_breakdown": tier_breakdown,
        }
        session.save(update_fields=["phrases_breakdown"])

        return success(
            {
                "phrases_known": known_count,
                "phrases_total": total,
                "phrases_pct": pct,
                "tier_breakdown": tier_breakdown,
            }
        )


# ─────────────────────────────────────────────
# PROGRESS PAGE
# ─────────────────────────────────────────────


@method_decorator(never_cache, name="dispatch")
class InterestProfileView(LoginRequiredMixin, View):
    """GET /learning/interests/ — user's interest profile form.

    After onboarding, users must pick at least one category and items.
    This powers AI-generated content with personalized context.
    """

    def get(self, request):
        # Prefetch existing interests
        existing = {ui.category: ui.items for ui in UserInterest.objects.filter(user=request.user)}

        # Forever movie — special featured question, one value only
        forever_items = existing.get("forever_movie", [])
        forever_movie = forever_items[0]["label"] if forever_items else ""

        categories = [
            {
                "key": "football",
                "label": "Football",
                "emoji": "⚽",
                "presets": ["Barcelona", "Real Madrid", "Messi", "Ronaldo"],
            },
            {
                "key": "music",
                "label": "Music",
                "emoji": "🎵",
                "presets": ["Pop", "Hip-hop", "Rock", "Classical"],
            },
            {
                "key": "food",
                "label": "Food",
                "emoji": "🍕",
                "presets": ["Pizza", "Sushi", "Burgers", "Pasta"],
            },
            {
                "key": "gaming",
                "label": "Gaming",
                "emoji": "🎮",
                "presets": ["FIFA", "Minecraft", "Call of Duty", "Fortnite"],
            },
            {
                "key": "travel",
                "label": "Travel",
                "emoji": "✈️",
                "presets": ["Paris", "New York", "Tokyo", "Dubai"],
            },
            {
                "key": "tech",
                "label": "Technology",
                "emoji": "💻",
                "presets": ["iPhone", "Tesla", "AI", "Gaming PCs"],
            },
            {
                "key": "books",
                "label": "Books",
                "emoji": "📚",
                "presets": ["Fantasy", "Mystery", "Self-help", "Science"],
            },
            {
                "key": "fashion",
                "label": "Fashion",
                "emoji": "👟",
                "presets": ["Nike", "Adidas", "Streetwear", "Vintage"],
            },
        ]

        # Attach existing selections to each category
        for cat in categories:
            items = existing.get(cat["key"], [])
            cat["selected_labels"] = [i["label"] for i in items]

        has_any = UserInterest.objects.filter(user=request.user).exists()

        return render(
            request,
            "learning/interest_profile.html",
            {
                "categories": categories,
                "has_any": has_any,
                "forever_movie": forever_movie,
            },
        )


class InterestSaveView(LoginRequiredMixin, View):
    """POST /learning/interests/save/ — save user's interest selections.

    Expects JSON:
        {
            "football": ["Barcelona", "Messi", "Lamine Yamal"],
            "music":    ["Pop", "Uzbek folk"],
            ...
        }
    """

    def post(self, request):
        data = json_body(request)
        if not isinstance(data, dict):
            return error("Invalid payload")

        valid_categories = {k for k, _ in UserInterest._meta.get_field("category").choices}

        total_items = 0
        with transaction.atomic():
            # Delete categories the user unselected
            UserInterest.objects.filter(user=request.user).exclude(
                category__in=[c for c in data.keys() if c in valid_categories]
            ).delete()

            for category, labels in data.items():
                if category not in valid_categories:
                    continue
                if not isinstance(labels, list):
                    continue
                # Normalize & dedupe items
                seen = set()
                items = []
                for label in labels:
                    if not isinstance(label, str):
                        continue
                    label = label.strip()
                    if not label or len(label) > 100:
                        continue
                    key = label.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append({"label": label, "source": "custom"})
                    if len(items) >= 20:  # cap per category
                        break

                if not items:
                    # Nothing to save — remove if existed
                    UserInterest.objects.filter(user=request.user, category=category).delete()
                    continue

                UserInterest.objects.update_or_create(
                    user=request.user,
                    category=category,
                    defaults={"items": items},
                )
                total_items += len(items)

        if total_items == 0:
            return error("Please pick at least one interest")

        return success({"ok": True, "total_items": total_items})
