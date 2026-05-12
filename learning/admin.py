from django.contrib import admin
from django.utils.html import format_html, mark_safe

from vocab.models import CoreWord, LineTranslation, LineVocab, VocabMastery, VocabWord, WordCache, WordNote

from .models import (
    GRAMMAR_PATTERNS,
    BuildAttempt,
    ConfusionPair,
    DailyActivity,
    FavoriteQuote,
    FlashcardAttempt,
    GrammarPracticeLog,
    LearningProgress,
    LookupLog,
    OnboardingResult,
    OnboardingSession,
    PageVisit,
    PatternMastery,
    QuoteMastery,
    SentencePattern,
    ShadowingLog,
    SourceProgress,
    UserLearningProfile,
)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _bar(value, max_val=100, color="#a855f7", width=80):
    """Tiny inline progress bar."""
    pct = min(100, max(0, (value / max_val) * 100)) if max_val else 0
    return mark_safe(
        '<div style="display:inline-flex;align-items:center;gap:6px;">'
        '<div style="width:%dpx;height:8px;background:#2a2a2a;border-radius:4px;overflow:hidden;">'
        '<div style="width:%.0f%%;height:100%%;background:%s;border-radius:4px;"></div>'
        "</div>"
        '<span style="font-size:11px;color:#888;">%.0f%%</span>'
        "</div>" % (width, pct, color, value)
    )


def _badge(text, bg="#666"):
    return mark_safe(
        '<span style="padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;'
        'color:#fff;background:%s;">%s</span>' % (bg, text)
    )


LEVEL_COLORS = {
    "weak": "#ef4444",
    "shaky": "#f59e0b",
    "good": "#3b82f6",
    "strong": "#8b5cf6",
    "mastered": "#10b981",
}

TYPE_COLORS = {
    "flash": "#3b82f6",
    "cloze": "#f59e0b",
    "produce": "#a855f7",
    "match": "#10b981",
    "listen": "#ec4899",
    "teach": "#6b7280",
}


def _time_ago(dt):
    if not dt:
        return mark_safe('<span style="color:#666;">never</span>')
    from django.utils.timesince import timesince

    return mark_safe('<span style="color:#888;">%s ago</span>' % timesince(dt))


# ─────────────────────────────────────────────
# Quiz System
# ─────────────────────────────────────────────


@admin.register(VocabMastery)
class VocabMasteryAdmin(admin.ModelAdmin):
    list_display = (
        "word_name",
        "user",
        "level_col",
        "knowledge_col",
        "streak_col",
        "attempts",
        "elo_col",
        "reviewed_col",
    )
    list_filter = ("level", "user")
    search_fields = ("vocab__english", "user__username")
    ordering = ("-mastery_score",)
    raw_id_fields = ("user", "vocab")
    list_per_page = 30

    @admin.display(description="Word", ordering="vocab__english")
    def word_name(self, obj):
        return format_html("<strong>{}</strong>", obj.vocab.english)

    @admin.display(description="Level")
    def level_col(self, obj):
        return _badge(obj.level.title(), LEVEL_COLORS.get(obj.level, "#666"))

    @admin.display(description="P(known)", ordering="p_known")
    def knowledge_col(self, obj):
        pct = obj.p_known * 100
        color = "#10b981" if pct >= 65 else "#f59e0b" if pct >= 30 else "#ef4444"
        return _bar(pct, color=color)

    @admin.display(description="Streak")
    def streak_col(self, obj):
        if obj.streak >= 3:
            return mark_safe('<span style="color:#f59e0b;font-weight:700;">x%d</span>' % obj.streak)
        return obj.streak

    @admin.display(description="Difficulty", ordering="difficulty")
    def elo_col(self, obj):
        return "%.0f" % obj.difficulty

    @admin.display(description="Last Review", ordering="last_reviewed")
    def reviewed_col(self, obj):
        return _time_ago(obj.last_reviewed)


@admin.register(UserLearningProfile)
class UserLearningProfileAdmin(admin.ModelAdmin):
    list_display = ("user",)
    readonly_fields = (
        "user",
        "elo_card",
        "accuracy_card",
        "vocab_card",
        "rate_card",
        "retention_card",
        "weakness_card",
        "updated_at",
    )
    fieldsets = (
        (
            "Overview",
            {
                "fields": ("user", "elo_card", "accuracy_card", "vocab_card"),
            },
        ),
        (
            "Learning Speed",
            {
                "fields": ("rate_card", "retention_card"),
            },
        ),
        (
            "Weakness Analysis",
            {
                "fields": ("weakness_card",),
            },
        ),
        (
            "Meta",
            {
                "fields": ("updated_at",),
                "classes": ("collapse",),
            },
        ),
    )

    @admin.display(description="Elo Rating")
    def elo_card(self, obj):
        r = obj.ability_rating
        color = "#10b981" if r >= 1200 else "#3b82f6" if r >= 1000 else "#f59e0b" if r >= 800 else "#ef4444"
        label = "Expert" if r >= 1200 else "Strong" if r >= 1000 else "Average" if r >= 800 else "Beginner"
        return mark_safe(
            '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:300px;">'
            '<div style="font-size:32px;font-weight:800;color:%s;">%.0f</div>'
            '<div style="font-size:13px;color:#888;margin-top:4px;">%s &mdash; compared to word difficulty</div>'
            "</div>" % (color, r, label)
        )

    @admin.display(description="Accuracy")
    def accuracy_card(self, obj):
        pct = obj.accuracy
        color = "#10b981" if pct >= 80 else "#f59e0b" if pct >= 60 else "#ef4444"
        return mark_safe(
            '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:300px;">'
            '<div style="font-size:28px;font-weight:800;color:%s;">%.1f%%</div>'
            '<div style="font-size:13px;color:#888;margin-top:4px;">%d correct out of %d attempts</div>'
            "</div>" % (color, pct, obj.total_correct, obj.total_attempts)
        )

    @admin.display(description="Vocabulary Size")
    def vocab_card(self, obj):
        return mark_safe(
            '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:300px;">'
            '<div style="font-size:28px;font-weight:800;color:#a855f7;">%d</div>'
            '<div style="font-size:13px;color:#888;margin-top:4px;">words with P(known) &gt; 50%%</div>'
            "</div>" % obj.words_known
        )

    @admin.display(description="Learning Rate")
    def rate_card(self, obj):
        r = obj.learning_rate
        if r == 0:
            txt = "No data yet"
            val = "\u2014"
            color = "#666"
        else:
            txt = "new words per day (7-day average)"
            val = "%.1f" % r
            color = "#3b82f6"
        return mark_safe(
            '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:300px;">'
            '<div style="font-size:28px;font-weight:800;color:%s;">%s</div>'
            '<div style="font-size:13px;color:#888;margin-top:4px;">%s</div>'
            "</div>" % (color, val, txt)
        )

    @admin.display(description="Retention Rate")
    def retention_card(self, obj):
        r = obj.retention_rate * 100
        if r == 0:
            return mark_safe(
                '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:300px;">'
                '<div style="font-size:18px;color:#666;">No long-term data yet</div>'
                '<div style="font-size:13px;color:#888;margin-top:4px;">Need 7+ days of learning to measure</div>'
                "</div>"
            )
        color = "#10b981" if r >= 70 else "#f59e0b" if r >= 50 else "#ef4444"
        label = "Excellent" if r >= 80 else "Good" if r >= 60 else "Needs work"
        return mark_safe(
            '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:300px;">'
            '<div style="font-size:28px;font-weight:800;color:%s;">%.0f%%</div>'
            '<div style="font-size:13px;color:#888;margin-top:4px;">%s &mdash; words retained after 7 days</div>'
            "</div>" % (color, r, label)
        )

    @admin.display(description="Weakness Map")
    def weakness_card(self, obj):
        categories = [
            ("Single Words", obj.weakness_single_word),
            ("Phrases", obj.weakness_phrase),
            ("Idioms", obj.weakness_idiom),
            ("Phrasal Verbs", obj.weakness_phrasal_verb),
            ("Abstract Words", obj.weakness_abstract),
        ]
        rows = ""
        for label, val in categories:
            # -1 to +1 → 0 to 100
            display_pct = (val + 1) * 50
            if val < -0.2:
                color = "#ef4444"
                status = "Weak \u2014 needs more practice"
            elif val > 0.3:
                color = "#10b981"
                status = "Strong"
            elif val == 0:
                color = "#444"
                status = "No data"
            else:
                color = "#f59e0b"
                status = "Average"
            rows += (
                '<div style="margin-bottom:12px;">'
                '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                '<span style="font-size:13px;font-weight:600;color:#ddd;">%s</span>'
                '<span style="font-size:11px;color:%s;font-weight:600;">%s</span>'
                "</div>"
                '<div style="width:100%%;height:8px;background:#2a2a2a;border-radius:4px;overflow:hidden;">'
                '<div style="width:%.0f%%;height:100%%;background:%s;border-radius:4px;transition:width 0.3s;"></div>'
                "</div>"
                "</div>" % (label, color, status, display_pct, color)
            )
        return mark_safe(
            '<div style="padding:16px 20px;background:#1a1a1a;border-radius:12px;border:1px solid #2a2a2a;max-width:400px;">'
            "%s"
            '<div style="font-size:11px;color:#666;margin-top:8px;border-top:1px solid #2a2a2a;padding-top:8px;">'
            "Score range: -1.0 (very weak) to +1.0 (very strong). "
            "The system quizzes weak areas 2x more often.</div>"
            "</div>" % rows
        )


@admin.register(ConfusionPair)
class ConfusionPairAdmin(admin.ModelAdmin):
    list_display = ("user", "pair_col", "count_col", "status_col", "last_confused")
    list_filter = ("resolved", "user")
    ordering = ("-confusion_count",)

    @admin.display(description="Confused Pair")
    def pair_col(self, obj):
        return mark_safe(
            '<strong>%s</strong> <span style="color:#f59e0b;">\u2194</span> <strong>%s</strong>'
            % (obj.word_a, obj.word_b)
        )

    @admin.display(description="Times")
    def count_col(self, obj):
        color = "#ef4444" if obj.confusion_count >= 3 else "#f59e0b"
        return mark_safe('<span style="color:%s;font-weight:700;">%dx</span>' % (color, obj.confusion_count))

    @admin.display(description="Status")
    def status_col(self, obj):
        if obj.resolved:
            return mark_safe('<span style="color:#10b981;font-weight:600;">\u2713 Resolved</span>')
        return mark_safe('<span style="color:#ef4444;font-weight:600;">\u26a0 Active</span>')


# ─────────────────────────────────────────────
# Build (Sentence Construction Game)
# ─────────────────────────────────────────────


@admin.register(SentencePattern)
class SentencePatternAdmin(admin.ModelAdmin):
    list_display = ("level_col", "patterns_col", "text_short", "complexity_score", "episode_col")
    list_filter = ("difficulty_level", "primary_pattern", "source")
    search_fields = ("text",)
    ordering = ("difficulty_level", "complexity_score")

    @admin.display(description="Level")
    def level_col(self, obj):
        colors = {
            1: "#10b981",
            2: "#3b82f6",
            3: "#3b82f6",
            4: "#f59e0b",
            5: "#f59e0b",
            6: "#a855f7",
            7: "#a855f7",
            8: "#ef4444",
        }
        c = colors.get(obj.difficulty_level, "#888")
        return mark_safe(f'<span style="color:{c};font-weight:700;">L{obj.difficulty_level}</span>')

    @admin.display(description="Patterns")
    def patterns_col(self, obj):
        tags = ""
        for p in obj.patterns:
            label = GRAMMAR_PATTERNS.get(p, {}).get("label", p)
            tags += f'<span style="background:#2a2a2a;padding:2px 6px;border-radius:4px;font-size:11px;margin-right:4px;">{label}</span>'
        return mark_safe(tags)

    @admin.display(description="Text")
    def text_short(self, obj):
        return obj.text[:80] + "..." if len(obj.text) > 80 else obj.text

    @admin.display(description="Episode")
    def episode_col(self, obj):
        if obj.season and obj.episode_number:
            return f"S{obj.season:02d}E{obj.episode_number:02d}"
        return "-"


@admin.register(PatternMastery)
class PatternMasteryAdmin(admin.ModelAdmin):
    list_display = ("user", "pattern_col", "mastery_bar", "stars_col", "lock_col", "attempts", "streak")
    list_filter = ("pattern", "unlocked", "user")
    ordering = ("user", "pattern")

    @admin.display(description="Pattern")
    def pattern_col(self, obj):
        label = GRAMMAR_PATTERNS.get(obj.pattern, {}).get("label", obj.pattern)
        lvl = GRAMMAR_PATTERNS.get(obj.pattern, {}).get("level", "?")
        return mark_safe(f'<span style="font-weight:600;">L{lvl} {label}</span>')

    @admin.display(description="Mastery")
    def mastery_bar(self, obj):
        return _bar(obj.mastery_score, color="#14b8a6")

    @admin.display(description="Stars")
    def stars_col(self, obj):
        return "*" * obj.stars if obj.stars else "-"

    @admin.display(description="Status")
    def lock_col(self, obj):
        if obj.unlocked:
            return mark_safe('<span style="color:#10b981;">Unlocked</span>')
        return mark_safe('<span style="color:#666;">Locked</span>')


@admin.register(BuildAttempt)
class BuildAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "pattern_tested",
        "game_type",
        "correct_col",
        "points_earned",
        "response_time_ms",
        "created_at",
    )
    list_filter = ("game_type", "correct", "pattern_tested", "user")
    ordering = ("-created_at",)

    @admin.display(description="Result")
    def correct_col(self, obj):
        if obj.correct:
            return mark_safe('<span style="color:#10b981;font-weight:600;">Correct</span>')
        return mark_safe('<span style="color:#ef4444;font-weight:600;">Wrong</span>')


# ─────────────────────────────────────────────
# Existing models
# ─────────────────────────────────────────────


@admin.register(WordCache)
class WordCacheAdmin(admin.ModelAdmin):
    list_display = ("word", "get_pos_display", "definition", "created_at")
    list_filter = ("pos", "created_at")
    search_fields = ("word", "definition")
    ordering = ("-created_at",)


@admin.register(QuoteMastery)
class QuoteMasteryAdmin(admin.ModelAdmin):
    list_display = ("user", "quote", "status", "review_count", "next_review")
    list_filter = ("status", "next_review")
    raw_id_fields = ("user", "quote")


@admin.register(WordNote)
class WordNoteAdmin(admin.ModelAdmin):
    list_display = ("word", "user", "stage", "confidence_col", "context_type", "created_at")
    list_filter = ("stage", "context_type", "created_at")
    search_fields = ("word", "user__username")

    @admin.display(description="Confidence")
    def confidence_col(self, obj):
        color = "#10b981" if obj.confidence >= 70 else "#f59e0b" if obj.confidence >= 30 else "#ef4444"
        return _bar(obj.confidence, color=color, width=60)


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


@admin.register(LineTranslation)
class LineTranslationAdmin(admin.ModelAdmin):
    list_display = ("episode_col", "english_col", "translation", "transcript_id")
    list_filter = ("season", "episode_number", "source")
    search_fields = ("transcript__text", "translation")
    ordering = ("season", "episode_number", "transcript__start_time")
    raw_id_fields = ("transcript", "source", "episode")
    list_per_page = 50

    @admin.display(description="Episode")
    def episode_col(self, obj):
        if obj.season:
            return f"S{obj.season:02d}E{obj.episode_number:02d}"
        return "-"

    @admin.display(description="English")
    def english_col(self, obj):
        text = obj.transcript.text[:80]
        return format_html("<strong>{}</strong>", text)


@admin.register(LineVocab)
class LineVocabAdmin(admin.ModelAdmin):
    list_display = ("english", "kind_col", "translation", "desc_short", "example_short", "episode_col")
    list_filter = ("kind", "season", "episode_number", "source")
    search_fields = ("english", "translation", "description", "example")
    ordering = ("season", "episode_number", "transcript__start_time")
    raw_id_fields = ("transcript", "source", "episode")
    list_per_page = 50

    KIND_COLORS = {
        "verb": "#10b981",
        "noun": "#3b82f6",
        "adj": "#a855f7",
        "adv": "#ec4899",
        "phrasal_verb": "#f59e0b",
        "phrase": "#6366f1",
        "pattern": "#14b8a6",
        "idiom": "#ef4444",
    }

    @admin.display(description="Type")
    def kind_col(self, obj):
        label = obj.get_kind_display()
        return _badge(label, self.KIND_COLORS.get(obj.kind, "#666"))

    @admin.display(description="Description")
    def desc_short(self, obj):
        text = obj.description or ""
        if not text:
            return "-"
        return text[:60] + "..." if len(text) > 60 else text

    @admin.display(description="Example")
    def example_short(self, obj):
        if not obj.example:
            return "-"
        return obj.example[:60] + "..." if len(obj.example) > 60 else obj.example

    @admin.display(description="Episode")
    def episode_col(self, obj):
        if obj.season:
            return f"S{obj.season:02d}E{obj.episode_number:02d}"
        return "-"


@admin.register(CoreWord)
class CoreWordAdmin(admin.ModelAdmin):
    list_display = ("word",)
    search_fields = ("word",)


# ─────────────────────────────────────────────
# Tracking & Analytics
# ─────────────────────────────────────────────


@admin.register(ShadowingLog)
class ShadowingLogAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "line_preview",
        "phases_col",
        "loops_col",
        "speed_col",
        "subs_col",
        "duration_col",
        "when_col",
    )
    list_filter = ("phases_completed", "reached_no_subs", "user")
    search_fields = ("line_text", "user__username")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "transcript", "source", "episode")
    list_per_page = 50

    @admin.display(description="Line")
    def line_preview(self, obj):
        text = obj.line_text or "—"
        short = text[:50] + "…" if len(text) > 50 else text
        return format_html('<span style="color:#ddd;">{}</span>', short)

    @admin.display(description="Phases")
    def phases_col(self, obj):
        color = "#10b981" if obj.phases_completed >= 4 else "#3b82f6" if obj.phases_completed >= 2 else "#f59e0b"
        return _bar(obj.phases_completed, max_val=5, color=color, width=60)

    @admin.display(description="Loops", ordering="total_loops")
    def loops_col(self, obj):
        if obj.total_loops >= 5:
            return mark_safe('<span style="color:#10b981;font-weight:700;">%d</span>' % obj.total_loops)
        return obj.total_loops

    @admin.display(description="Max Speed", ordering="max_speed")
    def speed_col(self, obj):
        s = obj.max_speed
        color = "#10b981" if s >= 1.5 else "#3b82f6" if s >= 1.0 else "#f59e0b"
        return mark_safe('<span style="color:%s;">%.1fx</span>' % (color, s))

    @admin.display(description="No Subs")
    def subs_col(self, obj):
        if obj.reached_no_subs:
            return mark_safe('<span style="color:#10b981;font-size:16px;">✓</span>')
        return mark_safe('<span style="color:#666;">—</span>')

    @admin.display(description="Duration")
    def duration_col(self, obj):
        m, s = divmod(obj.duration_sec, 60)
        if m > 0:
            return mark_safe('<span style="color:#888;">%dm %ds</span>' % (m, s))
        return mark_safe('<span style="color:#888;">%ds</span>' % s)

    @admin.display(description="When", ordering="created_at")
    def when_col(self, obj):
        return _time_ago(obj.created_at)


@admin.register(GrammarPracticeLog)
class GrammarPracticeLogAdmin(admin.ModelAdmin):
    list_display = ("user", "unit_col", "sentence_preview", "result_col", "attempts_col", "speed_col", "when_col")
    list_filter = ("correct", "unit_id", "user")
    search_fields = ("sentence_text", "unit_title", "user__username")
    ordering = ("-created_at",)
    raw_id_fields = ("user",)
    list_per_page = 50

    @admin.display(description="Unit")
    def unit_col(self, obj):
        return _badge("U%d %s" % (obj.unit_id, obj.unit_title), "#6366f1")

    @admin.display(description="Sentence")
    def sentence_preview(self, obj):
        text = obj.sentence_text or "—"
        short = text[:60] + "…" if len(text) > 60 else text
        return format_html('<span style="color:#ddd;">{}</span>', short)

    @admin.display(description="Result")
    def result_col(self, obj):
        if obj.correct:
            return mark_safe('<span style="color:#10b981;font-size:16px;">✓</span>')
        return mark_safe('<span style="color:#ef4444;font-size:16px;">✗</span>')

    @admin.display(description="Tries", ordering="attempts_on_sentence")
    def attempts_col(self, obj):
        n = obj.attempts_on_sentence
        if n >= 3:
            return mark_safe('<span style="color:#ef4444;font-weight:700;">%dx</span>' % n)
        return "%dx" % n

    @admin.display(description="Speed")
    def speed_col(self, obj):
        ms = obj.response_time_ms
        if ms == 0:
            return "—"
        secs = ms / 1000
        color = "#10b981" if secs < 3 else "#f59e0b" if secs < 8 else "#ef4444"
        return mark_safe('<span style="color:%s;">%.1fs</span>' % (color, secs))

    @admin.display(description="When", ordering="created_at")
    def when_col(self, obj):
        return _time_ago(obj.created_at)


@admin.register(DailyActivity)
class DailyActivityAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "date",
        "words_col",
        "quiz_col",
        "build_col",
        "shadow_col",
        "grammar_col",
        "lookups_col",
        "flashcard_col",
        "watch_col",
        "pages_col",
        "total_col",
    )
    list_filter = ("user", "date")
    ordering = ("-date", "user")
    list_per_page = 30

    @admin.display(description="Words")
    def words_col(self, obj):
        saved = obj.words_saved
        reviewed = obj.words_reviewed
        parts = []
        if saved:
            parts.append('<span style="color:#10b981;">+%d saved</span>' % saved)
        if reviewed:
            parts.append('<span style="color:#3b82f6;">%d reviewed</span>' % reviewed)
        return mark_safe(" · ".join(parts)) if parts else "—"

    @admin.display(description="Quiz")
    def quiz_col(self, obj):
        if not obj.quiz_attempts:
            return "—"
        pct = (obj.quiz_correct / obj.quiz_attempts * 100) if obj.quiz_attempts else 0
        color = "#10b981" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"
        return mark_safe(
            '<span style="color:%s;font-weight:600;">%d/%d</span>'
            '<span style="color:#666;font-size:11px;"> (%.0f%%)</span>'
            % (color, obj.quiz_correct, obj.quiz_attempts, pct)
        )

    @admin.display(description="Build")
    def build_col(self, obj):
        if not obj.build_attempts:
            return "—"
        pct = (obj.build_correct / obj.build_attempts * 100) if obj.build_attempts else 0
        color = "#10b981" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"
        return mark_safe(
            '<span style="color:%s;font-weight:600;">%d/%d</span>'
            '<span style="color:#666;font-size:11px;"> (%.0f%%)</span>'
            % (color, obj.build_correct, obj.build_attempts, pct)
        )

    @admin.display(description="Shadow")
    def shadow_col(self, obj):
        if not obj.shadow_sessions:
            return "—"
        parts = ['<span style="color:#a855f7;font-weight:600;">%d sess</span>' % obj.shadow_sessions]
        if obj.shadow_loops:
            parts.append('<span style="color:#888;">%d loops</span>' % obj.shadow_loops)
        if obj.shadow_minutes:
            parts.append('<span style="color:#888;">%dm</span>' % obj.shadow_minutes)
        return mark_safe(" · ".join(parts))

    @admin.display(description="Grammar")
    def grammar_col(self, obj):
        if not obj.grammar_attempts:
            return "—"
        pct = (obj.grammar_correct / obj.grammar_attempts * 100) if obj.grammar_attempts else 0
        color = "#10b981" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"
        return mark_safe(
            '<span style="color:%s;font-weight:600;">%d/%d</span>'
            '<span style="color:#666;font-size:11px;"> (%.0f%%)</span>'
            % (color, obj.grammar_correct, obj.grammar_attempts, pct)
        )

    @admin.display(description="Lookups")
    def lookups_col(self, obj):
        if not obj.lookups:
            return "—"
        return mark_safe('<span style="color:#ec4899;font-weight:600;">%d</span>' % obj.lookups)

    @admin.display(description="Flashcards")
    def flashcard_col(self, obj):
        if not obj.flashcard_reviews:
            return "—"
        pct = (obj.flashcard_known / obj.flashcard_reviews * 100) if obj.flashcard_reviews else 0
        color = "#10b981" if pct >= 70 else "#f59e0b" if pct >= 40 else "#ef4444"
        return mark_safe(
            '<span style="color:%s;font-weight:600;">%d/%d</span>'
            '<span style="color:#666;font-size:11px;"> (%.0f%%)</span>'
            % (color, obj.flashcard_known, obj.flashcard_reviews, pct)
        )

    @admin.display(description="Watch")
    def watch_col(self, obj):
        if not obj.watch_minutes:
            return "—"
        return mark_safe('<span style="color:#f59e0b;font-weight:600;">%dm</span>' % obj.watch_minutes)

    @admin.display(description="Pages")
    def pages_col(self, obj):
        if not obj.page_visits:
            return "—"
        return mark_safe('<span style="color:#888;">%d</span>' % obj.page_visits)

    @admin.display(description="Total Time")
    def total_col(self, obj):
        m = obj.total_minutes
        if not m:
            return "—"
        if m >= 60:
            return mark_safe('<span style="color:#10b981;font-weight:700;">%dh %dm</span>' % (m // 60, m % 60))
        color = "#10b981" if m >= 20 else "#3b82f6" if m >= 10 else "#888"
        return mark_safe('<span style="color:%s;font-weight:600;">%dm</span>' % (color, m))


@admin.register(LookupLog)
class LookupLogAdmin(admin.ModelAdmin):
    list_display = ("user", "word_col", "type_col", "context_short", "found_col", "saved_col", "when_col")
    list_filter = ("lookup_type", "result_found", "saved_to_notebook", "user")
    search_fields = ("word", "user__username", "context")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "source", "episode")
    list_per_page = 50

    @admin.display(description="Word", ordering="word")
    def word_col(self, obj):
        return format_html("<strong>{}</strong>", obj.word)

    @admin.display(description="Type")
    def type_col(self, obj):
        color = "#3b82f6" if obj.lookup_type == "dictionary" else "#a855f7"
        return _badge(obj.lookup_type.title(), color)

    @admin.display(description="Context")
    def context_short(self, obj):
        if not obj.context:
            return "—"
        return obj.context[:50] + "…" if len(obj.context) > 50 else obj.context

    @admin.display(description="Found")
    def found_col(self, obj):
        if obj.result_found:
            return mark_safe('<span style="color:#10b981;">✓</span>')
        return mark_safe('<span style="color:#ef4444;">✗</span>')

    @admin.display(description="Saved")
    def saved_col(self, obj):
        if obj.saved_to_notebook:
            return mark_safe('<span style="color:#10b981;">✓ saved</span>')
        return mark_safe('<span style="color:#666;">—</span>')

    @admin.display(description="When", ordering="created_at")
    def when_col(self, obj):
        return _time_ago(obj.created_at)


@admin.register(FlashcardAttempt)
class FlashcardAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "word_col", "result_col", "time_col", "flips", "batch_date", "when_col")
    list_filter = ("knew_it", "batch_date", "user")
    search_fields = ("word", "user__username")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "word_note")
    list_per_page = 50

    @admin.display(description="Word", ordering="word")
    def word_col(self, obj):
        return format_html("<strong>{}</strong>", obj.word)

    @admin.display(description="Result")
    def result_col(self, obj):
        if obj.knew_it:
            return _badge("Knew it", "#10b981")
        return _badge("Again", "#ef4444")

    @admin.display(description="Time on card")
    def time_col(self, obj):
        if not obj.time_on_card_ms:
            return "—"
        secs = obj.time_on_card_ms / 1000
        color = "#10b981" if secs < 5 else "#f59e0b" if secs < 15 else "#ef4444"
        return mark_safe('<span style="color:%s;">%.1fs</span>' % (color, secs))

    @admin.display(description="When", ordering="created_at")
    def when_col(self, obj):
        return _time_ago(obj.created_at)


@admin.register(PageVisit)
class PageVisitAdmin(admin.ModelAdmin):
    list_display = ("user", "page_col", "path", "duration_col", "referrer_page", "when_col")
    list_filter = ("page", "user")
    search_fields = ("user__username", "page", "path")
    ordering = ("-created_at",)
    raw_id_fields = ("user", "source")
    list_per_page = 50

    PAGE_COLORS = {
        "watch": "#ff6b35",
        "notebook": "#6366f1",
        "quiz": "#3b82f6",
        "build": "#14b8a6",
        "grammar": "#a855f7",
        "coach": "#10b981",
        "practice": "#f59e0b",
        "review": "#ec4899",
        "progress": "#3b82f6",
        "shadowing": "#8b5cf6",
        "home": "#888",
        "onboarding": "#f59e0b",
    }

    @admin.display(description="Page")
    def page_col(self, obj):
        color = self.PAGE_COLORS.get(obj.page, "#666")
        return _badge(obj.page, color)

    @admin.display(description="Duration")
    def duration_col(self, obj):
        s = obj.duration_sec
        if not s:
            return mark_safe('<span style="color:#666;">—</span>')
        if s >= 60:
            m, sec = divmod(s, 60)
            return mark_safe('<span style="color:#10b981;font-weight:600;">%dm %ds</span>' % (m, sec))
        color = "#10b981" if s >= 30 else "#f59e0b" if s >= 10 else "#888"
        return mark_safe('<span style="color:%s;">%ds</span>' % (color, s))

    @admin.display(description="When", ordering="created_at")
    def when_col(self, obj):
        return _time_ago(obj.created_at)
