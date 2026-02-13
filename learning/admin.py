from django.contrib import admin

from .models import ClozeResult, FavoriteQuote, LearningProgress, QuoteMastery, ReviewSession, SourceProgress, WordNote


class ClozeResultInline(admin.TabularInline):
    model = ClozeResult
    extra = 0
    readonly_fields = ("answered_at",)


@admin.register(QuoteMastery)
class QuoteMasteryAdmin(admin.ModelAdmin):
    list_display = ("user", "quote", "status", "review_count", "next_review")
    list_filter = ("status", "next_review")
    raw_id_fields = ("user", "quote")


@admin.register(WordNote)
class WordNoteAdmin(admin.ModelAdmin):
    list_display = ("word", "user", "context_type", "created_at")
    list_filter = ("context_type", "created_at")
    search_fields = ("word", "user__username")


@admin.register(ReviewSession)
class ReviewSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "session_type", "started_at", "quotes_reviewed")
    inlines = [ClozeResultInline]


@admin.register(LearningProgress)
class LearningProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "mastered_count", "total_session_minutes")
    readonly_fields = ("updated_at",)


@admin.register(SourceProgress)
class SourceProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "quotes_seen", "quotes_mastered")
    raw_id_fields = ("user", "source")


@admin.register(FavoriteQuote)
class FavoriteQuoteAdmin(admin.ModelAdmin):
    list_display = ("user", "quote", "emotion_tag", "created_at")
    list_filter = ("emotion_tag",)
