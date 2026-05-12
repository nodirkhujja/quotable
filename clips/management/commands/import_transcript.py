# clips/management/commands/import_transcript.py
#
# TV Show (auto-detects season/episode from filename):
#   make import-trans source=1 file=friends.s01.e01.srt
#
# Movie:
#   make import-trans source=5 file=inception.srt
#
# With replace:
#   make import-trans source=1 file=friends.s01.e01.srt replace=1

import re
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clips.models import Episode, Source, Transcript


def parse_srt_time(time_str):
    time_str = time_str.strip().replace(",", ".")
    h, m, rest = time_str.split(":")
    s, ms = rest.split(".")
    total = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    return Decimal(str(round(total, 3)))


def parse_srt(content):
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n{2,}", content)
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            int(lines[0].strip())
        except ValueError:
            continue
        time_match = re.match(r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}[,\.]\d{3})", lines[1])
        if not time_match:
            continue
        start = parse_srt_time(time_match.group(1))
        end = parse_srt_time(time_match.group(2))
        raw = " ".join(lines[2:]).strip()
        text = re.sub(r"<[^>]+>", "", raw).strip()
        if not text:
            continue
        entries.append({"start": start, "end": end, "text": text})
    return entries


class Command(BaseCommand):
    help = "Import SRT file as Transcript entries for a TV episode or movie"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True, help="Source ID")
        parser.add_argument("--srt", type=str, required=True, help="Path to .srt file")
        parser.add_argument("--replace", action="store_true", help="Delete existing transcripts first")

    def handle(self, *args, **options):
        source_id = options["source"]
        srt_path = Path(options["srt"])
        replace = options["replace"]

        # Validate source
        try:
            source = Source.objects.get(id=source_id)
        except Source.DoesNotExist:
            raise CommandError(f"Source with id={source_id} does not exist")

        self.stdout.write(f"Source: {source} (id={source.id}, type={source.source_type})")

        # Validate SRT file
        if not srt_path.exists():
            raise CommandError(f"SRT file not found: {srt_path}")

        # ── Determine target: episode (TV) or source (movie) ──
        episode = None
        season = ep_num = None

        if source.source_type == "tv_show":
            match = re.search(r"s(\d+)\.?e(\d+)", srt_path.stem, re.IGNORECASE)
            if not match:
                raise CommandError(
                    f'Could not detect season/episode from filename "{srt_path.name}". '
                    f"Name it like friends.s01.e01.srt or s01e01.srt"
                )
            season = int(match.group(1))
            ep_num = int(match.group(2))
            try:
                episode = Episode.objects.get(
                    source=source,
                    season=season,
                    episode_number=ep_num,
                )
            except Episode.DoesNotExist:
                raise CommandError(
                    f'Episode S{season:02d}E{ep_num:02d} not found under source "{source}" (id={source_id}). '
                    f"Check the episode exists in the database."
                )
            self.stdout.write(f"Episode: S{season:02d}E{ep_num:02d} — {episode.title or 'untitled'} (id={episode.id})")
        else:
            self.stdout.write(f'Movie: attaching transcripts directly to source "{source}"')

        # Read SRT
        try:
            content = srt_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = srt_path.read_text(encoding="latin-1")
            self.stdout.write(self.style.WARNING("Used latin-1 encoding fallback"))

        entries = parse_srt(content)
        if not entries:
            raise CommandError("No valid subtitle entries found in SRT file")

        self.stdout.write(f"Parsed {len(entries)} subtitle lines")

        # Import
        with transaction.atomic():
            if replace:
                if episode:
                    deleted, _ = Transcript.objects.filter(episode=episode).delete()
                else:
                    deleted, _ = Transcript.objects.filter(source=source, episode=None).delete()
                self.stdout.write(f"Deleted {deleted} existing transcripts")

            # Guard against double import
            existing = (
                Transcript.objects.filter(episode=episode).count()
                if episode
                else Transcript.objects.filter(source=source, episode=None).count()
            )
            if existing > 0 and not replace:
                self.stdout.write(
                    self.style.WARNING(f"{existing} transcripts already exist. Use --replace to overwrite.")
                )
                return

            # Always save source on every transcript for easy querying
            Transcript.objects.bulk_create(
                [
                    Transcript(
                        source=source,  # always set — TV and movie both
                        episode=episode,  # set for TV, None for movie
                        text=e["text"],
                        start_time=e["start"],
                        end_time=e["end"],
                    )
                    for e in entries
                ],
                batch_size=500,
            )

        label = f"S{season:02d}E{ep_num:02d}" if episode else source.title
        self.stdout.write(self.style.SUCCESS(f'Successfully imported {len(entries)} lines for "{label}"'))
