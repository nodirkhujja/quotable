import re

import requests
from django.db import transaction

from learning.models import WordCache


def clean_definition(raw_def):
    if not raw_def:
        return ""

    core_idea = re.split(r"[.;\(]", raw_def)[0].strip()

    if core_idea.lower().startswith("to "):
        core_idea = core_idea[3:].strip()

    words = core_idea.split()
    if len(words) > 6:
        words = words[:6]
        bridge_words = {"especially", "with", "for", "to", "and", "or", "of", "in", "by"}

        while words and words[-1].lower().strip(", ") in bridge_words:
            words.pop()

        core_idea = " ".join(words)

    return core_idea.strip(",").lower()


def get_micro_definition(word):
    """
    Main utility to get a definition.
    Checks local cache first, then hits the external API.
    """
    if not word:
        return {"pos": "!", "definition": "no word provided"}

    # Step 1: Normalize for DB and Internal Logic
    clean_word = word.strip().lower()

    # Step 2: Database Cache Lookup
    cached_entry = WordCache.objects.filter(word=clean_word).first()
    if cached_entry:
        return {"pos": cached_entry.pos, "definition": cached_entry.definition}

    # Step 3: API Fetching
    encoded_query = clean_word.replace(" ", "%20")
    api_url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{encoded_query}"

    try:
        response = requests.get(api_url, timeout=5)

        if response.status_code == 404:
            return {"pos": "!", "definition": "not found"}

        response.raise_for_status()
        data = response.json()

        # Step 4: Data Extraction & Mapping
        first_entry = data[0]
        meanings = first_entry.get("meanings", [])
        if not meanings:
            return {"pos": "etc.", "definition": "no definition found"}

        first_meaning = meanings[0]
        raw_pos = first_meaning.get("partOfSpeech", "n")

        # Determine the standardized POS Choice
        if " " in clean_word or "-" in clean_word:
            final_pos = WordCache.PostType.PHRASE
        elif raw_pos.startswith("verb"):
            final_pos = WordCache.PostType.VERB
        elif raw_pos.startswith("noun"):
            final_pos = WordCache.PostType.NOUN
        elif raw_pos.startswith("adj"):
            final_pos = WordCache.PostType.ADJECTIVE
        else:
            final_pos = WordCache.PostType.OTHER

        # Get and Clean Definition
        definitions = first_meaning.get("definitions", [])
        raw_text = definitions[0].get("definition", "") if definitions else ""
        final_definition = clean_definition(raw_text)

        # Step 5: Save to Cache (Atomic Transaction)
        with transaction.atomic():
            WordCache.objects.get_or_create(
                word=clean_word, defaults={"pos": final_pos, "definition": final_definition}
            )

        return {"pos": final_pos, "definition": final_definition}

    except (requests.RequestException, IndexError, KeyError):
        return {"pos": "err", "definition": "service unavailable"}
