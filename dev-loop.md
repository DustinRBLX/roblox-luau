# Dev loop for personal Roblox projects

## Setup by environment

- **Claude Code or local shell:** run `python3 scripts/luau_toolchain.py setup` once. Tools and caches live in `~/.cache/roblox-luau` (override with `ROBLOX_LUAU_HOME`). Tools already on PATH (`luau`, `luau-analyze`, `stylua`, `luau-lsp`) are used first. If the project uses Rojo, generate a sourcemap (`rojo sourcemap --help`) and keep `sourcemap.json` in the project root; `check` passes it to luau-lsp automatically. The facts ledger defaults to `.roblox-luau/facts.jsonl` inside a git or Rojo project.
- **claude.ai chat (code execution):** the sandbox resets between tasks, so tools re-download on first use (about 15 MB, a few seconds). Allow in network settings: github.com, raw.githubusercontent.com, release-assets.githubusercontent.com (tools and `api`), plus create.roblox.com, luau.org, rfcs.luau.org (docs). Without the docs domains, `api` and `check` still work if GitHub is allowed; docs fall back to web search.
- Only Linux x86_64 has been tested; other platforms use best-guess release asset names and prefer tools already on PATH.

## Testing procedural generation and systems logic outside Studio

1. Keep algorithms (noise fields, BSP/WFC layouts, rating/MMR math, economy formulas, loot tables) free of `Instance`/`game` references: data in, data out. A thin adapter writes results to Terrain, Workspace, or DataStores.
2. Write a small test script that calls the module and asserts invariants (bounds, connectivity, determinism for a fixed seed, totals conserved). Run it with `luau_toolchain.py run tests/x.luau --shim`; failures report your own file and line numbers.
3. Same seed gives the same output only inside the shim. The shim's `Random` is not Roblox's generator, so never claim a Roblox seed reproduces a layout; confirm seed behavior in Studio.
4. Compare variants with `bench` (at least 30 iterations, same machine, same shim setting). Treat results as relative evidence, then confirm hot spots with the Roblox profiler before optimizing.
5. Run `check` on module and adapter code; `--!strict` headers make the checker far more useful.

## Terrain notes (mirror, checked 2026-10-25; confirm in Creator Hub before relying)

- `Terrain:ReadVoxels` is tagged parallel-write-safe and `Terrain:WriteVoxels` is not; this matches the Creator Hub tags seen on the Terrain page.
- The mirror also lists `*_beta` voxel members (for example `ReadVoxelsAsync_beta`, `WriteVoxelsAsync_beta`) and `WriteVoxelChannels`. Beta status, availability, and which method is preferred are unverified until the docs say so.

## Facts ledger

`facts add "claim" --source URL --status stable` after verifying something non-obvious; `facts find TEXT` before re-researching. Facts older than 30 days are flagged STALE: recheck before relying on them. Record local test results with `--source "tested-locally: <command>"`.

## Cost habits

- Cheapest tool first (SKILL.md section 1); `--member`, `--brief`, `--limit`, and `--grep` keep output small.
- Repeat lookups are served from cache (docs 24h, search and news 6h, API mirror 12h). `--offline` uses only the cache; stale copies are served automatically when the network is blocked and are labelled.
- `scan --analyze` on a project replaces reading many files to find type errors, deprecated calls, and risky patterns.
- Do not re-run `check` or re-read a file unless the code changed.
