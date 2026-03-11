"""
Import curated translations from pipe-delimited files into SuggestedWord.

Supports TWO formats:

1. WITH timestamps (original):
   | Timestamp    | Word / Phrase | Uzbek Translation   | Level    |
   | 00:00:45,893 | go out        | uchrashuvga chiqmoq | Beginner |

2. WITHOUT timestamps (simple — auto-matches to subtitles):
   | Word / Phrase | Uzbek Translation   | Level    |
   | go out        | uchrashuvga chiqmoq | Beginner |

Optional extra columns (both formats):
   | ... | Level | Usage examples | Grammar note |
   Usage examples: semicolon-separated pairs: "That's awful=Bu dahshatli;I feel awful=O'zimni yomon his qilyapman"
   Grammar note: e.g. "awful (adj) → awfully (adv)"

Usage:
    poetry run python -m core.manage import_translations --source 1 --season 1 --episode 1 --file media/translation/friends.s01.e01.srt
    make import-words-all
"""

import re
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db.models import Q

from clips.models import Episode, Source, Transcript
from learning.models import SuggestedWord


def parse_timestamp(ts):
    """Convert 'HH:MM:SS,mmm' or 'HH:MM:SS' to seconds as Decimal."""
    ts = ts.strip()
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", ts)
    if m:
        h, mi, s, ms = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        return Decimal(str(h * 3600 + mi * 60 + s)) + Decimal(ms) / 1000
    m = re.match(r"(\d+):(\d+):(\d+)$", ts)
    if m:
        h, mi, s = int(m[1]), int(m[2]), int(m[3])
        return Decimal(str(h * 3600 + mi * 60 + s))
    return None


def parse_file(filepath):
    """Parse pipe-delimited translation file. Auto-detects format."""
    entries = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("| ---") or "| Timestamp" in line or "| Word" in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p]
            if len(parts) < 2:
                continue

            # Try to detect if first column is a timestamp
            ts = parse_timestamp(parts[0])

            if ts is not None and len(parts) >= 3:
                # Format 1: timestamp | word | translation | level? | usage? | grammar?
                base_idx = 1
                entries.append(
                    {
                        "timestamp": ts,
                        "word": parts[1].strip(),
                        "translation": parts[2].strip(),
                        "level": parts[3].strip() if len(parts) > 3 else "",
                        "usage_raw": parts[4].strip() if len(parts) > 4 else "",
                        "grammar_note": parts[5].strip() if len(parts) > 5 else "",
                    }
                )
            elif len(parts) >= 2:
                # Format 2: word | translation | level? | usage? | grammar?
                entries.append(
                    {
                        "timestamp": None,
                        "word": parts[0].strip(),
                        "translation": parts[1].strip(),
                        "level": parts[2].strip() if len(parts) > 2 else "",
                        "usage_raw": parts[3].strip() if len(parts) > 3 else "",
                        "grammar_note": parts[4].strip() if len(parts) > 4 else "",
                    }
                )
    return entries


def _parse_usage(raw):
    """Parse usage string: 'That is awful=Bu dahshatli;I feel awful=Yomon' -> [{"en":..,"uz":..}]"""
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


def _strip_parens(word):
    """Strip parenthetical hints: 'fluff (a pillow)' -> 'fluff', 'dump (someone)' -> 'dump'."""
    m = re.match(r"^(.+?)\s*\(.*\)\s*$", word)
    return m.group(1).strip() if m else word


def _whole_word_match(text, word):
    """Check if word appears as a whole word (or stem) in text.

    'tan' should NOT match 'understand', but 'pro' SHOULD match 'pros',
    'dread' SHOULD match 'dreading'.
    """
    # Match word at word boundary, allowing common suffixes (s, ed, ing, ly, er, est)
    pattern = r"\b" + re.escape(word) + r"(?:s|ed|ing|ly|er|est|d)?\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def find_transcript_by_text(episode, word):
    """Search subtitle lines for a word/phrase. Returns first match.

    Uses whole-word matching in Python to avoid 'tan' matching 'understand'.
    For phrases like "puts in perspective": finds lines containing ALL
    significant words (skips common filler like 'in', 'the', 'a', 'to').
    """
    STOP_WORDS = {"a", "an", "the", "in", "on", "at", "to", "of", "is", "it", "up", "for"}

    # Strip parenthetical hints: "fluff (a pillow)" -> "fluff"
    word = _strip_parens(word)

    # Pre-filter with icontains (fast DB query), then verify with whole-word in Python
    candidates = list(
        Transcript.objects.filter(episode=episode)
        .filter(text__icontains=word.split()[0])  # filter by first word for speed
        .order_by("start_time")
    )

    # First try: exact whole-word match for the full phrase/word
    for tr in candidates:
        if _whole_word_match(tr.text, word):
            return tr

    # Second try: for phrases, find lines containing all key words
    parts = word.lower().split()
    if len(parts) < 2:
        # Single word — also try stemmed search across ALL lines
        stem = word.rstrip("s").rstrip("ing").rstrip("ed") if len(word) > 3 else word
        if stem != word:
            all_lines = Transcript.objects.filter(episode=episode).order_by("start_time")
            for tr in all_lines:
                if _whole_word_match(tr.text, stem):
                    return tr
        return None

    # Get the significant words (skip stop words, keep stems)
    key_words = []
    for p in parts:
        clean = p.strip("'\".,!?")
        if clean and clean not in STOP_WORDS:
            stem = clean.rstrip("s").rstrip("ing").rstrip("ed") if len(clean) > 3 else clean
            key_words.append(stem if len(stem) >= 3 else clean)

    if not key_words:
        return None

    # Search all lines for ones containing ALL key words (whole-word)
    all_lines = Transcript.objects.filter(episode=episode).order_by("start_time")
    for tr in all_lines:
        text = tr.text
        if all(_whole_word_match(text, kw) for kw in key_words):
            return tr

    return None


def find_transcript_by_timestamp(episode, ts):
    """Find subtitle line near a timestamp (±2s, fallback ±5s)."""
    transcript = (
        Transcript.objects.filter(episode=episode)
        .filter(start_time__lte=ts + 2, end_time__gte=ts - 2)
        .order_by("start_time")
        .first()
    )
    if not transcript:
        transcript = (
            Transcript.objects.filter(episode=episode)
            .filter(start_time__lte=ts + 5, end_time__gte=ts - 5)
            .order_by("start_time")
            .first()
        )
    return transcript


class Command(BaseCommand):
    help = "Import curated translations from pipe-delimited files into SuggestedWord"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True, help="Source ID")
        parser.add_argument("--season", type=int, required=True, help="Season number")
        parser.add_argument("--episode", type=int, required=True, help="Episode number")
        parser.add_argument("--file", type=str, required=True, help="Path to translation file")

    def handle(self, *args, **options):
        source_id = options["source"]
        season = options["season"]
        ep_num = options["episode"]
        filepath = Path(options["file"])

        if not filepath.exists():
            self.stderr.write(self.style.ERROR(f"File not found: {filepath}"))
            return

        source = Source.objects.filter(id=source_id).first()
        if not source:
            self.stderr.write(self.style.ERROR(f"Source {source_id} not found"))
            return

        episode = Episode.objects.filter(source=source, season=season, episode_number=ep_num).first()
        if not episode:
            self.stderr.write(self.style.ERROR(f"Episode S{season:02d}E{ep_num:02d} not found"))
            return

        entries = parse_file(filepath)
        has_timestamps = any(e["timestamp"] is not None for e in entries)
        mode = "timestamp" if has_timestamps else "text-search"
        self.stdout.write(f"Parsed {len(entries)} words from {filepath.name} (mode: {mode})")

        created = 0
        updated = 0
        skipped = 0

        for entry in entries:
            word = entry["word"].lower()

            # Find the matching subtitle line
            if entry["timestamp"] is not None:
                transcript = find_transcript_by_timestamp(episode, entry["timestamp"])
            else:
                transcript = find_transcript_by_text(episode, word)

            if not transcript:
                self.stderr.write(f"  No subtitle match for '{word}' — skipped")
                skipped += 1
                continue

            # Map level
            raw_level = entry["level"].lower().strip()
            if raw_level.startswith("inter"):
                level = SuggestedWord.LEVEL_INTERMEDIATE
            elif raw_level.startswith("adv"):
                level = SuggestedWord.LEVEL_ADVANCED
            else:
                level = SuggestedWord.LEVEL_BEGINNER

            defaults = {
                "translation": entry["translation"],
                "level": level,
                "sentence": transcript.text,
                "source": source,
                "episode": episode,
                "start_time": transcript.start_time,
                "end_time": transcript.end_time,
                "season": season,
                "episode_number": ep_num,
            }

            # Add usage/grammar if present
            usage = _parse_usage(entry.get("usage_raw", ""))
            grammar = entry.get("grammar_note", "")
            if usage:
                defaults["usage_examples"] = usage
            if grammar:
                defaults["grammar_note"] = grammar

            _, was_created = SuggestedWord.objects.update_or_create(
                transcript=transcript,
                word=word,
                defaults=defaults,
            )

            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done: {created} created, {updated} updated, {skipped} skipped"))
