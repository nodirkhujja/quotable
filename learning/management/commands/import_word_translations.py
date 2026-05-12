"""Import batch-translated word files into WordTranslation model."""

import csv
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from vocab.models import WordTranslation


class Command(BaseCommand):
    help = "Import batch translation files (Word,Uzbek Translation,Level) into WordTranslation"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            type=str,
            default="media/translation/batch_translation",
            help="Directory containing batch_*.txt files",
        )
        parser.add_argument(
            "--freq-file",
            type=str,
            default="word_list.csv",
            help="word_list.csv for frequency data",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing WordTranslation entries before importing",
        )

    def handle(self, **opts):
        batch_dir = Path(opts["dir"])
        if not batch_dir.exists():
            self.stderr.write(f"Directory not found: {batch_dir}")
            return

        # Load frequency map
        freq_map = {}
        freq_file = Path(opts["freq_file"])
        if freq_file.exists():
            with open(freq_file) as f:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) == 2:
                        try:
                            freq_map[parts[0].lower()] = int(parts[1])
                        except ValueError:
                            pass

        # Find all batch files
        batch_files = sorted(batch_dir.glob("batch_*.txt"))
        if not batch_files:
            self.stderr.write("No batch_*.txt files found")
            return

        self.stdout.write(f"Found {len(batch_files)} batch files")

        if opts["clear"]:
            deleted = WordTranslation.objects.all().delete()[0]
            self.stdout.write(f"Cleared {deleted} existing entries")

        # Parse all batch files
        entries = []
        for bf in batch_files:
            with open(bf, encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, None)  # skip header
                if not header:
                    continue

                for row in reader:
                    if len(row) < 3:
                        continue
                    raw_word = row[0].strip()
                    translation = row[1].strip()
                    level = row[2].strip().upper()

                    if not raw_word or not translation:
                        continue

                    # Clean word: "wan (want)" → "want", "wed (wedding)" → "wedding"
                    m = re.match(r"^.+?\((.+?)\)\s*$", raw_word)
                    if m:
                        word = m.group(1).strip().lower()
                    else:
                        word = raw_word.lower()

                    # Normalize level
                    if level not in ("A1", "A2", "B1", "B2", "C1", "C2"):
                        level = "B1"

                    freq = freq_map.get(word, 0)
                    entries.append((word, translation, level, freq))

        self.stdout.write(f"Parsed {len(entries)} word entries")

        # Bulk upsert
        created = 0
        updated = 0
        for word, translation, level, freq in entries:
            obj, was_created = WordTranslation.objects.update_or_create(
                word=word,
                defaults={
                    "translation": translation,
                    "level": level,
                    "frequency": freq,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done: {created} created, {updated} updated, {created + updated} total"))
