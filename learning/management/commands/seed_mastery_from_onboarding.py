"""Backfill VocabMastery seeds from existing onboarding sessions.

Usage:
    poetry run python -m core.manage seed_mastery_from_onboarding              # all users
    poetry run python -m core.manage seed_mastery_from_onboarding --user 123   # one user
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from learning.models import OnboardingSession
from learning.utils.onboarding_seed import seed_mastery_from_onboarding


class Command(BaseCommand):
    help = "Seed VocabMastery records from completed OnboardingSessions."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, default=None, help="Single user id (default: all)")

    def handle(self, *args, **opts):
        User = get_user_model()
        if opts["user"]:
            users = User.objects.filter(id=opts["user"])
        else:
            users = User.objects.filter(onboarding__completed_at__isnull=False).distinct()

        total = users.count()
        self.stdout.write(f"Seeding for {total} user(s)...")

        agg = {"created": 0, "updated": 0, "matched_lv": 0, "skipped": 0}
        per_user = []
        for u in users:
            stats = seed_mastery_from_onboarding(u)
            for k, v in stats.items():
                agg[k] += v
            per_user.append((u.username, stats))

        for username, stats in per_user:
            self.stdout.write(
                f"  {username}: created={stats['created']} updated={stats['updated']} "
                f"matched_lv={stats['matched_lv']} skipped={stats['skipped']}"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nTotal: created={agg['created']} updated={agg['updated']} "
                f"matched_lv={agg['matched_lv']} skipped={agg['skipped']}"
            )
        )
