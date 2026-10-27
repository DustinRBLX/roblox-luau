# Workflows

Use the smallest workflow that fully answers the request. Do not turn a syntax question into a research project.

## General research

1. Extract the host, exact feature or API, requested output, and freshness need.
2. Name the claim type: language semantics, host behavior, API signature, permission, lifecycle, performance, or workaround.
3. For signature, security, yielding, and parallel-safety questions start with `api member`; use docs for behavior and lifecycle. Search the canonical source, then fetch only the relevant page or member (`--member --brief`). Add sources only to cover a remaining scope or conflict.
4. Record URL, retrieval date, update date if shown, and the environment each source describes.
5. Separate facts, inferences, assumptions, and unresolved questions. Put citations beside the claims.

## Current or experimental features

1. Split the question: Does the RFC exist? Does standalone Luau implement it? Does Studio analyze it? Can a live server use it?
2. Check current docs for documented support and restrictions; check official news, release notes, or staff posts for rollout and enablement; use RFCs for rationale only.
3. Inspect the upstream repository only when source-level evidence matters. Check for later corrections to old beta posts.
4. Report status per environment using the lifecycle labels in sources.md, with opt-in needs and limits. Stop when the exact claim is supported; say so if deployment stays unverified.

Never conclude: RFC exists = available; RFC merged = implemented; upstream code = deployed by Roblox; Studio beta = production support; API dump member = scriptable; "coming soon" = released.

## Code creation and refactoring

1. Identify inputs, outputs, failure behavior, host, script type, and client/server or parallel context. Keep the existing annotation mode and architecture.
2. Verify unfamiliar or time-sensitive signatures first. Prefer readable, idiomatic Luau and documented public APIs.
3. Build the smallest complete solution. Handle meaningful failures without hiding errors; clean up connections, instances, and threads when ownership requires it.
4. Keep annotations accurate. Explain any cast beyond silencing a diagnostic. Do not impose strict mode or a framework.
5. A refactor preserves behavior unless a change is requested; call out intentional changes.
6. Verify what you wrote: `check` for types and deprecated APIs, `run --shim` or `bench` for pure-logic code. Report only what actually ran, and name what remains unverified in Studio.

## Debugging

1. Read the exact code, the complete diagnostic, and the call site. Reproduce pure-logic bugs with `run --code` or `run FILE --shim` before theorizing. Ask for a minimal reproduction only if the context cannot support a diagnosis.
2. Classify first: parse error; type diagnostic; runtime nil or type error; API misuse or yielding; security, capability, or network restriction; replication or ownership; scheduling, race, or parallel hazard; performance or resource lifetime.
3. Form a specific hypothesis and the evidence that would confirm it; verify non-fundamental API rules in current docs.
4. Explain the root cause before the fix, make the smallest safe correction, and give a targeted verification step. Never invent error text, line numbers, or passing results. If several causes are plausible, give a short ordered list.

## Lua compatibility and migration

1. Pin down the source dialect and version, destination host, tooling, and libraries; "Lua" alone is not enough.
2. Read the current Luau compatibility page and the relevant Lua manual.
3. Separate syntax, runtime and library semantics, static typing, host globals, and Roblox replication, security, scheduler, and datatypes.
4. Map features as directly compatible, different semantics, requires rewrite, host-dependent, or unverified. Check behavior, not just parsing: numerics, bitwise ops, length and tables, metamethods, coroutines, module loading, environments, errors.
5. Do not infer Luau support from Lua 5.2 to 5.4 docs, or call a Luau extension invalid because Lua 5.1 lacks it. Migrate in steps and test each difference in the real runtime.

## DevForum research

Follow the DevForum rules in sources.md: official baseline first, exact-name searches, read the whole thread for corrections, label evidence accurately ("reported workaround", "confirmed by staff", "unresolved report"), and recheck old workarounds against current docs.

## Code review and performance

Order: correctness; runtime availability (host, identity, security, capabilities); types; scheduling and concurrency; replication and trust; resource lifetime; performance (complexity, allocations, polling, replication, repeated lookups); maintainability. Report by severity with locations and fixes. Use `scan` for candidates and `check` for diagnostics. Do not make speculative performance claims; `bench` gives relative Luau-VM timings only, so recommend profiling in the real runtime and state measurement conditions.

## Response shapes

- **Current capability:** direct answer and environment; lifecycle status; key restrictions; citations beside claims with the check date; secondary reports only when they add a known issue or workaround.
- **Code or debugging:** cause or approach in a line or two; code in a `luau` fence; only relevant assumptions; a verification step and any unverified remainder.
- **Unverified:** say which claim is unverified, what evidence was reachable, and what would resolve it; offer a safe alternative or test instead of a guess.
