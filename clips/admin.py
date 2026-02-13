from django.contrib import admin

from .models import Episode, Quote, Source


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
