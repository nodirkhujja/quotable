"""Backward-compatible shim — delegates to integrations.dictionary.service."""

from integrations.dictionary.service import define


def get_micro_definition(word: str) -> dict:
    entry = define(word)
    return {"pos": entry.pos, "definition": entry.definition}
