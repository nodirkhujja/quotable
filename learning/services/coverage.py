"""Vocabulary coverage calculation for the "Ready to Watch" feature.

Computes per-user, per-transcript-line coverage:
    coverage = known_tokens / total_tokens

A token is "known" if it appears in:
  • the user's WordNote library (any stage — saving = familiarity), OR
  • the COMMON_WORDS set below (basic English nobody saves but everyone knows).

The COMMON_WORDS set is critical: nobody saves "the" or "is" but
everyone understands them. Without it, coverage is artificially low
and the feature breaks (most scenes hover around 30-50% even when
the user actually understands them).

Pedagogical foundation:
  • Nation (2006), Hu & Nation (2000): 95% lexical coverage = unassisted
    reading comprehension. For TV (visual context, repetition, simple
    syntax), the threshold drops to ~85-90%.
  • Krashen i+1: input slightly above current ability produces fastest
    acquisition. At 88-95%, the unknown 1-2 words are LEARNABLE from
    context — exactly the i+1 sweet spot.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model

from clips.models import Transcript
from learning.models import SceneCoverage
from vocab.models import WordNote

User = get_user_model()

# Basic English. Function words + auxiliaries + ultra-high-frequency
# verbs/adverbs/conversation tokens nobody saves but everyone knows.
# Source: West's General Service List + Friends-vocab adaptations
# (yeah/oh/hey are common in the show even if textbooks ignore them).
COMMON_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "doing",
    "done",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "shall",
    "can",
    "must",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "its",
    "our",
    "their",
    "mine",
    "yours",
    "hers",
    "ours",
    "theirs",
    "this",
    "that",
    "these",
    "those",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "where",
    "when",
    "why",
    "how",
    "all",
    "each",
    "every",
    "both",
    "few",
    "more",
    "most",
    "other",
    "another",
    "some",
    "such",
    "no",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "just",
    "because",
    "but",
    "and",
    "or",
    "if",
    "then",
    "else",
    "about",
    "up",
    "out",
    "on",
    "off",
    "over",
    "under",
    "again",
    "further",
    "once",
    "here",
    "there",
    "any",
    "of",
    "to",
    "in",
    "for",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "until",
    "while",
    "without",
    "within",
    "know",
    "knew",
    "known",
    "get",
    "got",
    "gotten",
    "go",
    "goes",
    "went",
    "gone",
    "come",
    "came",
    "make",
    "made",
    "like",
    "liked",
    "think",
    "thought",
    "say",
    "said",
    "see",
    "saw",
    "seen",
    "look",
    "looked",
    "want",
    "wanted",
    "give",
    "gave",
    "given",
    "take",
    "took",
    "taken",
    "tell",
    "told",
    "let",
    "put",
    "try",
    "tried",
    "ask",
    "asked",
    "need",
    "needed",
    "feel",
    "felt",
    "become",
    "became",
    "leave",
    "left",
    "call",
    "called",
    "keep",
    "kept",
    "begin",
    "began",
    "show",
    "showed",
    "shown",
    "hear",
    "heard",
    "play",
    "played",
    "run",
    "ran",
    "move",
    "moved",
    "live",
    "lived",
    "believe",
    "believed",
    "bring",
    "brought",
    "happen",
    "happened",
    "find",
    "found",
    "use",
    "used",
    "work",
    "worked",
    "talk",
    "talked",
    "talking",
    "speak",
    "spoke",
    "spoken",
    # Conversational fillers — essential for TV dialogue
    "yeah",
    "yes",
    "no",
    "ok",
    "okay",
    "oh",
    "well",
    "right",
    "wrong",
    "hey",
    "hi",
    "hello",
    "bye",
    "please",
    "thanks",
    "thank",
    "sorry",
    "really",
    "actually",
    "maybe",
    "sure",
    "now",
    "still",
    "also",
    "never",
    "always",
    "much",
    "good",
    "bad",
    "new",
    "first",
    "last",
    "long",
    "short",
    "great",
    "little",
    "big",
    "old",
    "young",
    "way",
    "thing",
    "things",
    "time",
    "day",
    "night",
    "year",
    "people",
    "person",
    # Common verbs/contractions that surface in transcripts
    "im",
    "youre",
    "hes",
    "shes",
    "its",
    "were",
    "theyre",
    "weve",
    "youve",
    "ive",
    "id",
    "youd",
    "hed",
    "shed",
    "wed",
    "theyd",
    "isnt",
    "arent",
    "wasnt",
    "werent",
    "doesnt",
    "dont",
    "didnt",
    "wont",
    "wouldnt",
    "couldnt",
    "shouldnt",
    "cant",
    "shant",
    "havent",
    "hasnt",
    "hadnt",
    "ill",
    "youll",
    "hell",
    "shell",
    "well",
    "theyll",
    "lets",
    "thats",
    "whats",
    "wheres",
    "whens",
    "hows",
    "whos",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on word characters (incl. apostrophes).

    Strips trailing apostrophes from possessives so "Joey's" → "joeys".
    Then strips ALL apostrophes so "don't" → "dont" matches COMMON_WORDS.
    """
    raw = re.findall(r"[a-zA-Z']+", text.lower())
    out = []
    for w in raw:
        # Strip leading/trailing non-letters, then internal apostrophes.
        w = w.strip("'")
        w = w.replace("'", "")
        if w:
            out.append(w)
    return out


def _user_known_words(user) -> set[str]:
    """All words the user has saved, lowercased + tokenized.

    Multi-word phrases like "have a great time" expand to their
    individual tokens — saving "have a great time" implies the user
    can handle each of those tokens in any other context too. (This
    is conservative: it overestimates known a bit, but for the
    "feel-good" surface that's the right side to err on.)
    """
    known: set[str] = set()
    for note_word in WordNote.objects.filter(user=user).values_list("word", flat=True):
        for tok in _tokenize(note_word or ""):
            known.add(tok)
    return known


def calculate_transcript_coverage(user, transcript: Transcript) -> float:
    """Compute + persist coverage for one user × one transcript line.

    Returns coverage as float in [0.0, 1.0]. Side-effect: upserts a
    SceneCoverage row.
    """
    text = (transcript.text or "").strip()
    if not text:
        return 0.0
    tokens = _tokenize(text)
    total = len(tokens)
    if total == 0:
        return 0.0

    known_set = _user_known_words(user) | COMMON_WORDS
    known_count = sum(1 for tok in tokens if tok in known_set)
    coverage = known_count / total

    SceneCoverage.objects.update_or_create(
        user=user,
        transcript=transcript,
        defaults={
            "total_tokens": total,
            "known_tokens": known_count,
            "coverage": coverage,
        },
    )
    return coverage


def recalculate_for_user(user, *, source_filter: str | None = "Friends") -> dict:
    """Batch-compute coverage for every transcript in the DB for one user.

    Cheap: pure DB + simple set lookups. ~50ms for 1000 transcripts.
    Optional source_filter narrows to a specific source title (default
    "Friends" since that's the test corpus).

    Returns summary stats so the management command can report.
    """
    qs = Transcript.objects.exclude(text__isnull=True).exclude(text="")
    if source_filter:
        qs = qs.filter(episode__source__title__icontains=source_filter)
    qs = qs.select_related("episode__source")

    known_set = _user_known_words(user) | COMMON_WORDS

    written = 0
    coverage_buckets = {"≥95": 0, "≥88": 0, "≥80": 0, "<80": 0}
    for t in qs.iterator(chunk_size=500):
        text = (t.text or "").strip()
        if not text:
            continue
        tokens = _tokenize(text)
        total = len(tokens)
        if total == 0:
            continue
        known_count = sum(1 for tok in tokens if tok in known_set)
        coverage = known_count / total
        SceneCoverage.objects.update_or_create(
            user=user,
            transcript=t,
            defaults={
                "total_tokens": total,
                "known_tokens": known_count,
                "coverage": coverage,
            },
        )
        written += 1
        if coverage >= 0.95:
            coverage_buckets["≥95"] += 1
        elif coverage >= 0.88:
            coverage_buckets["≥88"] += 1
        elif coverage >= 0.80:
            coverage_buckets["≥80"] += 1
        else:
            coverage_buckets["<80"] += 1
    return {
        "user": user.username,
        "transcripts_processed": written,
        "buckets": coverage_buckets,
    }
