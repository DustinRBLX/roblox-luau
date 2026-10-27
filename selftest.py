#!/usr/bin/env python3
"""Offline self-test for the roblox-luau skill (no network needed; uses synthetic fixtures).

Run: python3 scripts/selftest.py        (add --live to also run tests that use installed Luau binaries)
Exit code 0 = all passed. Fixtures mirror the Creator Hub .md layout and the API dump schema, so a failure
here after a site or format change tells you which parser to update.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ORIG_HOME = os.environ.get("ROBLOX_LUAU_HOME")
os.environ["ROBLOX_LUAU_HOME"] = tempfile.mkdtemp(prefix="rl-selftest-")

import luau_toolchain as tc  # noqa: E402
import rl_common as rc  # noqa: E402
import rl_dump as rd  # noqa: E402
import rl_facts as rf  # noqa: E402
import roblox_luau as rl  # noqa: E402

FAILS = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(("PASS " if cond else "FAIL ") + label + ("" if cond else f"  [{detail[:200]}]"))
    if not cond:
        FAILS.append(label)


def capture(fn, *a, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*a, **kw)
    return buf.getvalue()


def raises(exc, fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except exc:
        return True
    return False


# ---- Creator Hub page parsing (real .md layout: front matter + '### Kind: Class.Member' headings)
PAGE = """---
name: Terrain
last_updated: 2026-06-10T02:17:47Z

# Class: Terrain

### Property: Terrain.WaterColor

Color of water.

### Method: Terrain:ReadVoxels

Reads voxels.

### Method: Terrain:WriteVoxels

Writes voxels.

```lua
terrain:WriteVoxels(region, 4, mats, occ)
```

### Event: Terrain.Changed

Fires.
"""
out = rl.extract_members(PAGE, ["WriteVoxels"])
check("heading-format method extraction", "Writes voxels" in out and "Reads voxels" not in out, out)
check("Class:Method input normalized", "Writes voxels" in rl.extract_members(PAGE, ["Terrain:WriteVoxels"]))
check("property extraction", "Color of water" in rl.extract_members(PAGE, ["WaterColor"]))
check("datatype heading (Instance.new)", "Creates" in rl.extract_members("### Instance.new\n\nCreates it.\n\n### Instance.fromExisting\n\nx\n", ["Instance.new"]))
check("missing member raises", raises(rl.ResearchError, rl.extract_members, PAGE, ["Nope"]))
check("page_updated parsed", rl.page_updated(PAGE) == "2026-06-10T02:17:47Z")
brief = rl.brief_text(PAGE)
check("brief drops code fences and front matter", "terrain:WriteVoxels(region" not in brief and "last_updated" not in brief, brief)
check("normalize trailing slash", rl.normalize_creator_doc("/docs/reference/engine/classes/Terrain/").endswith("Terrain.md"))
check("normalize rejects http and userinfo tricks", raises(rl.ResearchError, rl.normalize_creator_doc, "http://create.roblox.com/docs/x") and raises(rl.ResearchError, rl.normalize_creator_doc, "https://create.roblox.com@evil.example/docs/x"))

# ---- cache behavior
calls = {"n": 0}
rc._network_get = lambda url, **kw: (calls.__setitem__("n", calls["n"] + 1) or (f"body{calls['n']}", url, "text/plain"))
rc.CACHE_STATE.update(enabled=True, refresh=False, offline=False)
u = "https://create.roblox.com/docs/reference/engine/classes/Selftest.md"
a, b = rc.http_get(u)[0], rc.http_get(u)[0]
check("cache hit avoids second request", a == b and calls["n"] == 1 and rc.LAST_META["cache"] == "hit")
rc.CACHE_STATE["refresh"] = True
rc.http_get(u)
check("--refresh re-fetches", calls["n"] == 2)
rc.CACHE_STATE["refresh"] = False
rc.TTL_SECONDS["doc"] = -1


def blocked(url, **kw):
    raise rc.NetworkUnavailable("blocked")


rc._network_get = blocked
check("stale copy served when network blocked", rc.http_get(u)[0].startswith("body") and rc.LAST_META["cache"] == "stale")
rc.CACHE_STATE["offline"] = True
check("--offline without a cached copy raises", raises(rc.ResearchError, rc.http_get, "https://create.roblox.com/docs/never-cached.md"))
rc.CACHE_STATE["offline"] = False
rc.TTL_SECONDS["doc"] = 24 * 3600
check("network failure without cache propagates", raises(rc.NetworkUnavailable, rc.http_get, "https://create.roblox.com/docs/uncached2.md"))

# ---- timeout classification (two timeouts = network unavailable, exit 3)
rc.time.sleep = lambda s: None


def timed_out(req, timeout=None):
    raise urllib.error.URLError(TimeoutError("timed out"))


orig = rc.urllib.request.urlopen
rc.urllib.request.urlopen = timed_out
check("two timeouts classify as NetworkUnavailable", raises(rc.NetworkUnavailable, rc._network_get, "https://create.roblox.com/docs/x.md"))
rc.urllib.request.urlopen = orig

# ---- news: RSS and Atom
RSS = '<rss><channel><item><title>Solver</title><link>https://luau.org/a</link><pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item></channel></rss>'
ATOM = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Old</title><link href="https://luau.org/old"/><published>2025-01-01T00:00:00Z</published></entry><entry><title>New</title><link href="https://luau.org/new"/><updated>2026-06-01T00:00:00Z</updated></entry></feed>'


def news(doc, find=None):
    rl.http_get = lambda url, **k: (doc, url, "application/xml")
    return json.loads(capture(rl.command_news, argparse.Namespace(find=find, limit=5)))


r = news(RSS)
check("news parses RSS", r["matched"] == 1 and r["results"][0]["url"] == "https://luau.org/a")
r = news(ATOM)
check("news parses Atom, newest first", [x["title"] for x in r["results"]] == ["New", "Old"], str(r))
check("empty news result says not proof", "not proof" in news(ATOM, "zzz")["note"])

# ---- API dump formatting (synthetic data in the real schema)
DATA = {
    "Version": 1,
    "Classes": [
        {"Name": "Instance", "Superclass": "<<<ROOT>>>", "Members": [{"MemberType": "Property", "Name": "Name", "ValueType": {"Category": "Primitive", "Name": "string"}, "ThreadSafety": "ReadSafe", "Security": {"Read": "None", "Write": "None"}}]},
        {"Name": "Terrain", "Superclass": "Instance", "Tags": ["NotCreatable"], "Members": [
            {"MemberType": "Function", "Name": "WriteVoxels", "Parameters": [{"Name": "region", "Type": {"Category": "DataType", "Name": "Region3"}}], "ReturnType": {"Category": "Primitive", "Name": "null"}, "ThreadSafety": "Unsafe", "Security": "None", "Capabilities": ["Environment"]},
            {"MemberType": "Function", "Name": "ReadVoxels", "Parameters": [], "ReturnType": {"Category": "Group", "Name": "Tuple"}, "ThreadSafety": "Safe", "Security": "None", "Tags": ["Yields"]},
            {"MemberType": "Function", "Name": "SetCell", "Parameters": [], "ReturnType": {"Category": "Primitive", "Name": "null"}, "ThreadSafety": "Unsafe", "Security": "None", "Tags": ["Deprecated", {"PreferredDescriptorName": "WriteVoxels", "ThreadSafety": "Unknown"}]},
            {"MemberType": "Property", "Name": "Hidden1", "ValueType": {"Category": "Primitive", "Name": "int"}, "ThreadSafety": "Unsafe", "Security": "None", "Tags": ["NotScriptable"]},
        ]},
    ],
    "Enums": [{"Name": "Material", "Items": [{"Name": "Grass", "Value": 1280}, {"Name": "Sand", "Value": 1296}]}],
}
dump = rd.Dump(DATA, {"cache": "cache hit", "date": "2026-10-21", "title": "x"})
ns = argparse.Namespace
card = capture(rd.cmd_member, dump, ns(targets=["Terrain:WriteVoxels", "terrain.name", "Terrain:Nope"]))
check("member card: signature, thread safety, caps", "Terrain:WriteVoxels(region: Region3) -> void" in card and "thread=Unsafe" in card and "caps=Environment" in card, card)
check("member card: inheritance noted", "defined on Instance (inherited by Terrain)" in card, card)
check("missing member says not proof", "not proof" in card)
cls = capture(rd.cmd_class, dump, ns(name="Terrain", kind=None, grep=None, inherited=False, all=False, no_deprecated=False, limit=60))
check("class listing: Y marker, par=W, DEP->replacement, NotScriptable hidden", "[Y par=W]" in cls and "DEP->WriteVoxels" in cls and "Hidden1" not in cls, cls)
check("deprecated list shows replacement", "SetCell -> WriteVoxels" in capture(rd.cmd_deprecated, dump, ns(cls="Terrain", limit=10)))
check("enum find", "Grass=1280" in capture(rd.cmd_enum, dump, ns(name="material", find="gra", limit=10)))
check("find across kinds", "Terrain:WriteVoxels" in capture(rd.cmd_find, dump, ns(query="voxels", kind=None, limit=10)))
check("unknown class raises with hint", raises(rd.ResearchError, dump.class_name, "Terain"))

# ---- facts ledger
ledger = Path(os.environ["ROBLOX_LUAU_HOME"]) / "facts.jsonl"
rf.command_facts(ns(facts_command="add", file=str(ledger), claim="WriteVoxels is Unsafe in the mirror", source="tested-locally: api member", status="tested-locally", tag=["terrain"]))
check("facts add/find", "WriteVoxels" in capture(rf.command_facts, ns(facts_command="find", file=str(ledger), text="voxels", limit=5)))
rows = rf.load(ledger)
rows[0]["t"] -= 40 * 86400
rf.save(ledger, rows)
check("stale facts flagged", "STALE" in capture(rf.command_facts, ns(facts_command="list", file=str(ledger), stale=None, limit=5)))

# ---- project scan heuristics
BAD = '''local s = Instance.new("Part", workspace)
local ok = Instance.new("Part")
local v = store:GetAsync("k")
local safe, r = pcall(function()
	return store:GetAsync("k")
end)
remote.OnServerEvent:Connect(function() end)
while true do
	local x = 1
end
while true do
	task.wait()
end
-- Instance.new("Part", workspace)
--[[ while true do
end ]]
'''
rules = [(no, rule) for no, rule in tc.scan_source(BAD)]
check("scan flags parent arg only on line 1", [n for n, r in rules if r == "INSTANCE_NEW_PARENT"] == [1], str(rules))
check("scan flags unwrapped async on line 3 only", [n for n, r in rules if r == "ASYNC_NO_PCALL"] == [3], str(rules))
check("scan flags remote handler", [n for n, r in rules if r == "REMOTE_HANDLER"] == [7], str(rules))
check("scan flags tight loop but not the yielding one or comments", [n for n, r in rules if r == "TIGHT_LOOP"] == [8], str(rules))

# ---- wrapper generation keeps source intact for any bracket content
src = "print(']]')\nprint(']=]')\nprint(']==]')\n"
w = tc.build_wrapper(src, "we ird$name.luau", tc.SHIM_PATH.read_text(encoding="utf-8"))
check("wrapper picks a safe long-bracket level", "[===[" in w and '"=we_ird_name.luau"' in w)

# ---- live tests (installed binaries only; never downloads)
live = argparse.ArgumentParser()
live.add_argument("--live", action="store_true")
if live.parse_known_args()[0].live:
    if ORIG_HOME is None:
        os.environ.pop("ROBLOX_LUAU_HOME", None)
    else:
        os.environ["ROBLOX_LUAU_HOME"] = ORIG_HOME
    luau = tc.find_tool("luau")
    if not luau:
        print("SKIP live tests: luau not installed (run luau_toolchain.py setup)")
    else:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.luau"
            f.write_text(src + "local r = Random.new(5)\nprint(r:NextInteger(1, 1), typeof(Vector3.new(1,2,3).Magnitude))\nerror('boom')\n", encoding="utf-8")
            wrapper = Path(d) / "w.luau"
            wrapper.write_text(tc.build_wrapper(f.read_text(encoding="utf-8"), "t.luau", tc.SHIM_PATH.read_text(encoding="utf-8")), encoding="utf-8")
            p = subprocess.run([luau, str(wrapper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            check("live: shim wrapper runs, prints source text intact, reports user line numbers", "]=]" in p.stdout and "1\tnumber" in p.stdout and "t.luau:6" in p.stderr, p.stdout + p.stderr)

print(f"\n{'ALL PASSED' if not FAILS else str(len(FAILS)) + ' FAILED: ' + ', '.join(FAILS)}")
raise SystemExit(1 if FAILS else 0)
