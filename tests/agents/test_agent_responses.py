import unittest

from agents.agent_responses import (
    SourceCodeReference,
    Relation,
    Component,
    FileMethodGroup,
    AnalysisInsights,
    ROOT_PARENT_ID,
    hash_component_id,
)


class TestSourceCodeReference(unittest.TestCase):

    def test_create_basic_reference(self):
        # Test creating a basic source code reference
        ref = SourceCodeReference(
            qualified_name="mymodule.MyClass",
            reference_file="mymodule/class.py",
            reference_start_line=10,
            reference_end_line=50,
        )
        self.assertEqual(ref.qualified_name, "mymodule.MyClass")
        self.assertEqual(ref.reference_file, "mymodule/class.py")
        self.assertEqual(ref.reference_start_line, 10)
        self.assertEqual(ref.reference_end_line, 50)

    def test_reference_without_lines(self):
        # Test reference without line numbers
        ref = SourceCodeReference(
            qualified_name="mymodule.function",
            reference_file="mymodule/utils.py",
            reference_start_line=None,
            reference_end_line=None,
        )
        llm_str = ref.llm_str()
        self.assertIn("mymodule.function", llm_str)
        self.assertIn("mymodule/utils.py", llm_str)
        self.assertNotIn("Lines:", llm_str)

    def test_reference_llm_str_with_lines(self):
        # Test LLM string representation with line numbers
        ref = SourceCodeReference(
            qualified_name="mymodule.MyClass.method",
            reference_file="mymodule/class.py",
            reference_start_line=25,
            reference_end_line=35,
        )
        llm_str = ref.llm_str()
        self.assertIn("mymodule.MyClass.method", llm_str)
        self.assertIn("mymodule/class.py", llm_str)
        self.assertIn("Lines:(25:35)", llm_str)

    def test_reference_str_representation(self):
        # Test string representation
        ref = SourceCodeReference(
            qualified_name="mymodule.function",
            reference_file="mymodule/utils.py",
            reference_start_line=5,
            reference_end_line=15,
        )
        str_repr = str(ref)
        self.assertIn("mymodule.function", str_repr)
        self.assertIn("5-15", str_repr)

    def test_reference_invalid_lines(self):
        # Test with invalid line numbers (same start and end)
        ref = SourceCodeReference(
            qualified_name="test", reference_file="test.py", reference_start_line=10, reference_end_line=10
        )
        llm_str = ref.llm_str()
        self.assertNotIn("Lines:", llm_str)


class TestRelation(unittest.TestCase):

    def test_create_relation(self):
        # Test creating a relation
        rel = Relation(relation="uses", src_name="ComponentA", dst_name="ComponentB")
        self.assertEqual(rel.relation, "uses")
        self.assertEqual(rel.src_name, "ComponentA")
        self.assertEqual(rel.dst_name, "ComponentB")

    def test_relation_llm_str(self):
        # Test LLM string representation
        rel = Relation(relation="depends on", src_name="Frontend", dst_name="Backend")
        llm_str = rel.llm_str()
        self.assertEqual(llm_str, "(Frontend, depends on, Backend)")

    def test_relation_with_special_chars(self):
        # Test relation with complex names
        rel = Relation(relation="implements", src_name="User.Service", dst_name="IUserService")
        llm_str = rel.llm_str()
        self.assertIn("User.Service", llm_str)
        self.assertIn("IUserService", llm_str)


class TestComponent(unittest.TestCase):

    def test_create_component(self):
        # Test creating a component with references
        ref1 = SourceCodeReference(
            qualified_name="myapp.UserService",
            reference_file="myapp/services.py",
            reference_start_line=10,
            reference_end_line=50,
        )
        ref2 = SourceCodeReference(
            qualified_name="myapp.User", reference_file="myapp/models.py", reference_start_line=5, reference_end_line=20
        )

        component = Component(
            name="User Management",
            description="Handles user authentication and authorization",
            key_entities=[ref1, ref2],
        )

        self.assertEqual(component.name, "User Management")
        self.assertEqual(len(component.key_entities), 2)

    def test_component_llm_str(self):
        # Test LLM string representation
        ref = SourceCodeReference(
            qualified_name="myapp.Service",
            reference_file="myapp/service.py",
            reference_start_line=None,
            reference_end_line=None,
        )
        component = Component(name="Core Service", description="Main service component", key_entities=[ref])

        llm_str = component.llm_str()
        self.assertIn("Core Service", llm_str)
        self.assertIn("Main service component", llm_str)
        self.assertIn("myapp.Service", llm_str)

    def test_component_with_files(self):
        # Test component with assigned files
        ref = SourceCodeReference(
            qualified_name="myapp.Service",
            reference_file="myapp/service.py",
            reference_start_line=None,
            reference_end_line=None,
        )
        component = Component(
            name="Service",
            description="Service layer",
            key_entities=[ref],
            file_methods=[FileMethodGroup(file_path="myapp/service.py"), FileMethodGroup(file_path="myapp/utils.py")],
        )

        self.assertEqual(len(component.file_methods), 2)


class TestAnalysisInsights(unittest.TestCase):

    def test_create_analysis_insights(self):
        # Test creating analysis insights
        ref = SourceCodeReference(
            qualified_name="app.main", reference_file="app/main.py", reference_start_line=None, reference_end_line=None
        )
        component = Component(name="Main", description="Entry point", key_entities=[ref])
        relation = Relation(relation="uses", src_name="Main", dst_name="Database")

        insights = AnalysisInsights(
            description="Application entry point and data layer",
            components=[component],
            components_relations=[relation],
        )

        self.assertEqual(insights.description, "Application entry point and data layer")
        self.assertEqual(len(insights.components), 1)
        self.assertEqual(len(insights.components_relations), 1)

    def test_analysis_insights_llm_str(self):
        # Test LLM string representation
        ref = SourceCodeReference(
            qualified_name="app.service",
            reference_file="app/service.py",
            reference_start_line=None,
            reference_end_line=None,
        )
        component = Component(name="Service", description="Business logic", key_entities=[ref])
        relation = Relation(relation="depends on", src_name="Service", dst_name="Repository")

        insights = AnalysisInsights(
            description="Service layer architecture", components=[component], components_relations=[relation]
        )

        llm_str = insights.llm_str()
        self.assertIn("Service", llm_str)
        self.assertIn("Business logic", llm_str)

    def test_empty_analysis_insights(self):
        # Test with no components
        insights = AnalysisInsights(description="Empty analysis", components=[], components_relations=[])

        llm_str = insights.llm_str()
        self.assertIn("No abstract components found", llm_str)

    def test_reference_list_validation(self):
        # Test that key_entities cannot be empty per field description
        ref = SourceCodeReference(
            qualified_name="test.func", reference_file="test.py", reference_start_line=None, reference_end_line=None
        )
        # Should work with non-empty list
        component = Component(name="Test", description="Test component", key_entities=[ref])
        self.assertEqual(len(component.key_entities), 1)


class TestComponentIds(unittest.TestCase):

    def test_hash_component_id_is_deterministic(self):
        component_id_a = hash_component_id(ROOT_PARENT_ID, "ComponentA")
        component_id_b = hash_component_id(ROOT_PARENT_ID, "ComponentA")

        self.assertEqual(component_id_a, component_id_b)

    def test_hash_component_id_format_is_16_hex_chars(self):
        component_id = hash_component_id(ROOT_PARENT_ID, "ComponentA")

        self.assertEqual(len(component_id), 16)
        self.assertRegex(component_id, r"^[0-9a-f]{16}$")

    def test_hash_component_id_uses_sibling_index(self):
        first = hash_component_id(ROOT_PARENT_ID, "ComponentA", sibling_index=0)
        second = hash_component_id(ROOT_PARENT_ID, "ComponentA", sibling_index=1)

        self.assertNotEqual(first, second)
