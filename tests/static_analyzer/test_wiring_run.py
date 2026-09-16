"""The pass as a whole: the flag, the bucket it fills, the dumps, and the same answer twice."""

import filecmp
import json
import os
import pickle
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from static_analyzer import wiring
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.wiring import dump_dir, enabled, head_commit, repository_name, repository_slug, run, write_dump
from static_analyzer.wiring_results import DiagnosticCode, WiringResults

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"


class TestFlag(unittest.TestCase):
    def test_the_pass_is_off_unless_the_flag_says_otherwise(self) -> None:
        for value, expected in (("", False), ("0", False), ("true", False), ("1", True)):
            with mock.patch.dict("os.environ", {"CODEBOARDING_WIRING": value}):
                self.assertIs(enabled(), expected, value)

    def test_the_dump_directory_is_where_the_environment_points(self) -> None:
        with mock.patch.dict("os.environ", {"CODEBOARDING_WIRING_DUMP": "/tmp/dump"}):
            self.assertEqual(dump_dir(), Path("/tmp/dump"))
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(dump_dir())


class TestBucket(unittest.TestCase):
    def test_a_run_with_no_wiring_carries_an_empty_bucket(self) -> None:
        results = StaticAnalysisResults()
        self.assertEqual(results.wiring, WiringResults())
        self.assertEqual(results.wiring.units, [])

    def test_a_pickle_written_before_the_bucket_existed_loads(self) -> None:
        old = object.__new__(StaticAnalysisResults)
        old.__dict__.update({"results": {}, "diagnostics": {}, "incremental_base_results": None})

        loaded = pickle.loads(pickle.dumps(old))

        self.assertEqual(loaded.wiring, WiringResults())

    def test_the_bucket_survives_a_round_trip(self) -> None:
        results = StaticAnalysisResults()
        results.wiring = run(results, FIXTURES / "compose-merge")

        loaded = pickle.loads(pickle.dumps(results))

        self.assertEqual([unit.dir for unit in loaded.wiring.units], ["api"])


class TestDump(unittest.TestCase):
    def test_the_dump_is_the_schema_the_graders_read(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        wiring = run(StaticAnalysisResults(), FIXTURES / "maven-modules")

        write_dump(wiring, FIXTURES / "maven-modules", directory)
        units = json.loads((directory / "units.json").read_text())
        diagnostics = json.loads((directory / "diagnostics.json").read_text())

        self.assertEqual(sorted(units), ["commit", "diagnostics", "repo", "units"])
        self.assertEqual(sorted(units["units"][0]), ["aliases", "builds", "dir", "id", "kind", "manifest", "variant"])
        self.assertEqual([unit["dir"] for unit in units["units"]], ["gateway", "service"])
        self.assertEqual(units["diagnostics"], diagnostics["diagnostics"])

    def test_two_runs_over_one_tree_write_the_same_bytes(self) -> None:
        first = Path(self.enterContext(tempfile.TemporaryDirectory()))
        second = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repository = FIXTURES / "dotnet-aspire"

        write_dump(run(StaticAnalysisResults(), repository), repository, first)
        write_dump(run(StaticAnalysisResults(), repository), repository, second)

        self.assertEqual(
            (first / "units.json").read_bytes(),
            (second / "units.json").read_bytes(),
        )

    def test_two_processes_with_different_hash_seeds_write_the_same_bytes(self) -> None:
        """Every list the pass writes is sorted on a complete key, or a set's order would leak into the dumps."""
        dumps = []
        for seed in ("1", "7"):
            directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
            script = (
                "from pathlib import Path\n"
                "from static_analyzer.analysis_result import StaticAnalysisResults\n"
                "from static_analyzer.wiring import run, write_dump\n"
                f"repository = Path({str(FIXTURES / 'test-shapes')!r}).parent / 'compose-own-image'\n"
                f"write_dump(run(StaticAnalysisResults(), repository), repository, Path({str(directory)!r}))\n"
            )
            subprocess.run(
                [sys.executable, "-c", script],
                check=True,
                env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                cwd=Path(__file__).resolve().parents[2],
            )
            dumps.append(directory)
        names = sorted(path.name for path in dumps[0].iterdir())

        self.assertTrue(names)
        same, different, missing = filecmp.cmpfiles(dumps[0], dumps[1], names, shallow=False)
        self.assertEqual((sorted(same), different, missing), (names, [], []))


class TestGuard(unittest.TestCase):
    def test_a_failing_pass_is_one_row_and_never_a_broken_analysis(self) -> None:
        with mock.patch.object(wiring, "run", side_effect=KeyError("services")):
            found = wiring.run_or_report(StaticAnalysisResults(), FIXTURES / "compose-merge")

        self.assertEqual(found.units, [])
        self.assertEqual([d.code for d in found.diagnostics], [DiagnosticCode.UNREADABLE_MANIFEST])
        self.assertIn("KeyError", found.diagnostics[0].message)

    def test_a_working_pass_is_passed_through(self) -> None:
        self.assertEqual(
            [unit.dir for unit in wiring.run_or_report(StaticAnalysisResults(), FIXTURES / "compose-merge").units],
            ["api"],
        )


class TestRepositoryMetadata(unittest.TestCase):
    """The dump names the repository and the commit, read from the git directory, never from git."""

    def _repository(self, head: str, *, config: str = "", packed: str = "", ref: str = "") -> Path:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        git = root / ".git"
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "HEAD").write_text(head, encoding="utf-8")
        if config:
            (git / "config").write_text(config, encoding="utf-8")
        if packed:
            (git / "packed-refs").write_text(packed, encoding="utf-8")
        if ref:
            (git / "refs" / "heads" / "main").write_text(ref, encoding="utf-8")
        return root

    def test_a_loose_ref_and_an_ssh_remote(self) -> None:
        root = self._repository(
            "ref: refs/heads/main\n",
            config='[remote "origin"]\n\turl = git@github.com:dotnet/eShop.git\n',
            ref="a" * 40 + "\n",
        )

        self.assertEqual(repository_slug(root), "dotnet/eShop")
        self.assertEqual(repository_name(root), "eShop")
        self.assertEqual(head_commit(root), "a" * 40)

    def test_a_packed_ref_and_an_https_remote(self) -> None:
        root = self._repository(
            "ref: refs/heads/main\n",
            config='[remote "origin"]\n\turl = https://github.com/zulip/zulip\n',
            packed=f"# pack-refs with: peeled fully-peeled sorted \n{'b' * 40} refs/heads/main\n",
        )

        self.assertEqual(repository_slug(root), "zulip/zulip")
        self.assertEqual(head_commit(root), "b" * 40)

    def test_a_detached_head_and_no_remote(self) -> None:
        root = self._repository("c" * 40 + "\n")

        self.assertEqual(head_commit(root), "c" * 40)
        self.assertEqual(repository_slug(root), root.name)
        self.assertEqual(repository_name(root), root.name)


if __name__ == "__main__":
    unittest.main()
