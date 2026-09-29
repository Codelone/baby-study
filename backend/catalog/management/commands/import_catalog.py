import json

from django.core.management.base import BaseCommand, CommandError

from catalog.enrichment.collector import write_json
from catalog.enrichment.importer import import_records


class Command(BaseCommand):
    help = "逐条验证经核对的采集结果；默认 dry-run，--apply 才写入"

    def add_arguments(self, parser):
        parser.add_argument("input", nargs="?", default="imports/catalog-enriched.json")
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--report", default="imports/catalog-import-result.json")

    def handle(self, *args, **options):
        try:
            with open(options["input"]) as file:
                data = json.load(file)
            if data.get("schema_version") != 1:
                raise ValueError("不支持的采集结果版本")
            results = import_records(data["records"], apply=options["apply"])
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
        write_json(
            options["report"],
            {"mode": "apply" if options["apply"] else "dry-run", "records": results},
        )
        rejected = [r for r in results if r["status"] == "rejected"]
        for item in results:
            self.stdout.write(f"行 {item['row']}：{item['status']} {item.get('reason', '')}")
        self.stdout.write(f"共 {len(results)} 行，拒绝 {len(rejected)} 行；书架未自动修改")
        if rejected:
            raise CommandError("存在拒绝项，详情见导入结果报告；其他行仍按逐项事务处理")
