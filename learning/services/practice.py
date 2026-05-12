"""Practice service helpers — enriched word sets for drills."""

from __future__ import annotations

from learning.services.notebook import enrich_note_with_video
from vocab.models import WordNote


def get_practice_words(user, limit: int = 20) -> list[WordNote]:
    """Fetch enriched WordNotes that have a video clip, ordered easy→hard.

    Excludes notes with no quote or transcript (no scene to play).
    Only returns notes where enrichment produced a playable video_url.
    """
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
        enrich_note_with_video(note)
    return [n for n in notes if n.video_url]
