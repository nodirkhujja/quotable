"""Recompute SceneCoverage for users — feeds the "Ready to Watch" page.

Usage:
    poetry run python -m core.manage calculate_coverage              # all active users
    poetry run python -m core.manage calculate_coverage --user a@a.com   # one user
    poetry run python -m core.manage calculate_coverage --source Friends  # narrow corpus

Run nightly via cron / celery once words start flowing in. For now it's
fine to run ad-hoc; coverage shifts only when WordNotes change.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from learning.services.coverage import recalculate_for_user

User = get_user_model()


class Command(BaseCommand):
    help = "Recompute SceneCoverage for users (Ready to Watch feature)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            help="Email or username to limit recalc to a single user.",
        )
        parser.add_argument(
            "--source",
            default="Friends",
            help="Source title filter (default: Friends).",
        )
        parser.add_argument(
            "--all-sources",
            action="store_true",
            help="Process transcripts from ALL sources, not just --source.",
        )

    def handle(self, *args, **opts):
        if opts["all_sources"]:
            source_filter = None
        else:
            source_filter = opts["source"]

        user_q = User.objects.filter(is_active=True)
        if opts.get("user"):
            ident = opts["user"]
            user_q = user_q.filter(email__iexact=ident) | user_q.filter(username__iexact=ident)

        for u in user_q:
            stats = recalculate_for_user(u, source_filter=source_filter)
            self.stdout.write(
                f"{stats['user']:30s}  processed={stats['transcripts_processed']}  " f"buckets={stats['buckets']}"
            )
