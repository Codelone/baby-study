import json
from pathlib import Path

from django.core.management.base import BaseCommand

from catalog.enrichment.collector import write_json
from catalog.enrichment.report import build_report, markdown


class Command(BaseCommand):
    help = "结合全表采集、导入结果与当前证据生成逐行核对报告"

    def add_arguments(self, parser):
        parser.add_argument("--input", default="imports/catalog-enriched.json")
        parser.add_argument("--outcomes", default="imports/catalog-import-result.json")
        parser.add_argument("--output", default="imports/catalog-review.md")

    def handle(self, *args, **options):
        records = json.loads(Path(options["input"]).read_text())["records"]
        outcomes = json.loads(Path(options["outcomes"]).read_text())["records"]
        report = build_report(records, outcomes)
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        write_json(output.with_suffix(".json"), report)
        output.write_text(markdown(report))
        self.stdout.write(str(report["summary"]))
