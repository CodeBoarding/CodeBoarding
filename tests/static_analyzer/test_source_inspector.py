"""Tests for static_analyzer.engine.source_inspector.SourceInspector."""

from pathlib import Path

from static_analyzer.engine.models import CallSite
from static_analyzer.engine.source_inspector import SourceInspector


def _positions(sites: list[CallSite]) -> set[tuple[int, int]]:
    return {(site.line, site.column) for site in sites}


def test_call_site_exposes_human_and_lsp_positions() -> None:
    site = CallSite.from_lsp_position(file="/tmp/app.py", line=0, column=4)

    assert site.line == 1
    assert site.column == 5
    assert site.human_line == 1
    assert site.human_column == 5
    assert site.lsp_line == 0
    assert site.lsp_column == 4


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
        # This is not a valid Python call expression, so tree-sitter does not treat it as an invocation.
        assert si.is_invocation(f, 0, 7) is False

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


class TestIsReferenceInDeclarationBody:
    def test_reference_in_block_body(self, tmp_path: Path):
        f = tmp_path / "Caller.cs"
        source = "class Caller { string Call() { return Target(); } }\n"
        f.write_text(source)
        start = source.index("Target")

        declaration_start = source.index("Call")

        assert (
            SourceInspector().is_reference_in_declaration_body(
                f,
                0,
                declaration_start,
                0,
                start,
                start + len("Target"),
            )
            is True
        )

    def test_expression_body_requires_opt_in(self, tmp_path: Path):
        f = tmp_path / "Caller.cs"
        source = "class Caller { string Call() => Target(); }\n"
        f.write_text(source)
        start = source.index("Target")
        si = SourceInspector()
        declaration_start = source.index("Call")

        assert (
            si.is_reference_in_declaration_body(
                f,
                0,
                declaration_start,
                0,
                start,
                start + len("Target"),
            )
            is False
        )
        assert (
            si.is_reference_in_declaration_body(
                f,
                0,
                declaration_start,
                0,
                start,
                start + len("Target"),
                include_expression_body=True,
            )
            is True
        )

    def test_constructor_initializer_is_outside_body(self, tmp_path: Path):
        f = tmp_path / "Cat.cs"
        source = "class Cat : Animal { public Cat(string name) : base(name) {} }\n"
        f.write_text(source)
        start = source.index("base")
        declaration_start = source.index("Cat(string")

        assert (
            SourceInspector().is_reference_in_declaration_body(
                f,
                0,
                declaration_start,
                0,
                start,
                start + len("base"),
                include_expression_body=True,
            )
            is False
        )

    def test_outer_block_does_not_count_as_local_declaration_body(self, tmp_path: Path):
        f = tmp_path / "Outer.cs"
        source = "class Outer { void Body() { void Local(Target value) {} } }\n"
        f.write_text(source)
        declaration_start = source.index("Local")
        ref_start = source.index("Target")

        assert (
            SourceInspector().is_reference_in_declaration_body(
                f,
                0,
                declaration_start,
                0,
                ref_start,
                ref_start + len("Target"),
                include_expression_body=True,
            )
            is False
        )


class TestFindCallSites:
    def test_finds_regular_calls(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("foo()\nbar(x)\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        positions = _positions(sites)
        assert (1, 1) in positions  # foo
        assert (2, 1) in positions  # bar

    def test_finds_new_constructor(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("new Dog(name)\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (1, 5) in _positions(sites)  # Dog in "new Dog("

    def test_finds_method_reference(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("String::valueOf\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (1, 9) in _positions(sites)  # valueOf

    def test_skips_keywords(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("if (x) {\n    return foo();\n}\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        # "if" and "return" are keywords, should be skipped
        positions = _positions(sites)
        assert (1, 1) not in positions  # "if" at 1,1
        assert (2, 12) in positions  # foo

    def test_skips_comments(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("// foo()\n/* bar()\n   baz() */\nclass A { void m(){ real(); } }\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        assert (4, 21) in _positions(sites)  # real
        # Comment lines should be skipped entirely
        assert not any(site.line in (1, 2, 3) for site in sites)

    def test_finds_super_and_this(self, tmp_path: Path):
        f = tmp_path / "test.java"
        f.write_text("class A extends B { A(){ super(name); } }\nclass C { C(){ this(1); } }\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        positions = _positions(sites)
        assert (1, 26) in positions  # super
        assert (2, 16) in positions  # this

    def test_deduplicates_positions(self, tmp_path: Path):
        f = tmp_path / "test.java"
        # "new Dog(" matches both call_pattern and new_pattern for Dog
        f.write_text("new Dog()\n")
        si = SourceInspector()
        sites = si.find_call_sites(f)
        # Dog position should appear only once
        dog_positions = [site for site in sites if (site.line, site.column) == (1, 5)]
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
        assert any(site.line == 1 for site in sites)

    def test_uses_shared_constants_for_module_suffixes(self, tmp_path: Path):
        f = tmp_path / "test.mjs"
        f.write_text("foo()\n")
        si = SourceInspector()

        assert (1, 1) in _positions(si.find_call_sites(f))
