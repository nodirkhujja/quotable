from django.conf import settings
from django.db import models

from clips.models import Quote, Source


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
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="word_notes")
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="word_notes")

    word = models.CharField(max_length=100)
    definition = models.TextField(blank=True)
    personal_note = models.TextField(blank=True)
    example_usage = models.TextField(blank=True)

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

    class Meta:
        unique_together = ("user", "quote", "word")
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
            ("mixed", "Mixed"),
        ],
        default="mixed",
    )

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
