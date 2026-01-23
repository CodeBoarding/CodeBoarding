import unittest
from unittest.mock import MagicMock, patch

from agents.prompts.abstract_prompt_factory import AbstractPromptFactory
from agents.prompts.prompt_factory import (
    LLMType,
    PromptFactory,
    PromptType,
    get_global_factory,
    get_prompt,
    initialize_global_factory,
)


class TestAbstractPromptFactory(unittest.TestCase):
    def test_abstract_prompt_factory_cannot_instantiate(self):
        # Test that AbstractPromptFactory cannot be instantiated directly
        with self.assertRaises(TypeError):
            AbstractPromptFactory()  # type: ignore[abstract]

    def test_abstract_methods_exist(self):
        # Test that all expected abstract methods are defined
        expected_methods = [
            "get_system_message",
            "get_cluster_grouping_message",
            "get_final_analysis_message",
            "get_feedback_message",
            "get_system_details_message",
            "get_subcfg_details_message",
            "get_cfg_details_message",
            "get_enhance_structure_message",
            "get_details_message",
            "get_planner_system_message",
            "get_expansion_prompt",
            "get_validator_system_message",
            "get_component_validation_component",
            "get_relationships_validation",
            "get_system_diff_analysis_message",
            "get_diff_analysis_message",
            "get_system_meta_analysis_message",
            "get_meta_information_prompt",
            "get_file_classification_message",
            "get_unassigned_files_classification_message",
        ]

        for method_name in expected_methods:
            self.assertTrue(hasattr(AbstractPromptFactory, method_name))


class TestPromptType(unittest.TestCase):
    def test_prompt_type_values(self):
        # Test that PromptType has expected values
        self.assertEqual(PromptType.BIDIRECTIONAL.value, "bidirectional")
        self.assertEqual(PromptType.UNIDIRECTIONAL.value, "unidirectional")

    def test_prompt_type_count(self):
        # Test that we have exactly 2 types
        self.assertEqual(len(PromptType), 2)


class TestLLMType(unittest.TestCase):
    def test_llm_type_values(self):
        # Test that LLMType has expected values
        self.assertEqual(LLMType.GEMINI_FLASH.value, "gemini_flash")
        self.assertEqual(LLMType.CLAUDE_SONNET.value, "claude_sonnet")
        self.assertEqual(LLMType.CLAUDE.value, "claude")
        self.assertEqual(LLMType.GPT4.value, "gpt4")

    def test_llm_type_count(self):
        # Test that we have the expected number of types
        self.assertGreaterEqual(len(LLMType), 4)


class TestPromptFactory(unittest.TestCase):
    def test_factory_creation_gemini_bidirectional(self):
        # Test creating factory for Gemini bidirectional
        factory = PromptFactory(LLMType.GEMINI_FLASH, PromptType.BIDIRECTIONAL)
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)
        self.assertEqual(factory.prompt_type, PromptType.BIDIRECTIONAL)

    def test_factory_creation_gemini_unidirectional(self):
        # Test creating factory for Gemini unidirectional
        factory = PromptFactory(LLMType.GEMINI_FLASH, PromptType.UNIDIRECTIONAL)
        self.assertEqual(factory.prompt_type, PromptType.UNIDIRECTIONAL)

    def test_factory_creation_claude_bidirectional(self):
        # Test creating factory for Claude bidirectional
        factory = PromptFactory(LLMType.CLAUDE, PromptType.BIDIRECTIONAL)
        self.assertEqual(factory.llm_type, LLMType.CLAUDE)
        self.assertEqual(factory.prompt_type, PromptType.BIDIRECTIONAL)

    def test_factory_creation_claude_unidirectional(self):
        # Test creating factory for Claude unidirectional
        factory = PromptFactory(LLMType.CLAUDE_SONNET, PromptType.UNIDIRECTIONAL)
        self.assertEqual(factory.prompt_type, PromptType.UNIDIRECTIONAL)

    def test_factory_creation_gpt4_bidirectional(self):
        # Test creating factory for GPT-4 bidirectional
        factory = PromptFactory(LLMType.GPT4, PromptType.BIDIRECTIONAL)
        self.assertEqual(factory.llm_type, LLMType.GPT4)

    def test_factory_creation_gpt4_unidirectional(self):
        # Test creating factory for GPT-4 unidirectional
        factory = PromptFactory(LLMType.GPT4, PromptType.UNIDIRECTIONAL)
        self.assertEqual(factory.prompt_type, PromptType.UNIDIRECTIONAL)

    def test_get_prompt_success(self):
        # Test getting a valid prompt
        factory = PromptFactory(LLMType.GEMINI_FLASH, PromptType.BIDIRECTIONAL)
        result = factory.get_prompt("system_message")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_get_prompt_invalid(self):
        # Test getting an invalid prompt raises error
        factory = PromptFactory(LLMType.GEMINI_FLASH, PromptType.BIDIRECTIONAL)
        with self.assertRaises(AttributeError):
            factory.get_prompt("nonexistent_prompt")

    def test_get_all_prompts(self):
        # Test getting all prompts from factory
        factory = PromptFactory(LLMType.GEMINI_FLASH, PromptType.BIDIRECTIONAL)
        prompts = factory.get_all_prompts()

        self.assertIsInstance(prompts, dict)
        self.assertGreater(len(prompts), 0)
        self.assertIn("SYSTEM_MESSAGE", prompts)

    def test_create_for_vscode_runnable_unidirectional(self):
        # Test creating for vscode runnable with unidirectional
        factory = PromptFactory.create_for_vscode_runnable(use_unidirectional=True)
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)
        self.assertEqual(factory.prompt_type, PromptType.UNIDIRECTIONAL)

    def test_create_for_vscode_runnable_bidirectional(self):
        # Test creating for vscode runnable with bidirectional
        factory = PromptFactory.create_for_vscode_runnable(use_unidirectional=False)
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)
        self.assertEqual(factory.prompt_type, PromptType.BIDIRECTIONAL)

    def test_create_for_llm_gemini(self):
        # Test creating factory for Gemini
        factory = PromptFactory.create_for_llm("gemini")
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)

    def test_create_for_llm_gemini_flash(self):
        # Test creating factory for Gemini Flash
        factory = PromptFactory.create_for_llm("gemini_flash")
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)

    def test_create_for_llm_claude(self):
        # Test creating factory for Claude
        factory = PromptFactory.create_for_llm("claude")
        self.assertEqual(factory.llm_type, LLMType.CLAUDE)

    def test_create_for_llm_claude_sonnet(self):
        # Test creating factory for Claude Sonnet
        factory = PromptFactory.create_for_llm("claude_sonnet")
        self.assertEqual(factory.llm_type, LLMType.CLAUDE_SONNET)

    def test_create_for_llm_gpt4(self):
        # Test creating factory for GPT-4
        factory = PromptFactory.create_for_llm("gpt4")
        self.assertEqual(factory.llm_type, LLMType.GPT4)

    def test_create_for_llm_gpt4_dash(self):
        # Test creating factory for GPT-4 with dash
        factory = PromptFactory.create_for_llm("gpt-4")
        self.assertEqual(factory.llm_type, LLMType.GPT4)

    def test_create_for_llm_openai(self):
        # Test creating factory for OpenAI (defaults to GPT4)
        factory = PromptFactory.create_for_llm("openai")
        self.assertEqual(factory.llm_type, LLMType.GPT4)

    def test_create_for_llm_unknown_defaults_to_gemini(self):
        # Test that unknown LLM defaults to Gemini Flash
        factory = PromptFactory.create_for_llm("unknown_llm")
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)

    def test_create_for_llm_with_prompt_type(self):
        # Test creating with custom prompt type
        factory = PromptFactory.create_for_llm("gemini", prompt_type=PromptType.UNIDIRECTIONAL)
        self.assertEqual(factory.prompt_type, PromptType.UNIDIRECTIONAL)


class TestGlobalFactory(unittest.TestCase):
    def setUp(self):
        # Reset global factory before each test
        import agents.prompts.prompt_factory as pf

        pf._global_factory = None

    def tearDown(self):
        # Reset global factory after each test
        import agents.prompts.prompt_factory as pf

        pf._global_factory = None

    def test_initialize_global_factory(self):
        # Test initializing global factory
        initialize_global_factory(LLMType.CLAUDE, PromptType.UNIDIRECTIONAL)
        factory = get_global_factory()

        self.assertIsNotNone(factory)
        self.assertEqual(factory.llm_type, LLMType.CLAUDE)
        self.assertEqual(factory.prompt_type, PromptType.UNIDIRECTIONAL)

    def test_get_global_factory_auto_initialize(self):
        # Test that get_global_factory auto-initializes if not set
        factory = get_global_factory()

        self.assertIsNotNone(factory)
        # Should default to Gemini Flash bidirectional
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)
        self.assertEqual(factory.prompt_type, PromptType.BIDIRECTIONAL)

    def test_get_prompt_global(self):
        # Test getting prompt using global factory
        initialize_global_factory()
        result = get_prompt("system_message")

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


class TestConvenienceFunctions(unittest.TestCase):
    def setUp(self):
        # Initialize global factory before each test
        initialize_global_factory()

    def tearDown(self):
        # Reset global factory after each test
        import agents.prompts.prompt_factory as pf

        pf._global_factory = None

    def test_convenience_functions_exist(self):
        # Test that all convenience functions exist
        from agents.prompts import prompt_factory as pf

        convenience_functions = [
            "get_system_message",
            "get_cluster_grouping_message",
            "get_final_analysis_message",
            "get_feedback_message",
            "get_system_details_message",
            "get_subcfg_details_message",
            "get_cfg_details_message",
            "get_enhance_structure_message",
            "get_details_message",
            "get_planner_system_message",
            "get_expansion_prompt",
            "get_validator_system_message",
            "get_component_validation_component",
            "get_relationships_validation",
            "get_system_diff_analysis_message",
            "get_diff_analysis_message",
            "get_system_meta_analysis_message",
            "get_meta_information_prompt",
            "get_file_classification_message",
            "get_unassigned_files_classification_message",
        ]

        for func_name in convenience_functions:
            self.assertTrue(hasattr(pf, func_name))
            func = getattr(pf, func_name)
            self.assertTrue(callable(func))

    def test_convenience_functions_return_strings(self):
        # Test that convenience functions return non-empty strings
        from agents.prompts import prompt_factory as pf

        result = pf.get_system_message()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
