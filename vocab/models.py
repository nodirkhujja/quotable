"""Vocabulary domain — word notebook, line annotations, BKT mastery.

All models carry Meta.db_table pointing at the existing learning_* tables so
SeparateDatabaseAndState migrations can register them here without any DDL.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

# ─── BKT constants & helpers ────────────────────────────────────────────────

BKT_PARAMS = {
    "flash": (0.25, 0.05),
    "smart_mcq": (0.33, 0.05),
    "usage_check": (0.50, 0.08),
    "pattern_notice": (0.05, 0.10),
    "sentence_cloze": (0.05, 0.10),
    "translate_back": (0.03, 0.12),
    "quote_dash": (0.05, 0.10),
    "continue_video": (0.05, 0.10),
    "free_production": (0.02, 0.15),
    "match": (0.20, 0.05),
    "listen": (0.05, 0.15),
    "cloze": (0.05, 0.10),
    "produce": (0.02, 0.10),
    "build": (0.03, 0.10),
    "define": (0.01, 0.08),
    "pattern": (0.10, 0.10),
}
BKT_P_TRANSITION = 0.3
BKT_P_INIT = 0.1


def bkt_update(p_known: float, quiz_type: str, correct: bool, response_time_ms: int = 0) -> float:
    """Bayesian Knowledge Tracing update — single source of truth."""
    p_guess, p_slip = BKT_PARAMS.get(quiz_type, (0.10, 0.10))
    if correct:
        numerator = p_known * (1 - p_slip)
        denominator = numerator + (1 - p_known) * p_guess
    else:
        numerator = p_known * p_slip
        denominator = numerator + (1 - p_known) * (1 - p_guess)
    posterior = numerator / max(denominator, 1e-9)
    if correct:
        p_new = posterior + (1 - posterior) * BKT_P_TRANSITION
    else:
        p_new = posterior + (1 - posterior) * BKT_P_TRANSITION * 0.1
    if correct and response_time_ms > 0:
        time_bonus = max(-0.05, min(0.05, (3000 - response_time_ms) / 50000))
        p_new = max(0.0, min(1.0, p_new + time_bonus))
    return max(0.0, min(1.0, p_new))


# ─── Models ─────────────────────────────────────────────────────────────────


class WordNote(models.Model):

    class PostType(models.TextChoices):
        VERB = "v", "Verb"
        NOUN = "n", "Noun"
        ADJECTIVE = "adj", "Adjective"
        PHRASE = "phr", "Phrase"
        OTHER = "etc", "Other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="word_notes")
    quote = models.ForeignKey(
        "clips.Quote", on_delete=models.CASCADE, related_name="word_notes", null=True, blank=True
    )
    transcript = models.ForeignKey(
        "clips.Transcript", on_delete=models.CASCADE, related_name="word_notes", null=True, blank=True
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
        choices=[("sarcastic", "Sarcastic"), ("angry", "Angry"), ("funny", "Funny"), ("romantic", "Romantic")],
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
    scene_timestamp = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    scene_frame = models.ImageField(upload_to="scene_frames/", null=True, blank=True)
    personal_note = models.TextField(blank=True)
    example_usage = models.TextField(blank=True)
    usage_examples = models.JSONField(default=list, blank=True)
    grammar_note = models.CharField(max_length=300, blank=True, default="")
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
    next_review = models.DateTimeField(null=True, blank=True, db_index=True, default=timezone.now)
    review_count = models.PositiveIntegerField(default=0)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)

    @property
    def usage_examples_json(self):
        import json

        return json.dumps(self.usage_examples or [])

    class Meta:
        db_table = "learning_wordnote"
        unique_together = ("user", "transcript", "word")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} — '{self.word}'"


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

    class Meta:
        db_table = "learning_wordcache"

    def __str__(self):
        return f"{self.word} [{self.get_pos_display()}]"


class CoreWord(models.Model):
    word = models.CharField(max_length=100, unique=True, db_index=True)

    class Meta:
        db_table = "learning_coreword"
        ordering = ["word"]

    def __str__(self):
        return self.word


class SuggestedWord(models.Model):
    LEVEL_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]
    word = models.CharField(max_length=100, db_index=True)
    definition = models.CharField(max_length=300, blank=True, default="")
    translation = models.CharField(max_length=200, blank=True, default="")
    level = models.CharField(max_length=15, choices=LEVEL_CHOICES, default="beginner")
    frequency = models.PositiveIntegerField(default=0)
    usage_examples = models.JSONField(default=list, blank=True)
    grammar_note = models.CharField(max_length=300, blank=True, default="")
    sentence = models.TextField()
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
        db_table = "learning_suggestedword"
        ordering = ["source", "season", "episode_number", "start_time"]
        unique_together = ("transcript", "word")

    def __str__(self):
        loc = f"S{self.season:02d}E{self.episode_number:02d}" if self.season else "Movie"
        return f"{self.word} — {loc} [{self.start_time}s]"


class WordTranslation(models.Model):
    LEVEL_CHOICES = [("A1", "A1"), ("A2", "A2"), ("B1", "B1"), ("B2", "B2"), ("C1", "C1"), ("C2", "C2")]
    word = models.CharField(max_length=100, unique=True, db_index=True)
    translation = models.CharField(max_length=300)
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, default="B1")
    frequency = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "learning_wordtranslation"
        ordering = ["-frequency", "word"]

    def __str__(self):
        return f"{self.word} [{self.level}] → {self.translation}"


class LineTranslation(models.Model):
    source = models.ForeignKey("clips.Source", on_delete=models.CASCADE, related_name="line_translations")
    episode = models.ForeignKey(
        "clips.Episode", on_delete=models.CASCADE, related_name="line_translations", null=True, blank=True
    )
    transcript = models.OneToOneField("clips.Transcript", on_delete=models.CASCADE, related_name="line_translation")
    translation = models.TextField()
    season = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "learning_linetranslation"
        ordering = ["source", "season", "episode_number", "transcript__start_time"]

    def __str__(self):
        loc = f"S{self.season:02d}E{self.episode_number:02d}" if self.season else "Movie"
        return f"{loc} — {self.transcript.text[:50]}"


class LineVocab(models.Model):
    KIND_CHOICES = [
        ("verb", "Verb"),
        ("noun", "Noun"),
        ("adj", "Adjective"),
        ("adv", "Adverb"),
        ("phrasal_verb", "Phrasal Verb"),
        ("phrase", "Phrase"),
        ("pattern", "Pattern"),
        ("idiom", "Idiom"),
    ]
    LEVEL_CHOICES = [("A1", "A1"), ("A2", "A2"), ("B1", "B1"), ("B2", "B2"), ("C1", "C1"), ("C2", "C2")]
    CATEGORY_CHOICES = [
        ("social", "Social"),
        ("relationships", "Relationships"),
        ("emotions", "Emotions"),
        ("home", "Home"),
        ("household", "Household"),
        ("body", "Body"),
        ("food", "Food"),
        ("humor", "Humor"),
        ("entertainment", "Entertainment"),
        ("work", "Work"),
        ("personal_growth", "Personal Growth"),
        ("general", "General"),
    ]
    source = models.ForeignKey("clips.Source", on_delete=models.CASCADE, related_name="line_vocab")
    episode = models.ForeignKey(
        "clips.Episode", on_delete=models.CASCADE, related_name="line_vocab", null=True, blank=True
    )
    transcript = models.ForeignKey("clips.Transcript", on_delete=models.CASCADE, related_name="line_vocab")
    vocab_id = models.CharField(max_length=100, blank=True, default="", db_index=True)
    english = models.CharField(max_length=200)
    translation = models.CharField(max_length=300)
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default="phrase")
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES, blank=True, default="")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True, default="")
    description = models.TextField(blank=True)
    example = models.TextField(blank=True)
    pattern = models.CharField(max_length=300, blank=True)
    tavsif = models.TextField(blank=True)
    smart_mcq = models.JSONField(blank=True, default=dict)
    usage_check = models.JSONField(blank=True, default=dict)
    translated_examples = models.JSONField(blank=True, default=list)
    confusable_with = models.JSONField(blank=True, default=list)
    collocations = models.JSONField(blank=True, default=list)
    season = models.PositiveIntegerField(null=True, blank=True)
    episode_number = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "learning_linevocab"
        ordering = ["source", "season", "episode_number", "transcript__start_time"]
        unique_together = ("transcript", "english")

    def __str__(self):
        loc = f"S{self.season:02d}E{self.episode_number:02d}" if self.season else "Movie"
        return f"{loc} — {self.english} ({self.get_kind_display()})"


class VocabWord(models.Model):
    """Vocabulary word used in the onboarding assessment (100 words, 5 tiers)."""

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
        db_table = "learning_vocabword"
        ordering = ["frequency_rank"]

    def __str__(self):
        return f"[T{self.tier}] {self.word} — {self.uzbek_translation}"


class VocabMastery(models.Model):
    """Per-user BKT mastery for each LineVocab item."""

    LEVEL_CHOICES = [
        ("weak", "Weak"),
        ("shaky", "Shaky"),
        ("good", "Good"),
        ("strong", "Strong"),
        ("mastered", "Mastered"),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vocab_masteries")
    vocab = models.ForeignKey(LineVocab, on_delete=models.CASCADE, related_name="masteries")
    p_known = models.FloatField(default=BKT_P_INIT)
    translation_score = models.FloatField(default=0)
    context_score = models.FloatField(default=0)
    production_score = models.FloatField(default=0)
    mastery_score = models.FloatField(default=0)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, default="weak")
    overall_score = models.FloatField(default=0)
    difficulty = models.FloatField(default=1000)
    streak = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    next_review = models.DateTimeField(default=timezone.now)
    last_reviewed = models.DateTimeField(null=True, blank=True)
    interval_days = models.FloatField(default=0)
    ease_factor = models.FloatField(default=2.5)
    lapses = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "learning_vocabmastery"
        unique_together = ("user", "vocab")
        ordering = ["mastery_score"]

    def __str__(self):
        return f"{self.user.username} — {self.vocab.english} [{self.level}] P={self.p_known:.2f}"

    def record_answer(self, quiz_type, correct, response_time_ms=0):
        self.p_known = bkt_update(self.p_known, quiz_type, correct, response_time_ms)
        p_guess, p_slip = BKT_PARAMS.get(quiz_type, (0.10, 0.10))
        _TRANSLATION = ("flash", "match", "listen", "smart_mcq", "usage_check")
        _CONTEXT = ("cloze", "build", "pattern", "pattern_notice", "sentence_cloze", "quote_dash")
        _PRODUCTION = ("produce", "define", "translate_back")
        if quiz_type in _TRANSLATION:
            self.translation_score = self._bkt_update_dim(self.translation_score, correct, p_guess, p_slip)
        elif quiz_type in _CONTEXT:
            self.context_score = self._bkt_update_dim(self.context_score, correct, p_guess, p_slip)
        elif quiz_type in _PRODUCTION:
            self.production_score = self._bkt_update_dim(self.production_score, correct, p_guess, p_slip)
        self.attempts += 1
        self.streak = self.streak + 1 if correct else 0
        self.mastery_score = self.p_known * 100
        self.overall_score = self.mastery_score
        self.level = self._calc_level()
        self.last_reviewed = timezone.now()
        self._schedule_next_review()
        self.save()

    def _bkt_update_dim(self, current_p, correct, p_guess, p_slip):
        current_p = max(0.0, min(1.0, float(current_p or 0)))
        num = current_p * (1 - p_slip if correct else p_slip)
        den = num + (1 - current_p) * (p_guess if correct else 1 - p_guess)
        posterior = num / max(den, 1e-9)
        return max(0.0, min(1.0, posterior + (1 - posterior) * BKT_P_TRANSITION * (1 if correct else 0.1)))

    def _calc_level(self):
        s, prev = self.mastery_score, self.level
        if prev == "mastered":
            return "strong" if s < 80 or self.streak == 0 else "mastered"
        if prev == "strong":
            if s >= 85 and self.streak >= 3:
                return "mastered"
            return "good" if s < 58 else "strong"
        if prev == "good":
            if s >= 67:
                return "strong"
            return "shaky" if s < 35 else "good"
        if prev == "shaky":
            if s >= 42:
                return "good"
            return "weak" if s < 17 else "shaky"
        return "shaky" if s >= 22 else "weak"

    def _schedule_next_review(self):
        if self.streak == 0:
            if self.attempts > 1 and self.interval_days > 1:
                self.lapses += 1
            self.interval_days = 0
            self.next_review = timezone.now() + timezone.timedelta(minutes=30 if self.lapses else 10)
            self.ease_factor = max(1.3, self.ease_factor - 0.2)
            return
        self.interval_days = (
            1 if self.streak == 1 else (3 if self.streak == 2 else min(self.interval_days * self.ease_factor, 180))
        )
        self.ease_factor = max(1.3, self.ease_factor + 0.05)
        self.next_review = timezone.now() + timezone.timedelta(days=self.interval_days)

    def apply_decay(self):
        if not self.last_reviewed:
            return
        hours_since = (timezone.now() - self.last_reviewed).total_seconds() / 3600
        if hours_since < 0.5:
            return
        half_life = max(24, (self.interval_days or 1) * 24)
        self.p_known = self.p_known * max(0.1, 2 ** (-hours_since / half_life))
        self.mastery_score = self.p_known * 100
        self.overall_score = self.mastery_score
        self.level = self._calc_level()

    def get_urgency(self, current_episode=None, weakness_multiplier=1.0):
        need = (1 - self.p_known) * 100
        if self.last_reviewed:
            hours_since = (timezone.now() - self.last_reviewed).total_seconds() / 3600
            decay = (1 - max(0.1, 2 ** (-hours_since / max(24, (self.interval_days or 1) * 24)))) * 100
        else:
            decay = 100
        readiness = {"word": 100, "phrase": 70, "idiom": 40}.get(self.vocab.kind, 50)
        context_boost = 0
        if current_episode:
            if self.vocab.episode_id == current_episode.id:
                context_boost = 50
            elif self.vocab.season == getattr(current_episode, "season", None):
                context_boost = 25
        return (0.35 * need + 0.30 * decay + 0.15 * readiness + 0.20 * context_boost) * weakness_multiplier

    def weakest_dimension(self):
        return min(
            [("flash", self.translation_score), ("cloze", self.context_score), ("produce", self.production_score)],
            key=lambda x: x[1],
        )[0]
