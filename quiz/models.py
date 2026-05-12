from django.conf import settings
from django.db import models


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

    class Meta:
        db_table = "learning_reviewsession"  # keeps existing table — no data migration

    @property
    def accuracy(self):
        if self.quotes_reviewed == 0:
            return 0
        return round((self.correct_answers / self.quotes_reviewed) * 100)

    @property
    def duration_minutes(self):
        if not self.ended_at:
            return 0
        return round((self.ended_at - self.started_at).total_seconds() / 60, 1)

    def __str__(self):
        return f"{self.user.username} — Session {self.started_at.date()} ({self.session_type})"


class ClozeResult(models.Model):
    session = models.ForeignKey(ReviewSession, on_delete=models.CASCADE, related_name="cloze_results")
    quote = models.ForeignKey("clips.Quote", on_delete=models.CASCADE, related_name="cloze_results")
    target_word = models.CharField(max_length=100)
    user_answer = models.CharField(max_length=100)
    is_correct = models.BooleanField(default=False)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learning_clozemresult"  # keeps existing table

    def __str__(self):
        mark = "✅" if self.is_correct else "❌"
        return f"{self.session.user.username} — '{self.target_word}' {mark}"


class QuizAttempt(models.Model):
    """Individual quiz answer — every answer recorded for analytics + confusion detection."""

    QUIZ_TYPES = [
        ("flash", "Flash Match"),
        ("cloze", "Cloze"),
        ("produce", "Define & Use"),
        ("match", "Match"),
        ("listen", "Listen"),
        ("teach", "Teach"),
    ]
    DIRECTION_CHOICES = [
        ("l2_l1", "English → Uzbek"),
        ("l1_l2", "Uzbek → English"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts")
    vocab = models.ForeignKey(
        "vocab.LineVocab",
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
        null=True,
        blank=True,
    )
    note = models.ForeignKey(
        "vocab.WordNote",
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
        null=True,
        blank=True,
    )
    quiz_type = models.CharField(max_length=10, choices=QUIZ_TYPES)
    direction = models.CharField(max_length=5, choices=DIRECTION_CHOICES, default="l2_l1")
    correct = models.BooleanField()
    user_answer = models.CharField(max_length=300, blank=True)
    chosen_wrong = models.CharField(
        max_length=300, blank=True, help_text="Wrong option the user picked (confusion detection)"
    )
    response_time_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learning_quizattempt"  # keeps existing table
        ordering = ["-created_at"]

    def __str__(self):
        mark = "✓" if self.correct else "✗"
        word = self.vocab.english if self.vocab else (self.note.word if self.note else "?")
        return f"{self.user.username} — {word} ({self.quiz_type}) {mark}"


class SavedSentence(models.Model):
    """An accepted English sentence the learner produced during a quiz.

    Only clean wins stored (AI-graded `accepted`). The continuity layer
    (streak, sentence library, teacher voice) reads this table.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_sentences")
    word = models.CharField(max_length=120, blank=True, default="", help_text="Target word/phrase the learner used")
    note = models.ForeignKey(
        "vocab.WordNote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="saved_sentences",
    )
    uzbek_prompt = models.TextField(blank=True, default="")
    english = models.TextField(help_text="The English the learner produced")
    quiz_type = models.CharField(max_length=40, default="mastered_bridge")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "learning_savedsentence"  # keeps existing table
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self):
        return f"{self.user.username} · {self.word} · {self.created_at:%Y-%m-%d}"
