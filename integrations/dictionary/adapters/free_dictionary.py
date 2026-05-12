import re

import requests

from integrations.dictionary.port import DictionaryEntry, DictionaryPort

_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{}"
_TIMEOUT = 5  # seconds

# Maps dictionaryapi.dev partOfSpeech values → internal POS abbreviations.
# Must stay in sync with vocab.models.WordCache.PostType choices.
_POS_MAP = {
    "verb": "v",
    "noun": "n",
    "adjective": "adj",
    "adverb": "adv",
}


def _clean_definition(raw: str) -> str:
    """Trim to ≤6 words, drop trailing bridge words, lowercase."""
    if not raw:
        return ""
    core = re.split(r"[.;\(]", raw)[0].strip()
    if core.lower().startswith("to "):
        core = core[3:].strip()
    words = core.split()
    if len(words) > 6:
        bridge = {"especially", "with", "for", "to", "and", "or", "of", "in", "by"}
        words = words[:6]
        while words and words[-1].lower().strip(", ") in bridge:
            words.pop()
        core = " ".join(words)
    return core.strip(",").lower()


def _infer_pos(raw_pos: str, word: str) -> str:
    if " " in word or "-" in word:
        return "phr"
    for prefix, abbrev in _POS_MAP.items():
        if raw_pos.startswith(prefix):
            return abbrev
    return "etc."


class FreeDictionaryAdapter:
    """Adapter for the free dictionaryapi.dev API (no key required)."""

    def define(self, word: str) -> DictionaryEntry:
        url = _API_URL.format(word.replace(" ", "%20"))
        resp = requests.get(url, timeout=_TIMEOUT)

        if resp.status_code == 404:
            return DictionaryEntry(pos="!", definition="not found")

        resp.raise_for_status()
        data = resp.json()

        meanings = data[0].get("meanings", []) if data else []
        if not meanings:
            return DictionaryEntry(pos="etc.", definition="no definition found")

        first = meanings[0]
        raw_pos = first.get("partOfSpeech", "")
        pos = _infer_pos(raw_pos, word)

        defs = first.get("definitions", [])
        raw_text = defs[0].get("definition", "") if defs else ""
        definition = _clean_definition(raw_text)

        return DictionaryEntry(pos=pos, definition=definition)


assert isinstance(FreeDictionaryAdapter(), DictionaryPort)
