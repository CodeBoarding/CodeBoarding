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

    def test_injects_openai_base_url_for_self_hosted_proxy(self):
        cfg = UserConfig(provider=ProviderUserConfig(openai_base_url="http://127.0.0.1:8000/v1"))

        original = os.environ.copy()
        try:
            os.environ.pop("OPENAI_BASE_URL", None)

            cfg.apply_to_env()

            assert os.environ["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_injects_litellm_proxy_key_and_base_url(self):
        cfg = UserConfig(
            provider=ProviderUserConfig(
                litellm_api_key="sk-litellm-test",
                litellm_base_url="http://localhost:4000",
            )
        )

        original = os.environ.copy()
        try:
            os.environ.pop("LITELLM_API_KEY", None)
            os.environ.pop("LITELLM_BASE_URL", None)

            cfg.apply_to_env()

            assert os.environ["LITELLM_API_KEY"] == "sk-litellm-test"
            assert os.environ["LITELLM_BASE_URL"] == "http://localhost:4000"
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_shell_openai_base_url_takes_precedence(self):
        cfg = UserConfig(provider=ProviderUserConfig(openai_base_url="http://config-value/v1"))

        original = os.environ.copy()
        try:
            os.environ["OPENAI_BASE_URL"] = "http://shell-value/v1"

            cfg.apply_to_env()

            assert os.environ["OPENAI_BASE_URL"] == "http://shell-value/v1"
        finally:
            os.environ.clear()
            os.environ.update(original)


class TestLoadUserConfig:
    def test_loads_openai_base_url(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("[provider]\n" 'openai_api_key = "local"\n' 'openai_base_url = "http://127.0.0.1:8000/v1"\n')

        cfg = load_user_config(path)

        assert cfg.provider.openai_base_url == "http://127.0.0.1:8000/v1"

    def test_openai_base_url_defaults_none_when_absent(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[provider]\nopenai_api_key = "local"\n')

        cfg = load_user_config(path)

        assert cfg.provider.openai_base_url is None


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
