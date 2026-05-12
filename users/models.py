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

    # Cumulative XP for CEFR level progression (A1 → C2)
    total_xp = models.PositiveIntegerField(default=0, help_text="Lifetime XP earned across all sessions")

    # Chess.com-style session rating — skill estimate in points, ~1000 avg, can go up or down
    rating = models.PositiveIntegerField(default=1000, help_text="Skill rating (Elo-like), starts at 1000")
    peak_rating = models.PositiveIntegerField(default=1000, help_text="Highest rating ever achieved")

    # Preferences
    daily_goal_minutes = models.PositiveIntegerField(default=15)
    preferred_playback_speed = models.FloatField(default=1.0)
    show_subtitles_on_video = models.BooleanField(default=False)

    # Premium tier — gates pronunciation scoring (Phase 1: manual flag).
    # Phase 2 will add Stripe billing + premium_until expiration.
    is_premium = models.BooleanField(default=False)
    premium_until = models.DateTimeField(null=True, blank=True)
    # Free-tier learners get N lifetime pronunciation assessments before upgrade prompt.
    pronunciation_trials_used = models.PositiveIntegerField(default=0)
    PRONUNCIATION_FREE_TRIALS = 5

    @property
    def has_pronunciation(self):
        """Can this user request a pronunciation assessment right now?"""
        from django.utils import timezone

        if self.is_premium:
            if not self.premium_until or self.premium_until > timezone.now():
                return True
        return self.pronunciation_trials_used < self.PRONUNCIATION_FREE_TRIALS

    @property
    def pronunciation_trials_remaining(self):
        """How many free assessments left before upgrade is required."""
        if self.is_premium:
            return None  # unlimited
        return max(0, self.PRONUNCIATION_FREE_TRIALS - self.pronunciation_trials_used)

    @property
    def daily_xp_goal(self):
        """Daily XP target derived from minutes — 15 min ≈ 100 XP baseline."""
        # 5→40, 10→80, 15→120, 20→160, 30→200, 45→280, 60→360 (diminishing)
        m = self.daily_goal_minutes or 15
        if m <= 15:
            return m * 8
        return 120 + (m - 15) * 6

    def __str__(self):
        return self.username
