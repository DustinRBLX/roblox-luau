#!/usr/bin/env python3
"""Local Luau toolchain for Roblox projects (stdlib only).

setup/status  install official release binaries (luau, luau-analyze, StyLua, luau-lsp) + Roblox type definitions
run/bench     execute or time pure-logic Luau, optionally with assets/shim.luau (Random, task, Vector3, ...)
check         type-check with Roblox definitions (luau-lsp) or standalone Luau; compact, error-first output
fmt           StyLua check/write
scan          project overview + heuristic findings (candidates to verify, never conclusions)

Exit codes: 0 ok, 1 findings/failed script, 2 usage or tool error, 3 network unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rl_common import USER_AGENT, NetworkUnavailable, ResearchError, cache_root, is_network_block, is_timeout, utc_now  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent
SHIM_PATH = SKILL_DIR / "assets" / "shim.luau"
DEFS_URL = "https://raw.githubusercontent.com/JohnnyMorganz/luau-lsp/main/scripts/globalTypes.d.luau"
DEFS_TTL = 7 * 86400
LUA_EXT = (".lua", ".luau")
SKIP_DIRS = {".git", "node_modules", "Packages", "DevPackages", "ServerPackages", ".roblox-luau", "__pycache__"}
ERROR_KINDS = {"TypeError", "SyntaxError", "ModuleError"}
NOISE_KINDS = {"LocalUnused", "FunctionUnused", "ImportUnused"}
TESTED = ("Linux", "x86_64")
ROOT_MARKERS = ("default.project.json", ".luaurc", "wally.toml", "stylua.toml", "selene.toml", "rokit.toml", "aftman.toml", ".git")

PACKAGES = {
    "luau": {
        "repo": "luau-lang/luau",
        "bins": ("luau", "luau-analyze", "luau-compile"),
        "assets": {("Linux", "x86_64"): "luau-ubuntu.zip", ("Darwin", "*"): "luau-macos.zip", ("Windows", "*"): "luau-windows.zip"},
    },
    "stylua": {
        "repo": "JohnnyMorganz/StyLua",
        "bins": ("stylua",),
        "assets": {
            ("Linux", "x86_64"): "stylua-linux-x86_64.zip",
            ("Linux", "aarch64"): "stylua-linux-aarch64.zip",
            ("Darwin", "aarch64"): "stylua-macos-aarch64.zip",
            ("Darwin", "x86_64"): "stylua-macos-x86_64.zip",
            ("Windows", "*"): "stylua-windows-x86_64.zip",
        },
    },
    "luau-lsp": {
        "repo": "JohnnyMorganz/luau-lsp",
        "bins": ("luau-lsp",),
        "assets": {("Linux", "x86_64"): "luau-lsp-linux-x86_64.zip", ("Darwin", "*"): "luau-lsp-macos.zip", ("Windows", "*"): "luau-lsp-win64.zip"},
    },
}
BIN_TO_PKG = {b: pkg for pkg, spec in PACKAGES.items() for b in spec["bins"]}


# ----------------------------------------------------------------------------- installation


def tools_root() -> Path:
    path = cache_root() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def exe(name: str) -> str:
    return name + (".exe" if platform.system() == "Windows" else "")


def pkg_dir(pkg: str) -> Path:
    return tools_root() / "bin" / pkg


def find_tool(name: str):
    pkg = BIN_TO_PKG.get(name)
    if pkg:
        candidate = pkg_dir(pkg) / exe(name)
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def asset_for(pkg: str):
    system = platform.system()
    mach = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
    table = PACKAGES[pkg]["assets"]
    return table.get((system, mach)) or table.get((system, "*"))


def load_manifest() -> dict:
    try:
        return json.loads((tools_root() / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_manifest(data: dict) -> None:
    (tools_root() / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def download(url: str, dest: Path, max_bytes: int = 120_000_000) -> None:
    host = urllib.parse.urlsplit(url).hostname
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
            total = 0
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ResearchError(f"Download exceeded {max_bytes} bytes: {url}")
                fh.write(chunk)
    except urllib.error.HTTPError as exc:
        if exc.headers.get("x-deny-reason"):
            raise NetworkUnavailable(f"{host} denied by network policy ({exc.headers['x-deny-reason']}); allow github.com, raw.githubusercontent.com and release-assets.githubusercontent.com") from exc
        raise ResearchError(f"HTTP {exc.code} downloading {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if is_network_block(reason) or is_timeout(exc):
            raise NetworkUnavailable(f"{host} is unreachable ({reason}). Allow github.com, raw.githubusercontent.com and release-assets.githubusercontent.com in the network settings, or install the tools yourself and put them on PATH.") from exc
        raise ResearchError(f"Could not download {url}: {reason}") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


def resolve_latest_tag(url: str):
    try:
        urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT}), timeout=20)
    except urllib.error.HTTPError as exc:
        m = re.search(r"/releases/download/([^/]+)/", exc.headers.get("Location", ""))
        return m.group(1) if m else None
    except (urllib.error.URLError, OSError):
        return None
    return None


def install(pkg: str, force: bool = False) -> str:
    spec, dest = PACKAGES[pkg], pkg_dir(pkg)
    if not force and all((dest / exe(b)).exists() for b in spec["bins"]):
        return "present"
    asset = asset_for(pkg)
    if not asset:
        raise ResearchError(f"No known {pkg} release asset for {platform.system()}/{platform.machine()}; install it manually and put it on PATH.")
    url = f"https://github.com/{spec['repo']}/releases/latest/download/{asset}"
    tag = resolve_latest_tag(url)
    print(f"installing {pkg} {tag or ''} ({asset})...", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / asset
        download(url, archive)
        if not zipfile.is_zipfile(archive):
            raise ResearchError(f"{asset} is not a zip archive; the release layout may have changed.")
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        root = dest.resolve()
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                target = (dest / info.filename).resolve()
                if target != root and root not in target.parents:
                    raise ResearchError(f"Unsafe path in archive: {info.filename}")
            zf.extractall(dest)
    for b in spec["bins"]:
        path = dest / exe(b)
        if not path.exists():
            nested = next(dest.rglob(exe(b)), None)
            if nested is None:
                raise ResearchError(f"{b} is missing from {asset}; the release layout may have changed.")
            shutil.move(str(nested), str(path))
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    manifest = load_manifest()
    manifest[pkg] = {"tag": tag, "asset": asset, "sha256": sha, "installed": utc_now(), "platform": f"{platform.system()}/{platform.machine()}"}
    save_manifest(manifest)
    return f"installed {tag or ''}".strip()


def ensure(name: str) -> str:
    path = find_tool(name)
    if path:
        return path
    install(BIN_TO_PKG[name])
    path = find_tool(name)
    if not path:
        raise ResearchError(f"{name} not found; run `luau_toolchain.py setup` or install it and put it on PATH.")
    return path


def defs_path() -> Path:
    return tools_root() / "defs" / "globalTypes.d.luau"


def ensure_defs(force: bool = False) -> Path:
    path = defs_path()
    if path.exists() and not force and time.time() - path.stat().st_mtime < DEFS_TTL:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        download(DEFS_URL, tmp, max_bytes=20_000_000)
        os.replace(tmp, path)
    except NetworkUnavailable:
        if path.exists():
            print("warning: network unavailable; using cached Roblox type definitions", file=sys.stderr)
            return path
        raise
    return path


# ----------------------------------------------------------------------------- helpers


def run_cmd(cmd, timeout: int, cwd=None):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace")
        return 124, out, f"timed out after {timeout}s (possible infinite loop; use --timeout to extend)"
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


WRAPPER_NOISE = re.compile(r"(wrapper|bench)\.luau(:\d+)?$|^\[C\] function error$")


def show(out: str, err: str, max_lines: int) -> None:
    lines = out.rstrip("\n").splitlines()
    if lines:
        print("\n".join(lines[:max_lines]))
        if len(lines) > max_lines:
            print(f"... {len(lines) - max_lines} more output lines (raise --max-lines)")
    err_lines = [l for l in err.splitlines() if l.strip() and not l.startswith("[INFO]") and not WRAPPER_NOISE.search(l.strip())]
    if err_lines:
        print("[stderr]")
        print("\n".join(err_lines[:40]))
        if len(err_lines) > 40:
            print(f"... {len(err_lines) - 40} more stderr lines")


def collect(paths) -> list:
    files = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.suffix in LUA_EXT and f.is_file() and not any(part in SKIP_DIRS for part in f.relative_to(p).parts[:-1]):
                    files.append(f)
        elif p.is_file():
            files.append(p)
        else:
            raise ResearchError(f"Path not found: {raw}")
    return list(dict.fromkeys(files))


def find_root(path: Path) -> Path:
    start = path.resolve()
    start = start if start.is_dir() else start.parent
    for cand in [start, *start.parents]:
        if any((cand / m).exists() for m in ROOT_MARKERS) or any(cand.glob("*.project.json")):
            return cand
    return start


def source_for(file, code, tmp: str) -> Path:
    if code is not None:
        p = Path(tmp) / "snippet.luau"
        p.write_text(code, encoding="utf-8")
        return p
    if not file:
        raise ResearchError("Provide a FILE or --code.")
    p = Path(file)
    if not p.is_file():
        raise ResearchError(f"File not found: {file}")
    return p


def build_wrapper(src: str, chunk: str, shim_text=None, bench=None) -> str:
    """Wrap user source in a loadstring chunk so line numbers/chunk names survive; optionally add shim and a timing loop."""
    body = src if src.endswith("\n") else src + "\n"
    n = 0
    while f"]{'=' * n}]" in body:
        n += 1
    eq = "=" * n
    chunk = re.sub(r"[^\w.\-]", "_", chunk)
    # Luau resolves global imports (print, ...) when a chunk is loaded, so bench silences output BEFORE loadstring.
    quiet = ["local __print = print", "print = function() end", "warn = function() end"] if bench is not None else []
    parts = [shim_text or "", *quiet, f"local __src = [{eq}[\n{body}]{eq}]", f'local __f, __e = loadstring(__src, "={chunk}")', "if not __f then error(__e, 0) end"]
    flush = "__shim_flush()" if shim_text else ""
    if bench is None:
        parts += ["local __ok, __err = xpcall(__f, debug.traceback)", "if not __ok then error(__err, 0) end", flush]
    else:
        iters, warm = bench
        parts += [
            f"local function __run() __f() {flush} end",
            f"for _ = 1, {warm} do __run() end",
            f"local __t = table.create({iters})",
            f"for i = 1, {iters} do local s = os.clock() __run() __t[i] = (os.clock() - s) * 1000 end",
            "table.sort(__t)",
            "local function __q(p) return __t[math.max(1, math.ceil(#__t * p))] end",
            "local __sum = 0",
            "for _, v in __t do __sum += v end",
            f'__print(string.format("bench n=%d warmup={warm} min=%.4fms median=%.4fms mean=%.4fms p95=%.4fms max=%.4fms", #__t, __t[1], __q(0.5), __sum / #__t, __q(0.95), __t[#__t]))',
        ]
    return "\n".join(parts) + "\n"


# ----------------------------------------------------------------------------- commands


def cmd_setup(args) -> int:
    want = [w.strip() for w in args.only.split(",")] if args.only else ["luau", "stylua", "luau-lsp", "defs"]
    for item in want:
        if item == "defs":
            print(f"defs: {ensure_defs(args.force)}")
        elif item in PACKAGES:
            print(f"{item}: {install(item, args.force)}")
        else:
            raise ResearchError(f"Unknown component {item!r}; choose from luau, stylua, luau-lsp, defs.")
    if (platform.system(), platform.machine().lower().replace("amd64", "x86_64")) != TESTED:
        print(f"note: only {TESTED[0]}/{TESTED[1]} has been tested; other platforms use best-guess asset names.")
    return 0


def cmd_status(args) -> int:
    manifest = load_manifest()
    print(f"tools root: {tools_root()}  platform: {platform.system()}/{platform.machine()}")
    for name in ("luau", "luau-analyze", "luau-compile", "stylua", "luau-lsp"):
        path = find_tool(name)
        tag = manifest.get(BIN_TO_PKG[name], {}).get("tag") if path and str(tools_root()) in path else None
        print(f"  {name}: {path or 'missing'}" + (f" ({tag})" if tag else (" (from PATH)" if path else "")))
    d = defs_path()
    print(f"  roblox type definitions: {'age ' + str(int((time.time() - d.stat().st_mtime) // 86400)) + 'd' if d.exists() else 'missing'}")
    return 0


def cmd_run(args) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = source_for(args.file, args.code, tmp)
        luau = ensure("luau")
        script = path
        if args.shim:
            script = Path(tmp) / "wrapper.luau"
            script.write_text(build_wrapper(path.read_text(encoding="utf-8"), path.name, SHIM_PATH.read_text(encoding="utf-8")), encoding="utf-8")
        cmd = [luau] + (["--codegen"] if args.native else []) + [f"-O{args.opt}", str(script)] + (["-a", *args.script_args] if args.script_args else [])
        t0 = time.time()
        rc, out, err = run_cmd(cmd, args.timeout)
        show(out, err, args.max_lines)
        print(f"[exit {rc} in {time.time() - t0:.2f}s{' | shim' if args.shim else ''}{' | --codegen' if args.native else ''}]")
        return 0 if rc == 0 else 1


def cmd_bench(args) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = source_for(args.file, args.code, tmp)
        luau = ensure("luau")
        shim = SHIM_PATH.read_text(encoding="utf-8") if args.shim else None
        script = Path(tmp) / "bench.luau"
        script.write_text(build_wrapper(path.read_text(encoding="utf-8"), path.name, shim, bench=(args.iters, args.warmup)), encoding="utf-8")
        cmd = [luau] + (["--codegen"] if args.native else []) + ["-O2", str(script)]
        rc, out, err = run_cmd(cmd, args.timeout)
        show(out, err, 40)
        print("[timing is Luau-VM time on this machine: compare variants with each other; it does not predict Roblox client/server performance]")
        return 0 if rc == 0 else 1


DIAG_RE = re.compile(r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)(?:-\d+)?\): (?P<kind>\w+): (?P<msg>.*)$")


def analyze(files, std: bool, strict: bool, cwd):
    if std:
        base = [ensure("luau-analyze")] + (["--mode=strict"] if strict else [])
        label = "standalone Luau; no Roblox globals"
    else:
        lsp, defs = ensure("luau-lsp"), ensure_defs()
        base = [lsp, "analyze", f"--definitions={defs}"]
        sm = Path(cwd) / "sourcemap.json"
        if sm.exists():
            base += ["--sourcemap", str(sm)]
        label = f"Roblox definitions, {int((time.time() - defs.stat().st_mtime) // 86400)}d old"
    diags, other = [], []
    for i in range(0, len(files), 60):
        _rc, out, err = run_cmd(base + [str(f) for f in files[i : i + 60]], 300, cwd)
        for line in (out + "\n" + err).splitlines():
            line = line.strip()
            if not line or line.startswith("[INFO]"):
                continue
            m = DIAG_RE.match(line)
            (diags if m else other).append(m.groupdict() if m else line)
    return diags, other, label


def report(diags, other, label, nfiles, max_lines, show_all) -> int:
    errors = [d for d in diags if d["kind"] in ERROR_KINDS]
    warns = [d for d in diags if d["kind"] not in ERROR_KINDS]
    hidden = [] if show_all else [d for d in warns if d["kind"] in NOISE_KINDS]
    warns = [d for d in warns if d not in hidden]
    print(f"check [{label}] {nfiles} file(s): {len(errors)} error(s), {len(warns)} warning(s)" + (f", {len(hidden)} unused-symbol hint(s) hidden (--all)" if hidden else ""))
    rows = errors + warns
    for d in rows[:max_lines]:
        print(f"{d['file']}:{d['line']}:{d['col']} {d['kind']}: {d['msg']}")
    if len(rows) > max_lines:
        print(f"... {len(rows) - max_lines} more (raise --max-lines); by kind: " + ", ".join(f"{k} x{v}" for k, v in Counter(d['kind'] for d in rows).most_common(6)))
    for line in other[:5]:
        print(f"[tool output] {line}")
    return 1 if errors else 0


def cmd_check(args) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        if args.code is not None:
            files, cwd = [source_for(None, args.code, tmp)], Path(tmp)
        else:
            files = collect(args.paths or ["."])
            if not files:
                raise ResearchError("No .lua/.luau files found.")
            cwd = find_root(files[0])
        diags, other, label = analyze(files, args.std, args.strict, cwd)
        return report(diags, other, label, len(files), args.max_lines, args.all)


def cmd_fmt(args) -> int:
    files = collect(args.paths)
    if not files:
        raise ResearchError("No .lua/.luau files found.")
    stylua, root = ensure("stylua"), find_root(files[0])
    rc, out, err = run_cmd([stylua, "--check", *[str(f) for f in files]], 300, root)
    if rc not in (0, 1):
        raise ResearchError(f"stylua failed: {err.strip()[:300]}")
    need = list(dict.fromkeys(re.findall(r"^Diff in (.+?):$", out, re.M)))
    if args.write and need:
        rc2, _o, err2 = run_cmd([stylua, *need], 300, root)
        if rc2 != 0:
            raise ResearchError(f"stylua failed while writing: {err2.strip()[:300]}")
        print(f"formatted {len(need)} of {len(files)} file(s)")
        return 0
    print(f"{len(need)} of {len(files)} file(s) need formatting" + (": " + ", ".join(need[:8]) + (" ..." if len(need) > 8 else "") if need else ""))
    if args.diff and need:
        print("\n".join(out.splitlines()[: args.max_lines]))
    return 1 if need else 0


# ----------------------------------------------------------------------------- scan (heuristics)

RULES = {
    "INSTANCE_NEW_PARENT": ("hint", "Instance.new with a parent argument; docs discourage it for performance, set Parent last (https://create.roblox.com/docs/reference/engine/datatypes/Instance.md)"),
    "ASYNC_NO_PCALL": ("warn", "DataStore/HttpService-style request with no pcall in the preceding 3 lines; requests can fail (verify wrapping)"),
    "TIGHT_LOOP": ("warn", "while-true loop with no yield, break, or return in its body; verify it cannot freeze the thread"),
    "REMOTE_HANDLER": ("info", "server remote handler; confirm it validates argument types and sender authority"),
}
YIELDERS = re.compile(r"\bwait\s*\(|task\.wait|:Wait\s*\(|\bbreak\b|\breturn\b|coroutine\.yield|:WaitForChild\s*\(|\w+Async\s*\(|\.Heartbeat|\.Stepped|\.RenderStepped|\.PreRender|\.PreSimulation|\.PostSimulation|\.PreAnimation")
ASYNC_CALL = re.compile(r":\s*(GetAsync|SetAsync|UpdateAsync|IncrementAsync|RemoveAsync|PostAsync|RequestAsync|GetSortedAsync)\s*\(")
NEW_PARENT = re.compile(r"\bInstance\.new\(\s*[^(),]+,")
REMOTE = re.compile(r"\.(OnServerEvent|OnServerInvoke)\b")
HEADER = re.compile(r"^--!(strict|nonstrict|nocheck|native)\b", re.M)


def code_lines(text: str):
    """Yield (lineno, code) with comments blanked. Heuristic: strings containing '--' are mishandled."""
    block_close = None
    for no, line in enumerate(text.splitlines(), 1):
        if block_close:
            end = line.find(block_close)
            if end == -1:
                yield no, ""
                continue
            line, block_close = line[end + len(block_close) :], None
        m = re.search(r"--\[(=*)\[", line)
        if m:
            close = "]" + m.group(1) + "]"
            end = line.find(close, m.end())
            if end == -1:
                block_close, line = close, line[: m.start()]
            else:
                line = line[: m.start()] + line[end + len(close) :]
        else:
            line = re.sub(r"--.*$", "", line)
        yield no, line


def scan_source(text: str) -> list:
    lines = list(code_lines(text))
    codes = [c for _, c in lines]
    found = []
    for idx, (no, code) in enumerate(lines):
        if NEW_PARENT.search(code):
            found.append((no, "INSTANCE_NEW_PARENT"))
        if ASYNC_CALL.search(code) and "pcall" not in " ".join(codes[max(0, idx - 3) : idx + 1]):
            found.append((no, "ASYNC_NO_PCALL"))
        if REMOTE.search(code):
            found.append((no, "REMOTE_HANDLER"))
        if re.match(r"\s*while\s+true\s+do\b", code):
            indent = len(code) - len(code.lstrip())
            end = next((j for j in range(idx + 1, len(lines)) if codes[j].strip() and len(codes[j]) - len(codes[j].lstrip()) <= indent and codes[j].lstrip().startswith("end")), None)
            if end is not None and not YIELDERS.search("\n".join(codes[idx + 1 : end])):
                found.append((no, "TIGHT_LOOP"))
    return found


def rojo_layout(root: Path) -> list:
    project = root / "default.project.json"
    if not project.exists():
        project = next(iter(sorted(root.glob("*.project.json"))), None)
    if not project:
        return []
    try:
        tree = json.loads(project.read_text(encoding="utf-8")).get("tree", {})
    except (OSError, ValueError):
        return [f"{project.name}: unreadable"]
    rows = [f"{project.name}"]

    def walk(name, node, depth):
        if not isinstance(node, dict):
            return
        path = node.get("$path")
        if path or depth <= 1:
            rows.append("  " * (depth + 1) + name + (f" -> {path}" if path else ""))
        if depth < 3:
            for k, v in node.items():
                if not k.startswith("$"):
                    walk(k, v, depth + 1)

    walk(tree.get("$className", "tree") if isinstance(tree, dict) else "tree", tree, 0)
    return rows[:16]


def cmd_scan(args) -> int:
    target = Path(args.path)
    root = find_root(target) if target.exists() else target
    files = collect([args.path])
    print(f"scan {root} : {len(files)} Luau file(s)")
    for row in rojo_layout(root):
        print("  " + row)
    present = [m for m in ("default.project.json", "wally.toml", "rokit.toml", "aftman.toml", "selene.toml", "stylua.toml", ".luaurc", "sourcemap.json") if (root / m).exists()]
    print("config: " + (", ".join(present) if present else "none of the usual project files found"))
    modes = Counter()
    findings = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = HEADER.search(text[:400])
        modes[m.group(1) if m else "(no header)"] += 1
        findings += [(str(f), no, rule) for no, rule in scan_source(text)]
    print("strictness: " + ", ".join(f"{k} x{v}" for k, v in modes.most_common()))
    counts = Counter(rule for _f, _n, rule in findings)
    if counts:
        print("heuristic findings (candidates to verify): " + ", ".join(f"{r} x{c}" for r, c in counts.most_common()))
        order = {"warn": 0, "hint": 1, "info": 2}
        findings.sort(key=lambda x: (order[RULES[x[2]][0]], x[0], x[1]))
        shown = [x for x in findings if RULES[x[2]][0] != "info"][: args.max_findings]
        for fp, no, rule in shown:
            print(f"  {fp}:{no} {rule}")
        for rule in counts:
            print(f"  {rule}: {RULES[rule][1]}")
    else:
        print("heuristic findings: none (heuristics are narrow; this is not a clean bill of health)")
    if args.analyze and files:
        diags, other, label = analyze(files, False, False, root)
        report(diags, other, label, len(files), 15, False)
    return 0


# ----------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local Luau toolchain: setup, run, bench, check, fmt, scan.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="Install luau, stylua, luau-lsp and the Roblox type definitions.")
    s.add_argument("--only", help="Comma list: luau,stylua,luau-lsp,defs")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_setup)

    sub.add_parser("status", help="Show installed tools.").set_defaults(func=cmd_status)

    r = sub.add_parser("run", help="Run Luau (pure logic). --shim adds Random/task/Vector3 stand-ins.")
    r.add_argument("file", nargs="?")
    r.add_argument("script_args", nargs="*")
    r.add_argument("--code", help="Run an inline snippet instead of a file.")
    r.add_argument("--shim", action="store_true")
    r.add_argument("--native", action="store_true", help="Use --codegen (native compilation).")
    r.add_argument("--opt", type=int, choices=(0, 1, 2), default=1)
    r.add_argument("--timeout", type=int, default=20)
    r.add_argument("--max-lines", type=int, default=80)
    r.set_defaults(func=cmd_run)

    b = sub.add_parser("bench", help="Time a script in-VM: n runs after warmup; reports min/median/mean/p95/max.")
    b.add_argument("file", nargs="?")
    b.add_argument("--code")
    b.add_argument("--iters", type=int, default=30)
    b.add_argument("--warmup", type=int, default=3)
    b.add_argument("--shim", action="store_true")
    b.add_argument("--native", action="store_true")
    b.add_argument("--timeout", type=int, default=120)
    b.set_defaults(func=cmd_bench)

    c = sub.add_parser("check", help="Type-check files/dirs (Roblox definitions by default).")
    c.add_argument("paths", nargs="*")
    c.add_argument("--code", help="Check an inline snippet.")
    c.add_argument("--std", action="store_true", help="Standalone Luau analysis (no Roblox globals).")
    c.add_argument("--strict", action="store_true", help="With --std: default to strict mode.")
    c.add_argument("--all", action="store_true", help="Also show unused-symbol hints.")
    c.add_argument("--max-lines", type=int, default=40)
    c.set_defaults(func=cmd_check)

    f = sub.add_parser("fmt", help="StyLua: report (default) or --write files needing formatting.")
    f.add_argument("paths", nargs="+")
    f.add_argument("--write", action="store_true")
    f.add_argument("--diff", action="store_true")
    f.add_argument("--max-lines", type=int, default=40)
    f.set_defaults(func=cmd_fmt)

    sc = sub.add_parser("scan", help="Project overview + heuristic findings.")
    sc.add_argument("path", nargs="?", default=".")
    sc.add_argument("--max-findings", type=int, default=30)
    sc.add_argument("--analyze", action="store_true", help="Also run the Roblox-aware type check.")
    sc.set_defaults(func=cmd_scan)
    return p


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args()
    try:
        return args.func(args) or 0
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
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
