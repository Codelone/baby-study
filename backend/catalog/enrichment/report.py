"""Produce a reproducible audit without exposing purchase data in public book APIs."""

from collections import Counter

from django.utils import timezone

from catalog.models import Book, BookSeries, CatalogImportRecord, ShelfItem

BOOK_REVIEW_FIELDS = (
    "original_title",
    "isbn",
    "authors",
    "illustrators",
    "translators",
    "publisher",
    "publication_date",
    "binding",
    "pages",
    "cover_url",
    "description",
    "themes",
    "reading_attributes",
)
SERIES_REVIEW_FIELDS = ("isbn", "cover_url", "publisher", "publication_date", "expected_count")


def describe(obj):
    is_book = isinstance(obj, Book)
    fields = BOOK_REVIEW_FIELDS if is_book else SERIES_REVIEW_FIELDS
    result = {
        "kind": "book" if is_book else "series",
        "id": obj.pk,
        "name": obj.title if is_book else obj.name,
        "identity_key": obj.identity_key,
        "cover_url": obj.cover_url,
        "missing_fields": [field for field in fields if not getattr(obj, field)],
        "evidence": list(
            obj.evidence.values(
                "field", "value", "source_url", "source_label", "status", "collected_at"
            )
        ),
    }
    if is_book:
        result["catalog_status"] = obj.catalog_status
        result["missing_reading_attributes"] = [
            field
            for field in ("point_reading", "publisher_age", "native_level", "lexile")
            if obj.reading_attributes.get(field, {}).get("status") != "confirmed"
        ]
    else:
        members = obj.memberships.filter(book__catalog_status="active").select_related("book")
        result.update(
            {
                "recorded_count": members.count(),
                "expected_count": obj.expected_count,
                "completeness": obj.completeness,
                "members": [describe(member.book) for member in members],
            }
        )
    return result


def build_report(records, outcomes):
    outcomes = {item["row"]: item for item in outcomes}
    rows = []
    for record in records:
        imported = CatalogImportRecord.objects.filter(
            source_file=record["source_file"], sheet=record["sheet"], row=record["row"]
        ).first()
        objects = []
        if imported:
            objects = [describe(obj) for obj in imported.series.all()]
            member_ids = {b["id"] for obj in objects for b in obj["members"]}
            objects += [describe(obj) for obj in imported.books.exclude(pk__in=member_ids)]
        issues = list(dict.fromkeys(record["issues"] + (imported.issues if imported else [])))
        outcome = outcomes.get(record["row"], {})
        if outcome.get("reason"):
            issues.append(outcome["reason"])
        rows.append(
            {
                "row": record["row"],
                "sheet": record["sheet"],
                "title": record["raw"].get("书本名称", ""),
                "status": outcome.get("status", record["status"]),
                "objects": objects,
                "issues": issues,
                "candidates": [
                    {
                        k: candidate[k]
                        for k in ("url", "title", "status", "reason", "collected_at")
                        if k in candidate
                    }
                    for candidate in record["candidates"]
                ],
            }
        )
    return {
        "generated_at": timezone.now().isoformat(),
        "summary": {
            "rows": len(rows),
            "row_statuses": dict(Counter(r["status"] for r in rows)),
            "active_books": Book.objects.filter(catalog_status="active").count(),
            "legacy_books": Book.objects.filter(catalog_status="legacy").count(),
            "series": BookSeries.objects.count(),
            "book_cover_urls": Book.objects.filter(catalog_status="active")
            .exclude(cover_url="")
            .count(),
            "series_cover_urls": BookSeries.objects.exclude(cover_url="").count(),
            "shelf_records": ShelfItem.objects.count(),
        },
        "records": rows,
    }


def markdown(report):
    def safe(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    summary = report["summary"]
    lines = [
        "# 全表图书资料核对报告",
        "",
        f"报告生成时间：{report['generated_at']}。原表保留，覆盖全部 {summary['rows']} 行。",
        "",
        f"处理 {summary['rows']} 行；状态 {summary['row_statuses']}。当前目录有 "
        f"{summary['active_books']} 条活动单本、{summary['legacy_books']} 条历史映射、"
        f"{summary['series']} 个系列。单本封面 URL {summary['book_cover_urls']} 条，"
        f"系列封面 URL {summary['series_cover_urls']} 条。"
        "封面 URL 已匹配来源，在线可用性可能变化。",
        "",
        "可靠项先导入，候选不进入公共书目。‘已导入’不代表每个字段已齐全；"
        "未知版本属性和蓝思保留为空。原表年龄、分类、价格和个人备注只在私有导入记录中。",
        "",
        "`super hammy 1/2` 的现有 ID 和系列保留，具体小蝌蚪套装仍待核实。"
        "My First Book of Words 原误建套装已保留为历史映射，未传播拥有或阅读状态。",
        "",
        "- [结构化核对报告](catalog-review.json)",
        "- [可复用来源映射](catalog-sources.json)",
        "- [完整采集结果及原始记录](catalog-enriched.json)（包含私人购买资料，请勿公开发布）",
        "",
        "重新生成：`python backend/manage.py report_catalog`。",
        "",
        "| Excel 行 | 原始书名 | 处理结果 | 对象与成员覆盖 |",
        "| --- | --- | --- | --- |",
    ]
    labels = {"imported": "已导入可靠项", "pending": "待确认", "rejected": "导入拒绝"}
    for row in report["records"]:
        coverage = []
        for obj in row["objects"]:
            if obj["kind"] == "series":
                expected = obj["expected_count"] if obj["expected_count"] is not None else "?"
                coverage.append(f"系列#{obj['id']} {obj['recorded_count']}/{expected}本")
            else:
                coverage.append(
                    f"单本#{obj['id']}"
                    + ("（历史映射）" if obj["catalog_status"] == "legacy" else "")
                )
        lines.append(
            f"| {row['row']} | {safe(row['title'])} | "
            f"{labels.get(row['status'], row['status'])} | "
            f"{'；'.join(coverage) or '无可靠版本入库'} |"
        )
    for row in report["records"]:
        lines += ["", f"## 行 {row['row']}：{safe(row['title'])}", ""]
        for obj in row["objects"]:
            lines.append(
                f"- {obj['kind']} #{obj['id']}：{safe(obj['name'])}。"
                f"缺失/不适用字段：{', '.join(obj['missing_fields']) or '无'}。"
            )
            sources = {}
            for evidence in obj["evidence"]:
                sources.setdefault(evidence["source_url"], []).append(evidence["field"])
            for url, fields in sources.items():
                lines.append(f"  - 已核实字段 {', '.join(dict.fromkeys(fields))}：[来源]({url})。")
            if obj["cover_url"]:
                lines.append(f"  - [已匹配封面 URL]({obj['cover_url']})。")
            if obj["kind"] == "book":
                lines.append(
                    "  - 未核实/不适用阅读声明："
                    + ", ".join(obj["missing_reading_attributes"])
                    + "。"
                )
            if obj["kind"] == "series":
                members = obj["members"]
                missing = Counter(field for b in members for field in b["missing_fields"])
                expected = obj["expected_count"] if obj["expected_count"] is not None else "?"
                covers = sum(bool(b["cover_url"]) for b in members)
                lines.append(
                    f"  - 成员 {len(members)}/{expected} 本，完整性 {obj['completeness']}；"
                    f"有封面 {covers} 本。"
                )
                lines.append(
                    "  - 成员："
                    + (
                        "；".join(f"#{b['id']} {safe(b['name'])}" for b in members)
                        or "官方页面仅列整包音频/套装，未核实实际分册，不生成单本"
                    )
                    + "。"
                )
                lines.append(
                    f"  - 分册缺失/不适用字段数量：{dict(missing)}；"
                    "各册字段证据见结构化报告与详情 API。"
                )
        for issue in row["issues"]:
            lines.append(f"- 核对说明：{safe(issue)}")
        for candidate in row["candidates"]:
            url = candidate.get("url")
            if url:
                label = safe(candidate.get("title") or "检索/候选来源")[:150]
                lines.append(
                    f"- [{label}]({url})：{candidate.get('status', 'candidate')}"
                    + (f"，原因：{safe(candidate['reason'])}" if candidate.get("reason") else "")
                    + "。"
                )
    return "\n".join(lines) + "\n"
