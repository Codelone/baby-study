"""Public bibliographic adapters. Parsed values are candidates until reviewed in a mapping."""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


class SourceError(ValueError):
    pass


class CachedClient:
    def __init__(self, cache, refresh=False, interval=2, timeout=20, retries=2):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.interval = interval
        self.timeout = timeout
        self.retries = retries
        self.last = {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "BabyStudyCatalog/1.0 (bibliographic research; no media)"
        )

    def fetch(self, url):
        host = urlparse(url).hostname
        if urlparse(url).scheme not in ("https", "http") or not host:
            raise SourceError("只接受公开 http(s) 来源 URL")
        if host == "covers.openlibrary.org":
            raise SourceError("封面仅保存 URL，不采集或下载 Open Library 封面")
        file = self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".json")
        if file.exists() and not self.refresh:
            return json.loads(file.read_text())
        for attempt in range(self.retries + 1):
            wait = self.interval - (time.monotonic() - self.last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            self.last[host] = time.monotonic()
            try:
                headers = (
                    {"X-HB-Client-Type": "diandubi-pc"} if host.endswith("xiaobien.com") else {}
                )
                response = self.session.get(url, timeout=self.timeout, headers=headers)
                if response.status_code in (429, 503):
                    retry = response.headers.get("Retry-After", "")
                    if retry:
                        try:
                            seconds = float(retry)
                        except ValueError:
                            seconds = max(0, parsedate_to_datetime(retry).timestamp() - time.time())
                        if seconds > 60:
                            raise SourceError(
                                f"HTTP {response.status_code}：延后重试，Retry-After={retry}"
                            )
                        time.sleep(max(seconds, 0))
                    response.raise_for_status()
                else:
                    response.raise_for_status()
                if len(response.content) > 5_000_000:
                    raise SourceError("来源超过 5MB 限制")
                # Some Chinese servers declare ISO-8859-1 for UTF-8 HTML.
                if response.encoding in (None, "ISO-8859-1"):
                    response.encoding = response.apparent_encoding
                result = {
                    "url": url,
                    "final_url": response.url,
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                    "text": response.text,
                }
                temp = file.with_suffix(".tmp")
                temp.write_text(json.dumps(result, ensure_ascii=False))
                temp.replace(file)
                return result
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if attempt == self.retries or (
                    status is not None and status not in (429, 500, 502, 503, 504)
                ):
                    raise SourceError(
                        f"{type(exc).__name__}: HTTP {status or 'timeout/network'}"
                    ) from exc
                time.sleep(2**attempt)
        raise SourceError("采集失败")


def unpack_nuxt(html):
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NUXT_DATA__")
    if not script:
        raise SourceError("小彼恩页面结构已变化：缺少 __NUXT_DATA__")
    values = json.loads(script.string)
    memo = {}

    def resolve(index):
        if index < 0:
            return None
        if index in memo:
            return memo[index]
        value = values[index]
        if isinstance(value, dict):
            result = {}
            memo[index] = result
            result.update({key: resolve(ref) for key, ref in value.items()})
            return result
        if isinstance(value, list):
            if value and isinstance(value[0], str):
                result = resolve(value[1]) if len(value) > 1 else []
            else:
                result = [resolve(ref) for ref in value]
            memo[index] = result
            return result
        return value

    return resolve(0)


def parse_xiaobien(html, url):
    root = unpack_nuxt(html)
    product = next(
        (v for v in root.get("data", {}).values() if isinstance(v, dict) and "book_name" in v), None
    )
    if not product:
        raise SourceError("小彼恩页面结构已变化：未找到书目对象")
    members = []
    for item in product.get("download_url", []):
        if isinstance(item, dict) and item.get("book_name"):
            members.append(
                {
                    "title": item["book_name"],
                    "source_id": str(item.get("book_id", "")),
                    "cover_url": item.get("cover", ""),
                }
            )
    return {
        "title": product["book_name"],
        "isbn": product.get("isbn", ""),
        "cover_url": product.get("cover", ""),
        "description": product.get("book_desc", ""),
        "members": members,
        "tags": product.get("tags", []),
        "source_id": str(product.get("book_id", "")),
        "source_url": url,
    }


def parse_structured(html, url):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            value = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue

        def walk(node):
            if isinstance(node, dict):
                if node.get("@type") in ("Book", "Product"):
                    results.append(node)
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(value)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    og = soup.find("meta", property="og:image")
    return {
        "title": title,
        "cover_url": urljoin(url, og.get("content", "")) if og else "",
        "structured": results,
        "text": soup.get_text(" ", strip=True),
        "source_url": url,
    }


def parse_blcup(html, url):
    result = parse_structured(html, url)
    text = result["text"]
    match = re.search(r"ISBN[：:\s]*([\d-]{10,17})", text, re.I)
    if match:
        result["isbn"] = match[1].replace("-", "")
    return result


def parse_openlibrary(text, url):
    data = json.loads(text)
    # Search API returns works. Keep candidates; never promote a work to an edition.
    if "docs" in data:
        return {"works": data["docs"], "source_url": url, "edition_verified": False}
    key = data.get("key", "")
    edition = {"edition": data, "source_url": url, "edition_verified": False}
    if key.startswith("/books/"):
        edition.update(
            {
                "title": data.get("title", ""),
                "isbn": (data.get("isbn_13") or data.get("isbn_10") or [""])[0],
                "publisher": "; ".join(data.get("publishers", [])),
                "publication_date": data.get("publish_date", ""),
                "pages": data.get("number_of_pages"),
                "binding": data.get("physical_format", ""),
                "cover_url": "https://covers.openlibrary.org/b/olid/"
                + key.removeprefix("/books/")
                + "-L.jpg?default=false",
            }
        )
    return edition


def parse_xiaobien_search(text, url):
    data = json.loads(text)
    if "list" not in data:
        raise SourceError("官方检索接口结构变化：" + str(data.get("message", "缺少 list")))
    return {"results": data["list"], "total": data.get("total", 0), "source_url": url}


ADAPTERS = {
    "xiaobien-search": parse_xiaobien_search,
    "xiaobien": parse_xiaobien,
    "blcup": parse_blcup,
    "fltrp": parse_blcup,
    "prh": parse_structured,
    "ladybird": parse_structured,
    "dc-canada": parse_structured,
    "structured": parse_structured,
    "openlibrary": parse_openlibrary,
}
