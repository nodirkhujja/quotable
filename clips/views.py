import json

from django.shortcuts import get_object_or_404, render
from django.views.generic import DetailView, ListView

from .models import Quote, Source


class QuoteDetailView(DetailView):
    model = Quote
    template_name = "clips/quote_detail.html"  # fixed typo: ttemplate_name → template_name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Increment view count
        self.object.views += 1
        self.object.save(update_fields=["views"])
        return context


class QuoteSearchView(ListView):
    model = Quote
    template_name = "clips/base.html"  # or your search results template
    context_object_name = "quotes"
    paginate_by = 20

    def get_queryset(self):
        query = self.request.GET.get("q", "")
        if query:
            return Quote.objects.filter(text__icontains=query).select_related("source")
        return Quote.objects.all().select_related("source")


def home_view(request):
    """Simple home page listing all sources/videos"""
    sources = Source.objects.all().order_by("title")[:12]  # limit to 12 for now
    return render(request, "clips/home.html", {"sources": sources})


def watch_source(request, source_id):
    """Main single-page video view with search + recommendations"""
    source = get_object_or_404(Source, id=source_id)
    query = request.GET.get("search", "")

    # Get quotes for this source
    quotes = source.quotes.all().order_by("start_time")
    quotes_json = json.dumps(
        [
            {
                "id": q.id,
                "text": q.text,
                "startTime": q.start_time,
                "endTime": q.end_time,
                "duration": q.duration,
                # Fallback if model doesn't have these fields yet
                "scene": getattr(q, "scene", ""),
                "character": getattr(q, "character", ""),
            }
            for q in quotes
        ]
    )

    # Recommendations: exclude current, take latest 5 (you can improve ordering later)
    recommendations = Source.objects.exclude(id=source_id).order_by("-id")[:5]

    return render(
        request,
        "clips/watch_source.html",
        {
            "source": source,
            "quotes_json": quotes_json,
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
