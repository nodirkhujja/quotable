"""
Export vocabulary to a CSV file for editing in Excel / Google Sheets.

Usage:
    poetry run python -m core.manage export_vocab_csv
    poetry run python -m core.manage export_vocab_csv --output media/translation/vocab.csv
    poetry run python -m core.manage export_vocab_csv --empty-only   # only rows missing translation
    poetry run python -m core.manage export_vocab_csv --source friends

After editing in Excel, import back:
    poetry run python -m core.manage import_vocab_csv --file media/translation/vocab.csv
"""

import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from vocab.models import WordTranslation


class Command(BaseCommand):
    help = "Export vocabulary to CSV for Excel editing"

    def add_arguments(self, parser):
        parser.add_argument("--output", default="media/translation/vocab_export.csv")
        parser.add_argument("--empty-only", action="store_true", help="Export only rows with missing translation")
        parser.add_argument("--min-freq", type=int, default=0, help="Only export words with frequency >= this value")

    def handle(self, **opts):
        out_path = Path(opts["output"])
        out_path.parent.mkdir(parents=True, exist_ok=True)

        qs = WordTranslation.objects.all().order_by("-frequency", "word")

        if opts["empty_only"]:
            qs = qs.filter(translation="")

        if opts["min_freq"]:
            qs = qs.filter(frequency__gte=opts["min_freq"])

        rows = list(qs)
        self.stdout.write(f"Exporting {len(rows)} words → {out_path}")

        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            # utf-8-sig = Excel opens with correct encoding automatically
            writer = csv.writer(f)
            writer.writerow(["word", "uzbek_translation", "level", "frequency"])
            for w in rows:
                writer.writerow(
                    [
                        w.word,
                        w.translation or "",
                        w.level or "",
                        w.frequency or 0,
                    ]
                )

        empty = sum(1 for w in rows if not w.translation)
        filled = len(rows) - empty
        self.stdout.write(self.style.SUCCESS(f"Done. {filled} have translation, {empty} are empty."))
        self.stdout.write(f"Open in Excel: {out_path.resolve()}")
