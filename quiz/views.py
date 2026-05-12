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
from quiz.models import ClozeResult, QuizAttempt, ReviewSession, SavedSentence
from vocab.models import LineTranslation, LineVocab, SuggestedWord, VocabMastery, VocabWord, WordNote, WordTranslation


class AICheckSentenceView(LoginRequiredMixin, View):
    """POST /learning/ai/check-sentence/ — Gemini-powered sentence check.

    Request JSON:
        {
            "word": "drift apart",
            "translation": "uzoqlashmoq",
            "sentence": "Me and Rachel drifted apart after college.",
            "example": "She drifted apart from her family." (optional),
            "pattern": "Drift apart" (optional)
        }

    Response JSON:
        {
            "correct": true/false,
            "feedback_uz": "Uzbek feedback",
            "corrected": "Corrected sentence or empty"
        }
    """

    def post(self, request):
        data = json_body(request)
        word = str(data.get("word", "")).strip()
        translation = str(data.get("translation", "")).strip()
        sentence = str(data.get("sentence", "")).strip()
        example = str(data.get("example", "")).strip()
        pattern = str(data.get("pattern", "")).strip()

        if not word:
            return error("Word is required")
        if not sentence:
            return error("Sentence is required")
        if len(sentence) > 500:
            return error("Sentence too long (max 500 chars)")

        # Pull user's interests to personalize AI feedback.
        # Forever movie goes FIRST — it's the strongest emotional anchor.
        forever_label = ""
        interest_labels = []
        for ui in UserInterest.objects.filter(user=request.user):
            if ui.category == "forever_movie":
                if ui.items:
                    forever_label = (ui.items[0].get("label") or "").strip()
                continue
            for item in (ui.items or [])[:5]:  # cap at 5 per category
                label = item.get("label", "").strip()
                if label:
                    interest_labels.append(label)

        parts = []
        if forever_label:
            parts.append(f"forever-rewatch: {forever_label}")
        if interest_labels:
            parts.append(", ".join(interest_labels[:15]))
        user_interests = "; ".join(parts)

        # User's first name (from Google auth) — fallback to username
        user_name = (request.user.first_name or request.user.username or "").strip()

        # User's CEFR-ish level — prefer the rich onboarding level, fallback to user.proficiency_level
        _LEVEL_MAP = {
            "beginner": "A2",
            "elementary": "A2",
            "intermediate": "B1",
            "upper_intermediate": "B2",
            "advanced": "C1",
            "fluent": "C2",
            # CEFR direct
            "a1": "A1",
            "a2": "A2",
            "b1": "B1",
            "b2": "B2",
            "c1": "C1",
            "c2": "C2",
        }
        user_level = ""
        try:
            latest = (
                OnboardingSession.objects.filter(user=request.user, completed_at__isnull=False)
                .order_by("-completed_at")
                .first()
            )
            if latest and latest.level:
                user_level = _LEVEL_MAP.get(latest.level.lower(), "")
        except Exception:
            pass
        if not user_level and getattr(request.user, "proficiency_level", ""):
            user_level = _LEVEL_MAP.get(request.user.proficiency_level.lower(), "")

        # Recent vocab the user has already saved — Gemini can reference these
        saved_words = list(
            WordNote.objects.filter(user=request.user).order_by("-created_at").values_list("word", flat=True)[:12]
        )

        result = ai_check_sentence(
            word,
            translation,
            sentence,
            example,
            pattern,
            user_interests=user_interests,
            user_name=user_name,
            user_level=user_level,
            known_words=saved_words,
        )
        return success(result)


class QuizPageView(LoginRequiredMixin, View):
    """GET /learning/quiz/?source=<id>&episode=<id>&mode=all|saved"""

    def get(self, request):
        import json

        from clips.models import Episode, Source

        source_id = request.GET.get("source")
        episode_id = request.GET.get("episode")
        mode = request.GET.get("mode")  # None = show picker, "all" or "saved" = start quiz

        source = get_object_or_404(Source, id=source_id) if source_id else Source.objects.first()
        episode = Episode.objects.filter(id=episode_id).first() if episode_id else None

        # Counts for the picker screen
        all_vocab_count = LineVocab.objects.filter(source=source).count() if source else 0
        saved_vocab_count = WordNote.objects.filter(user=request.user).exclude(translation="", definition="").count()

        if not mode:
            # Auto-start: saved words first, fall back to all vocab
            from urllib.parse import urlencode

            from django.shortcuts import redirect as _redirect

            params = {}
            if source_id:
                params["source"] = source_id
            if episode_id:
                params["episode"] = episode_id
            # Preserve ?dev=results across the auto-redirect so the JS dev
            # preview hook fires after the redirect lands. Without this the
            # dev param is silently dropped and the user sees the normal quiz.
            if request.GET.get("dev"):
                params["dev"] = request.GET["dev"]
            params["mode"] = "saved" if saved_vocab_count > 0 else "all"
            return _redirect(f"{request.path}?{urlencode(params)}")

        from learning.utils.quiz_engine import get_quiz_queue

        focus = request.GET.get("focus")  # recognition, context, production

        # Session quiz — words saved during learn session
        session_notes_param = request.GET.get("session_notes", "")
        session_note_ids = (
            [int(i) for i in session_notes_param.split(",") if i.strip().isdigit()] if session_notes_param else None
        )
        # fallback: word strings if no note IDs
        session_words_param = request.GET.get("session_words", "")
        session_words = (
            [w.strip() for w in session_words_param.split(",") if w.strip()] if session_words_param else None
        )

        if session_note_ids or session_words:
            queue = get_quiz_queue(
                request.user,
                source,
                mode="session",
                note_ids=session_note_ids,
                words=session_words,
            )
            total_vocab = len(session_note_ids or session_words or [])
        elif mode == "rescue":
            # Rescue mode — drill at-risk words across ALL sources
            queue = get_quiz_queue(request.user, None, batch_size=15, mode="rescue")
            total_vocab = len(queue)
        elif mode == "saved":
            mood = (request.GET.get("mood") or "").strip()
            queue = get_quiz_queue(
                request.user,
                source,
                batch_size=15,
                mode="saved",
                focus=focus,
                mood=mood or None,
            )
            total_vocab = saved_vocab_count
        elif source:
            queue = get_quiz_queue(request.user, source, episode=episode, batch_size=15, mode="all", focus=focus)
            total_vocab = all_vocab_count
        else:
            queue = []
            total_vocab = 0

        # ── Debug-only: force a specific quiz_type so a single feature can be
        # tested without grinding to the right confidence band. Currently only
        # supports continue_video (the new cinematic cloze) since that's the
        # type we actively iterate on. Items without the data needed for the
        # forced type are dropped from the queue.
        force = request.GET.get("force", "")
        if force == "continue_video":
            from learning.utils.quiz_engine import _make_continue_video

            forced = []
            for item in queue:
                payload = _make_continue_video(
                    item.get("transcript_text") or "",
                    item.get("english") or "",
                    item.get("clip_start"),
                    item.get("clip_end"),
                    item.get("video_url") or "",
                )
                if not payload:
                    continue
                # Strip prior payloads + apply the forced shape.
                item["quiz_type"] = "continue_video"
                item["continue_video"] = payload
                forced.append(item)
            queue = forced

        return render(
            request,
            "learning/quiz.html",
            {
                "queue_json": json.dumps(queue),
                "source": source,
                "episode": episode,
                "mode": mode,
                "total_vocab": total_vocab,
                "all_vocab_count": all_vocab_count,
                "saved_vocab_count": saved_vocab_count,
                "show_picker": False,
                "connector_mode": False,
            },
        )


class QuizSubmitView(LoginRequiredMixin, View):
    """POST /learning/quiz/submit/ — Record a quiz answer."""

    def post(self, request):
        data = json_body(request)
        vocab_id = data.get("vocab_id")
        note_id = data.get("note_id")
        quiz_type = data.get("quiz_type")
        correct = data.get("correct", False)
        user_answer = data.get("user_answer", "")
        chosen_wrong = data.get("chosen_wrong", "")
        response_time_ms = data.get("response_time_ms", 0)
        direction = data.get("direction", "l2_l1")
        bonus_multiplier = float(data.get("bonus_multiplier", 1.0) or 1.0)
        # Clamp to prevent client exploits (max 3.0 = legendary)
        bonus_multiplier = max(1.0, min(3.0, bonus_multiplier))
        used_hint = bool(data.get("used_hint", False))
        skipped = bool(data.get("skipped", False))

        if not vocab_id and not note_id:
            return error("vocab_id or note_id required")
        if not quiz_type:
            return error("quiz_type required")

        from learning.utils.quiz_engine import submit_answer

        result = submit_answer(
            user=request.user,
            vocab_id=vocab_id,
            note_id=note_id,
            quiz_type=quiz_type,
            correct=correct,
            user_answer=user_answer,
            chosen_wrong=chosen_wrong,
            response_time_ms=response_time_ms,
            direction=direction,
            bonus_multiplier=bonus_multiplier,
            used_hint=used_hint,
            skipped=skipped,
        )
        # Append note's latest stage/confidence so the client can update the
        # card DOM in-place (avoids a full page reload after Challenge finish).
        if note_id and isinstance(result, dict) and "note_stage" not in result:
            note_obj = WordNote.objects.filter(id=note_id, user=request.user).only("stage", "confidence").first()
            if note_obj:
                result["note_stage"] = note_obj.stage
                result["note_confidence"] = note_obj.confidence
        return success(result)


class QuizFreeProductionGradeView(LoginRequiredMixin, View):
    """POST /learning/quiz/free-production/grade/

    Grades a learner's 2-3 sentence self-generated production of a target
    word. Returns score (0-3), correct (score>=2), Uzbek feedback, and the
    five boolean axes the AI evaluated.

    Decoupled from /quiz/submit/ on purpose: the frontend gets the AI
    response (1-3s call), shows feedback, then submits via the regular
    /quiz/submit/ with `correct` resolved from this score. Single AI grading
    path; mastery update goes through the same code path as every other
    quiz_type.
    """

    def post(self, request):
        limited, _remaining, retry_after = _rate_limited(request.user.id, "free_production_grade")
        if limited:
            return _rate_limit_response("free_production_grade", retry_after)

        data = json_body(request)
        vocab_id = data.get("vocab_id")
        note_id = data.get("note_id")
        user_answer = data.get("user_answer", "") or ""

        if not vocab_id and not note_id:
            return error("vocab_id or note_id required")

        # Resolve word + translation + kind/category from the source row.
        word = ""
        translation = ""
        kind = ""
        category = ""
        if note_id:
            note = (
                WordNote.objects.filter(id=note_id, user=request.user)
                .only("word", "translation", "definition", "transcript_id")
                .first()
            )
            if not note:
                return error("Word not found", 404)
            word = note.word
            translation = note.translation or note.definition or ""
            # Pull kind/category from originating LineVocab if present.
            from vocab.models import LineVocab

            if note.transcript_id:
                lv = (
                    LineVocab.objects.filter(
                        transcript_id=note.transcript_id,
                        english__iexact=note.word,
                    )
                    .only("kind", "category")
                    .first()
                )
                if lv:
                    kind = lv.kind or ""
                    category = lv.category or ""
        else:
            from vocab.models import LineVocab

            vocab = LineVocab.objects.filter(id=vocab_id).only("english", "translation", "kind", "category").first()
            if not vocab:
                return error("Vocab not found", 404)
            word = vocab.english
            translation = vocab.translation
            kind = vocab.kind or ""
            category = vocab.category or ""

        _interests, _name, user_level = _build_learner_context(request.user)

        try:
            from learning.utils.ai_quiz import grade_free_production
        except ImportError:
            return error("AI module unavailable", 500)

        result = grade_free_production(
            word=word,
            translation=translation,
            user_answer=user_answer,
            user_level=user_level,
            kind=kind,
            category=category,
        )
        return success(result)


class QuizNextBatchView(LoginRequiredMixin, View):
    """GET /learning/quiz/next/?source=<id>&episode=<id> — Get next batch of quiz items."""

    def get(self, request):
        from clips.models import Episode, Source

        source_id = request.GET.get("source")
        episode_id = request.GET.get("episode")

        source = get_object_or_404(Source, id=source_id) if source_id else Source.objects.first()
        episode = Episode.objects.filter(id=episode_id).first() if episode_id else None

        from learning.utils.quiz_engine import get_quiz_queue

        focus = request.GET.get("focus")
        queue = get_quiz_queue(request.user, source, episode=episode, batch_size=15, focus=focus)
        return success({"queue": queue})


class QuizSceneView(LoginRequiredMixin, View):
    """GET /learning/quiz/scene/

    Returns ONE Contextual Production Challenge built around the learner's
    most-due saved word. This is the "first question" experiment — a single
    AI-generated scene + target word + memory anchor. Frontend overlays this
    on top of the regular quiz on page load.
    """

    def get(self, request):
        limited, _remaining, retry_after = _rate_limited(request.user.id, "quiz_scene")
        if limited:
            return _rate_limit_response("quiz_scene", retry_after)

        # Pick the most-due saved word with non-empty translation. Fall back
        # to any saved word if nothing is due. We prefer learning-stage words
        # with attached quote/transcript so the memory anchor has real content.
        from django.db.models import Case, F, Q, Value, When
        from django.utils import timezone

        now = timezone.now()
        notes_qs = (
            WordNote.objects.filter(user=request.user)
            .exclude(translation="", definition="")
            .exclude(word="")
            .annotate(
                # Order: due now first, then by stage (learning > inbox > mastered)
                due_rank=Case(
                    When(next_review__lte=now, then=Value(0)),
                    default=Value(1),
                ),
                stage_rank=Case(
                    When(stage="learning", then=Value(0)),
                    When(stage="inbox", then=Value(1)),
                    When(stage="mastered", then=Value(2)),
                    default=Value(3),
                ),
            )
            .order_by("due_rank", "stage_rank", F("next_review").asc(nulls_last=True), "-created_at")
        )
        note = notes_qs.first()
        if not note:
            return error("No saved words yet — save a few words first to unlock the scene quiz.", 404)

        # Memory anchor: prefer the original quote line, then the transcript line.
        source_line_en = ""
        source_label = ""
        if note.quote_id and getattr(note.quote, "line", None):
            source_line_en = (note.quote.line or "").strip()
            try:
                source_label = note.quote.source.title if note.quote.source_id else ""
            except Exception:
                source_label = ""
        elif note.transcript_id and getattr(note.transcript, "line", None):
            source_line_en = (note.transcript.line or "").strip()
            try:
                source_label = note.transcript.source.title if note.transcript.source_id else ""
            except Exception:
                source_label = ""

        interest_pool, user_name, user_level = _build_learner_context(request.user)
        interest = _pick_interests(interest_pool, [])

        try:
            from learning.utils.ai_quiz import generate_scene_for_word
        except ImportError:
            return error("AI module unavailable", 500)

        scene = generate_scene_for_word(
            word=note.word,
            word_translation_uz=note.translation,
            pos=note.pos,
            source_line_en=source_line_en,
            source_label=source_label,
            interest=interest,
            user_name=user_name,
            user_level=user_level,
        )
        if not scene:
            return error("Couldn't generate a scene right now — AI is busy. Try again in a moment.", 503)

        # Memory-card payload — re-activates the original episodic memory.
        # The frontend renders these in a compact card above the chat bubble:
        # thumbnail (if scene_frame exists) + the exact saved line + source
        # label + tap-to-listen button. Empty strings mean "skip that piece".
        scene_frame_url = ""
        try:
            if note.scene_frame and note.scene_frame.name:
                scene_frame_url = note.scene_frame.url
        except Exception:
            scene_frame_url = ""

        # Personal recall cue — the most recent sentence the learner wrote
        # using THIS word in a past quiz session. Surfacing their own past
        # production is the strongest recall cue we can give them
        # (Testing Effect + self-reference + recency). Cleanly empty if
        # they haven't done a "Now you" turn for this word yet.
        personal_recall_cue = ""
        try:
            personal_examples = [
                e for e in (note.usage_examples or []) if isinstance(e, dict) and e.get("personal") and e.get("en")
            ]
            if personal_examples:
                # Most recent first — entries are appended in order in the
                # personal-sentence endpoint.
                personal_recall_cue = str(personal_examples[-1].get("en") or "").strip()
        except Exception:
            personal_recall_cue = ""

        scene["note_id"] = note.id
        scene["original_line"] = source_line_en
        scene["source_label"] = source_label
        scene["scene_frame_url"] = scene_frame_url
        scene["personal_recall_cue"] = personal_recall_cue
        return success(scene)


class QuizSceneCheckView(LoginRequiredMixin, View):
    """POST /learning/quiz/scene/check/

    Grades a learner's free-form reply to a Contextual Production Challenge
    on four axes (used / form / fits / natural). Accepts an optional list of
    SpeechRecognition alternatives so a misheard primary doesn't tank the
    grade — same pattern as grammar voice checks.
    """

    def post(self, request):
        limited, _remaining, retry_after = _rate_limited(request.user.id, "quiz_scene_check")
        if limited:
            return _rate_limit_response("quiz_scene_check", retry_after)

        data = json_body(request)
        word = str(data.get("word") or "").strip()
        scene_uz = str(data.get("scene_uz") or "").strip()
        sample_en = str(data.get("sample_en") or "").strip()
        reply = str(data.get("reply") or "").strip()

        raw_alts = data.get("alternatives") or []
        if not isinstance(raw_alts, list):
            raw_alts = []
        alternatives = [str(a).strip() for a in raw_alts if isinstance(a, str) and a.strip() and len(a) <= 500][:3]

        if not word or not scene_uz or not reply:
            return error("Missing word / scene_uz / reply", 400)
        if len(reply) > 500:
            return error("Reply too long", 400)

        _interests, _name, user_level = _build_learner_context(request.user)

        try:
            from learning.utils.ai_quiz import grade_scene_reply
        except ImportError:
            return error("AI module unavailable", 500)

        result = grade_scene_reply(
            word=word,
            scene_uz=scene_uz,
            sample_en=sample_en,
            learner_reply=reply,
            alternatives=alternatives,
            user_level=user_level,
        )
        return success(result)


class QuizPersonalSentenceView(LoginRequiredMixin, View):
    """POST /learning/quiz/scene/personal/

    Accepts a learner's self-generated sentence about their own life using
    the target word, lightly grades it, and (on accept) saves it to
    WordNote.usage_examples as a personal example.

    This is the Generation Effect turn — the learner generating their own
    context is the missing 50% of vocab production. The saved sentences
    accumulate into a personal corpus that's surfaced as a recall cue on
    future encounters with the same word.
    """

    def post(self, request):
        # Reuse the scene_check rate limit — same general AI cost class.
        limited, _remaining, retry_after = _rate_limited(request.user.id, "quiz_scene_check")
        if limited:
            return _rate_limit_response("quiz_scene_check", retry_after)

        data = json_body(request)
        note_id = data.get("note_id")
        word = str(data.get("word") or "").strip()
        sentence = str(data.get("sentence") or "").strip()

        raw_alts = data.get("alternatives") or []
        if not isinstance(raw_alts, list):
            raw_alts = []
        alternatives = [str(a).strip() for a in raw_alts if isinstance(a, str) and a.strip() and len(a) <= 500][:3]

        if not word or not sentence:
            return error("Missing word / sentence", 400)
        if len(sentence) > 500:
            return error("Sentence too long", 400)

        _interests, _name, user_level = _build_learner_context(request.user)

        try:
            from learning.utils.ai_quiz import grade_personal_sentence
        except ImportError:
            return error("AI module unavailable", 500)

        result = grade_personal_sentence(
            word=word,
            sentence=sentence,
            alternatives=alternatives,
            user_level=user_level,
        )

        # On accept, append to the user's WordNote.usage_examples. Best-effort
        # — if the note can't be located, still return the AI feedback so the
        # learner sees the warm response. Cap stored personal examples at 8
        # per word so the corpus stays a recall set, not a journal.
        saved = False
        if result.get("accepted") and note_id:
            try:
                note = WordNote.objects.filter(user=request.user, id=int(note_id)).first()
                if note and note.word.strip().lower() == word.lower():
                    examples = list(note.usage_examples or [])
                    # Avoid exact duplicates so spam-clicking doesn't bloat the list
                    new_text = result.get("best_text") or sentence
                    if not any(
                        isinstance(e, dict)
                        and e.get("personal")
                        and e.get("en", "").strip().lower() == new_text.lower()
                        for e in examples
                    ):
                        examples.append(
                            {
                                "en": new_text,
                                "personal": True,
                                "saved_at": timezone.now().isoformat(),
                            }
                        )
                        # Keep the most recent 8 personal entries; archive older ones
                        personal = [e for e in examples if isinstance(e, dict) and e.get("personal")]
                        non_personal = [e for e in examples if not (isinstance(e, dict) and e.get("personal"))]
                        if len(personal) > 8:
                            personal = personal[-8:]
                        note.usage_examples = non_personal + personal
                        note.save(update_fields=["usage_examples"])
                    saved = True
            except (ValueError, TypeError):
                saved = False

        result["saved"] = saved
        return success(result)


class WordBridgeGenerateView(LoginRequiredMixin, View):
    """POST /learning/quiz/word-bridge/generate/

    Generates an Uzbek sentence for a mastered word that the learner must
    translate to English using that exact word. Mirrors grammar's bridge
    pattern but operates on a specific WordNote rather than a grammar unit.

    Request JSON:
        {"note_id": int}  — the WordNote whose word we're generating for

    Response:
        {
            "uzbek": "...",
            "sample_en": "...",        # not displayed to user; used for grading
            "focus": "...",            # short label of required form
            "word": "...",             # echoed for client convenience
            "translation": "...",      # echoed for client convenience
            "note_id": int,
        }
    """

    def post(self, request):
        from learning.utils.ai_word_bridge import generate_word_bridge
        from vocab.models import WordNote

        data = json_body(request)
        note_id = data.get("note_id")
        if not note_id:
            return error("note_id required", 400)

        note = WordNote.objects.filter(id=note_id, user=request.user).first()
        if not note:
            return error("Note not found", 404)
        if not note.translation:
            return error("Word has no translation", 400)

        limited, _remaining, retry_after = _rate_limited(request.user.id, "word_bridge_generate")
        if limited:
            return _rate_limit_response("word_bridge_generate", retry_after)

        interest_pool, user_name, user_level = _build_learner_context(request.user)

        # Build the CATEGORIZED interest map. A flat pool biases toward the
        # category with the most items — if the user has 5 football items
        # but only 1 cs2 item, the AI picks football 5/6 of the time.
        # Categorized framing forces the AI to pick a CATEGORY first
        # (uniform-random across categories), then an element within.
        # Result: balanced rotation across football/gaming/music/etc.
        from learning.models import UserInterest

        interests_by_category = {}
        for ui in UserInterest.objects.filter(user=request.user):
            # forever_movie has its own dedicated handling elsewhere; skip it
            # here so it doesn't pollute the category-rotation logic.
            if ui.category == "forever_movie":
                continue
            items = [(it.get("label") or "").strip() for it in (ui.items or [])[:5]]  # cap per category
            items = [x for x in items if x]
            if items:
                interests_by_category[ui.category] = items

        # Grammar progress: SERVER-SIDE source of truth. `GrammarPracticeLog`
        # records every sentence-build attempt the learner makes on a
        # grammar unit. We treat a unit as "practiced" if the learner has
        # ≥5 attempts on it — low bar, since even partial engagement
        # exposes them to that grammar pattern. The previous version of
        # this code trusted the client's localStorage, which was easy to
        # spoof and drifts when the user wipes browser data.
        from django.db.models import Count

        from learning.models import GrammarPracticeLog

        _MIN_ATTEMPTS = 5  # below this, the unit hasn't been seriously practiced
        practiced_unit_ids = set(
            GrammarPracticeLog.objects.filter(user=request.user)
            .values("unit_id")
            .annotate(n=Count("id"))
            .filter(n__gte=_MIN_ATTEMPTS)
            .values_list("unit_id", flat=True)
        )
        unit_titles_map = GrammarExplainView._UNIT_TITLES
        completed_grammar = [title for uid, title in unit_titles_map.items() if uid in practiced_unit_ids]
        not_yet_grammar = [title for uid, title in unit_titles_map.items() if uid not in practiced_unit_ids]

        # ── Variant rotation ───────────────────────────────────────
        # Cache by (word, level, interest_set, week, variant_idx) where
        # variant_idx = (prior bridge attempts on this word) mod 3.
        # Result: each user sees up to 3 distinct prompts per word per
        # week, then cycles. Bounded cost (3 Gemini calls per word per
        # week max), real variety (no immediate repeats).
        from quiz.models import QuizAttempt

        _VARIANT_POOL_SIZE = 3
        prior_attempts = QuizAttempt.objects.filter(
            user=request.user,
            note_id=note.id,
            quiz_type="mastered_bridge",
        ).count()
        variant_idx = prior_attempts % _VARIANT_POOL_SIZE

        # Bridge skill — count of CORRECT bridge attempts across ALL words.
        # Drives the LENGTH MATH in the prompt: starter (0-2 correct) sees
        # 5-7 word sentences, master (16+) sees 12-16 word sentences. Length
        # grows with production skill, not grammar coverage.
        bridge_success_count = QuizAttempt.objects.filter(
            user=request.user,
            quiz_type="mastered_bridge",
            correct=True,
        ).count()

        result = generate_word_bridge(
            word=note.word,
            translation=note.translation,
            user_level=user_level,
            interests_by_category=interests_by_category,  # category-first picking
            user_name=user_name,
            definition=note.definition or "",
            completed_grammar=completed_grammar,
            not_yet_grammar=not_yet_grammar,
            variant_idx=variant_idx,
            bridge_success_count=bridge_success_count,
        )
        if not result:
            return error("AI generation failed; try again in a moment", 503)

        return success(
            {
                **result,
                "word": note.word,
                "translation": note.translation,
                "note_id": note.id,
            }
        )


class WordBridgeCheckView(LoginRequiredMixin, View):
    """POST /learning/quiz/word-bridge/check/

    Grades the learner's English translation of an Uzbek prompt. Returns
    a dev-style {status / review_uz / fix_en} payload — same shape as
    grammar's check-bridge.

    Request JSON:
        {
            "note_id": int,
            "uzbek": "...",
            "sample_en": "...",     # reference, optional
            "user_english": "...",
        }
    """

    def post(self, request):
        from learning.utils.ai_word_bridge import check_word_bridge
        from vocab.models import WordNote

        data = json_body(request)
        note_id = data.get("note_id")
        uzbek = str(data.get("uzbek") or "").strip()
        sample_en = str(data.get("sample_en") or "").strip()
        user_english = str(data.get("user_english") or "").strip()

        if not note_id or not uzbek or not user_english:
            return error("note_id, uzbek, user_english required", 400)
        if len(user_english) > 500:
            return error("Answer too long", 400)

        note = WordNote.objects.filter(id=note_id, user=request.user).first()
        if not note:
            return error("Note not found", 404)

        limited, _remaining, retry_after = _rate_limited(request.user.id, "word_bridge_check")
        if limited:
            return _rate_limit_response("word_bridge_check", retry_after)

        _interests, user_name, user_level = _build_learner_context(request.user)

        result = check_word_bridge(
            word=note.word,
            translation=note.translation or "",
            uzbek_prompt=uzbek,
            user_english=user_english,
            sample_en=sample_en,
            user_level=user_level,
            user_name=user_name,
        )

        # Save to the sentence library — only on `accepted` (clean
        # production), not `close` (right meaning + grammar slip). The
        # library is "things I said in English correctly" — slips don't
        # belong. Cap user input length already enforced above (≤500).
        if result.get("correct") and (result.get("status_label") or "").startswith("✓"):
            try:
                from quiz.models import SavedSentence

                SavedSentence.objects.create(
                    user=request.user,
                    word=note.word,
                    note=note,
                    uzbek_prompt=uzbek[:1000],
                    english=user_english[:1000],
                    quiz_type="mastered_bridge",
                )
            except Exception:
                # Never let library save failure break the grading reply.
                pass

        return success(result)


class QuizSummaryView(LoginRequiredMixin, View):
    """GET /learning/quiz/summary/

    Returns the continuity-layer payload for the end-of-quiz "teacher
    voice" header. Surfaces facts the learner doesn't otherwise see:
    today's production count, tomorrow's review queue, the streak.

    No write side-effects — pure read.

    Response shape:
        {
            "streak_days": int,                  # current daily streak
            "longest_streak": int,
            "sentences_today": int,              # bridge sentences saved TODAY
            "sentences_lifetime": int,           # all-time, for the artifact archive
            "due_today": int,                    # WordNotes with next_review ≤ now
            "due_tomorrow": int,                 # WordNotes with next_review on tomorrow's date
            "mastered_lifetime": int,            # WordNotes with stage='mastered'
            "last_sentence": "..." | None,       # the most recent saved sentence — surface as proof
            "last_sentence_word": "..." | None,  # word for the most recent
        }
    """

    def get(self, request):
        from datetime import timedelta

        from django.utils import timezone

        from quiz.models import SavedSentence
        from vocab.models import WordNote

        u = request.user
        today = timezone.localdate()
        tomorrow = today + timedelta(days=1)
        now = timezone.now()
        tomorrow_end = (
            timezone.make_aware(timezone.datetime.combine(tomorrow, timezone.datetime.max.time()))
            if timezone.is_naive(timezone.now())
            else (now.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=1))
        )

        # Sentence library counts
        sentences_today = SavedSentence.objects.filter(
            user=u,
            created_at__date=today,
        ).count()
        sentences_lifetime = SavedSentence.objects.filter(user=u).count()

        # Most recent saved sentence — proof artifact for the header.
        last = SavedSentence.objects.filter(user=u).order_by("-created_at").first()

        # Review queue counts — words due now or by tomorrow.
        due_today = WordNote.objects.filter(
            user=u,
            next_review__isnull=False,
            next_review__lte=now,
        ).count()

        # Tomorrow's window: from end-of-today to end-of-tomorrow.
        end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=0)
        end_of_tomorrow = end_of_today + timedelta(days=1)
        due_tomorrow = WordNote.objects.filter(
            user=u,
            next_review__isnull=False,
            next_review__gt=end_of_today,
            next_review__lte=end_of_tomorrow,
        ).count()

        mastered_lifetime = WordNote.objects.filter(user=u, stage="mastered").count()

        return success(
            {
                "streak_days": u.streak_days or 0,
                "longest_streak": u.longest_streak or 0,
                "sentences_today": sentences_today,
                "sentences_lifetime": sentences_lifetime,
                "due_today": due_today,
                "due_tomorrow": due_tomorrow,
                "mastered_lifetime": mastered_lifetime,
                "last_sentence": last.english if last else None,
                "last_sentence_word": last.word if last else None,
            }
        )


class ConnectorQuizView(LoginRequiredMixin, View):
    """GET /learning/quiz/connectors/ — Sentence builder quiz for connector words."""

    def get(self, request):
        return render(request, "learning/connector_quiz.html")


# ─────────────────────────────────────────────────────
# FLASHCARD LOG — Today's Words study tracking
# ─────────────────────────────────────────────────────
