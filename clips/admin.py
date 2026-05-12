from django.contrib import admin
from django.utils import timezone

from .models import Episode, Quote, Reel, ReelLine, ReelView, SceneBlock, Source, Transcript


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "episode", "start_time", "text_snippet")
    list_filter = ("source",)
    search_fields = ("text", "source__title")
    autocomplete_fields = ["source", "episode"]

    def text_snippet(self, obj):
        return (obj.text or "")[:80]

    text_snippet.short_description = "Text"


# ─── REELS ADMIN ────────────────────────────────────────


class ReelLineInline(admin.TabularInline):
    model = ReelLine
    extra = 1
    autocomplete_fields = ["transcript"]
    fields = ("order", "transcript", "is_shadow_target", "is_punchline", "target_words")


@admin.register(Reel)
class ReelAdmin(admin.ModelAdmin):
    list_display = ("title", "source", "duration_label", "cefr_level", "comedy_score", "is_published", "view_count")
    list_filter = ("is_published", "cefr_level", "source")
    search_fields = ("title", "description", "source__title")
    autocomplete_fields = ["source", "episode"]
    inlines = [ReelLineInline]
    actions = ["publish_reels", "unpublish_reels"]
    fieldsets = (
        (
            "Basic",
            {
                "fields": ("title", "description", "source", "episode"),
            },
        ),
        (
            "Time bounds",
            {
                "fields": ("start_time", "end_time", "poster_frame", "thumbnail"),
                "description": "Times are seconds within the source/episode video. "
                "Poster frame defaults to midpoint if blank.",
            },
        ),
        (
            "Curation",
            {
                "fields": ("cefr_level", "comedy_score"),
            },
        ),
        (
            "Publishing",
            {
                "fields": ("is_published", "published_at"),
            },
        ),
        (
            "Stats (read-only)",
            {
                "fields": ("view_count", "save_count", "shadow_count"),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = ("view_count", "save_count", "shadow_count")

    def duration_label(self, obj):
        return f"{obj.duration_sec}s"

    duration_label.short_description = "Duration"

    def publish_reels(self, request, qs):
        n = qs.update(is_published=True, published_at=timezone.now())
        self.message_user(request, f"Published {n} reel(s).")

    publish_reels.short_description = "Publish selected reels"

    def unpublish_reels(self, request, qs):
        n = qs.update(is_published=False)
        self.message_user(request, f"Unpublished {n} reel(s).")

    unpublish_reels.short_description = "Unpublish selected reels"


@admin.register(ReelView)
class ReelViewAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "reel",
        "completed",
        "shadowed_lines",
        "saved_words",
        "liked",
        "bookmarked",
        "last_seen_at",
    )
    list_filter = ("completed", "liked", "bookmarked")
    search_fields = ("user__username", "reel__title")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ("title", "source_type", "year", "created_at")
    list_filter = ("source_type", "year")
    search_fields = ("title", "description")

    # This makes the "Source Type" column look nice (e.g., Green for Movie, Blue for TV)
    def source_type_badge(self, obj):
        return obj.get_source_type_display()

    source_type_badge.short_description = "Type"


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    list_display = ("source", "season", "episode_number", "title")
    list_filter = ("source", "season")
    search_fields = ("title", "source__title")
    autocomplete_fields = ["source"]  # Helpful if you have many shows


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("text_snippet", "source_title", "start_time", "duration")
    list_filter = ("source__source_type", "source")
    search_fields = ("text", "source__title")

    def text_snippet(self, obj):
        return obj.text[:50] + "..." if len(obj.text) > 50 else obj.text

    def source_title(self, obj):
        return obj.source.title


@admin.register(SceneBlock)
class SceneBlockAdmin(admin.ModelAdmin):
    list_display = ("source", "episode", "start_time", "end_time", "thumbnail")
    list_filter = ("source", "episode")
    search_fields = ("source__title",)
    readonly_fields = ("thumbnail",)
