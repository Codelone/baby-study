"""Public edition lookup; returned claims require a user's explicit selection.

Open Library's search endpoint returns works. Only the matching edition keys in
``editions.docs`` are followed, and all bibliographic claims come from that edition
(or its explicit author references), never from the surrounding work. API docs:
https://openlibrary.org/dev/docs/api/search
https://openlibrary.org/dev/docs/api/books

Like the offline collector, this uses timeouts, bounded retries/response sizes and
keeps covers as URLs. The collector is deliberately not used as a synchronous API.
"""

import copy
import json
import re
import threading
import time
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urlparse

import requests
from django.conf import settings

from catalog.enrichment.sources import SourceError

BASE_URL = "https://openlibrary.org"
MAX_CANDIDATES = 4
MAX_AUTHORS = 3
MAX_REQUESTS = 12
MAX_SECONDS = 30
MAX_RESPONSE_BYTES = 1_000_000
MAX_RETRIES = 1
MAX_RETRY_WAIT = 3
CACHE_SECONDS = 300
CACHE_ENTRIES = 128
EDITION_KEY = re.compile(r"/books/OL[0-9]+M\Z")
AUTHOR_KEY = re.compile(r"/authors/OL[0-9]+A\Z")
SAFE_PATH = re.compile(r"/(?:search|isbn/[0-9]{13}|books/OL[0-9]+M|authors/OL[0-9]+A)\.json\Z")
SET_MARKER = re.compile(
    r"boxed?\s+set|box\s+set|\b[2-9][0-9]*[- ](?:book|volume)\b"
    r"|套装|全套|全[二三四五六七八九十\d]+册",
    re.IGNORECASE,
)
REVIEW_ISSUE = "公开书目候选尚未经用户核对；请确认具体版本及单本/套装，书名相似不代表同一本。"
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_LAST_REQUEST = None


def _text(value, maximum=500):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value if len(value) <= maximum else ""


def _list(value):
    return value if isinstance(value, list) else []


def _isbn(value):
    """Same ISBN checks as the reviewed importer, without importing ORM code."""
    value = re.sub(r"[\s-]", "", _text(value, 32)).upper()
    if re.fullmatch(r"[0-9]{9}[0-9X]", value):
        digits = [int(v) for v in value[:9]] + [10 if value[-1] == "X" else int(value[-1])]
        if sum((10 - index) * digit for index, digit in enumerate(digits)) % 11:
            raise ValueError("ISBN-10 校验位错误")
        value = "978" + value[:9]
        value += str((-sum(int(n) * (1 if i % 2 == 0 else 3) for i, n in enumerate(value))) % 10)
    if not re.fullmatch(r"97[89][0-9]{10}", value):
        raise ValueError("ISBN 格式错误")
    if sum(int(n) * (1 if i % 2 == 0 else 3) for i, n in enumerate(value)) % 10:
        raise ValueError("ISBN-13 校验位错误")
    return value


def _canonical(value):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _quote(value):
    # Input stays a literal Solr phrase, including URL-like and query-like text.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _safe_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "openlibrary.org"
        or parsed.fragment
        or not SAFE_PATH.fullmatch(parsed.path)
    ):
        raise SourceError("来源跳转不在允许的公开书目接口内，已停止访问。")


def _throttle(deadline):
    """At most one request/second across lookup threads in this process."""
    global _LAST_REQUEST
    with _LOCK:
        now = time.monotonic()
        wait = max(0, 1 - (now - _LAST_REQUEST)) if _LAST_REQUEST is not None else 0
        if now + wait >= deadline:
            raise SourceError("公开检索超过时间限制，请稍后重试或手工补充。")
        if wait:
            time.sleep(wait)
        _LAST_REQUEST = time.monotonic()


def _retry_wait(value, attempt):
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            seconds = max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            seconds = 2**attempt
    if not 0 <= seconds <= MAX_RETRY_WAIT:
        raise SourceError("公开来源限流或暂不可用，请稍后重试。")
    return seconds


class _PublicClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        proxy = getattr(settings, "INTAKE_ENRICHMENT_PROXY_URL", "")
        if proxy:
            parsed = urlparse(proxy)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                self.session.close()
                raise SourceError("公开书目检索代理配置无效，请检查服务端配置。")
            self.session.proxies = {"https": proxy}
        self.session.headers.update(
            {
                "User-Agent": "BabyStudyCatalog/1.0 (public bibliographic lookup; no media)",
                "Accept": "application/json",
            }
        )
        self.deadline = time.monotonic() + MAX_SECONDS
        self.requests = 0

    def close(self):
        self.session.close()

    def get_json(self, path, params=None):
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params)
        _safe_url(url)
        cache_key = url
        with _LOCK:
            cached = _CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
                _CACHE.move_to_end(cache_key)
                return copy.deepcopy(cached[1])
        redirects = 0
        for attempt in range(MAX_RETRIES + 1):
            while True:
                remaining = self.deadline - time.monotonic()
                if self.requests >= MAX_REQUESTS or remaining <= 0:
                    raise SourceError("公开检索达到请求/时间限制，请核对已有候选或稍后重试。")
                _throttle(self.deadline)
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceError("公开检索超过时间限制，请稍后重试。")
                self.requests += 1
                try:
                    with self.session.get(
                        url,
                        timeout=(min(3, remaining), min(5, remaining)),
                        allow_redirects=False,
                        stream=True,
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("Location", "")
                            target = BASE_URL + location if location.startswith("/") else location
                            _safe_url(target)
                            redirects += 1
                            if redirects > 2:
                                raise SourceError("公开来源跳转次数过多，已停止访问。")
                            url = target
                            continue
                        if response.status_code == 404:
                            raise SourceError("公开来源未收录该 ISBN/版本。")
                        if response.status_code in (429, 500, 502, 503, 504):
                            if attempt == MAX_RETRIES:
                                raise SourceError("公开来源限流或暂不可用，请稍后重试。")
                            wait = _retry_wait(response.headers.get("Retry-After", ""), attempt)
                            if time.monotonic() + wait >= self.deadline:
                                raise SourceError("公开检索超过时间限制，请稍后重试。")
                            time.sleep(wait)
                            break
                        if response.status_code != 200:
                            raise SourceError("公开来源拒绝检索，请稍后重试或手工补充。")
                        content = bytearray()
                        for chunk in response.iter_content(chunk_size=8192):
                            content.extend(chunk)
                            if len(content) > MAX_RESPONSE_BYTES:
                                raise SourceError("公开来源响应超过大小限制，已停止读取。")
                            if time.monotonic() >= self.deadline:
                                raise SourceError("公开检索超过时间限制，请稍后重试。")
                        try:
                            data = json.loads(content)
                        except (ValueError, UnicodeError, RecursionError) as exc:
                            raise SourceError("公开书目接口返回无效数据，请稍后重试。") from exc
                        if not isinstance(data, dict) or data.get("error"):
                            raise SourceError("公开书目接口结构变化或返回错误，未应用任何资料。")
                        result = (data, datetime.now(timezone.utc).isoformat())
                        with _LOCK:
                            _CACHE[cache_key] = (time.monotonic(), copy.deepcopy(result))
                            _CACHE.move_to_end(cache_key)
                            while len(_CACHE) > CACHE_ENTRIES:
                                _CACHE.popitem(last=False)
                        return result
                except requests.RequestException as exc:
                    if attempt == MAX_RETRIES:
                        raise SourceError("公开来源连接超时或网络不可用，请稍后重试。") from exc
                    break
        raise SourceError("公开来源检索失败，请稍后重试或手工补充。")


def _candidate(client, data, collected_at, draft):
    key = data.get("key", "")
    if not isinstance(key, str) or not EDITION_KEY.fullmatch(key):
        raise SourceError("来源不是可核对的具体版本记录，未生成图书候选。")
    title = _text(data.get("title"), 255)
    if not title:
        raise SourceError("具体版本记录缺少有效书名，资料不足。")
    url = BASE_URL + key
    fields = {}
    evidence = []
    issues = [REVIEW_ISSUE]

    def claim(field, value, source_url=url, when=collected_at):
        if value in ("", [], None):
            return
        fields[field] = value
        evidence.append(
            {
                "field": field,
                "value": copy.deepcopy(value),
                "kind": "web",
                "source_url": source_url,
                "source_label": "Open Library 公开版本书目（需人工核对）",
                "status": "candidate",
                "collected_at": when,
            }
        )

    claim("title", title)
    publishers = [name for v in _list(data.get("publishers")) if (name := _text(v, 255))]
    publisher = "; ".join(publishers)
    claim("publisher", publisher if len(publisher) <= 255 else "")
    claim("publication_date", _text(data.get("publish_date"), 32))
    claim("binding", _text(data.get("physical_format"), 100))
    claim("edition", _text(data.get("edition_name"), 500))
    pages = data.get("number_of_pages")
    if type(pages) is int and 0 < pages <= 2_147_483_647:
        claim("pages", pages)
    description = data.get("description")
    if isinstance(description, dict):
        description = description.get("value")
    claim("description", _text(description, 10000))
    languages = {
        _text(lang.get("key"), 32)
        for lang in _list(data.get("languages"))
        if isinstance(lang, dict)
    }
    if languages == {"/languages/eng"}:
        claim("language", "english")
    elif languages and languages <= {"/languages/chi", "/languages/zho"}:
        claim("language", "chinese")
    elif (
        "/languages/eng" in languages
        and languages & {"/languages/chi", "/languages/zho"}
        and languages <= {"/languages/eng", "/languages/chi", "/languages/zho"}
    ):
        claim("language", "bilingual")
    isbns = []
    for value in _list(data.get("isbn_13")) + _list(data.get("isbn_10")):
        try:
            normalized = _isbn(value)
            if normalized not in isbns:
                isbns.append(normalized)
        except ValueError:
            issues.append("公开版本记录有无效 ISBN，已忽略该号码。")
    is_set = SET_MARKER.search(title + " " + fields.get("binding", "")) or (
        type(data.get("number_of_volumes")) is int and data["number_of_volumes"] > 1
    )
    if is_set:
        issues.append("来源为疑似套装/多卷候选；套装 ISBN 不作为单本 ISBN 提供，不推断分册。")
    elif draft.get("isbn") in isbns:
        claim("isbn", draft["isbn"])
    elif len(isbns) == 1:
        claim("isbn", isbns[0])
    elif isbns:
        issues.append("来源包含多个不同 ISBN，需核对具体版本后手工选择，未自动选用号码。")
    else:
        issues.append("来源缺少有效 ISBN，请用版权页核对单本/套装及具体版本。")
    if draft.get("isbn") and draft["isbn"] not in isbns:
        issues.append("ISBN 与照片/草稿不一致或来源缺少该 ISBN；禁止据此自动匹配版本。")
    covers = [v for v in _list(data.get("covers")) if type(v) is int and v > 0]
    if covers:
        claim("cover_url", f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg?default=false")
        claim("cover_source_url", url)
    authors = []
    author_evidence = []
    references = _list(data.get("authors"))
    for reference in references[:MAX_AUTHORS]:
        author_key = reference.get("key", "") if isinstance(reference, dict) else ""
        if not isinstance(author_key, str) or not AUTHOR_KEY.fullmatch(author_key):
            issues.append("版本作者标识无效，未使用作品级作者或猜测作者。")
            continue
        try:
            author, when = client.get_json(author_key + ".json")
            if author.get("key") != author_key:
                raise SourceError("作者来源标识不一致，未应用作者资料。")
            name = _text(author.get("name"), 255)
            if name:
                authors.append(name)
                author_evidence.append((name, BASE_URL + author_key, when))
        except SourceError as exc:
            issues.append(str(exc))
    if authors:
        claim("authors", authors)
        # Both the edition's author association and the resolved names are traceable.
        association = evidence[-1]
        for name, source_url, when in author_evidence:
            evidence.append(
                {**association, "value": [name], "source_url": source_url, "collected_at": when}
            )
    if len(references) > MAX_AUTHORS:
        issues.append("作者列表超过本次检索上限，仅返回部分作者，请核对完整名单。")
    for field, label in (
        ("title", "书名"),
        ("publisher", "出版社"),
        ("publication_date", "出版时间"),
        ("binding", "装帧"),
        ("edition", "版本说明"),
        ("language", "语言"),
    ):
        visible = draft.get(field)
        if visible and visible != "unknown":
            if not fields.get(field):
                issues.append(f"公开版本记录缺少{label}，无法核对草稿线索。")
            elif _canonical(visible) != _canonical(str(fields[field])):
                issues.append(f"候选{label}与照片/草稿不一致，请人工核对，未合并或覆盖资料。")
    if draft.get("authors"):
        if not authors:
            issues.append("缺少可核对的版本作者，无法核对草稿作者。")
        elif not {_canonical(v) for v in draft["authors"]} <= {_canonical(v) for v in authors}:
            issues.append("候选作者与照片/草稿不一致，请人工核对。")
    return {
        "id": "openlibrary:" + key.removeprefix("/books/"),
        "fields": fields,
        "evidence": evidence,
        "issues": list(dict.fromkeys(issues)),
    }


def search_candidates(fields: dict) -> dict:
    """Lookup public records only; never mutate input, books, drafts or manual fields."""
    result = {"candidates": [], "issues": []}
    if not isinstance(fields, dict):
        result["issues"].append("请提供书名或有效 ISBN 后再检索。")
        return result
    draft = {
        field: _text(fields.get(field), 500)
        for field in ("title", "publisher", "publication_date", "binding", "edition", "language")
    }
    draft["authors"] = [v for item in _list(fields.get("authors")) if (v := _text(item, 255))]
    draft["isbn"] = ""
    if fields.get("isbn"):
        try:
            draft["isbn"] = _isbn(fields["isbn"])
        except ValueError:
            result["issues"].append("ISBN 无效，未按该号码检索；可用书名和可见身份线索查询。")
    if not draft["isbn"] and not draft["title"]:
        result["issues"].append("缺少有效 ISBN 或书名，公开来源不足；请补拍版权页或手工补充。")
        return result
    try:
        client = _PublicClient()
    except SourceError as exc:
        result["issues"].append(str(exc))
        return result
    try:
        if draft["isbn"]:
            try:
                data, when = client.get_json("/isbn/" + draft["isbn"] + ".json")
                result["candidates"].append(_candidate(client, data, when, draft))
            except SourceError as exc:
                result["issues"].append(str(exc))
        if not result["candidates"] and draft["title"]:
            query = "title:" + _quote(draft["title"])
            for author in draft["authors"][:MAX_AUTHORS]:
                query += " AND author:" + _quote(author)
            if draft["publisher"]:
                query += " AND publisher:" + _quote(draft["publisher"])
            try:
                data, _ = client.get_json(
                    "/search.json",
                    {
                        "q": query,
                        "fields": "key,editions,editions.key",
                        "limit": MAX_CANDIDATES,
                    },
                )
                if not isinstance(data.get("docs"), list):
                    raise SourceError("公开检索接口结构变化，缺少版本检索结果。")
                seen = set()
                for work in data["docs"][:MAX_CANDIDATES]:
                    editions = work.get("editions", {}) if isinstance(work, dict) else {}
                    docs = editions.get("docs", []) if isinstance(editions, dict) else []
                    for edition in _list(docs)[:MAX_CANDIDATES]:
                        key = edition.get("key", "") if isinstance(edition, dict) else ""
                        if not isinstance(key, str) or not EDITION_KEY.fullmatch(key):
                            result["issues"].append(
                                "检索结果没有安全有效的具体版本标识，未使用作品级资料。"
                            )
                            continue
                        if key in seen or len(seen) >= MAX_CANDIDATES:
                            continue
                        seen.add(key)
                        try:
                            record, when = client.get_json(key + ".json")
                            if record.get("key") != key:
                                raise SourceError("检索版本与来源记录标识不一致，未应用该候选。")
                            result["candidates"].append(_candidate(client, record, when, draft))
                        except SourceError as exc:
                            result["issues"].append(str(exc))
            except SourceError as exc:
                result["issues"].append(str(exc))
    finally:
        client.close()
    if not result["candidates"]:
        result["issues"].append("公开来源未提供足够的具体版本资料；可补拍版权页或继续手工录入。")
    result["issues"] = list(dict.fromkeys(result["issues"]))
    return result
