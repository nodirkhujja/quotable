from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html

from learning.models import FavoriteQuote

# IMPORT the models from your learning app
from vocab.models import WordNote

from .models import User


# 1. Create Inlines for the learning data
class FavoriteQuoteInline(admin.TabularInline):
    model = FavoriteQuote
    extra = 0
    raw_id_fields = ("quote",)


class WordNoteInline(admin.TabularInline):
    model = WordNote
    extra = 0


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # Keep all your existing list_display, list_filter, etc.
    list_display = ("username", "email", "avatar_preview", "target_language", "streak_days", "is_staff")

    # ADD THIS LINE to show favorites and notes on the User edit page
    inlines = [FavoriteQuoteInline, WordNoteInline]

    fieldsets = UserAdmin.fieldsets + (
        ("Profile Picture", {"fields": ("avatar",)}),
        ("Language Learning Details", {"fields": ("native_language", "target_language", "proficiency_level")}),
        ("Gamification (Streaks)", {"fields": ("streak_days", "longest_streak", "last_active_date")}),
        (
            "User Preferences",
            {"fields": ("daily_goal_minutes", "preferred_playback_speed", "show_subtitles_on_video")},
        ),
    )

    search_fields = ("username", "email", "native_language", "target_language")

    fieldsets = UserAdmin.fieldsets + (
        ("Profile Picture", {"fields": ("avatar",)}),
        ("Language Learning Details", {"fields": ("native_language", "target_language", "proficiency_level")}),
        ("Gamification (Streaks)", {"fields": ("streak_days", "longest_streak", "last_active_date")}),
        (
            "User Preferences",
            {"fields": ("daily_goal_minutes", "preferred_playback_speed", "show_subtitles_on_video")},
        ),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Initial Info",
            {
                "fields": ("email", "native_language", "target_language"),
            },
        ),
    )

    def avatar_preview(self, obj):
        if obj.avatar:
            return format_html(
                '<img src="{}" style="width: 35px; height: 35px; border-radius: 50%; object-fit: cover;" />',
                obj.avatar.url,
            )
        return "No Image"

    avatar_preview.short_description = "Avatar"

    ordering = ("-date_joined",)
