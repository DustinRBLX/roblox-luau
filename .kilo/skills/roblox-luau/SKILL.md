---
name: roblox-luau
description: Verify-first Roblox/Luau engineering with a built-in toolchain. Looks up Engine API signatures, security, yielding, thread safety and deprecations cheaply (API dump mirror, Creator Hub docs, Luau docs/news/RFCs) and verifies code by type-checking against Roblox definitions, running and benchmarking pure-logic Luau, formatting with StyLua, and scanning projects for problems. Use for Roblox Studio scripting, writing, debugging, reviewing or migrating Luau, procedural generation and systems design, Parallel Luau, and any "latest", "current", deprecated or beta API question. Skip for trivial syntax questions.
---

# Roblox Luau

Evidence-first help grounded in the real execution environment. Take the cheapest step that is sufficient.

## 1. Cheapest sufficient step

0. Stable syntax, small edits, idioms: answer directly, no tools. State host (server, client, plugin, standalone, parallel) only if it changes the answer; prefer an assumption over a question.
1. Does it exist, signature, yields, parallel-safe, security, deprecated, replacement: `api member` / `api class` / `api find` (one call, tiny output).
2. Behavior, lifecycle, beta vs stable, restrictions, replication: `doc URL --member NAME --brief` (use `search` only if the URL is unknown; drop `--brief` only if detail is missing).
3. Language features, rollout, design status: `luau-index`, `fetch`, `news`, `rfcs`.
4. Code you wrote or changed: `check`, then `run --shim` / `bench` for pure logic. Say "compiled/ran/benchmarked" only if one of these ran.
5. Existing project: `scan PATH --analyze` first, then read only the files it points at.
6. Reuse: `facts find` before researching; `facts add --source ...` after verifying something non-obvious.

## 2. Commands (run from the directory containing this SKILL.md; Python 3 stdlib only)

```bash
# evidence
python3 scripts/roblox_luau.py api member Terrain:WriteVoxels Players.PlayerAdded
python3 scripts/roblox_luau.py api class Terrain --grep Voxel     # also: api find X | api enum Material --find Grass | api deprecated Terrain
python3 scripts/roblox_luau.py search "WriteVoxels" --engine-only --limit 5
python3 scripts/roblox_luau.py doc "<url from search>" --member WriteVoxels --brief
python3 scripts/roblox_luau.py index engine --find SharedTable | luau-index --find compat | fetch URL | news --find solver | rfcs --find require
python3 scripts/roblox_luau.py facts find terrain | facts add "claim" --source URL --status stable
# code (first use auto-installs official luau, StyLua, luau-lsp binaries + Roblox type definitions)
python3 scripts/luau_toolchain.py check src/            # Roblox-aware type check; --std for standalone, --code '...' for a snippet
python3 scripts/luau_toolchain.py run file.luau --shim  # pure logic; shim adds Random/task/Vector3 stand-ins
python3 scripts/luau_toolchain.py bench file.luau --shim --iters 30
python3 scripts/luau_toolchain.py fmt src/ [--write]    # StyLua
python3 scripts/luau_toolchain.py scan .  [--analyze]   # layout, strictness, heuristic findings
```

Cache flags on evidence commands: `--refresh`, `--offline`, `--no-cache`; `cache stats|clear`. `python3 scripts/selftest.py` verifies the skill itself offline.

## 3. Failures

1. **Exit 3 / `NETWORK_UNAVAILABLE`:** that host is blocked (two consecutive timeouts count). Do not retry that host. Other hosts may work: `api` and the toolchain need github.com, raw.githubusercontent.com, release-assets.githubusercontent.com; docs need create.roblox.com, luau.org, rfcs.luau.org. Use web search and fetch for the blocked ones, and say once which domains to allow (then start a new chat).
2. **Exit 2** (bad input, HTTP error, tool missing): fall back to web tools for that query. A failed request never proves a topic does not exist.
3. **Budget:** one Engine API question = one `api member` plus at most one `doc --member --brief`. Never fetch `llms-full.txt`. Do not guess URLs after a 404.
4. **Web fallback:** prefer the en-us Creator Hub page; if search returns only localized copies, fetch one, say it is machine-translated, and trust signatures and tags over translated labels. Class pages are large: fetch once, use only the member needed.

## 4. Evidence rules

1. Official first: Luau docs for language behavior, Creator Hub for Engine APIs (pages show a `last_updated` date), release notes and identified staff posts for rollout and lifecycle. The `api` mirror (Roblox-Client-Tracker), RFCs, repositories, and forum posts are secondary and date-sensitive: use the mirror for signature, security, thread safety and tags, but confirm lifecycle, behavior, beta/stable, and deprecation in Creator Hub before consequential claims.
2. Label status narrowly: proposal, accepted design, implemented upstream, released in tooling, beta/opt-in, stable, deprecated, removed, or unverified. Never infer availability from an RFC, commit, dump, or old "coming soon" post. A `_beta` member in the mirror is not proof of release status.
3. For an Engine member check signature, yielding, security and capabilities, replication, parallel tags (mirror `Safe` = Write Parallel, `ReadSafe` = Read Parallel), and deprecation or replacement.
4. Fast flags, undocumented endpoints, and exploit-only access are not supported capabilities.
5. On conflict, prefer newer canonical evidence and say what remains uncertain.
6. Cite beside the claim (host citation mechanism, else title, URL, retrieval date). Say when status could not be verified. Never fabricate a source, date, API member, error, or test result.

## 5. Code and review

- Match the user's host, script type, annotation mode, and architecture. No unrelated rewrites. Use `luau` fences and current idioms; replace deprecated APIs.
- Coroutines are not parallel execution. A documented member is not callable from every script identity.
- Local tools cover pure logic only: `run` has no `game`, `workspace`, or `Instance`; the shim's `Random` is not Roblox's generator; `bench` times the Luau CLI, not Roblox. `check` uses community-maintained Roblox definitions that can lag Studio. Never present local results as Roblox runtime behavior.
- Review order: correctness, runtime availability and security, yielding and concurrency, replication and trust, resource cleanup, measurable performance, then style. `scan` findings are candidates to verify, never conclusions.

## References (read only the one you need)

- [references/dev-loop.md](references/dev-loop.md): personal-project workflow, testing procedural generation outside Studio, setup per environment, cost habits.
- [references/workflows.md](references/workflows.md): research, experimental-feature, debugging, migration, and review procedures.
- [references/sources.md](references/sources.md): endpoints, authority order, lifecycle labels, search tips.
- [references/luau.md](references/luau.md): language coverage and capability gates, for comprehensive or subtle language answers.
