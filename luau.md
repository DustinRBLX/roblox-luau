# Luau coverage and capability gates

A completeness checklist for language answers, code generation, debugging, and review. Not a substitute for current documentation; verify environment-dependent and evolving features in the target host.

## Execution environments

Establish which applies before answering:

| Environment | What can change the answer |
|---|---|
| Standalone Luau | Release, compile options, CLI entry point, host-registered globals and libraries. |
| Analysis or editor tooling | Analyzer or language server, solver, configuration, definition files, aliases. |
| Custom embedding | Which libraries and sandbox rules the embedder exposed; userdata, modules, threads. |
| Roblox server Script | Security context, service permissions, yielding, server authority. |
| Roblox client LocalScript | Replicated state visible, what must stay server-authoritative, client restrictions. |
| ModuleScript | Which side runs it, who requires it, shared state, init order, cyclic requires. |
| Studio plugin or command bar | Studio-only APIs, plugin security, capabilities, data-model permissions. |
| Parallel Luau | Which Actor owns the data, what is parallel-safe, how communication and sync happen. |

Standalone Luau and Roblox do not expose identical libraries or defaults, and an analyzer does not accept exactly what a runtime executes.

## Language areas to cover

Use these when a request is comprehensive or a problem is subtle.

- **Syntax:** reserved words, literals and numeric edge cases, operators and precedence, compound assignment, `continue`, if-expressions, generalized iteration, varargs, multiple returns, attributes. Use the official grammar for exact syntax questions, and check parser, analyzer, and runtime support separately for newer syntax.
- **Values and semantics:** truthiness, equality, ordering, conversion, concatenation, length, non-finite numbers; closures and upvalues; errors, stack traces, protected calls, yield across boundaries; garbage collection and weak references. Do not import version-specific Lua numeric or metamethod semantics without checking Luau's compatibility page.
- **Tables and metatables:** arrays versus dictionaries, holes, length boundaries, iteration order, cycles, metatable lookup and protection, raw operations, and the difference between runtime behavior and the static table model. Roblox instances and datatypes are host objects, not tables.
- **Functions, errors, modules:** return contracts, `error` and `assert`, propagation across module or scheduler boundaries, require resolution, aliases, caching, cyclic dependencies. Require semantics have evolved and differ between runtime, tooling, and Roblox ModuleScripts, so verify the active behavior.
- **Coroutines and scheduling:** coroutine lifecycle and errors, the host scheduler and `task` library as distinct from raw coroutines, cancellation, reentrancy, races, Actor boundaries, and per-member parallel-access annotations. "Coroutine", "thread", "task", and "parallel execution" are not synonyms.
- **Gradual typing:** annotation modes, solver selection, aliases, optionals, unions, intersections, generics, type packs, table shapes, refinement and narrowing, casts, newer type features (verify status). Separate four outcomes: parser accepts, analyzer accepts, runtime executes, runtime matches the static contract. A cast changes analysis only; it converts nothing at runtime.
- **Libraries and native data:** base, string, table, math, utf8, bit32, buffer, vector, coroutine, debug, and host libraries such as `task`. Do not assume an open-source library is exposed by every embedding or that Roblox globals exist in the standalone CLI.
- **Tooling and embedding:** compiler, analyzer, linter, language server, formatter roles; `.luaurc`, aliases, definition files, sourcemaps; C/C++ embedding and compile options; open-source defaults versus Roblox defaults. Identify third-party language servers separately.

## Roblox integration checks

Check the relevant intersection of: data model (instances, services, datatypes, enums, inheritance); script context and identity; replication and streaming (visibility, timing, server authority, remote validation, missing descendants); scheduling (yielding, event order, deferral); parallel execution (Actors, sync points, member-level read/write rules); security and capabilities; lifecycle (deprecation, beta, replacement). Read the member's page and class-level caveats; presence in an index does not make a member usable.

## Capability gates ("Can Luau or Roblox do this?")

Apply each relevant gate and report the first that fails, with a supported alternative:

1. Syntax: does the target parser support it?
2. Analysis: does the active solver and configuration accept it?
3. Runtime: is the function, library, or datatype present?
4. Permission: can this script identity and capability context call it?
5. Topology: valid on this server, client, plugin, Actor, or replicated object?
6. Scheduling: can it yield or run in parallel here without breaking order or isolation?
7. Resources: network, memory, asset, quota, platform, or lifecycle limits?
8. Performance: possible, or appropriate at this scale, based on evidence?

If nothing fails but evidence is incomplete, name the remaining assumption instead of declaring unconditional success.

## Common category errors

- Treating "largely compatible with Lua 5.1" as full compatibility with every Lua 5.1 environment, or importing later-Lua features without Luau evidence.
- Rejecting Luau extensions because standard Lua lacks them.
- Treating Roblox instances or datatypes as ordinary tables.
- Confusing analyzer diagnostics with runtime errors.
- Treating an RFC, upstream commit, Studio beta, and production deployment as equivalent.
- Treating an undocumented or dumped member as supported API.
- Treating coroutines as OS threads or parallel Actors.
- Assuming client-visible state is server-authoritative, or that a replicated parent guarantees every descendant exists.
- Claiming performance from intuition without naming the runtime and measurement conditions.
