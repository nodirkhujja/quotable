from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import models, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET

from clips.models import Quote, Transcript
from learning.models import (
    FavoriteQuote, LearningProgress, OnboardingResult, OnboardingSession, QuoteMastery, ReviewSession, SourceProgress,
    SuggestedWord, VocabWord, WordNote,
)
from learning.utils.dictionary import get_micro_definition
from learning.utils.responses import error, json_body, success


def _enrich_note_with_video(note):
    """Attach video_url, video_start/end, slow_start/end to a WordNote."""
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
    else:
        note.video_url = ""
        note.video_start = note.video_end = 0
        note.slow_start = note.slow_end = 0
        note.context_text = ""
        note.context_source = None
        note.context_episode = None


def _pull_from_suggested(word, episode_id=None, source_id=None):
    """Pull usage_examples, grammar_note, and translation from SuggestedWord."""
    qs = SuggestedWord.objects.filter(word=word.lower())
    if episode_id:
        qs = qs.filter(episode_id=episode_id)
    elif source_id:
        qs = qs.filter(source_id=source_id)
    sw = qs.first()
    if not sw:
        return [], "", ""
    return sw.usage_examples or [], sw.grammar_note or "", sw.translation or ""


class FavoriteToggleView(LoginRequiredMixin, View):
    def post(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        data = json_body(request)
        favorite = FavoriteQuote.objects.filter(user=request.user, quote=quote).first()

        if favorite:
            favorite.delete()
            SourceProgress.objects.filter(user=request.user, source=quote.source).update(
                quotes_favorited=models.F("quotes_favorited") - 1
            )
            return success(
                {
                    "favorited": False,
                    "total_favorites": FavoriteQuote.objects.filter(quote=quote).count(),
                }
            )
        else:
            favorite = FavoriteQuote.objects.create(
                user=request.user,
                quote=quote,
                emotion_tag=data.get("emotion_tag", ""),
                personal_note=data.get("personal_note", ""),
            )
            QuoteMastery.objects.get_or_create(user=request.user, quote=quote, defaults={"status": "saved"})
            sp, _ = SourceProgress.objects.get_or_create(user=request.user, source=quote.source)
            SourceProgress.objects.filter(pk=sp.pk).update(
                quotes_favorited=models.F("quotes_favorited") + 1,
                last_watched=timezone.now(),
            )
            return success(
                {
                    "favorited": True,
                    "total_favorites": FavoriteQuote.objects.filter(quote=quote).count(),
                    "emotion_tag": favorite.emotion_tag,
                },
                status=201,
            )


class FavoriteListView(LoginRequiredMixin, View):
    def get(self, request):
        # 1. Asosiy favorite'larni olish
        favorites = FavoriteQuote.objects.filter(user=request.user).select_related(
            "quote", "quote__source", "quote__episode"
        )

        # 2. Filtrlar
        source_id = request.GET.get("source_id")
        emotion = request.GET.get("emotion")
        if source_id:
            favorites = favorites.filter(quote__source_id=source_id)
        if emotion:
            favorites = favorites.filter(emotion_tag=emotion)

        # 3. N+1 Muammosini yechish (Prefetching logic)
        # Barcha kerakli Mastery statuslarini bitta so'rovda olamiz
        quote_ids = [f.quote_id for f in favorites]
        masteries = QuoteMastery.objects.filter(user=request.user, quote_id__in=quote_ids).in_bulk(
            field_name="quote_id"
        )

        # 4. Ma'lumotlarni yig'ish
        data = []
        for fav in favorites:
            q = fav.quote
            ep = q.episode

            # Bazaga murojaat qilmasdan xotiradan (dict) olamiz
            mastery = masteries.get(q.id)

            data.append(
                {
                    "id": fav.id,
                    "quote_id": q.id,
                    "text": q.text,
                    "character": getattr(q, "character", ""),
                    "source": q.source.title,
                    "season": ep.season if ep else None,
                    "episode": ep.episode_number if ep else None,
                    "start_time": float(q.start_time),
                    "thumbnail": q.thumbnail.url if q.thumbnail else None,
                    "emotion_tag": fav.emotion_tag,
                    "personal_note": fav.personal_note,
                    "mastery_status": mastery.status if mastery else "saved",
                    "created_at": fav.created_at.isoformat(),
                }
            )

        return success({"favorites": data, "count": len(data)})


class FavoriteUpdateView(LoginRequiredMixin, View):
    def patch(self, request, quote_id):
        favorite = get_object_or_404(FavoriteQuote, user=request.user, quote_id=quote_id)
        data = json_body(request)
        if "emotion_tag" in data:
            favorite.emotion_tag = data["emotion_tag"]
        if "personal_note" in data:
            favorite.personal_note = data["personal_note"]
        favorite.save()
        return success({"emotion_tag": favorite.emotion_tag, "personal_note": favorite.personal_note})


# ─────────────────────────────────────────────
# MASTERY VIEWS
# ─────────────────────────────────────────────

MASTERY_ORDER = ["saved", "learning", "mastered"]
SPACED_REPETITION_INTERVALS = {"saved": 1, "learning": 3, "mastered": 14}


class MasteryUpdateView(LoginRequiredMixin, View):
    """
    Spaced Repetition mantiqi va foydalanuvchi progressini
    atomar tarzda yangilash uchun View.
    """

    def post(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        data = json_body(request)

        # 1. Obyektni olish va "old_status"ni xavfsiz belgilash
        mastery, created = QuoteMastery.objects.get_or_create(
            user=request.user, quote=quote, defaults={"status": "saved"}
        )
        old_status = mastery.status

        # 2. Statusni yangilash logikasi
        if "status" in data:
            new_status = data["status"]
            if new_status not in MASTERY_ORDER:
                return error("Invalid status")
            mastery.status = new_status
        elif data.get("advance"):
            try:
                current_index = MASTERY_ORDER.index(mastery.status)
                if current_index < len(MASTERY_ORDER) - 1:
                    mastery.status = MASTERY_ORDER[current_index + 1]
            except ValueError:
                mastery.status = "learning"  # Fallback
        else:
            return error('Provide "status" or "advance": true')

        # 3. Spaced Repetition parametrlarini hisoblash
        interval = SPACED_REPETITION_INTERVALS.get(mastery.status, 1)
        mastery.interval_days = interval
        mastery.review_count += 1
        mastery.last_reviewed = timezone.now()
        mastery.next_review = timezone.now() + timezone.timedelta(days=interval)

        # 4. LearningProgress counterlarini tayyorlash
        progress, _ = LearningProgress.objects.get_or_create(user=request.user)
        progress.total_quotes_reviewed += 1

        # Faqat status o'zgargandagina counterlarni manipulyatsiya qilamiz
        if not created and old_status != mastery.status:
            count_field_map = {"saved": "saved_count", "learning": "learning_count", "mastered": "mastered_count"}
            old_field = count_field_map.get(old_status)
            new_field = count_field_map.get(mastery.status)

            if old_field:
                setattr(progress, old_field, max(0, getattr(progress, old_field) - 1))
            if new_field:
                setattr(progress, new_field, getattr(progress, new_field) + 1)

        # Agar yangi yaratilgan bo'lsa, default fieldni oshiramiz
        elif created:
            progress.saved_count += 1

        # 5. ATOMIC SAVE (Ma'lumotlar yaxlitligi uchun)
        with transaction.atomic():
            mastery.save()
            progress.save()

        # 6. SourceProgress yangilash (Faqat Mastered bo'lganda)
        if mastery.status == "mastered" and old_status != "mastered":
            SourceProgress.objects.filter(user=request.user, source=quote.source).update(
                quotes_mastered=models.F("quotes_mastered") + 1
            )

        return success(
            {
                "status": mastery.status,
                "review_count": mastery.review_count,
                "interval_days": mastery.interval_days,
                "next_review": mastery.next_review.isoformat(),
            }
        )


class MasteryStatusView(LoginRequiredMixin, View):
    def get(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        mastery = QuoteMastery.objects.filter(user=request.user, quote=quote).first()
        if not mastery:
            return success({"status": None, "review_count": 0})
        return success(
            {
                "status": mastery.status,
                "review_count": mastery.review_count,
                "last_reviewed": mastery.last_reviewed.isoformat() if mastery.last_reviewed else None,
                "next_review": mastery.next_review.isoformat() if mastery.next_review else None,
                "interval_days": mastery.interval_days,
            }
        )


# ─────────────────────────────────────────────
# WORD NOTE VIEWS
# ─────────────────────────────────────────────


class WordNoteCreateView(LoginRequiredMixin, View):
    """
    Automated and User-editable Word Note creation.
    POST /learning/words/quote/<quote_id>/
    """

    def post(self, request, quote_id):
        quote = get_object_or_404(Quote, id=quote_id)
        data = json_body(request)

        # 1. Foydalanuvchi ma'lumotlarini tozalash
        word = data.get("word", "").strip().lower()
        definition = data.get("definition", "").strip()
        pos = data.get("pos", "").strip()
        translation = data.get("translation", "").strip()

        # 2. Validatsiya
        if not word:
            return error("Word is required")

        word_count = len(word.split())
        if word_count > 3:
            return error(f"Target must be 1-3 words. You sent {word_count}.")

        # 3. Dublikatni tekshirish
        if WordNote.objects.filter(user=request.user, quote=quote, word=word).exists():
            return error("You already noted this word from this quote", status=409)

        # 4. Smart Automation (Lookup WordCache/API if fields are empty)
        if not definition or not pos:
            res = get_micro_definition(word)
            if not definition:
                definition = res.get("definition", "Definition not found")
            if not pos:
                pos = res.get("pos", "other")

        # 5. Pull usage data + translation from curated SuggestedWord
        usage_ex, grammar, sw_translation = _pull_from_suggested(
            word,
            episode_id=getattr(quote, "episode_id", None),
            source_id=quote.source_id,
        )
        # Backfill translation from SuggestedWord if empty
        if not translation and sw_translation:
            translation = sw_translation
        # Clear useless definitions (don't fallback to Uzbek — it duplicates translation)
        if definition in ("not found", "Definition not found"):
            definition = ""

        # 6. Database Transaction (Atomic)
        try:
            with transaction.atomic():
                note = WordNote.objects.create(
                    user=request.user,
                    quote=quote,
                    word=word,
                    definition=definition,
                    translation=translation,
                    pos=pos,
                    mood=data.get("mood", ""),
                    emotion_vibe=data.get("emotion_vibe", ""),
                    stage=data.get("stage", "inbox"),
                    personal_note=data.get("personal_note", ""),
                    example_usage=data.get("example_usage") or quote.text,
                    context_type=data.get("context_type", "general"),
                    usage_examples=usage_ex,
                    grammar_note=grammar,
                )

                # Progressni yangilash
                progress, _ = LearningProgress.objects.get_or_create(user=request.user)
                LearningProgress.objects.filter(pk=progress.pk).update(
                    total_words_noted=models.F("total_words_noted") + 1
                )
        except Exception as e:
            return error(f"Database error: {str(e)}", status=500)

        return success(
            {
                "id": note.id,
                "word": note.word,
                "pos": note.pos,
                "definition": note.definition,
                "created_at": note.created_at.isoformat(),
            },
            status=201,
        )


class WordNoteCreateFromTranscriptView(LoginRequiredMixin, View):
    """
    Save a word from a live transcript line.
    POST /learning/words/transcript/<transcript_id>/
    """

    def post(self, request, transcript_id):
        transcript = get_object_or_404(Transcript, id=transcript_id)
        data = json_body(request)

        word = data.get("word", "").strip().lower()
        definition = data.get("definition", "").strip()
        pos = data.get("pos", "").strip()
        translation = data.get("translation", "").strip()

        if not word:
            return error("Word is required")
        if len(word.split()) > 3:
            return error(f"Target must be 1-3 words. You sent {len(word.split())}.")

        if WordNote.objects.filter(user=request.user, transcript=transcript, word=word).exists():
            return error("You already noted this word from this line", status=409)

        if not definition or not pos:
            res = get_micro_definition(word)
            if not definition:
                definition = res.get("definition", "Definition not found")
            if not pos:
                pos = res.get("pos", "other")

        usage_ex, grammar, sw_translation = _pull_from_suggested(
            word,
            episode_id=transcript.episode_id,
            source_id=transcript.source_id,
        )
        if not translation and sw_translation:
            translation = sw_translation
        if definition in ("not found", "Definition not found"):
            definition = ""

        try:
            with transaction.atomic():
                note = WordNote.objects.create(
                    user=request.user,
                    transcript=transcript,
                    word=word,
                    definition=definition,
                    translation=translation,
                    pos=pos,
                    mood=data.get("mood", ""),
                    emotion_vibe=data.get("emotion_vibe", ""),
                    stage=data.get("stage", "inbox"),
                    personal_note=data.get("personal_note", ""),
                    example_usage=data.get("example_usage") or transcript.text,
                    context_type=data.get("context_type", "general"),
                    usage_examples=usage_ex,
                    grammar_note=grammar,
                )
                progress, _ = LearningProgress.objects.get_or_create(user=request.user)
                LearningProgress.objects.filter(pk=progress.pk).update(
                    total_words_noted=models.F("total_words_noted") + 1
                )
        except Exception as e:
            return error(f"Database error: {str(e)}", status=500)

        return success(
            {
                "id": note.id,
                "word": note.word,
                "pos": note.pos,
                "definition": note.definition,
                "created_at": note.created_at.isoformat(),
            },
            status=201,
        )


class WordNoteDetailView(LoginRequiredMixin, View):
    """
    Handle update (PATCH) and delete (DELETE) for specific WordNote.
    """

    def patch(self, request, note_id):
        note = get_object_or_404(WordNote, id=note_id, user=request.user)
        data = json_body(request)

        fields = ["definition", "pos", "personal_note", "context_type", "stage", "confidence", "mood", "emotion_vibe"]
        for field in fields:
            if field in data:
                setattr(note, field, data[field])

        note.save()
        return success(
            {
                "id": note.id,
                "word": note.word,
                "stage": note.stage,
                "confidence": note.confidence,
                "mood": note.mood,
                "status": "updated",
            }
        )

    def delete(self, request, note_id):
        note = get_object_or_404(WordNote, id=note_id, user=request.user)

        with transaction.atomic():
            note.delete()
            LearningProgress.objects.filter(user=request.user).update(
                total_words_noted=models.F("total_words_noted") - 1
            )

        return success({"deleted": True})


# Add this view to learning/views.py


class WordNotePageView(LoginRequiredMixin, View):
    """
    Renders the word notebook page.
    GET /learning/words/
    """

    def get(self, request):
        words = list(
            WordNote.objects.filter(user=request.user)
            .select_related(
                "quote",
                "quote__source",
                "quote__episode",
                "transcript",
                "transcript__source",
                "transcript__episode",
            )
            .order_by("-created_at")
        )

        for note in words:
            _enrich_note_with_video(note)

        return render(request, "learning/word_notebook.html", {"words": words})


# ─────────────────────────────────────────────
# REVIEW QUEUE VIEW
# ─────────────────────────────────────────────


class ReviewQueueView(LoginRequiredMixin, View):
    def get(self, request):
        now = timezone.now()
        queue = (
            QuoteMastery.objects.filter(user=request.user, next_review__lte=now)
            .select_related("quote", "quote__source", "quote__episode")
            .order_by("next_review")
        )

        data = []
        for mastery in queue:
            q = mastery.quote
            ep = q.episode

            video_url = None
            try:
                if ep and ep.video_file and ep.video_file.name:
                    video_url = ep.video_file.url
                elif q.source and q.source.video_file and q.source.video_file.name:
                    video_url = q.source.video_file.url
            except (ValueError, AttributeError):
                video_url = None

            data.append(
                {
                    "id": mastery.id,
                    "quote_id": q.id,
                    "text": q.text,
                    "video_url": video_url,
                    "start_time": float(q.start_time),
                    "end_time": float(q.end_time) if q.end_time else None,
                    "mastery_status": mastery.status,
                    "review_count": mastery.review_count,
                    "next_review": mastery.next_review.isoformat(),
                }
            )

        return success({"queue": data, "count": len(data)})


class DictionaryLookupView(View):
    def get(self, request):
        word = request.GET.get("word", "").strip()
        if not word:
            return error("Word required")
        result = get_micro_definition(word)
        return success(result)


@require_GET
def translate_word(request):
    word = request.GET.get("word", "").strip()
    context = request.GET.get("context", "").strip()
    source_id = request.GET.get("source_id", "")
    episode_id = request.GET.get("episode_id", "")

    if not word:
        return JsonResponse({"error": "no word"}, status=400)

    # Check curated translations first (SuggestedWord)
    clean = word.lower()
    sw_qs = SuggestedWord.objects.filter(word=clean)
    if episode_id:
        sw_qs = sw_qs.filter(episode_id=episode_id)
    elif source_id:
        sw_qs = sw_qs.filter(source_id=source_id)
    curated = sw_qs.first()

    if curated and curated.translation:
        return JsonResponse(
            {
                "word": word,
                "translation": curated.translation,
                "context_translation": None,
                "source": "curated",
            }
        )

    try:
        context_translation = None
        if context and word.lower() in context.lower():
            context_translation = _google_translate(context)

        word_translation = _google_translate(word)

        return JsonResponse(
            {
                "word": word,
                "translation": word_translation,
                "context_translation": context_translation,
                "source": "google",
            }
        )

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# ─────────────────────────────────────────────
# VOCABULARY ONBOARDING VIEWS
# ─────────────────────────────────────────────


class OnboardingWelcomeView(LoginRequiredMixin, View):
    """
    GET /learning/onboarding/
    Renders the onboarding SPA.  If the user already completed assessment,
    redirects straight to the word notebook.
    """

    def get(self, request):
        existing = OnboardingSession.objects.filter(user=request.user, completed_at__isnull=False).first()
        if existing:
            return redirect("clips:home")

        session, _ = OnboardingSession.objects.get_or_create(user=request.user)
        return render(request, "learning/onboarding.html", {"session_id": session.id})


class OnboardingDataView(LoginRequiredMixin, View):
    """
    GET /learning/onboarding/data/
    Returns the full vocab word pool as JSON so the client-side adaptive
    algorithm can build and re-order the assessment queue.
    """

    def get(self, request):
        session = get_object_or_404(OnboardingSession, user=request.user)
        words = list(
            VocabWord.objects.all().values(
                "id",
                "word",
                "pos",
                "tier",
                "uzbek_translation",
                "uzbek_synonyms",
                "example_sentence",
                "gif_url",
                "frequency_rank",
            )
        )
        return JsonResponse({"words": words, "session_id": session.id})


class OnboardingProgressView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/progress/
    Saves a single word result mid-session (fire-and-forget; duplicates ignored).
    Body: {session_id, word_id, known, response_time_ms}
    """

    def post(self, request):
        data = json_body(request)
        session = get_object_or_404(OnboardingSession, id=data.get("session_id"), user=request.user)
        word = get_object_or_404(VocabWord, id=data.get("word_id"))
        OnboardingResult.objects.get_or_create(
            session=session,
            word=word,
            defaults={
                "known": bool(data.get("known", False)),
                "response_time_ms": int(data.get("response_time_ms", 0)),
            },
        )
        return success({"ok": True})


class OnboardingCompleteView(LoginRequiredMixin, View):
    """
    POST /learning/onboarding/complete/
    Finalises the session: bulk-saves results, computes per-tier accuracy,
    projects vocabulary size, assigns level.
    Body: {session_id, results: [{word_id, known, response_time_ms}, ...]}
    """

    def post(self, request):
        data = json_body(request)
        session = get_object_or_404(OnboardingSession, id=data.get("session_id"), user=request.user)

        # Bulk-save results (skip duplicates)
        for r in data.get("results", []):
            OnboardingResult.objects.get_or_create(
                session=session,
                word_id=r.get("word_id"),
                defaults={
                    "known": bool(r.get("known", False)),
                    "response_time_ms": int(r.get("response_time_ms", 0)),
                },
            )

        # Compute per-tier accuracy
        tier_breakdown = {}
        for r in OnboardingResult.objects.filter(session=session).select_related("word"):
            t = str(r.word.tier)
            if t not in tier_breakdown:
                tier_breakdown[t] = {"shown": 0, "known": 0}
            tier_breakdown[t]["shown"] += 1
            if r.known:
                tier_breakdown[t]["known"] += 1

        # Project vocabulary size (each tier represents 200 words)
        projected_total = 0
        weak_tiers, strong_tiers = [], []
        for tier_str, counts in tier_breakdown.items():
            accuracy = counts["known"] / counts["shown"] if counts["shown"] else 0
            projected_total += round(accuracy * 200)
            t = int(tier_str)
            if accuracy < 0.50:
                weak_tiers.append(t)
            elif accuracy >= 0.75:
                strong_tiers.append(t)

        # Assign level
        if projected_total >= 800:
            level = "advanced"
        elif projected_total >= 600:
            level = "upper_intermediate"
        elif projected_total >= 400:
            level = "intermediate"
        else:
            level = "beginner"

        level_display = {
            "beginner": "Beginner",
            "intermediate": "Intermediate",
            "upper_intermediate": "Upper-Intermediate",
            "advanced": "Advanced",
        }

        total_shown = sum(c["shown"] for c in tier_breakdown.values())
        total_known = sum(c["known"] for c in tier_breakdown.values())

        session.words_shown = total_shown
        session.words_known = total_known
        session.projected_total = projected_total
        session.level = level
        session.tier_breakdown = tier_breakdown
        session.weak_tiers = sorted(weak_tiers)
        session.strong_tiers = sorted(strong_tiers)
        session.completed_at = timezone.now()
        session.save()

        return success(
            {
                "projected_total": projected_total,
                "level": level,
                "level_display": level_display.get(level, level),
                "tier_breakdown": tier_breakdown,
                "weak_tiers": sorted(weak_tiers),
                "strong_tiers": sorted(strong_tiers),
            }
        )


def _google_translate(text, source="en", target="uz"):
    from deep_translator import GoogleTranslator

    return GoogleTranslator(source=source, target=target).translate(text)


# ─────────────────────────────────────────────
# PROGRESS PAGE
# ─────────────────────────────────────────────


class ProgressView(LoginRequiredMixin, View):
    """GET /learning/progress/ — full stats dashboard."""

    def get(self, request):
        from datetime import date, timedelta

        from django.db.models import Avg, Count, Max, Sum
        from django.db.models.functions import TruncDate

        user = request.user

        # ── Word pipeline ──────────────────────────────────────────────────
        stage_qs = WordNote.objects.filter(user=user).values("stage").annotate(c=Count("id"))
        stages = {row["stage"]: row["c"] for row in stage_qs}
        inbox_count = stages.get("inbox", 0)
        learning_count = stages.get("learning", 0)
        mastered_count = stages.get("mastered", 0)
        total_words = inbox_count + learning_count + mastered_count

        # ── Streak & session minutes ───────────────────────────────────────
        streak = user.streak_days
        try:
            lp = LearningProgress.objects.get(user=user)
            session_minutes = lp.total_session_minutes
        except LearningProgress.DoesNotExist:
            session_minutes = 0

        # ── 7-day forecast ────────────────────────────────────────────────
        cutoff_week = date.today() - timedelta(days=7)
        recent_words = WordNote.objects.filter(user=user, created_at__date__gte=cutoff_week).count()
        daily_rate = recent_words / 7
        forecast_7 = round(daily_rate * 7)
        forecast_30 = round(daily_rate * 30)

        # ── Activity heatmap (last 84 days = 12 weeks) ────────────────────
        cutoff_heatmap = date.today() - timedelta(weeks=12)
        raw = (
            ReviewSession.objects.filter(user=user, started_at__date__gte=cutoff_heatmap)
            .annotate(day=TruncDate("started_at"))
            .values("day")
            .annotate(count=Count("id"))
        )
        heatmap = {str(row["day"]): row["count"] for row in raw}

        # Build 84-day list for template
        today = date.today()
        heatmap_days = []
        for i in range(83, -1, -1):
            d = today - timedelta(days=i)
            heatmap_days.append({"date": str(d), "count": heatmap.get(str(d), 0)})

        # ── Session stats (quiz + review) ─────────────────────────────────
        completed_sessions = ReviewSession.objects.filter(user=user, ended_at__isnull=False)

        # All-time bests
        session_agg = completed_sessions.aggregate(
            high_score=Max("score"),
            best_combo=Max("best_combo"),
            total_sessions=Count("id"),
        )
        high_score = session_agg["high_score"] or 0
        best_combo_ever = session_agg["best_combo"] or 0
        total_sessions = session_agg["total_sessions"] or 0

        # Rolling accuracy — last 10 completed sessions
        last_10 = completed_sessions.order_by("-ended_at")[:10]
        last_10_agg = last_10.aggregate(
            sum_total=Sum("quotes_reviewed"),
            sum_correct=Sum("correct_answers"),
        )
        rolling_total = last_10_agg["sum_total"] or 0
        rolling_correct = last_10_agg["sum_correct"] or 0
        rolling_accuracy = round((rolling_correct / rolling_total) * 100) if rolling_total else 0

        # Recent sessions for the list (last 10)
        recent_sessions = list(
            completed_sessions.order_by("-ended_at")[:10].values(
                "id",
                "session_type",
                "score",
                "best_combo",
                "quotes_reviewed",
                "correct_answers",
                "started_at",
                "ended_at",
            )
        )
        for s in recent_sessions:
            total = s["quotes_reviewed"] or 0
            correct = s["correct_answers"] or 0
            s["accuracy"] = round((correct / total) * 100) if total else 0
            delta = s["ended_at"] - s["started_at"]
            s["duration_minutes"] = round(delta.total_seconds() / 60, 1)

        # ── Onboarding level ──────────────────────────────────────────────
        level = level_display = ""
        session = OnboardingSession.objects.filter(user=user, completed_at__isnull=False).first()
        if session:
            level = session.level
            level_display = {
                "beginner": "Beginner",
                "intermediate": "Intermediate",
                "upper_intermediate": "Upper-Intermediate",
                "advanced": "Advanced",
            }.get(level, level)

        return render(
            request,
            "learning/progress.html",
            {
                "inbox_count": inbox_count,
                "learning_count": learning_count,
                "mastered_count": mastered_count,
                "total_words": total_words,
                "streak": streak,
                "session_minutes": session_minutes,
                "forecast_7": forecast_7,
                "forecast_30": forecast_30,
                "daily_rate": round(daily_rate, 1),
                "heatmap_days": heatmap_days,
                "level": level,
                "level_display": level_display,
                "high_score": high_score,
                "best_combo_ever": best_combo_ever,
                "total_sessions": total_sessions,
                "rolling_accuracy": rolling_accuracy,
                "recent_sessions": recent_sessions,
            },
        )


class PracticeHubView(LoginRequiredMixin, View):
    """GET /learning/practice/ — Practice hub launchpad."""

    def get(self, request):
        user = request.user

        # Word counts by stage
        user_words = WordNote.objects.filter(user=user)
        total_words = user_words.count()
        words_with_video = user_words.exclude(quote=None, transcript=None).count()

        # Stage breakdown for badges
        stage_counts = dict(user_words.values_list("stage").annotate(c=models.Count("id")).values_list("stage", "c"))
        mastered = stage_counts.get("mastered", 0)

        # Today's activity
        from datetime import date

        today = date.today()
        today_sessions = ReviewSession.objects.filter(
            user=user,
            ended_at__isnull=False,
            started_at__date=today,
        )
        today_count = today_sessions.count()
        today_best = today_sessions.order_by("-score").values_list("score", flat=True).first()

        return render(
            request,
            "learning/practice.html",
            {
                "total_words": total_words,
                "words_with_video": words_with_video,
                "mastered": mastered,
                "streak": user.streak_days,
                "today_count": today_count,
                "today_best": today_best or 0,
            },
        )


class ShadowingView(LoginRequiredMixin, View):
    """Redirect to My Words — shadowing is now integrated there."""

    def get(self, request):
        return redirect("learning:word_note_list")


def _get_practice_words(user, limit=20):
    """Fetch enriched WordNotes with video for practice modes."""
    notes = list(
        WordNote.objects.filter(user=user)
        .exclude(quote=None, transcript=None)
        .select_related(
            "quote",
            "quote__source",
            "quote__episode",
            "transcript",
            "transcript__source",
            "transcript__episode",
        )
        .order_by("confidence", "?")[:limit]
    )
    for note in notes:
        _enrich_note_with_video(note)
    return [n for n in notes if n.video_url]


class ReviewPageView(LoginRequiredMixin, View):
    """GET /learning/review/ — Cloze fill-in-the-blank review page."""

    def get(self, request):
        words = _get_practice_words(request.user)
        return render(request, "learning/review.html", {"words": words})


class SessionStartView(LoginRequiredMixin, View):
    """POST /learning/session/start/ — Create a new review/quiz session."""

    def post(self, request):
        data = json_body(request)
        session_type = data.get("session_type", "mixed")
        valid_types = ["cloze", "shadow", "review", "quiz", "mixed"]
        if session_type not in valid_types:
            return error("Invalid session_type")

        session = ReviewSession.objects.create(
            user=request.user,
            session_type=session_type,
        )
        return success({"session_id": session.id}, status=201)


class SessionEndView(LoginRequiredMixin, View):
    """POST /learning/session/end/ — Finalize a session with results."""

    def post(self, request):
        data = json_body(request)
        session_id = data.get("session_id")
        if not session_id:
            return error("session_id required")

        session = get_object_or_404(ReviewSession, id=session_id, user=request.user)
        if session.ended_at:
            return error("Session already ended")

        session.ended_at = timezone.now()
        session.quotes_reviewed = data.get("total", 0)
        session.correct_answers = data.get("correct", 0)
        session.score = data.get("score", 0)
        session.best_combo = data.get("best_combo", 0)
        session.save()

        # Update LearningProgress
        progress, _ = LearningProgress.objects.get_or_create(user=request.user)
        progress.total_cloze_attempts += session.quotes_reviewed
        progress.total_cloze_correct += session.correct_answers
        duration = session.duration_minutes
        progress.total_session_minutes += int(duration)
        progress.save()

        return success(
            {
                "session_id": session.id,
                "accuracy": session.accuracy,
                "duration_minutes": duration,
            }
        )


class QuizPageView(LoginRequiredMixin, View):
    """GET /learning/quiz/ — 5 today + 5 yesterday + 10 rotating past words."""

    def get(self, request):
        import hashlib
        import random as _rnd
        from datetime import timedelta

        today = timezone.now().date()
        yesterday = today - timedelta(days=1)

        base_qs = (
            WordNote.objects.filter(user=request.user)
            .exclude(translation="", definition="")
            .exclude(stage="mastered")
            .order_by("confidence", "id")
        )
        pool = list(base_qs)
        all_words = list(base_qs)

        def pick_daily(date, source, count):
            """Deterministic pick for a given date."""
            if len(source) <= count:
                return list(source)
            seed_str = f"{date.isoformat()}-{request.user.id}"
            seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
            rng = _rnd.Random(seed)
            return rng.sample(source, count)

        # 1. Today's 5 (synced with flashcard daily words)
        today_words = pick_daily(today, pool, 5)
        today_ids = {w.id for w in today_words}

        # 2. Yesterday's 5
        yesterday_pool = [w for w in pool if w.id not in today_ids]
        yesterday_words = pick_daily(yesterday, yesterday_pool, 5)
        yesterday_ids = {w.id for w in yesterday_words}

        # 3. 10 rotating past words (exclude today + yesterday, rotate daily)
        used_ids = today_ids | yesterday_ids
        past_pool = [w for w in pool if w.id not in used_ids]
        past_words = pick_daily(today, past_pool, 10) if past_pool else []

        quiz_words = today_words + yesterday_words + past_words

        # Shuffle deterministically so order varies daily
        seed_str = f"quiz-{today.isoformat()}-{request.user.id}"
        seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
        rng = _rnd.Random(seed)
        rng.shuffle(quiz_words)

        return render(
            request,
            "learning/quiz.html",
            {
                "words": quiz_words,
                "all_words": all_words,
            },
        )


class GrammarHubView(LoginRequiredMixin, View):
    """GET /learning/grammar/ — Grammar course hub with 8 units."""

    def get(self, request):
        return render(request, "learning/grammar.html")


class GrammarUnitView(LoginRequiredMixin, View):
    """GET /learning/grammar/<unit_id>/ — Single grammar unit lesson."""

    def get(self, request, unit_id):
        if unit_id < 1 or unit_id > 8:
            return redirect("learning:grammar")
        return render(request, "learning/grammar_unit.html", {"unit_id": unit_id})
