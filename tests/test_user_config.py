import os

from user_config import ProviderUserConfig, UserConfig


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
