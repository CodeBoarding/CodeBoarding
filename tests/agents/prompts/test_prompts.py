import unittest
from unittest.mock import MagicMock, patch

from agents.prompts.abstract_prompt_factory import AbstractPromptFactory
from agents.prompts.prompt_factory import (
    LLMType,
    PromptFactory,
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
            "get_planner_system_message",
            "get_expansion_prompt",
            "get_system_meta_analysis_message",
            "get_meta_information_prompt",
            "get_file_classification_message",
            "get_unassigned_files_classification_message",
            "get_validation_feedback_message",
            "get_system_details_message",
            "get_cfg_details_message",
            "get_details_message",
        ]

        for method_name in expected_methods:
            self.assertTrue(hasattr(AbstractPromptFactory, method_name))


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
    def test_factory_creation_gemini(self):
        # Test creating factory for Gemini
        factory = PromptFactory(LLMType.GEMINI_FLASH)
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)

    def test_factory_creation_claude(self):
        # Test creating factory for Claude
        factory = PromptFactory(LLMType.CLAUDE)
        self.assertEqual(factory.llm_type, LLMType.CLAUDE)

    def test_factory_creation_claude_sonnet(self):
        # Test creating factory for Claude Sonnet
        factory = PromptFactory(LLMType.CLAUDE_SONNET)
        self.assertEqual(factory.llm_type, LLMType.CLAUDE_SONNET)

    def test_factory_creation_gpt4(self):
        # Test creating factory for GPT-4
        factory = PromptFactory(LLMType.GPT4)
        self.assertEqual(factory.llm_type, LLMType.GPT4)

    def test_get_prompt_success(self):
        # Test getting a valid prompt
        factory = PromptFactory(LLMType.GEMINI_FLASH)
        result = factory.get_prompt("system_message")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_get_prompt_invalid(self):
        # Test getting an invalid prompt raises error
        factory = PromptFactory(LLMType.GEMINI_FLASH)
        with self.assertRaises(AttributeError):
            factory.get_prompt("nonexistent_prompt")

    def test_get_all_prompts(self):
        factory = PromptFactory(LLMType.GEMINI_FLASH)
        prompts = factory.get_all_prompts()

        self.assertIsInstance(prompts, dict)
        self.assertGreater(len(prompts), 0)
        self.assertIn("SYSTEM_MESSAGE", prompts)


class TestLLMTypeFromModelName(unittest.TestCase):
    def test_from_model_name_gemini(self):
        self.assertEqual(LLMType.from_model_name("gemini-1.5-pro"), LLMType.GEMINI_FLASH)
        self.assertEqual(LLMType.from_model_name("gemini-2.5-flash"), LLMType.GEMINI_FLASH)
        self.assertEqual(LLMType.from_model_name("gemini"), LLMType.GEMINI_FLASH)

    def test_from_model_name_gpt(self):
        self.assertEqual(LLMType.from_model_name("gpt-4"), LLMType.GPT4)
        self.assertEqual(LLMType.from_model_name("gpt-4o"), LLMType.GPT4)
        self.assertEqual(LLMType.from_model_name("gpt4"), LLMType.GPT4)
        self.assertEqual(LLMType.from_model_name("o1-preview"), LLMType.GPT4)

    def test_from_model_name_claude(self):
        self.assertEqual(LLMType.from_model_name("claude"), LLMType.CLAUDE)
        self.assertEqual(LLMType.from_model_name("claude-3-opus"), LLMType.CLAUDE)
        self.assertEqual(LLMType.from_model_name("sonnet"), LLMType.CLAUDE)

    def test_from_model_name_deepseek(self):
        self.assertEqual(LLMType.from_model_name("deepseek-chat"), LLMType.DEEPSEEK)
        self.assertEqual(LLMType.from_model_name("deepseek-v3"), LLMType.DEEPSEEK)

    def test_from_model_name_glm(self):
        self.assertEqual(LLMType.from_model_name("glm-4"), LLMType.GLM)
        self.assertEqual(LLMType.from_model_name("glm-4-flash"), LLMType.GLM)

    def test_from_model_name_kimi(self):
        self.assertEqual(LLMType.from_model_name("kimi-k2.5"), LLMType.KIMI)
        self.assertEqual(LLMType.from_model_name("moonshot-v1"), LLMType.KIMI)

    def test_from_model_name_unknown_defaults_to_gemini(self):
        self.assertEqual(LLMType.from_model_name("unknown-model"), LLMType.GEMINI_FLASH)
        self.assertEqual(LLMType.from_model_name("llama-70b"), LLMType.GEMINI_FLASH)


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
        initialize_global_factory(LLMType.CLAUDE)
        factory = get_global_factory()

        self.assertIsNotNone(factory)
        self.assertEqual(factory.llm_type, LLMType.CLAUDE)

    def test_get_global_factory_auto_initialize(self):
        # Test that get_global_factory auto-initializes if not set
        factory = get_global_factory()

        self.assertIsNotNone(factory)
        # Should default to Gemini Flash
        self.assertEqual(factory.llm_type, LLMType.GEMINI_FLASH)

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
            "get_planner_system_message",
            "get_expansion_prompt",
            "get_system_meta_analysis_message",
            "get_meta_information_prompt",
            "get_file_classification_message",
            "get_unassigned_files_classification_message",
            "get_validation_feedback_message",
            "get_system_details_message",
            "get_cfg_details_message",
            "get_details_message",
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
