#!/usr/bin/env python3
"""Shared plumbing for the roblox-luau skill: errors, HTTP with an on-disk cache, cache management."""

from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
from pathlib import Path

import argparse
import json
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

CREATOR_ROOT = "https://create.roblox.com"
LUAU_ROOT = "https://luau.org"
RFC_ROOT = "https://rfcs.luau.org"
USER_AGENT = "roblox-luau-skill/2.0 (public documentation research)"
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_CHARS = 8_000  # keep output small; raise with --max-chars only when needed
BRIEF_MAX_CHARS = 2_500
SUMMARY_CHARS = 300
DEFAULT_MAX_BYTES = 8_000_000
FETCH_HOSTS = {
    "luau.org",
    "rfcs.luau-lang.org",
    "rfcs.luau.org",
    "devforum.roblox.com",
    "lua.org",
    "www.lua.org",
}


class ResearchError(Exception):
    """A retrieval or response-shape error that must not be treated as no data."""


class NetworkUnavailable(ResearchError):
    """The environment cannot reach the source (DNS failure, refused connection, or network policy block)."""


NETWORK_GUIDANCE = (
    "Do not retry this host in this conversation. Use web search and page fetch on the official sources "
    "(create.roblox.com, luau.org, rfcs.luau.org) instead; `api` lookups and the local toolchain use github.com "
    "and may still work. If the user wants this host reachable, they must allow it in the code-execution "
    "network settings and start a new chat."
)


def is_network_block(reason: object) -> bool:
    """True for failures that retrying cannot fix: DNS failure, refused connection, or a proxy tunnel denial."""
    if isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
        return True
    return "tunnel connection failed" in str(reason).casefold()


def is_timeout(exc: object) -> bool:
    reason = getattr(exc, "reason", None)
    return (
        isinstance(exc, (TimeoutError, socket.timeout))
        or isinstance(reason, (TimeoutError, socket.timeout))
        or "timed out" in str(exc).casefold()
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_url(url: str, hosts: set[str]) -> urllib.parse.SplitResult:
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.hostname.lower() not in hosts
        or parts.username
        or parts.password
    ):
        raise ResearchError(f"URL is not an allowed HTTPS documentation source: {url}")
    return parts


def error_hint(url: str, status: int) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    if status == 429:
        return " The source is rate-limiting requests; retry later or use web tools."
    if status == 404 and host == "create.roblox.com":
        return " Do not guess another path; use the Creator Hub search command or an official index."
    if status == 404 and host == "luau.org":
        return " Use luau-index to discover an actual language-documentation path."
    if host == "devforum.roblox.com":
        return (
            " Direct DevForum retrieval may be restricted. Use site-restricted web search or a browser; "
            "this failure is not evidence that the topic is absent."
        )
    return ""


def _network_get(
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
    attempts: int = 2,
) -> tuple[str, str, str]:
    """Fetch a small public document with bounded retries and response size."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/markdown, text/plain, text/html, application/xml;q=0.9, */*;q=0.5",
                "Accept-Encoding": "identity",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ResearchError(
                        f"Response exceeded {max_bytes} bytes: {url}. Retrieve a narrower page; never fetch llms-full.txt."
                    )
                charset = response.headers.get_content_charset() or "utf-8"
                text = data.decode(charset, errors="replace").lstrip("\ufeff")
                return text, response.geturl(), response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            last_error = exc
            deny = exc.headers.get("x-deny-reason") if exc.headers else None
            if deny:
                raise NetworkUnavailable(
                    f"{urllib.parse.urlsplit(url).hostname} blocked by network policy ({deny}). {NETWORK_GUIDANCE}"
                ) from exc
            if exc.code in {429, 500, 502, 503, 504} and attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
                continue
            raise ResearchError(f"HTTP {exc.code} for {url}.{error_hint(url, exc.code)}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if is_network_block(getattr(exc, "reason", exc)) or is_network_block(exc):
                raise NetworkUnavailable(
                    f"{urllib.parse.urlsplit(url).hostname} is unreachable from this environment ({exc}). {NETWORK_GUIDANCE}"
                ) from exc
            if attempt + 1 < attempts:
                time.sleep(0.6 * (attempt + 1))
                continue
    if last_error is not None and is_timeout(last_error):
        raise NetworkUnavailable(
            f"{urllib.parse.urlsplit(url).hostname} timed out on {attempts} consecutive attempts ({last_error}); "
            f"timeouts usually mean outbound traffic is blocked. {NETWORK_GUIDANCE}"
        )
    raise ResearchError(f"Could not retrieve {url}: {last_error}")




# ---------------------------------------------------------------------------
# On-disk cache: fewer requests, instant repeats, and stale-serving when the network is blocked.
# ---------------------------------------------------------------------------
CACHE_STATE = {"enabled": True, "refresh": False, "offline": False}
LAST_META: dict = {}
TTL_SECONDS = {"search": 6 * 3600, "news": 6 * 3600, "dump": 12 * 3600, "index": 24 * 3600, "doc": 24 * 3600}


def cache_root() -> Path:
    candidates = []
    if os.environ.get("ROBLOX_LUAU_HOME"):
        candidates.append(Path(os.environ["ROBLOX_LUAU_HOME"]))
    try:
        candidates.append(Path.home() / ".cache" / "roblox-luau")
    except RuntimeError:
        pass
    candidates.append(Path(tempfile.gettempdir()) / "roblox-luau-cache")
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".w{os.getpid()}"
            probe.write_text("ok")
            probe.unlink()
            return path
        except OSError:
            continue
    raise ResearchError("No writable cache directory; set ROBLOX_LUAU_HOME to a writable path.")


def ttl_for(url: str) -> int:
    if "/docs/api/search" in url:
        return TTL_SECONDS["search"]
    if url.endswith(("feed.xml", ".atom")):
        return TTL_SECONDS["news"]
    if "Roblox-Client-Tracker" in url and url.endswith(".json"):
        return TTL_SECONDS["dump"]
    if url.endswith(("llms.txt", "sitemap.xml")):
        return TTL_SECONDS["index"]
    return TTL_SECONDS["doc"]


def _cache_file(url: str) -> Path:
    return cache_root() / "http" / (hashlib.sha256(url.encode("utf-8")).hexdigest()[:40] + ".json.gz")


def _cache_load(url: str):
    try:
        with gzip.open(_cache_file(url), "rt", encoding="utf-8") as fh:
            entry = json.load(fh)
        return entry if isinstance(entry, dict) and "text" in entry and "t" in entry else None
    except (OSError, ValueError, ResearchError):
        return None


def _cache_store(url: str, text: str, final_url: str, ctype: str) -> None:
    try:
        path = _cache_file(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump({"url": url, "final_url": final_url, "ctype": ctype, "t": time.time(), "text": text}, fh)
        os.replace(tmp, path)
    except (OSError, ResearchError):
        pass  # the cache is an optimization; never fail a lookup because of it


def human_age(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return f"{s}s"
    if s < 5400:
        return f"{s // 60}m"
    if s < 172800:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def cache_note() -> str:
    state = LAST_META.get("cache", "n/a")
    age = LAST_META.get("age")
    return f"cache {state}" + (f" age {human_age(age)}" if age is not None and state not in ("miss", "off") else "")


def http_get(url: str, **kwargs):
    """Cached GET returning (text, final_url, content_type). Records provenance in LAST_META."""
    entry = _cache_load(url) if CACHE_STATE["enabled"] else None
    now = time.time()

    def served(item, state):
        LAST_META.clear()
        LAST_META.update({"cache": state, "age": now - item["t"], "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(item["t"]))})
        return item["text"], item.get("final_url", url), item.get("ctype", "")

    if entry and not CACHE_STATE["refresh"] and now - entry["t"] < ttl_for(url):
        return served(entry, "hit")
    if CACHE_STATE["offline"]:
        if entry:
            return served(entry, "stale")
        raise ResearchError(f"--offline was set and there is no cached copy of {url}.")
    try:
        text, final_url, ctype = _network_get(url, **kwargs)
    except NetworkUnavailable:
        if entry:
            print(f"warning: network unavailable; serving cached copy ({human_age(now - entry['t'])} old) of {url}", file=sys.stderr)
            return served(entry, "stale")
        raise
    if CACHE_STATE["enabled"]:
        _cache_store(url, text, final_url, ctype)
    LAST_META.clear()
    LAST_META.update({"cache": "miss" if CACHE_STATE["enabled"] else "off", "age": 0, "fetched": utc_now()})
    return text, final_url, ctype


def cache_stats() -> dict:
    root = cache_root() / "http"
    files = list(root.glob("*.json.gz")) if root.exists() else []
    return {"path": str(cache_root()), "entries": len(files), "bytes": sum(f.stat().st_size for f in files)}


def cache_clear(older_than_days=None) -> int:
    root = cache_root() / "http"
    removed = 0
    for f in list(root.glob("*.json.gz")) if root.exists() else []:
        if older_than_days is None or time.time() - f.stat().st_mtime > older_than_days * 86400:
            f.unlink()
            removed += 1
    return removed


def configure_cache(args) -> None:
    CACHE_STATE["enabled"] = not getattr(args, "no_cache", False)
    CACHE_STATE["refresh"] = getattr(args, "refresh", False)
    CACHE_STATE["offline"] = getattr(args, "offline", False)


def common_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    g = p.add_argument_group("cache")
    g.add_argument("--no-cache", action="store_true", help="Neither read nor write the local cache.")
    g.add_argument("--refresh", action="store_true", help="Skip cached copies and re-fetch (still updates the cache).")
    g.add_argument("--offline", action="store_true", help="Use only cached copies (any age); never touch the network.")
    return p
