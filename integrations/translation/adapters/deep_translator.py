from integrations.translation.port import TranslationPort


class DeepTranslatorAdapter:
    """Adapter over deep-translator's GoogleTranslator.

    Requires the `deep-translator` package (already in pyproject.toml).
    Calls Google Translate under the hood — no API key needed for low volume.
    """

    def translate(self, text: str, source: str = "en", target: str = "uz") -> str:
        from deep_translator import GoogleTranslator

        return GoogleTranslator(source=source, target=target).translate(text) or ""


# Structural check — caught at import time during tests
assert isinstance(DeepTranslatorAdapter(), TranslationPort)
