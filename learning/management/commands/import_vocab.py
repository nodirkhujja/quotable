"""Import vocabulary from the two-file relational system:

  lexicon.yaml      — canonical vocab dictionary (all episodes)
  ep{NN}_index.yaml — per-episode index (vocab_id → line number)

Usage:
  poetry run python -m core.manage import_vocab \
      --source 1 --season 1 --episode 1 \
      --lexicon media/translation/piw/lexicon.yaml \
      --index media/translation/piw/ep01_index.yaml
"""

from pathlib import Path

import yaml
from django.core.management.base import BaseCommand

from clips.models import Episode, Source, Transcript
from vocab.models import LineVocab

VALID_KINDS = {k for k, _ in LineVocab.KIND_CHOICES}
VALID_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
VALID_CATEGORIES = {k for k, _ in LineVocab.CATEGORY_CHOICES}


class Command(BaseCommand):
    help = "Import vocab from lexicon.yaml + per-episode ep{NN}_index.yaml into LineVocab"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True)
        parser.add_argument("--season", type=int, required=True)
        parser.add_argument("--episode", type=int, required=True)
        parser.add_argument("--lexicon", type=str, required=True, help="Path to lexicon.yaml")
        parser.add_argument("--index", type=str, required=True, help="Path to ep{NN}_index.yaml")
        parser.add_argument("--dry-run", action="store_true", help="Validate only, don't write to DB")

    def handle(self, **opts):
        source = Source.objects.get(id=opts["source"])
        season = opts["season"]
        ep_num = opts["episode"]
        episode = Episode.objects.filter(source=source, season=season, episode_number=ep_num).first()
        lexicon_path = Path(opts["lexicon"])
        index_path = Path(opts["index"])
        dry_run = opts["dry_run"]

        if not lexicon_path.exists():
            self.stderr.write(f"Lexicon file not found: {lexicon_path}")
            return
        if not index_path.exists():
            self.stderr.write(f"Index file not found: {index_path}")
            return

        # ── Load lexicon ──
        with open(lexicon_path, encoding="utf-8") as f:
            lexicon_data = yaml.safe_load(f)

        lexicon = {}
        for entry in lexicon_data:
            vid = entry.get("id", "").strip()
            if not vid:
                continue
            lexicon[vid] = entry

        self.stdout.write(f"Loaded {len(lexicon)} lexicon entries")

        # ── Load episode index ──
        with open(index_path, encoding="utf-8") as f:
            index_data = yaml.safe_load(f)

        ep_entries = []
        for entry in index_data:
            vid = entry.get("id", "").strip()
            try:
                line_num = int(entry.get("line", 0))
            except (ValueError, TypeError):
                continue
            if vid and line_num > 0:
                ep_entries.append((vid, line_num))

        self.stdout.write(f"Loaded {len(ep_entries)} episode index entries")

        # ── Build transcript map: line_number → Transcript ──
        qs = Transcript.objects.filter(source=source, episode=episode).order_by("start_time")
        transcript_map = {i: t for i, t in enumerate(qs, start=1)}
        self.stdout.write(f"Found {len(transcript_map)} transcript lines")

        # ── Validate ──
        errors = []
        for vid, line_num in ep_entries:
            if vid not in lexicon:
                errors.append(f"ID '{vid}' not found in lexicon")
            if line_num not in transcript_map:
                errors.append(f"Line {line_num} (for '{vid}') not in transcripts")

        if errors:
            self.stderr.write(self.style.ERROR(f"\n{len(errors)} errors found:"))
            for err in errors:
                self.stderr.write(f"  - {err}")
            if not dry_run:
                self.stderr.write("Fix errors before importing.")
            return

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry run OK: {len(ep_entries)} entries, 0 errors"))
            return

        # ── Clear existing vocab for this episode ──
        deleted, _ = LineVocab.objects.filter(source=source, season=season, episode_number=ep_num).delete()
        if deleted:
            self.stdout.write(f"Cleared {deleted} existing LineVocab for S{season:02d}E{ep_num:02d}")

        # ── Import ──
        created = 0
        counts = {}

        for vid, line_num in ep_entries:
            m = lexicon[vid]
            transcript = transcript_map[line_num]

            english = m.get("english", "").strip()
            uzbek = m.get("uzbek", "").strip()
            kind = m.get("type", "").strip()
            level = m.get("level", "").strip()
            category = m.get("category", "").strip()
            description = m.get("description", "").strip()
            pattern = m.get("pattern", "").strip()

            # Build example string from examples list
            examples = m.get("examples", [])
            if isinstance(examples, list):
                example = " / ".join(str(e).strip() for e in examples[:3])
            else:
                example = ""

            tavsif = m.get("tavsif", "").strip() if isinstance(m.get("tavsif"), str) else ""
            smart_mcq = m.get("smart_mcq", {}) if isinstance(m.get("smart_mcq"), dict) else {}
            usage_check = m.get("usage_check", {}) if isinstance(m.get("usage_check"), dict) else {}
            # example_pairs: [{en, uz}, ...] for retranslation quiz
            raw_pairs = m.get("example_pairs") or m.get("translated_examples") or []
            translated_examples = []
            if isinstance(raw_pairs, list):
                for p in raw_pairs[:5]:
                    if isinstance(p, dict) and p.get("en") and p.get("uz"):
                        translated_examples.append(
                            {
                                "en": str(p["en"]).strip(),
                                "uz": str(p["uz"]).strip(),
                            }
                        )

            # confusable_with and collocations
            confusable_with = m.get("confusable_with", [])
            if not isinstance(confusable_with, list):
                confusable_with = []
            collocations = m.get("collocations", [])
            if not isinstance(collocations, list):
                collocations = []

            # Validate kind
            if kind not in VALID_KINDS:
                kind = "phrase"
            if level not in VALID_LEVELS:
                level = ""
            if category not in VALID_CATEGORIES:
                category = "general"

            LineVocab.objects.update_or_create(
                transcript=transcript,
                english=english,
                defaults={
                    "vocab_id": vid,
                    "source": source,
                    "episode": episode,
                    "translation": uzbek,
                    "kind": kind,
                    "level": level,
                    "category": category,
                    "description": description,
                    "example": example,
                    "pattern": pattern,
                    "tavsif": tavsif,
                    "smart_mcq": smart_mcq,
                    "usage_check": usage_check,
                    "translated_examples": translated_examples,
                    "confusable_with": confusable_with,
                    "collocations": collocations,
                    "season": season,
                    "episode_number": ep_num,
                },
            )
            counts[kind] = counts.get(kind, 0) + 1
            created += 1

        summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        self.stdout.write(self.style.SUCCESS(f"Done: {created} vocab imported ({summary})"))
