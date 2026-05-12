"""Gemini-powered usage-example generator for vocabulary words.

Given a word/phrase + (optional) definition + Uzbek translation, returns 3
short, contextually distinct example sentences with Uzbek translations.

Returned shape matches the documented WordNote.usage_examples format:
    [{"en": "...", "uz": "..."}, ...]

Caller is responsible for saving the result back to WordNote.usage_examples
so the AI cost is paid only once per word.
"""

import json
import re

import google.generativeai as genai
from django.conf import settings

_MODEL_NAME = "gemini-2.5-flash-lite"
_model = None


def _get_model():
    global _model
    if _model is None:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _model = genai.GenerativeModel(_MODEL_NAME)
    return _model


def generate_usage_examples(word: str, definition: str = "", translation: str = "", count: int = 3) -> list[dict]:
    """Generate `count` short example sentences using `word` verbatim.

    The model is asked to vary register/context (casual, professional,
    emotional) so the learner gets a wider sense of the phrase's reach.
    """
    word = (word or "").strip()
    if not word:
        return []

    # Some saved phrases use [bracketed] placeholders to mark a slot
    # ("give [someone] a break"). For the prompt, fold the bracket contents
    # in as a hint word ("give someone a break"). For the verbatim filter,
    # collapse the slot to whitespace so anchor words = give / a / break,
    # which Gemini's substituted versions still contain.
    has_placeholder = bool(re.search(r"\[[^\]]+\]", word))
    prompt_word = re.sub(r"\[([^\]]+)\]", r"\1", word).strip()
    # Anchor base for the filter: brackets removed entirely (slot words
    # are substituted by Gemini, so we don't anchor on them).
    check_word = re.sub(r"\s*\[[^\]]+\]\s*", " ", word).strip().lower()
    check_word = re.sub(r"\s+", " ", check_word)

    def_line = f'Definition: "{definition}"' if definition else ""
    trans_line = f'Uzbek translation: "{translation}"' if translation else ""
    placeholder_note = (
        '\n- The phrase contains slot words like "someone" — substitute with a real subject (e.g. "him", "Sarah", "the team").'
        if has_placeholder
        else ""
    )

    prompt = f"""You are an English teacher writing example sentences for an Uzbek learner.

Target phrase: "{prompt_word}"
{def_line}
{trans_line}

Write {count} natural example sentences that USE the target phrase.

Requirements:
- Each sentence must contain the phrase "{prompt_word}" — keep the core meaningful
  words intact. For idioms/multi-word phrases, the phrase should appear as
  a recognizable unit, not split apart.{placeholder_note}
- Vary the contexts clearly: one casual/everyday, one professional/serious,
  one emotional/personal. NO repeated scenarios.
- Keep each sentence between 6 and 14 words. Conversational English.
- Provide an Uzbek translation for each.

Respond with ONLY a JSON array (no markdown, no code fences) of objects:
[
  {{"en": "First English sentence.", "uz": "Birinchi gap o'zbek tilida."}},
  {{"en": "Second sentence.", "uz": "Ikkinchi gap."}},
  {{"en": "Third sentence.", "uz": "Uchinchi gap."}}
]

CRITICAL — Uzbek output rules:
1. Uzbek MUST be in LATIN script (lotin yozuvida), NOT Cyrillic.
   Wrong (Cyrillic): "Бу мен учун муҳим"
   Right  (Latin):    "Bu men uchun muhim"
2. Use modern conversational Uzbek that an O'zbekistonlik would actually say.
3. Uzbek-specific letters use apostrophes: o', g', ch, sh, ng.
"""

    try:
        response = _get_model().generate_content(
            prompt,
            generation_config={"temperature": 0.7},
        )
        text = (response.text or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        if not isinstance(data, list):
            return []
        out = []
        # Build a list of core lemmas to look for. For idioms with [slot]
        # placeholders, we expect Gemini to substitute, so we check that
        # the non-placeholder words appear in order.
        core = re.sub(r"^(it|the|a|to|of)\s+", "", check_word).strip()
        anchor_tokens = [t for t in re.findall(r"[a-z']+", core) if len(t) >= 3]
        for item in data:
            if not isinstance(item, dict):
                continue
            en = str(item.get("en", "")).strip()
            uz = str(item.get("uz", "")).strip()
            if not en:
                continue
            en_low = en.lower()
            # Pass conditions (any one):
            #  1) Full phrase appears verbatim (post-bracket-strip).
            #  2) Phrase with leading filler dropped appears verbatim.
            #  3) For phrases with [slots], every >=3-letter anchor word appears.
            ok = (
                check_word in en_low
                or (core and core in en_low)
                or (has_placeholder and anchor_tokens and all(t in en_low for t in anchor_tokens))
            )
            if ok:
                out.append({"en": en, "uz": uz})
        return out[:count]
    except (json.JSONDecodeError, AttributeError, ValueError):
        return []
    except Exception:
        return []
