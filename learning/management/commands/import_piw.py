"""Import PIW (Phrases, Idioms, Words) files into LineVocab model.

Supported formats
─────────────────
YAML (preferred — human readable):
    - line:        5
      english:     There is nothing to tell
      uzbek:       "Aytarli gap yo'q"
      type:        phrase
      level:       B1
      category:    social
      description: Used to say there is nothing interesting to share.
      example1:    There is nothing to tell — we just had coffee.
      example2:    She said there was nothing to tell, but I knew otherwise.
      pattern:     ""

CSV legacy (9-col):
    #,English,Uzbek,Type,Level,Category,Description,Example,Pattern

CSV legacy (7-col, no level/category):
    #,English,Uzbek,Type,Description,Example,Pattern
"""

import csv
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand

from clips.models import Episode, Source, Transcript
from vocab.models import LineVocab

VALID_KINDS = {k for k, _ in LineVocab.KIND_CHOICES}
VALID_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}


def _detect_kind(english):
    words = english.split()
    lower = english.lower()
    idiom_signals = [
        "it turns out",
        "it hits me",
        "a dear diary",
        "grab a spoon",
        "on a roll",
        "for the best",
        "i wish i could",
    ]
    if any(s in lower for s in idiom_signals):
        return "idiom"
    if "[" in english or len(words) >= 3:
        return "phrase"
    if len(words) == 2:
        return "phrasal_verb"
    return "noun"


def _load_yaml(filepath):
    with open(filepath, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rows = []
    for entry in data:
        try:
            line_num = int(str(entry.get("line", "")).strip())
        except (ValueError, TypeError):
            continue
        english = str(entry.get("english", "")).strip()
        uzbek = str(entry.get("uzbek", "")).strip()
        kind = str(entry.get("type", "")).strip()
        level = str(entry.get("level", "")).strip()
        category = str(entry.get("category", "")).strip()
        desc = str(entry.get("description", "")).strip()
        ex1 = str(entry.get("example1", "")).strip()
        ex2 = str(entry.get("example2", "")).strip()
        pattern = str(entry.get("pattern", "")).strip()
        example = f"{ex1} / {ex2}" if ex2 else ex1
        if not english or not uzbek:
            continue
        if kind not in VALID_KINDS:
            kind = _detect_kind(english)
        if level not in VALID_LEVELS:
            level = ""
        rows.append((line_num, english, uzbek, kind, level, category, desc, example, pattern))
    return rows


def _load_csv(filepath):
    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, [])
    num_cols = len(header)

    rows = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 3:
                continue
            try:
                line_num = int(row[0].strip())
            except ValueError:
                continue
            english = row[1].strip().strip('"')
            uzbek = row[2].strip().strip('"')
            if not english or not uzbek:
                continue

            if num_cols >= 9:
                # 9-col: #,English,Uzbek,Type,Level,Category,Description,Example,Pattern
                kind = row[3].strip()
                level = row[4].strip() if len(row) > 4 else ""
                category = row[5].strip() if len(row) > 5 else ""
                desc = row[6].strip().strip('"') if len(row) > 6 else ""
                example = row[7].strip().strip('"') if len(row) > 7 else ""
                pattern = row[8].strip().strip('"') if len(row) > 8 else ""
            elif num_cols >= 7:
                # 7-col: #,English,Uzbek,Type,Description,Example,Pattern
                kind = row[3].strip()
                level = ""
                category = ""
                if len(row) > 7:
                    desc_parts = [c.strip().strip('"') for c in row[4:-2]]
                    desc = ", ".join(p for p in desc_parts if p)
                    example = row[-2].strip().strip('"')
                    pattern = row[-1].strip().strip('"')
                else:
                    desc = row[4].strip().strip('"') if len(row) > 4 else ""
                    example = row[5].strip().strip('"') if len(row) > 5 else ""
                    pattern = row[6].strip().strip('"') if len(row) > 6 else ""
            else:
                kind = ""
                level = ""
                category = ""
                desc = row[3].strip().strip('"') if len(row) > 3 else ""
                example = ""
                pattern = ""

            if kind not in VALID_KINDS:
                kind = _detect_kind(english)
            if level not in VALID_LEVELS:
                level = ""
            rows.append((line_num, english, uzbek, kind, level, category, desc, example, pattern))
    return rows


class Command(BaseCommand):
    help = "Import a PIW YAML (or CSV) file into LineVocab objects"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True)
        parser.add_argument("--season", type=int, required=True)
        parser.add_argument("--episode", type=int, required=True)
        parser.add_argument("--file", type=str, required=True)

    def handle(self, **opts):
        source = Source.objects.get(id=opts["source"])
        season = opts["season"]
        ep_num = opts["episode"]
        episode = Episode.objects.filter(source=source, season=season, episode_number=ep_num).first()
        filepath = Path(opts["file"])

        if not filepath.exists():
            self.stderr.write(f"File not found: {filepath}")
            return

        # Load rows depending on format
        suffix = filepath.suffix.lower()
        if suffix in (".yaml", ".yml"):
            rows = _load_yaml(filepath)
            fmt = "YAML"
        else:
            rows = _load_csv(filepath)
            fmt = "CSV"

        self.stdout.write(f"Loaded {len(rows)} entries from {fmt} file")

        # Build transcript map: sequential index → Transcript
        qs = Transcript.objects.filter(source=source, episode=episode).order_by("start_time")
        transcript_map = {i: t for i, t in enumerate(qs, start=1)}

        # Clear existing vocab for this episode
        LineVocab.objects.filter(source=source, season=season, episode_number=ep_num).delete()

        created = 0
        skipped = 0
        counts = {}

        for entry in rows:
            line_num, english, uzbek, kind, level, category, desc, example, pattern = entry
            transcript = transcript_map.get(line_num)
            if not transcript:
                self.stdout.write(f"  No transcript #{line_num} — skipped ({english})")
                skipped += 1
                continue

            LineVocab.objects.update_or_create(
                transcript=transcript,
                english=english,
                defaults={
                    "source": source,
                    "episode": episode,
                    "translation": uzbek,
                    "kind": kind,
                    "level": level,
                    "category": category,
                    "description": desc,
                    "example": example,
                    "pattern": pattern,
                    "season": season,
                    "episode_number": ep_num,
                },
            )
            counts[kind] = counts.get(kind, 0) + 1
            created += 1

        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f"Done: {created} vocab imported ({summary}), {skipped} skipped"))
