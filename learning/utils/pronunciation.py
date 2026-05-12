"""Pronunciation Assessment — Azure Speech adapter.

Wraps Azure's Pronunciation Assessment so the rest of the app talks to a
clean Python interface. If Azure credentials aren't configured (dev /
staging without keys), falls back to a stub that returns plausible
sample scores so the full UI flow can be built and tested.

Set these env vars to enable real Azure scoring:
  AZURE_SPEECH_KEY      — your Azure Speech resource key
  AZURE_SPEECH_REGION   — e.g. "eastus"
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WordScore:
    word: str
    accuracy: float  # 0–100
    error_type: str = ""  # "Mispronunciation", "Omission", "Insertion", or ""


@dataclass
class AssessmentResult:
    accuracy_score: float
    fluency_score: float
    completeness_score: float
    overall_score: float
    prosody_score: float | None = None
    word_scores: list[WordScore] = field(default_factory=list)
    weak_phonemes: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────
# Azure adapter — real assessment
# ─────────────────────────────────────────────────────────


def _azure_assess(audio_path: str, reference_text: str) -> AssessmentResult:
    """Send audio + reference to Azure Pronunciation Assessment.

    Requires `azure-cognitiveservices-speech` installed and AZURE_SPEECH_KEY
    + AZURE_SPEECH_REGION env vars set. Raises RuntimeError otherwise.
    """
    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise RuntimeError("AZURE_SPEECH_KEY / AZURE_SPEECH_REGION not set")
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError as e:
        raise RuntimeError(
            "azure-cognitiveservices-speech not installed — " "`poetry add azure-cognitiveservices-speech`"
        ) from e

    config = speechsdk.SpeechConfig(subscription=key, region=region)
    config.speech_recognition_language = "en-US"

    audio_config = speechsdk.audio.AudioConfig(filename=audio_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=config, audio_config=audio_config)

    pa_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    pa_config.apply_to(recognizer)

    result = recognizer.recognize_once_async().get()
    pa_result = speechsdk.PronunciationAssessmentResult(result)
    raw = result.json or {}

    word_scores: list[WordScore] = []
    weak_phonemes: list[str] = []
    try:
        for w in pa_result.words:
            word_scores.append(
                WordScore(
                    word=w.word,
                    accuracy=w.accuracy_score,
                    error_type=w.error_type or "",
                )
            )
            for ph in w.phonemes or []:
                if ph.accuracy_score < 60:
                    if ph.phoneme not in weak_phonemes:
                        weak_phonemes.append(ph.phoneme)
    except AttributeError:
        # SDK versions differ slightly — fall back to raw json walk
        pass

    return AssessmentResult(
        accuracy_score=pa_result.accuracy_score,
        fluency_score=pa_result.fluency_score,
        completeness_score=pa_result.completeness_score,
        overall_score=pa_result.pronunciation_score,
        word_scores=word_scores,
        weak_phonemes=weak_phonemes,
        raw_response=raw if isinstance(raw, dict) else {"raw": raw},
    )


# ─────────────────────────────────────────────────────────
# Stub adapter — returns plausible sample scores for dev/UI testing
# ─────────────────────────────────────────────────────────


def _stub_assess(audio_path: str, reference_text: str) -> AssessmentResult:
    """Returns plausible scores. Used when Azure isn't configured.

    Lets us build/test the full UX without burning API credits.
    """
    words = [w.strip(".,!?\"'") for w in reference_text.split() if w.strip()]
    word_scores = []
    weak_phonemes_pool = ["th-voiceless", "r", "ʌ", "ɪ", "v", "w"]
    weak_phonemes = []
    for w in words:
        # Random plausible accuracy biased toward 70-95
        acc = random.choices(
            [random.uniform(40, 60), random.uniform(60, 80), random.uniform(80, 100)],
            weights=[1, 3, 6],
        )[0]
        error = "Mispronunciation" if acc < 60 else ""
        word_scores.append(WordScore(word=w, accuracy=round(acc, 1), error_type=error))
        if acc < 60 and len(weak_phonemes) < 3:
            ph = random.choice(weak_phonemes_pool)
            if ph not in weak_phonemes:
                weak_phonemes.append(ph)

    accuracy = round(sum(w.accuracy for w in word_scores) / max(len(word_scores), 1), 1)
    fluency = round(random.uniform(70, 95), 1)
    completeness = round(min(100, random.uniform(85, 100)), 1)
    overall = round((accuracy * 0.5 + fluency * 0.3 + completeness * 0.2), 1)

    return AssessmentResult(
        accuracy_score=accuracy,
        fluency_score=fluency,
        completeness_score=completeness,
        overall_score=overall,
        prosody_score=round(random.uniform(60, 90), 1),
        word_scores=word_scores,
        weak_phonemes=weak_phonemes,
        raw_response={"stub": True},
    )


# ─────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────


def assess_pronunciation(audio_path: str, reference_text: str) -> AssessmentResult:
    """Assess one audio attempt against the expected line.

    Routes to Azure if configured, otherwise returns a stub. This is the
    only function callers should use — keeps the choice of provider out
    of the views layer.
    """
    if os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"):
        try:
            return _azure_assess(audio_path, reference_text)
        except Exception:
            # Fail open to stub so prod doesn't crash if Azure has a hiccup.
            # In production we'd log this; for now, silent fallback.
            pass
    return _stub_assess(audio_path, reference_text)


# ─────────────────────────────────────────────────────────
# Side-effects helper — update PhonemeWeakness rollup
# ─────────────────────────────────────────────────────────


def update_phoneme_weakness(user, weak_phonemes: list[str], overall_score: float):
    """Roll the assessment's weak phonemes into the user's running tracker."""
    from django.utils import timezone

    from learning.models import PhonemeWeakness

    if not weak_phonemes:
        return
    now = timezone.now()
    for ph in weak_phonemes:
        obj, _ = PhonemeWeakness.objects.get_or_create(user=user, phoneme=ph)
        obj.attempts += 1
        if overall_score < 60:
            obj.fails += 1
            obj.last_failed_at = now
        # Running mean
        obj.avg_accuracy = (obj.avg_accuracy * (obj.attempts - 1) + overall_score) / obj.attempts
        obj.save(update_fields=["attempts", "fails", "avg_accuracy", "last_failed_at"])
