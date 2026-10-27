#!/usr/bin/env python3
"""Roblox/Luau evidence retrieval (stdlib only): API dump lookups, Creator Hub docs, Luau docs/news/RFCs,
response cache, and a verified-facts ledger. See SKILL.md for the cost ladder and evidence rules."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from rl_common import *  # noqa: F401,F403
from rl_dump import add_api_parser
from rl_facts import add_facts_parser

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def clean_name(text: str) -> str:
    """Reduce a heading or signature line to a bare name such as Terrain:WriteVoxels."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").replace("\\", "")
    text = re.sub(r"\(.*$", "", text)
    return text.strip(" *_:#")


def name_matches(candidate: str, member: str) -> bool:
    c, m = candidate.casefold(), member.casefold()
    return c == m or c.endswith((":" + m, "." + m))


def member_blocks(lines: list[str], member: str) -> list[tuple[int, int]]:
    """Locate sections for one member. Supports heading-style pages and '---'-separated pages."""
    in_fence = False
    fenced: list[bool] = []
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            fenced.append(True)
        else:
            fenced.append(in_fence)

    blocks: list[tuple[int, int]] = []
    for i, line in enumerate(lines):  # Strategy A: Markdown headings
        match = None if fenced[i] else HEADING_RE.match(line)
        if match and name_matches(clean_name(match.group(2)), member):
            level, j = len(match.group(1)), i + 1
            while j < len(lines):
                nxt = None if fenced[j] else HEADING_RE.match(lines[j])
                if nxt and len(nxt.group(1)) <= level:
                    break
                j += 1
            blocks.append((i, j))
    if blocks:
        return blocks

    seps = [i for i, line in enumerate(lines) if line.strip() == "---" and not fenced[i]]

    def enclosing(index: int) -> tuple[int, int]:
        start = max([k for k in seps if k < index], default=-1) + 1
        end = min([k for k in seps if k > index], default=len(lines))
        return start, end

    for i, line in enumerate(lines):  # Strategy B: block opens with the bare member name
        if fenced[i] or not line.strip() or len(line) > 80:
            continue
        if not name_matches(clean_name(line), member):
            continue
        prev = next((k for k in range(i - 1, -1, -1) if lines[k].strip()), None)
        if prev is None or prev in seps:  # nearest non-blank line above is a separator (or page start)
            blocks.append(enclosing(i))
    if blocks:
        return blocks

    sig = re.compile(r"^\s*[\w.]+[:.]" + re.escape(member) + r"\(", re.IGNORECASE)
    for i, line in enumerate(lines):  # Strategy C: a signature line such as Class:Member(
        if not fenced[i] and sig.match(line):
            blocks.append(enclosing(i))
    return blocks


def candidate_members(lines: list[str], limit: int = 40) -> list[str]:
    names: list[str] = []
    for line in lines:
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) >= 2:
            name = clean_name(match.group(2))
            if name and len(name) < 60 and name not in names:
                names.append(name)
        if len(names) >= limit:
            break
    return names


def extract_members(text: str, members: list[str]) -> str:
    members = [re.split(r"[:.]", m)[-1] or m for m in members]  # accept Class:Method and Class.Member
    """Return only the named member sections from a documentation page."""
    lines = text.splitlines()
    title = next((m.group(1).strip() for m in (re.match(r"^#\s+(.*)$", line) for line in lines) if m), "")
    out: list[str] = [f"Page: {title}"] if title else []
    seen: set[tuple[int, int]] = set()
    for member in members:
        blocks = [b for b in dict.fromkeys(member_blocks(lines, member)) if b not in seen]
        if not blocks:
            hint = ", ".join(candidate_members(lines)) or "none detected"
            raise ResearchError(
                f"Member {member!r} not found on this page. Headings seen: {hint}. It may be inherited "
                "from a parent class (check that class page) or live on a different page; search again. "
                "Absence here is not proof the member does not exist."
            )
        for start, end in blocks:
            seen.add((start, end))
            out.append("\n".join(lines[start:end]).strip())
    return "\n\n=====\n\n".join(out)


def print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_document(url: str, content_type: str, text: str, max_chars: int, updated=None) -> None:
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip() + "\n\n[Output truncated; use --member NAME, --brief, a narrower page, or a filter.]"
    print(f"Source: {url}")
    print(f"Retrieved: {LAST_META.get('fetched') or utc_now()} ({cache_note()})" + (f" | Page-Updated: {updated}" if updated else ""))
    print("---")
    print(text)
    if truncated:
        print("[Truncation notice: content above is incomplete.]", file=sys.stderr)


def page_updated(text: str):
    m = re.search(r"^last_updated:\s*(\S+)", text[:3000], re.M)
    return m.group(1) if m else None


def brief_text(text: str) -> str:
    """Drop code samples, link targets, and front matter to cut tokens; keeps signatures and prose."""
    t = re.sub(r"```.*?```", "", text, flags=re.S)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\A(?:---\n)?(?:(?:name|last_updated):[^\n]*\n)+", "", t)
    t = re.sub(r"[ \t]+$", "", t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def normalize_creator_doc(value: str) -> str:
    if value.startswith("/"):
        value = CREATOR_ROOT + value
    parts = validate_url(value, {"create.roblox.com"})
    if not parts.path.startswith("/docs/"):
        raise ResearchError("Creator documentation paths must begin with /docs/.")
    if parts.path == "/docs/llms-full.txt":
        raise ResearchError("Refusing llms-full.txt; search and retrieve an individual Markdown page instead.")
    path = parts.path.rstrip("/")
    if not path.endswith((".md", ".txt", ".xml", ".json")):
        path += ".md"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def command_search(args: argparse.Namespace) -> None:
    url = CREATOR_ROOT + "/docs/api/search?" + urllib.parse.urlencode({"q": args.query})
    text, final_url, _ = http_get(url)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ResearchError(f"Creator Hub search did not return JSON: {final_url}") from exc
    if not isinstance(data, dict):
        raise ResearchError("Creator Hub search returned an unexpected JSON shape.")
    if data.get("error"):
        raise ResearchError(f"Creator Hub search error: {data['error']} Usage: {data.get('usage', 'unavailable')}")
    results = data.get("results", [])
    if not isinstance(results, list):
        raise ResearchError("Creator Hub search response has no valid results array.")
    results = [item for item in results if isinstance(item, dict)]
    if args.engine_only:
        results = [item for item in results if "/reference/engine/" in str(item.get("url", ""))]
    cleaned = []
    for item in results[: args.limit]:
        if not isinstance(item, dict):
            continue
        cleaned.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "type": item.get("type"),
                "summary": str(item.get("summary") or "")[:SUMMARY_CHARS],
            }
        )
    payload = {
        "source": final_url,
        "authority": "official Roblox Creator Hub",
        "retrieved_at": utc_now(),
        "query": args.query,
        "engine_only": args.engine_only,
        "returned": len(cleaned),
        "reported_total_results": data.get("totalResults"),
        "results": cleaned,
    }
    if not cleaned:
        payload["note"] = "No matches in this response. Refine the query; do not infer that the topic is undocumented."
    print_json(payload)


def command_doc(args: argparse.Namespace) -> None:
    url = normalize_creator_doc(args.url)
    text, final_url, content_type = http_get(url)
    updated = page_updated(text)
    if args.member:
        text = extract_members(text, args.member)
    limit = args.max_chars or (BRIEF_MAX_CHARS if args.brief else DEFAULT_MAX_CHARS)
    if args.brief:
        text = brief_text(text)
    print_document(final_url, content_type, text, limit, updated)


def command_index(args: argparse.Namespace) -> None:
    urls = {
        "creator": CREATOR_ROOT + "/docs/llms.txt",
        "engine": CREATOR_ROOT + "/docs/reference/engine/llms.txt",
    }
    url = urls[args.kind]
    text, final_url, content_type = http_get(url, max_bytes=2_000_000)
    if args.find:
        needle = args.find.casefold()
        section = ""
        matches = []
        seen = set()
        for number, line in enumerate(text.splitlines(), start=1):
            if line.startswith("## "):
                section = line.strip()
            if needle in line.casefold():
                block = f"{section}\nline {number}: {line.strip()}"
                if block not in seen:
                    matches.append(block)
                    seen.add(block)
        if matches:
            text = "# Matching index lines\n\n" + "\n\n".join(matches)
        else:
            text = (
                f"No index lines matched {args.find!r}. Refine the filter or use Creator Hub search. "
                "This is not proof that the topic is undocumented."
            )
    print_document(final_url, content_type, text, args.max_chars)


class HTMLToMarkdown(HTMLParser):
    """Small HTML-to-text converter for documentation pages, including code and links."""

    HEADINGS = {f"h{level}": level for level in range(1, 7)}
    IGNORED = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button"}
    BLOCKS = {"p", "div", "section", "article", "main", "ul", "ol", "table", "tr", "blockquote", "dl", "dt", "dd"}

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self.ignore_depth = 0
        self.pre_depth = 0
        self.links: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.IGNORED:
            self.ignore_depth += 1
            return
        if self.ignore_depth:
            return
        attributes = dict(attrs)
        if tag in self.HEADINGS:
            self.parts.append("\n\n" + "#" * self.HEADINGS[tag] + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "pre":
            self.pre_depth += 1
            self.parts.append("\n\n```\n")
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
        elif tag == "a":
            href = attributes.get("href")
            self.links.append(href)
            if href:
                self.parts.append("[")
        elif tag == "br":
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag == "img" and attributes.get("alt"):
            self.parts.append(str(attributes["alt"]))
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "img", "hr", "meta", "link", "input"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORED:
            self.ignore_depth = max(0, self.ignore_depth - 1)
            return
        if self.ignore_depth:
            return
        if tag in self.HEADINGS or tag in self.BLOCKS:
            self.parts.append("\n")
        elif tag == "pre":
            self.pre_depth = max(0, self.pre_depth - 1)
            self.parts.append("\n```\n\n")
        elif tag == "code" and not self.pre_depth:
            self.parts.append("`")
        elif tag == "a" and self.links:
            href = self.links.pop()
            if href:
                absolute = urllib.parse.urljoin(self.base_url, href)
                self.parts.append("](" + absolute + ")")

    def handle_data(self, data: str) -> None:
        if self.ignore_depth:
            return
        if self.pre_depth:
            self.parts.append(data)
        else:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        value = "".join(self.parts).replace("\r", "")
        value = re.sub(r"[ \t]+\n", "\n", value)
        value = re.sub(r"\n +\n", "\n\n", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.current_href: str | None = None
        self.current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a" and self.current_href is None:
            self.current_href = dict(attrs).get("href") or ""
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_href is not None:
            self.current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.current_href is not None:
            title = re.sub(r"\s+", " ", "".join(self.current_text)).strip()
            self.links.append((title, urllib.parse.urljoin(self.base_url, self.current_href)))
            self.current_href = None
            self.current_text = []


def html_to_markdown(html: str, base_url: str) -> str:
    parser = HTMLToMarkdown(base_url)
    parser.feed(html)
    parser.close()
    return parser.text()


def html_to_plain(html: str) -> str:
    parser = HTMLToMarkdown("https://luau.org/")
    parser.feed(html)
    parser.close()
    return re.sub(r"\s+", " ", parser.text()).strip()


def command_fetch(args: argparse.Namespace) -> None:
    parts = validate_url(args.url, FETCH_HOSTS)
    text, final_url, content_type = http_get(urllib.parse.urlunsplit(parts))
    if content_type == "text/html" or re.match(r"\s*(?:<!DOCTYPE\s+html|<html\b)", text, re.IGNORECASE):
        text = html_to_markdown(text, final_url)
        content_type = "text/markdown (extracted from HTML)"
    print_document(final_url, content_type, text, args.max_chars)


def xml_locs(text: str) -> tuple[str, list[str]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ResearchError("Source returned invalid XML.") from exc
    values = [node.text.strip() for node in root.iter() if local_name(node.tag) == "loc" and node.text]
    return local_name(root.tag), values


def command_luau_index(args: argparse.Namespace) -> None:
    index_url = LUAU_ROOT + "/sitemap-index.xml"
    text, final_url, _ = http_get(index_url, max_bytes=2_000_000)
    kind, locs = xml_locs(text)
    if kind == "sitemapindex":
        page_urls: list[str] = []
        for sitemap_url in locs[:10]:
            validate_url(sitemap_url, {"luau.org"})
            sitemap_text, _, _ = http_get(sitemap_url, max_bytes=4_000_000)
            sitemap_kind, values = xml_locs(sitemap_text)
            if sitemap_kind == "urlset":
                page_urls.extend(values)
    elif kind == "urlset":
        page_urls = locs
    else:
        raise ResearchError(f"Unexpected Luau sitemap root element: {kind}")
    unique = list(dict.fromkeys(page_urls))
    if args.find:
        needle = args.find.casefold()
        unique = [url for url in unique if needle in url.casefold()]
    selected = unique[: args.limit]
    print_json(
        {
            "source": final_url,
            "authority": "official Luau documentation sitemap",
            "retrieved_at": utc_now(),
            "filter": args.find,
            "matched": len(unique),
            "returned": len(selected),
            "results": selected,
        }
    )


def item_child_text(item: ET.Element, names: set[str]) -> str:
    for child in item:
        if local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def item_link(item: ET.Element) -> str:
    """RSS puts the URL in <link> text; Atom puts it in <link href=...> (prefer rel=alternate)."""
    fallback = ""
    for child in item:
        if local_name(child.tag) != "link":
            continue
        if child.text and child.text.strip():
            return child.text.strip()
        href = child.get("href")
        if href and child.get("rel") in (None, "alternate"):
            return href.strip()
        fallback = fallback or (href or "").strip()
    return fallback or item_child_text(item, {"guid", "id"})


def publication_timestamp(value: str) -> float:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    try:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (OverflowError, OSError):
        return 0.0


def command_news(args: argparse.Namespace) -> None:
    url = LUAU_ROOT + "/feed.xml"
    text, final_url, _ = http_get(url, max_bytes=12_000_000)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ResearchError("Luau news feed returned invalid XML.") from exc
    needle = args.find.casefold() if args.find else None
    items = []
    for item in root.iter():
        if local_name(item.tag) not in {"item", "entry"}:  # RSS <item> or Atom <entry>
            continue
        title = item_child_text(item, {"title"})
        link = item_link(item)
        published = item_child_text(item, {"pubDate", "date", "published", "updated"})
        content = item_child_text(item, {"encoded", "description", "summary", "content"})
        searchable = " ".join((title, link, content)).casefold()
        if needle and needle not in searchable:
            continue
        items.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "summary": html_to_plain(content)[:700],
                "_sort": publication_timestamp(published),
            }
        )
    items.sort(key=lambda entry: entry["_sort"], reverse=True)
    selected = items[: args.limit]
    for entry in selected:
        entry.pop("_sort", None)
    print_json(
        {
            "source": final_url,
            "authority": "official Luau news feed",
            "retrieved_at": utc_now(),
            "filter": args.find,
            "matched": len(items),
            "returned": len(selected),
            "note": "News is historical evidence; verify current behavior and later updates before making a current-status claim."
            + ("" if items else " Zero entries matched or parsed; that is not proof the topic is absent."),
            "results": selected,
        }
    )


def command_rfcs(args: argparse.Namespace) -> None:
    text, final_url, _ = http_get(RFC_ROOT + "/", max_bytes=2_000_000)
    parser = LinkExtractor(final_url)
    parser.feed(text)
    parser.close()
    needle = args.find.casefold() if args.find else None
    results = []
    seen = set()
    for title, url in parser.links:
        parts = urllib.parse.urlsplit(url)
        if parts.hostname not in {"rfcs.luau-lang.org", "rfcs.luau.org"} or not parts.path.endswith(".html") or url in seen:
            continue
        if needle and needle not in (title + " " + url).casefold():
            continue
        seen.add(url)
        results.append({"title": title, "url": url})
    matched = len(results)
    results = results[: args.limit]
    print_json(
        {
            "source": final_url,
            "authority": "official Luau RFC index",
            "retrieved_at": utc_now(),
            "filter": args.find,
            "matched": matched,
            "returned": len(results),
            "note": "An RFC establishes design history only; it does not establish implementation, deployment, or default enablement.",
            "results": results,
        }
    )


def ranged_int(minimum: int, maximum: int):
    def convert(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number

    return convert


def command_cache(args: argparse.Namespace) -> None:
    if args.action == "path":
        print(cache_root())
    elif args.action == "stats":
        s = cache_stats()
        print(f"{s['path']}: {s['entries']} cached responses, {s['bytes'] / 1e6:.2f} MB")
    else:
        print(f"removed {cache_clear(args.older_than)} cached responses")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Roblox/Luau evidence retrieval: API dump lookups (api), Creator Hub docs, Luau docs/news/RFCs, cache, facts ledger."
    )
    common = common_parser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    def leaf(name: str, help_text: str, func):
        p = subparsers.add_parser(name, parents=[common], help=help_text)
        p.set_defaults(func=func)
        return p

    search = leaf("search", "Search the official Creator Hub documentation API.", command_search)
    search.add_argument("query", help="Natural-language question or exact API name.")
    search.add_argument("--engine-only", action="store_true", help="Keep only Engine API reference results.")
    search.add_argument("--limit", type=ranged_int(1, 25), default=5)

    doc = leaf("doc", "Fetch an exact Creator Hub page as Markdown.", command_doc)
    doc.add_argument("url", help="Absolute URL or /docs/... path returned by search or an index.")
    doc.add_argument("--member", action="append", metavar="NAME", help="Return only this property, method, event, or section (repeatable). Strongly preferred for class pages.")
    doc.add_argument("--brief", action="store_true", help="Strip code samples and link targets; default cap 2500 chars.")
    doc.add_argument("--max-chars", type=ranged_int(500, 200_000), default=None)

    index = leaf("index", "Read or filter an official Creator Hub machine-readable index.", command_index)
    index.add_argument("kind", choices=("creator", "engine"))
    index.add_argument("--find", help="Case-insensitive substring filter for index lines.")
    index.add_argument("--max-chars", type=ranged_int(500, 200_000), default=DEFAULT_MAX_CHARS)

    fetch = leaf("fetch", "Fetch an allowed Luau, RFC, DevForum, or Lua documentation URL.", command_fetch)
    fetch.add_argument("url", help="Exact HTTPS URL; Luau and RFC HTML is converted to readable text.")
    fetch.add_argument("--max-chars", type=ranged_int(500, 200_000), default=DEFAULT_MAX_CHARS)

    luau_index = leaf("luau-index", "List actual pages from the official Luau sitemap.", command_luau_index)
    luau_index.add_argument("--find", help="Case-insensitive substring filter for page URLs.")
    luau_index.add_argument("--limit", type=ranged_int(1, 500), default=50)

    news = leaf("news", "Search or list official Luau news, newest first.", command_news)
    news.add_argument("--find", help="Case-insensitive substring filter for titles, URLs, and article content.")
    news.add_argument("--limit", type=ranged_int(1, 50), default=10)

    rfcs = leaf("rfcs", "Search the official Luau RFC index.", command_rfcs)
    rfcs.add_argument("--find", help="Case-insensitive substring filter for RFC titles and URLs.")
    rfcs.add_argument("--limit", type=ranged_int(1, 100), default=25)

    add_api_parser(subparsers, common)
    add_facts_parser(subparsers)

    cache = subparsers.add_parser("cache", help="Inspect or clear the local response cache.")
    cache.add_argument("action", choices=("stats", "path", "clear"))
    cache.add_argument("--older-than", type=int, metavar="DAYS", help="With clear: only entries older than DAYS.")
    cache.set_defaults(func=command_cache)
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = build_parser()
    args = parser.parse_args()
    configure_cache(args)
    try:
        args.func(args)
        return 0
    except NetworkUnavailable as exc:
        print(f"error: NETWORK_UNAVAILABLE: {exc}", file=sys.stderr)
        return 3
    except ResearchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
