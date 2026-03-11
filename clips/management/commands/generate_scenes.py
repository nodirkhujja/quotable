import os
from decimal import Decimal

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from clips.models import SceneBlock, Source, Transcript, generate_thumbnail


class Command(BaseCommand):
    help = "Group transcript lines into scene blocks and extract thumbnails"

    def add_arguments(self, parser):
        parser.add_argument("--source", type=int, required=True)
        parser.add_argument(
            "--interval",
            type=int,
            default=30,
            help="Block duration in seconds (default: 30)",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Delete existing scene blocks first",
        )
        parser.add_argument(
            "--episode",
            type=int,
            default=None,
            help="Only process this episode ID (TV shows only)",
        )

    def handle(self, *args, **options):
        source_id = options["source"]
        interval = options["interval"]
        replace = options["replace"]

        try:
            source = Source.objects.get(id=source_id)
        except Source.DoesNotExist:
            raise CommandError(f"Source {source_id} not found")

        self.stdout.write(f"Processing: {source}")

        # Build list of (episode_or_None, video_path) targets
        targets = []
        if source.source_type == "tv_show":
            episodes = source.episodes.exclude(video_file="").order_by("season", "episode_number")
            if options["episode"]:
                episodes = episodes.filter(id=options["episode"])
                if not episodes.exists():
                    raise CommandError(f"Episode {options['episode']} not found")
            for ep in episodes:
                targets.append((ep, ep.video_file.path))
        else:
            if not source.video_file:
                raise CommandError("Source has no video file")
            targets.append((None, source.video_file.path))

        total_blocks = 0
        for episode, video_path in targets:
            label = str(episode) if episode else source.title

            if replace:
                if episode:
                    SceneBlock.objects.filter(episode=episode).delete()
                else:
                    SceneBlock.objects.filter(source=source, episode=None).delete()
                self.stdout.write(f"  Deleted existing blocks for {label}")

            # Fetch transcript lines for this target
            if episode:
                lines = list(
                    Transcript.objects.filter(episode=episode).order_by("start_time").values("start_time", "end_time")
                )
            else:
                lines = list(
                    Transcript.objects.filter(source=source, episode=None)
                    .order_by("start_time")
                    .values("start_time", "end_time")
                )

            if not lines:
                self.stdout.write(self.style.WARNING(f"  No transcript lines for {label}, skipping"))
                continue

            blocks = self._bucket_lines(lines, interval)
            self.stdout.write(f"  {label}: {len(lines)} lines -> {len(blocks)} blocks")

            with transaction.atomic():
                for block_start, block_end in blocks:
                    mid = float(block_start + block_end) / 2.0
                    block = SceneBlock(
                        source=source,
                        episode=episode,
                        start_time=block_start,
                        end_time=block_end,
                    )
                    block.save()

                    thumb_path = f"/tmp/scene_{block.id}.jpg"
                    try:
                        generate_thumbnail(video_path, mid, thumb_path)
                        with open(thumb_path, "rb") as f:
                            block.thumbnail.save(f"scene_{block.id}.jpg", File(f), save=True)
                        self.stdout.write(f"    Block {float(block_start):.0f}s-{float(block_end):.0f}s: OK")
                    except Exception as e:
                        self.stdout.write(self.style.WARNING(f"    Thumbnail failed: {e}"))
                    finally:
                        if os.path.exists(thumb_path):
                            os.unlink(thumb_path)

                    total_blocks += 1

        self.stdout.write(self.style.SUCCESS(f"Done. {total_blocks} scene blocks created."))

    def _bucket_lines(self, lines, interval):
        """Bucket transcript lines into interval-width time windows."""
        if not lines:
            return []

        populated = set()
        for line in lines:
            bucket = int(line["start_time"]) // interval
            populated.add(bucket)

        result = []
        for bucket in sorted(populated):
            b_start = Decimal(str(bucket * interval))
            b_end = Decimal(str((bucket + 1) * interval))
            result.append((b_start, b_end))

        return result
