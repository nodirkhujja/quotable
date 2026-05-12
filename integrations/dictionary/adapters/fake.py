from integrations.dictionary.port import DictionaryEntry, DictionaryPort


class FakeDictionaryAdapter:
    """Test double — no network, deterministic output.

    Usage:
        monkeypatch.setattr(service, "_adapter", FakeDictionaryAdapter())
    """

    def define(self, word: str) -> DictionaryEntry:
        return DictionaryEntry(pos="v", definition=f"[fake definition of {word}]")


assert isinstance(FakeDictionaryAdapter(), DictionaryPort)
