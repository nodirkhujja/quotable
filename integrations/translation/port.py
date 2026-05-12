from typing import Protocol, runtime_checkable


class TranslationError(Exception):
    """Raised when every adapter in the chain has failed."""


@runtime_checkable
class TranslationPort(Protocol):
    """Translate a piece of text from one language to another.

    Implementors must be stateless and thread-safe — the service layer
    reuses a single instance per process.
    """

    def translate(self, text: str, source: str = "en", target: str = "uz") -> str:
        """Return the translated string.

        Raise any exception on failure; the service layer catches it,
        logs it, and tries the next adapter in the chain.
        """
        ...
