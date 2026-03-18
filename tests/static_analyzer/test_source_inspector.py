"""Tests for static_analyzer.engine.source_inspector.SourceInspector."""

from pathlib import Path

from static_analyzer.engine.source_inspector import SourceInspector


class TestGetSourceLine:
    def test_reads_existing_line(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("line0\nline1\nline2\n")
        si = SourceInspector()
        assert si.get_source_line(f, 0) == "line0"
        assert si.get_source_line(f, 1) == "line1"
        assert si.get_source_line(f, 2) == "line2"

    def test_returns_none_for_out_of_range(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("only one line")
        si = SourceInspector()
        assert si.get_source_line(f, 100) is None

    def test_returns_none_for_missing_file(self):
        si = SourceInspector()
        assert si.get_source_line(Path("/nonexistent/file.py"), 0) is None

    def test_caches_file_content(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("cached")
        si = SourceInspector()
        si.get_source_line(f, 0)
        # Modify file — cached version should still be returned
        f.write_text("modified")
        assert si.get_source_line(f, 0) == "cached"


class TestIsInvocation:
    def test_direct_call(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    foo(bar)\n")
        si = SourceInspector()
        assert si.is_invocation(f, 0, 7) is True  # after "foo"

    def test_not_a_call(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    x = foo\n")
        si = SourceInspector()
        assert si.is_invocation(f, 0, 11) is False

    def test_generic_instantiation(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("    new List<String>()\n")
        si = SourceInspector()
        # After "List" at char 8, rest is "<String>()"
        assert si.is_invocation(f, 0, 12) is True

    def test_conservative_on_missing_file(self):
        si = SourceInspector()
        assert si.is_invocation(Path("/nonexistent.py"), 0, 0) is True

    def test_call_on_next_line(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    foo\n    (bar)\n")
        si = SourceInspector()
        # "foo" ends at char 7, rest of line is empty
        assert si.is_invocation(f, 0, 7) is True

    def test_no_call_on_next_line(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    foo\n    bar\n")
        si = SourceInspector()
        assert si.is_invocation(f, 0, 7) is False

    def test_end_of_file(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    foo")
        si = SourceInspector()
        assert si.is_invocation(f, 0, 7) is False


class TestIsCallableUsage:
    def test_direct_invocation(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    func(args)\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 4, 8) is True

    def test_return_value(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    return handler\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 11, 18) is True

    def test_callback_argument(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    filter(func)\n")
        si = SourceInspector()
        # "func" starts at 11, ends at 15; preceded by unmatched "("
        assert si.is_callable_usage(f, 0, 11, 15) is True

    def test_plain_reference(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    x = func\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 8, 12) is False

    def test_conservative_on_missing_file(self):
        si = SourceInspector()
        assert si.is_callable_usage(Path("/nonexistent.py"), 0, 0, 5) is True


class TestFindCallSites:
    def test_finds_regular_calls(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("foo()\nbar(x)\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (0, 0) in sites  # foo
        assert (1, 0) in sites  # bar

    def test_finds_new_constructor(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("new Dog(name)\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (0, 4) in sites  # Dog in "new Dog("

    def test_finds_method_reference(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("String::valueOf\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (0, 8) in sites  # valueOf

    def test_skips_keywords(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("if (x) {\n    return foo();\n}\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        # "if" and "return" are keywords, should be skipped
        assert not any(s for s in sites if s == (0, 0))  # "if" at 0,0
        assert (1, 11) in sites  # foo

    def test_skips_comments(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("// foo()\n* bar()\n/* baz() */\nreal()\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (3, 0) in sites  # real
        # Comment lines should be skipped entirely
        assert not any(s[0] in (0, 1, 2) for s in sites)

    def test_finds_super_and_this(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("super(name)\nthis(x)\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (0, 0) in sites  # super
        assert (1, 0) in sites  # this

    def test_deduplicates_positions(self, tmp_path: Path):
        f = tmp_path / "test.java"
        # "new Dog(" matches both call_pattern and new_pattern for Dog
        f.write_text("new Dog()\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        # Dog position should appear only once
        dog_positions = [s for s in sites if s == (0, 4)]
        assert len(dog_positions) == 1

    def test_returns_empty_for_missing_file(self):
        si = SourceInspector()
        assert si.find_call_sites(Path("/nonexistent.py")) == []

    def test_generic_call(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("Collections.<String>sort(list)\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        # "sort" should be found via the call pattern
        assert any(s[0] == 0 for s in sites)
