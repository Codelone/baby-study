"""Conservative, deterministic grouping from visible edition evidence only."""

import hashlib
import json
import re
import unicodedata
from itertools import combinations

from .recognition import (
    LIST_FIELDS,
    UNUSABLE_PHOTO_TYPES,
    empty_fields,
    issue,
    validate_extraction,
)

VERSION_FIELDS = (
    "title",
    "isbn",
    "authors",
    "publisher",
    "edition",
    "publication_date",
    "language",
    "binding",
    "pages",
)
LABELS = {
    "cover": "封面识别",
    "back_cover": "封底识别",
    "copyright_page": "版权页识别",
    "title_page": "扉页识别",
    "interior": "内页识别",
}


def _normalize(value):
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    # Preserve numbers/subtitles: series volume and edition distinctions are identity.
    return " ".join(re.findall(r"[^\W_]+", text, flags=re.UNICODE))


def _present(value):
    return value not in (None, "", [], "unknown")


def _values(value):
    return {_normalize(item) for item in value} if isinstance(value, list) else {_normalize(value)}


def _same(left, right):
    a, b = _values(left), _values(right)
    return a <= b or b <= a


def _conflicts(left, right):
    return [
        key
        for key in VERSION_FIELDS
        if _present(left["fields"][key])
        and _present(right["fields"][key])
        and not _same(left["fields"][key], right["fields"][key])
    ]


def _in_text(value, text):
    """Match whole visible names, not a short fragment or an approximate series title."""
    value, text = _normalize(value), _normalize(text)
    if not value or len(value.replace(" ", "")) < 3:
        return False
    if re.search(r"[\u3400-\u9fff]", value):
        return value in text
    return f" {value} " in f" {text} "


def _distinctive_title(title):
    normalized = _normalize(title)
    if re.search(r"[\u3400-\u9fff]", normalized):
        return len(normalized.replace(" ", "")) >= 4
    return len(normalized.replace(" ", "")) >= 8


def _title_match(left, right):
    a, b = left["fields"]["title"], right["fields"]["title"]
    if a and b:
        return _normalize(a) == _normalize(b)
    title, other = (a, right) if a else (b, left)
    return bool(title and _distinctive_title(title) and _in_text(title, other["identity_text"]))


def _support_match(left, right):
    for key in ("authors", "publisher"):
        a, b = left["fields"][key], right["fields"][key]
        if _present(a) and _present(b) and _same(a, b):
            return True
        for value, other in ((a, right), (b, left)):
            names = value if isinstance(value, list) else [value]
            if any(_in_text(name, other["identity_text"]) for name in names if name):
                return True
    return False


def _match(left, right):
    if _conflicts(left, right):
        return False
    a, b = left["fields"]["isbn"], right["fields"]["isbn"]
    if a and b:
        return a == b
    return _title_match(left, right) and _support_match(left, right)


def _compatible(items):
    return all(not _conflicts(left, right) for left, right in combinations(items, 2))


def _components(items):
    """Connected text matches are usable only after checking the entire component."""
    remaining = {item["id"]: item for item in items}
    result = []
    while remaining:
        component = [remaining.pop(min(remaining))]
        queue = list(component)
        while queue:
            current = queue.pop()
            for key in sorted(remaining):
                if _match(current, remaining[key]):
                    item = remaining.pop(key)
                    component.append(item)
                    queue.append(item)
        result.append(component)
    return result


def _deduplicate_issues(items):
    seen = {}
    for item in items:
        seen.setdefault(json.dumps(item, ensure_ascii=False, sort_keys=True), item)
    return [seen[key] for key in sorted(seen)]


def _aggregate(items, extra_issues):
    items = sorted(items, key=lambda item: item["id"])
    fields, evidence, issues = empty_fields(), [], list(extra_issues)
    for photo in items:
        extraction = photo["extraction"]
        for note in extraction["issues"]:
            issues.append({**note, "photo_id": photo["id"]})
        for key, value in extraction["fields"].items():
            if not _present(value):
                continue
            evidence.append(
                {
                    "field": key,
                    "value": value,
                    "kind": "photo",
                    "photo_id": photo["id"],
                    "source_label": LABELS.get(extraction["photo_type"], "照片识别"),
                    "model_name": extraction["model_name"],
                }
            )
    for key in fields:
        claims = [item["value"] for item in evidence if item["field"] == key]
        if not claims:
            continue
        if all(_same(a, b) for a, b in combinations(claims, 2)):
            # Choose the most complete compatible list, while retaining every original claim.
            fields[key] = max(claims, key=len) if key in LIST_FIELDS else claims[0]
        else:
            issues.append(issue("field_conflict", "照片中的字段互相冲突，请人工核对", field=key))
    if not fields["title"]:
        issues.append(issue("missing_title", "缺少可见书名，请补拍或手工填写", field="title"))
    photo_ids = [item["id"] for item in items]
    fingerprint = json.dumps(photo_ids, ensure_ascii=False, separators=(",", ":"))
    group_id = "group-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
    issues = _deduplicate_issues(issues)
    unsafe = {"identity_conflict", "ambiguous_match", "field_conflict", "missing_title"}
    return {
        "id": group_id,
        "photo_ids": photo_ids,
        "fields": fields,
        "evidence": evidence,
        "issues": issues,
        "existing_book_id": None,
        "selected": not any(note["code"] in unsafe for note in issues),
    }


def group_photos(photos):
    """Return drafts and unassigned ids; no model call, ORM lookup, or upload-order inference.

    ISBN anchors are formed first. A text-only photo joins an anchor only when exactly
    one whole group is compatible. Conflicting text bridges cannot combine editions;
    uncertain photos stay unassigned and their extraction remains on the intake photo.
    Group ids depend only on their photo-id set, so result/upload order does not affect them.
    """
    if not isinstance(photos, list):
        raise ValueError("照片必须是列表")
    by_id = {}
    for photo in photos:
        if not isinstance(photo, dict) or not isinstance(photo.get("id"), str) or not photo["id"]:
            raise ValueError("每张照片必须有非空字符串 id")
        extraction = validate_extraction(photo.get("extraction"))
        item = {"id": photo["id"], "extraction": extraction, **extraction}
        if item["id"] in by_id and by_id[item["id"]] != item:
            raise ValueError("同一照片 id 不能有不同提取结果")
        by_id[item["id"]] = item
    unassigned, anchors, no_isbn, group_notes = set(), [], [], {}
    isbn_buckets = {}
    for item in sorted(by_id.values(), key=lambda item: item["id"]):
        if item["photo_type"] in UNUSABLE_PHOTO_TYPES:
            unassigned.add(item["id"])
        elif item["fields"]["isbn"]:
            isbn_buckets.setdefault(item["fields"]["isbn"], []).append(item)
        else:
            no_isbn.append(item)
    for isbn in sorted(isbn_buckets):
        items = isbn_buckets[isbn]
        if _compatible(items):
            anchors.append(items)
        else:
            for item in items:
                anchors.append([item])
                group_notes[item["id"]] = [
                    issue("identity_conflict", "同 ISBN 照片存在书名或版本冲突，未自动合并")
                ]

    proposed, remaining = {}, []
    for item in no_isbn:
        candidates = [
            index
            for index, group in enumerate(anchors)
            if any(_match(item, member) for member in group) and _compatible([*group, item])
        ]
        if len(candidates) == 1:
            proposed.setdefault(candidates[0], []).append(item)
        elif len(candidates) > 1:
            unassigned.add(item["id"])
        else:
            remaining.append(item)
    for index, additions in proposed.items():
        if _compatible([*anchors[index], *additions]):
            anchors[index].extend(additions)
        else:
            # Simultaneous weak attachments must not create an order-dependent winner.
            unassigned.update(item["id"] for item in additions)

    for component in _components(remaining):
        if _compatible(component):
            if any(item["fields"]["title"] for item in component):
                anchors.append(component)
            else:
                unassigned.update(item["id"] for item in component)
        else:
            # A bridge without edition evidence may match both incompatible versions.
            for item in component:
                if item["fields"]["title"]:
                    anchors.append([item])
                    group_notes[item["id"]] = [
                        issue("ambiguous_match", "可见文字关联存在版本冲突，请核对照片归属")
                    ]
                else:
                    unassigned.add(item["id"])
    # Title alone is useful for a draft, but cannot justify joining two photographs.
    for left, right in combinations(anchors, 2):
        if any(_title_match(a, b) for a in left for b in right):
            if not any(_conflicts(a, b) for a in left for b in right):
                for group in (left, right):
                    group_notes.setdefault(group[0]["id"], []).append(
                        issue("ambiguous_match", "相同书名缺少足够版本证据，保留独立草稿")
                    )
    groups = [
        _aggregate(group, [note for item in group for note in group_notes.get(item["id"], [])])
        for group in anchors
    ]
    return {
        "groups": sorted(groups, key=lambda group: tuple(group["photo_ids"])),
        "unassigned_photo_ids": sorted(unassigned),
    }
