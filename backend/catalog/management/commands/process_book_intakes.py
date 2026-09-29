import time

from django.core.management.base import BaseCommand

from catalog.intake_worker import run_once


class Command(BaseCommand):
    help = "处理图书照片识别任务（省钱 Batch / 实时），不自动确认入库"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=15)

    def handle(self, *args, **options):
        while True:
            counts = run_once()
            if options["once"] or any(counts.values()):
                self.stdout.write(
                    f"processed={counts['submitted_or_processed']} polled={counts['polled']}"
                )
            if options["once"]:
                return
            time.sleep(max(5, options["interval"]))
