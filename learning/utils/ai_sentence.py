"""Gemini-powered sentence check for vocabulary practice.

Given a target word/phrase and a user's sentence, ask Gemini if the usage is
correct. Returns structured feedback in Uzbek so Uzbek-speaker students can
learn from their mistakes.
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


def check_sentence(
    word: str,
    translation: str,
    user_sentence: str,
    example: str = "",
    pattern: str = "",
    user_interests: str = "",
    user_name: str = "",
    user_level: str = "",
    known_words: list | None = None,
) -> dict:
    """Ask Gemini if `user_sentence` uses `word` correctly.

    Args:
        user_interests: things the learner loves (e.g. "Barcelona, Messi").
            Used to make examples personally memorable.
        user_name: learner's first name — for warm, personal feedback.
        user_level: CEFR level (A1-C2) — calibrates feedback complexity.
        known_words: list of words the learner has already saved — AI can
            reference these in explanations to build vocabulary networks.

    Returns:
        {
            "correct": bool,
            "feedback_uz": "Uzbek explanation",
            "corrected": "Fixed sentence or empty if correct",
        }
    """
    interests_line = f"\nThe learner loves: {user_interests}" if user_interests else ""
    name_line = f"\nLearner name: {user_name}" if user_name else ""
    level_line = f"\nLearner CEFR level: {user_level}" if user_level else ""
    known_line = (
        f"\nWords the learner already knows (they saved these): {', '.join(known_words[:10])}" if known_words else ""
    )

    prompt = f"""You are an English teacher helping an Uzbek speaker learn English.

Target word/phrase: "{word}"
Uzbek translation: "{translation}"
{f'Example usage: "{example.split(" / ")[0]}"' if example else ""}
{f"Grammar pattern: {pattern}" if pattern else ""}{name_line}{level_line}{interests_line}{known_line}

The learner wrote this sentence:
"{user_sentence}"

Your task: Check if the learner used "{word}" correctly in meaning AND grammar.

Respond with ONLY a JSON object (no markdown, no code fences):
{{
  "correct": true or false,
  "feedback_uz": "Feedback ONLY in Uzbek (Latin script). NEVER English.",
  "corrected": "If wrong, corrected English sentence. If correct, empty string."
}}

CRITICAL RULES:
1. feedback_uz MUST be written in UZBEK language using Latin letters (o'zbek tilida, lotin yozuvida).
   Example Uzbek feedback: "Gap to'g'ri! 'Rachel and I' deyish kerak, 'me and Rachel' emas."
   NEVER write feedback in English. Always Uzbek.
2. If the sentence uses the word correctly AND grammar is fine → correct: true, praise briefly in Uzbek.
3. If meaning is wrong OR grammar has issues → correct: false, explain the SPECIFIC mistake in Uzbek.
4. Keep feedback to 1-2 short sentences.
5. Be encouraging but honest. Teach the rule.
6. English words can appear in quotes within the Uzbek feedback (e.g., "'drifted apart' to'g'ri shakl").
7. When giving the "corrected" sentence OR an example, PREFER using the learner's interests as the subject (e.g. "Messi", "Barcelona", or a character/place from their forever-rewatch show). This makes feedback memorable and personal.
8. If the learner's name is provided, use it ONCE naturally in praise (e.g. "Zo'r, Alibek!"). Don't over-use it — once per feedback is enough. Skip if no name given.
9. CEFR level calibration: if A1/A2, use SIMPLE Uzbek + avoid grammar jargon. If B1/B2, teach the rule directly. If C1/C2, discuss nuance. Match the learner's level — don't over-simplify for advanced students or overwhelm beginners.
10. If the learner already knows certain words (listed above), feel free to reference them in the "corrected" sentence or explanation — builds a connected vocabulary network. E.g., if they know "drift apart" and are learning "get over", connect the two.
"""

    try:
        response = _get_model().generate_content(prompt)
        text = response.text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        data = json.loads(text)
        return {
            "correct": bool(data.get("correct", False)),
            "feedback_uz": str(data.get("feedback_uz", "")).strip(),
            "corrected": str(data.get("corrected", "")).strip(),
        }
    except json.JSONDecodeError:
        return {
            "correct": False,
            "feedback_uz": "Tekshirib bo'lmadi, qayta urining.",
            "corrected": "",
        }
    except Exception as e:
        return {
            "correct": False,
            "feedback_uz": f"Xatolik: {str(e)[:100]}",
            "corrected": "",
        }
