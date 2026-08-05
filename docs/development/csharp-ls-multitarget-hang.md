# Investigation: csharp-ls hangs forever on multi-targeted C# monorepos

**Date:** 2026-08-05
**Status:** Root cause identified and reproduced (synthetic + OSS). Fix not yet decided — this document records the evidence.

## TL;DR

A customer's C# analysis fails with `StaticAnalysisFatalError: No component groups found` because
**csharp-ls (0.24.0, our pinned C# language server) never finishes loading their solution**. The hang
is an exponential-time target-framework computation in csharp-ls that is triggered by projects using
`<TargetFrameworks>` (plural, i.e. multi-targeting). With ~30 or more multi-targeted projects in one
solution the load takes hours-to-years, so CodeBoarding's sync probe times out after 1800s, the C#
result comes back empty, and the pipeline (correctly, per our fail-fast policy) refuses to build an
architecture out of nothing.

The bug is in csharp-ls, was introduced around 0.21, and is **still present in csharp-ls main** as of
August 2026 — bumping our pin does not fix it.

## Customer failure signature

From the customer's GitHub Actions log (a private .NET monorepo: 7,592 C# files, `.slnx` solution,
microservices layout). Everything before static analysis is healthy — `dotnet restore` succeeds and
csharp-ls answers `initialize`:

```
18:03:07 INFO  [static_analyzer:274] CSharp LSP start: 18.3s
18:03:07 INFO  [static_analyzer:812] Analyzing 7592 CSharp files
18:03:07 INFO  [static_analyzer.engine.call_graph_builder:211] Waiting for LSP server indexing (timeout=1800s)...
18:33:07 ERROR [static_analyzer:623] Error during engine analysis for CSharp: Timeout waiting for LSP response to request 2
18:33:07 ERROR [telemetry.events:132] LSP analysis result for csharp is unhealthy: zero nodes despite LOC, ...
...
static_analyzer.StaticAnalysisFatalError: No component groups found: the static analysis
produced no callable structure to build an architecture from.
```

"Request 2" is the first real request after `initialize`: the sync probe
(`textDocument/documentSymbol`, `static_analyzer/engine/call_graph_builder.py::_send_sync_probe`).
For C# the adapter sets `probe_before_open = True`, so nothing else was in flight — 30 minutes of
total silence means csharp-ls's workspace never left its `Loading` state. csharp-ls queues all
solution-dependent requests until the load completes (verified in its `ServerStateLoop.fs`:
awaiters are only released when the solution becomes `Ready` or `Defunct`), so a load that never
finishes is indistinguishable, from our side, from a dead server that still holds its stdio open.

After the empty C# result only the repo's two trivial JS/TS config files remain (1 node, 0 edges
each), clustering finds 0 clusters, and `agents/abstraction_agent.py` raises the fatal error. That
last part is working as designed; the C# emptiness is the real failure.

## Root cause: exponential TFM fold in csharp-ls

Before opening the Roslyn workspace, csharp-ls computes a single target framework to evaluate the
solution with. In `src/CSharpLanguageServer/Roslyn/Solution.fs` (0.24.0; identical logic in main):

```fsharp
let compatibleTfmsOfTwoSets afxs bfxs = seq {
    for a in afxs |> Seq.map NuGetFramework.Parse do
        for b in bfxs |> Seq.map NuGetFramework.Parse do
            if frameworkIsCompatible a b then yield a.GetShortFolderName()
            else if frameworkIsCompatible b a then yield b.GetShortFolderName()
}

let compatibleTfmSet (tfmSets: list<Set<string>>) : Set<string> =
    ...
    tfmSets |> List.skip 1 |> Seq.fold compatibleTfmsOfTwoSets firstSet |> Set.ofSeq
```

The fold state is a **lazy sequence that is never deduplicated between steps** — `Set.ofSeq` runs
only once at the very end. Every fold step nests another double loop around the previous state, so
each project whose TFM set contains T mutually compatible frameworks multiplies the number of
elements the final `Set.ofSeq` must enumerate by ~T. For N multi-targeted projects with 2 TFMs each
the enumeration is O(2^N), with a `NuGetFramework.Parse` call per element. Single-targeted projects
multiply by ~1 and are harmless — the blowup depends only on the **count of multi-targeted projects**
in the solution, which is why small fixtures and single-target repos never showed it.

Measured on csharp-ls 0.24.0 (.NET SDK 10.0.300, Linux, fast desktop):

| Solution | Load time |
|---|---|
| 50 projects, single-target | 16.5s |
| 100 projects, single-target | 27s (linear, ~0.26s/project) |
| 25 projects, `<TargetFrameworks>net9.0;net10.0</TargetFrameworks>` | 33s |
| 50 projects, same multi-targeting | **hang — no response after 850s, killed** |

The 25-project delta (~24s over the single-target baseline) matches 2^26 enumerations at ~2.8M/s.
Extrapolating at that rate: 30 projects ≈ 13 min, **32 projects ≈ 51 min (already past our 1800s
probe cap)**, 40 ≈ 9 days, 50 ≈ decades. Any realistic monorepo with 30+ multi-targeted shared
libraries (`netstandard2.0;net8.0` contract/client packages are the common case) hangs "forever".

## Reproduction

Scripts live in `tests/repro/csharp_ls_multitarget_hang/`:

- `gen_repo.py` — generates a synthetic monorepo (N projects under `src/services/`, `.sln`/`.slnx`,
  optional `--multitarget`).
- `lsp_probe.py` — minimal LSP driver that mimics our client handshake, prints csharp-ls's own
  load narration (`window/logMessage`, `$/progress`) with timestamps, and times the first
  `documentSymbol` — the exact "request 2" from the customer log.

### Synthetic (deterministic, ~15 min)

```bash
python tests/repro/csharp_ls_multitarget_hang/gen_repo.py /tmp/repro \
    --projects 50 --files 10 --format slnx --multitarget
cd /tmp/repro && dotnet restore Monorepo.slnx
python tests/repro/csharp_ls_multitarget_hang/lsp_probe.py /tmp/repro 600
# -> "Loading solution ..." then silence; TIMEOUT after 600s.
# Control: regenerate WITHOUT --multitarget -> loads in ~16s, probe answers.
```

### Real OSS project (verified)

`open-telemetry/opentelemetry-dotnet` reproduces it out of the box: two root `.slnx` files;
csharp-ls selects `OpenTelemetry.Extended.slnx` (78 projects, **44 multi-targeted**).

```bash
git clone --depth 1 https://github.com/open-telemetry/opentelemetry-dotnet.git /tmp/otel
rm /tmp/otel/global.json        # pins SDK 10.0.302; irrelevant to the hang
cd /tmp/otel && dotnet restore OpenTelemetry.Extended.slnx   # best-effort; mobile targets may fail
python tests/repro/csharp_ls_multitarget_hang/lsp_probe.py /tmp/otel 420
```

Observed:

```
[  0.8s] logMessage: csharp-ls: 2 solution(s) found: [.../OpenTelemetry.slnx, .../OpenTelemetry.Extended.slnx]
[  0.8s] logMessage: csharp-ls: Loading solution ".../OpenTelemetry.Extended.slnx"...
[420.8s] TIMEOUT: no documentSymbol response after 420s  << reproduces customer failure
```

A healthy load at this project count would be ~30s (see scaling table). Other large repos whose
main solution contains well over 32 multi-targeted projects (per GitHub code search, untested):
`DataDog/dd-trace-dotnet` (118 csproj with `TargetFrameworks`), `dotnet/orleans` (109),
`MassTransit/MassTransit` (34).

To confirm a specific customer repo is hitting this without seeing their code:

```bash
grep -rl "<TargetFrameworks>" --include="*.csproj" | wc -l   # >= ~30 -> this hang
```

## Notes and rejected hypotheses

- **Not the `.slnx` format**: csharp-ls 0.24.0 discovers and loads `.slnx` fine (verified, 4s on a
  small solution; slnx support landed in 0.18.0, and MSBuild's `SolutionFile.Parse` handles slnx on
  current SDKs).
- **Not NuGet credentials**: `LSPClient.start()` inherits `os.environ`, so the customer's private
  feed credentials reached csharp-ls; their `dotnet restore` succeeded in the same job.
- **Not the #448 memory recycler**: it only engages during the references phase, which was never
  reached.
- **Why #448's testing missed it**: bitwarden/server (3,926 C# files, 70 projects) is
  single-targeted — its workspace loads in ~21s. The trigger is multi-target count, not repo size.
- **Upstream status**: the slow-load regression appeared around csharp-ls 0.21 (matches upstream
  issue #352 "30x Slower LSP Initialization"; also #242, #171). PR #369 (0.25.0) parallelized TFM
  *reading* but left the exponential fold untouched — it is still in csharp-ls main. Upgrading our
  0.24.0 pin alone does not fix this.
- **Observability gap on our side**: csharp-ls's stderr goes to `DEVNULL` and its
  `window/logMessage` / `window/showMessage` narration is discarded unless it matches hardcoded
  markers (`static_analyzer/engine/lsp_client.py`), so the customer log shows 30 minutes of
  nothing. Surfacing that narration would have named the stuck phase immediately.

## Fix option space (for later discussion)

- Upstream fix: dedup the fold state per step (e.g. `Set.ofSeq` inside the fold) — a one-line
  change in csharp-ls `Solution.fs`; worth filing an issue/PR.
- Our side, independent of upstream: surface csharp-ls narration (stderr + logMessage) into our
  logs; consider failing fast when the workspace-load progress stalls instead of burning the full
  1800s probe; consider detecting the multi-target count up front and warning.
