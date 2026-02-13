import json

from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.views.generic import DetailView, ListView

from .models import Quote, Source


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


def home_view(request):
    """
    Home page listing sources with live search by title or slug.
    Shows up to 12 results, ordered alphabetically.
    Search query is preserved in the input field.
    """
    query = request.GET.get("q", "").strip()

    # Base queryset
    sources = Source.objects.all()

    # Apply search filter if query exists
    if query:
        sources = sources.filter(Q(title__icontains=query) | Q(slug__icontains=query))

    # Order and limit
    sources = sources.order_by("title")[:12]

    context = {
        "sources": sources,
        "query": query,
        "total_sources": Source.objects.count(),
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
        if source.video_file:
            url = source.video_file.url
            video_list = [url]
            video_map["movie"] = url

    # 2. Get quotes with optimized DB query
    if source.source_type == "tv_show":
        quotes = (
            Quote.objects.filter(episode__source=source)
            .select_related("episode")
            .order_by("episode__season", "episode__episode_number", "start_time")
        )
    else:
        quotes = source.quotes.all().order_by("start_time")

    quotes_data = []
    default_url = video_list[0] if video_list else ""

    for q in quotes:
        ep = getattr(q, "episode", None)

        if ep:
            video_key = f"S{ep.season}E{ep.episode_number}"
            current_video_url = video_map.get(video_key, default_url)
        else:
            current_video_url = default_url

        # Get thumbnail URL - this is the key addition!
        thumbnail_url = q.thumbnail.url if q.thumbnail else None

        quotes_data.append(
            {
                "id": q.id,
                "text": q.text,
                "episodeTitle": ep.title if ep else source.title,
                "startTime": float(q.start_time),
                "endTime": float(q.end_time),
                "videoUrl": current_video_url,
                "thumbnailUrl": thumbnail_url,  # ← ADD THIS LINE
                "season": ep.season if ep else None,
                "episode": ep.episode_number if ep else None,
            }
        )

    recommendations = Source.objects.exclude(id=source_id).order_by("-id")[:5]

    return render(
        request,
        "clips/watch_source.html",
        {
            "source": source,
            "video_url": default_url,
            "video_map": json.dumps(video_map),
            "quotes_json": json.dumps(quotes_data),
            "query": query,
            "recommendations": recommendations,
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
