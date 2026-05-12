import structlog
from django.conf import settings
from django.core.files import File
from django.core.validators import MinValueValidator
from django.db import models

from integrations.ffmpeg import service as ffmpeg

log = structlog.get_logger(__name__)


class SourceType(models.TextChoices):
    MOVIE = "movie", "Movie"
    TV_SHOW = "tv_show", "TV Show"


class Source(models.Model):
    """
    Represents the 'Container' or the 'Movie'.
    For a Movie: Holds the video file directly.
    For a TV Show: Holds the Title/Thumbnail, but NO video file (episodes have those).
    """

    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, null=True)
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.MOVIE)
    year = models.PositiveIntegerField(null=True, blank=True)
    description = models.TextField(blank=True)
    thumbnail = models.ImageField(upload_to="thumbnails/", blank=True, null=True)
    video_file = models.FileField(upload_to="videos/", blank=True, null=True)
    duration = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in seconds")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.get_source_type_display()})"


class Episode(models.Model):
    """
    Only used for TV Shows. Links back to the parent Source.
    """

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="episodes")
    season = models.PositiveIntegerField()
    episode_number = models.PositiveIntegerField()
    title = models.CharField(max_length=255, blank=True)
    video_file = models.FileField(upload_to="episodes/", blank=True, null=True)
    duration = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["season", "episode_number"]
        unique_together = [["source", "season", "episode_number"]]

    def __str__(self):
        return f"{self.source.title} S{self.season}E{self.episode_number}"

    def save(self, *args, **kwargs):
        is_new_video = False
        if self.pk:
            old_file = Episode.objects.get(pk=self.pk).video_file
            if old_file != self.video_file:
                is_new_video = True
        else:
            is_new_video = True
        super().save(*args, **kwargs)

        if is_new_video and self.video_file:
            self.update_video_duration()
            from clips.tasks import generate_scene_blocks_task

            generate_scene_blocks_task.delay(self.pk)

    def update_video_duration(self):
        try:
            duration = ffmpeg.duration(self.video_file.path)
            Episode.objects.filter(pk=self.pk).update(duration=duration)
        except Exception as exc:
            log.warning("update_video_duration failed", episode_id=self.pk, error=str(exc))


class Quote(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="quotes")
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE, related_name="quotes", null=True, blank=True)
    text = models.TextField()
    start_time = models.FloatField(validators=[MinValueValidator(0.0)])
    end_time = models.FloatField(validators=[MinValueValidator(0.0)])
    views = models.PositiveIntegerField(default=0)
    thumbnail = models.ImageField(upload_to="quote_thumbnails/", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        return f"{self.source.title}: {self.text[:30]}..."

    def save(self, *args, **kwargs):
        if not self.thumbnail and (self.source.video_file or self.episode.video_file):
            video_path = None
            if self.episode:
                video_path = self.episode.video_file.path
            elif self.source.video_file:
                video_path = self.source.video_file.path
            else:
                return
            thumb_path = f"/tmp/thumb_{self.id}.jpg"
            ffmpeg.thumbnail(video_path, self.start_time, thumb_path)
            with open(thumb_path, "rb") as f:
                self.thumbnail.save(f"quote_{self.id}.jpg", File(f), save=False)
        super().save(*args, **kwargs)

    @property
    def duration(self):
        return self.end_time - self.start_time

    def get_timestamp_url(self):
        return f"/watch/{self.source.id}/?t={self.start_time}"


class Transcript(models.Model):
    source = models.ForeignKey(Source, null=True, blank=True, related_name="transcripts", on_delete=models.CASCADE)
    episode = models.ForeignKey(Episode, null=True, blank=True, related_name="transcripts", on_delete=models.CASCADE)
    text = models.TextField()
    start_time = models.DecimalField(max_digits=10, decimal_places=3)
    end_time = models.DecimalField(max_digits=10, decimal_places=3)


class SceneBlock(models.Model):
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="scene_blocks")
    episode = models.ForeignKey(Episode, null=True, blank=True, on_delete=models.CASCADE, related_name="scene_blocks")
    start_time = models.DecimalField(max_digits=10, decimal_places=3)
    end_time = models.DecimalField(max_digits=10, decimal_places=3)
    thumbnail = models.ImageField(upload_to="scene_thumbnails/", null=True, blank=True)

    class Meta:
        ordering = ["start_time"]

    def __str__(self):
        ep = f" {self.episode}" if self.episode else ""
        return f"{self.source.title}{ep} [{self.start_time}–{self.end_time}]"

    @property
    def label(self):
        def fmt(t):
            t = int(t)
            return f"{t // 60}:{t % 60:02d}"

        return f"{fmt(self.start_time)} – {fmt(self.end_time)}"


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "quote")


class WatchHistory(models.Model):
    """Tracks the last playback position per user per video (movie or episode)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="watch_history",
    )
    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="watch_history")
    episode = models.ForeignKey(
        Episode,
        on_delete=models.CASCADE,
        related_name="watch_history",
        null=True,
        blank=True,
    )
    position_sec = models.FloatField(default=0)
    duration_sec = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "source", "episode")

    @property
    def progress_pct(self):
        if not self.duration_sec:
            return 0
        return min(self.position_sec / self.duration_sec, 1.0)

    @property
    def is_complete(self):
        return self.progress_pct >= 0.9

    @property
    def remaining_sec(self):
        return max(self.duration_sec - self.position_sec, 0)


# ─────────────────────────────────────────────────────
# REELS — short curated clips (30-120s) for shadowing
# Entertainment-first, vocab-rich, the daily-return loop.
# ─────────────────────────────────────────────────────


class Reel(models.Model):
    """A curated short clip (30s–2min) from an existing Source.

    Designed for the home-page Reels section: bite-sized comedy
    or memorable scenes that contain key vocabulary, optimized for
    shadowing practice and daily return.

    Curated by admin in Django admin (start/end picker over an
    existing Source's video). Gets a thumbnail snapshot at
    `poster_frame` and links to the transcript lines it covers.
    """

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="reels",
    )
    episode = models.ForeignKey(
        "Episode",
        on_delete=models.CASCADE,
        related_name="reels",
        null=True,
        blank=True,
        help_text="For TV shows: which episode this reel comes from",
    )

    title = models.CharField(max_length=140, help_text="Short headline (e.g. 'Joey's audition')")
    description = models.TextField(blank=True, help_text="1-2 lines of setup shown under the reel")

    # Time bounds within the source/episode video
    start_time = models.FloatField(
        validators=[MinValueValidator(0.0)], help_text="Start time in source video (seconds)"
    )
    end_time = models.FloatField(validators=[MinValueValidator(0.0)], help_text="End time in source video (seconds)")
    poster_frame = models.FloatField(
        null=True, blank=True, help_text="Timestamp to snapshot for the thumbnail (default: midpoint)"
    )
    thumbnail = models.ImageField(
        upload_to="reel_thumbnails/", null=True, blank=True, help_text="Auto-generated; can be overridden"
    )

    # Curation metadata
    CEFR_CHOICES = [
        ("A1", "A1 — Beginner"),
        ("A2", "A2 — Elementary"),
        ("B1", "B1 — Intermediate"),
        ("B2", "B2 — Upper-Intermediate"),
        ("C1", "C1 — Advanced"),
        ("C2", "C2 — Mastery"),
    ]
    cefr_level = models.CharField(
        max_length=2, choices=CEFR_CHOICES, default="B1", help_text="Difficulty band — drives feed personalization"
    )
    comedy_score = models.PositiveSmallIntegerField(default=5, help_text="1-10: how funny / re-watchable")

    is_published = models.BooleanField(default=False, help_text="Show in the public Reels feed")
    published_at = models.DateTimeField(null=True, blank=True)

    # Engagement counters (denormalized for feed sort)
    view_count = models.PositiveIntegerField(default=0)
    save_count = models.PositiveIntegerField(default=0, help_text="Users who saved a word from this reel")
    shadow_count = models.PositiveIntegerField(default=0, help_text="Users who shadowed at least one line")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-published_at"]),
            models.Index(fields=["cefr_level", "is_published"]),
        ]

    def __str__(self):
        return f"{self.title} · {self.source.title} · {self.duration_sec}s"

    @property
    def duration_sec(self):
        return max(0, round(self.end_time - self.start_time))

    @property
    def video_url(self):
        """Resolve which video file to play (episode > source)."""
        if self.episode and self.episode.video_file:
            return self.episode.video_file.url
        if self.source.video_file:
            return self.source.video_file.url
        return None


class ReelLine(models.Model):
    """Links a Reel to the Transcript lines it contains.

    Lets us highlight target vocabulary in subtitles, mark which
    lines are good shadowing targets, and identify punchlines for
    a future "highlight reel" mode.
    """

    reel = models.ForeignKey(
        Reel,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    transcript = models.ForeignKey(
        "Transcript",
        on_delete=models.CASCADE,
        related_name="reel_appearances",
    )
    order = models.PositiveIntegerField(default=0, help_text="Display order within the reel")
    is_shadow_target = models.BooleanField(default=False, help_text="Marked for shadowing practice")
    is_punchline = models.BooleanField(default=False, help_text="Comedy beat — used by future highlight-reel mode")
    target_words = models.JSONField(default=list, blank=True, help_text="List of important words this line teaches")

    class Meta:
        ordering = ["order"]
        unique_together = [("reel", "transcript")]

    def __str__(self):
        return f"{self.reel.title} · line {self.order}"


class ReelView(models.Model):
    """Per-user per-reel engagement record.

    Drives feed personalization: don't show finished reels,
    track which words/sounds the learner engaged with.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reel_views",
    )
    reel = models.ForeignKey(
        Reel,
        on_delete=models.CASCADE,
        related_name="user_views",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    completed = models.BooleanField(default=False, help_text="Watched ≥80% of the reel duration")
    shadowed_lines = models.PositiveIntegerField(default=0)
    saved_words = models.PositiveIntegerField(default=0)
    liked = models.BooleanField(default=False)
    bookmarked = models.BooleanField(default=False)

    class Meta:
        unique_together = [("user", "reel")]
        indexes = [
            models.Index(fields=["user", "-last_seen_at"]),
        ]

    def __str__(self):
        return f"{self.user.username} ⇒ {self.reel.title}"
