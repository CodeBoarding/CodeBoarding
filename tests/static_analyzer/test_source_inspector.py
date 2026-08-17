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
    def test_object_literal_is_not_a_declaration_body(self, tmp_path: Path):
        f = tmp_path / "Caller.ts"
        source = "const caller = { run: target };\n"
        f.write_text(source)
        target_start = source.index("target")

        assert (
            SourceInspector().is_reference_in_declaration_body(
                f,
                0,
                source.index("caller"),
                0,
                target_start,
                target_start + len("target"),
            )
            is False
        )

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


class TestFindTypeBases:
    def test_csharp_base_class(self, tmp_path: Path):
        f = tmp_path / "Animal.cs"
        f.write_text("abstract class Animal {}\nclass Dog : Animal {}\nclass Cat : Animal {}\n")
        si = SourceInspector()
        assert si.find_type_bases(f) == [("Dog", ["Animal"]), ("Cat", ["Animal"])]

    def test_csharp_generic_interface_reduces_to_its_name(self, tmp_path: Path):
        f = tmp_path / "Repo.cs"
        f.write_text("class Repo : Base, IRepo<Task> {}\n")
        si = SourceInspector()
        assert si.find_type_bases(f) == [("Repo", ["Base", "IRepo"])]

    def test_type_without_bases_is_omitted(self, tmp_path: Path):
        f = tmp_path / "Plain.cs"
        f.write_text("class Plain { void M(){} }\n")
        si = SourceInspector()
        assert si.find_type_bases(f) == []

    def test_positional_record_base_is_the_type_not_its_constructor_argument(self, tmp_path: Path):
        f = tmp_path / "Item.cs"
        f.write_text("record Item(int Id) : Entity(Id), IItem { }\n")
        si = SourceInspector()
        assert si.find_type_bases(f) == [("Item", ["Entity", "IItem"])]

    def test_java_keeps_every_implemented_interface(self, tmp_path: Path):
        f = tmp_path / "Dog.java"
        f.write_text("public class Dog extends Animal implements Walker, Runner {}\n")
        si = SourceInspector()
        assert si.find_type_bases(f) == [("Dog", ["Animal", "Walker", "Runner"])]

    def test_java_interface_extends_list(self, tmp_path: Path):
        f = tmp_path / "Cat.java"
        f.write_text("interface Cat extends Pet, Feline {}\n")
        si = SourceInspector()
        assert si.find_type_bases(f) == [("Cat", ["Pet", "Feline"])]


class TestFindMethodGroupSites:
    def test_handler_passed_as_an_argument_is_a_site(self, tmp_path: Path):
        f = tmp_path / "test.cs"
        f.write_text('class A { void M(){ app.MapGet("/items", GetAllItems); } }\n')
        si = SourceInspector()

        assert (1, 42) not in _positions(si.find_call_sites(f))
        assert (1, 42) in _positions(si.find_method_group_sites(f))

    def test_dotted_handler_resolves_to_the_member(self, tmp_path: Path):
        f = tmp_path / "test.cs"
        f.write_text('class A { void M(){ app.MapGet("/i", Handlers.Create); } }\n')
        si = SourceInspector()
        positions = _positions(si.find_method_group_sites(f))

        assert (1, 47) in positions  # Create
        assert (1, 38) not in positions  # Handlers, the type it hangs off

    def test_named_argument_skips_the_label(self, tmp_path: Path):
        f = tmp_path / "test.cs"
        f.write_text("class A { void M(){ Map(handler: GetAllItems); } }\n")
        si = SourceInspector()
        positions = _positions(si.find_method_group_sites(f))

        assert (1, 34) in positions  # GetAllItems
        assert (1, 25) not in positions  # handler:, the parameter name

    def test_type_mentioned_deeper_in_an_argument_is_not_a_site(self, tmp_path: Path):
        f = tmp_path / "test.cs"
        f.write_text('class A { void M(){ Log("{K}", OrderKind.Retail); Run(x.Where(o => o.T > Limits.Max)); } }\n')
        si = SourceInspector()
        positions = _positions(si.find_method_group_sites(f))

        assert (1, 32) not in positions  # OrderKind
        assert (1, 74) not in positions  # Limits

    def test_leaves_the_invocation_itself_to_find_call_sites(self, tmp_path: Path):
        f = tmp_path / "test.cs"
        f.write_text('class A { void M(){ app.MapGet("/items", GetAllItems); } }\n')
        si = SourceInspector()

        assert (1, 25) in _positions(si.find_call_sites(f))  # MapGet


class TestFindMemberModifiers:
    def test_reports_modifiers_per_declaring_type(self, tmp_path: Path):
        f = tmp_path / "Types.cs"
        f.write_text(
            "class Base { public virtual void V() {} }\n"
            "class Derived : Base {\n"
            "  public override void V() {}\n"
            "  public new void Plain() {}\n"
            "  void IThing.Run() {}\n"
            "}\n"
        )
        si = SourceInspector()
        modifiers = si.find_member_modifiers(f)

        assert modifiers[("Base", "V")] == frozenset({"public", "virtual"})
        assert modifiers[("Derived", "V")] == frozenset({"public", "override"})
        assert modifiers[("Derived", "Plain")] == frozenset({"public", "new"})
        assert modifiers[("Derived", "Run")] == frozenset({"explicit"})


class TestTreeCacheEviction:
    def _write_project(self, tmp_path: Path, count: int) -> list[Path]:
        files = []
        for i in range(count):
            f = tmp_path / f"mod{i}.py"
            f.write_text(f"def caller{i}():\n    target{i}()\n    return other{i}\n")
            files.append(f)
        return files

    def test_evicts_trees_past_the_budget(self, tmp_path: Path):
        files = self._write_project(tmp_path, 20)
        si = SourceInspector(tree_node_budget=1)

        for f in files:
            si._usage_index(f)

        stats = si.cache_stats()
        assert stats["parsed_files"] == 1
        assert stats["trees_evicted"] == 19
        # The derived index is what callers actually need, and it survives.
        assert stats["usage_files"] == 20

    def test_eviction_does_not_change_answers(self, tmp_path: Path):
        files = self._write_project(tmp_path, 20)
        unbounded = SourceInspector(tree_node_budget=10**9)
        evicting = SourceInspector(tree_node_budget=1)

        for f in files:
            assert evicting.find_call_sites(f) == unbounded.find_call_sites(f)
            assert evicting.is_invocation(f, 1, 11) == unbounded.is_invocation(f, 1, 11)
            assert evicting.get_file_lines(f) == unbounded.get_file_lines(f)

        assert evicting.cache_stats()["trees_evicted"] > 0
        assert unbounded.cache_stats()["trees_evicted"] == 0

    def test_reparses_after_eviction(self, tmp_path: Path):
        a, b = self._write_project(tmp_path, 2)
        si = SourceInspector(tree_node_budget=1)

        first = si.find_call_sites(a)
        si.find_call_sites(b)  # evicts a
        assert si.cache_stats()["trees_evicted"] == 1
        assert si.find_call_sites(a) == first

    def test_single_file_larger_than_budget_is_still_usable(self, tmp_path: Path):
        f = tmp_path / "big.py"
        f.write_text("\n".join(f"call{i}()" for i in range(200)))
        si = SourceInspector(tree_node_budget=1)

        assert len(si.find_call_sites(f)) == 200


class TestMethodGroupValuePositions:
    """A method named rather than called is control flow wherever it appears."""

    def _positions_for(self, tmp_path: Path, body: str) -> set:
        f = tmp_path / "test.cs"
        f.write_text(body)
        return _positions(SourceInspector().find_method_group_sites(f))

    def test_event_subscription_is_a_site(self, tmp_path: Path):
        positions = self._positions_for(tmp_path, "class A { void M(){ c.Received += OnMessage; } }\n")
        assert (1, 35) in positions  # OnMessage

    def test_event_unsubscription_is_a_site(self, tmp_path: Path):
        positions = self._positions_for(tmp_path, "class A { void M(){ c.Received -= OnMessage; } }\n")
        assert (1, 35) in positions

    def test_assignment_to_a_delegate_is_a_site(self, tmp_path: Path):
        positions = self._positions_for(tmp_path, "class A { void M(){ Action cb = HandleClick; } }\n")
        assert (1, 33) in positions  # HandleClick

    def test_returned_method_group_is_a_site(self, tmp_path: Path):
        positions = self._positions_for(tmp_path, "class A { Action M(){ return HandleClick; } }\n")
        assert (1, 30) in positions

    def test_expression_body_method_group_is_a_site(self, tmp_path: Path):
        positions = self._positions_for(tmp_path, "class A { Action P => HandleClick; }\n")
        assert (1, 23) in positions

    def test_ordinary_literal_assignment_is_not_queried(self, tmp_path: Path):
        assert self._positions_for(tmp_path, 'class A { void M(){ int x = 5; string s = "a"; } }\n') == set()

    def test_assignment_from_a_call_is_not_queried(self, tmp_path: Path):
        """The invocation is already a call site; the assignment adds nothing."""
        assert self._positions_for(tmp_path, "class A { void M(){ var y = Compute(); } }\n") == set()


class TestConditionalCompilationBodies:
    """C#'s default-interface idiom splits a member body across a ``#if``.

    tree-sitter cannot parse that shape: the subtree becomes ERROR and the call
    is emitted as a constructor declaration, so the call-site walk misses it.
    Serilog's ILogger hides 55 calls this way.
    """

    SPLIT_BODY = (
        "namespace N;\n"
        "public interface ILogger\n"
        "{\n"
        "    void Error<T>(string t, T v)\n"
        "#if FEATURE_DEFAULT_INTERFACE\n"
        "        => Write(1, t, v)\n"
        "#endif\n"
        "    ;\n"
        "    void Write(int l, string t, object v);\n"
        "}\n"
    )

    def test_call_inside_a_guarded_body_is_found(self, tmp_path: Path):
        f = tmp_path / "ILogger.cs"
        f.write_text(self.SPLIT_BODY)
        si = SourceInspector()
        assert (6, 12) in _positions(si.find_call_sites(f))  # Write(1, t, v)

    def test_positions_still_match_the_original_bytes(self, tmp_path: Path):
        f = tmp_path / "ILogger.cs"
        f.write_text(self.SPLIT_BODY)
        si = SourceInspector()
        line = self.SPLIT_BODY.splitlines()[5]
        for site in si.find_call_sites(f):
            if site.line == 6:
                assert line[site.column - 1 :].startswith("Write")

    def test_a_hash_comment_language_is_left_alone(self, tmp_path: Path):
        # ``#`` opens a comment in Python; blanking those lines would be wrong.
        f = tmp_path / "mod.py"
        f.write_text("# call_me()\ndef f():\n    real()\n")
        si = SourceInspector()
        positions = _positions(si.find_call_sites(f))
        assert (3, 5) in positions
        assert not any(p[0] == 1 for p in positions)


class TestTargetTypedNew:
    """C# 9 ``new(...)`` puts the type on the assignment target, not the call site.

    Serilog uses it throughout its wiring, so without it the constructor call
    from one class to another is invisible.
    """

    def test_target_typed_new_is_a_call_site(self, tmp_path: Path):
        f = tmp_path / "Holder.cs"
        f.write_text(
            "namespace N;\nclass Cache { public Cache(int x) {} }\nclass Holder { readonly Cache _c = new(1); }\n"
        )
        si = SourceInspector()
        assert (3, 36) in _positions(si.find_call_sites(f))  # the `new` keyword

    def test_explicit_new_still_targets_the_type_name(self, tmp_path: Path):
        f = tmp_path / "Holder.cs"
        f.write_text(
            "namespace N;\nclass Cache { public Cache(int x) {} }\nclass Holder { readonly Cache _c = new Cache(1); }\n"
        )
        si = SourceInspector()
        assert (3, 40) in _positions(si.find_call_sites(f))  # `Cache`, not `new`
