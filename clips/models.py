import subprocess

from django.conf import settings
from django.core.files import File
from django.core.validators import MinValueValidator
from django.db import models

from clips.utils.video_duration import get_video_duration


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

    def update_video_duration(self):
        try:
            duration = get_video_duration(self.video_file.path)
            Episode.objects.filter(pk=self.pk).update(duration=duration)
        except Exception as e:
            print(f"Error getting duration: {e}")


def generate_thumbnail(video_path, timestamp, output_path):
    """Generate thumbnail from video at specific timestamp"""
    command = ["ffmpeg", "-ss", str(timestamp), "-i", video_path, "-vframes", "1", "-q:v", "2", "-y", output_path]
    subprocess.run(command, check=True)
    return output_path


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
            generate_thumbnail(video_path, self.start_time, thumb_path)
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
