import json

from django.contrib.auth.decorators import login_required
from django.db import models
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from .models import Episode, Quote, Reel, ReelView, Source, WatchHistory


class QuoteDetailView(DetailView):
    model = Quote
    template_name = "clips/quote_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Increment view count
        self.object.views += 1
        self.object.save(update_fields=["views"])
        return context


class QuoteSearchView(ListView):
    model = Quote
    template_name = "clips/base.html"
    context_object_name = "quotes"
    paginate_by = 20

    def get_queryset(self):
        query = self.request.GET.get("q", "")
        if query:
            return Quote.objects.filter(text__contains=query).select_related("source")
        return Quote.objects.all().select_related("source")


@login_required
@require_POST
def save_watch_history(request):
    """
    Silently called by the player every 15 s (and on pause/unload).
    Ignores positions < 60 s to avoid recording accidental opens.
    """
    try:
        data = json.loads(request.body)
        position = float(data.get("position", 0))
        duration = float(data.get("duration", 0))
    except (ValueError, KeyError):
        return JsonResponse({"ok": False}, status=400)

    if position < 60:
        return JsonResponse({"ok": True})

    source = get_object_or_404(Source, id=data.get("source_id"))

    episode = None
    if data.get("episode_id"):
        episode = get_object_or_404(Episode, id=data["episode_id"], source=source)

    WatchHistory.objects.update_or_create(
        user=request.user,
        source=source,
        episode=episode,
        defaults={"position_sec": position, "duration_sec": duration},
    )
    return JsonResponse({"ok": True})


_LEVEL_DISPLAY = {
    "beginner": "Beginner",
    "intermediate": "Intermediate",
    "upper_intermediate": "Upper-Intermediate",
    "advanced": "Advanced",
}


def home_view(request):
    query = request.GET.get("q", "").strip()

    base_qs = Source.objects.all()
    if query:
        base_qs = base_qs.filter(Q(title__icontains=query) | Q(slug__icontains=query))

    movies = base_qs.filter(source_type="movie").annotate(quote_count=Count("quotes")).order_by("title")
    tv_shows = base_qs.filter(source_type="tv_show").annotate(episode_count=Count("episodes")).order_by("title")

    # Resolve user's onboarding level (single cheap query, no circular import risk)
    user_level = user_level_display = ""
    if request.user.is_authenticated:
        from learning.models import OnboardingSession  # lazy — avoids app-registry issues

        row = (
            OnboardingSession.objects.filter(user=request.user, completed_at__isnull=False)
            .values_list("level", flat=True)
            .first()
        )
        if row:
            user_level = row
            user_level_display = _LEVEL_DISPLAY.get(row, row)

    # Continue Watching — last non-complete entry (≥60 s in, <90 % through)
    last_watched = None
    if request.user.is_authenticated:
        candidate = (
            WatchHistory.objects.filter(user=request.user, position_sec__gte=60)
            .select_related("source", "episode")
            .order_by("-updated_at")
            .first()
        )
        if candidate and not candidate.is_complete:
            last_watched = candidate

    # Comprehension % per source
    comprehension_map = {}
    if request.user.is_authenticated:
        from vocab.models import LineVocab, VocabMastery

        total_by_source = dict(
            LineVocab.objects.values("source_id").annotate(cnt=Count("id")).values_list("source_id", "cnt")
        )
        if total_by_source:
            known_by_source = dict(
                VocabMastery.objects.filter(user=request.user, p_known__gt=0.5)
                .values("vocab__source_id")
                .annotate(cnt=Count("id"))
                .values_list("vocab__source_id", "cnt")
            )
            for src_id, total in total_by_source.items():
                known = known_by_source.get(src_id, 0)
                comprehension_map[src_id] = round(known / max(total, 1) * 100)

    # Enrich source objects with comprehension pct (avoids template filter gymnastics)
    movies_list = list(movies)
    for src in movies_list:
        src.comprehension_pct = comprehension_map.get(src.id, 0)
    tv_list = list(tv_shows)
    for src in tv_list:
        src.comprehension_pct = comprehension_map.get(src.id, 0)

    # Reels — published, sorted by published date (newest first).
    # For now: just show all published. Phase 2 will personalize by
    # the user's learning-stage words.
    reels = (
        Reel.objects.filter(is_published=True)
        .select_related("source", "episode")
        .order_by("-published_at", "-created_at")[:12]
    )

    context = {
        "movies": movies_list,
        "tv_shows": tv_list,
        "reels": reels,
        "query": query,
        "total_sources": Source.objects.count(),
        "user_level": user_level,
        "user_level_display": user_level_display,
        "last_watched": last_watched,
    }

    return render(request, "clips/home.html", context)


# ─── REELS — feed + viewer ───────────────────────────────────────────


def reels_feed(request):
    """GET /reels/ — vertical TikTok-style feed of all published reels.

    Hands the front-end the full per-reel payload (lines, target words,
    user engagement state) so the inline shadow drill can run without
    a round-trip.
    """
    reels = list(
        Reel.objects.filter(is_published=True)
        .select_related("source", "episode")
        .prefetch_related("lines__transcript")
        .order_by("-published_at", "-created_at")
    )

    rv_by_reel = {}
    if request.user.is_authenticated and reels:
        rv_by_reel = {
            rv.reel_id: rv
            for rv in ReelView.objects.filter(
                user=request.user,
                reel__in=reels,
            )
        }

    # Pre-fetch Uzbek translations for every transcript covered by these
    # reels so the drill's meaning anchor doesn't need a round-trip.
    from vocab.models import LineTranslation, LineVocab

    transcript_ids = {ln.transcript_id for r in reels for ln in r.lines.all()}
    translation_by_tid = dict(
        LineTranslation.objects.filter(transcript_id__in=transcript_ids).values_list("transcript_id", "translation")
    )

    # Pre-fetch vocab cards (LineVocab) for every transcript these reels
    # cover. We pick the entry whose `english` matches `reel.title` so
    # the side panel shows the vocab the reel actually teaches.
    target_tids = {
        next(
            (ln.transcript_id for ln in r.lines.all() if ln.is_shadow_target),
            r.lines.all()[0].transcript_id if r.lines.all() else None,
        )
        for r in reels
    }
    target_tids.discard(None)
    vocab_by_tid = {}
    for lv in LineVocab.objects.filter(transcript_id__in=target_tids):
        vocab_by_tid.setdefault(lv.transcript_id, []).append(lv)

    # Pre-fetch which (transcript_id, word) pairs the user has already
    # saved to their notebook — used to render the vocab Save button as
    # already-saved on subsequent visits to the same reel.
    saved_pairs = set()
    if request.user.is_authenticated and target_tids:
        from vocab.models import WordNote

        for note in WordNote.objects.filter(
            user=request.user,
            transcript_id__in=target_tids,
        ).only("transcript_id", "word"):
            saved_pairs.add((note.transcript_id, (note.word or "").strip().lower()))

    def _vocab_for_reel(r):
        """Return the LineVocab card whose english matches reel.title.

        Falls back to the first vocab on the target line if no exact
        match (which still lets the side panel populate something).
        """
        target_tid = next(
            (ln.transcript_id for ln in r.lines.all() if ln.is_shadow_target),
            None,
        )
        if not target_tid and r.lines.all():
            target_tid = r.lines.all()[0].transcript_id
        candidates = vocab_by_tid.get(target_tid, [])
        if not candidates:
            return None
        title_norm = (r.title or "").strip().lower()
        match = next(
            (lv for lv in candidates if lv.english.strip().lower() == title_norm),
            candidates[0],
        )
        return {
            "english": match.english,
            "uzbek": match.translation,
            "kind": match.kind,
            "level": match.level,
            "category": match.category,
            "description": match.description,
            "tavsif": match.tavsif,
            "pattern": match.pattern,
            "example": match.example,
            "translated_examples": match.translated_examples or [],
            "collocations": match.collocations or [],
            "saved": (target_tid, match.english.strip().lower()) in saved_pairs,
        }

    reels_data = []
    for r in reels:
        rv = rv_by_reel.get(r.id)
        lines = []
        for ln in r.lines.all():
            tr = ln.transcript
            lines.append(
                {
                    "id": ln.id,
                    "transcript_id": tr.id,
                    "start_time": float(tr.start_time or 0),
                    "end_time": float(tr.end_time or 0),
                    "text": tr.text or "",
                    "translation": translation_by_tid.get(tr.id, ""),
                    "is_shadow_target": ln.is_shadow_target,
                    "is_punchline": ln.is_punchline,
                    "target_words": ln.target_words or [],
                }
            )
        reels_data.append(
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "source_id": r.source_id,
                "source_title": r.source.title,
                "episode_id": r.episode_id,
                "episode_label": (f"S{r.episode.season:02d}E{r.episode.episode_number:02d}" if r.episode else ""),
                "duration_sec": r.duration_sec,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "thumbnail_url": r.thumbnail.url if r.thumbnail else "",
                "video_url": r.video_url or "",
                "cefr_level": r.cefr_level,
                "liked": bool(rv and rv.liked),
                "bookmarked": bool(rv and rv.bookmarked),
                "lines": lines,
                "vocab": _vocab_for_reel(r),
            }
        )
    # Tutor-mode personalization: warm greeting + name. The greeting strip
    # appears briefly on landing so the student feels seen before content.
    user = request.user
    first_name = ""
    if user.is_authenticated:
        first_name = (user.first_name or user.username or "").split()[0] if (user.first_name or user.username) else ""
    from django.utils import timezone as _tz

    hour = _tz.localtime().hour
    if 5 <= hour < 12:
        greeting = "Good morning"
    elif 12 <= hour < 17:
        greeting = "Welcome back"
    elif 17 <= hour < 22:
        greeting = "Good evening"
    else:
        greeting = "Welcome back"

    return render(
        request,
        "clips/reels_feed.html",
        {
            "reels": reels,
            "reels_json": json.dumps(reels_data),
            "first_name": first_name,
            "greeting": greeting,
        },
    )


@require_POST
@login_required
def reel_track(request, reel_id):
    """POST /reels/<id>/track/ — record a view + engagement events.

    Body (JSON):
      event: "view" | "complete" | "shadow" | "save" | "like" | "bookmark"
    """
    reel = get_object_or_404(Reel, id=reel_id, is_published=True)
    try:
        data = json.loads(request.body or "{}")
    except Exception:
        data = {}
    event = data.get("event", "view")

    rv, created = ReelView.objects.get_or_create(user=request.user, reel=reel)
    if event == "view" and created:
        Reel.objects.filter(pk=reel.pk).update(view_count=models.F("view_count") + 1)
    elif event == "complete" and not rv.completed:
        rv.completed = True
        rv.save(update_fields=["completed", "last_seen_at"])
    elif event == "shadow":
        rv.shadowed_lines += 1
        rv.save(update_fields=["shadowed_lines", "last_seen_at"])
        Reel.objects.filter(pk=reel.pk).update(shadow_count=models.F("shadow_count") + 1)
    elif event == "save":
        rv.saved_words += 1
        rv.save(update_fields=["saved_words", "last_seen_at"])
        Reel.objects.filter(pk=reel.pk).update(save_count=models.F("save_count") + 1)
    elif event == "like":
        rv.liked = not rv.liked
        rv.save(update_fields=["liked", "last_seen_at"])
    elif event == "bookmark":
        rv.bookmarked = not rv.bookmarked
        rv.save(update_fields=["bookmarked", "last_seen_at"])

    return JsonResponse({"ok": True, "liked": rv.liked, "bookmarked": rv.bookmarked})


@login_required
def watch_source(request, source_id):
    source = get_object_or_404(Source, id=source_id)
    query = request.GET.get("search", "")

    video_list = []
    video_map = {}

    # 1. Build video mapping
    if source.source_type == "tv_show":
        episodes = source.episodes.exclude(video_file="").order_by("season", "episode_number")
        for ep in episodes:
            video_key = f"S{ep.season}E{ep.episode_number}"
            url = ep.video_file.url
            video_map[video_key] = url
            video_list.append(url)
    else:
        try:
            if source.video_file:
                url = source.video_file.url
                video_list = [url]
                video_map["movie"] = url
        except ValueError:
            pass

    default_url = video_list[0] if video_list else ""

    # 2. Build transcript data (with IDs for word-saving)
    transcript_data = {}
    episode_titles = {}
    if source.source_type == "tv_show":
        all_episodes = source.episodes.prefetch_related("transcripts").order_by("season", "episode_number")
        for ep in all_episodes:
            key = f"S{ep.season}E{ep.episode_number}"
            if ep.title:
                episode_titles[key] = ep.title
            transcript_data[key] = [
                {
                    "id": t.id,
                    "text": t.text,
                    "start": float(t.start_time),
                    "end": float(t.end_time),
                }
                for t in ep.transcripts.order_by("start_time")
            ]
    else:
        transcript_data["movie"] = [
            {
                "id": t.id,
                "text": t.text,
                "start": float(t.start_time),
                "end": float(t.end_time),
            }
            for t in source.transcripts.filter(episode=None).order_by("start_time")
        ]

    # First episode ID so JS can initialise currentEpisodeId before any quote loads
    first_episode_id = None
    if source.source_type == "tv_show":
        first_ep = episodes.first()
        first_episode_id = first_ep.id if first_ep else None

    # 3. Build scene blocks data (pre-grouped with thumbnails)
    scene_blocks_data = {}
    if source.source_type == "tv_show":
        for ep in source.episodes.prefetch_related("scene_blocks").order_by("season", "episode_number"):
            key = f"S{ep.season}E{ep.episode_number}"
            scene_blocks_data[key] = [
                {
                    "id": sb.id,
                    "start": float(sb.start_time),
                    "end": float(sb.end_time),
                    "label": sb.label,
                    "thumbnail": sb.thumbnail.url if sb.thumbnail else None,
                }
                for sb in ep.scene_blocks.order_by("start_time")
            ]
    else:
        scene_blocks_data["movie"] = [
            {
                "id": sb.id,
                "start": float(sb.start_time),
                "end": float(sb.end_time),
                "label": sb.label,
                "thumbnail": sb.thumbnail.url if sb.thumbnail else None,
            }
            for sb in source.scene_blocks.filter(episode=None).order_by("start_time")
        ]

    return render(
        request,
        "clips/watch_source.html",
        {
            "source": source,
            "video_url": default_url,
            "video_map": json.dumps(video_map),
            "transcript_json": json.dumps(transcript_data),
            "scene_blocks_json": json.dumps(scene_blocks_data),
            "episode_titles_json": json.dumps(episode_titles),
            "query": query,
            "first_episode_id": first_episode_id,
        },
    )


def episode_data(request, episode_id):
    """GET /clips/episode/<id>/data/ — transcript lines + scene blocks for one episode.

    Used by the watch page to lazy-load non-first episodes on demand instead
    of embedding all 26 episodes' worth of JSON on page load.
    """
    episode = get_object_or_404(Episode, id=episode_id)
    transcript_lines = [
        {
            "id": t.id,
            "text": t.text,
            "start": float(t.start_time),
            "end": float(t.end_time),
        }
        for t in episode.transcripts.order_by("start_time")
    ]
    scene_blocks = [
        {
            "id": sb.id,
            "start": float(sb.start_time),
            "end": float(sb.end_time),
            "label": sb.label,
            "thumbnail": sb.thumbnail.url if sb.thumbnail else None,
        }
        for sb in episode.scene_blocks.order_by("start_time")
    ]
    return JsonResponse({"transcript": transcript_lines, "scene_blocks": scene_blocks})


@login_required
def ui_test(request):
    """Quick UI preview without data"""
    return render(
        request,
        "clips/watch_source.html",
        {
            "source": Source(title="UI Test Video"),
            "quotes_json": "[]",
            "query": "",
            "recommendations": Source.objects.all()[:3],
        },
    )
