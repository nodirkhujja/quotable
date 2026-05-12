import requests

from integrations.translation.port import TranslationPort

_ENDPOINT = "https://api.mymemory.translated.net/get"
_TIMEOUT = 5  # seconds


class MyMemoryAdapter:
    """Free MyMemory REST API — no key required, 5 req/s rate limit.

    Used as a fallback when DeepTranslatorAdapter fails.
    """

    def translate(self, text: str, source: str = "en", target: str = "uz") -> str:
        resp = requests.get(
            _ENDPOINT,
            params={"q": text, "langpair": f"{source}|{target}"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        translation = data.get("responseData", {}).get("translatedText", "")
        if not translation or translation.upper() == text.upper():
            raise ValueError(f"MyMemory returned no useful translation for {text!r}")
        return translation


assert isinstance(MyMemoryAdapter(), TranslationPort)
