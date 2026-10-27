"""Verified-facts ledger: remember what was already verified (with source and date).

Stored as JSON lines. Default location: ./.roblox-luau/facts.jsonl inside a Rojo/git project,
otherwise the skill cache. Override with --file or ROBLOX_LUAU_FACTS. Facts go stale: entries older
than STALE_DAYS are flagged so they are rechecked before use.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from rl_common import ResearchError, cache_root

STALE_DAYS = 30
STATUSES = ("stable", "beta", "opt-in", "deprecated", "removed", "proposal", "unverified", "tested-locally")


def default_path() -> Path:
    env = os.environ.get("ROBLOX_LUAU_FACTS")
    if env:
        return Path(env)
    cwd = Path.cwd()
    if (cwd / ".git").exists() or any(cwd.glob("*.project.json")):
        return cwd / ".roblox-luau" / "facts.jsonl"
    return cache_root() / "facts.jsonl"


def load(path: Path) -> list[dict]:
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "id" in row:
                rows.append(row)
    return rows


def save(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


def age_days(row: dict) -> int:
    return int((time.time() - row.get("t", 0)) // 86400)


def fmt(row: dict) -> str:
    stale = f"  STALE({age_days(row)}d): recheck before relying" if age_days(row) > STALE_DAYS else ""
    tags = f" #{' #'.join(row['tags'])}" if row.get("tags") else ""
    return f"#{row['id']} {row['date']} [{row['status']}] {row['claim']} (source: {row['source']}){tags}{stale}"


def command_facts(args) -> None:
    path = Path(args.file) if args.file else default_path()
    rows = load(path)
    action = args.facts_command
    if action == "add":
        if not args.source.strip():
            raise ResearchError("facts add requires --source (a URL, or 'tested-locally' with the command used).")
        row = {
            "id": max([r["id"] for r in rows], default=0) + 1,
            "date": time.strftime("%Y-%m-%d", time.gmtime()),
            "t": time.time(),
            "status": args.status,
            "claim": args.claim.strip(),
            "source": args.source.strip(),
            "tags": args.tag or [],
        }
        rows.append(row)
        save(path, rows)
        print(f"added {fmt(row)}\nledger: {path}")
    elif action == "find":
        q = args.text.lower()
        hits = [r for r in rows if q in json.dumps(r, ensure_ascii=False).lower()]
        for r in hits[: args.limit]:
            print(fmt(r))
        print(f"{len(hits)} match(es) in {path}" if hits else f"No recorded facts match {args.text!r} in {path} (not proof the topic is unverified elsewhere).")
    elif action == "list":
        chosen = [r for r in rows if not args.stale or age_days(r) > args.stale]
        for r in chosen[-args.limit:]:
            print(fmt(r))
        print(f"{len(chosen)} fact(s) in {path}")
    elif action == "rm":
        kept = [r for r in rows if r["id"] != args.id]
        if len(kept) == len(rows):
            raise ResearchError(f"No fact #{args.id} in {path}.")
        save(path, kept)
        print(f"removed #{args.id} from {path}")


def add_facts_parser(subparsers) -> None:
    facts = subparsers.add_parser("facts", help="Ledger of verified facts (claim + source + date) to avoid re-researching.")
    sub = facts.add_subparsers(dest="facts_command", required=True)

    def leaf(name, help_text):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--file", help="Ledger path (default: ./.roblox-luau/facts.jsonl in a project, else the skill cache).")
        p.set_defaults(func=command_facts)
        return p

    a = leaf("add", "Record a verified fact.")
    a.add_argument("claim")
    a.add_argument("--source", required=True, help="URL, or 'tested-locally' plus the command used.")
    a.add_argument("--status", choices=STATUSES, default="unverified")
    a.add_argument("--tag", action="append")
    f = leaf("find", "Search recorded facts.")
    f.add_argument("text")
    f.add_argument("--limit", type=int, default=10)
    l = leaf("list", "List recorded facts.")
    l.add_argument("--stale", type=int, metavar="DAYS", help="Only facts older than DAYS.")
    l.add_argument("--limit", type=int, default=30)
    r = leaf("rm", "Remove a fact by id.")
    r.add_argument("id", type=int)
