"""Notebook service layer — business logic for word-saving, enrichment,
spaced repetition scheduling, and companion state.

Views in learning/views/notebook.py are thin wrappers around these functions.
"""

from __future__ import annotations

import json as _json
import random
import re
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.shortcuts import get_object_or_404
from django.utils import timezone

from clips.models import Transcript
from learning.models import OnboardingSession
from vocab.models import LineVocab, SuggestedWord, VocabWord, WordNote

# ─── Text helpers ────────────────────────────────────────────────────────────


def normalize_word(raw: str) -> str:
    """Clean user-selected text into a saveable word/phrase."""
    w = raw.strip().lower()
    w = re.sub(r"^[^\w\[]+|[^\w\]]+$", "", w)
    w = re.sub(r"\s+", " ", w)
    return w.strip()


def humanize_interval(days: int) -> str:
    """Friendly copy for the finish screen."""
    if days <= 1:
        return "tomorrow"
    if days < 7:
        return f"in {days} days"
    if days < 14:
        return "in 1 week"
    if days < 30:
        return f"in {days // 7} weeks"
    if days < 60:
        return "in 1 month"
    if days < 180:
        return f"in {days // 30} months"
    return "in 6 months"


def google_translate(text: str, source: str = "en", target: str = "uz") -> str:
    from deep_translator import GoogleTranslator

    return GoogleTranslator(source=source, target=target).translate(text)


# ─── Data-pull helpers ────────────────────────────────────────────────────────


def pull_from_suggested(
    word: str,
    episode_id: int | None = None,
    source_id: int | None = None,
) -> tuple[list, str, str]:
    """Return (usage_examples, grammar_note, translation) from SuggestedWord."""
    qs = SuggestedWord.objects.filter(word=word.lower())
    if episode_id:
        qs = qs.filter(episode_id=episode_id)
    elif source_id:
        qs = qs.filter(source_id=source_id)
    sw = qs.first()
    if not sw:
        return [], "", ""
    return sw.usage_examples or [], sw.grammar_note or "", sw.translation or ""


# ─── Note enrichment ──────────────────────────────────────────────────────────


def enrich_note_with_video(note: WordNote) -> None:
    """Attach video_url, video_start/end, slow_start/end, context_* to a WordNote."""
    tr = note.transcript
    q = note.quote

    if tr:
        video_url = ""
        try:
            if tr.episode and tr.episode.video_file and tr.episode.video_file.name:
                video_url = tr.episode.video_file.url
            elif tr.source and tr.source.video_file and tr.source.video_file.name:
                video_url = tr.source.video_file.url
        except (ValueError, AttributeError):
            pass
        note.video_url = video_url
        note.video_start = max(0.0, float(tr.start_time) - 1.0)
        note.video_end = float(tr.end_time) + 0.5
        note.slow_start = float(tr.start_time)
        note.slow_end = float(tr.end_time)
        note.context_text = tr.text
        note.context_source = tr.source
        note.context_episode = tr.episode
        note.scene_frame_url = note.scene_frame.url if note.scene_frame and note.scene_frame.name else ""

    elif q:
        video_url = ""
        try:
            if q.episode and q.episode.video_file and q.episode.video_file.name:
                video_url = q.episode.video_file.url
            elif q.source and q.source.video_file and q.source.video_file.name:
                video_url = q.source.video_file.url
        except (ValueError, AttributeError):
            pass
        note.video_url = video_url
        note.video_start = float(q.start_time)
        note.video_end = float(q.end_time)
        note.slow_start = float(q.start_time)
        note.slow_end = float(q.end_time)
        try:
            buf = Decimal("1")
            tqs = Transcript.objects.filter(
                text__icontains=note.word,
                start_time__gte=Decimal(str(q.start_time)) - buf,
                start_time__lte=Decimal(str(q.end_time)) + buf,
            )
            if q.episode:
                tqs = tqs.filter(episode=q.episode)
            else:
                tqs = tqs.filter(source=q.source, episode__isnull=True)
            found = tqs.order_by("start_time").first()
            if found:
                note.slow_start = float(found.start_time)
                note.slow_end = float(found.end_time)
                note.video_start = max(0.0, float(found.start_time) - 1.0)
                note.video_end = float(found.end_time) + 0.5
        except Exception:
            pass
        note.context_text = q.text
        note.context_source = q.source
        note.context_episode = q.episode
        note.scene_frame_url = note.scene_frame.url if note.scene_frame and note.scene_frame.name else ""

    else:
        note.video_url = ""
        note.video_start = note.video_end = 0
        note.slow_start = note.slow_end = 0
        note.context_text = ""
        note.context_source = None
        note.context_episode = None
        note.scene_frame_url = ""


def enrich_notes_with_tavsif(notes: list[WordNote]) -> None:
    """Batch-attach tavsif + smart_mcq from LineVocab to each WordNote."""
    from learning.utils.quiz_distractors import augment_smart_mcq

    transcript_ids = [n.transcript_id for n in notes if n.transcript_id]
    if not transcript_ids:
        for n in notes:
            n.tavsif = ""
            n.smart_mcq = {}
            n.smart_mcq_json = "{}"
        return

    vocab_map: dict[int, dict[str, Any]] = {}
    for lv in LineVocab.objects.filter(transcript_id__in=transcript_ids).only(
        "id",
        "transcript_id",
        "english",
        "tavsif",
        "smart_mcq",
        "description",
        "pattern",
        "vocab_id",
        "category",
        "level",
        "kind",
        "confusable_with",
        "translated_examples",
    ):
        augmented_mcq = augment_smart_mcq(lv, lv.smart_mcq) if lv.smart_mcq else {}
        vocab_map.setdefault(lv.transcript_id, {})[lv.english.lower()] = {
            "tavsif": lv.tavsif or "",
            "smart_mcq": augmented_mcq,
            "description": lv.description or "",
            "pattern": lv.pattern or "",
        }

    for note in notes:
        note.tavsif = ""
        note.smart_mcq = {}
        note.smart_mcq_json = "{}"
        note.lexicon_description = ""
        note.lexicon_pattern = ""
        if note.transcript_id and note.transcript_id in vocab_map:
            entry = vocab_map[note.transcript_id].get(note.word.lower())
            if entry:
                note.tavsif = entry["tavsif"]
                note.smart_mcq = entry["smart_mcq"]
                note.lexicon_description = entry["description"]
                note.lexicon_pattern = entry["pattern"]
                if note.smart_mcq:
                    note.smart_mcq_json = _json.dumps(note.smart_mcq)


# ─── Deep Learn ───────────────────────────────────────────────────────────────


def load_deep_learn_note(user, note_id: int) -> WordNote:
    """Fetch + enrich + lazy-generate AI examples for the Deep Learn surface."""
    note = get_object_or_404(
        WordNote.objects.select_related(
            "quote",
            "quote__source",
            "quote__episode",
            "transcript",
            "transcript__source",
            "transcript__episode",
        ),
        id=note_id,
        user=user,
    )
    enrich_note_with_video(note)
    enrich_notes_with_tavsif([note])

    if not note.usage_examples:
        try:
            from learning.utils.ai_examples import generate_usage_examples

            examples = generate_usage_examples(
                note.word,
                definition=note.lexicon_description or note.definition or "",
                translation=note.translation or "",
                count=3,
            )
            if examples:
                note.usage_examples = examples
                note.save(update_fields=["usage_examples"])
        except Exception:
            pass

    return note


# ─── Word notebook page helpers ───────────────────────────────────────────────


def group_notes_by_episode(words: list[WordNote]) -> list[dict]:
    """Bucket WordNotes by (source, episode), ordered by recency."""
    groups: dict[str, dict] = {}
    for note in words:
        src = note.context_source
        ep = note.context_episode
        source_title = src.title if src else ""
        source_thumb = ""
        if src is not None:
            try:
                if src.thumbnail and src.thumbnail.name:
                    source_thumb = src.thumbnail.url
            except (ValueError, AttributeError):
                pass

        if ep is not None:
            key = f"ep-{ep.id}"
            ep_code = f"S{ep.season:02d}E{ep.episode_number:02d}"
            ep_title = (ep.title or "").strip()
            if ep_title:
                title = ep_title
                meta_parts = [p for p in (source_title, ep_code) if p]
            else:
                title = ep_code
                meta_parts = [source_title] if source_title else []
            meta_line = " · ".join(meta_parts)
            label, sublabel = ep_code, ep_title or source_title
        elif src is not None:
            key = f"src-{src.id}"
            title, meta_line, ep_code, ep_title = source_title, "", "", ""
            label, sublabel = source_title, ""
        else:
            key = "standalone"
            title, meta_line, ep_code, ep_title = "Standalone", "Words saved without a scene", "", ""
            label, sublabel = "Standalone", "Words saved without a scene"

        g = groups.get(key)
        if g is None:
            g = {
                "key": key,
                "title": title,
                "meta_line": meta_line,
                "source_title": source_title,
                "thumbnail_url": source_thumb,
                "ep_code": ep_code,
                "ep_title": ep_title,
                "label": label,
                "sublabel": sublabel,
                "words": [],
                "due_count": 0,
                "max_created_at": note.created_at,
            }
            groups[key] = g
        g["words"].append(note)
        if note.is_due:
            g["due_count"] += 1
        if note.created_at > g["max_created_at"]:
            g["max_created_at"] = note.created_at

    ordered = sorted(groups.values(), key=lambda g: g["max_created_at"], reverse=True)
    for g in ordered:
        g["count"] = len(g["words"])
    if ordered:
        ordered[0]["default_open"] = True
    return ordered


def build_zpd_config(user) -> dict:
    """Build Zone of Proximal Development config from onboarding data."""
    try:
        session = OnboardingSession.objects.get(user=user)
    except OnboardingSession.DoesNotExist:
        return {"zpd_tiers": [], "word_tiers": {}}

    zpd_tiers: list[int] = []
    tier_breakdown = session.tier_breakdown or {}
    is_new_format = any(isinstance(v, int) for v in tier_breakdown.values())

    if is_new_format:
        bucket_to_tiers = {"easy": [1, 2], "medium": [3, 4], "expert": [5]}
        BUCKET_SIZE = 11
        for bucket, tiers in bucket_to_tiers.items():
            known = tier_breakdown.get(bucket, 0)
            if not isinstance(known, int) or known < 0:
                continue
            accuracy = known / BUCKET_SIZE if BUCKET_SIZE > 0 else 0
            if 0.30 <= accuracy <= 0.75:
                for t in tiers:
                    if t not in zpd_tiers:
                        zpd_tiers.append(t)
    else:
        for tier_str, counts in tier_breakdown.items():
            if not isinstance(counts, dict):
                continue
            shown = counts.get("shown", 0)
            known = counts.get("known", 0)
            if shown > 0:
                accuracy = known / shown
                if 0.30 <= accuracy <= 0.75:
                    try:
                        zpd_tiers.append(int(tier_str))
                    except (TypeError, ValueError):
                        continue

    word_tiers: dict[str, int] = {vw.word.lower(): vw.tier for vw in VocabWord.objects.all()}

    phrases = session.phrases_breakdown or {}
    for tier_str, tb in (phrases.get("tier_breakdown") or {}).items():
        if not isinstance(tb, dict):
            continue
        total = tb.get("total", 0)
        known = tb.get("known", 0)
        if total > 0:
            accuracy = known / total
            try:
                t = int(tier_str)
            except (TypeError, ValueError):
                continue
            if 0.30 <= accuracy <= 0.75 and t not in zpd_tiers:
                zpd_tiers.append(t)

    return {"zpd_tiers": sorted(zpd_tiers), "word_tiers": word_tiers}


# ─── Notebook companion ───────────────────────────────────────────────────────


def build_notebook_companion(user) -> dict:
    """Pick ONE contextual line for the word notebook header.

    Priority (first match wins):
      1. No words yet → nudge to save first.
      2. Recent win (reviewed in last hour) → positive reinforcement.
      3. Single deeply-overdue word with a scene → offer 5-second refresher.
      4. Stack of 4+ overdue → batch CTA.
      5. All caught up → quiet line with next-due timing.
    """
    now = timezone.now()
    notes = WordNote.objects.filter(user=user)
    total = notes.count()

    if total == 0:
        return {
            "tone": "neutral",
            "headline": "Tap a word while you're watching to start your collection.",
            "subline": "",
            "action": None,
        }

    recent_win = (
        notes.filter(last_reviewed_at__gte=now - timedelta(hours=1), review_count__gte=1)
        .order_by("-last_reviewed_at")
        .first()
    )
    if recent_win:
        return {
            "tone": "positive",
            "headline": f"You nailed “{recent_win.word}” recently. That one’s solid.",
            "subline": "",
            "action": None,
        }

    due = notes.filter(next_review__lte=now).order_by("next_review")
    due_count = due.count()

    deeply_overdue = due.filter(
        next_review__lte=now - timedelta(hours=24),
        transcript__isnull=False,
    ).first()
    if deeply_overdue:
        enrich_note_with_video(deeply_overdue)
        if deeply_overdue.video_url:
            days_ago = max(1, (now - deeply_overdue.next_review).days)
            return {
                "tone": "warn",
                "headline": f"You haven’t seen “{deeply_overdue.word}” in {days_ago} day{'s' if days_ago != 1 else ''}.",
                "subline": "5-second refresher?",
                "action": {
                    "action": "replay",
                    "note_id": deeply_overdue.id,
                    "video_url": deeply_overdue.video_url,
                    "video_start": float(getattr(deeply_overdue, "video_start", 0) or 0),
                    "video_end": float(getattr(deeply_overdue, "video_end", 0) or 0),
                    "slow_start": float(getattr(deeply_overdue, "slow_start", 0) or 0),
                    "slow_end": float(getattr(deeply_overdue, "slow_end", 0) or 0),
                    "label": "Refresh",
                    "href": None,
                },
            }

    if due_count >= 4:
        return {
            "tone": "warn",
            "headline": f"{due_count} words to review.",
            "subline": "A short Scene Recall round catches them up.",
            "action": {"action": "review_all", "note_id": None, "label": "Review", "href": "/learning/scene-recall/"},
        }
    if due_count >= 1:
        first = due.first()
        if first:
            return {
                "tone": "warn",
                "headline": f"“{first.word}” is due.",
                "subline": "Quick Scene Recall round?",
                "action": {
                    "action": "review_all",
                    "note_id": first.id,
                    "label": "Review",
                    "href": "/learning/scene-recall/",
                },
            }

    soonest = notes.filter(next_review__gt=now).order_by("next_review").first()
    sub = ""
    if soonest and soonest.next_review:
        delta = soonest.next_review - now
        hours = int(delta.total_seconds() // 3600)
        if hours <= 1:
            sub = "Next word due within the hour."
        elif hours < 24:
            sub = f"Next word due in about {hours} hours."
        else:
            days = max(1, hours // 24)
            sub = f"Next word due in {days} day{'s' if days != 1 else ''}."

    return {"tone": "calm", "headline": "All caught up.", "subline": sub, "action": None}


# ─── Challenge (Battle) drill ─────────────────────────────────────────────────


def build_word_battle_data(note: WordNote) -> dict:
    """Build stage-aware round list for the Challenge drill.

    Round progression easy→hard per stage:
      inbox    → [listen, mean_en, context]
      learning → [listen, spell, pronounce, context]
      mastered → [mean_en, context, pronounce, produce_mic]
    """
    tavsif = ""
    lex_desc = ""
    lex_entry = None
    if note.transcript_id:
        lex_entry = LineVocab.objects.filter(transcript_id=note.transcript_id, english__iexact=note.word).first()
        if lex_entry:
            tavsif = lex_entry.tavsif or ""
            lex_desc = lex_entry.description or ""
    if not tavsif:
        tavsif = note.personal_note or ""

    description = lex_desc or note.definition or ""
    context_text = ""
    if note.transcript:
        context_text = note.transcript.text or ""
    elif note.quote:
        context_text = note.quote.text or ""

    stage = note.stage or "inbox"
    type_plan: list[str] = {
        "inbox": ["listen", "mean_en", "context"],
        "learning": ["listen", "spell", "pronounce", "context"],
        "mastered": ["mean_en", "context", "pronounce", "produce_mic"],
    }.get(stage, ["listen", "mean_en", "context"])

    pool_words = list(
        WordNote.objects.filter(user_id=note.user_id)
        .exclude(id=note.id)
        .exclude(word="")
        .values_list("word", flat=True)[:100]
    )
    random.shuffle(pool_words)

    _fallback = ["stay", "go", "think", "leave", "keep", "bring", "hold", "lose"]

    def _pick_distractors(n: int = 3) -> list[str]:
        d = [w for w in pool_words if w.lower() != note.word.lower()][:n]
        while len(d) < n:
            d.append(random.choice(_fallback))
        return d

    def _listen():
        opts = [note.word] + _pick_distractors(3)
        random.shuffle(opts)
        return {
            "type": "listen",
            "quiz_type": "listen",
            "prompt_word": note.word,
            "options": opts,
            "answer": note.word,
        }

    def _mean_en():
        if not description:
            return None
        opts = [note.word] + _pick_distractors(3)
        random.shuffle(opts)
        return {"type": "mean_en", "quiz_type": "flash", "prompt": description, "options": opts, "answer": note.word}

    def _spell():
        return {"type": "spell", "quiz_type": "listen", "prompt_word": note.word, "answer": note.word.lower().strip()}

    def _context():
        search_term = re.sub(r"[\[\(].*?[\]\)]", "", note.word).strip() or note.word
        pattern = re.compile(r"\b" + re.escape(search_term) + r"\b", re.IGNORECASE)
        scene_sent = context_text if (context_text and pattern.search(context_text)) else ""
        lex_sents: list[str] = []
        if lex_entry:
            for ex in lex_entry.translated_examples or []:
                if isinstance(ex, dict):
                    en = (ex.get("en") or "").strip()
                    if en and pattern.search(en) and en not in lex_sents:
                        lex_sents.append(en)
            raw = (lex_entry.example or "").strip()
            if raw:
                parts = [p.strip().lstrip("-• ").strip() for p in re.split(r"[\n;]|(?<=[.!?])\s+/\s+", raw)]
                for p in parts:
                    if p and pattern.search(p) and p not in lex_sents:
                        lex_sents.append(p)
        if stage == "inbox":
            chosen = scene_sent or (random.choice(lex_sents) if lex_sents else "")
        else:
            chosen = random.choice(lex_sents) if lex_sents else scene_sent
        if not chosen:
            return None
        cloze = pattern.sub("_____", chosen, count=1)
        is_transfer = bool(lex_sents) and stage != "inbox" and chosen != scene_sent
        return {
            "type": "context",
            "quiz_type": "cloze",
            "prompt": cloze,
            "answer": search_term.lower().strip(),
            "is_transfer": is_transfer,
        }

    def _pronounce():
        return {
            "type": "pronounce",
            "quiz_type": "produce",
            "prompt_word": note.word,
            "answer": note.word.lower().strip(),
        }

    def _produce_mic():
        if not note.translation:
            return None
        return {
            "type": "produce_mic",
            "quiz_type": "produce",
            "prompt": note.translation,
            "target_word": note.word,
            "answer": note.word.lower().strip(),
        }

    builders = {
        "listen": _listen,
        "mean_en": _mean_en,
        "spell": _spell,
        "context": _context,
        "pronounce": _pronounce,
        "produce_mic": _produce_mic,
    }

    rounds = [r for t in type_plan if (b := builders.get(t)) and (r := b())]
    if not rounds:
        rounds = [_listen()]

    return {
        "note_id": note.id,
        "word": note.word,
        "translation": note.translation,
        "stage": stage,
        "confidence": note.confidence,
        "tavsif": tavsif,
        "description": description,
        "context_text": context_text,
        "rounds": rounds,
    }
