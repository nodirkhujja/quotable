from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import FavoriteQuote, LearningProgress, SourceProgress

User = get_user_model()


# 1. AUTO-CREATE LEARNING PROGRESS ON REGISTRATION
@receiver(post_save, sender=User)
def create_user_learning_progress(sender, instance, created, **kwargs):
    if created:
        LearningProgress.objects.create(user=instance)


# 2. UPDATE STREAK ON LOGIN
# Logging in counts as a sign-of-life — bump the streak through the
# shared helper so streak logic lives in exactly one place.
@receiver(user_logged_in)
def update_user_streak(sender, request, user, **kwargs):
    from learning.utils.activity import bump_streak_for_today

    bump_streak_for_today(user)


# 3. UPDATE SOURCE PROGRESS WHEN QUOTE IS FAVORITED
@receiver(post_save, sender=FavoriteQuote)
def update_source_stats_on_favorite(sender, instance, created, **kwargs):
    if created:
        # We find or create the progress record for this specific Show/Movie
        progress, _ = SourceProgress.objects.get_or_create(user=instance.user, source=instance.quote.source)

        # Increment the favorite count for this source
        progress.quotes_favorited = FavoriteQuote.objects.filter(
            user=instance.user, quote__source=instance.quote.source
        ).count()

        progress.save(update_fields=["quotes_favorited"])

        # Also update the global LearningProgress stats
        learning_stats = instance.user.progress
        learning_stats.total_words_noted = FavoriteQuote.objects.filter(user=instance.user).count()
        learning_stats.save(update_fields=["total_words_noted"])
