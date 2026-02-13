import json

from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from clips.models import Quote

from .models import FavoriteQuote, LearningProgress, QuoteMastery, SourceProgress, WordNote

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────


def json_body(request):
    """Safely parse JSON request body"""
    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return {}


def error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def success(data=None, status=200):
    return JsonResponse({"ok": True, **(data or {})}, status=status)


# ─────────────────────────────────────────────
# FAVORITE TOGGLE
# ─────────────────────────────────────────────


@login_required
@require_http_methods(["POST"])
def favorite_toggle(request, quote_id):
    """
    Toggle favorite on a quote.
    POST /learning/favorites/<quote_id>/toggle/
    Optional body: { "emotion_tag": "funny", "personal_note": "..." }
    Returns: { ok, favorited, total_favorites }
    """
    quote = get_object_or_404(Quote, id=quote_id)
    data = json_body(request)

    favorite = FavoriteQuote.objects.filter(user=request.user, quote=quote).first()

    if favorite:
        # Unfavorite
        favorite.delete()

        # Update source progress
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
        # Favorite
        favorite = FavoriteQuote.objects.create(
            user=request.user,
            quote=quote,
            emotion_tag=data.get("emotion_tag", ""),
            personal_note=data.get("personal_note", ""),
        )

        # Auto-create saved mastery status
        QuoteMastery.objects.get_or_create(user=request.user, quote=quote, defaults={"status": "saved"})

        # Update source progress
        sp, _ = SourceProgress.objects.get_or_create(
            user=request.user,
            source=quote.source,
        )
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


@login_required
@require_http_methods(["GET"])
def favorite_list(request):
    """
    List all favorites for the current user.
    GET /learning/favorites/
    Query params: ?status=learning&source_id=1
    Returns: { ok, favorites: [...] }
    """
    favorites = FavoriteQuote.objects.filter(user=request.user).select_related(
        "quote", "quote__source", "quote__episode"
    )

    # Optional filters
    source_id = request.GET.get("source_id")
    emotion = request.GET.get("emotion")

    if source_id:
        favorites = favorites.filter(quote__source_id=source_id)
    if emotion:
        favorites = favorites.filter(emotion_tag=emotion)

    data = []
    for fav in favorites:
        q = fav.quote
        ep = q.episode

        # Get mastery status if exists
        mastery = QuoteMastery.objects.filter(user=request.user, quote=q).first()

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


@login_required
@require_http_methods(["PATCH"])
def favorite_update(request, quote_id):
    """
    Update emotion tag or personal note on a favorite.
    PATCH /learning/favorites/<quote_id>/
    Body: { "emotion_tag": "funny", "personal_note": "..." }
    """
    favorite = get_object_or_404(FavoriteQuote, user=request.user, quote_id=quote_id)
    data = json_body(request)

    if "emotion_tag" in data:
        favorite.emotion_tag = data["emotion_tag"]
    if "personal_note" in data:
        favorite.personal_note = data["personal_note"]

    favorite.save()

    return success(
        {
            "emotion_tag": favorite.emotion_tag,
            "personal_note": favorite.personal_note,
        }
    )


# ─────────────────────────────────────────────
# MASTERY
# ─────────────────────────────────────────────

MASTERY_ORDER = ["saved", "learning", "mastered"]

SPACED_REPETITION_INTERVALS = {
    "saved": 1,  # review tomorrow
    "learning": 3,  # review in 3 days
    "mastered": 14,  # review in 2 weeks
}


@login_required
@require_http_methods(["POST"])
def mastery_update(request, quote_id):
    """
    Update mastery status for a quote.
    POST /learning/mastery/<quote_id>/
    Body: { "status": "learning" }  OR  { "advance": true }
    Returns: { ok, status, next_review, interval_days }
    """
    quote = get_object_or_404(Quote, id=quote_id)
    data = json_body(request)

    mastery, created = QuoteMastery.objects.get_or_create(user=request.user, quote=quote, defaults={"status": "saved"})

    # Either set explicit status or advance to next level
    if "status" in data:
        new_status = data["status"]
        if new_status not in MASTERY_ORDER:
            return error("Invalid status. Must be: saved, learning, mastered")
        old_status = mastery.status
        mastery.status = new_status
    elif data.get("advance"):
        old_status = mastery.status
        current_index = MASTERY_ORDER.index(mastery.status)
        if current_index < len(MASTERY_ORDER) - 1:
            mastery.status = MASTERY_ORDER[current_index + 1]
        # already mastered, no change
    else:
        return error('Provide either "status" or "advance": true')

    # Update spaced repetition fields
    interval = SPACED_REPETITION_INTERVALS[mastery.status]
    mastery.interval_days = interval
    mastery.review_count += 1
    mastery.last_reviewed = timezone.now()
    mastery.next_review = timezone.now() + timezone.timedelta(days=interval)
    mastery.save()

    # Update LearningProgress counts
    progress, _ = LearningProgress.objects.get_or_create(user=request.user)
    progress.total_quotes_reviewed += 1

    # Update denormalized counts
    if not created and "old_status" in locals():
        count_field_map = {
            "saved": "saved_count",
            "learning": "learning_count",
            "mastered": "mastered_count",
        }
        old_field = count_field_map.get(old_status)
        new_field = count_field_map.get(mastery.status)
        if old_field and old_field != new_field:
            setattr(progress, old_field, max(0, getattr(progress, old_field) - 1))
            setattr(progress, new_field, getattr(progress, new_field) + 1)

    progress.save()

    # Update source progress mastered count
    if mastery.status == "mastered":
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


@login_required
@require_http_methods(["GET"])
def mastery_status(request, quote_id):
    """
    Get mastery status for a single quote.
    GET /learning/mastery/<quote_id>/
    """
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
# WORD NOTES (CRUD)
# ─────────────────────────────────────────────


@login_required
@require_http_methods(["POST"])
def word_note_create(request, quote_id):
    """
    Add a word note to a quote.
    POST /learning/words/<quote_id>/
    Body: {
        "word": "break",
        "definition": "pause in a relationship",
        "personal_note": "Ross uses this defensively",
        "context_type": "slang"
    }
    """
    quote = get_object_or_404(Quote, id=quote_id)
    data = json_body(request)

    word = data.get("word", "").strip().lower()
    if not word:
        return error("Word is required")

    # Check duplicate
    if WordNote.objects.filter(user=request.user, quote=quote, word=word).exists():
        return error("You already noted this word from this quote", status=409)

    note = WordNote.objects.create(
        user=request.user,
        quote=quote,
        word=word,
        definition=data.get("definition", ""),
        personal_note=data.get("personal_note", ""),
        example_usage=data.get("example_usage", ""),
        context_type=data.get("context_type", ""),
    )

    # Update progress counter
    LearningProgress.objects.filter(user=request.user).update(total_words_noted=models.F("total_words_noted") + 1)

    return success(
        {
            "id": note.id,
            "word": note.word,
            "context_type": note.context_type,
            "created_at": note.created_at.isoformat(),
        },
        status=201,
    )


@login_required
@require_http_methods(["GET"])
def word_note_list(request):
    """
    List all word notes for the current user.
    GET /learning/words/
    Query params: ?word=break&quote_id=5&context_type=idiom
    """
    notes = WordNote.objects.filter(user=request.user).select_related("quote", "quote__source", "quote__episode")

    # Optional filters
    word = request.GET.get("word")
    quote_id = request.GET.get("quote_id")
    context_type = request.GET.get("context_type")

    if word:
        notes = notes.filter(word__icontains=word)
    if quote_id:
        notes = notes.filter(quote_id=quote_id)
    if context_type:
        notes = notes.filter(context_type=context_type)

    data = []
    for note in notes:
        q = note.quote
        ep = q.episode
        data.append(
            {
                "id": note.id,
                "word": note.word,
                "definition": note.definition,
                "personal_note": note.personal_note,
                "example_usage": note.example_usage,
                "context_type": note.context_type,
                "quote": {
                    "id": q.id,
                    "text": q.text,
                    "character": getattr(q, "character", ""),
                    "source": q.source.title,
                    "season": ep.season if ep else None,
                    "episode": ep.episode_number if ep else None,
                    "thumbnail": q.thumbnail.url if q.thumbnail else None,
                    "start_time": float(q.start_time),
                },
                "created_at": note.created_at.isoformat(),
            }
        )

    return success({"words": data, "count": len(data)})


@login_required
@require_http_methods(["PATCH"])
def word_note_update(request, note_id):
    """
    Update a word note.
    PATCH /learning/words/<note_id>/update/
    Body: { "definition": "...", "personal_note": "...", "context_type": "..." }
    """
    note = get_object_or_404(WordNote, id=note_id, user=request.user)
    data = json_body(request)

    updatable = ["definition", "personal_note", "example_usage", "context_type"]
    for field in updatable:
        if field in data:
            setattr(note, field, data[field])

    note.save()

    return success(
        {
            "id": note.id,
            "word": note.word,
            "definition": note.definition,
            "personal_note": note.personal_note,
            "context_type": note.context_type,
        }
    )


@login_required
@require_http_methods(["DELETE"])
def word_note_delete(request, note_id):
    """
    Delete a word note.
    DELETE /learning/words/<note_id>/delete/
    """
    note = get_object_or_404(WordNote, id=note_id, user=request.user)
    note.delete()

    # Update progress counter
    LearningProgress.objects.filter(user=request.user).update(total_words_noted=models.F("total_words_noted") - 1)

    return success({"deleted": True})


# ─────────────────────────────────────────────
# REVIEW QUEUE
# ─────────────────────────────────────────────


@login_required
@require_http_methods(["GET"])
def review_queue(request):
    """
    Get quotes due for review today.
    GET /learning/review/queue/
    Returns: { ok, due_today: [...], count }
    """
    now = timezone.now()

    due = (
        QuoteMastery.objects.filter(
            user=request.user,
            next_review__lte=now,
            status__in=["saved", "learning"],
        )
        .select_related("quote", "quote__source", "quote__episode")
        .order_by("next_review")
    )

    data = []
    for mastery in due:
        q = mastery.quote
        ep = q.episode
        data.append(
            {
                "quote_id": q.id,
                "text": q.text,
                "character": getattr(q, "character", ""),
                "source": q.source.title,
                "season": ep.season if ep else None,
                "episode": ep.episode_number if ep else None,
                "start_time": float(q.start_time),
                "end_time": float(q.end_time),
                "video_url": (
                    q.episode.video_file.url
                    if ep and ep.video_file
                    else (q.source.video_file.url if q.source.video_file else None)
                ),
                "thumbnail": q.thumbnail.url if q.thumbnail else None,
                "status": mastery.status,
                "review_count": mastery.review_count,
                "overdue_days": (now - mastery.next_review).days,
            }
        )

    return success({"due_today": data, "count": len(data)})
