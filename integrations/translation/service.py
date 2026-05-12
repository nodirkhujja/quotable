"""Public surface of the translation integration.

Call translate() from anywhere in the codebase — adapters are an
implementation detail. The chain is built lazily so importing this
module never makes network calls.
"""

import structlog
from django.conf import settings

from integrations.translation.adapters.deep_translator import DeepTranslatorAdapter
from integrations.translation.adapters.mymemory import MyMemoryAdapter
from integrations.translation.port import TranslationError, TranslationPort

log = structlog.get_logger(__name__)


def translate(text: str, source: str = "en", target: str = "uz") -> str:
    """Translate *text* from *source* language to *target*.

    Tries each adapter in the configured chain in order, returning the
    first successful non-empty result. Raises TranslationError only when
    every adapter has failed.
    """
    if not text or not text.strip():
        return ""

    chain = _build_chain()
    errors: list[Exception] = []

    for adapter in chain:
        try:
            result = adapter.translate(text, source, target)
            if result and result.strip():
                return result.strip()
        except Exception as exc:
            log.warning(
                "translation.adapter.failed",
                adapter=type(adapter).__name__,
                error=str(exc),
            )
            errors.append(exc)

    raise TranslationError(f"All adapters failed for {text!r}") from (errors[-1] if errors else None)


def _build_chain() -> list[TranslationPort]:
    """Assemble the adapter chain from settings.

    Default: DeepTranslator → MyMemory
    Optional: prepend LibreTranslate when LIBRETRANSLATE_URL is configured.
    """
    chain: list[TranslationPort] = [DeepTranslatorAdapter(), MyMemoryAdapter()]

    libre_url = getattr(settings, "LIBRETRANSLATE_URL", None)
    if libre_url:
        from integrations.translation.adapters.libretranslate import LibreTranslateAdapter

        api_key = getattr(settings, "LIBRETRANSLATE_API_KEY", "")
        chain.insert(0, LibreTranslateAdapter(url=libre_url, api_key=api_key))

    return chain
