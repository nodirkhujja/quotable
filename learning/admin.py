from django.contrib import admin

from .models import (
    ClozeResult, CoreWord, FavoriteQuote, LearningProgress, OnboardingResult, OnboardingSession, QuoteMastery,
    ReviewSession, SourceProgress, VocabWord, WordCache, WordNote,
)


@admin.register(WordCache)
class WordCacheAdmin(admin.ModelAdmin):
    list_display = ("word", "get_pos_display", "definition", "created_at")
    list_filter = ("pos", "created_at")
    search_fields = ("word", "definition")
    ordering = ("-created_at",)


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


@admin.register(VocabWord)
class VocabWordAdmin(admin.ModelAdmin):
    list_display = ("word", "frequency_rank", "tier", "pos", "uzbek_translation")
    list_filter = ("tier", "pos")
    search_fields = ("word", "uzbek_translation")
    ordering = ("frequency_rank",)


class OnboardingResultInline(admin.TabularInline):
    model = OnboardingResult
    extra = 0
    readonly_fields = ("answered_at",)
    raw_id_fields = ("word",)


@admin.register(OnboardingSession)
class OnboardingSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "level", "projected_total", "words_shown", "words_known", "completed_at")
    list_filter = ("level",)
    readonly_fields = ("started_at", "completed_at")
    inlines = [OnboardingResultInline]


@admin.register(CoreWord)
class CoreWordAdmin(admin.ModelAdmin):
    list_display = ("word",)
    search_fields = ("word",)
