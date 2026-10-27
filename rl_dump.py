"""Compact lookups against the Roblox API dump mirror (MaximumADHD/Roblox-Client-Tracker).

The mirror is SECONDARY evidence: it shows what exists in a recent client build, with security,
thread safety, and tags. It does not prove documented behavior, rollout status, or script access.
Use it as the cheap first check; confirm lifecycle and behavior claims in the Creator Hub.
"""

from __future__ import annotations

import difflib
import json
import xml.etree.ElementTree as ET

from rl_common import ResearchError, cache_note, http_get, local_name

DUMP_URL = "https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/roblox/Mini-API-Dump.json"
DUMP_FEED = "https://github.com/MaximumADHD/Roblox-Client-Tracker/commits/roblox.atom"
THREAD_LABEL = {"Safe": "Safe(Write Parallel)", "ReadSafe": "ReadSafe(Read Parallel)", "Unsafe": "Unsafe"}
KIND_ORDER = ("Property", "Function", "Event", "Callback")
KIND_WORD = {"Property": "prop", "Function": "method", "Event": "event", "Callback": "callback"}
KIND_ARG = {"prop": "Property", "method": "Function", "event": "Event", "callback": "Callback"}
LEGEND = "markers: DEP=deprecated(->replacement) Y=yields Y?=can yield RO/WO=read/write-only NR=not replicated NS=not scriptable par=W/R parallel write/read safe sec=security"


class Dump:
    def __init__(self, data: dict, meta: dict):
        self.classes = {c["Name"]: c for c in data.get("Classes", [])}
        self.enums = {e["Name"]: e for e in data.get("Enums", [])}
        self.version = data.get("Version")
        self.meta = meta

    def chain(self, name: str) -> list[str]:
        out, seen = [], set()
        while name in self.classes and name not in seen:
            out.append(name)
            seen.add(name)
            name = self.classes[name].get("Superclass") or ""
        return out

    def find_member(self, cls: str, member: str):
        for owner in self.chain(cls):
            for m in self.classes[owner].get("Members", []):
                if m["Name"] == member:
                    return owner, m
        return None

    def class_name(self, wanted: str) -> str:
        if wanted in self.classes:
            return wanted
        lower = {n.lower(): n for n in self.classes}
        if wanted.lower() in lower:
            return lower[wanted.lower()]
        close = difflib.get_close_matches(wanted, list(self.classes), n=5, cutoff=0.6)
        hint = f" Close matches: {', '.join(close)}." if close else ""
        raise ResearchError(f"Class {wanted!r} is not in the API dump mirror.{hint} Absence here is not proof it does not exist.")


def commit_info() -> tuple[str, str]:
    try:
        text, _, _ = http_get(DUMP_FEED, max_bytes=2_000_000)
        root = ET.fromstring(text)
        for entry in root.iter():
            if local_name(entry.tag) == "entry":
                kids = {local_name(c.tag): (c.text or "").strip() for c in entry}
                return kids.get("updated", "")[:10] or "unknown", kids.get("title", "")[:60]
    except (ResearchError, ET.ParseError):
        pass
    return "unknown", ""


def load_dump() -> Dump:
    text, _url, _ctype = http_get(DUMP_URL, max_bytes=20_000_000)
    note = cache_note()
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ResearchError("API dump mirror returned invalid JSON.") from exc
    if not isinstance(data, dict) or "Classes" not in data:
        raise ResearchError("API dump mirror has an unexpected shape.")
    date, title = commit_info()
    return Dump(data, {"cache": note, "date": date, "title": title})


def header(dump: Dump) -> str:
    return (
        f"[mirror MaximumADHD/Roblox-Client-Tracker | commit {dump.meta['date']} | {dump.meta['cache']} | "
        "secondary evidence: confirm behavior and lifecycle in Creator Hub]"
    )


def fmt_type(t) -> str:
    if isinstance(t, dict):
        name = str(t.get("Name", "?"))
        return "void" if name in ("null", "void") else name
    return "void" if t is None else str(t)


def fmt_param(p: dict) -> str:
    s = f"{p.get('Name', '?')}: {fmt_type(p.get('Type'))}"
    return s + (f" = {p['Default']}" if "Default" in p else "")


def tag_info(item: dict) -> tuple[list[str], list[str]]:
    plain, repl = [], []
    for t in item.get("Tags", []) or []:
        if isinstance(t, str):
            plain.append(t)
        elif isinstance(t, dict) and t.get("PreferredDescriptorName"):
            repl.append(str(t["PreferredDescriptorName"]))
    return plain, repl


def security_text(m: dict) -> str:
    s = m.get("Security")
    if isinstance(s, dict):
        r, w = s.get("Read"), s.get("Write")
        return str(r) if r == w else f"R={r} W={w}"
    return str(s) if s else ""


def caps_text(m: dict) -> str:
    c = m.get("Capabilities")
    if isinstance(c, list):
        return ",".join(c)
    if isinstance(c, dict):
        r, w = ",".join(c.get("Read") or []), ",".join(c.get("Write") or [])
        return r if r == w else f"R:{r} W:{w}"
    return ""


def markers(m: dict) -> str:
    plain, repl = tag_info(m)
    out: list[str] = []
    if "Deprecated" in plain or repl:
        out.append("DEP" + ("->" + ",".join(repl) if repl else ""))
    for tag, label in (("Yields", "Y"), ("CanYield", "Y?"), ("NoYield", "noY"), ("ReadOnly", "RO"), ("WriteOnly", "WO"), ("NotReplicated", "NR"), ("NotScriptable", "NS")):
        if tag in plain:
            out.append(label)
    ts = m.get("ThreadSafety")
    if ts == "Safe":
        out.append("par=W")
    elif ts == "ReadSafe":
        out.append("par=R")
    sec = security_text(m)
    if sec not in ("", "None"):
        out.append("sec=" + sec)
    return "[" + " ".join(out) + "]" if out else ""


def short_sig(m: dict) -> str:
    kind = m["MemberType"]
    if kind == "Property":
        return f"{m['Name']}: {fmt_type(m.get('ValueType'))}"
    base = f"{m['Name']}({', '.join(fmt_param(p) for p in m.get('Parameters', []))})"
    return base + (f" -> {fmt_type(m.get('ReturnType'))}" if kind in ("Function", "Callback") else "")


def full_sig(owner: str, m: dict) -> str:
    kind = m["MemberType"]
    sep = ":" if kind == "Function" else "."
    tail = {"Event": "  [event]", "Callback": "  [callback]"}.get(kind, "")
    return f"{owner}{sep}{short_sig(m)}{tail}"


def card(owner: str, m: dict, asked: str | None = None) -> str:
    ts = m.get("ThreadSafety")
    plain, repl = tag_info(m)
    bits = [f"thread={THREAD_LABEL.get(ts, ts)}", f"sec={security_text(m) or 'n/a'}"]
    if caps_text(m):
        bits.append(f"caps={caps_text(m)}")
    if plain:
        bits.append("tags=" + ",".join(plain))
    if repl:
        bits.append("replacement=" + ",".join(repl))
    lines = [full_sig(owner, m), "  " + " ".join(bits)]
    if asked and asked != owner:
        lines.append(f"  defined on {owner} (inherited by {asked})")
    return "\n".join(lines)


def cmd_class(dump: Dump, args) -> None:
    name = dump.class_name(args.name)
    chain = dump.chain(name)
    cls = dump.classes[name]
    ctags = [t for t in cls.get("Tags", []) if isinstance(t, str)]
    print(f"{name} : {' > '.join(chain[1:]) or '(root)'}" + (f"  tags={','.join(ctags)}" if ctags else ""))
    owners = chain if args.inherited else [name]
    want = KIND_ARG.get(args.kind) if args.kind else None
    needle = args.grep.lower() if args.grep else None
    rows = []
    for kind in KIND_ORDER:
        for owner in owners:
            for m in dump.classes[owner].get("Members", []):
                plain, repl = tag_info(m)
                if m["MemberType"] != kind or (want and kind != want):
                    continue
                if needle and needle not in m["Name"].lower():
                    continue
                if not args.all and "NotScriptable" in plain:
                    continue
                if args.no_deprecated and ("Deprecated" in plain or repl):
                    continue
                rows.append((kind, owner, m))
    shown = rows[: args.limit]
    if any(markers(m) for _k, _o, m in shown):
        print(LEGEND)
    last = None
    for kind, owner, m in shown:
        if kind != last:
            print(f"-- {KIND_WORD[kind]}s")
            last = kind
        prefix = f"{owner}." if args.inherited and owner != name else ""
        print(f"  {prefix}{short_sig(m)} {markers(m)}".rstrip())
    if len(rows) > len(shown):
        print(f"... {len(rows) - len(shown)} more (narrow with --grep/--kind, or raise --limit)")
    if not rows:
        print("(no members matched; NotScriptable members are hidden unless --all)")


def split_target(target: str) -> tuple[str | None, str]:
    for sep in (":", "."):
        if sep in target:
            cls, member = target.split(sep, 1)
            return cls, member
    return None, target


def cmd_member(dump: Dump, args) -> None:
    for target in args.targets:
        cls, member = split_target(target)
        if cls is None:
            hits = [
                (n, m) for n, c in dump.classes.items() for m in c.get("Members", []) if m["Name"].lower() == member.lower()
            ]
            if not hits:
                print(f"{member}: not found on any class in the mirror (not proof it does not exist).")
                continue
            print(f"{member}: defined on {len(hits)} class(es)" + (" (showing 10)" if len(hits) > 10 else ""))
            for n, m in hits[:10]:
                print(f"  {full_sig(n, m)} {markers(m)}".rstrip())
            continue
        name = dump.class_name(cls)
        found = dump.find_member(name, member)
        if not found:
            lower = {m["Name"].lower(): m["Name"] for o in dump.chain(name) for m in dump.classes[o].get("Members", [])}
            if member.lower() in lower:
                found = dump.find_member(name, lower[member.lower()])
        if not found:
            names = [m["Name"] for o in dump.chain(name) for m in dump.classes[o].get("Members", [])]
            close = difflib.get_close_matches(member, names, n=5, cutoff=0.6)
            hint = f" Close: {', '.join(close)}." if close else ""
            print(f"{name}.{member}: not found on {name} or its superclasses.{hint} Absence is not proof it does not exist.")
            continue
        print(card(found[0], found[1], asked=name))


def cmd_find(dump: Dump, args) -> None:
    q = args.query.lower()
    out: list[str] = []
    want = KIND_ARG.get(args.kind) if args.kind and args.kind in KIND_ARG else None
    if args.kind in (None, "class"):
        out += [f"{n} (class)" for n in dump.classes if q in n.lower()][:10]
    if args.kind in (None, "enum"):
        out += [f"Enum.{n} (enum)" for n in dump.enums if q in n.lower()][:10]
        for n, e in dump.enums.items():
            out += [f"Enum.{n}.{i['Name']}" for i in e.get("Items", []) if q in i["Name"].lower()][:3]
    if args.kind not in ("class", "enum"):
        for n, c in dump.classes.items():
            for m in c.get("Members", []):
                if q in m["Name"].lower() and (not want or m["MemberType"] == want):
                    out.append(f"{n}{':' if m['MemberType'] == 'Function' else '.'}{m['Name']} ({KIND_WORD[m['MemberType']]}) {markers(m)}".rstrip())
    out = list(dict.fromkeys(out))
    for line in out[: args.limit]:
        print(line)
    if len(out) > args.limit:
        print(f"... {len(out) - args.limit} more (raise --limit or narrow with --kind)")
    if not out:
        print(f"No matches for {args.query!r} (not proof it does not exist).")


def cmd_enum(dump: Dump, args) -> None:
    if args.name not in dump.enums:
        lower = {n.lower(): n for n in dump.enums}
        name = lower.get(args.name.lower())
        if not name:
            close = difflib.get_close_matches(args.name, list(dump.enums), n=5, cutoff=0.6)
            raise ResearchError(f"Enum {args.name!r} not in the mirror." + (f" Close: {', '.join(close)}." if close else ""))
    else:
        name = args.name
    items = dump.enums[name].get("Items", [])
    if args.find:
        items = [i for i in items if args.find.lower() in i["Name"].lower()]
    shown = items[: args.limit]
    print(f"Enum.{name} ({len(dump.enums[name].get('Items', []))} items)" + (f", {len(items)} match {args.find!r}" if args.find else ""))
    print(", ".join(f"{i['Name']}={i['Value']}" for i in shown) + (f" ... +{len(items) - len(shown)} more" if len(items) > len(shown) else ""))


def cmd_deprecated(dump: Dump, args) -> None:
    classes = [dump.class_name(args.cls)] if args.cls else list(dump.classes)
    rows = []
    for cname in classes:
        owners = dump.chain(cname) if args.cls else [cname]
        for owner in owners:
            for m in dump.classes[owner].get("Members", []):
                plain, repl = tag_info(m)
                if "Deprecated" in plain or repl:
                    rows.append(f"{owner}{':' if m['MemberType'] == 'Function' else '.'}{m['Name']}" + (f" -> {','.join(repl)}" if repl else ""))
    rows = list(dict.fromkeys(rows))
    print(f"{len(rows)} deprecated member(s)" + (f" on {classes[0]} and superclasses" if args.cls else " across all classes"))
    for r in rows[: args.limit]:
        print("  " + r)
    if len(rows) > args.limit:
        print(f"... {len(rows) - args.limit} more (pass a class name or raise --limit)")


def cmd_info(dump: Dump, args) -> None:
    members = sum(len(c.get("Members", [])) for c in dump.classes.values())
    print(f"classes={len(dump.classes)} enums={len(dump.enums)} members={members} schema-version={dump.version}")
    if dump.meta.get("title"):
        print(f"latest tracker commit: {dump.meta['date']} {dump.meta['title']}")


def command_api(args) -> None:
    dump = load_dump()
    print(header(dump))
    args.api_func(dump, args)


def add_api_parser(subparsers, common) -> None:
    api = subparsers.add_parser("api", help="Compact API dump lookups: cheapest first check for signatures, security, yielding, thread safety, deprecation.")
    sub = api.add_subparsers(dest="api_command", required=True)

    c = sub.add_parser("class", parents=[common], help="List a class's members compactly.")
    c.add_argument("name")
    c.add_argument("--kind", choices=sorted(KIND_ARG))
    c.add_argument("--grep", help="Case-insensitive member-name filter.")
    c.add_argument("--inherited", action="store_true", help="Include superclass members.")
    c.add_argument("--all", action="store_true", help="Include NotScriptable members.")
    c.add_argument("--no-deprecated", action="store_true")
    c.add_argument("--limit", type=int, default=60)
    c.set_defaults(func=command_api, api_func=cmd_class)

    m = sub.add_parser("member", parents=[common], help="Signature card for Class.Member or Class:Method (inheritance-aware).")
    m.add_argument("targets", nargs="+", help="e.g. Terrain:WriteVoxels, Players.PlayerAdded, or a bare member name.")
    m.set_defaults(func=command_api, api_func=cmd_member)

    f = sub.add_parser("find", parents=[common], help="Substring search over classes, members, and enums.")
    f.add_argument("query")
    f.add_argument("--kind", choices=["class", "enum", *sorted(KIND_ARG)])
    f.add_argument("--limit", type=int, default=25)
    f.set_defaults(func=command_api, api_func=cmd_find)

    e = sub.add_parser("enum", parents=[common], help="List an enum's items.")
    e.add_argument("name")
    e.add_argument("--find")
    e.add_argument("--limit", type=int, default=80)
    e.set_defaults(func=command_api, api_func=cmd_enum)

    d = sub.add_parser("deprecated", parents=[common], help="Deprecated members with replacements.")
    d.add_argument("cls", nargs="?", help="Optional class (includes superclasses).")
    d.add_argument("--limit", type=int, default=60)
    d.set_defaults(func=command_api, api_func=cmd_deprecated)

    i = sub.add_parser("info", parents=[common], help="Mirror version, size, and freshness.")
    i.set_defaults(func=command_api, api_func=cmd_info)
