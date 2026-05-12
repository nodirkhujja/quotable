"""Import learn-mode CSV files (line translations + accents) into LearnLine model."""

import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from clips.models import Episode, Source, Transcript
from learning.models import LearnLine


class Command(BaseCommand):
    help = "Import a learn CSV file into LearnLine objects"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True)
        parser.add_argument("--season", type=int, required=True)
        parser.add_argument("--episode", type=int, required=True)
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            help="Filename inside media/translation/ (e.g. friends.s01.e01.learn.srt)",
        )

    def handle(self, **opts):
        source = Source.objects.get(id=opts["source"])
        season = opts["season"]
        ep_num = opts["episode"]
        episode = Episode.objects.filter(source=source, season=season, episode_number=ep_num).first()

        filepath = Path("media/translation") / opts["file"]
        if not filepath.exists():
            self.stderr.write(f"File not found: {filepath}")
            return

        # Build ordered transcript map: row_number -> Transcript object
        qs = Transcript.objects.filter(source=source, episode=episode).order_by("start_time")
        transcript_map = {}
        for i, t in enumerate(qs, start=1):
            transcript_map[i] = t

        # Parse CSV
        rows = []
        with open(filepath, encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)  # skip header
            for row in reader:
                if len(row) < 3:
                    continue
                # Skip any extra header rows
                try:
                    line_num = int(row[0].strip())
                except ValueError:
                    continue
                english = row[1].strip().strip('"')
                translation = row[2].strip().strip('"')
                accents_raw = row[3].strip() if len(row) > 3 else ""
                accents = self._parse_accents(accents_raw)
                rows.append((line_num, english, translation, accents))

        # Clear existing learn lines for this episode
        LearnLine.objects.filter(source=source, season=season, episode_number=ep_num).delete()

        created = 0
        skipped = 0
        for line_num, english, translation, accents in rows:
            transcript = transcript_map.get(line_num)
            if not transcript:
                self.stdout.write(f"  No transcript line #{line_num} — skipped")
                skipped += 1
                continue

            existing = LearnLine.objects.filter(transcript=transcript).first()
            if existing:
                # Merge accents from duplicate lines
                existing.accents = existing.accents + accents if accents else existing.accents
                existing.save()
                self.stdout.write(f"  Merged accents into line #{line_num}")
                continue

            LearnLine.objects.create(
                source=source,
                episode=episode,
                transcript=transcript,
                english=english,
                translation=translation,
                accents=accents,
                season=season,
                episode_number=ep_num,
            )
            created += 1

        self.stdout.write(f"Done: {created} created, {skipped} skipped")

    def _parse_accents(self, raw):
        """Parse accents in either format:
        - 'word (translation); word2 (translation2)'
        - 'word — translation; word2 — translation2'
        """
        accents = []
        parts = [p.strip() for p in raw.split(";") if p.strip()]
        for part in parts:
            # Format 1: word (translation)
            m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", part)
            if m:
                accents.append({"word": m.group(1).strip(), "translation": m.group(2).strip()})
                continue
            # Format 2: word — translation
            if " — " in part:
                word, translation = part.split(" — ", 1)
                accents.append({"word": word.strip(), "translation": translation.strip()})
                continue
            # Fallback: just the word
            if part:
                accents.append({"word": part, "translation": ""})
        return accents
