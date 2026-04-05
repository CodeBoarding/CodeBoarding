"""Compare two incremental benchmark result files and output a markdown table.

Usage:
    python compare_results.py base_results.json head_results.json
"""

import json
import sys


def compare(base_path: str, head_path: str) -> str:
    base = json.load(open(base_path))["scenarios"]
    head = json.load(open(head_path))["scenarios"]

    all_names = sorted(set(list(base) + list(head)))
    rows: list[str] = []

    for name in all_names:
        b = (base.get(name) or [{}])[0]
        h = (head.get(name) or [{}])[0]
        bt = b.get("wall_clock_seconds", 0)
        ht = h.get("wall_clock_seconds", 0)
        delta = ht - bt
        pct = (delta / bt * 100) if bt else 0
        sign = "+" if delta > 0 else ""
        b_esc = b.get("escalation_level", "-")
        h_esc = h.get("escalation_level", "-")
        flag = " :warning:" if b_esc != h_esc else ""
        rows.append(
            f"| {name} | {bt:.1f} | {ht:.1f} | {sign}{delta:.1f} | {sign}{pct:.0f}% | {b_esc} | {h_esc}{flag} |"
        )

    header = (
        "| Scenario | Base (s) | Head (s) | Delta | Delta% | Base Escalation | Head Escalation |\n"
        "|----------|----------|----------|-------|--------|-----------------|-----------------|"
    )
    return header + "\n" + "\n".join(rows)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} base.json head.json", file=sys.stderr)
        sys.exit(1)
    print(compare(sys.argv[1], sys.argv[2]))
