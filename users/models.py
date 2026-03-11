from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    email = models.EmailField(unique=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    # Language Learning
    native_language = models.CharField(max_length=50, blank=True)
    target_language = models.CharField(max_length=50, blank=True)
    proficiency_level = models.CharField(
        max_length=20,
        choices=[
            ("beginner", "Beginner"),
            ("intermediate", "Intermediate"),
            ("advanced", "Advanced"),
            ("fluent", "Fluent"),
        ],
        default="beginner",
    )

    # Streaks
    streak_days = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_active_date = models.DateField(null=True, blank=True)

    # Preferences
    daily_goal_minutes = models.PositiveIntegerField(default=15)
    preferred_playback_speed = models.FloatField(default=1.0)
    show_subtitles_on_video = models.BooleanField(default=False)

    def __str__(self):
        return self.username
