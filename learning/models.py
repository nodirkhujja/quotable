from django.conf import settings
from django.db import models

from clips.models import Quote, Source, Transcript


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


class WordNote(models.Model):

    class PostType(models.TextChoices):
        VERB = "v", "Verb"
        NOUN = "n", "Noun"
        ADJECTIVE = "adj", "Adjective"
        PHRASE = "phr", "Phrase"
        OTHER = "etc", "Other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="word_notes")
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="word_notes", null=True, blank=True)
    transcript = models.ForeignKey(
        Transcript, on_delete=models.CASCADE, related_name="word_notes", null=True, blank=True
    )

    word = models.CharField(max_length=100)
    pos = models.CharField(max_length=5, choices=PostType.choices, default=PostType.OTHER)
    definition = models.TextField(blank=True)
    translation = models.CharField(max_length=500, blank=True, default="")
    stage = models.CharField(
        max_length=20,
        choices=[("inbox", "Inbox"), ("learning", "Learning"), ("mastered", "Mastered")],
        default="inbox",
    )
    confidence = models.IntegerField(default=0)
    mood = models.CharField(
        max_length=20,
        choices=[
            ("sarcastic", "Sarcastic"),
            ("angry", "Angry"),
            ("funny", "Funny"),
            ("romantic", "Romantic"),
        ],
        blank=True,
    )
    emotion_vibe = models.CharField(
        max_length=20,
        blank=True,
        choices=[
            ("nostalgic", "Nostalgic"),
            ("thrilling", "Thrilling"),
            ("inspiring", "Inspiring"),
            ("humorous", "Humorous"),
            ("tense", "Tense"),
            ("heartwarming", "Heartwarming"),
        ],
    )
    personal_note = models.TextField(blank=True)
    example_usage = models.TextField(blank=True)
    usage_examples = models.JSONField(default=list, blank=True, help_text='[{"en":"...","uz":"..."},...]')
    grammar_note = models.CharField(max_length=300, blank=True, default="")

    # Context of the word in the quote
    context_type = models.CharField(
        max_length=50,
        blank=True,
        choices=[
            ("idiom", "Idiom"),
            ("slang", "Slang"),
            ("formal", "Formal"),
            ("phrasal_verb", "Phrasal Verb"),
            ("expression", "Expression"),
            ("sarcasm", "Sarcasm"),
            ("humor", "Humor"),
            ("casual", "Casual"),
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def usage_examples_json(self):
        import json

        return json.dumps(self.usage_examples or [])

    class Meta:
        unique_together = ("user", "transcript", "word")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} — '{self.word}' from {self.quote}"


class ReviewSession(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_sessions")
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    quotes_reviewed = models.PositiveIntegerField(default=0)
    correct_answers = models.PositiveIntegerField(default=0)
    session_type = models.CharField(
        max_length=20,
        choices=[
            ("cloze", "Fill in the Blank"),
            ("shadow", "Shadow Mode"),
            ("review", "Quote Review"),
            ("quiz", "Multiple Choice Quiz"),
            ("mixed", "Mixed"),
        ],
        default="mixed",
    )
    score = models.PositiveIntegerField(default=0)
    best_combo = models.PositiveIntegerField(default=0)

    @property
    def accuracy(self):
        if self.quotes_reviewed == 0:
            return 0
        return round((self.correct_answers / self.quotes_reviewed) * 100)

    @property
    def duration_minutes(self):
        if not self.ended_at:
            return 0
        delta = self.ended_at - self.started_at
        return round(delta.total_seconds() / 60, 1)

    def __str__(self):
        return f"{self.user.username} — Session {self.started_at.date()} ({self.session_type})"


class ClozeResult(models.Model):
    session = models.ForeignKey(ReviewSession, on_delete=models.CASCADE, related_name="cloze_results")
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="cloze_results")
    target_word = models.CharField(max_length=100)
    user_answer = models.CharField(max_length=100)
    is_correct = models.BooleanField(default=False)
    answered_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.session.user.username} — '{self.target_word}' {'✅' if self.is_correct else '❌'}"


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


class VocabWord(models.Model):
    """A vocabulary word used in the onboarding assessment."""

    class Tier(models.IntegerChoices):
        ONE = 1, "Tier 1 — Core"
        TWO = 2, "Tier 2 — Common"
        THREE = 3, "Tier 3 — Familiar"
        FOUR = 4, "Tier 4 — Advanced"
        FIVE = 5, "Tier 5 — Expert"

    word = models.CharField(max_length=100, unique=True)
    frequency_rank = models.PositiveIntegerField(unique=True)
    tier = models.IntegerField(choices=Tier.choices)
    pos = models.CharField(
        max_length=5,
        choices=[
            ("n", "Noun"),
            ("v", "Verb"),
            ("adj", "Adjective"),
            ("adv", "Adverb"),
            ("phr", "Phrase"),
            ("etc", "Other"),
        ],
        default="etc",
    )
    uzbek_translation = models.CharField(max_length=200)
    uzbek_synonyms = models.CharField(max_length=500, blank=True)
    example_sentence = models.TextField(blank=True)
    gif_url = models.URLField(blank=True)

    class Meta:
        ordering = ["frequency_rank"]

    def __str__(self):
        return f"[T{self.tier}] {self.word} — {self.uzbek_translation}"


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

    def __str__(self):
        return f"{self.user.username} — Onboarding ({self.level or 'incomplete'})"


class OnboardingResult(models.Model):
    """Stores the user's response (know/don't know) for each word in the assessment."""

    session = models.ForeignKey(OnboardingSession, on_delete=models.CASCADE, related_name="results")
    word = models.ForeignKey(VocabWord, on_delete=models.CASCADE)
    known = models.BooleanField()
    response_time_ms = models.PositiveIntegerField(default=0)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("session", "word")

    def __str__(self):
        mark = "\u2713" if self.known else "\u2717"
        return f"{self.session.user.username} — '{self.word.word}' {mark}"


class WordCache(models.Model):
    class PostType(models.TextChoices):
        VERB = "v", "Verb"
        NOUN = "n", "Noun"
        ADJECTIVE = "adj", "Adjective"
        PHRASE = "phr", "Phrase"
        OTHER = "etc", "Other"

    word = models.CharField(max_length=100, unique=True)
    pos = models.CharField(max_length=5, choices=PostType.choices, default=PostType.OTHER)
    definition = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.word} [{self.get_pos_display()}]"


class CoreWord(models.Model):
    """High-value vocabulary words that learners should prioritize."""

    word = models.CharField(max_length=100, unique=True, db_index=True)

    class Meta:
        ordering = ["word"]

    def __str__(self):
        return self.word


class SuggestedWord(models.Model):
    """Curated vocabulary extracted from subtitles for learners."""

    LEVEL_BEGINNER = "beginner"
    LEVEL_INTERMEDIATE = "intermediate"
    LEVEL_ADVANCED = "advanced"
    LEVEL_CHOICES = [
        (LEVEL_BEGINNER, "Beginner"),
        (LEVEL_INTERMEDIATE, "Intermediate"),
        (LEVEL_ADVANCED, "Advanced"),
    ]

    word = models.CharField(max_length=100, db_index=True)
    translation = models.CharField(max_length=200, blank=True, default="")
    level = models.CharField(max_length=15, choices=LEVEL_CHOICES, default=LEVEL_BEGINNER)
    usage_examples = models.JSONField(
        default=list, blank=True, help_text='[{"en":"That\'s awful","uz":"Bu dahshatli"},...]'
    )
    grammar_note = models.CharField(
        max_length=300, blank=True, default="", help_text='e.g. "awful (adj) → awfully (adv)"'
    )
    sentence = models.TextField(help_text="The full subtitle line where the word appears")
    source = models.ForeignKey("clips.Source", on_delete=models.CASCADE, related_name="suggested_words")
    episode = models.ForeignKey(
        "clips.Episode", on_delete=models.CASCADE, related_name="suggested_words", null=True, blank=True
    )
    transcript = models.ForeignKey("clips.Transcript", on_delete=models.CASCADE, related_name="suggested_words")
    start_time = models.DecimalField(max_digits=10, decimal_places=3)
    end_time = models.DecimalField(max_digits=10, decimal_places=3)
    season = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["source", "season", "episode_number", "start_time"]
        unique_together = ("transcript", "word")

    def __str__(self):
        loc = f"S{self.season:02d}E{self.episode_number:02d}" if self.season else "Movie"
        return f"{self.word} — {loc} [{self.start_time}s]"
