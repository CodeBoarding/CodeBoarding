"""Re-run patch_analysis_scope against the real LLM with the captured (analysis,
patch_scope) inputs. Skips the wrapper, the e2e harness, the binary build, the
LSP, the static-analysis stages, the tracer — calls the patcher LLM and the
deterministic fallback in apply_scope_patch directly. Each iteration is one
LLM round-trip (~5–30s) instead of one e2e run (~2–7min).

Usage:
    cd CodeBoarding
    uv run python tests/incremental_analysis/run_patcher_against_llm.py \
        tests/incremental_analysis/fixtures/scope_patch_telemetry_subsystem.pkl

The fixture's stored scope_patch (the LLM's response from one run) is ignored;
we recall the LLM ourselves with (analysis, patch_scope). That lets us iterate
on the prompt + retry feedback inside patch_analysis_scope and watch how the
LLM behaves run-to-run.

A non-zero exit means the patcher had to fall back to the deterministic
directory-grouped component minter — which means the LLM still produced an
empty/incomplete patch and the prompt or retry feedback needs more work.
"""

import argparse
import os
import pickle
import sys
from pathlib import Path

# Make sure we're using the project's .env if present; load before importing
# llm_config so PATCHING_MODEL etc. are picked up at import time.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV = _REPO_ROOT / ".env"
if _ENV.is_file():
    for line in _ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from agents.llm_config import MONITORING_CALLBACK, initialize_patching_llm  # noqa: E402
from incremental_analysis.analysis_patcher import (  # noqa: E402
    apply_scope_patch,
    patch_analysis_scope,
)


def _component_files(component) -> set[str]:
    return {g.file_path for g in component.file_methods}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path, help="Path to a captured .pkl scope-patch dump.")
    parser.add_argument(
        "--unallocated-must-be-owned",
        action="store_true",
        default=True,
        help="Fail if any path in patch_scope.unallocated_files isn't owned by some component after patching.",
    )
    parser.add_argument(
        "--require-llm-allocation",
        action="store_true",
        help="Fail if the deterministic fallback in apply_scope_patch had to mint a component "
        "(i.e. the LLM didn't allocate the unallocated files itself).",
    )
    args = parser.parse_args()

    with args.dump.open("rb") as f:
        captured = pickle.load(f)

    analysis = captured["analysis"]
    patch_scope = captured["patch_scope"]
    print(f"# replay LLM patch for scope_id={patch_scope.scope_id!r}")
    print(f"# target_component_ids={patch_scope.target_component_ids}")
    print(f"# unallocated_files=({len(patch_scope.unallocated_files)})")
    for p in patch_scope.unallocated_files:
        print(f"#   - {p}")

    agent_llm = initialize_patching_llm()
    print(
        f"# patching_llm={type(agent_llm).__name__} model={getattr(agent_llm, 'model', getattr(agent_llm, 'model_name', '?'))}"
    )

    patched = patch_analysis_scope(analysis, patch_scope, agent_llm, callbacks=[MONITORING_CALLBACK])
    if patched is None:
        print("FAIL: patch_analysis_scope returned None (all retries exhausted)")
        return 1

    unallocated = set(patch_scope.unallocated_files)
    owners_of_unallocated: dict[str, list] = {p: [] for p in unallocated}
    for component in patched.components:
        for path in unallocated & _component_files(component):
            owners_of_unallocated[path].append(component)

    print("\n## components with any of the unallocated files")
    seen_components = set()
    for owners in owners_of_unallocated.values():
        for c in owners:
            if c.component_id in seen_components:
                continue
            seen_components.add(c.component_id)
            files = sorted(_component_files(c) & unallocated)
            print(f"  [{c.component_id}] {c.name!r} owns {len(files)}/{len(unallocated)}: {files}")

    orphans = [p for p, owners in owners_of_unallocated.items() if not owners]
    failed = False
    if args.unallocated_must_be_owned and orphans:
        print(f"FAIL: {len(orphans)} unallocated path(s) ended up orphaned: {orphans}")
        failed = True

    if args.require_llm_allocation:
        # Heuristic: if any patched component has the auto-fallback description
        # prefix we emit, the LLM didn't allocate.
        for c in patched.components:
            if (c.description or "").startswith("Auto-grouped fallback"):
                print(f"FAIL: deterministic fallback minted [{c.component_id}] {c.name!r} — LLM didn't allocate")
                failed = True

    if failed:
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
