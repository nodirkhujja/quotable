"""Notebook views — thin HTTP layer for word-saving, drilling, and companion.

All business logic lives in learning/services/notebook.py.
"""

from __future__ import annotations

import base64
import json
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from clips.models import Quote, Transcript
from learning.models import DailyActivity, LearningProgress, LookupLog
from learning.services.notebook import (
    build_notebook_companion,
    build_word_battle_data,
    build_zpd_config,
    enrich_note_with_video,
    enrich_notes_with_tavsif,
    google_translate,
    group_notes_by_episode,
    humanize_interval,
    load_deep_learn_note,
    normalize_word,
    pull_from_suggested,
)
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success
from vocab.models import SuggestedWord, WordNote


class WordNoteCreateView(LoginRequiredMixin, View):
    """POST /learning/words/quote/<quote_id>/"""

    def post(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        data = json_body(request)

        word = normalize_word(data.get("word", ""))
        definition = data.get("definition", "").strip()
        pos = data.get("pos", "").strip()
        translation = data.get("translation", "").strip()

        if not word:
            return error("Word is required")
        if len(word) > 100:
            return error("Too long — max 100 characters.")
        if WordNote.objects.filter(user=request.user, quote=quote, word=word).exists():
            return error("You already noted this word from this quote", status=409)

        if not definition or not pos:
            res = get_micro_definition(word)
            if not definition:
                definition = res.get("definition", "Definition not found")
            if not pos:
                pos = res.get("pos", "other")

        usage_ex, grammar, sw_translation = pull_from_suggested(
            word,
            episode_id=getattr(quote, "episode_id", None),
            source_id=quote.source_id,
        )
        if not translation and sw_translation:
            translation = sw_translation
        if definition in ("not found", "Definition not found"):
            definition = ""

        try:
            with transaction.atomic():
                note = WordNote.objects.create(
                    user=request.user,
                    quote=quote,
                    word=word,
                    definition=definition,
                    translation=translation,
                    pos=pos,
                    mood=data.get("mood", ""),
                    emotion_vibe=data.get("emotion_vibe", ""),
                    stage=data.get("stage", "inbox"),
                    personal_note=data.get("personal_note", ""),
                    example_usage=data.get("example_usage") or quote.text,
                    context_type=data.get("context_type", "general"),
                    usage_examples=usage_ex,
                    grammar_note=grammar,
                    # SR: schedule first review 30 min out so the word enters
                    # the review cycle immediately rather than sitting at NULL.
                    next_review=timezone.now() + timedelta(minutes=30),
                )
                progress, _ = LearningProgress.objects.get_or_create(user=request.user)
                LearningProgress.objects.filter(pk=progress.pk).update(
                    total_words_noted=models.F("total_words_noted") + 1
                )
                DailyActivity.increment(request.user, words_saved=1)
        except Exception as e:
            return error(f"Database error: {str(e)}", status=500)

        return success(
            {
                "id": note.id,
                "word": note.word,
                "pos": note.pos,
                "definition": note.definition,
                "created_at": note.created_at.isoformat(),
            },
            status=201,
        )


class WordNoteCreateFromTranscriptView(LoginRequiredMixin, View):
    """POST /learning/words/transcript/<transcript_id>/"""

    def post(self, request, transcript_id):
        transcript = get_object_or_404(Transcript, id=transcript_id)
        data = json_body(request)

        word = normalize_word(data.get("word", ""))
        definition = data.get("definition", "").strip()
        pos = data.get("pos", "").strip()
        translation = data.get("translation", "").strip()

        if not word:
            return error("Word is required")
        if len(word) > 100:
            return error("Too long — max 100 characters.")
        if WordNote.objects.filter(user=request.user, transcript=transcript, word=word).exists():
            return error("You already noted this word from this line", status=409)

        if not definition or not pos:
            res = get_micro_definition(word)
            if not definition:
                definition = res.get("definition", "Definition not found")
            if not pos:
                pos = res.get("pos", "other")

        usage_ex, grammar, sw_translation = pull_from_suggested(
            word,
            episode_id=transcript.episode_id,
            source_id=transcript.source_id,
        )
        if not translation and sw_translation:
            translation = sw_translation
        if definition in ("not found", "Definition not found"):
            definition = ""

        scene_timestamp = data.get("scene_timestamp")
        if scene_timestamp is not None:
            try:
                scene_timestamp = round(float(scene_timestamp), 3)
            except (ValueError, TypeError):
                scene_timestamp = None

        scene_frame_file = None
        scene_frame_b64 = data.get("scene_frame", "")
        if scene_frame_b64 and scene_frame_b64.startswith("data:image/"):
            fmt, imgstr = scene_frame_b64.split(";base64,", 1)
            ext = fmt.split("/")[-1][:4]
            scene_frame_file = ContentFile(
                base64.b64decode(imgstr),
                name=f"frame_{request.user.id}_{transcript_id}_{word[:20].replace(' ', '_')}.{ext}",
            )

        try:
            with transaction.atomic():
                note = WordNote.objects.create(
                    user=request.user,
                    transcript=transcript,
                    word=word,
                    definition=definition,
                    translation=translation,
                    pos=pos,
                    mood=data.get("mood", ""),
                    emotion_vibe=data.get("emotion_vibe", ""),
                    stage=data.get("stage", "inbox"),
                    personal_note=data.get("personal_note", ""),
                    example_usage=data.get("example_usage") or transcript.text,
                    context_type=data.get("context_type", "general"),
                    usage_examples=usage_ex,
                    grammar_note=grammar,
                    scene_timestamp=scene_timestamp,
                    scene_frame=scene_frame_file,
                    next_review=timezone.now() + timedelta(minutes=30),
                )
                progress, _ = LearningProgress.objects.get_or_create(user=request.user)
                LearningProgress.objects.filter(pk=progress.pk).update(
                    total_words_noted=models.F("total_words_noted") + 1
                )
                DailyActivity.increment(request.user, words_saved=1)
        except Exception as e:
            return error(f"Database error: {str(e)}", status=500)

        return success(
            {
                "id": note.id,
                "word": note.word,
                "pos": note.pos,
                "definition": note.definition,
                "created_at": note.created_at.isoformat(),
            },
            status=201,
        )


class WordNoteDetailView(LoginRequiredMixin, View):
    """PATCH / DELETE /learning/words/<note_id>/"""

    def patch(self, request, note_id):
        note = get_object_or_404(WordNote, id=note_id, user=request.user)
        data = json_body(request)
        for field in [
            "definition",
            "pos",
            "personal_note",
            "context_type",
            "stage",
            "confidence",
            "mood",
            "emotion_vibe",
        ]:
            if field in data:
                setattr(note, field, data[field])
        note.save()
        return success(
            {
                "id": note.id,
                "word": note.word,
                "stage": note.stage,
                "confidence": note.confidence,
                "mood": note.mood,
                "status": "updated",
            }
        )

    def delete(self, request, note_id):
        note = get_object_or_404(WordNote, id=note_id, user=request.user)
        with transaction.atomic():
            note.delete()
            LearningProgress.objects.filter(user=request.user).update(
                total_words_noted=models.F("total_words_noted") - 1
            )
        return success({"deleted": True})


class WordNoteReviewCompleteView(LoginRequiredMixin, View):
    """POST /learning/words/<note_id>/review-complete/"""

    def post(self, request, note_id):
        from learning.utils.scheduler import compute_next_review

        note = get_object_or_404(WordNote, id=note_id, user=request.user)
        data = json_body(request)
        perfect = bool(data.get("perfect", False))
        new_count, interval_days, next_at = compute_next_review(note.review_count, perfect)
        note.review_count = new_count
        note.last_reviewed_at = timezone.now()
        note.next_review = next_at
        note.save(update_fields=["review_count", "last_reviewed_at", "next_review"])
        return success(
            {
                "interval_days": interval_days,
                "review_count": new_count,
                "next_review": next_at.isoformat(),
                "next_review_human": humanize_interval(interval_days),
            }
        )


class WordBattleDataView(LoginRequiredMixin, View):
    """GET /learning/battle/<note_id>/ — Challenge: stage-aware progressive drill."""

    def get(self, request, note_id):
        note = get_object_or_404(
            WordNote.objects.select_related("transcript", "quote"),
            id=note_id,
            user=request.user,
        )
        return success(build_word_battle_data(note))


class WordNotePageView(LoginRequiredMixin, View):
    """GET /learning/words/ — Word notebook page."""

    def get(self, request):
        words = list(
            WordNote.objects.filter(user=request.user)
            .select_related(
                "quote",
                "quote__source",
                "quote__episode",
                "transcript",
                "transcript__source",
                "transcript__episode",
            )
            .order_by("-created_at")
        )

        for note in words:
            enrich_note_with_video(note)
        enrich_notes_with_tavsif(words)

        now = timezone.now()
        due_count = 0
        for note in words:
            is_due = (note.next_review is None) or (note.next_review <= now)
            note.is_due = is_due
            if not is_due and note.next_review:
                delta = note.next_review - now
                d = max(1, delta.days)
                note.review_label = f"in {d}d" if d < 7 else (f"in {d // 7}w" if d < 30 else f"in {d // 30}mo")
            else:
                note.review_label = ""
            if is_due:
                due_count += 1

        stage_order = ["inbox", "learning", "mastered"]
        stage_labels = {"inbox": "Inbox", "learning": "Learning", "mastered": "Mastered"}
        buckets: dict[str, list] = {s: [] for s in stage_order}
        for note in words:
            buckets.setdefault(note.stage or "inbox", buckets["inbox"]).append(note)
        sections = [
            {"stage": s, "label": stage_labels[s], "count": len(buckets[s]), "words": buckets[s]}
            for s in stage_order
            if buckets[s]
        ]

        episode_groups = group_notes_by_episode(words)
        moods_present = sorted({n.mood for n in words if n.mood})
        zpd_config = build_zpd_config(request.user)

        return render(
            request,
            "learning/word_notebook.html",
            {
                "words": words,
                "sections": sections,
                "episode_groups": episode_groups,
                "moods_present": moods_present,
                "due_count": due_count,
                "zpd_config_json": json.dumps(zpd_config),
            },
        )


class DeepLearnView(LoginRequiredMixin, View):
    """GET /learning/deep/<note_id>/ — Full-page focused study."""

    def get(self, request, note_id):
        note = load_deep_learn_note(request.user, note_id)
        return render(request, "learning/deep_learn.html", {"note": note})


class DeepLearnFragmentView(LoginRequiredMixin, View):
    """GET /learning/deep/<note_id>/fragment/ — Content partial for notebook overlay."""

    def get(self, request, note_id):
        note = load_deep_learn_note(request.user, note_id)
        return render(request, "learning/_deep_learn_content.html", {"note": note})


class DictionaryLookupView(View):
    """GET /learning/dictionary/"""

    def get(self, request):
        word = request.GET.get("word", "").strip()
        if not word:
            return error("Word required")
        result = get_micro_definition(word)
        if request.user.is_authenticated:
            source_id = request.GET.get("source_id") or None
            episode_id = request.GET.get("episode_id") or None
            LookupLog.objects.create(
                user=request.user,
                word=word.lower(),
                lookup_type="dictionary",
                context=request.GET.get("context", ""),
                source_id=source_id,
                episode_id=episode_id,
                result_found=bool(result.get("definition")),
            )
            DailyActivity.increment(request.user, lookups=1)
        return success(result)


@require_GET
def translate_word(request):
    """GET /learning/translate/"""
    word = request.GET.get("word", "").strip()
    context = request.GET.get("context", "").strip()
    source_id = request.GET.get("source_id", "")
    episode_id = request.GET.get("episode_id", "")

    if not word:
        return JsonResponse({"error": "no word"}, status=400)

    clean = word.lower()
    sw_qs = SuggestedWord.objects.filter(word=clean)
    if episode_id:
        sw_qs = sw_qs.filter(episode_id=episode_id)
    elif source_id:
        sw_qs = sw_qs.filter(source_id=source_id)
    curated = sw_qs.first()

    if curated and curated.translation:
        if request.user.is_authenticated:
            LookupLog.objects.create(
                user=request.user,
                word=clean,
                lookup_type="translate",
                context=context,
                source_id=source_id or None,
                episode_id=episode_id or None,
                result_found=True,
            )
            DailyActivity.increment(request.user, lookups=1)
        return JsonResponse(
            {"word": word, "translation": curated.translation, "context_translation": None, "source": "curated"}
        )

    try:
        context_translation = None
        if context and word.lower() in context.lower():
            context_translation = google_translate(context)
        word_translation = google_translate(word)
        if request.user.is_authenticated:
            LookupLog.objects.create(
                user=request.user,
                word=clean,
                lookup_type="translate",
                context=context,
                source_id=source_id or None,
                episode_id=episode_id or None,
                result_found=bool(word_translation),
            )
            DailyActivity.increment(request.user, lookups=1)
        return JsonResponse(
            {
                "word": word,
                "translation": word_translation,
                "context_translation": context_translation,
                "source": "google",
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


class WordNotebookCompanionView(LoginRequiredMixin, View):
    """GET /learning/words/companion/"""

    def get(self, request):
        return success(build_notebook_companion(request.user))


class WordNotebookCompanionTouchView(LoginRequiredMixin, View):
    """POST /learning/words/companion/touch/<note_id>/"""

    def post(self, request, note_id):
        from learning.utils.scheduler import compute_next_review

        note = WordNote.objects.filter(id=note_id, user=request.user).first()
        if not note:
            return error("Note not found", 404)
        now = timezone.now()
        new_count, _days, next_at = compute_next_review(current_count=note.review_count or 0, perfect=True)
        note.review_count = new_count
        note.confidence = min(100, (note.confidence or 0) + 5)
        note.next_review = next_at
        note.last_reviewed_at = now
        note.save(update_fields=["review_count", "confidence", "next_review", "last_reviewed_at"])
        return success({"touched": True, "next_companion": build_notebook_companion(request.user)})


class WatchOverlayView(LoginRequiredMixin, View):
    """GET /learning/watch-overlay/<source_id>/

    Returns all learning-context data for the watch page in one request.
    The watch page JS fetches this after mounting — clips/views.py stays
    clean with zero learning imports.
    """

    def get(self, request, source_id):
        from clips.models import Source
        from vocab.models import CoreWord, LineTranslation, LineVocab, WordTranslation

        source = get_object_or_404(Source, id=source_id)

        saved_words = list(
            WordNote.objects.filter(user=request.user).values(
                "id", "word", "stage", "translation", "pos", "transcript_id"
            )
        )

        core_words = list(CoreWord.objects.values_list("word", flat=True))

        learn_lines: dict = {}
        for lt in LineTranslation.objects.filter(source=source).select_related("transcript"):
            learn_lines[lt.transcript_id] = {
                "english": lt.transcript.text,
                "translation": lt.translation,
                "accents": [],
                "ep_key": f"S{lt.season}E{lt.episode_number}" if lt.season else "movie",
            }
        for lv in LineVocab.objects.filter(source=source).select_related("transcript"):
            tid = lv.transcript_id
            if tid not in learn_lines:
                learn_lines[tid] = {
                    "english": lv.transcript.text,
                    "translation": "",
                    "accents": [],
                    "ep_key": f"S{lv.season}E{lv.episode_number}" if lv.season else "movie",
                }
            learn_lines[tid]["accents"].append(
                {
                    "word": lv.english,
                    "translation": lv.translation,
                    "description": lv.description,
                    "example": lv.example,
                    "pattern": lv.pattern,
                    "kind": lv.kind,
                    "tavsif": lv.tavsif,
                    "level": lv.level,
                }
            )

        word_translations = {
            wt.word: {"translation": wt.translation, "level": wt.level} for wt in WordTranslation.objects.all()
        }

        return JsonResponse(
            {
                "core_words": core_words,
                "saved_words": saved_words,
                "learn_lines": learn_lines,
                "word_translations": word_translations,
            }
        )
