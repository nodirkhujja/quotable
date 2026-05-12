"""Import transcript translation CSV files into LineTranslation model.

File format (from translation/transcript/ folder):
  Transcript #,English,Uzbek Translation
  5,There's nothing to tell.,Aytarli gap yo'q.
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from clips.models import Episode, Source, Transcript
from vocab.models import LineTranslation


class Command(BaseCommand):
    help = "Import a transcript translation CSV into LineTranslation objects"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True)
        parser.add_argument("--season", type=int, required=True)
        parser.add_argument("--episode", type=int, required=True)
        parser.add_argument("--file", type=str, required=True, help="Path to CSV file")

    def handle(self, **opts):
        source = Source.objects.get(id=opts["source"])
        season = opts["season"]
        ep_num = opts["episode"]
        episode = Episode.objects.filter(source=source, season=season, episode_number=ep_num).first()

        filepath = Path(opts["file"])
        if not filepath.exists():
            self.stderr.write(f"File not found: {filepath}")
            return

        # Build transcript map: row_number -> Transcript
        qs = Transcript.objects.filter(source=source, episode=episode).order_by("start_time")
        transcript_map = {i: t for i, t in enumerate(qs, start=1)}

        # Clear existing translations for this episode
        LineTranslation.objects.filter(source=source, season=season, episode_number=ep_num).delete()

        rows = []
        with open(filepath, encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 3:
                    continue
                try:
                    line_num = int(row[0].strip())
                except ValueError:
                    continue  # skip headers
                english = row[1].strip().strip('"')
                translation = row[2].strip().strip('"')
                if not translation:
                    continue
                rows.append((line_num, english, translation))

        created = 0
        skipped = 0
        for line_num, english, translation in rows:
            transcript = transcript_map.get(line_num)
            if not transcript:
                self.stdout.write(f"  No transcript #{line_num} — skipped")
                skipped += 1
                continue

            LineTranslation.objects.update_or_create(
                transcript=transcript,
                defaults={
                    "source": source,
                    "episode": episode,
                    "translation": translation,
                    "season": season,
                    "episode_number": ep_num,
                },
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Done: {created} translations imported, {skipped} skipped"))
