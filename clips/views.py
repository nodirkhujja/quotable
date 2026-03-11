import json

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from .models import Episode, Quote, Source, WatchHistory


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


def _motivational_line(streak, total_words, mastered):
    if total_words == 0:
        return "Pick a scene you love. Save 5 words. That's your first step."
    if streak == 0:
        return "Find one show. Discover 5 words. Do it just for today."
    if streak == 1:
        return "Great start. Watch 10 minutes today and save 3 words you hear."
    if streak == 3:
        return "3 days in. Watch one scene and grab the words that stick with you."
    if streak >= 7:
        return f"{streak} days strong. One scene today — find 3 new words you've never saved."
    if mastered >= 10:
        return "Watch 10 minutes today. The words you save now are yours forever."
    if mastered > 0:
        return "One show. Five words. That's all it takes to keep moving forward."
    return "Discover 5 words in your next scene and enjoy the story even more."


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

    # Word stats + motivational line
    streak = total_words = mastered_words = 0
    motivational_line = ""
    if request.user.is_authenticated:
        from learning.models import WordNote

        streak = request.user.streak_days
        qs = WordNote.objects.filter(user=request.user)
        total_words = qs.count()
        mastered_words = qs.filter(stage="mastered").count()
        motivational_line = _motivational_line(streak, total_words, mastered_words)

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

    context = {
        "movies": movies,
        "tv_shows": tv_shows,
        "query": query,
        "total_sources": Source.objects.count(),
        "user_level": user_level,
        "user_level_display": user_level_display,
        "last_watched": last_watched,
        "motivational_line": motivational_line,
        "streak": streak,
        "total_words": total_words,
    }

    return render(request, "clips/home.html", context)


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

    # Saved words count + list for the counter badge and CC highlights
    saved_words_count = 0
    saved_words_list = []
    if request.user.is_authenticated:
        from learning.models import WordNote

        saved_words_qs = WordNote.objects.filter(user=request.user)
        saved_words_count = saved_words_qs.count()
        saved_words_list = list(saved_words_qs.values_list("word", flat=True))

    # Core words set for gold highlight
    from learning.models import CoreWord, SuggestedWord

    core_words = list(CoreWord.objects.values_list("word", flat=True))

    # Suggested words for this source — keyed by word for fast lookup
    from learning.models import WordCache

    suggested_qs = SuggestedWord.objects.filter(source=source).select_related("transcript", "episode")

    # Pre-load definitions from WordCache for all suggested words
    all_suggested_words = set(sw.word.lower() for sw in suggested_qs)
    word_definitions = dict(WordCache.objects.filter(word__in=all_suggested_words).values_list("word", "definition"))

    suggested_words_map = {}
    vocab_words_list = []
    for sw in suggested_qs:
        w = sw.word.lower()
        level = sw.level or SuggestedWord.LEVEL_BEGINNER
        ep_key = f"S{sw.season}E{sw.episode_number}" if sw.season else "movie"

        vocab_words_list.append(
            {
                "word": sw.word,
                "translation": sw.translation,
                "definition": word_definitions.get(w, ""),
                "level": level,
                "ep_key": ep_key,
                "start": float(sw.start_time),
                "end": float(sw.end_time),
                "transcript_id": sw.transcript_id,
            }
        )

        if w not in suggested_words_map:
            suggested_words_map[w] = {
                "word": sw.word,
                "translation": sw.translation,
                "is_phrase": " " in sw.word,
                "level": level,
                "ep_key": ep_key,
                "transcript_id": sw.transcript_id,
            }

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
            "core_words_json": json.dumps(core_words),
            "saved_words_json": json.dumps(saved_words_list),
            "suggested_words_json": json.dumps(suggested_words_map),
            "vocab_words_json": json.dumps(vocab_words_list),
            "query": query,
            "first_episode_id": first_episode_id,
            "saved_words_count": saved_words_count,
        },
    )


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
