"""Public surface of the dictionary integration.

Call define() from anywhere in the codebase. The WordCache DB table is
owned here — adapters are pure HTTP and never touch the database.
"""

import structlog
from django.db import transaction

from integrations.dictionary.adapters.free_dictionary import FreeDictionaryAdapter
from integrations.dictionary.port import DictionaryEntry

log = structlog.get_logger(__name__)

_adapter = FreeDictionaryAdapter()


def define(word: str) -> DictionaryEntry:
    """Return a definition for *word*, hitting the cache before the network.

    Always returns a DictionaryEntry — callers never need to handle None.
    Soft failures (word not found, API error) are reflected in the pos field
    ("!" = not found, "err" = service error).
    """
    if not word or not word.strip():
        return DictionaryEntry(pos="!", definition="no word provided")

    clean = word.strip().lower()

    # 1. Cache hit
    from vocab.models import WordCache

    cached = WordCache.objects.filter(word=clean).first()
    if cached:
        return DictionaryEntry(pos=cached.pos, definition=cached.definition)

    # 2. Adapter call
    try:
        entry = _adapter.define(clean)
    except Exception as exc:
        log.warning("dictionary.adapter.failed", word=clean, error=str(exc))
        return DictionaryEntry(pos="err", definition="service unavailable")

    # 3. Persist to cache (only real results, not error sentinels)
    if entry.pos not in ("!", "err", "etc.") or entry.definition not in (
        "not found",
        "no definition found",
        "service unavailable",
    ):
        with transaction.atomic():
            WordCache.objects.get_or_create(
                word=clean,
                defaults={"pos": entry.pos, "definition": entry.definition},
            )

    return entry
