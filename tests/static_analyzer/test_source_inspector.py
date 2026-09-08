"""Tests for static_analyzer.engine.source_inspector.SourceInspector."""

from pathlib import Path

from static_analyzer.engine.models import CallSite, ImportBinding, UsingDirective
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


class TestIsConstructionSite:
    """Which call sites run a constructor, for constructor expansion."""

    def _site(self, source: Path, line: int, column: int) -> CallSite:
        return CallSite.from_lsp_position(file=str(source), line=line, column=column)

    def test_java_new(self, tmp_path: Path):
        f = tmp_path / "Main.java"
        f.write_text("class Main {\n    void run() {\n        Dog dog = new Dog();\n    }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 22)) is True

    def test_a_member_call_is_not_a_construction(self, tmp_path: Path):
        f = tmp_path / "Main.java"
        f.write_text("class Main {\n    void run() {\n        dog.speak();\n    }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 12)) is False

    def test_an_argument_inside_a_construction_is_not_itself_one(self, tmp_path: Path):
        """The position has to be the type being constructed, not merely inside the `new`."""
        f = tmp_path / "Main.java"
        f.write_text("class Main {\n    void run() {\n        new Dog(cat.name());\n    }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 20)) is False

    def test_java_super_call(self, tmp_path: Path):
        f = tmp_path / "Cat.java"
        f.write_text("class Cat extends Animal {\n    Cat(String n) {\n        super(n);\n    }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 8)) is True

    def test_java_constructor_reference(self, tmp_path: Path):
        f = tmp_path / "Main.java"
        f.write_text("class Main {\n    void run() {\n        make(Dog::new);\n    }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 13)) is True

    def test_java_method_reference_is_not_a_construction(self, tmp_path: Path):
        f = tmp_path / "Main.java"
        f.write_text("class Main {\n    void run() {\n        each(Dog::speak);\n    }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 18)) is False

    def test_csharp_target_typed_new(self, tmp_path: Path):
        f = tmp_path / "Holder.cs"
        f.write_text("class Holder\n{\n    private Settings s = new(5);\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 25)) is True

    def test_csharp_base_initializer(self, tmp_path: Path):
        f = tmp_path / "Derived.cs"
        f.write_text("class Derived : Settings\n{\n    public Derived(int n) : base(n) { }\n}\n")
        assert SourceInspector().is_construction_site(self._site(f, 2, 28)) is True

    def test_conservative_on_missing_file(self):
        site = CallSite.from_lsp_position(file="/nonexistent.java", line=0, column=0)
        assert SourceInspector().is_construction_site(site) is False


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

    def test_a_mention_inside_a_callback_body_is_not_an_argument(self, tmp_path: Path):
        """``abp`` here is used by the callback, not handed to ``$``."""
        f = tmp_path / "test.js"
        f.write_text("$(function () { abp.run(); });\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 16, 19) is False

    def test_a_value_passed_directly_still_is(self, tmp_path: Path):
        f = tmp_path / "test.js"
        f.write_text("$(abp);\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 2, 5) is True

    def test_a_receiver_inside_an_argument_is_not_the_value_passed(self, tmp_path: Path):
        f = tmp_path / "test.js"
        f.write_text("show(abp.localization.get(key));\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 5, 8) is False

    def test_a_receiver_inside_a_returned_expression_is_not_the_value_returned(self, tmp_path: Path):
        f = tmp_path / "test.js"
        f.write_text("return { locale: abp.localization.currentCulture };\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 17, 20) is False

    def test_a_python_receiver_is_not_the_value_passed(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("    register(handlers.on_start)\n")
        si = SourceInspector()
        assert si.is_callable_usage(f, 0, 13, 21) is False

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


class TestConstructorDelegation:
    """``: this(...)`` and ``: base(...)`` are calls, and the server resolves
    them from the keyword rather than from any type name."""

    def test_this_initializer_is_a_call_site(self, tmp_path: Path):
        f = tmp_path / "S.cs"
        f.write_text("class S {\n    public S() : this(10) { }\n    public S(int x) { }\n}\n")
        si = SourceInspector()
        assert (2, 18) in _positions(si.find_call_sites(f))  # the `this` keyword

    def test_base_initializer_is_a_call_site(self, tmp_path: Path):
        f = tmp_path / "D.cs"
        f.write_text("class S { public S(int x) { } }\nclass D : S {\n    public D(int x) : base(x) { }\n}\n")
        si = SourceInspector()
        assert (3, 23) in _positions(si.find_call_sites(f))  # the `base` keyword


class TestCollectionInitializer:
    """``new Bag { 1, 2 }`` calls ``Bag.Add`` per element; ``new T { P = 1 }``
    assigns a property and calls nothing."""

    def test_collection_initializer_is_reported(self, tmp_path: Path):
        f = tmp_path / "C.cs"
        f.write_text("class C { void M() { var a = new Bag { 1, 2 }; } }\n")
        si = SourceInspector()
        assert (1, 34) in _positions(si.find_collection_initializer_sites(f))

    def test_object_initializer_is_not(self, tmp_path: Path):
        f = tmp_path / "C.cs"
        f.write_text("class C { void M() { var a = new Thing { Prop = 1 }; } }\n")
        si = SourceInspector()
        assert si.find_collection_initializer_sites(f) == []

    def test_plain_construction_is_not(self, tmp_path: Path):
        f = tmp_path / "C.cs"
        f.write_text("class C { void M() { var a = new Bag(); } }\n")
        si = SourceInspector()
        assert si.find_collection_initializer_sites(f) == []


class TestIteratedExpression:
    def test_foreach_collection_is_reported(self, tmp_path: Path):
        f = tmp_path / "C.cs"
        f.write_text("class C { void M(Bag bag) { foreach (int v in bag) { } } }\n")
        si = SourceInspector()
        assert (1, 47) in _positions(si.find_iterated_expression_sites(f))

    def test_member_access_collection_points_at_the_last_name(self, tmp_path: Path):
        f = tmp_path / "C.cs"
        f.write_text("class C { void M() { foreach (var x in Other.Items) { } } }\n")
        si = SourceInspector()
        positions = _positions(si.find_iterated_expression_sites(f))
        assert (1, 46) in positions  # `Items`, whose type is what gets enumerated


class TestFindTypeReferenceSites:
    def test_csharp_type_positions(self, tmp_path: Path):
        f = tmp_path / "Module.cs"
        f.write_text(
            "using Volo.Abp.Modularity;\n"
            "namespace App;\n"
            "[DependsOn(typeof(CoreModule))]\n"
            "public class AppModule : AbpModule\n"
            "{\n"
            "    public DbSet<Blog> Blogs { get; set; }\n"
            "    private readonly IRepository<Blog, Guid> _repo;\n"
            "    public List<BlogDto> Convert(IEnumerable<Blog> items, Blog? single, Blog[] many)\n"
            "    {\n"
            "        var x = (BlogDto)null; if (single is BlogDto b) { } var o = items as Other;\n"
            "        try { } catch (BusinessException e) { }\n"
            "        return new List<BlogDto>();\n"
            "    }\n"
            "    void Register(ServiceConfigurationContext context) { context.Services.AddTransient<IBlogService, BlogService>(); }\n"
            "    Volo.Abp.Settings.SettingValue Read() { return BlogKind.Draft; }\n"
            "}\n"
        )
        sites = SourceInspector().find_type_reference_sites(f)
        names = {(site.line, site.name) for site in sites}
        assert (3, "CoreModule") in names
        assert (4, "AbpModule") in names
        assert {(6, "DbSet"), (6, "Blog"), (7, "IRepository"), (7, "Guid")} <= names
        assert {(8, "List"), (8, "BlogDto"), (8, "IEnumerable"), (8, "Blog")} <= names
        assert {(10, "BlogDto"), (10, "Other")} <= names
        assert (11, "BusinessException") in names
        assert {(14, "IBlogService"), (14, "BlogService")} <= names
        assert (15, "BlogKind") in names
        qualified = next(site for site in sites if site.name == "SettingValue")
        assert qualified.qualifier == "Volo.Abp.Settings"
        assert not any(site.name in ("var", "void", "AppModule", "DependsOn") for site in sites)
        # a member-access receiver is a candidate too; ``context`` fails to resolve later, ``BlogKind`` resolves
        assert (14, "context") in names
        # ``new List<BlogDto>()`` names the created type only through its constructor call
        assert [site.name for site in sites if site.line == 12] == ["BlogDto"]

    def test_typescript_type_positions(self, tmp_path: Path):
        f = tmp_path / "blog.ts"
        f.write_text(
            "import { BlogService } from './blog.service';\n"
            "@NgModule({ imports: [CoreModule], providers: [{ provide: TOKEN, useClass: Impl }] })\n"
            "export class BlogModule extends BaseModule implements OnInit {\n"
            "  items: BlogDto[] = [];\n"
            "  constructor(private svc: BlogService, cfg: Config<BlogDto>) { super(); }\n"
            "  load(id: string): Observable<BlogDto> { return this.svc.get(id) as BlogDto; }\n"
            "  make(): Foo { return new Foo(); }\n"
            "}\n"
            "export interface Shape extends Base<Q> { a: Qux; }\n"
        )
        sites = SourceInspector().find_type_reference_sites(f)
        names = {(site.line, site.name) for site in sites}
        assert {(2, "CoreModule"), (2, "Impl")} <= names
        assert {(3, "BaseModule"), (3, "OnInit")} <= names
        assert (4, "BlogDto") in names
        assert {(5, "BlogService"), (5, "Config"), (5, "BlogDto")} <= names
        assert {(6, "Observable"), (6, "BlogDto")} <= names
        assert {(9, "Base"), (9, "Q"), (9, "Qux")} <= names
        assert not any(site.name in ("BlogModule", "Shape", "string", "NgModule") for site in sites)
        assert [site.name for site in sites if site.line == 7] == ["Foo"]

    def test_javascript_heritage_is_a_type_position(self, tmp_path: Path):
        f = tmp_path / "a.js"
        f.write_text("class A extends B { make() { return new C(); } }\n")
        sites = SourceInspector().find_type_reference_sites(f)
        assert [site.name for site in sites] == ["B"]

    def test_a_language_without_a_selector_has_no_type_sites(self, tmp_path: Path):
        f = tmp_path / "a.rs"
        f.write_text("struct A { b: B }\nimpl C for A { fn m(&self, x: D) -> E { }}\n")
        assert SourceInspector().find_type_reference_sites(f) == []

    def test_a_language_without_a_selector_is_never_parsed(self, tmp_path: Path):
        """Why: the pass runs over every source file, and a parse thrown away is the whole cost."""
        f = tmp_path / "a.rs"
        f.write_text("struct A { b: B }\n")
        inspector = SourceInspector()
        assert inspector.find_type_reference_sites(f) == []
        assert inspector.cache_stats()["parsed_files"] == 0


class TestCSharpTypeNameNormalization:
    """The written name is reduced to what the index is keyed by, and the prefix to a namespace."""

    def _sites(self, tmp_path: Path, body: str) -> dict[str, tuple[str, str]]:
        f = tmp_path / "a.cs"
        f.write_text(body)
        return {s.name: (s.name, s.qualifier) for s in SourceInspector().find_type_reference_sites(f)}

    def test_a_generic_written_with_a_namespace_keeps_only_the_outer_name(self, tmp_path: Path):
        sites = self._sites(tmp_path, "class K { Lib.Domain.Box<Lib.Domain.Order> F; }\n")
        # Not "Box<Lib.Domain.Order>", and not the "Order>" a last-dot split would give.
        assert sites["Box"] == ("Box", "Lib.Domain")
        assert sites["Order"] == ("Order", "Lib.Domain")

    def test_an_extern_alias_root_is_stripped_from_the_qualifier(self, tmp_path: Path):
        sites = self._sites(tmp_path, "class K { global::Lib.Domain.Order F; }\n")
        assert sites["Order"] == ("Order", "Lib.Domain")

    def test_a_plain_qualified_name_is_unchanged(self, tmp_path: Path):
        sites = self._sites(tmp_path, "class K { Lib.Domain.Order F; }\n")
        assert sites["Order"] == ("Order", "Lib.Domain")


class TestJavaTypeSites:
    def _sites(self, tmp_path: Path, body: str) -> set[tuple[str, str]]:
        f = tmp_path / "S.java"
        f.write_text(body)
        return {(s.name, s.qualifier) for s in SourceInspector().find_type_reference_sites(f)}

    def test_every_type_position_java_writes(self, tmp_path: Path):
        sites = self._sites(
            tmp_path,
            "package com.example;\n"
            "@Component\n"
            "public class Service extends BaseService implements Runnable {\n"
            "  private java.util.List<Widget> all;\n"
            "  public Repo find(Query q) throws NotFound {\n"
            "    Object o = (Widget) all; if (o instanceof Widget w) { }\n"
            "    try { } catch (BadThing e) { }\n"
            "    return null;\n"
            "  }\n"
            "}\n",
        )
        assert {("BaseService", ""), ("Runnable", ""), ("Component", "")} <= sites
        assert ("List", "java.util") in sites and ("Widget", "") in sites
        assert {("Repo", ""), ("Query", ""), ("NotFound", ""), ("BadThing", "")} <= sites
        # The class names itself with a plain identifier, so a declaration is never a site.
        assert not any(name == "Service" for name, _ in sites)

    def test_a_created_type_is_left_to_the_constructor_call(self, tmp_path: Path):
        sites = self._sites(tmp_path, "class S { void m() { Widget w = new Other(); } }\n")
        assert ("Widget", "") in sites
        assert ("Other", "") not in sites


class TestPythonTypeSites:
    def _sites(self, tmp_path: Path, body: str) -> set[tuple[str, str]]:
        f = tmp_path / "a.py"
        f.write_text(body)
        return {(s.name, s.qualifier) for s in SourceInspector().find_type_reference_sites(f)}

    def test_annotations_bases_and_nested_generics(self, tmp_path: Path):
        sites = self._sites(
            tmp_path,
            "class Service(BaseService, Runnable):\n"
            "    widget: Widget\n"
            "    items: list[Nested]\n"
            "    def find(self, q: Query, o: Optional[Deep]) -> Result: ...\n"
            "    def use(self, x: mod.Thing): ...\n",
        )
        assert {("BaseService", ""), ("Runnable", ""), ("Widget", "")} <= sites
        assert {("list", ""), ("Nested", ""), ("Optional", ""), ("Deep", "")} <= sites
        assert {("Query", ""), ("Result", ""), ("Thing", "mod")} <= sites
        assert not any(name == "Service" for name, _ in sites)

    def test_a_call_is_not_a_type_position(self, tmp_path: Path):
        assert self._sites(tmp_path, "def m():\n    return Widget()\n") == set()


class TestGoTypeSites:
    def _sites(self, tmp_path: Path, body: str) -> set[tuple[str, str]]:
        f = tmp_path / "s.go"
        f.write_text(body)
        return {(s.name, s.qualifier) for s in SourceInspector().find_type_reference_sites(f)}

    def test_qualified_and_bare_types(self, tmp_path: Path):
        sites = self._sites(
            tmp_path,
            "package app\n"
            'import core "example.com/x/core"\n'
            "type Service struct { widget core.Widget; items []core.Thing; local Helper }\n"
            "func (s *Service) Find(q *core.Query) (Result, error) { return nil, nil }\n",
        )
        assert {("Widget", "core"), ("Thing", "core"), ("Query", "core")} <= sites
        assert {("Helper", ""), ("Result", "")} <= sites
        # Neither the type being declared nor the receiver of its own method is a reference to it.
        assert ("Service", "") not in sites
        assert not any(name == "app" for name, _ in sites)

    def test_a_declaration_is_not_a_site(self, tmp_path: Path):
        assert self._sites(tmp_path, "package app\ntype Widget struct { }\n") == set()

    def test_a_method_does_not_depend_on_its_own_receiver(self, tmp_path: Path):
        """The receiver names the type the method is on: containment, which CONTAINS already carries."""
        sites = self._sites(
            tmp_path,
            "package app\n"
            "func (e *Entity) Set(t string) { }\n"
            'func (c Cat) Name() string { return "" }\n'
            "func Free(x *Widget) *Result { return nil }\n",
        )
        assert ("Entity", "") not in sites and ("Cat", "") not in sites
        # A genuine parameter and return type are still references.
        assert {("Widget", ""), ("Result", "")} <= sites


class TestGoImportBindings:
    def test_aliased_and_bare_imports(self, tmp_path: Path):
        f = tmp_path / "s.go"
        f.write_text(
            'package app\nimport (\n  core "example.com/x/core"\n  "example.com/x/util"\n  _ "side/effect"\n)\n'
        )
        bindings = SourceInspector().find_import_bindings(f)
        assert bindings["core"] == ImportBinding("example.com/x/core", "*")
        # Unaliased, a package is named by the last segment of its path.
        assert bindings["util"] == ImportBinding("example.com/x/util", "*")
        assert "_" not in bindings


class TestPhpTypeSites:
    def test_heritage_properties_parameters_and_returns(self, tmp_path: Path):
        f = tmp_path / "s.php"
        f.write_text(
            "<?php\n"
            "namespace App\\Models;\n"
            "class Service extends BaseService implements Runnable {\n"
            "  private Widget $widget;\n"
            "  public function find(Alias $a, \\App\\Other\\Deep $d): Result { }\n"
            "}\n"
        )
        sites = {(s.name, s.qualifier) for s in SourceInspector().find_type_reference_sites(f)}
        assert {("BaseService", ""), ("Runnable", ""), ("Widget", ""), ("Alias", ""), ("Result", "")} <= sites
        # A fully qualified name keeps its namespace, whose separator is normalised to a dot.
        assert ("Deep", "App.Other") in sites


class TestPhpNamespaceContext:
    def test_namespace_and_use_clauses(self, tmp_path: Path):
        f = tmp_path / "s.php"
        f.write_text("<?php\nnamespace App\\Models;\nuse App\\Core\\Widget;\nuse App\\Util\\Thing as Alias;\n")
        context = SourceInspector().find_namespace_context(f)
        assert context.namespaces[0][0] == "App.Models"
        # Every ``use`` binds one name, so each is recorded the way an alias is.
        assert [(d.target, d.alias) for d in context.usings] == [
            ("App.Core.Widget", "Widget"),
            ("App.Util.Thing", "Alias"),
        ]


class TestJavaNamespaceContext:
    def test_package_imports_wildcards_and_static(self, tmp_path: Path):
        f = tmp_path / "S.java"
        f.write_text(
            "package com.example.app;\n"
            "import com.example.core.Widget;\n"
            "import com.example.util.*;\n"
            "import static com.example.util.Helpers.check;\n"
            "class S { }\n"
        )
        context = SourceInspector().find_namespace_context(f)
        assert context.namespaces[0][0] == "com.example.app"
        # A single-type import decides a name, exactly as a C# alias does; a wildcard offers a
        # package; a static import offers members and not the type itself.
        assert [(d.target, d.alias, d.static) for d in context.usings] == [
            ("com.example.core.Widget", "Widget", False),
            ("com.example.util", "", False),
            ("com.example.util.Helpers.check", "", True),
        ]


class TestPythonImportBindings:
    def test_absolute_relative_and_aliased_imports(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text(
            "from a.b import Widget, Other as Alias\n"
            "from .mod import Nearby\n"
            "from . import sibling\n"
            "import pkg.sub\n"
            "import pkg.sub as ps\n"
        )
        bindings = SourceInspector().find_import_bindings(f)
        assert bindings["Widget"] == ImportBinding("a.b", "Widget")
        assert bindings["Alias"] == ImportBinding("a.b", "Other")
        assert bindings["Nearby"] == ImportBinding(".mod", "Nearby")
        assert bindings["sibling"] == ImportBinding(".", "sibling")
        # ``import a.b`` binds the dotted name, because that is what a type site writes.
        assert bindings["pkg.sub"] == ImportBinding("pkg.sub", "*")
        assert bindings["ps"] == ImportBinding("pkg.sub", "*")


class TestFindNamespaceContext:
    def test_block_and_nested_namespaces_with_ranges(self, tmp_path: Path):
        f = tmp_path / "a.cs"
        f.write_text(
            "using System;\n"
            "global using Volo.Abp;\n"
            "namespace A.B\n"
            "{\n"
            "    using Inner.Use;\n"
            "    namespace C { class K { } }\n"
            "}\n"
        )
        context = SourceInspector().find_namespace_context(f)
        # A file-level directive governs the whole file; the block-level one governs its block.
        assert [(d.target, d.first_line) for d in context.usings] == [("System", 1), ("Volo.Abp", 1), ("Inner.Use", 3)]
        assert [d.last_line for d in context.usings] == [8, 8, 7]
        assert context.namespaces == (("A.B", 3, 7), ("A.B.C", 6, 6))

    def test_a_file_scoped_namespace_spans_the_rest_of_the_file(self, tmp_path: Path):
        f = tmp_path / "a.cs"
        f.write_text("namespace Volo.CmsKit;\npublic class X { }\npublic class Y { }\n")
        ((name, start, end),) = SourceInspector().find_namespace_context(f).namespaces
        assert (name, start) == ("Volo.CmsKit", 1)
        assert end >= 3

    def test_an_alias_using_keeps_both_its_name_and_its_target(self, tmp_path: Path):
        f = tmp_path / "a.cs"
        f.write_text("using Alias = Volo.Abp.Foo;\n")
        (directive,) = SourceInspector().find_namespace_context(f).usings
        assert (directive.target, directive.alias, directive.static) == ("Volo.Abp.Foo", "Alias", False)

    def test_a_static_using_is_marked_as_one(self, tmp_path: Path):
        f = tmp_path / "a.cs"
        f.write_text("using static Volo.Abp.Check;\n")
        (directive,) = SourceInspector().find_namespace_context(f).usings
        assert (directive.target, directive.alias, directive.static) == ("Volo.Abp.Check", "", True)

    def test_an_extern_alias_root_is_not_part_of_the_target(self, tmp_path: Path):
        """``global::`` names an assembly; keeping it made the directive match no namespace."""
        f = tmp_path / "a.cs"
        f.write_text("using global::System.Collections;\nusing global::System;\n")
        assert [d.target for d in SourceInspector().find_namespace_context(f).usings] == [
            "System.Collections",
            "System",
        ]

    def test_other_languages_are_empty(self, tmp_path: Path):
        f = tmp_path / "a.ts"
        f.write_text("namespace A { }\n")
        assert SourceInspector().find_namespace_context(f).namespaces == ()


class TestFindImportBindings:
    def test_named_alias_namespace_and_default_imports(self, tmp_path: Path):
        f = tmp_path / "a.ts"
        f.write_text(
            "import { A, B as Bee } from './mod';\n"
            "import * as NS from '../ns';\n"
            'import D from "./d";\n'
            "import type { T1 } from './t';\n"
            "export { X } from './x';\n"
        )
        assert SourceInspector().find_import_bindings(f) == {
            "A": ImportBinding("./mod", "A"),
            "Bee": ImportBinding("./mod", "B"),
            "NS": ImportBinding("../ns", "*"),
            "D": ImportBinding("./d", "default"),
            "T1": ImportBinding("./t", "T1"),
        }

    def test_other_languages_are_empty(self, tmp_path: Path):
        f = tmp_path / "a.cs"
        f.write_text("using A;\n")
        assert SourceInspector().find_import_bindings(f) == {}
