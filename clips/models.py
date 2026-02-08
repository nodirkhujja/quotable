from django.conf import settings
from django.db import models


class Source(models.Model):
    """Full movie or TV show"""

    class SourceType(models.TextChoices):
        MOVIE = "movie", "Movie"
        TV_SHOW = "tv_show", "TV Show"

    title = models.CharField(max_length=200)
    source_type = models.CharField(max_length=50, choices=SourceType.choices)
    season = models.IntegerField(null=True, blank=True)
    episode = models.IntegerField(null=True, blank=True)
    year = models.IntegerField(null=True)
    thumbnail = models.ImageField(upload_to="thumbnails/", blank=True, null=True)
    video_file = models.FileField(upload_to="videos/", blank=True, null=True)  # Full video here
    duration = models.FloatField()  # Total duration in seconds

    def __str__(self):
        if self.source_type == "tv_show":
            return f"{self.title} S{self.season}E{self.episode}"
        return self.title


class Quote(models.Model):
    """A quote/moment within a video"""

    source = models.ForeignKey(Source, on_delete=models.CASCADE, related_name="quotes")
    text = models.TextField()  # The actual quote text
    start_time = models.FloatField()  # Start timestamp in seconds
    end_time = models.FloatField()  # End timestamp in seconds
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    views = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.text[:50]}... ({self.start_time}s)"

    @property
    def duration(self):
        return self.end_time - self.start_time

    def get_timestamp_url(self):
        """Generate URL with timestamp parameter"""
        return f"/watch/{self.source.id}?t={self.start_time}"


class Favorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    quote = models.ForeignKey(Quote, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "quote")
