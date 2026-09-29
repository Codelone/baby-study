from django.core.management.base import BaseCommand, CommandError

from catalog.enrichment.collector import collect


class Command(BaseCommand):
    help = "从原表及经核对的公开来源映射采集书目；缓存、续跑、--refresh 刷新"

    def add_arguments(self, parser):
        parser.add_argument("workbook")
        parser.add_argument("--mapping", default="imports/catalog-sources.json")
        parser.add_argument("--output", default="imports/catalog-enriched.json")
        parser.add_argument("--cache", default="imports/.cache")
        parser.add_argument("--refresh", action="store_true")

    def handle(self, *args, **options):
        try:
            results = collect(
                options["workbook"],
                options["mapping"],
                options["output"],
                options["cache"],
                options["refresh"],
                self.stdout.write,
            )
        except (OSError, ValueError, KeyError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"共 {len(results)} 行，可靠对象行 {sum(bool(r['entities']) for r in results)}"
        )
