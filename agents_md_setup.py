"""Entry point for ``codeboarding-agents-md``: install the CODEBOARDING marker into cwd."""

import argparse
from pathlib import Path

from output_generators.agents_md_install import setup_agents_md, setup_agents_md_all


def main() -> None:
    """Entry point for the ``codeboarding-agents-md`` CLI command."""
    parser = argparse.ArgumentParser(
        description=(
            "Install the CODEBOARDING marker into the current repo's agent-instruction files. "
            "Non-destructive: existing content outside the marker block is preserved."
        )
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Install into AGENTS.md, CLAUDE.md, .github/copilot-instructions.md, and .windsurfrules "
            "(default: AGENTS.md only)."
        ),
    )
    args = parser.parse_args()

    repo_path = Path.cwd()
    if args.all:
        touched = setup_agents_md_all(repo_path)
        print(f"Installed CODEBOARDING.md placeholder and marker in {len(touched)} agent-instruction files:")
        for p in touched:
            print(f"  - {p.relative_to(repo_path)}")
    else:
        setup_agents_md(repo_path)
        print(f"Installed CODEBOARDING.md placeholder and AGENTS.md marker in {repo_path}")
    print("Run `codeboarding --local .` to populate the digest.")


if __name__ == "__main__":
    main()
