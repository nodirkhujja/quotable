"""
Import usage examples and grammar notes into existing WordNotes.

Matches by word name (case-insensitive) against the user's saved words.

Format (pipe-delimited):
    | word | translation | level | usage_en1=usage_uz1;usage_en2=usage_uz2 | grammar_note |

Usage:
    poetry run python -m core.manage import_usage --file media/using_words/friends.s01.e01.srt
    poetry run python -m core.manage import_usage --file media/using_words/friends.s01.e01.srt --user admin
"""

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from learning.models import WordNote

User = get_user_model()


def _parse_usage(raw):
    if not raw:
        return []
    examples = []
    for pair in raw.split(";"):
        pair = pair.strip()
        if "=" in pair:
            en, uz = pair.split("=", 1)
            examples.append({"en": en.strip(), "uz": uz.strip()})
        elif pair:
            examples.append({"en": pair, "uz": ""})
    return examples


def parse_file(filepath):
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("| ---") or "| Word" in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p]
            if len(parts) < 2:
                continue
            entries.append(
                {
                    "word": parts[0].strip().lower(),
                    "translation": parts[1].strip() if len(parts) > 1 else "",
                    "usage_raw": parts[3].strip() if len(parts) > 3 else "",
                    "grammar_note": parts[4].strip() if len(parts) > 4 else "",
                }
            )
    return entries


class Command(BaseCommand):
    help = "Import usage examples and grammar notes into existing WordNotes"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=str, required=True)
        parser.add_argument("--user", type=str, default="", help="Username (default: all users)")

    def handle(self, *args, **options):
        filepath = Path(options["file"])
        if not filepath.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {filepath}"))
            return

        entries = parse_file(filepath)
        self.stdout.write(f"Parsed {len(entries)} words from {filepath.name}")

        # Build WordNote lookup
        qs = WordNote.objects.all()
        if options["user"]:
            user = User.objects.filter(username=options["user"]).first()
            if not user:
                self.stderr.write(self.style.ERROR(f"User '{options['user']}' not found"))
                return
            qs = qs.filter(user=user)

        # Group notes by word (lowercase)
        notes_by_word = {}
        for note in qs:
            key = note.word.lower()
            notes_by_word.setdefault(key, []).append(note)

        updated = 0
        skipped = 0

        for entry in entries:
            word = entry["word"]
            notes = notes_by_word.get(word, [])

            if not notes:
                self.stderr.write(f"  No WordNote for '{word}' — skipped")
                skipped += 1
                continue

            usage = _parse_usage(entry["usage_raw"])
            grammar = entry["grammar_note"]

            for note in notes:
                changed = False
                if usage and not note.usage_examples:
                    note.usage_examples = usage
                    changed = True
                if grammar and not note.grammar_note:
                    note.grammar_note = grammar
                    changed = True
                # Also backfill translation if empty
                if entry["translation"] and not note.translation:
                    note.translation = entry["translation"]
                    changed = True
                if changed:
                    note.save()
                    updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done: {updated} updated, {skipped} skipped"))
