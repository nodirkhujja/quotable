"""
Scan transcripts for grammar patterns and curate the best examples per unit.

Outputs a JSON file mapping unit_id → list of transcript clips that demonstrate
that unit's grammar pattern. Used by the grammar lesson page to show
"Grammar in the Wild — real Friends dialogue."

Usage:
    poetry run python -m core.manage scan_grammar_clips
    poetry run python -m core.manage scan_grammar_clips --limit 5
"""

import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand

from clips.models import Transcript

# Grammar patterns per unit — (regex, label, exclude_regex_or_None)
# exclude_regex prevents cross-contamination between tenses
UNIT_PATTERNS = {
    1: {
        "name": "Present Simple",
        "patterns": [
            (r"\bdon'?t\s+\w+", "don't + verb", r"\bdidn'?t\b"),
            (r"\bdoesn'?t\s+\w+", "doesn't + verb", None),
            (r"\b[Dd]o\s+(you|we|they)\s+\w+", "Do + subject + verb", None),
            (r"\b[Dd]oes\s+(he|she|it)\s+\w+", "Does + subject + verb", None),
            (
                r"\b(I|you|we|they)\s+(always|usually|never|often)\s+\w+",
                "frequency + verb",
                r"\b(have|has|'ve|'s)\b.*\b(always|never|ever)\b",
            ),
        ],
    },
    2: {
        "name": "Present Continuous",
        "patterns": [
            (r"\b(I'm|you're|we're|they're)\s+\w+ing\b", "subject + be + -ing", r"\bgoing\s+to\b"),
            (r"\b(am|are)\s+(not\s+)?\w+ing\b", "be + verb-ing", r"\bgoing\s+to\b"),
            (r"\b(he's|she's)\s+\w+ing\b", "he/she is + -ing", r"\bgoing\s+to\b"),
        ],
    },
    3: {
        "name": "Past Simple",
        "patterns": [
            (r"\bdidn'?t\s+\w+", "didn't + base verb", None),
            (r"\b[Dd]id\s+(you|he|she|I|we|they)\s+\w+", "Did + subject + verb", None),
            (
                r"\b(I|you|he|she|we|they)\s+(went|saw|told|came|knew|thought|found|left|took|forgot|understood|wrote)\b",
                "subject + irregular past",
                r"\b(have|has|'ve|'s)\s+",
            ),
        ],
    },
    4: {
        "name": "Future",
        "patterns": [
            (r"\b(I'll|you'll|he'll|she'll|we'll|they'll)\s+\w+", "will + verb", None),
            (r"\bwon'?t\s+\w+", "won't + verb", None),
            (r"\bgoing\s+to\s+\w+", "going to + verb", None),
        ],
    },
    5: {
        "name": "Articles",
        "patterns": [
            # Only match sentences where articles are the teaching point (contrast a/the)
            (r"\ba\s+\w+.+\bthe\s+\w+", "a vs the contrast", None),
            (r"\bget\s+a\s+\w+", "get a + noun", None),
            (r"\bneed\s+a\s+\w+", "need a + noun", None),
            (r"\b[Ii]t'?s\s+a\s+\w+", "it's a + noun", None),
        ],
    },
    6: {
        "name": "Prepositions",
        "patterns": [
            (r"\bat\s+(the\s+\w+|home|work|school|night|\d)", "at + place/time", None),
            (r"\bon\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)", "on + day", None),
            (
                r"\bin\s+(the\s+(morning|evening|room|car|house|office|kitchen|bathroom|city|world))",
                "in + space",
                None,
            ),
            (r"\bstuck\s+at\b|\blate\s+.+\bat\b|\bwaiting\s+at\b", "at (specific place)", None),
        ],
    },
    7: {
        "name": "Modal Verbs",
        "patterns": [
            (r"\bcan'?t\s+(believe|do|stop|wait|help|sleep|stand|take|even)\b", "can't + verb", None),
            (r"\bshould(n'?t)?\s+\w+", "should + verb", None),
            (r"\bhave\s+to\s+\w+", "have to + verb", None),
            (r"\bcould(n'?t)?\s+\w+", "could + verb", None),
        ],
    },
    8: {
        "name": "Conditionals",
        "patterns": [
            (r"\b[Ii]f\s+(I|you|he|she|we|they|it)\s+\w+.+(will|'ll|would|'d|won'?t)\b", "if-clause + result", None),
            (r"\b[Ii]f\s+(I|you|he|she|we|they)\s+(don'?t|didn'?t|could|had|were)\b", "if + condition", None),
            (r"\b[Ww]hat\s+if\b", "what if", None),
        ],
    },
    9: {
        "name": "Present Perfect",
        "patterns": [
            (r"\b(I've|you've|we've|they've)\s+(never|always|already|just|been|ever)\b", "have + adverb/pp", None),
            (r"\b(I've|you've|we've|they've)\s+\w+ed\b", "have + past participle", None),
            (r"\bhaven'?t\s+\w+", "haven't + pp", None),
            (r"\bhasn'?t\s+\w+", "hasn't + pp", None),
            (r"\bhave\s+(you|we|they)\s+(ever|never|been|seen|done)\b", "have you ever...?", None),
        ],
    },
    10: {
        "name": "Relative Clauses",
        "patterns": [
            (r"\bthe\s+\w+\s+(who|which|that)\s+\w+", "noun + who/which/that + verb", None),
            (r"\b(person|man|woman|guy|girl|one|thing|place)\s+(who|which|that)\b", "noun + relative pronoun", None),
        ],
    },
    11: {
        "name": "Linking Words",
        "patterns": [
            (r"\b[Bb]ecause\s+(I|you|he|she|we|they|it)\b", "because + subject", None),
            (r"\b[Aa]lthough\b", "although", None),
            (r"\b[Ee]ven\s+though\b", "even though", None),
            (r",\s*so\s+(I|you|he|she|we|they|it)\b", "so + result", None),
        ],
    },
}


def score_line(text):
    """Score a transcript line for quality as a grammar example.
    Higher = better example. Considers completeness, clarity, length."""
    score = 0
    text = text.strip()

    # Penalize fragments
    if text.startswith("-") or text.startswith("..."):
        return -1
    # Reject lines with character labels (ROSS:, CHANDLER:, etc.)
    if re.match(r"^[A-Z]{2,}:", text):
        return -1

    # Reward complete sentences
    if text[0].isupper():
        score += 5
    if any(text.endswith(p) for p in [".", "!", "?"]):
        score += 5

    # Sweet spot length: 20-55 chars (clear, memorable)
    if 20 <= len(text) <= 55:
        score += 10
    elif 55 < len(text) <= 70:
        score += 5
    elif len(text) < 20:
        score -= 5

    # Bonus for emotionally engaging/funny/dramatic lines
    emotional_markers = [
        "!",
        "?",
        "love",
        "hate",
        "never",
        "always",
        "can't",
        "won't",
        "believe",
        "sorry",
        "please",
        "want",
    ]
    for m in emotional_markers:
        if m.lower() in text.lower():
            score += 2

    return score


class Command(BaseCommand):
    help = "Scan transcripts for grammar patterns and output curated clips JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Max clips per unit (default: 5)",
        )
        parser.add_argument(
            "--output",
            type=str,
            default="learning/templates/learning/grammar_clips.json",
            help="Output JSON file path",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        output_path = options["output"]

        transcripts = list(Transcript.objects.select_related("source", "episode").all())
        self.stdout.write(f"Scanning {len(transcripts)} transcript lines...")

        result = {}

        for unit_id, unit_info in UNIT_PATTERNS.items():
            matches = []

            for t in transcripts:
                text = t.text.strip()
                line_score = score_line(text)
                if line_score < 0:
                    continue

                for pattern_tuple in unit_info["patterns"]:
                    regex, label, exclude = pattern_tuple
                    # Skip if exclusion pattern matches
                    if exclude and re.search(exclude, text, re.IGNORECASE):
                        continue
                    m = re.search(regex, text, re.IGNORECASE)
                    if m:
                        # Find the matched grammar span for highlighting
                        highlight = m.group(0)
                        matches.append(
                            {
                                "transcript_id": t.id,
                                "text": text,
                                "highlight": highlight,
                                "grammar_label": label,
                                "source_id": t.source_id,
                                "source_title": t.source.title if t.source else "",
                                "episode": (f"S{t.episode.season}E{t.episode.episode_number}" if t.episode else ""),
                                "start_time": float(t.start_time),
                                "end_time": float(t.end_time),
                                "score": line_score,
                            }
                        )
                        break  # Only match first pattern per line

            # Sort by score descending, deduplicate by text
            matches.sort(key=lambda x: x["score"], reverse=True)
            seen_texts = set()
            unique = []
            for m in matches:
                normalized = m["text"].lower().strip()
                if normalized not in seen_texts:
                    seen_texts.add(normalized)
                    unique.append(m)

            # Take top N
            top = unique[:limit]
            result[str(unit_id)] = top

            self.stdout.write(f"  Unit {unit_id} ({unit_info['name']}): " f"{len(unique)} unique → top {len(top)}")
            for clip in top:
                self.stdout.write(f"    [{clip['episode']}] \"{clip['text']}\" " f"(score={clip['score']})")

        # Write JSON
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

        self.stdout.write(
            self.style.SUCCESS(f"\nWrote {output_path} — {sum(len(v) for v in result.values())} total clips")
        )
