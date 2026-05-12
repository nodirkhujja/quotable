"""
Extract vocabulary from an episode transcript using word_list.csv for levels.

Outputs a draft CSV file ready for ChatGPT to fill in definitions + translations.
Also extracts phrasal verbs and idioms automatically.

Usage:
    poetry run python -m core.manage extract_vocab --source 1 --season 1 --episode 1
    make extract-vocab source=1 s=1 e=1
"""

import re
from pathlib import Path

from django.core.management.base import BaseCommand

from clips.models import Episode, Source, Transcript

# Ultra-basic words to always skip (grammar words, pronouns, etc.)
SKIP_WORDS = {
    # Pronouns, determiners
    "i",
    "you",
    "he",
    "she",
    "we",
    "they",
    "it",
    "me",
    "my",
    "your",
    "his",
    "her",
    "our",
    "their",
    "its",
    "us",
    "him",
    "them",
    "myself",
    "yourself",
    "himself",
    "herself",
    "themselves",
    "ourselves",
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    # Conjunctions, prepositions, articles
    "and",
    "but",
    "or",
    "if",
    "so",
    "than",
    "because",
    "since",
    "until",
    "while",
    "although",
    "unless",
    "whether",
    "in",
    "on",
    "at",
    "to",
    "for",
    "with",
    "about",
    "from",
    "of",
    "by",
    "up",
    "out",
    "off",
    "down",
    "over",
    "under",
    "after",
    "before",
    "between",
    "into",
    "through",
    "around",
    "without",
    "during",
    "against",
    # Question words, modals
    "what",
    "who",
    "how",
    "where",
    "when",
    "why",
    "which",
    "whose",
    "can",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "must",
    "shall",
    # Common verbs (too basic)
    "be",
    "is",
    "am",
    "are",
    "was",
    "were",
    "been",
    "being",
    "have",
    "has",
    "had",
    "having",
    "do",
    "does",
    "did",
    "done",
    "doing",
    "go",
    "goes",
    "went",
    "gone",
    "going",
    "get",
    "got",
    "gets",
    "getting",
    "say",
    "said",
    "says",
    "saying",
    "make",
    "made",
    "makes",
    "making",
    "know",
    "knew",
    "known",
    "knows",
    "think",
    "thought",
    "thinks",
    "come",
    "came",
    "comes",
    "coming",
    "see",
    "saw",
    "seen",
    "sees",
    "look",
    "looked",
    "looking",
    "looks",
    "want",
    "wanted",
    "wants",
    "give",
    "gave",
    "gives",
    "given",
    "take",
    "took",
    "taken",
    "takes",
    "tell",
    "told",
    "tells",
    "find",
    "found",
    "finds",
    "put",
    "puts",
    "putting",
    "try",
    "tried",
    "tries",
    "trying",
    "leave",
    "left",
    "leaves",
    "call",
    "called",
    "calls",
    "keep",
    "kept",
    "keeps",
    "let",
    "lets",
    "need",
    "needed",
    "needs",
    "feel",
    "felt",
    "feels",
    "mean",
    "meant",
    "means",
    "ask",
    "asked",
    "asks",
    "seem",
    "seemed",
    "seems",
    "help",
    "helped",
    "helps",
    "talk",
    "talked",
    "talks",
    "turn",
    "turned",
    "turns",
    "start",
    "started",
    "starts",
    "show",
    "showed",
    "shown",
    "hear",
    "heard",
    "hears",
    "move",
    "moved",
    "moves",
    "live",
    "lived",
    "lives",
    "believe",
    "believed",
    "happen",
    "happened",
    "happens",
    "work",
    "worked",
    "works",
    "hold",
    "held",
    "bring",
    "brought",
    "sit",
    "sat",
    "stand",
    "stood",
    "set",
    "catch",
    "caught",
    "sophisticate",  # sophisticated→sophisticate by stemmer, not useful as vocab
    # Common adjectives/adverbs (too basic)
    "good",
    "bad",
    "great",
    "big",
    "little",
    "small",
    "long",
    "short",
    "new",
    "old",
    "young",
    "right",
    "wrong",
    "nice",
    "happy",
    "ready",
    "best",
    "better",
    "worse",
    "worst",
    "hard",
    "easy",
    "fine",
    "free",
    "sure",
    "true",
    "real",
    "whole",
    "different",
    "same",
    "only",
    "just",
    "really",
    "very",
    "well",
    "too",
    "also",
    "still",
    "even",
    "never",
    "always",
    "maybe",
    "already",
    "again",
    "actually",
    "probably",
    "ever",
    "yet",
    "almost",
    "enough",
    "quite",
    "pretty",
    "here",
    "there",
    "now",
    "then",
    "today",
    "tonight",
    "tomorrow",
    "much",
    "more",
    "most",
    "many",
    "few",
    "less",
    "some",
    "any",
    "every",
    "each",
    "another",
    "other",
    "both",
    "all",
    "no",
    "not",
    "yes",
    "yeah",
    "ok",
    "okay",
    "alright",
    # Common nouns (too basic)
    "man",
    "woman",
    "people",
    "guy",
    "guys",
    "life",
    "time",
    "day",
    "night",
    "morning",
    "year",
    "thing",
    "things",
    "way",
    "place",
    "world",
    "home",
    "house",
    "room",
    "door",
    "water",
    "food",
    "money",
    "phone",
    "car",
    "name",
    "mother",
    "father",
    "brother",
    "sister",
    "friend",
    "baby",
    "boy",
    "girl",
    "hand",
    "head",
    "face",
    "eyes",
    "job",
    "school",
    "number",
    "lot",
    "kind",
    "part",
    "everybody",
    "everyone",
    "someone",
    "something",
    "nothing",
    "anything",
    "question",
    "answer",
    "problem",
    # Fillers
    "gonna",
    "gotta",
    "wanna",
    "kinda",
    "oh",
    "hey",
    "hi",
    "hello",
    "bye",
    "please",
    "thank",
    "thanks",
    "sorry",
    "wow",
    "god",
    "ah",
    "uh",
    "um",
    "er",
    "hmm",
    "mmm",
    "la",
    "ooh",
    # Common adjectives/adverbs (more)
    "low",
    "high",
    "close",
    "open",
    "full",
    "late",
    "early",
    "fast",
    "slow",
    "loud",
    "quiet",
    "deep",
    "wide",
    "flat",
    "hot",
    "cold",
    "warm",
    "cool",
    "dark",
    "light",
    "clear",
    "clean",
    "dirty",
    "dry",
    "wet",
    "soft",
    "strong",
    "weak",
    "rich",
    "poor",
    "thick",
    "thin",
    "heavy",
    "tight",
    "loose",
    "sharp",
    "bright",
    "rough",
    "smooth",
    "safe",
    "dead",
    "alive",
    # Possessive / basic pronouns
    "hers",
    "his",
    "theirs",
    "ours",
    "mine",
    "yours",
    # More common verbs
    "use",
    "used",
    "using",
    "uses",
    "run",
    "ran",
    "running",
    "runs",
    "play",
    "played",
    "playing",
    "read",
    "write",
    "wrote",
    "open",
    "opened",
    "close",
    "closed",
    "stop",
    "stopped",
    "stops",
    "send",
    "sent",
    "pay",
    "paid",
    "spend",
    "spent",
    "win",
    "won",
    "lose",
    "lost",
    "cut",
    "tie",
    "tied",
    "tying",
    "wait",
    "waited",
    "pick",
    "picked",
    "pass",
    "passed",
    "wear",
    "wore",
    "worn",
    "carry",
    "carried",
    "change",
    "changed",
    "watch",
    "watched",
    "follow",
    "followed",
    "lead",
    "led",
    "grow",
    "grew",
    "grown",
    "draw",
    "drew",
    "drawn",
    "break",
    "broke",
    "broken",
    # Ordinals
    "first",
    "last",
    "next",
    "second",
    "third",
    "fourth",
    "fifth",
    # Misc
    "else",
    "anyway",
    "back",
    "away",
    "ago",
    "own",
    "together",
    "half",
    "course",
    "mostly",
    "especially",
    "sometimes",
    "sometime",
    "don",
    "didn",
    "doesn",
    "won",
    "isn",
    "wasn",
    "weren",
    "hasn",
    "haven",
    "wouldn",
    "couldn",
    "shouldn",
    "re",
    "ve",
    "ll",
    "s",
    "t",
    "d",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
}

# Phrasal verb / idiom patterns to search for
PHRASE_PATTERNS = [
    # 2-word phrasal verbs
    (r"\bgo(?:ing|es)?\s+out\s+with\b", "go out with"),
    (r"\bgo(?:ing|es)?\s+through\b", "go through"),
    (r"\bget(?:ting|s)?\s+through\b", "get through"),
    (r"\bget(?:ting|s)?\s+out\s+of\b", "get out of"),
    (r"\bgot\s+screwed\b", "get screwed"),
    (r"\bmove(?:d|s)?\s+out\b", "move out"),
    (r"\bcom(?:e|ing)\s+over\b", "come over"),
    (r"\bturn(?:s|ed)?\s+out\b", "turn out"),
    (r"\bturn(?:s|ed)?\s+on\b", "turned on"),
    (r"\bdrift(?:ed|ing)?\s+apart\b", "drift apart"),
    (r"\bfreak(?:ed|ing)?\s+out\b", "freak out"),
    (r"\bhang(?:ing|s)?\s+out\b", "hang out"),
    (r"\bhit(?:ting|s)?\s+on\b", "hit on"),
    (r"\bask(?:ed|ing)?\s+(?:\w+\s+)?out\b", "ask out"),
    (r"\bput(?:ting|s)?\s+together\b", "put together"),
    (r"\bcatch(?:es|ing)?\s+on\b|caught\s+on\b", "catch on"),
    (r"\bcut(?:ting|s)?\s+(?:\w+\s+)?off\b", "cut off"),
    (r"\bpull(?:ed|ing)?\s+out\b", "pull out"),
    (r"\bbuzz(?:ed)?\s+(?:\w+\s+)?in\b", "buzz in"),
    (r"\bspell(?:ed|ing)?\s+(?:\w+\s+)?out\b", "spell out"),
    (r"\bend(?:ed|ing|s)?\s+up\b", "end up"),
    (r"\blive(?:d|s)?\s+off\b", "live off"),
    (r"\bwalk(?:ed|ing)?\s+out\s+on\b", "walked out on"),
    (r"\bpick(?:ed|ing|s)?\s+up\b", "pick up"),
    (r"\bgive(?:s)?\s+(?:\w+\s+)?(?:a\s+)?break\b|gave\s+(?:\w+\s+)?(?:a\s+)?break\b", "give a break"),
    (r"\bfigure(?:d|s)?\s+out\b", "figure out"),
    (r"\bfind(?:ing|s)?\s+out\b|found\s+out\b", "find out"),
    (r"\bwork(?:ed|ing|s)?\s+out\b", "work out"),
    (r"\bshow(?:ed|ing|s)?\s+up\b", "show up"),
    (r"\bbreak(?:ing|s)?\s+up\b|broke\s+up\b", "break up"),
    (r"\bmake(?:s)?\s+up\b|made\s+up\b", "make up"),
    (r"\brun(?:ning|s)?\s+into\b|ran\s+into\b", "run into"),
    (r"\bget(?:ting|s)?\s+rid\s+of\b|got\s+rid\s+of\b", "get rid of"),
    (r"\bcrash(?:ed|ing)?\s+on\s+(?:the\s+)?couch\b", "crash on the couch"),
    (r"\bcheck(?:ed|ing|s)?\s+out\b", "check out"),
    (r"\bfreak(?:ed|ing)?\s+out\b", "freak out"),
    (r"\bpass(?:ed|ing|es)?\s+out\b", "pass out"),
    (r"\bcome(?:s)?\s+on\b|came\s+on\b", "come on"),
    (r"\bcalm(?:ed|ing|s)?\s+down\b", "calm down"),
    (r"\bshut(?:ting|s)?\s+up\b", "shut up"),
    (r"\bscrew(?:ed|ing|s)?\s+up\b", "screw up"),
    (r"\bmess(?:ed|ing|es)?\s+up\b", "mess up"),
    (r"\bget(?:ting|s)?\s+over\b|got\s+over\b", "get over"),
    (r"\bget(?:ting|s)?\s+back\b|got\s+back\b", "get back"),
    (r"\bblow(?:ing|s|n)?\s+off\b|blew\s+off\b", "blow off"),
    (r"\bwait\s+a\s+minute\b", "wait a minute"),
    (r"\bfix(?:at(?:ed|ing))?\s+on\b", "fixate on"),
    # Multi-word expressions
    (r"\ball\s+of\s+a\s+sudden\b", "all of a sudden"),
    (r"\bto\s+hell\s+with\b", "to hell with"),
    (r"\bout\s+loud\b", "out loud"),
    (r"\bon\s+(?:a\s+)?sale\b", "on sale"),
    (r"\bon\s+your\s+own\b", "on your own"),
    (r"\bon\s+a\s+roll\b", "on a roll"),
    (r"\bgrab\s+a\s+spoon\b", "grab a spoon"),
    (r"\bit\s+sucks\b", "it sucks"),
    (r"\bmaking\s+love\b", "making love"),
    (r"\btake\s+credit\b", "take credit for"),
    (r"\btake\s+control\b", "take control"),
    (r"\breal\s+world\b", "real world"),
    (r"\bhigh\s+school\b", "high school"),
    (r"\bdear\s+diary\b", "dear diary"),
    (r"\bby\s+the\s+way\b", "by the way"),
    (r"\bno\s+big\s+deal\b", "no big deal"),
    (r"\ba\s+big\s+deal\b", "a big deal"),
    (r"\bkind\s+of\b", "kind of"),
    (r"\bno\s+way\b", "no way"),
    (r"\boh\s+my\s+god\b", "oh my god"),
    # Compound nouns worth learning
    (r"\bgravy\s+boat\b", "gravy boat"),
    (r"\bpipe\s+organ\b", "pipe organ"),
    (r"\bice\s+cream\b", "ice cream"),
    (r"\bwhipped\s+cream\b", "whipped cream"),
    (r"\bcredit\s+card\b", "credit card"),
    (r"\bsmall\s+intestine\b", "small intestine"),
    (r"\bstrip\s+joint\b", "strip joint"),
    (r"\bcookie\s+dough\b", "cookie dough"),
    (r"\brocky\s+road\b", "rocky road"),
    (r"\bport\s+authority\b", "port authority"),
    (r"\bsweet\s+and\s+low\b", "sweet and low"),
    (r"\bsnapping\s+turtle\b", "snapping turtle"),
    (r"\bpotato\s+head\b", "potato head"),
]

# Skip these common phrases (too basic)
SKIP_PHRASES = {
    "come on",
    "kind of",
    "oh my god",
    "no way",
    "by the way",
    "shut up",
    "high school",
}


# Words that are clearly A1-A2 or B1-B2 even if not in word_list.csv
KNOWN_BEGINNER = {
    "angry",
    "alone",
    "beer",
    "bookcase",
    "boots",
    "building",
    "cafeteria",
    "chalk",
    "coffee",
    "cookie",
    "cup",
    "date",
    "decaf",
    "dinner",
    "dream",
    "floor",
    "furniture",
    "guess",
    "hammer",
    "hat",
    "hanger",
    "interview",
    "invite",
    "legs",
    "lucky",
    "married",
    "mean",
    "naked",
    "neck",
    "pants",
    "presents",
    "purse",
    "relax",
    "scary",
    "shoe",
    "single",
    "sorry",
    "stairs",
    "stuff",
    "sweetie",
    "throat",
    "wedding",
    "weird",
    "wrong",
    "actor",
    "bell",
    "bought",
    "broke",
    "cannot",
    "cherry",
    "crash",
    "diary",
    "drank",
    "feelings",
    "feet",
    "goodnight",
    "honestly",
    "rose",
    "sale",
    "slept",
    "stopped",
    "string",
    "surprised",
    "vanilla",
}
KNOWN_INTERMEDIATE = {
    "abuse",
    "accidentally",
    "assume",
    "attach",
    "bridesmaid",
    "buzz",
    "cancel",
    "catch",
    "crush",
    "dentist",
    "doubt",
    "establish",
    "factor",
    "familiar",
    "flavor",
    "freezer",
    "geeky",
    "gorgeous",
    "hairpiece",
    "horrible",
    "honeymoon",
    "hormone",
    "horny",
    "hump",
    "idiot",
    "independence",
    "input",
    "intense",
    "lesbian",
    "line",
    "mess",
    "metaphor",
    "noodles",
    "omelet",
    "pain",
    "perform",
    "pour",
    "prison",
    "production",
    "regional",
    "serve",
    "serving",
    "smash",
    "socks",
    "spoon",
    "stepdad",
    "stereo",
    "survivor",
    "trained",
    "upbeat",
    "valuable",
    "western",
    "wooden",
    "spit",
    "sophisticate",
}


def get_level(freq, word=""):
    """Determine CEFR level from Friends show frequency + known word lists."""
    if word in KNOWN_BEGINNER:
        return "A1-A2"
    if word in KNOWN_INTERMEDIATE:
        return "B1-B2"
    if freq >= 100:
        return "A1-A2"
    elif freq >= 15:
        return "B1-B2"
    else:
        return "C1-C2"


class Command(BaseCommand):
    help = "Extract vocabulary from episode transcript with auto-leveling from word_list.csv"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True)
        parser.add_argument("--season", type=int, required=True)
        parser.add_argument("--episode", type=int, required=True)

    def handle(self, *args, **options):
        source = Source.objects.filter(id=options["source"]).first()
        if not source:
            self.stderr.write(self.style.ERROR("Source not found"))
            return

        episode = Episode.objects.filter(
            source=source,
            season=options["season"],
            episode_number=options["episode"],
        ).first()
        if not episode:
            self.stderr.write(self.style.ERROR("Episode not found"))
            return

        # Load frequency data
        freq_map = {}
        freq_file = Path("word_list.csv")
        if freq_file.exists():
            with open(freq_file) as f:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) == 2:
                        freq_map[parts[0].lower()] = int(parts[1])

        # Load transcript lines
        transcripts = list(Transcript.objects.filter(episode=episode).order_by("start_time"))
        if not transcripts:
            self.stderr.write(self.style.ERROR("No transcript found for this episode"))
            return

        # Build a set of character/proper names to skip
        # (from the source — could be extended per show)
        NAMES = {
            "ross",
            "rachel",
            "rach",
            "monica",
            "chandler",
            "joey",
            "phoebe",
            "carol",
            "barry",
            "carl",
            "brandy",
            "geppetto",
            "joni",
            "chachi",
            "liza",
            "minnelli",
            "david",
            "joan",
            "pinocchio",
            "billy",
            "tony",
            "dimarco",
            "limoges",
            "rachael",
            "vegas",
            "lincoln",
            "florida",
            "aruba",
            "ruba",
            "charles",
            "paul",
            "wee",
            "las",
        }

        # Common inflection suffixes to strip for frequency lookup
        def stem_word(w):
            """Try to find base form in freq_map.

            Conservative stemming — only strips safe inflectional suffixes
            to avoid over-stemming (hammer→ham, finally→fin).
            """
            # 1. -ied/-ier/-iest → y FIRST: scariest→scary, married→marry
            # (check before direct match so "scary" wins over "scariest")
            for suffix in ["ied", "ier", "iest", "ies"]:
                if w.endswith(suffix) and len(w) - len(suffix) >= 2:
                    base_y = w[: -len(suffix)] + "y"
                    if base_y in freq_map:
                        return base_y, freq_map[base_y]

            if w in freq_map:
                return w, freq_map[w]

            # 2. e-adding: hoping→hope, making→make (only -ing/-ed)
            for suffix in ["ing", "ed"]:
                if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                    base_e = w[: -len(suffix)] + "e"
                    if base_e in freq_map:
                        return base_e, freq_map[base_e]

            # 3. Plain suffix removal (ONLY safe inflections: -ing, -ed, -s, -ly)
            for suffix in ["ing", "ed", "s", "ly"]:
                if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                    base = w[: -len(suffix)]
                    if base in freq_map:
                        return base, freq_map[base]
                    # Double consonant: stopping→stop
                    if len(base) >= 2 and base[-1] == base[-2]:
                        shorter = base[:-1]
                        if shorter in freq_map:
                            return shorter, freq_map[shorter]

            # 4. Fallback: return clean base for words not in freq_map
            for suffix in ["ied", "ier", "iest", "ies"]:
                if w.endswith(suffix) and len(w) - len(suffix) >= 2:
                    return w[: -len(suffix)] + "y", 0
            for suffix in ["ing", "ed"]:
                if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                    base_e = w[: -len(suffix)] + "e"
                    if len(base_e) >= 3:
                        return base_e, 0

            return w, 0

        # Extract phrases FIRST (so we can exclude phrase-words from single words)
        phrase_data = {}  # phrase -> context_line
        for tr in transcripts:
            for pattern, phrase_name in PHRASE_PATTERNS:
                if phrase_name in SKIP_PHRASES:
                    continue
                if phrase_name in phrase_data:
                    continue
                if re.search(pattern, tr.text, re.IGNORECASE):
                    phrase_data[phrase_name] = tr.text

        # Build set of "phrase key words" to exclude from single-word extraction
        # e.g. "catch on" → exclude "catch", "fixate on" → exclude "fixate"
        STOP_IN_PHRASES = {
            "a",
            "an",
            "the",
            "in",
            "on",
            "at",
            "to",
            "of",
            "up",
            "out",
            "off",
            "down",
            "with",
            "for",
            "it",
            "your",
        }
        phrase_key_words = set()
        for phrase in phrase_data:
            for pw in phrase.lower().split():
                if pw not in STOP_IN_PHRASES:
                    phrase_key_words.add(pw)

        # Extract single words with context
        word_data = {}  # word -> (context_line, freq)
        for tr in transcripts:
            words = re.findall(r"[a-zA-Z']+", tr.text)
            for w in words:
                wl = w.lower().strip("'")
                # Strip common contractions
                for suffix in ["'s", "'t", "'re", "'m", "'ve", "'ll", "'d"]:
                    wl = wl.replace(suffix, "")
                if wl in SKIP_WORDS or wl in NAMES or len(wl) <= 2:
                    continue
                if wl not in word_data:
                    base, freq = stem_word(wl)
                    # Skip if base form is in skip list or ultra-common
                    if base in SKIP_WORDS or freq > 500:
                        continue
                    # Skip words that are key parts of extracted phrases
                    if base in phrase_key_words:
                        continue
                    # Use base form as the word (avoid duplicates like "boot" and "boots")
                    if base in word_data:
                        continue
                    word_data[base] = (tr.text, freq)

        # Build output sorted by level
        words_by_level = {"A1-A2": [], "B1-B2": [], "C1-C2": []}
        for word, (context, freq) in sorted(word_data.items()):
            level = get_level(freq, word)
            words_by_level[level].append((word, context, freq))

        # Output file
        s = options["season"]
        e = options["episode"]
        out_path = Path(f"media/translation/friends.s{s:02d}.e{e:02d}.draft.srt")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with open(out_path, "w") as f:
            f.write(f"# Friends S{s:02d}E{e:02d} — Vocabulary + Context\n")
            f.write(
                "# ChatGPT: 'Fill in Definition and Uzbek Translation based on Context. Keep definitions under 5 words.'\n\n"
            )

            for level_name, label in [
                ("A1-A2", "BEGINNER (A1-A2)"),
                ("B1-B2", "INTERMEDIATE (B1-B2)"),
                ("C1-C2", "ADVANCED (C1-C2)"),
            ]:
                items = words_by_level[level_name]
                if not items:
                    continue
                f.write(f"{label}\n\n")
                f.write("| Word | Definition | Uzbek Translation | Context |\n")
                f.write("| --- | --- | --- | --- |\n")
                for word, context, freq in items:
                    ctx = context[:80] + "..." if len(context) > 80 else context
                    f.write(f'| {word} | | | "{ctx}" |\n')
                f.write("\n")

            # Phrases
            if phrase_data:
                f.write("PHRASES & IDIOMS\n\n")
                f.write("| Phrase | Definition | Uzbek Translation | Context |\n")
                f.write("| --- | --- | --- | --- |\n")
                for phrase, context in sorted(phrase_data.items()):
                    ctx = context[:80] + "..." if len(context) > 80 else context
                    f.write(f'| {phrase} | | | "{ctx}" |\n')

        # Summary
        total_words = sum(len(v) for v in words_by_level.values())
        total_phrases = len(phrase_data)
        self.stdout.write(f"\nExtracted from {len(transcripts)} transcript lines:")
        self.stdout.write(f"  A1-A2: {len(words_by_level['A1-A2'])} words")
        self.stdout.write(f"  B1-B2: {len(words_by_level['B1-B2'])} words")
        self.stdout.write(f"  C1-C2: {len(words_by_level['C1-C2'])} words")
        self.stdout.write(f"  Phrases: {total_phrases}")
        self.stdout.write(f"  Total: {total_words + total_phrases}")
        self.stdout.write(self.style.SUCCESS(f"\nSaved to: {out_path}"))
