import math

from django.conf import settings
from django.db import models
from django.utils import timezone

from clips.models import Episode, Quote, Source, Transcript


class FavoriteQuote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites")
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="learning_favorites")
    created_at = models.DateTimeField(auto_now_add=True)

    # Personal context
    personal_note = models.TextField(blank=True)
    emotion_tag = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ("funny", "😂 Funny"),
            ("sad", "😢 Sad"),
            ("angry", "😡 Angry"),
            ("romantic", "😍 Romantic"),
            ("confused", "🤔 Confused"),
            ("excited", "🎉 Excited"),
            ("frustrated", "😤 Frustrated"),
        ],
    )

    class Meta:
        unique_together = ("user", "quote")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} → {self.quote}"


class QuoteMastery(models.Model):
    STATUS_CHOICES = [
        ("saved", "📌 Saved"),
        ("learning", "🔄 Learning"),
        ("mastered", "✅ Mastered"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="masteries")
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="masteries")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="saved")
    review_count = models.PositiveIntegerField(default=0)
    last_reviewed = models.DateTimeField(null=True, blank=True)
    next_review = models.DateTimeField(null=True, blank=True)

    # Spaced repetition interval in days
    interval_days = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("user", "quote")
        ordering = ["next_review"]

    def __str__(self):
        return f"{self.user.username} — {self.quote} [{self.status}]"


class LearningProgress(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="progress")

    # Overall stats
    total_quotes_reviewed = models.PositiveIntegerField(default=0)
    total_words_noted = models.PositiveIntegerField(default=0)
    total_session_minutes = models.PositiveIntegerField(default=0)
    total_cloze_attempts = models.PositiveIntegerField(default=0)
    total_cloze_correct = models.PositiveIntegerField(default=0)

    # Mastery counts (denormalized for performance)
    saved_count = models.PositiveIntegerField(default=0)
    learning_count = models.PositiveIntegerField(default=0)
    mastered_count = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    @property
    def cloze_accuracy(self):
        if self.total_cloze_attempts == 0:
            return 0
        return round((self.total_cloze_correct / self.total_cloze_attempts) * 100)

    def __str__(self):
        return f"{self.user.username} — Progress"


class SourceProgress(models.Model):
    """Tracks how much of a source (show/movie) the user has engaged with"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="source_progress")
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="user_progress")

    quotes_seen = models.PositiveIntegerField(default=0)
    quotes_favorited = models.PositiveIntegerField(default=0)
    quotes_mastered = models.PositiveIntegerField(default=0)
    last_watched = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user", "source")

    def __str__(self):
        return f"{self.user.username} — {self.source.title}"


class OnboardingSession(models.Model):
    """Tracks a user's vocabulary assessment session."""

    LEVEL_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("upper_intermediate", "Upper-Intermediate"),
        ("advanced", "Advanced"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="onboarding")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    words_shown = models.PositiveIntegerField(default=0)
    words_known = models.PositiveIntegerField(default=0)
    projected_total = models.PositiveIntegerField(default=0)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, blank=True)
    tier_breakdown = models.JSONField(default=dict)
    weak_tiers = models.JSONField(default=list)
    strong_tiers = models.JSONField(default=list)
    grammar_breakdown = models.JSONField(default=dict, blank=True)
    grammar_level = models.CharField(max_length=20, blank=True, default="")
    phrases_breakdown = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.user.username} — Onboarding ({self.level or 'incomplete'})"


class OnboardingResult(models.Model):
    """Stores the user's response (know/don't know) for each word in the assessment."""

    session = models.ForeignKey(OnboardingSession, on_delete=models.CASCADE, related_name="results")
    word = models.ForeignKey("vocab.VocabWord", on_delete=models.CASCADE)
    known = models.BooleanField()
    response_time_ms = models.PositiveIntegerField(default=0)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("session", "word")

    def __str__(self):
        mark = "\u2713" if self.known else "\u2717"
        return f"{self.session.user.username} — '{self.word.word}' {mark}"


class UserLearningProfile(models.Model):
    """Aggregated learning analytics per user — Elo ability, weakness vector, stats."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="learning_profile")

    # Elo ability rating (starts 1000)
    ability_rating = models.FloatField(default=1000)

    # Weakness vector: -1.0 (very weak) to +1.0 (strong)
    weakness_phrasal_verb = models.FloatField(default=0.0)
    weakness_idiom = models.FloatField(default=0.0)
    weakness_single_word = models.FloatField(default=0.0)
    weakness_abstract = models.FloatField(default=0.0)
    weakness_phrase = models.FloatField(default=0.0)

    # Aggregate stats
    words_known = models.PositiveIntegerField(default=0, help_text="Count of words with P(L)>0.5")
    learning_rate = models.FloatField(default=0.0, help_text="Words learned per day (7-day avg)")
    retention_rate = models.FloatField(default=0.0, help_text="Fraction of learned words retained after 7 days")
    total_attempts = models.PositiveIntegerField(default=0)
    total_correct = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        pass

    def __str__(self):
        return f"{self.user.username} — Elo={self.ability_rating:.0f}, Known={self.words_known}"

    def update_weakness(self, category, correct):
        """Update weakness vector for a category. Asymmetric: 2x penalty for wrong."""
        field = f"weakness_{category}"
        if not hasattr(self, field):
            return
        score = getattr(self, field)
        if correct:
            score += 0.1 * (1 - score)
        else:
            score -= 0.2 * (1 + score)
        setattr(self, field, max(-1.0, min(1.0, score)))

    def update_elo(self, word_mastery, correct):
        """Elo update for both user ability and word difficulty. Decreasing K-factor."""
        k = 32 / (1 + self.total_attempts / 100)
        expected = 1 / (1 + 10 ** ((word_mastery.difficulty - self.ability_rating) / 400))
        if correct:
            self.ability_rating += k * (1 - expected)
            word_mastery.difficulty -= k * (1 - expected)
        else:
            self.ability_rating -= k * expected
            word_mastery.difficulty += k * expected

    def get_weakness_multiplier(self, kind):
        """Get weakness multiplier (1.0-2.0) for a word category."""
        mapping = {
            "word": "weakness_single_word",
            "phrase": "weakness_phrase",
            "idiom": "weakness_idiom",
        }
        field = mapping.get(kind, "weakness_single_word")
        score = getattr(self, field, 0.0)
        return 1.0 + max(0, -score)  # negative score = weak = higher multiplier

    @property
    def accuracy(self):
        if self.total_attempts == 0:
            return 0
        return round(self.total_correct / self.total_attempts * 100, 1)


class ConfusionPair(models.Model):
    """Tracks pairs of words the user consistently confuses."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="confusion_pairs")
    word_a = models.CharField(max_length=200, help_text="The word being quizzed")
    word_b = models.CharField(max_length=200, help_text="The word user confuses it with")
    confusion_count = models.PositiveIntegerField(default=1)
    last_confused = models.DateTimeField(auto_now=True)
    resolved = models.BooleanField(default=False, help_text="True once user can distinguish them")

    class Meta:
        unique_together = ("user", "word_a", "word_b")
        ordering = ["-confusion_count"]

    def __str__(self):
        status = "resolved" if self.resolved else f"confused {self.confusion_count}x"
        return f"{self.user.username}: {self.word_a} ↔ {self.word_b} ({status})"


# ─────────────────────────────────────────────────────
# SENTENCE BUILD GAME — Grammar-in-Context
# ─────────────────────────────────────────────────────

# Pattern definitions: id, regex pattern, weight, display name
GRAMMAR_PATTERNS = {
    "because_so_but": {
        "label": "because / so / but",
        "weight": 1.0,
        "level": 1,
        "branch": "A",
    },
    "which_that": {
        "label": "which / that (relative)",
        "weight": 1.5,
        "level": 2,
        "branch": "A",
    },
    "who_whose_whom": {
        "label": "who / whose / whom",
        "weight": 2.0,
        "level": 3,
        "branch": "A",
    },
    "where_when": {
        "label": "where / when (relative)",
        "weight": 2.0,
        "level": 2,
        "branch": "B",
    },
    "although_even_though": {
        "label": "although / even though",
        "weight": 2.5,
        "level": 4,
        "branch": "B",
    },
    "if_unless": {
        "label": "if / unless / as long as",
        "weight": 2.5,
        "level": 5,
        "branch": "merged",
    },
    "what_clause": {
        "label": "what (noun clause)",
        "weight": 3.0,
        "level": 6,
        "branch": "merged",
    },
    "however_therefore": {
        "label": "however / therefore / meanwhile",
        "weight": 3.0,
        "level": 7,
        "branch": "merged",
    },
    "multi_clause": {
        "label": "multi-clause stacking",
        "weight": 4.0,
        "level": 8,
        "branch": "merged",
    },
}

# Unlock tree: pattern → prerequisite patterns (all must be P(L)≥0.6)
PATTERN_UNLOCK_TREE = {
    "because_so_but": [],  # always unlocked
    "which_that": ["because_so_but"],
    "where_when": ["because_so_but"],
    "who_whose_whom": ["which_that"],
    "although_even_though": ["where_when"],
    "if_unless": ["who_whose_whom", "although_even_though"],
    "what_clause": ["if_unless"],
    "however_therefore": ["what_clause"],
    "multi_clause": ["however_therefore"],
}

UNLOCK_THRESHOLD = 0.6  # P(L) required to unlock next pattern


class SentencePattern(models.Model):
    """Auto-detected grammar pattern in a transcript line. One per transcript × pattern."""

    transcript = models.ForeignKey("clips.Transcript", on_delete=models.CASCADE, related_name="sentence_patterns")
    source = models.ForeignKey("clips.Source", on_delete=models.CASCADE, related_name="sentence_patterns")
    episode = models.ForeignKey(
        "clips.Episode", on_delete=models.CASCADE, related_name="sentence_patterns", null=True, blank=True
    )

    # Detected patterns as JSON list: ["because_so_but", "which_that"]
    patterns = models.JSONField(default=list, help_text="List of detected pattern IDs")
    # Sentence text (cached from transcript for quick access)
    text = models.TextField()
    # Uzbek translation (from LineTranslation if available)
    translation = models.TextField(blank=True)

    # Computed difficulty
    complexity_score = models.FloatField(default=0, help_text="Weighted sum of pattern weights × clause multiplier")
    difficulty_level = models.PositiveIntegerField(default=1, help_text="1-8 difficulty bracket")
    clause_count = models.PositiveIntegerField(default=1)

    # Primary pattern — the highest-weight pattern in this sentence (for filtering)
    primary_pattern = models.CharField(max_length=30, blank=True, db_index=True)

    season = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        unique_together = ("transcript", "primary_pattern")
        ordering = ["complexity_score"]

    def __str__(self):
        return f"[L{self.difficulty_level}] {self.text[:60]}..."


class PatternMastery(models.Model):
    """Per-user mastery of each grammar pattern using BKT."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pattern_masteries")
    pattern = models.CharField(max_length=30, db_index=True, help_text="Pattern ID from GRAMMAR_PATTERNS")

    # BKT core
    p_known = models.FloatField(default=0.1, help_text="P(L) — Bayesian knowledge probability")
    mastery_score = models.FloatField(default=0)

    # Stars: 0-3 (⭐ at 0.4, ⭐⭐ at 0.65, ⭐⭐⭐ at 0.85)
    stars = models.PositiveIntegerField(default=0)

    # Unlock state
    unlocked = models.BooleanField(default=False)

    # Stats
    streak = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    last_played = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user", "pattern")
        ordering = ["pattern"]

    def __str__(self):
        stars_str = "⭐" * self.stars
        lock = "" if self.unlocked else "🔒"
        return f"{self.user.username} — {self.pattern} P={self.p_known:.2f} {stars_str} {lock}"

    def record_answer(self, correct, response_time_ms=0):
        """BKT update for grammar pattern. Uses cloze-like params (harder than MCQ)."""
        p_guess = 0.15  # Some patterns can be guessed from structure
        p_slip = 0.10

        # Bayesian posterior
        if correct:
            num = self.p_known * (1 - p_slip)
            den = num + (1 - self.p_known) * p_guess
        else:
            num = self.p_known * p_slip
            den = num + (1 - self.p_known) * (1 - p_guess)

        posterior = num / max(den, 1e-9)

        if correct:
            p_new = posterior + (1 - posterior) * BKT_P_TRANSITION
        else:
            p_new = posterior + (1 - posterior) * BKT_P_TRANSITION * 0.1

        # Time nudge
        if correct and response_time_ms > 0:
            time_bonus = max(-0.03, min(0.03, (5000 - response_time_ms) / 80000))
            p_new = max(0.0, min(1.0, p_new + time_bonus))

        self.p_known = max(0.0, min(1.0, p_new))
        self.mastery_score = self.p_known * 100

        # Stars
        if self.p_known >= 0.85:
            self.stars = 3
        elif self.p_known >= 0.65:
            self.stars = 2
        elif self.p_known >= 0.4:
            self.stars = 1
        else:
            self.stars = 0

        # Streak
        self.attempts += 1
        if correct:
            self.streak += 1
            self.correct_count += 1
        else:
            self.streak = 0

        self.last_played = timezone.now()
        self.save()

    def check_unlock_children(self, user):
        """After this pattern's P(L) changes, check if any children should unlock."""
        for child_pattern, prereqs in PATTERN_UNLOCK_TREE.items():
            if self.pattern not in prereqs:
                continue
            # Check all prereqs are met
            all_met = True
            for prereq in prereqs:
                try:
                    pm = PatternMastery.objects.get(user=user, pattern=prereq)
                    if pm.p_known < UNLOCK_THRESHOLD:
                        all_met = False
                        break
                except PatternMastery.DoesNotExist:
                    all_met = False
                    break

            if all_met:
                child, _ = PatternMastery.objects.get_or_create(user=user, pattern=child_pattern)
                if not child.unlocked:
                    child.unlocked = True
                    child.save()


class BuildAttempt(models.Model):
    """Records every sentence-build game answer."""

    GAME_TYPES = [
        ("meaning", "Meaning Match"),
        ("fix", "Fix the Sentence"),
        ("connect", "Pick Connector"),
        ("complete", "Type Connector"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="build_attempts")
    sentence_pattern = models.ForeignKey(SentencePattern, on_delete=models.CASCADE, related_name="attempts")
    pattern_tested = models.CharField(max_length=30, help_text="Which pattern was the focus")
    game_type = models.CharField(max_length=10, choices=GAME_TYPES)
    correct = models.BooleanField()
    user_answer = models.TextField(blank=True)
    response_time_ms = models.PositiveIntegerField(default=0)
    points_earned = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        mark = "✓" if self.correct else "✗"
        return f"{self.user.username} — {self.pattern_tested} ({self.game_type}) {mark}"


# ─────────────────────────────────────────────────────
# SHADOWING LOG — Tracks every shadowing interaction
# ─────────────────────────────────────────────────────


class ShadowingLog(models.Model):
    """Records each shadowing session on a transcript line.

    Written by JS on the video page every time the user completes a
    shadowing pass (all 4 phases or exits early).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shadowing_logs")
    transcript = models.ForeignKey(Transcript, on_delete=models.CASCADE, related_name="shadowing_logs")
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="shadowing_logs")
    episode = models.ForeignKey(
        Episode, on_delete=models.CASCADE, related_name="shadowing_logs", null=True, blank=True
    )

    # How far the student got
    phases_completed = models.PositiveIntegerField(
        default=0,
        help_text="How many of the 4 phases completed (0=started only, 4=all done)",
    )
    total_loops = models.PositiveIntegerField(default=0, help_text="Total loop iterations across all phases")
    max_speed = models.FloatField(default=0.5, help_text="Highest playback speed reached (0.5 / 0.75 / 1.0)")
    reached_no_subs = models.BooleanField(
        default=False, help_text="True if student reached the final no-subtitles phase"
    )
    duration_sec = models.PositiveIntegerField(default=0, help_text="Wall-clock seconds spent in shadowing mode")

    # Line context
    line_text = models.TextField(blank=True, help_text="The transcript line text (cached)")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "transcript"]),
        ]

    def __str__(self):
        return (
            f"{self.user.username} — shadow {self.phases_completed}/4 phases "
            f"({self.total_loops} loops) @ {self.max_speed}x"
        )


# ─────────────────────────────────────────────────────
# PRONUNCIATION ASSESSMENT — Premium speaking score
# ─────────────────────────────────────────────────────


class PronunciationAssessment(models.Model):
    """One assessment per shadow attempt scored by Azure Speech.

    Stores the audio plus all per-line, per-word, and per-phoneme scores
    Microsoft returns. This is the source of truth for the premium
    "Your accent" features (trajectory chart, weak-phoneme drill picker,
    side-by-side native-vs-you playback).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="pronunciation_assessments",
    )
    shadow_log = models.ForeignKey(
        "ShadowingLog",
        on_delete=models.CASCADE,
        related_name="assessments",
        null=True,
        blank=True,
        help_text="Shadow session this assessment belongs to (if any)",
    )
    transcript = models.ForeignKey(
        Transcript,
        on_delete=models.CASCADE,
        related_name="pronunciation_assessments",
    )

    # Recorded audio (kept so users can play back their attempts)
    audio_file = models.FileField(upload_to="pronunciation/%Y/%m/", null=True, blank=True)
    audio_duration_ms = models.PositiveIntegerField(default=0)

    # Azure Pronunciation Assessment top-level scores (all 0–100)
    accuracy_score = models.FloatField(default=0, help_text="Phoneme-level pronunciation accuracy")
    fluency_score = models.FloatField(default=0, help_text="Smoothness / pacing")
    completeness_score = models.FloatField(default=0, help_text="Did the user say all the words?")
    prosody_score = models.FloatField(null=True, blank=True, help_text="Rhythm/stress (Azure prosody, optional)")
    overall_score = models.FloatField(default=0, help_text="Composite score, what the UI surfaces as headline")

    # Per-word breakdown — list of dicts:
    # [{"word": "betray", "accuracy": 87, "error_type": null}, …]
    word_scores = models.JSONField(default=list, blank=True)

    # Phonemes that scored < 60 (weak-spot drill targets):
    # ["th-voiceless", "r", "ʌ"]
    weak_phonemes = models.JSONField(default=list, blank=True)

    # Full Azure response — kept for replay/debugging/future features
    raw_response = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "transcript"]),
        ]

    def __str__(self):
        return f"{self.user.username} — pronunciation {self.overall_score:.0f}/100"


class PhonemeWeakness(models.Model):
    """Per-user rolling tracker of pronunciation weak spots.

    Updated on each PronunciationAssessment write — drives the
    "Drill the 'th' sound" recommendation flow.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phoneme_weaknesses",
    )
    phoneme = models.CharField(max_length=20)
    attempts = models.PositiveIntegerField(default=0)
    fails = models.PositiveIntegerField(default=0, help_text="Times this phoneme scored < 60")
    avg_accuracy = models.FloatField(default=0)
    last_failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("user", "phoneme")]
        indexes = [
            models.Index(fields=["user", "-fails"]),
        ]

    def __str__(self):
        return f"{self.user.username} — /{self.phoneme}/ {self.avg_accuracy:.0f}%"


# ─────────────────────────────────────────────────────
# GRAMMAR PRACTICE LOG — Tracks grammar unit practice
# ─────────────────────────────────────────────────────


class GrammarPracticeLog(models.Model):
    """Records grammar unit sentence-builder practice results.

    Written by JS in grammar_unit.html when the student completes
    a sentence or finishes a unit.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="grammar_logs")
    unit_id = models.PositiveIntegerField(help_text="Grammar unit number (1-8)")
    unit_title = models.CharField(max_length=100)

    # Per-sentence results
    sentence_index = models.PositiveIntegerField(help_text="Which sentence in the unit (0-based)")
    sentence_text = models.TextField(help_text="The target sentence")
    correct = models.BooleanField()
    attempts_on_sentence = models.PositiveIntegerField(default=1, help_text="How many tries before getting it right")
    response_time_ms = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "unit_id"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        mark = "✓" if self.correct else "✗"
        return f"{self.user.username} — Unit {self.unit_id} Q{self.sentence_index} {mark}"


# ─────────────────────────────────────────────────────
# DAILY ACTIVITY — Single-row daily aggregation
# ─────────────────────────────────────────────────────


class DailyActivity(models.Model):
    """One row per user per day. Aggregates all learning metrics.

    Updated incrementally throughout the day (not recomputed from scratch).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="daily_activities")
    date = models.DateField()

    # Watch
    watch_minutes = models.FloatField(default=0, help_text="Cumulative minutes spent watching video")

    # Words
    words_saved = models.PositiveIntegerField(default=0)
    words_reviewed = models.PositiveIntegerField(default=0)

    # Quiz
    quiz_attempts = models.PositiveIntegerField(default=0)
    quiz_correct = models.PositiveIntegerField(default=0)

    # Build (sentence game)
    build_attempts = models.PositiveIntegerField(default=0)
    build_correct = models.PositiveIntegerField(default=0)

    # Shadowing
    shadow_sessions = models.PositiveIntegerField(default=0)
    shadow_loops = models.PositiveIntegerField(default=0)
    shadow_minutes = models.FloatField(default=0)

    # Grammar
    grammar_attempts = models.PositiveIntegerField(default=0)
    grammar_correct = models.PositiveIntegerField(default=0)

    # Lookups
    lookups = models.PositiveIntegerField(default=0, help_text="Dictionary + translation lookups")

    # Flashcards (Today's Words)
    flashcard_reviews = models.PositiveIntegerField(default=0)
    flashcard_known = models.PositiveIntegerField(default=0)

    # Page visits
    page_visits = models.PositiveIntegerField(default=0)

    # Sessions
    session_count = models.PositiveIntegerField(default=0)
    total_minutes = models.FloatField(default=0, help_text="Total active learning minutes")

    # Addiction layer — retention mechanics
    xp_earned = models.PositiveIntegerField(default=0, help_text="XP earned this day (drives goal ring)")
    words_mastered = models.PositiveIntegerField(default=0, help_text="Words that leveled up today")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["user", "date"]),
        ]

    def __str__(self):
        return f"{self.user.username} — {self.date} ({self.total_minutes:.0f}m)"

    @property
    def quiz_accuracy(self):
        if self.quiz_attempts == 0:
            return 0
        return round(self.quiz_correct / self.quiz_attempts * 100)

    @classmethod
    def increment(cls, user, **fields):
        """Increment counters for today. Creates row if needed.

        Side effect: bumps the user's streak when any field in
        STREAK_QUALIFYING_FIELDS is incremented with a non-zero value.
        Page-visit-only increments do NOT bump the streak (navigation
        without study should never count). The streak helper is
        idempotent so multiple increments per day are safe.
        """
        from django.utils import timezone

        # Use locale-aware date so streak day boundaries match the user's
        # perceived "today" rather than the server's UTC clock.
        today = timezone.localdate()
        obj, _created = cls.objects.get_or_create(user=user, date=today)
        for field, delta in fields.items():
            setattr(obj, field, getattr(obj, field) + delta)
        obj.save(update_fields=list(fields.keys()) + ["updated_at"])

        # Bump streak only when this increment represents real study activity.
        from learning.utils.activity import STREAK_QUALIFYING_FIELDS, bump_streak_for_today

        if any(f in STREAK_QUALIFYING_FIELDS and v for f, v in fields.items()):
            bump_streak_for_today(user)


# ─────────────────────────────────────────────────────
# LOOKUP LOG — Tracks dictionary & translation lookups
# ─────────────────────────────────────────────────────


class LookupLog(models.Model):
    """Records every dictionary/translation lookup a user performs."""

    LOOKUP_TYPES = [
        ("dictionary", "Dictionary"),
        ("translate", "Translation"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lookup_logs")
    word = models.CharField(max_length=200)
    lookup_type = models.CharField(max_length=12, choices=LOOKUP_TYPES)
    context = models.TextField(blank=True, help_text="Sentence context if available")
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="lookup_logs")
    episode = models.ForeignKey(Episode, on_delete=models.SET_NULL, null=True, blank=True, related_name="lookup_logs")
    result_found = models.BooleanField(default=True, help_text="Whether a result was returned")
    saved_to_notebook = models.BooleanField(default=False, help_text="Did user save this word after lookup?")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "word"]),
        ]

    def __str__(self):
        return f"{self.user.username} — {self.lookup_type} '{self.word}'"


# ─────────────────────────────────────────────────────
# FLASHCARD ATTEMPT — Tracks Today's Words study mode
# ─────────────────────────────────────────────────────


class FlashcardAttempt(models.Model):
    """Records each flashcard flip/answer in Today's Words study mode."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="flashcard_attempts")
    word_note = models.ForeignKey("vocab.WordNote", on_delete=models.CASCADE, related_name="flashcard_attempts")
    word = models.CharField(max_length=200, help_text="Cached word text")
    knew_it = models.BooleanField(help_text="True if user marked 'I know this'")
    time_on_card_ms = models.PositiveIntegerField(default=0, help_text="Milliseconds spent on this card")
    flips = models.PositiveIntegerField(default=1, help_text="How many times the card was flipped")
    batch_date = models.DateField(help_text="Which day's batch this belongs to")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "batch_date"]),
            models.Index(fields=["user", "word_note"]),
        ]

    def __str__(self):
        mark = "✓" if self.knew_it else "✗"
        return f"{self.user.username} — flashcard '{self.word}' {mark}"


# ─────────────────────────────────────────────────────
# PAGE VISIT — Tracks which pages user visits & time spent
# ─────────────────────────────────────────────────────


class PageVisit(models.Model):
    """Records each page load and approximate time spent."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="page_visits")
    page = models.CharField(max_length=100, help_text="Page identifier (e.g. 'quiz', 'watch', 'notebook')")
    path = models.CharField(max_length=500, help_text="Full URL path")
    source = models.ForeignKey(Source, on_delete=models.SET_NULL, null=True, blank=True, related_name="page_visits")
    duration_sec = models.PositiveIntegerField(default=0, help_text="Seconds spent on page (updated on leave)")
    referrer_page = models.CharField(max_length=100, blank=True, help_text="Which page user came from")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "page"]),
            models.Index(fields=["user", "created_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} — {self.page} ({self.duration_sec}s)"


# ─────────────────────────────────────────────────────
# USER INTEREST PROFILE
# ─────────────────────────────────────────────────────


class UserInterest(models.Model):
    """User's selected interest category (football, music, movies, etc).

    Each user can have multiple categories. For each category, the user picks
    preset items and/or writes their own — stored in `items` JSON as
    [{"label": "Barcelona", "source": "preset"}, {"label": "Lamine Yamal", "source": "custom"}].
    """

    CATEGORY_CHOICES = [
        ("forever_movie", "Forever Movie"),  # the one movie/show they watch over and over
        ("football", "Football"),
        ("music", "Music"),
        ("movies", "Movies & TV"),
        ("food", "Food"),
        ("gaming", "Gaming"),
        ("travel", "Travel"),
        ("tech", "Technology"),
        ("books", "Books"),
        ("fashion", "Fashion"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="interests")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    items = models.JSONField(default=list, blank=True, help_text='[{"label":"Barcelona","source":"preset"}, ...]')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "category")
        ordering = ["category"]

    def __str__(self):
        return f"{self.user.username} — {self.category} ({len(self.items)} items)"


class GrammarAIExplanation(models.Model):
    """Cached Gemini explanation for why a specific wrong option on an MCQ is wrong.

    One row per (unit_id, question_index, chosen_option). Shared across all users —
    the explanation depends on the question + the wrong choice, not on the user.
    Generated lazily on the first student to hit each wrong path.
    """

    unit_id = models.PositiveSmallIntegerField()
    question_index = models.PositiveIntegerField(
        help_text="Zero-based index into the unit's question pool in grammar_questions.json"
    )
    chosen_option = models.PositiveSmallIntegerField(help_text="Index of the wrong option the learner picked")
    explanation_uz = models.TextField(help_text="Gemini-generated explanation in Uzbek (Latin script), 1-3 sentences")
    created_at = models.DateTimeField(auto_now_add=True)
    # Track hits so we know which explanations students actually see — useful for
    # pruning rarely-used entries or flagging popular ones for editorial polish.
    hit_count = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("unit_id", "question_index", "chosen_option")
        indexes = [models.Index(fields=["unit_id", "question_index"])]

    def __str__(self):
        return f"unit {self.unit_id} · q{self.question_index} · opt{self.chosen_option}"


class SceneCoverage(models.Model):
    """Per-user vocabulary coverage on a single transcript line / scene.

    Used by the "Ready to Watch" feature: surfaces only the scenes where
    the user already knows enough words to comprehend on the first pass.
    Pedagogy: Krashen's i+1 — input slightly above current level produces
    fastest acquisition. At ≥88% coverage, the user understands enough
    that the unknown 1-2 words are LEARNABLE from context, not blockers.

    Recomputed by the `calculate_coverage` management command (cheap loop
    over all transcripts × all users; can run nightly via cron).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="scene_coverages",
    )
    transcript = models.ForeignKey(
        "clips.Transcript",
        on_delete=models.CASCADE,
        related_name="coverages",
    )
    total_tokens = models.IntegerField(default=0)
    known_tokens = models.IntegerField(default=0)
    coverage = models.FloatField(default=0.0)  # 0.0 - 1.0
    last_calculated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "transcript")
        indexes = [
            models.Index(fields=["user", "-coverage"]),
            models.Index(fields=["user", "transcript"]),
        ]

    def __str__(self):
        return f"{self.user.username} · transcript={self.transcript_id} · {int(self.coverage*100)}%"
