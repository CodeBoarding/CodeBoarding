import os

from user_config import ProviderUserConfig, UserConfig, ensure_config_template, load_user_config


class TestUserConfigApplyToEnv:
    def test_injects_provider_key_when_no_provider_env_is_set(self):
        cfg = UserConfig(provider=ProviderUserConfig(vercel_api_key="vck-test"))

        original = os.environ.copy()
        try:
            os.environ.pop("VERCEL_API_KEY", None)

            cfg.apply_to_env()

            assert os.environ["VERCEL_API_KEY"] == "vck-test"
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_does_not_override_matching_provider_env_var(self):
        cfg = UserConfig(provider=ProviderUserConfig(vercel_api_key="vck-test"))

        original = os.environ.copy()
        try:
            os.environ["VERCEL_API_KEY"] = "vck-existing"

            cfg.apply_to_env()

            assert os.environ["VERCEL_API_KEY"] == "vck-existing"
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_injects_when_unrelated_provider_env_var_is_set(self):
        cfg = UserConfig(provider=ProviderUserConfig(vercel_api_key="vck-test"))

        original = os.environ.copy()
        try:
            os.environ["OPENROUTER_API_KEY"] = "sk-or-test"
            os.environ.pop("VERCEL_API_KEY", None)

            cfg.apply_to_env()

            assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test"
            assert os.environ["VERCEL_API_KEY"] == "vck-test"
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_injects_when_alt_provider_env_var_is_set(self):
        cfg = UserConfig(provider=ProviderUserConfig(openrouter_api_key="sk-or-test"))

        original = os.environ.copy()
        try:
            os.environ["VERCEL_BASE_URL"] = "https://ai-gateway.vercel.sh/v1"
            os.environ.pop("OPENROUTER_API_KEY", None)

            cfg.apply_to_env()

            assert os.environ["VERCEL_BASE_URL"] == "https://ai-gateway.vercel.sh/v1"
            assert os.environ["OPENROUTER_API_KEY"] == "sk-or-test"
        finally:
            os.environ.clear()
            os.environ.update(original)


class TestEnsureConfigTemplate:
    def test_appends_context_window_under_llm_when_missing(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[provider]\n# openai_api_key = "sk-..."\n\n[llm]\n# agent_model = "x"\n')

        ensure_config_template(path)

        text = path.read_text()
        assert "context_window" in text
        # Landed inside [llm], before agent_model (which is fine — it's a comment).
        llm_idx = text.index("[llm]")
        assert text.index("context_window") > llm_idx

    def test_noop_when_key_already_present(self, tmp_path):
        path = tmp_path / "config.toml"
        original = "[llm]\n# context_window = 42  # already here\n"
        path.write_text(original)

        ensure_config_template(path)

        assert path.read_text() == original

    def test_adds_llm_section_if_absent(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[provider]\n# openai_api_key = "sk-..."\n')

        ensure_config_template(path)

        text = path.read_text()
        assert "[llm]" in text
        assert "context_window" in text


class TestLoadUserConfig:
    def test_loads_timeouts(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text(
            "[provider]\n"
            'openai_api_key = "local"\n'
            "\n[llm]\n"
            'agent_model = "qwen"\n'
            "agent_timeout_s = 1200\n"
            "parsing_timeout_s = 900\n"
        )

        cfg = load_user_config(path)

        assert cfg.llm.agent_timeout_s == 1200
        assert cfg.llm.parsing_timeout_s == 900

    def test_timeouts_default_none_when_absent(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[llm]\nagent_model = "qwen"\n')

        cfg = load_user_config(path)

        assert cfg.llm.agent_timeout_s is None
        assert cfg.llm.parsing_timeout_s is None
