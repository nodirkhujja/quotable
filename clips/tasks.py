import os
import tempfile
from decimal import Decimal

import structlog
from celery import shared_task
from django.core.files import File

log = structlog.get_logger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name="clips.generate_scene_blocks")
def generate_scene_blocks_task(self, episode_id: int, interval: int = 30) -> None:
    from clips.models import Episode, SceneBlock, Transcript, generate_thumbnail

    try:
        episode = Episode.objects.select_related("source").get(pk=episode_id)
    except Episode.DoesNotExist:
        log.warning("generate_scene_blocks: episode not found", episode_id=episode_id)
        return

    if not episode.video_file:
        log.warning("generate_scene_blocks: no video file", episode_id=episode_id)
        return

    video_path = episode.video_file.path
    lines = list(Transcript.objects.filter(episode=episode).order_by("start_time").values("start_time"))
    if not lines:
        log.info("generate_scene_blocks: no transcript lines yet", episode_id=episode_id)
        return

    SceneBlock.objects.filter(episode=episode).delete()

    buckets = sorted({int(line["start_time"]) // interval for line in lines})
    log.info("generate_scene_blocks: building blocks", episode_id=episode_id, bucket_count=len(buckets))

    for bucket in buckets:
        b_start = Decimal(str(bucket * interval))
        b_end = Decimal(str((bucket + 1) * interval))
        mid = float(b_start + b_end) / 2.0

        block = SceneBlock.objects.create(
            source=episode.source,
            episode=episode,
            start_time=b_start,
            end_time=b_end,
        )

        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.close()
        try:
            generate_thumbnail(video_path, mid, tmp.name)
            with open(tmp.name, "rb") as f:
                block.thumbnail.save(f"scene_{block.id}.jpg", File(f), save=True)
        except Exception as exc:
            log.warning(
                "generate_scene_blocks: thumbnail failed",
                episode_id=episode_id,
                block_id=block.id,
                error=str(exc),
            )
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    log.info("generate_scene_blocks: done", episode_id=episode_id, blocks=len(buckets))
