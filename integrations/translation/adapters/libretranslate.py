import requests

from integrations.translation.port import TranslationPort

_TIMEOUT = 5  # seconds


class LibreTranslateAdapter:
    """Self-hosted LibreTranslate instance.

    Opt-in: only included in the chain when LIBRETRANSLATE_URL is set in settings.
    Run locally with: docker run -p 5000:5000 libretranslate/libretranslate
    """

    def __init__(self, url: str, api_key: str = "") -> None:
        self._url = url.rstrip("/") + "/translate"
        self._api_key = api_key

    def translate(self, text: str, source: str = "en", target: str = "uz") -> str:
        payload: dict = {"q": text, "source": source, "target": target, "format": "text"}
        if self._api_key:
            payload["api_key"] = self._api_key
        resp = requests.post(self._url, json=payload, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("translatedText", "") or ""


assert isinstance(LibreTranslateAdapter("http://localhost:5000"), TranslationPort)
