import os

from django.core.management.base import BaseCommand, CommandError

from clips.models import Episode, Source, SourceType
from clips.utils.subtitle_importer import import_quotes_from_srt


class Command(BaseCommand):
    help = "Universal importer for Movies and TV Show episodes"  # noqa: A003

    def add_arguments(self, parser):
        # Positional arguments (Required)
        parser.add_argument("source_id", type=int, help="ID of the Movie or TV Show")
        parser.add_argument("srt_path", type=str, help="Path to the SRT file")

        # Optional Flag (Required ONLY for TV Shows)
        parser.add_argument("--episode", type=int, help="Episode ID (Mandatory if Source is a TV Show)")

    def handle(self, *args, **options):
        s_id = options["source_id"]
        srt_path = options["srt_path"]
        e_id = options["episode"]

        # 1. Basic File Check
        if not os.path.exists(srt_path):
            raise CommandError(f"SRT file not found at: {srt_path}")

        # 2. Source Validation
        try:
            source = Source.objects.get(id=s_id)
        except Source.DoesNotExist:
            raise CommandError(f"Source with ID {s_id} does not exist.")

        # 3. The "Universal" Logic Bridge
        # If it's a TV Show, we MUST have an episode ID
        if source.source_type == SourceType.TV_SHOW:
            if not e_id:
                raise CommandError(
                    f"🛑 '{source.title}' is a TV Show. You must provide an episode ID.\n"
                    f"Usage: python manage.py process_subs {s_id} {srt_path} --episode <ID>"
                )
            # Verify the episode belongs to this show
            if not Episode.objects.filter(id=e_id, source=source).exists():
                raise CommandError(f"🛑 Episode {e_id} does not belong to show '{source.title}'.")

        # If it's a Movie and they provided an episode ID, warn them but continue
        elif source.source_type == SourceType.MOVIE and e_id:
            self.stdout.write(self.style.WARNING("⚠️ Warning: Ignoring episode ID because source is a Movie."))
            e_id = None

        # 4. Execution
        self.stdout.write(f"🚀 Processing: {source.title}" + (f" (EP ID: {e_id})" if e_id else ""))

        try:
            count = import_quotes_from_srt(source_id=s_id, srt_file_path=srt_path, episode_id=e_id)
            self.stdout.write(self.style.SUCCESS(f"✅ Success! Created {count} quotes."))
        except Exception as e:
            raise CommandError(f"💥 Failed to import: {str(e)}")
