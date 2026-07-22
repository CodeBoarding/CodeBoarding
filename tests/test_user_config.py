import os

import pytest

from agents.llm_config import LLM_PROVIDERS
from user_config import (
    _PROVIDER_ENDPOINTS,
    _PROVIDER_SECRETS,
    CONFIG_TEMPLATE,
    ProviderUserConfig,
    UserConfig,
    ensure_config_template,
    load_user_config,
)


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

    def test_loads_atlascloud_api_key(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[provider]\natlascloud_api_key = "atlas-local"\n')

        cfg = load_user_config(path)

        assert cfg.provider.atlascloud_api_key == "atlas-local"

    def test_empty_atlascloud_api_key_defaults_none(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text('[provider]\natlascloud_api_key = ""\n')

        cfg = load_user_config(path)

        assert cfg.provider.atlascloud_api_key is None

    def test_atlascloud_api_key_rejects_non_string(self, tmp_path):
        path = tmp_path / "config.toml"
        path.write_text("[provider]\natlascloud_api_key = 123\n")

        with pytest.raises(ValueError, match=r"\[provider\]\.atlascloud_api_key must be a string"):
            load_user_config(path)


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


class TestProviderEnvContract:
    """config.toml must be able to select every provider and store every secret."""

    def test_every_provider_selectable_from_config_toml(self):
        mapped = set((_PROVIDER_SECRETS | _PROVIDER_ENDPOINTS).values())
        for name, config in LLM_PROVIDERS.items():
            assert mapped & set(config.selection_envs), (
                f"Provider '{name}' cannot be selected from config.toml: none of its "
                f"selection_envs {config.selection_envs} has an entry. Add one to "
                f"_PROVIDER_SECRETS or _PROVIDER_ENDPOINTS in user_config.py."
            )

    def test_every_secret_storable_in_config_toml(self):
        for name, config in LLM_PROVIDERS.items():
            if config.api_key_env:
                assert config.api_key_env in _PROVIDER_SECRETS.values(), (
                    f"Secret {config.api_key_env} for provider '{name}' has no config.toml key. "
                    f"Keys belong in config.toml, not shell profiles - add "
                    f"'{config.api_key_env.lower()}' to _PROVIDER_SECRETS in user_config.py."
                )

    def test_secrets_are_secrets_and_endpoints_are_endpoints(self):
        # The bucket names are the documentation - keep them honest.
        key_envs = {c.api_key_env for c in LLM_PROVIDERS.values() if c.api_key_env}
        # Credentials consumed by the SDK itself rather than passed as a kwarg
        # (api_key_env=None), e.g. botocore reading AWS_BEARER_TOKEN_BEDROCK.
        sdk_credential_envs = {v for c in LLM_PROVIDERS.values() if c.api_key_env is None for v in c.selection_envs}
        stray_secrets = set(_PROVIDER_SECRETS.values()) - key_envs - sdk_credential_envs
        assert (
            not stray_secrets
        ), f"_PROVIDER_SECRETS entries no provider reads as a credential: {sorted(stray_secrets)}"
        selection_envs = {v for c in LLM_PROVIDERS.values() for v in c.selection_envs}
        stray_endpoints = set(_PROVIDER_ENDPOINTS.values()) - (selection_envs - set(_PROVIDER_SECRETS.values()))
        assert (
            not stray_endpoints
        ), f"_PROVIDER_ENDPOINTS entries that are not non-secret selection vars: {sorted(stray_endpoints)}"

    def test_entries_are_discoverable_and_loadable(self):
        for key, env in (_PROVIDER_SECRETS | _PROVIDER_ENDPOINTS).items():
            assert key == env.lower(), f"config key '{key}' must be the lowercase of '{env}'"
            assert (
                key in ProviderUserConfig.__dataclass_fields__
            ), f"'{key}' is mapped but not a ProviderUserConfig field - apply_to_env would silently skip it."
            assert key in CONFIG_TEMPLATE, f"'{key}' is missing from CONFIG_TEMPLATE - users cannot discover it."
