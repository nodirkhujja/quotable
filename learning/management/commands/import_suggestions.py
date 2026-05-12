"""
Import word suggestions from JSON into the SuggestedWord model.

Usage:
    poetry run python -m core.manage import_suggestions suggestions.json
    poetry run python -m core.manage import_suggestions suggestions.json --clear
"""

import json
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand

from clips.models import Episode, Source, Transcript
from vocab.models import SuggestedWord


class Command(BaseCommand):
    help = "Import suggested words from a JSON file into the database"

    def add_arguments(self, parser):
        parser.add_argument("file", type=str, help="Path to the suggestions JSON file")
        parser.add_argument("--clear", action="store_true", help="Clear existing suggestions before importing")

    def handle(self, *args, **options):
        json_path = Path(options["file"])
        if not json_path.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {json_path}"))
            return

        data = json.loads(json_path.read_text())
        self.stdout.write(f"Loaded {len(data)} word suggestions from {json_path}")

        if options["clear"]:
            deleted, _ = SuggestedWord.objects.all().delete()
            self.stdout.write(f"Cleared {deleted} existing suggestions")

        created = 0
        skipped = 0

        for entry in data:
            transcript_id = entry.get("transcript_id")
            if not transcript_id:
                skipped += 1
                continue

            try:
                transcript = Transcript.objects.get(id=transcript_id)
            except Transcript.DoesNotExist:
                skipped += 1
                continue

            source_id = entry.get("source_id")
            episode_id = entry.get("episode_id")

            try:
                source = Source.objects.get(id=source_id) if source_id else None
            except Source.DoesNotExist:
                skipped += 1
                continue

            episode = None
            if episode_id:
                try:
                    episode = Episode.objects.get(id=episode_id)
                except Episode.DoesNotExist:
                    pass

            _, was_created = SuggestedWord.objects.update_or_create(
                transcript=transcript,
                word=entry["word"],
                defaults={
                    "sentence": entry["sentence"],
                    "translation": entry.get("translation", ""),
                    "source": source,
                    "episode": episode,
                    "start_time": Decimal(entry["start_time"]),
                    "end_time": Decimal(entry["end_time"]),
                    "season": entry.get("season"),
                    "episode_number": entry.get("episode_number"),
                },
            )

            if was_created:
                created += 1

        self.stdout.write(
            self.style.SUCCESS(f"Done: {created} created, {len(data) - created - skipped} updated, {skipped} skipped")
        )
