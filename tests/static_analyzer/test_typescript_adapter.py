"""The TypeScript adapter must configure the language server for deterministic answers."""

from static_analyzer.engine.adapters.typescript_adapter import JavaScriptAdapter, TypeScriptAdapter


class TestInitOptions:
    def test_only_the_semantic_tsserver_answers_queries(self):
        # Why: the default "auto" mode answers references from a syntax-only server while a
        # project loads, which resolves nothing typed through a callback parameter.
        opts = TypeScriptAdapter().get_lsp_init_options()
        assert opts["tsserver"]["useSyntaxServer"] == "never"

    def test_no_background_typings_acquisition(self):
        opts = TypeScriptAdapter().get_lsp_init_options()
        assert opts["disableAutomaticTypingAcquisition"] is True

    def test_javascript_shares_the_typescript_server_configuration(self):
        assert JavaScriptAdapter().get_lsp_init_options() == TypeScriptAdapter().get_lsp_init_options()
