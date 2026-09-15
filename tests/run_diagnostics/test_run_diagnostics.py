"""The collector that decides what a finished run admits to."""

import unittest

from run_diagnostics import Diagnostic, DiagnosticSeverity, RunDiagnostics
from run_diagnostics.catalog import (
    language_engine_failed,
    language_not_supported,
    names_not_generated,
)


def _notice(code: str, subject: str = "") -> Diagnostic:
    return Diagnostic(
        code=code,
        severity=DiagnosticSeverity.NOTICE,
        title="t",
        detail="d",
        subject=subject,
    )


class TestRunDiagnostics(unittest.TestCase):
    def test_an_empty_collector_is_falsy(self):
        self.assertFalse(RunDiagnostics())

    def test_repeats_of_one_code_and_subject_collapse_into_a_count(self):
        collector = RunDiagnostics()
        collector.record(language_engine_failed("TypeScript", "boom"))
        collector.record(language_engine_failed("TypeScript", "boom again"))

        entries = collector.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].count, 2)
        # The first recording's wording wins, so the count never contradicts the detail.
        self.assertIn("boom)", entries[0].detail)

    def test_the_same_code_for_two_subjects_stays_two_entries(self):
        collector = RunDiagnostics()
        collector.record(language_engine_failed("TypeScript", "boom"))
        collector.record(language_engine_failed("Go", "boom"))

        self.assertEqual([e.subject for e in collector.entries()], ["Go", "TypeScript"])

    def test_degraded_entries_sort_ahead_of_notices(self):
        collector = RunDiagnostics()
        collector.record(_notice("a.notice"))
        collector.record(names_not_generated(1, 4))

        self.assertEqual(
            [e.severity for e in collector.entries()],
            [DiagnosticSeverity.DEGRADED, DiagnosticSeverity.NOTICE],
        )

    def test_the_report_counts_each_severity(self):
        collector = RunDiagnostics()
        collector.record(language_engine_failed("Go", "boom"))
        collector.record(names_not_generated(1, 4))
        collector.record(_notice("a.notice"))

        report = collector.report()
        self.assertEqual((report.degraded, report.notices), (2, 1))
        self.assertEqual(len(report.entries), 3)

    def test_absorbing_another_collector_takes_its_records(self):
        static_analysis = RunDiagnostics()
        static_analysis.record(language_engine_failed("Go", "boom"))
        run = RunDiagnostics()
        run.record(names_not_generated(1, 4))

        run.absorb(static_analysis)

        self.assertEqual(
            {e.code for e in run.entries()}, {"static.language_engine_failed", "semantics.names_not_generated"}
        )

    def test_a_cause_the_reader_cannot_fix_carries_no_remedy(self):
        """Consumers branch on an empty remedy to offer "report this" instead of an instruction."""
        self.assertEqual(language_not_supported("Kotlin", 30.0).remedy, "")
        self.assertNotEqual(language_engine_failed("Go", "boom").remedy, "")


if __name__ == "__main__":
    unittest.main()
