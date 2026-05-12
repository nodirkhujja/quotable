"""Auto-detect grammar patterns in transcript lines and compute complexity scores."""

import re

# ─────────────────────────────────────────────────────
# PATTERN REGEX DEFINITIONS
# Each pattern has one or more regex that detect its presence.
# Patterns are ordered by weight (simple → complex).
# ─────────────────────────────────────────────────────

PATTERN_REGEXES = {
    "because_so_but": [
        re.compile(r"\bbecause\b", re.I),
        re.compile(r"\bso\b(?!\s+(?:much|many|far|long|that))", re.I),  # "so" as connector, not "so much"
        re.compile(r"\bbut\b", re.I),
        re.compile(r"\bsince\b", re.I),  # causal "since"
    ],
    "which_that": [
        re.compile(r"\bwhich\b", re.I),
        re.compile(
            r"\bthat\b(?=\s+(?:I|you|he|she|it|we|they|is|was|were|are|has|had|will|would|could|should|might|can|do|did|does))",
            re.I,
        ),
    ],
    "who_whose_whom": [
        re.compile(
            r"\bwho\b(?!\s+(?:is|are|was|were|do|does|did|has|have|had|will|would|could|can|should|might)\b)", re.I
        ),
        # The above avoids "who is that" (question) — we want "the guy who works..."
        re.compile(r"\bwhose\b", re.I),
        re.compile(r"\bwhom\b", re.I),
        re.compile(r"\bwho\s+(?:I|you|he|she|we|they)\b", re.I),  # "who I told"
    ],
    "where_when": [
        re.compile(r"\bwhere\b(?!\s*\?)", re.I),  # "where" not as question
        re.compile(r"\bwhen\b(?!\s*\?)", re.I),  # "when" not as question
    ],
    "although_even_though": [
        re.compile(r"\balthough\b", re.I),
        re.compile(r"\beven though\b", re.I),
        re.compile(r"\beven if\b", re.I),
        re.compile(r"\bdespite\b", re.I),
        re.compile(r"\bin spite of\b", re.I),
    ],
    "if_unless": [
        re.compile(r"\bif\b(?!\s+(?:I|you)\s+(?:were|was)\s+you)", re.I),  # conditional "if"
        re.compile(r"\bunless\b", re.I),
        re.compile(r"\bas long as\b", re.I),
        re.compile(r"\bprovided\b", re.I),
    ],
    "what_clause": [
        re.compile(r"\bwhat\b(?=\s+(?:I|you|he|she|it|we|they)\b)", re.I),  # "what I said"
        re.compile(r"\bwhat\b(?=\s+(?:is|was|are|were)\s+\w+\s+is)", re.I),  # "what is important is"
    ],
    "however_therefore": [
        re.compile(r"\bhowever\b", re.I),
        re.compile(r"\btherefore\b", re.I),
        re.compile(r"\bmeanwhile\b", re.I),
        re.compile(r"\bnevertheless\b", re.I),
        re.compile(r"\bfurthermore\b", re.I),
        re.compile(r"\bmoreover\b", re.I),
        re.compile(r"\bnonetheless\b", re.I),
    ],
}

# Pattern weights (from GRAMMAR_PATTERNS)
PATTERN_WEIGHTS = {
    "because_so_but": 1.0,
    "which_that": 1.5,
    "who_whose_whom": 2.0,
    "where_when": 2.0,
    "although_even_though": 2.5,
    "if_unless": 2.5,
    "what_clause": 3.0,
    "however_therefore": 3.0,
    "multi_clause": 4.0,
}

# Clause multipliers
CLAUSE_MULTIPLIERS = {1: 1.0, 2: 1.3, 3: 1.6}
DEFAULT_CLAUSE_MULT = 2.0  # 4+ clauses

# Difficulty brackets
DIFFICULTY_BRACKETS = [
    (1.5, 1),  # 0-1.5 → Level 1
    (3.0, 2),  # 1.5-3.0 → Level 3
    (5.0, 4),  # 3.0-5.0 → Level 5
    (7.0, 6),  # 5.0-7.0 → Level 6
    (999, 8),  # 7.0+ → Level 8
]

# Minimum sentence length to consider (skip very short lines)
MIN_SENTENCE_LENGTH = 30  # characters


def detect_patterns(text):
    """Detect all grammar patterns in a sentence.
    Returns: list of pattern IDs found, e.g. ["because_so_but", "which_that"]
    """
    found = []
    for pattern_id, regexes in PATTERN_REGEXES.items():
        for regex in regexes:
            if regex.search(text):
                found.append(pattern_id)
                break  # one match per pattern is enough
    return found


def count_clauses(text):
    """Estimate number of clauses in a sentence.
    Heuristic: count verb groups (conjugated verbs) as proxy for clauses.
    """
    # Count clause separators: commas, semicolons, conjunctions
    separators = len(re.findall(r"[,;]|\b(?:and|but|or|because|although|while|when|if|that|which|who)\b", text, re.I))
    # At least 1 clause, estimated as separators + 1, capped at 5
    return min(max(1, separators), 5)


def compute_complexity(patterns, clause_count):
    """Compute complexity score from detected patterns and clause count."""
    if not patterns:
        return 0.0

    weight_sum = sum(PATTERN_WEIGHTS.get(p, 1.0) for p in patterns)
    multiplier = CLAUSE_MULTIPLIERS.get(clause_count, DEFAULT_CLAUSE_MULT)

    # Multi-clause bonus if 3+ patterns detected
    if len(patterns) >= 3:
        patterns_with_multi = patterns + ["multi_clause"]
        weight_sum = sum(PATTERN_WEIGHTS.get(p, 1.0) for p in patterns_with_multi)

    return round(weight_sum * multiplier, 2)


def get_difficulty_level(complexity_score):
    """Map complexity score to difficulty level 1-8."""
    for threshold, level in DIFFICULTY_BRACKETS:
        if complexity_score <= threshold:
            return level
    return 8


def get_primary_pattern(patterns):
    """Return the highest-weight pattern (primary challenge of the sentence)."""
    if not patterns:
        return ""
    return max(patterns, key=lambda p: PATTERN_WEIGHTS.get(p, 0))


def analyze_sentence(text):
    """Full analysis of a sentence. Returns dict with all computed fields."""
    patterns = detect_patterns(text)
    if not patterns:
        return None  # Skip sentences with no grammar patterns

    clause_count = count_clauses(text)
    complexity = compute_complexity(patterns, clause_count)
    level = get_difficulty_level(complexity)
    primary = get_primary_pattern(patterns)

    return {
        "patterns": patterns,
        "clause_count": clause_count,
        "complexity_score": complexity,
        "difficulty_level": level,
        "primary_pattern": primary,
    }


def scan_transcripts(source, episode=None):
    """Scan all transcripts for a source/episode and return analyzed sentences.
    Returns list of dicts: [{transcript, text, translation, analysis}, ...]
    """
    from clips.models import Transcript
    from vocab.models import LineTranslation

    qs = Transcript.objects.filter(source=source)
    if episode:
        qs = qs.filter(episode=episode)

    # Pre-load translations
    translations = {}
    for lt in LineTranslation.objects.filter(source=source):
        translations[lt.transcript_id] = lt.translation

    results = []
    for t in qs:
        if len(t.text) < MIN_SENTENCE_LENGTH:
            continue

        analysis = analyze_sentence(t.text)
        if not analysis:
            continue

        results.append(
            {
                "transcript": t,
                "text": t.text,
                "translation": translations.get(t.id, ""),
                **analysis,
            }
        )

    return results
