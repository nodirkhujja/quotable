from django.contrib import admin
from django.utils.html import format_html, mark_safe

from quiz.models import ClozeResult, QuizAttempt, ReviewSession, SavedSentence

TYPE_COLORS = {
    "flash": "#6366f1",
    "cloze": "#f59e0b",
    "produce": "#10b981",
    "match": "#3b82f6",
    "listen": "#8b5cf6",
    "teach": "#ec4899",
}


def _badge(text, color):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 7px;border-radius:4px;font-size:11px;">{}</span>',
        color,
        text,
    )


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("word_name", "user", "type_col", "dir_col", "result_col", "speed_col", "created_at")
    list_filter = ("quiz_type", "direction", "correct", "user")
    search_fields = ("vocab__english", "note__word", "user__username")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "vocab", "note")
    list_per_page = 50

    @admin.display(description="Word")
    def word_name(self, obj):
        word = obj.vocab.english if obj.vocab else (obj.note.word if obj.note else "—")
        return format_html("<strong>{}</strong>", word)

    @admin.display(description="Type")
    def type_col(self, obj):
        return _badge(obj.quiz_type.upper(), TYPE_COLORS.get(obj.quiz_type, "#666"))

    @admin.display(description="Dir")
    def dir_col(self, obj):
        return mark_safe(
            '<span style="color:#888;">UZ→EN</span>'
            if obj.direction == "l1_l2"
            else '<span style="color:#888;">EN→UZ</span>'
        )

    @admin.display(description="Result")
    def result_col(self, obj):
        return mark_safe(
            '<span style="color:#10b981;font-size:16px;">✓</span>'
            if obj.correct
            else '<span style="color:#ef4444;font-size:16px;">✗</span>'
        )

    @admin.display(description="Speed")
    def speed_col(self, obj):
        if not obj.response_time_ms:
            return "—"
        secs = obj.response_time_ms / 1000
        color = "#10b981" if secs < 3 else "#f59e0b" if secs < 6 else "#ef4444"
        return mark_safe(f'<span style="color:{color};">{secs:.1f}s</span>')


class ClozeResultInline(admin.TabularInline):
    model = ClozeResult
    extra = 0
    readonly_fields = ("answered_at",)


@admin.register(ReviewSession)
class ReviewSessionAdmin(admin.ModelAdmin):
    list_display = ("user", "session_type", "started_at", "quotes_reviewed", "score")
    list_filter = ("session_type",)
    inlines = [ClozeResultInline]


@admin.register(SavedSentence)
class SavedSentenceAdmin(admin.ModelAdmin):
    list_display = ("user", "word", "quiz_type", "created_at")
    list_filter = ("quiz_type",)
    search_fields = ("user__username", "word", "english")
    ordering = ("-created_at",)
