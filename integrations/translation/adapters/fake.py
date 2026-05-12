from integrations.translation.port import TranslationPort


class FakeTranslationAdapter:
    """Test double — returns a deterministic string without hitting any network.

    Usage in tests:
        from integrations.translation.adapters.fake import FakeTranslationAdapter
        from integrations.translation import service
        monkeypatch.setattr(service, "_build_chain", lambda: [FakeTranslationAdapter()])
    """

    def translate(self, text: str, source: str = "en", target: str = "uz") -> str:
        return f"[fake:{text}]"


assert isinstance(FakeTranslationAdapter(), TranslationPort)
