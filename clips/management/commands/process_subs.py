"""
Django management command to import quotes from subtitle files

Usage:
    python manage.py import_subtitles <source_id> <srt_file_path>
"""

import os

from django.core.management.base import BaseCommand, CommandError

from clips.models import Source
from clips.utils.subtitle_importer import import_quotes_from_srt


class Command(BaseCommand):
    help = "Import quotes from an SRT subtitle file"  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument("source_id", type=int, help="ID of the Source (movie/TV show) to add quotes to")
        parser.add_argument("srt_file", type=str, help="Path to the .srt subtitle file")
        parser.add_argument(
            "--max-gap", type=float, default=2.0, help="Maximum gap in seconds to group subtitles (default: 2.0)"
        )
        parser.add_argument(
            "--min-length", type=int, default=20, help="Minimum character length for quotes (default: 20)"
        )

    def handle(self, *args, **options):
        source_id = options["source_id"]
        srt_file = options["srt_file"]
        max_gap = options["max_gap"]
        min_length = options["min_length"]

        # Check if file exists
        if not os.path.exists(srt_file):
            raise CommandError(f"File not found: {srt_file}")

        # Check if source exists
        try:
            source = Source.objects.get(id=source_id)
        except Source.DoesNotExist:
            raise CommandError(f"Source with id {source_id} not found")

        self.stdout.write(f"Importing quotes for: {source.title}")
        self.stdout.write(f"From file: {srt_file}")

        try:
            count = import_quotes_from_srt(
                source_id=source_id, srt_file_path=srt_file, max_gap=max_gap, min_length=min_length
            )

            self.stdout.write(self.style.SUCCESS(f"✓ Successfully imported {count} quotes!"))
        except Exception as e:
            raise CommandError(f"Error importing subtitles: {str(e)}")
