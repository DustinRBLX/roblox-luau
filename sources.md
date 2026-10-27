# Sources and evidence

Pick evidence by the claim being made. An open-source commit proves implementation in that repository, not deployment in Roblox.

## Canonical sources

| Resource | Location | Use |
|---|---|---|
| Creator Hub search API | `https://create.roblox.com/docs/api/search?q=...` | Find pages by question or exact API name (`search` command). Check any `error` field. |
| Docs index | `https://create.roblox.com/docs/llms.txt` | Discover official pages (`index creator`). |
| Engine API index | `https://create.roblox.com/docs/reference/engine/llms.txt` | Discover classes, datatypes, enums, globals, libraries (`index engine --find X`). |
| Deprecated inventory | `https://create.roblox.com/docs/reference/engine/deprecated.md` | Legacy APIs and replacements. |
| Single page | exact docs path plus `.md` | Signatures, restrictions, examples (`doc URL --member NAME`). |
| Luau docs | `https://luau.org/` and `https://luau.org/sitemap-index.xml` | Language semantics, libraries, compatibility (`luau-index`, `fetch`). |
| Luau news | `https://luau.org/feed.xml` | Rollout history; check dates and later updates (`news`). |
| Luau RFCs | `https://rfcs.luau.org/` | Design status and rationale (`rfcs`). |
| Upstream repos | `github.com/luau-lang/luau`, `github.com/luau-lang/rfcs` | Source-level evidence, tests, issues, releases. |
| DevForum | `https://devforum.roblox.com/` | Official announcements, release notes, bug reports, community discussion. |
| Lua manuals | `https://www.lua.org/manual/` | Inherited behavior or explicit cross-version comparison only. |
| API dump mirror | `raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/roblox/Mini-API-Dump.json` | Fast signature, security, thread-safety, tag, and replacement lookups (`api` commands). Secondary evidence; the commit date is printed. |
| Roblox type definitions | `raw.githubusercontent.com/JohnnyMorganz/luau-lsp/main/scripts/globalTypes.d.luau` | Types used by `check`; community-maintained, can lag Studio. |
| Tool binaries | GitHub releases of `luau-lang/luau`, `JohnnyMorganz/StyLua`, `JohnnyMorganz/luau-lsp` | Installed by `luau_toolchain.py setup` (official release assets). |

Creator Hub `.md` pages start with front matter that includes `last_updated`; `doc` prints it as `Page-Updated`. Class pages use headings such as `### Method: Class:Member`. Follow URLs from search results exactly; do not extrapolate paths. A later Lua manual proves nothing about Luau.

## Authority by claim

1. Current canonical documentation: documented behavior, public signatures, restrictions.
2. Official implementation and announcements: source-level behavior, release timing, eligibility, known issues. Keep their scopes separate.
3. RFCs and issue threads: rationale, rejected alternatives, defects. Never alone for availability.
4. Mirrors and community references: discovery and diffs; cross-check anything consequential.
5. Blogs, videos, and model memory: leads only.

When sources conflict, work out whether they describe different hosts, solver versions, release channels, dates, or script identities. Prefer the newer canonical source for the user's actual target.

## Lifecycle labels

Use the narrowest label the evidence supports: **proposal**, **accepted design**, **implemented upstream**, **released in tooling** (name tool and version), **beta or experimental** (record eligibility and opt-in), **available but opt-in**, **stable** (only with current evidence; a docs page existing is not enough), **deprecated** (give the replacement), **removed or unsupported**, **unverified**. One feature can differ across standalone Luau, analysis tooling, Studio, and live servers or clients; report those separately.

## Freshness

Retrieve live evidence for "latest/current/still/new", availability, rollout, beta, deprecation, signatures, security or capabilities, platform limits, type-system or require changes, and implementation-specific performance claims. Skip research for stable fundamentals. Record the retrieval date and the page's own date when shown. Never call a result exhaustive because a search found nothing.

## Search tactics

1. Start narrow: a natural-language query, then exact `Class:Method` or the error's key phrase. Use `--engine-only` when articles crowd out API pages.
2. Fetch one exact page with `--member`. If a member seems absent, check inherited members and the relevant global or library page before concluding absence.
3. Check the deprecated inventory for legacy code and migrations.
4. For availability, use current docs plus the single most relevant official rollout source. Add sources only to resolve a specific uncertainty.

## DevForum and community evidence

1. Establish the official baseline first.
2. Use site-restricted web search (`site:devforum.roblox.com` plus the API, error, or feature). Open the actual thread and check the date, later corrections, staff replies, and whether a workaround predates a fix.
3. Cite the exact post; separate official statements from reproductions, speculation, and workarounds.
4. If direct retrieval fails, use web or browser tools and report the limitation. Never turn a failed request into "no results". Use only public, read-only access; no cookies, private endpoints, or credentials.

## Mirrors, dumps, undocumented surface

Treat community API references and client trackers (including the `api` mirror) as secondary: the `api` header shows the mirror commit date; cross-check Creator Hub before consequential claims. A dumped member is not necessarily script-accessible, documented, replicated, or enabled. Never treat fast flags, internal fields, test channels, or undocumented endpoints as stable API. Never recommend bypassing script security, capabilities, or sandboxing.
