"""Replay a captured apply_scope_patch invocation in isolation.

Capture step:
    CB_PATCHER_DUMP_DIR=/tmp/cb-patch-dumps \\
        npm --prefix /path/to/CodeBoarding-vscode run test:e2e:backend -- e2e/backend/10-newComponentCreation.pw.ts

Replay step (fast, no LLM):
    uv run python tests/incremental_analysis/replay_scope_patch.py /tmp/cb-patch-dumps
    uv run python tests/incremental_analysis/replay_scope_patch.py /tmp/cb-patch-dumps/<file>.pkl
"""

import argparse
import pickle
import sys
from pathlib import Path

from incremental_analysis.analysis_patcher import apply_scope_patch


def _resolve_dump(target: Path) -> Path:
    if target.is_file():
        return target
    if target.is_dir():
        candidates = sorted(target.glob("*.pkl"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            sys.exit(f"no .pkl dumps found under {target}")
        return candidates[-1]
    sys.exit(f"{target} does not exist")


def _component_files(component) -> list[str]:
    return [g.file_path for g in component.file_methods]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path, help="Path to a .pkl dump or a dir containing them.")
    parser.add_argument(
        "--expect-new",
        action="append",
        default=[],
        help="Component name expected to exist as a freshly minted component. May be repeated.",
    )
    parser.add_argument(
        "--expect-files",
        action="append",
        default=[],
        help="File path expected to belong to one of --expect-new components. May be repeated.",
    )
    args = parser.parse_args()

    dump_path = _resolve_dump(args.dump)
    print(f"# replaying {dump_path}")

    with dump_path.open("rb") as f:
        captured = pickle.load(f)

    analysis = captured["analysis"]
    patch_scope = captured["patch_scope"]
    scope_patch = captured["scope_patch"]

    print(f"# patch_scope.scope_id={patch_scope.scope_id!r}")
    print(f"# patch_scope.target_component_ids={patch_scope.target_component_ids}")
    print(f"# patch_scope.unallocated_files=({len(patch_scope.unallocated_files)} files)")
    for p in patch_scope.unallocated_files:
        print(f"#   - {p}")
    print(f"# scope_patch.new_components=({len(scope_patch.new_components)})")
    for spec in scope_patch.new_components:
        print(f"#   + {spec.name!r} owned_files={spec.owned_files}")
    print(f"# scope_patch.components=({len(scope_patch.components)} touched)")
    for cp in scope_patch.components:
        print(f"#   ~ id={cp.component_id!r} added_files={cp.added_files}")

    print("\n## BEFORE")
    for c in analysis.components:
        print(f"  [{c.component_id}] {c.name} files={len(c.file_methods)}")

    patched = apply_scope_patch(analysis, patch_scope, scope_patch)

    print("\n## AFTER")
    for c in patched.components:
        files = _component_files(c)
        print(f"  [{c.component_id}] {c.name} files={len(files)}")
        for fp in files:
            print(f"      {fp}")

    failed = False
    if args.expect_new:
        new_by_name = {c.name: c for c in patched.components if c.name in args.expect_new}
        for name in args.expect_new:
            if name not in new_by_name:
                print(f"FAIL: expected new component '{name}' is missing from patched analysis")
                failed = True
                continue
            owned = set(_component_files(new_by_name[name]))
            for rel in args.expect_files:
                if rel not in owned:
                    print(f"FAIL: '{name}' is missing expected file {rel}")
                    failed = True

    if failed:
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
