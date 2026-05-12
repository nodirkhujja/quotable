"""Placement test content + scoring.

Two-page student level test:
  Page 1 — Vocab tap-grid (43 items: 11 easy + 11 medium + 11 expert + 10 phrases)
  Page 2 — Grammar MCQ (4 items spanning A2 -> C1)

Total max score: 111. Thresholds derived from expected-score midpoints
under a research-calibrated knowing-probability model.
"""

# ── Vocab items ───────────────────────────────────────────────────────────

EASY_WORDS = [
    "hungry",
    "kitchen",
    "borrow",
    "answer",
    "forget",
    "explain",
    "happen",
    "believe",
    "enough",
    "remember",
    "although",
]

MEDIUM_WORDS = [
    "awkward",
    "realize",
    "pretend",
    "overwhelmed",
    "commitment",
    "reluctant",
    "reveal",
    "struggle",
    "approach",
    "nevertheless",
    "assume",
]

EXPERT_WORDS = [
    "peculiar",
    "eloquent",
    "bittersweet",
    "ambivalent",
    "euphemism",
    "ominous",
    "resilient",
    "vicarious",
    "scrutinize",
    "quintessential",
    "inadvertently",
]

# Phrases carry their own CEFR weight (mixed B1/B2/C1)
PHRASES = [
    {"id": "p1", "text": "hang out", "cefr": "B1", "weight": 1},
    {"id": "p2", "text": "break up", "cefr": "B1", "weight": 1},
    {"id": "p3", "text": "figure out", "cefr": "B1", "weight": 1},
    {"id": "p4", "text": "piece of cake", "cefr": "B1", "weight": 1},
    {"id": "p5", "text": "freak out", "cefr": "B1", "weight": 1},
    {"id": "p6", "text": "run into (someone)", "cefr": "B2", "weight": 2},
    {"id": "p7", "text": "get the hang of", "cefr": "B2", "weight": 2},
    {"id": "p8", "text": "pull someone's leg", "cefr": "B2", "weight": 2},
    {"id": "p9", "text": "spill the beans", "cefr": "C1", "weight": 3},
    {"id": "p10", "text": "under the weather", "cefr": "C1", "weight": 3},
]

# ── Grammar items (MCQ, 3 options each) ───────────────────────────────────

GRAMMAR_ITEMS = [
    {
        "id": "g1",
        "cefr": "A2",
        "weight": 4,
        "prompt": "Yesterday I ___ to the supermarket.",
        "options": ["go", "went", "going"],
        "correct": 1,
    },
    {
        "id": "g2",
        "cefr": "B1",
        "weight": 6,
        "prompt": "I ___ never ___ to Paris.",
        "options": ["am / been", "have / been", "did / went"],
        "correct": 1,
    },
    {
        "id": "g3",
        "cefr": "B2",
        "weight": 8,
        "prompt": "If I ___ more time, I ___ learn Spanish.",
        "options": ["have / will", "had / would", "had / will"],
        "correct": 1,
    },
    {
        "id": "g4",
        "cefr": "C1",
        "weight": 10,
        "prompt": "If I hadn't met her, my life ___ so different now.",
        "options": ["would act", "wouldn't be", "wouldn't have been"],
        "correct": 1,
    },
]

# ── Totals / maxima ───────────────────────────────────────────────────────

VOCAB_EASY_WEIGHT = 1
VOCAB_MEDIUM_WEIGHT = 2
VOCAB_EXPERT_WEIGHT = 3

VOCAB_MAX = (
    len(EASY_WORDS) * VOCAB_EASY_WEIGHT
    + len(MEDIUM_WORDS) * VOCAB_MEDIUM_WEIGHT
    + len(EXPERT_WORDS) * VOCAB_EXPERT_WEIGHT
    + sum(p["weight"] for p in PHRASES)
)  # = 83

GRAMMAR_MAX = sum(g["weight"] for g in GRAMMAR_ITEMS)  # = 28
TOTAL_MAX = VOCAB_MAX + GRAMMAR_MAX  # = 111

# ── Placement buckets ─────────────────────────────────────────────────────

LEVEL_ORDER = ["beginner", "intermediate", "upper_intermediate", "advanced"]

LEVEL_LABELS = {
    "beginner": "Beginner",
    "intermediate": "Intermediate",
    "upper_intermediate": "Upper-Intermediate",
    "advanced": "Advanced",
}

LEVEL_CEFR = {
    "beginner": "A1",
    "intermediate": "A2–B1",
    "upper_intermediate": "B2",
    "advanced": "C1+",
}


def _base_level(total: int) -> str:
    if total >= 86:
        return "advanced"
    if total >= 61:
        return "upper_intermediate"
    if total >= 20:
        return "intermediate"
    return "beginner"


def _cap(current: str, ceiling: str) -> str:
    """Return min(current, ceiling) by LEVEL_ORDER."""
    return current if LEVEL_ORDER.index(current) <= LEVEL_ORDER.index(ceiling) else ceiling


# ── Scoring ───────────────────────────────────────────────────────────────


def compute_placement(
    checked_easy: list[str],
    checked_medium: list[str],
    checked_expert: list[str],
    checked_phrase_ids: list[str],
    grammar_answers: dict[str, int],
) -> dict:
    """Score a placement submission.

    Inputs:
      checked_easy / medium / expert — words the user tapped as "known"
      checked_phrase_ids — PHRASES[*]["id"] values tapped as "known"
      grammar_answers — {item_id: chosen_option_idx}

    Returns a result dict suitable for persisting + rendering.
    """
    # Valid sets (reject anything the client might forge)
    easy_set = set(EASY_WORDS)
    medium_set = set(MEDIUM_WORDS)
    expert_set = set(EXPERT_WORDS)
    phrase_ids = {p["id"] for p in PHRASES}
    phrase_by_id = {p["id"]: p for p in PHRASES}

    easy = [w for w in checked_easy if w in easy_set]
    medium = [w for w in checked_medium if w in medium_set]
    expert = [w for w in checked_expert if w in expert_set]
    phrases = [pid for pid in checked_phrase_ids if pid in phrase_ids]

    vocab_raw = (
        len(easy) * VOCAB_EASY_WEIGHT
        + len(medium) * VOCAB_MEDIUM_WEIGHT
        + len(expert) * VOCAB_EXPERT_WEIGHT
        + sum(phrase_by_id[pid]["weight"] for pid in phrases)
    )

    grammar_correct_ids = [g["id"] for g in GRAMMAR_ITEMS if grammar_answers.get(g["id"]) == g["correct"]]
    grammar_raw = sum(g["weight"] for g in GRAMMAR_ITEMS if g["id"] in grammar_correct_ids)

    total = vocab_raw + grammar_raw
    level = _base_level(total)

    # ── Guard rails ─────────────────────────────────────────────────
    # 1. No grammar at all → recognition-only; cap at Intermediate
    if grammar_raw == 0:
        level = _cap(level, "intermediate")

    # 2. Over-claim: tapped >=95% of vocab but barely any grammar
    vocab_items_total = len(EASY_WORDS) + len(MEDIUM_WORDS) + len(EXPERT_WORDS) + len(PHRASES)
    vocab_items_checked = len(easy) + len(medium) + len(expert) + len(phrases)
    if vocab_items_total > 0 and vocab_items_checked / vocab_items_total >= 0.95 and grammar_raw <= 6:
        level = _cap(level, "intermediate")

    # 3. Advanced score but missed the C1 grammar item → cap at Upper-Int
    if level == "advanced" and "g4" not in grammar_correct_ids:
        level = "upper_intermediate"

    # 4. Failed to tap basic words (< 55% of Easy) → floor at Beginner
    if len(EASY_WORDS) > 0 and len(easy) / len(EASY_WORDS) < 0.55:
        level = "beginner"

    return {
        "total": total,
        "vocab_raw": vocab_raw,
        "grammar_raw": grammar_raw,
        "level": level,
        "cefr": LEVEL_CEFR[level],
        "label": LEVEL_LABELS[level],
        "breakdown": {
            "easy": len(easy),
            "medium": len(medium),
            "expert": len(expert),
            "phrases": len(phrases),
            "grammar_correct": len(grammar_correct_ids),
            "grammar_correct_ids": grammar_correct_ids,
            "vocab_raw": vocab_raw,
            "grammar_raw": grammar_raw,
        },
    }


def test_content_for_template() -> dict:
    """Serializable test content for the front-end."""
    return {
        "easy": EASY_WORDS,
        "medium": MEDIUM_WORDS,
        "expert": EXPERT_WORDS,
        "phrases": [{"id": p["id"], "text": p["text"]} for p in PHRASES],
        "grammar": [
            {
                "id": g["id"],
                "prompt": g["prompt"],
                "options": g["options"],
            }
            for g in GRAMMAR_ITEMS
        ],
    }
