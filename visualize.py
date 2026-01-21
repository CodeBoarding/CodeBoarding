"""
Combined visualization tool for git commits and performance metrics.
Includes timeline visualization and time-taken vs LOC regression analysis.
"""

import csv
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from git import Repo

# Mapping of repo names (last word in commit message) to lines of code
REPO_LOC = {
    "lmql": 18622,
    "MetaGPT": 66083,
    "mlflow": 573050,
    "mlc-llm": 52861,
    "kotaemon": 25773,
    "recommenders": 32815,
    "dash": 42278,
    "matharena": 6974,
    "BitNet": 7217,
    "mkdocs": 15650,
    "vit-pytorch": 10500,
    "torchtyping": 1912,
    "susi_api_wrapper": 469,
    "dominate": 1953,
    "elephas": 2680,
    "once-for-all": 10172,
    "Scrapegraph-ai": 18068,
    "craft-application": 25579,
    "transformer-deploy": 6043,
    "splink": 31250,
    "OpenNE": 1984,
    "penzai": 32075,
    "ChatGLM-6B": 4693,
    "glom": 7547,
    "unidiffuser": 2963,
    "keras": 242239,
    "ebooklib": 3435,
}


def get_commits_in_date_range(
    repo_path: Path,
    start_date: datetime,
    end_date: datetime,
    message_prefix: Optional[str] = None,
    author_filter: Optional[str] = None,
) -> list[dict]:
    """Get all commits within a date range."""
    repo = Repo(repo_path)
    commits_in_range = []

    for commit in repo.iter_commits():
        commit_datetime = datetime.fromtimestamp(commit.committed_date)

        if start_date <= commit_datetime <= end_date:
            if author_filter and str(commit.author) != author_filter:
                continue

            commit_message = commit.message.strip()
            if message_prefix and not commit_message.startswith(message_prefix):
                continue

            md_files_added = 0
            try:
                if commit.parents:
                    diff = commit.parents[0].diff(commit)
                    for d in diff:
                        if d.change_type != "D":
                            file_path = d.b_path or d.a_path
                            if file_path and file_path.endswith(".md"):
                                md_files_added += 1
                else:
                    for item in commit.tree.traverse():
                        if item.path.endswith(".md"):
                            md_files_added += 1
            except Exception:
                md_files_added = 0

            commits_in_range.append(
                {
                    "datetime": commit_datetime,
                    "message": commit.message.strip().split("\n")[0],
                    "author": str(commit.author),
                    "hash": commit.hexsha[:8],
                    "md_files_added": md_files_added,
                }
            )
        elif commit_datetime < start_date:
            break

    commits_in_range.sort(key=lambda x: x["datetime"])
    return commits_in_range


def plot_commits_timeline(commits: list[dict], title: str = "Commits Timeline"):
    """Plot commits on a scatter plot - one dot per commit."""
    if not commits:
        print("No commits found in the specified date range.")
        return

    datetimes = [c["datetime"] for c in commits]
    times = [dt.hour + dt.minute / 60 for dt in datetimes]

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.suptitle(title, fontsize=16, fontweight="bold")

    ax.scatter(datetimes, times, s=80, color="steelblue", edgecolors="black", alpha=0.8)

    for i, commit in enumerate(commits):
        ax.annotate(
            commit["hash"],
            (datetimes[i], times[i]),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
            alpha=0.7,
        )

    ax.set_xlabel("Date", fontsize=12)
    ax.set_ylabel("Time of Day (Hour)", fontsize=12)
    ax.set_ylim(0, 24)
    ax.set_yticks(range(0, 25, 2))
    ax.set_yticklabels([f"{h:02d}:00" for h in range(0, 25, 2)])
    ax.grid(True, alpha=0.3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    fig.autofmt_xdate()

    plt.tight_layout()
    plt.show()


def print_time_between_commits(
    commits: list[dict],
    repo_name: str,
    max_gap_hours: float = 1.0,
    save_csv: bool = True,
):
    """Print time between consecutive commits and save to CSV."""
    if len(commits) < 2:
        print("Need at least 2 commits to calculate time between.")
        return

    min_gap_minutes = 4
    print("\n" + "=" * 90)
    print(f"{'TIME BETWEEN COMMITS (gaps < 4 min or > 1 hour excluded)':^90}")
    print("=" * 90)
    print(f"{'Commit':<10} {'#MD':<6} {'Datetime':<20} {'Gap':<15} {'Counted':<10}")
    print("-" * 90)

    total_working_time = timedelta()
    counted_gaps = 0
    skipped_gaps = 0
    csv_rows = []

    for i in range(1, len(commits)):
        prev_commit = commits[i - 1]
        curr_commit = commits[i]
        prev_dt = prev_commit["datetime"]
        curr_dt = curr_commit["datetime"]
        curr_hash = curr_commit["hash"][:7]
        curr_md_count = curr_commit.get("md_files_added", 0)
        gap = curr_dt - prev_dt
        gap_hours = gap.total_seconds() / 3600
        gap_minutes = int(gap.total_seconds() / 60)

        commit_message = curr_commit.get("message", "")
        last_word = commit_message.split()[-1] if commit_message.split() else "unknown"
        gap_str = f"{gap_minutes} min" if gap_minutes < 60 else f"{gap_hours:.1f} hrs"

        if gap_minutes >= min_gap_minutes and gap_hours <= max_gap_hours:
            counted = True
            total_working_time += gap
            counted_gaps += 1
        else:
            counted = False
            skipped_gaps += 1

        csv_rows.append(
            {
                "repo_name": last_word,
                "commit": curr_hash,
                "md_added": curr_md_count,
                "datetime": curr_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "gap_minutes": gap_minutes,
                "gap_hours": round(gap_hours, 2),
                "counted": counted,
            }
        )

        counted_display = "\u2713" if counted else "\u2717 (skipped)"
        print(
            f"{curr_hash:<10} {curr_md_count:<6} {curr_dt.strftime('%Y-%m-%d %H:%M:%S'):<20} {gap_str:<15} {counted_display:<10}"
        )

    print("=" * 90)
    total_minutes = int(total_working_time.total_seconds() / 60)
    total_hours = total_working_time.total_seconds() / 3600

    print(f"\nSUMMARY:")
    print(f"  Counted gaps (4 min - 1 hour): {counted_gaps}")
    print(f"  Skipped gaps (<4 min or >1 hour): {skipped_gaps}")
    print(f"  Total working time:      {total_hours:.2f} hours ({total_minutes} minutes)")
    print("=" * 90 + "\n")

    if save_csv and csv_rows:
        csv_path = Path(__file__).parent / f"commit_gaps_{repo_name}.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_rows[0].keys())
            writer.writeheader()
            writer.writerows(csv_rows)
        print(f"Saved to: {csv_path}")

    if csv_rows:
        save_samples(csv_rows)


def save_samples(csv_rows: list[dict], num_bins: int = 4, samples_per_bin: int = 5):
    """Sample from counted rows and save to samples.csv."""
    counted_rows = [row for row in csv_rows if row["counted"]]
    if not counted_rows:
        return

    counted_rows.sort(key=lambda x: x["gap_minutes"])
    total = len(counted_rows)
    samples = []

    print(f"\nSAMPLING {samples_per_bin} FROM {num_bins} BINS:")

    for i in range(num_bins):
        start_idx = (i * total) // num_bins
        end_idx = ((i + 1) * total) // num_bins
        bin_rows = counted_rows[start_idx:end_idx]

        if not bin_rows:
            continue

        num_to_sample = min(len(bin_rows), samples_per_bin)
        bin_samples = random.sample(bin_rows, num_to_sample)

        for s in bin_samples:
            s["bin_index"] = i + 1

        samples.extend(bin_samples)
        print(
            f"  Bin {i+1} ({bin_rows[0]['gap_minutes']}-{bin_rows[-1]['gap_minutes']} min): Sampled {num_to_sample}/{len(bin_rows)}"
        )

    if samples:
        samples_path = Path(__file__).parent / "samples.csv"
        samples.sort(key=lambda x: x["gap_minutes"])
        fieldnames = [
            "repo_name",
            "commit",
            "md_added",
            "datetime",
            "gap_minutes",
            "gap_hours",
            "counted",
            "bin_index",
        ]
        with open(samples_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(samples)
        print(f"Saved samples to: {samples_path}")


def clone_or_pull_repo(repo_url: str, local_path: Path) -> Path:
    """Clone or pull the repository."""
    if local_path.exists():
        print(f"Repository already exists at {local_path}, pulling latest...")
        repo = Repo(local_path)
        repo.remotes.origin.pull()
    else:
        print(f"Cloning {repo_url} to {local_path}...")
        Repo.clone_from(repo_url, local_path)
    return local_path


def fit_log_model(locs: list, times: list) -> tuple[float, float]:
    """Fit time = a * log10(LOC) + b."""
    log_locs = [np.log10(loc) for loc in locs]
    n = len(locs)
    mean_x = sum(log_locs) / n
    mean_y = sum(times) / n
    numerator = sum((log_locs[i] - mean_x) * (times[i] - mean_y) for i in range(n))
    denominator = sum((log_locs[i] - mean_x) ** 2 for i in range(n))
    a = numerator / denominator
    b = mean_y - a * mean_x
    return a, b


def estimate_time_log(loc: int, a: float, b: float) -> float:
    """Estimate time in minutes using log model."""
    return a * np.log10(loc) + b


def run_regression_analysis():
    """Perform regression analysis on samples.csv."""
    samples_path = Path(__file__).parent / "samples.csv"
    if not samples_path.exists():
        print(f"No samples.csv found at {samples_path}. Run commit fetching first.")
        return

    samples = []
    with open(samples_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(row)

    matched_data = []
    unmatched = []

    for sample in samples:
        repo_name = sample["repo_name"]
        gap_minutes = int(sample["gap_minutes"])
        if repo_name in REPO_LOC:
            matched_data.append({"repo_name": repo_name, "gap_minutes": gap_minutes, "loc": REPO_LOC[repo_name]})
        else:
            unmatched.append(repo_name)

    if unmatched:
        print(f"Unmatched repos: {set(unmatched)}")

    if not matched_data:
        print("No matched data for regression analysis.")
        return

    times = [d["gap_minutes"] for d in matched_data]
    locs = [d["loc"] for d in matched_data]
    names = [d["repo_name"] for d in matched_data]

    a, b = fit_log_model(locs, times)

    print(f"\n{'='*60}")
    print("LOG MODEL: time = a * log10(LOC) + b")
    print(f"{'='*60}")
    print(f"  a (slope):     {a:.4f}")
    print(f"  b (intercept): {b:.4f}")
    print(f"  Formula:       time = {a:.2f} * log10(LOC) + ({b:.2f})")
    print(f"{'='*60}")

    # Plotting
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.scatter(locs, times, s=100, color="steelblue", edgecolors="black", alpha=0.8, zorder=5)

    for i, name in enumerate(names):
        ax.annotate(
            name,
            (locs[i], times[i]),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
            alpha=0.7,
        )

    x_line = np.logspace(np.log10(min(locs) * 0.5), np.log10(max(locs) * 2), 100)
    y_line = [estimate_time_log(x, a, b) for x in x_line]
    ax.plot(x_line, y_line, "r--", linewidth=2, label=f"Log model: {a:.1f}\u00d7log\u2081\u2080(LOC) + ({b:.1f})")

    ax.set_xlabel("Lines of Code", fontsize=12)
    ax.set_ylabel("Time Taken (minutes)", fontsize=12)
    ax.set_title("Time Taken vs Lines of Code Regression", fontsize=14, fontweight="bold")
    ax.grid(True, alpha=0.3)
    ax.legend()
    ax.set_xscale("log")

    plt.tight_layout()
    plt.savefig(Path(__file__).parent / "time_vs_loc.png", dpi=150)
    print(f"\nSaved regression plot to time_vs_loc.png")
    plt.show()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="CodeBoarding Visualization Tools")
    parser.add_argument("--fetch", action="store_true", help="Fetch commits and update samples.csv")
    parser.add_argument("--analyze", action="store_true", help="Run regression analysis from samples.csv")
    args = parser.parse_args()

    # Default to running both if no arguments are provided
    run_all = not (args.fetch or args.analyze)

    if args.fetch or run_all:
        repo_url = "https://github.com/CodeBoarding/GeneratedOnBoardings"
        repo_name = "GeneratedOnBoardings"
        local_path = Path(__file__).parent / "repos" / repo_name
        repo_path = clone_or_pull_repo(repo_url, local_path)

        start_date = datetime(2025, 8, 11, 0, 0, 0)
        end_date = datetime(2025, 8, 20, 23, 59, 59)
        message_prefix = "Uploading onboarding materials"
        author_filter = "ubuntu"

        print(f"Fetching commits from {start_date.date()} to {end_date.date()}...")
        commits = get_commits_in_date_range(
            repo_path,
            start_date,
            end_date,
            message_prefix=message_prefix,
            author_filter=author_filter,
        )

        print_time_between_commits(commits, repo_name=repo_name)
        plot_commits_timeline(commits, title=f"GeneratedOnBoardings Commits: Aug 11-20, 2025")

    if args.analyze or run_all:
        run_regression_analysis()


if __name__ == "__main__":
    main()
