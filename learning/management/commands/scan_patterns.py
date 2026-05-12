"""Scan transcripts for grammar patterns and populate SentencePattern table."""

from django.core.management.base import BaseCommand

from clips.models import Episode, Source
from learning.models import SentencePattern
from learning.utils.pattern_detector import scan_transcripts


class Command(BaseCommand):
    help = "Scan transcripts for grammar patterns and create SentencePattern records"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True, help="Source ID")
        parser.add_argument("--season", type=int, help="Season number (optional)")
        parser.add_argument("--episode", type=int, help="Episode number (optional)")
        parser.add_argument("--clear", action="store_true", help="Clear existing patterns first")

    def handle(self, *args, **options):
        source = Source.objects.get(id=options["source"])
        episode = None

        if options.get("season") and options.get("episode"):
            episode = Episode.objects.filter(
                source=source,
                season=options["season"],
                episode_number=options["episode"],
            ).first()
            if not episode:
                self.stderr.write(f"Episode S{options['season']:02d}E{options['episode']:02d} not found")
                return

        if options["clear"]:
            qs = SentencePattern.objects.filter(source=source)
            if episode:
                qs = qs.filter(episode=episode)
            deleted = qs.delete()[0]
            self.stdout.write(f"Cleared {deleted} existing patterns")

        results = scan_transcripts(source, episode=episode)

        created = 0
        skipped = 0
        level_counts = {}

        for r in results:
            t = r["transcript"]
            ep = t.episode if hasattr(t, "episode") else episode

            _, was_created = SentencePattern.objects.update_or_create(
                transcript=t,
                primary_pattern=r["primary_pattern"],
                defaults={
                    "source": source,
                    "episode": ep,
                    "patterns": r["patterns"],
                    "text": r["text"],
                    "translation": r["translation"],
                    "complexity_score": r["complexity_score"],
                    "difficulty_level": r["difficulty_level"],
                    "clause_count": r["clause_count"],
                    "season": ep.season if ep else None,
                    "episode_number": ep.episode_number if ep else None,
                },
            )

            if was_created:
                created += 1
            else:
                skipped += 1

            level = r["difficulty_level"]
            level_counts[level] = level_counts.get(level, 0) + 1

        self.stdout.write(f"\nDone: {created} created, {skipped} updated")
        self.stdout.write("\nDistribution by difficulty level:")
        for level in sorted(level_counts.keys()):
            count = level_counts[level]
            bar = "█" * min(count, 40)
            self.stdout.write(f"  Level {level}: {count:3d} {bar}")
