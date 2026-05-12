from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class DictionaryEntry:
    """A normalised definition result returned by any dictionary adapter."""

    pos: str  # "v" | "n" | "adj" | "phr" | "etc." | "!" | "err"
    definition: str


@runtime_checkable
class DictionaryPort(Protocol):
    """Look up a single English word and return its definition.

    Implementors must be stateless — the service layer reuses one instance.
    Raise any exception on hard failure; the service catches and logs it.
    Return a DictionaryEntry with pos="!" / "err" for soft failures (not-found,
    empty response) so the caller always gets a usable value.
    """

    def define(self, word: str) -> DictionaryEntry: ...
