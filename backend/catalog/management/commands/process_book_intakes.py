import time

from django.core.management.base import BaseCommand

from catalog.intake_worker import run_once
from config.observability import event


class Command(BaseCommand):
    help = "处理图书照片识别任务（省钱 Batch / 实时），不自动确认入库"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=5)
        parser.add_argument("--batch-interval", type=int, default=60)

    def handle(self, *args, **options):
        next_poll = 0
        while True:
            poll_batches = time.monotonic() >= next_poll
            counts = run_once(poll_batches=poll_batches)
            if poll_batches:
                next_poll = time.monotonic() + max(5, options["batch_interval"])
            event(
                "worker_cycle", processed=counts["submitted_or_processed"], polls=counts["polled"]
            )
            if options["once"]:
                return
            time.sleep(max(5, options["interval"]))
