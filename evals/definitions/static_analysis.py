from typing import Dict, Any, List
from evals.base import BaseEval
from evals.utils import generate_header


class StaticAnalysisEval(BaseEval):
    def extract_metrics(self, project: Dict, run_data: Dict) -> Dict[str, Any]:
        code_stats = run_data.get("code_stats", {})

        # Calculate totals
        total_files = 0
        total_loc = 0
        languages_summary = {}

        for lang, stats in code_stats.get("languages", {}).items():
            file_count = stats.get("file_count", 0)
            loc = stats.get("lines_of_code", 0)
            total_files += file_count
            total_loc += loc
            languages_summary[lang] = {"files": file_count, "loc": loc}

        return {
            "code_stats": {
                "total_files": total_files,
                "total_loc": total_loc,
                "languages": languages_summary,
            }
        }

    def generate_report(self, results: List[Dict]) -> str:
        header = generate_header("Static Analysis Performance Evaluation")

        # Aggregate totals
        total_files = 0
        total_loc = 0
        for r in results:
            if r.get("success"):
                stats = r.get("code_stats", {})
                total_files += stats.get("total_files", 0)
                total_loc += stats.get("total_loc", 0)

        lines = [
            header,
            "### Summary",
            "",
            "| Project | Language | Status | Time (s) | Files | LOC |",
            "|---------|----------|--------|----------|-------|-----|",
        ]

        for r in results:
            status = "✅ Success" if r.get("success") else "❌ Failed"
            time_taken = f"{r.get('duration_seconds', 0):.1f}"
            lang = r.get("expected_language", "Unknown")

            files = 0
            loc = 0
            if r.get("success"):
                stats = r.get("code_stats", {})
                files = stats.get("total_files", 0)
                loc = stats.get("total_loc", 0)

            lines.append(f"| {r.get('project', 'Unknown')} | {lang} | {status} | {time_taken} | {files:,} | {loc:,} |")

        lines.extend(
            [
                "",
                f"**Total Files:** {total_files:,}",
                f"**Total LOC:** {total_loc:,}",
            ]
        )

        return "\n".join(lines)
