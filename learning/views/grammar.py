from __future__ import annotations

import base64
import json
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from clips.models import Episode, Quote, Source, Transcript
from learning.models import (
    BuildAttempt,
    ConfusionPair,
    DailyActivity,
    FavoriteQuote,
    FlashcardAttempt,
    GrammarAIExplanation,
    GrammarPracticeLog,
    LearningProgress,
    LookupLog,
    OnboardingResult,
    OnboardingSession,
    PageVisit,
    PatternMastery,
    PronunciationAssessment,
    QuoteMastery,
    SceneCoverage,
    SentencePattern,
    ShadowingLog,
    SourceProgress,
    UserInterest,
    UserLearningProfile,
)
from learning.services.grammar import build_learner_context as _build_learner_context
from learning.services.grammar import pick_interest as _pick_interests
from learning.services.rate_limiting import is_rate_limited as _rate_limited
from learning.services.rate_limiting import rate_limit_response as _rate_limit_response
from learning.utils.ai_sentence import check_sentence as ai_check_sentence
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success
from vocab.models import LineTranslation, LineVocab, SuggestedWord, VocabMastery, VocabWord, WordNote, WordTranslation


class GrammarHubView(LoginRequiredMixin, View):
    """GET /learning/grammar/ — Grammar course hub with 11 units."""

    UNIT_MAP = {
        "unit_1_present_simple": 1,
        "unit_2_present_continuous": 2,
        "unit_3_past_simple": 3,
        "unit_4_future": 4,
        "unit_5_articles": 5,
        "unit_6_prepositions": 6,
        "unit_7_modals": 7,
        "unit_8_conditionals": 8,
    }

    def get(self, request):
        diagnostic = None
        try:
            session = OnboardingSession.objects.get(user=request.user)
            gb = session.grammar_breakdown or {}
            units_data = gb.get("per_unit", gb)

            scores = []
            for key, counts in units_data.items():
                unit_id = self.UNIT_MAP.get(key)
                if not unit_id:
                    continue
                total = counts.get("total", 0)
                correct = counts.get("correct", 0)
                if total > 0:
                    pct = round(correct / total * 100)
                    scores.append({"unit_id": unit_id, "pct": pct, "correct": correct, "total": total})

            if scores:
                scores.sort(key=lambda x: x["unit_id"])
                strong = [s for s in scores if s["pct"] >= 80]
                weak = sorted([s for s in scores if s["pct"] < 70], key=lambda x: x["pct"])
                diagnostic = json.dumps(
                    {
                        "level": session.grammar_level or "",
                        "scores": scores,
                        "strong_ids": [s["unit_id"] for s in strong],
                        "weak_ids": [s["unit_id"] for s in weak],
                        "start_unit": weak[0]["unit_id"] if weak else None,
                    }
                )
        except OnboardingSession.DoesNotExist:
            pass

        return render(
            request,
            "learning/grammar.html",
            {
                "diagnostic_json": diagnostic or "null",
            },
        )


class GrammarUnitView(LoginRequiredMixin, View):
    """GET /learning/grammar/<unit_id>/ — Single grammar unit lesson."""

    def get(self, request, unit_id):
        import json
        from pathlib import Path

        if unit_id < 1 or unit_id > 11:
            return redirect("learning:grammar")

        # Load grammar clips for this unit
        clips_path = Path(__file__).parent / "templates" / "learning" / "grammar_clips.json"
        clips = []
        video_map = {}
        if clips_path.exists():
            with open(clips_path) as f:
                all_clips = json.load(f)
            clips = all_clips.get(str(unit_id), [])

            # Build video URL map for referenced episodes
            if clips:
                from clips.models import Episode, Source

                source_ids = {c["source_id"] for c in clips}
                ep_keys = {c["episode"] for c in clips if c.get("episode")}

                for source in Source.objects.filter(id__in=source_ids):
                    if source.source_type == "tv_show":
                        for ep in source.episodes.exclude(video_file=""):
                            key = f"S{ep.season}E{ep.episode_number}"
                            if key in ep_keys:
                                video_map[key] = ep.video_file.url
                    elif source.video_file:
                        video_map["movie"] = source.video_file.url

        return render(
            request,
            "learning/grammar_unit.html",
            {
                "unit_id": unit_id,
                "grammar_clips_json": json.dumps(clips, ensure_ascii=False),
                "clip_video_map_json": json.dumps(video_map, ensure_ascii=False),
            },
        )


class GrammarExplainView(LoginRequiredMixin, View):
    """POST /learning/grammar/<unit_id>/explain/ — AI explanation for a wrong MCQ pick.

    Request JSON:
        {"question_index": 3, "chosen_option": 1}

    Response JSON:
        {"explanation_uz": "..."}  or  {"explanation_uz": ""} on failure
    """

    # Minimal server-side mirror of the unit rule content rendered into
    # grammar_unit.html. Grown lazily as new units are added. Keeping it here
    # (instead of a DB table or JSON file) because the content is tightly
    # co-authored with the template's UNITS object and changes together.
    # Unit titles + Uzbek rules MUST match the frontend grammar.html UNITS
    # array AND the keys in grammar_questions.json. The hub UI labels each
    # tile by id; learners click "Future" tile (id=4) and expect Future
    # questions. Keep this aligned or AI generates wrong-tense Qs.
    _UNIT_RULES_UZ = {
        1: (
            "Present Simple — har kuni takrorlanadigan ishlar va har doim to'g'ri "
            "bo'lgan faktlar uchun. Uchinchi shaxsda (he/she/it) fe'lga -s qo'shiladi; "
            "inkor don't/doesn't; savol Do/Does."
        ),
        2: (
            "Present Continuous — ayni hozir bo'layotgan ishlar uchun. "
            "am/is/are + verb-ing. Stative fe'llar (like, want, know) Continuous bo'lmaydi."
        ),
        3: (
            "Past Simple — o'tmishda tugagan ishlar uchun. Ikkinchi shakl (went, saw) "
            "yoki -ed; inkor didn't + base; savol Did + base."
        ),
        4: (
            "Future (will / going to) — kelajakdagi voqealar uchun. will + base verb "
            "(qaror, ehtimol, va'da); going to + base verb (rejalashtirilgan harakat, "
            "hozirgi belgilarga asoslangan bashorat). Inkor: won't, am/is/are not going to."
        ),
        5: (
            "Articles (a / an / the / ∅) — a/an birinchi marta tilga olinayotgan, "
            "sanaladigan, birlikdagi ot oldida; the aniq narsa uchun (ilgari aytilgan, "
            "yagona, kontekstdan ma'lum); ∅ (artiksiz) — umumiy ma'noda yoki sanalmaydigan ot."
        ),
        6: (
            "Prepositions (in / on / at) — vaqt: in 2024, on Monday, at 5 pm. "
            "Joy: in the room, on the table, at the door. Eng ko'p chalkashtiriladigan "
            "uchta predloglar — qaysi paytda qaysi biri ishlatilishini yodlang."
        ),
        7: (
            "Modal Verbs (can, should, must, have to, may) — qobiliyat, ruxsat, "
            "majburiyat, maslahat va imkoniyat uchun. Modaldan keyin har doim base "
            "verb keladi (modal + V1)."
        ),
        8: (
            "Conditionals — Zero/First/Second/Third. Zero: umumiy haqiqat (if + present, "
            "+ present). First: real ehtimol (if + present, will + base). Second: xayoliy "
            "hozir (if + past, would + base). Third: o'tmishdagi afsus (if + had + p.p., "
            "would have + p.p.)."
        ),
        9: (
            "Present Perfect — o'tmishda bo'lgan va hozir ham ta'sir qilayotgan ishlar uchun. "
            "have/has + past participle (3-shakl). since/for/ever/never/just/already/yet — signal."
        ),
        10: (
            "Relative Clauses (who / which / that) — ikkita gapni bog'lashda ishlatiladi. "
            "who — odamlar uchun; which — narsalar/hayvonlar uchun; that — ikkalasi "
            "uchun ham ishlatiladi (informal). Defining vs non-defining relative clauses."
        ),
        11: (
            "Linking Words — gaplarni bog'lash uchun. because (sabab); so (natija); "
            "although/though (qarama-qarshi); however (lekin, formal); while (paytida); "
            "since (chunki / -dan beri)."
        ),
    }

    _UNIT_TITLES = {
        1: "Present Simple",
        2: "Present Continuous",
        3: "Past Simple",
        4: "Future (will / going to)",
        5: "Articles (a / an / the)",
        6: "Prepositions (in / on / at)",
        7: "Modal Verbs (can / should / must / have to)",
        8: "Conditionals",
        9: "Present Perfect",
        10: "Relative Clauses (who / which / that)",
        11: "Linking Words (because / so / although)",
    }

    def post(self, request, unit_id):
        from pathlib import Path

        from learning.models import GrammarAIExplanation

        if unit_id < 1 or unit_id > 11:
            return error("Invalid unit", 400)

        data = json_body(request)
        try:
            q_idx = int(data.get("question_index"))
            chosen = int(data.get("chosen_option"))
        except (TypeError, ValueError):
            return error("question_index and chosen_option must be integers", 400)

        # Cache hit? Return immediately + bump hit count.
        cached = GrammarAIExplanation.objects.filter(
            unit_id=unit_id,
            question_index=q_idx,
            chosen_option=chosen,
        ).first()
        if cached:
            GrammarAIExplanation.objects.filter(pk=cached.pk).update(
                hit_count=models.F("hit_count") + 1,
            )
            return success({"explanation_uz": cached.explanation_uz, "cached": True})

        # Cache miss — load the question, call Gemini.
        # Rate limit only applies on the miss path; cached replies are free.
        limited, _remaining, retry_after = _rate_limited(request.user.id, "explain")
        if limited:
            return _rate_limit_response("explain", retry_after)

        q_path = Path(__file__).parent / "templates" / "learning" / "grammar_questions.json"
        if not q_path.exists():
            return error("Question bank missing", 500)
        with open(q_path) as f:
            all_questions = json.load(f)
        unit_questions = all_questions.get(str(unit_id), [])
        if q_idx < 0 or q_idx >= len(unit_questions):
            return error("question_index out of range", 400)

        q = unit_questions[q_idx]
        if q.get("type") != "mcq":
            return error("Only MCQ questions supported", 400)

        correct_idx = q.get("answer")
        if chosen == correct_idx:
            return error("Chosen option is correct — nothing to explain", 400)

        try:
            from learning.utils.ai_grammar import explain_wrong_answer
        except ImportError:
            return error("AI module unavailable", 500)

        explanation = explain_wrong_answer(
            unit_title=self._UNIT_TITLES.get(unit_id, f"Unit {unit_id}"),
            unit_rule_uz=self._UNIT_RULES_UZ.get(unit_id, ""),
            sentence=q.get("sentence", ""),
            options=q.get("options", []),
            correct_idx=correct_idx,
            chosen_idx=chosen,
            translation_uz=q.get("translation", ""),
        )

        if not explanation:
            # Don't cache failures — let next user retry.
            return success({"explanation_uz": "", "cached": False})

        # Persist — future students on this same wrong path get instant response.
        GrammarAIExplanation.objects.update_or_create(
            unit_id=unit_id,
            question_index=q_idx,
            chosen_option=chosen,
            defaults={"explanation_uz": explanation},
        )
        return success({"explanation_uz": explanation, "cached": False})


class GrammarAIQuestionsView(LoginRequiredMixin, View):
    """POST /learning/grammar/<unit_id>/ai-questions/

    Returns the 5 personalized AI questions appended after the standard 10.
    Currently ships just the 2 Bridge (Uz→En translation) items — Bug and
    Voice types will be added later. Always generated fresh per session so
    each student gets different sentences tailored to their interests.
    """

    def post(self, request, unit_id):
        import logging

        log = logging.getLogger(__name__)

        if unit_id < 1 or unit_id > 11:
            return error("Invalid unit", 400)

        limited, _remaining, retry_after = _rate_limited(request.user.id, "ai_questions")
        if limited:
            log.info("grammar_ai.rate_limited user=%s endpoint=ai_questions", request.user.id)
            return _rate_limit_response("ai_questions", retry_after)

        interest_pool, user_name, user_level = _build_learner_context(request.user)

        unit_rules = GrammarExplainView._UNIT_RULES_UZ
        unit_titles = GrammarExplainView._UNIT_TITLES
        unit_title = unit_titles.get(unit_id, f"Unit {unit_id}")
        unit_rule_uz = unit_rules.get(unit_id, "")

        try:
            from learning.utils.ai_grammar import generate_bridge_question, generate_bug_story, generate_voice_task
        except ImportError:
            return error("AI module unavailable", 500)

        # Pre-pick a distinct interest per question — done synchronously before
        # we fan out to Gemini so the selection logic is deterministic.
        used: list[str] = []
        pick_bridge_1 = _pick_interests(interest_pool, used)
        pick_bridge_2 = _pick_interests(interest_pool, used)
        pick_bug = _pick_interests(interest_pool, used)
        pick_voice_1 = _pick_interests(interest_pool, used)
        pick_voice_2 = _pick_interests(interest_pool, used)

        shared_kwargs = dict(
            unit_title=unit_title,
            unit_rule_uz=unit_rule_uz,
            user_name=user_name,
            user_level=user_level,
        )

        # Each item: (slot_key, generator_callable, slot-specific kwargs).
        # Keys ensure stable ordering when we stitch results back together.
        tasks = [
            ("bridge_1", generate_bridge_question, {**shared_kwargs, "interest": pick_bridge_1}),
            ("bridge_2", generate_bridge_question, {**shared_kwargs, "interest": pick_bridge_2}),
            ("bug", generate_bug_story, {**shared_kwargs, "interest": pick_bug}),
            ("voice_1", generate_voice_task, {**shared_kwargs, "interest": pick_voice_1}),
            ("voice_2", generate_voice_task, {**shared_kwargs, "interest": pick_voice_2}),
        ]

        # ── Fire the 5 Gemini calls IN PARALLEL ──
        # The 4-key chain (paid primary + free fallbacks) gives us thousands
        # of RPM headroom — sequential was a workaround for the free-tier
        # 15-RPM cap and is no longer needed. Parallel cuts wall-time from
        # ~25-50s sequential to ~3-8s (whichever single task is slowest).
        # Each generator function is already self-retrying internally for
        # pattern validation; we add ONE outer retry only for bug-hunt
        # because its shape constraint (exactly 3 bugs, each appearing
        # exactly once) is the strictest in the chain.
        RETRY_COUNTS = {"bug": 2, "bridge_1": 1, "bridge_2": 1, "voice_1": 1, "voice_2": 1}

        # Import the pattern validator once for view-level defense in depth.
        # Even if the generator slipped (cache bug, race, future regression)
        # and returned a wrong-tense payload, this rejects it before it
        # leaves the server. This is the second wall behind the generator's
        # own validate-and-retry loop.
        from learning.utils.ai_grammar import _validates_pattern

        def _valid(key, payload):
            if not payload:
                return False
            if key.startswith("bridge"):
                if not (payload.get("uzbek") and payload.get("sample_en")):
                    return False
                if not _validates_pattern(unit_title, payload["sample_en"]):
                    log.warning(
                        "grammar_ai.view_pattern_reject key=%s unit=%s sample=%r",
                        key,
                        unit_title,
                        payload["sample_en"][:120],
                    )
                    return False
                return True
            if key.startswith("voice"):
                if not (payload.get("uzbek") and payload.get("sample_en")):
                    return False
                if not _validates_pattern(unit_title, payload["sample_en"]):
                    log.warning(
                        "grammar_ai.view_pattern_reject key=%s unit=%s sample=%r",
                        key,
                        unit_title,
                        payload["sample_en"][:120],
                    )
                    return False
                return True
            if key == "bug":
                if not (payload.get("story") and len(payload.get("bugs", [])) == 3):
                    return False
                # For bug-hunt, validate the CORRECTED story (bugs swapped
                # back to right forms) against the unit pattern.
                corrected = payload["story"]
                for b in payload["bugs"]:
                    w = str(b.get("wrong", "")).strip()
                    c = str(b.get("correct", "")).strip()
                    if w and c:
                        import re as _re

                        corrected = _re.sub(
                            r"\b" + _re.escape(w) + r"\b",
                            c,
                            corrected,
                            count=1,
                            flags=_re.IGNORECASE,
                        )
                if not _validates_pattern(unit_title, corrected):
                    log.warning(
                        "grammar_ai.view_pattern_reject key=%s unit=%s story=%r",
                        key,
                        unit_title,
                        payload["story"][:120],
                    )
                    return False
                return True
            return False

        def _run_one(key, fn, kw):
            """Run a single generator with up to RETRY_COUNTS[key] outer attempts."""
            attempts = RETRY_COUNTS.get(key, 1)
            payload = {}
            for attempt in range(attempts):
                try:
                    payload = fn(**kw) or {}
                except Exception as e:  # noqa: BLE001
                    log.warning("grammar_ai.task_failed key=%s attempt=%d cls=%s", key, attempt + 1, type(e).__name__)
                    payload = {}
                if _valid(key, payload):
                    return key, payload
                if attempt + 1 < attempts:
                    log.info("grammar_ai.retry_validation key=%s attempt=%d", key, attempt + 1)
            return key, payload

        from concurrent.futures import ThreadPoolExecutor

        results: dict[str, dict] = {}
        # max_workers = number of tasks — each task has its own thread, all
        # fire concurrently. Generators are pure (no shared mutable state)
        # so this is thread-safe; _call_gemini's internal `_config_lock`
        # already serializes the genai.configure() global.
        with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
            futures = [ex.submit(_run_one, key, fn, kw) for key, fn, kw in tasks]
            for fut in futures:
                try:
                    key, payload = fut.result(timeout=60)  # generous per-task ceiling
                    results[key] = payload
                except Exception as e:  # noqa: BLE001
                    log.warning("grammar_ai.task_future_failed cls=%s", type(e).__name__)

        # ── Stitch results into the response payload, preserving order ──
        questions = []

        def _take_bridge(key, interest_used):
            q = results.get(key, {})
            if q.get("uzbek") and q.get("sample_en"):
                questions.append(
                    {
                        "type": "bridge",
                        "uzbek": q["uzbek"],
                        "sample_en": q["sample_en"],
                        "focus": q.get("focus", ""),
                        "hard_words": q.get("hard_words", []),
                        "interest_used": interest_used,
                    }
                )

        def _take_voice(key, interest_used):
            v = results.get(key, {})
            if v.get("uzbek") and v.get("sample_en"):
                questions.append(
                    {
                        "type": "voice_real",
                        "uzbek": v["uzbek"],
                        "sample_en": v["sample_en"],
                        "scene": v.get("scene", ""),
                        "hard_words": v.get("hard_words", []),
                        "interest_used": interest_used,
                    }
                )

        _take_bridge("bridge_1", pick_bridge_1)
        _take_bridge("bridge_2", pick_bridge_2)

        bug = results.get("bug", {})
        if bug.get("story") and len(bug.get("bugs", [])) == 3:
            questions.append(
                {
                    "type": "bug_hunt",
                    "title_uz": bug.get("title_uz", ""),
                    "story": bug["story"],
                    "bugs": bug["bugs"],
                    "interest_used": pick_bug,
                }
            )

        _take_voice("voice_1", pick_voice_1)
        _take_voice("voice_2", pick_voice_2)

        log.info("grammar_ai.session_generated unit=%d produced=%d/%d", unit_id, len(questions), len(tasks))

        # Summarize why the batch may have come back empty / partial so the
        # frontend can show a specific message to the learner instead of a
        # generic "unavailable". We don't expose raw exception classes — just
        # a coarse reason code the UI can map to friendly copy.
        ai_status = "ok"
        ai_reason = ""
        if not questions:
            ai_status = "unavailable"
            # With all 5 parallel calls returning empty, the overwhelmingly
            # common cause is Gemini free-tier quota. Server logs
            # (grammar_ai.gemini_error) have the precise cls= for forensics.
            ai_reason = "quota_exhausted"
        elif len(questions) < len(tasks):
            ai_status = "partial"
            ai_reason = "some_calls_failed"

        return success(
            {
                "questions": questions,
                "ai_status": ai_status,
                "ai_reason": ai_reason,
            }
        )


class GrammarCheckVoiceView(LoginRequiredMixin, View):
    """POST /learning/grammar/<unit_id>/check-voice/

    Grades a speech-to-text transcript against an AI-generated Uzbek prompt.

    Request JSON:
        {"uzbek": "...", "sample_en": "...", "transcript": "..."}
    """

    def post(self, request, unit_id):
        if unit_id < 1 or unit_id > 11:
            return error("Invalid unit", 400)

        limited, _remaining, retry_after = _rate_limited(request.user.id, "check_voice")
        if limited:
            return _rate_limit_response("check_voice", retry_after)

        data = json_body(request)
        uzbek = str(data.get("uzbek") or "").strip()
        sample_en = str(data.get("sample_en") or "").strip()
        transcript = str(data.get("transcript") or "").strip()
        # Top-3 SpeechRecognition alternatives. Optional — falls back to the
        # single transcript when the client doesn't send any (e.g. text-typed
        # fallback). When present, the grader picks the best fit before
        # judging — this is what stops "won the case one versus three" from
        # being marked wrong when the learner actually said "clutched a 1v3".
        raw_alts = data.get("transcript_alternatives") or []
        if not isinstance(raw_alts, list):
            raw_alts = []
        # Sanitize: strings only, ≤ 500 chars each, max 3.
        alternatives = [str(a).strip() for a in raw_alts if isinstance(a, str) and a.strip() and len(a) <= 500][:3]
        if not uzbek or not transcript:
            return error("Missing uzbek or transcript", 400)
        if len(transcript) > 500:
            return error("Transcript too long", 400)

        _interests, user_name, user_level = _build_learner_context(request.user)
        unit_rules = GrammarExplainView._UNIT_RULES_UZ
        unit_titles = GrammarExplainView._UNIT_TITLES

        try:
            from learning.utils.ai_grammar import check_voice_answer
        except ImportError:
            return error("AI module unavailable", 500)

        result = check_voice_answer(
            uzbek_prompt=uzbek,
            transcript=transcript,
            unit_title=unit_titles.get(unit_id, f"Unit {unit_id}"),
            unit_rule_uz=unit_rules.get(unit_id, ""),
            sample_en=sample_en,
            user_name=user_name,
            user_level=user_level,
            transcript_alternatives=alternatives,
        )
        return success(result)


class GrammarCheckBridgeView(LoginRequiredMixin, View):
    """POST /learning/grammar/<unit_id>/check-bridge/

    Checks a learner's English translation of an AI-generated Uzbek sentence.
    Returns a dev-style feedback payload (status / review / fix).

    Request JSON:
        {"uzbek": "...", "sample_en": "...", "user_english": "..."}
    """

    def post(self, request, unit_id):
        if unit_id < 1 or unit_id > 11:
            return error("Invalid unit", 400)

        limited, _remaining, retry_after = _rate_limited(request.user.id, "check_bridge")
        if limited:
            return _rate_limit_response("check_bridge", retry_after)

        data = json_body(request)
        uzbek = str(data.get("uzbek") or "").strip()
        sample_en = str(data.get("sample_en") or "").strip()
        user_english = str(data.get("user_english") or "").strip()
        if not uzbek or not user_english:
            return error("Missing uzbek or user_english", 400)
        if len(user_english) > 500:
            return error("Answer too long", 400)

        _interests, user_name, user_level = _build_learner_context(request.user)
        unit_rules = GrammarExplainView._UNIT_RULES_UZ
        unit_titles = GrammarExplainView._UNIT_TITLES

        try:
            from learning.utils.ai_grammar import check_bridge_answer
        except ImportError:
            return error("AI module unavailable", 500)

        result = check_bridge_answer(
            uzbek_prompt=uzbek,
            user_english=user_english,
            unit_title=unit_titles.get(unit_id, f"Unit {unit_id}"),
            unit_rule_uz=unit_rules.get(unit_id, ""),
            sample_en=sample_en,
            user_name=user_name,
            user_level=user_level,
        )
        return success(result)
