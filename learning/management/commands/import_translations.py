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
from vocab.models import SuggestedWord


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


def _detect_format(filepath):
    """Detect if file is CSV or pipe-delimited."""
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                return "pipe"
            if "," in line:
                return "csv"
    return "pipe"


def _parse_csv(filepath):
    """Parse CSV format: Word/Phrase,Level,Definition,Uzbek Translation"""
    import csv

    entries = []
    with open(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = None
        for row in reader:
            if not row or not row[0].strip():
                continue

            # Detect header rows
            first = row[0].strip().lower()
            if first in ("word / phrase", "word", "phrase", "level"):
                header = [c.strip().lower() for c in row]
                continue

            # Skip if looks like a header
            if first.startswith("---"):
                continue

            if len(row) < 2:
                continue

            # CSV format: word, level, definition, translation
            word = row[0].strip()
            level_raw = row[1].strip().lower() if len(row) > 1 else ""
            definition = row[2].strip() if len(row) > 2 else ""
            translation = row[3].strip() if len(row) > 3 else ""

            # Map level
            if level_raw.startswith(("a1", "a2", "beginner")):
                level = "beginner"
            elif level_raw.startswith(("b1", "b2", "inter")):
                level = "intermediate"
            elif level_raw.startswith(("c1", "c2", "adv")):
                level = "advanced"
            else:
                level = "intermediate"

            entries.append(
                {
                    "timestamp": None,
                    "word": word,
                    "definition": definition,
                    "translation": translation,
                    "level": level,
                    "usage_raw": "",
                    "grammar_note": "",
                }
            )
    return entries


def parse_file(filepath):
    """Parse translation file. Auto-detects CSV or pipe-delimited format.

    CSV format:
        Word / Phrase,Level,Definition,Uzbek Translation
        naked,A1,Not wearing clothes,yalang'och

    Pipe-delimited with section headers:
        BEGINNER (A1-A2)
        | word | definition | translation |

    Pipe-delimited flat:
        | word | translation | level? |
    """
    fmt = _detect_format(filepath)
    if fmt == "csv":
        return _parse_csv(filepath)

    entries = []
    current_level = ""

    with open(filepath, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Detect section headers like "BEGINNER (A1-A2)" or "INTERMEDIATE (B1-B2)"
            upper = line.upper()
            if upper.startswith("BEGINNER") or upper.startswith("A1"):
                current_level = "beginner"
                continue
            elif upper.startswith("INTERMEDIATE") or upper.startswith("B1"):
                current_level = "intermediate"
                continue
            elif upper.startswith("ADVANCED") or upper.startswith("C1"):
                current_level = "advanced"
                continue

            # Skip header/separator rows
            if line.startswith("| ---") or "| Word" in line or "| Level" in line:
                continue

            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p]
            if len(parts) < 2:
                continue

            # Try to detect if first column is a timestamp
            ts = parse_timestamp(parts[0])

            if ts is not None and len(parts) >= 3:
                # Format: timestamp | word | translation | level?
                entries.append(
                    {
                        "timestamp": ts,
                        "word": parts[1].strip(),
                        "translation": parts[2].strip(),
                        "level": parts[3].strip() if len(parts) > 3 else current_level,
                        "usage_raw": parts[4].strip() if len(parts) > 4 else "",
                        "grammar_note": parts[5].strip() if len(parts) > 5 else "",
                    }
                )
            elif len(parts) >= 3 and current_level:
                # Section-header format: word | definition | translation
                entries.append(
                    {
                        "timestamp": None,
                        "word": parts[0].strip(),
                        "definition": parts[1].strip(),
                        "translation": parts[2].strip(),
                        "level": current_level,
                        "usage_raw": "",
                        "grammar_note": "",
                    }
                )
            elif len(parts) >= 2:
                # Flat format: word | translation | level?
                entries.append(
                    {
                        "timestamp": None,
                        "word": parts[0].strip(),
                        "translation": parts[1].strip(),
                        "level": parts[2].strip() if len(parts) > 2 else current_level,
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


def _simple_stem(word):
    """Strip common English suffixes for fuzzy matching.

    'breaking' -> 'break', 'walked' -> 'walk', 'gives' -> 'give'.
    Only strips if result is >= 3 chars.
    """
    w = word.lower()
    # Try longest suffixes first; 'es' only after consonant+es (boxes, watches)
    for suffix in ("ing", "ed", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    return w


def _strip_parens(word):
    """Strip parenthetical hints: 'fluff (a pillow)' -> 'fluff', 'dump (someone)' -> 'dump'."""
    m = re.match(r"^(.+?)\s*\(.*\)\s*$", word)
    return m.group(1).strip() if m else word


_IRREGULAR_FORMS = {
    "get": ("got", "gotten", "getting"),
    "go": ("went", "gone", "going", "goes"),
    "come": ("came", "coming", "comes"),
    "take": ("took", "taken", "taking", "takes"),
    "make": ("made", "making", "makes"),
    "give": ("gave", "given", "giving", "gives"),
    "find": ("found", "finding", "finds"),
    "know": ("knew", "known", "knowing", "knows"),
    "think": ("thought", "thinking", "thinks"),
    "see": ("saw", "seen", "seeing", "sees"),
    "tell": ("told", "telling", "tells"),
    "put": ("putting", "puts"),
    "run": ("ran", "running", "runs"),
    "break": ("broke", "broken", "breaking", "breaks"),
    "bring": ("brought", "bringing", "brings"),
    "turn": ("turned", "turning", "turns"),
    "keep": ("kept", "keeping", "keeps"),
    "let": ("letting", "lets"),
    "hold": ("held", "holding", "holds"),
    "leave": ("left", "leaving", "leaves"),
    "fall": ("fell", "fallen", "falling", "falls"),
    "hang": ("hung", "hanging", "hangs"),
    "set": ("setting", "sets"),
    "pick": ("picked", "picking", "picks"),
    "blow": ("blew", "blown", "blowing", "blows"),
}


def _whole_word_match(text, word):
    """Check if word appears as a whole word (or stem) in text.

    'tan' should NOT match 'understand', but 'pro' SHOULD match 'pros',
    'dread' SHOULD match 'dreading'.
    Also checks irregular verb forms (get → got/gotten).
    """
    # Match word at word boundary, allowing common suffixes (s, ed, ing, ly, er, est)
    pattern = r"\b" + re.escape(word) + r"(?:s|es|ed|ing|ly|er|est|d)?\b"
    if re.search(pattern, text, re.IGNORECASE):
        return True

    # Handle e-dropping: amaze→amazing, serve→serving, cleanse→cleansing
    if word.lower().endswith("e"):
        base = word[:-1]
        pattern_e = r"\b" + re.escape(base) + r"(?:ing|ed|er|est)\b"
        if re.search(pattern_e, text, re.IGNORECASE):
            return True

    # Check irregular forms
    forms = _IRREGULAR_FORMS.get(word.lower())
    if forms:
        for form in forms:
            pat = r"\b" + re.escape(form) + r"\b"
            if re.search(pat, text, re.IGNORECASE):
                return True

    return False


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

    # Second try: single word — full scan with whole-word match (handles e-dropping, irregulars)
    parts = word.lower().split()
    if len(parts) < 2:
        if not candidates:
            # Pre-filter missed it (e.g. "amaze" not substring of "amazing")
            # Full scan with _whole_word_match which handles e-dropping and irregulars
            all_lines = Transcript.objects.filter(episode=episode).order_by("start_time")
            for tr in all_lines:
                if _whole_word_match(tr.text, word):
                    return tr
        return None

    # Get the significant words (skip stop words, keep stems)
    key_words = []
    for p in parts:
        clean = p.strip("'\".,!?")
        if clean and clean not in STOP_WORDS:
            stem = _simple_stem(clean) if len(clean) > 3 else clean
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

        # Load frequency map from word_list.csv if it exists
        freq_file = Path("word_list.csv")
        if freq_file.exists():
            self._freq_map = {}
            with open(freq_file) as ff:
                for line in ff:
                    parts = line.strip().split(",")
                    if len(parts) == 2:
                        self._freq_map[parts[0].lower()] = int(parts[1])

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

            # Add definition if present
            definition = entry.get("definition", "")
            if definition:
                defaults["definition"] = definition

            # Add frequency from word_list.csv if available
            if hasattr(self, "_freq_map"):
                if " " in word:
                    # For phrases, use the rarest (most meaningful) word's frequency
                    word_freqs = [self._freq_map.get(w, 0) for w in word.split() if len(w) > 2]
                    freq = min(word_freqs) if word_freqs else 0
                else:
                    freq = self._freq_map.get(word, 0)
                defaults["frequency"] = freq

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
