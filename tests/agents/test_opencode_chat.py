"""Tests for ChatOpenCode LangChain adapter."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from agents.opencode_chat import ChatOpenCode
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class TestChatOpenCodeInitialization:
    def test_default_values(self):
        client = ChatOpenCode()
        assert client.model == "anthropic/claude-3-5-sonnet-20241022"
        assert client.base_url == "http://localhost:4096"
        assert client.password is None
        assert client.temperature == 0.0
        assert client.timeout == 120

    def test_custom_values(self):
        client = ChatOpenCode(
            model="openai/gpt-4o",
            base_url="http://custom:9999",
            password="secret",
            temperature=0.7,
            max_tokens=1000,
        )
        assert client.model == "openai/gpt-4o"
        assert client.base_url == "http://custom:9999"
        assert client.password == "secret"
        assert client.temperature == 0.7
        assert client.max_tokens == 1000

    def test_llm_type(self):
        client = ChatOpenCode()
        assert client._llm_type == "opencode"

    def test_identifying_params(self):
        client = ChatOpenCode(model="openai/gpt-4", temperature=0.5)
        params = client._identifying_params
        assert params["model"] == "openai/gpt-4"
        assert params["base_url"] == "http://localhost:4096"
        assert params["temperature"] == 0.5


class TestAuthHeader:
    def test_no_password(self):
        client = ChatOpenCode()
        assert client._get_auth_header() == {}

    def test_with_password(self):
        client = ChatOpenCode(password="test-pass")
        headers = client._get_auth_header()
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("Basic ")


class TestHealthCheck:
    @patch("urllib.request.urlopen")
    def test_health_check_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"healthy": True, "version": "1.0.0"}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        client = ChatOpenCode()
        assert client._health_check() is True

    @patch("urllib.request.urlopen")
    def test_health_check_failure(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("Connection refused")

        client = ChatOpenCode()
        assert client._health_check() is False

    @patch("urllib.request.urlopen")
    def test_health_check_caching(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"healthy": True}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        client = ChatOpenCode()
        assert client._health_check() is True
        client._health_check()

        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_health_check_caching_false(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"healthy": False}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        client = ChatOpenCode()
        assert client._health_check() is False
        client._health_check()

        assert mock_urlopen.call_count == 1
        assert client._cached_healthy is False


class TestSessionManagement:
    @patch("urllib.request.urlopen")
    def test_ensure_session_creates_session(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"id": "session-123"}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        client = ChatOpenCode()
        session_id = client._ensure_session()

        assert session_id == "session-123"
        assert client._session_id == "session-123"

    def test_ensure_session_reuses_session(self):
        client = ChatOpenCode()
        client._session_id = "existing-session"

        session_id = client._ensure_session()
        assert session_id == "existing-session"


class TestMessageBuilding:
    def test_build_parts_human_message(self):
        client = ChatOpenCode()
        messages: list = [HumanMessage(content="Hello")]
        parts = client._build_parts(messages)

        assert len(parts) == 1
        assert parts[0]["type"] == "text"
        assert parts[0]["text"] == "Hello"

    def test_build_parts_system_message(self):
        client = ChatOpenCode()
        messages: list = [SystemMessage(content="You are helpful")]
        parts = client._build_parts(messages)

        assert len(parts) == 1
        assert parts[0]["type"] == "text"
        assert parts[0]["text"] == "System: You are helpful"

    def test_build_parts_ai_message(self):
        client = ChatOpenCode()
        messages: list = [AIMessage(content="I can help")]
        parts = client._build_parts(messages)

        assert len(parts) == 1
        assert parts[0]["type"] == "text"
        assert parts[0]["text"] == "I can help"

    def test_build_parts_multiple_messages(self):
        client = ChatOpenCode()
        messages: list = [
            SystemMessage(content="System prompt"),
            HumanMessage(content="User question"),
        ]
        parts = client._build_parts(messages)

        assert len(parts) == 2
        assert "System:" in parts[0]["text"]
        assert parts[1]["text"] == "User question"


class TestModelSpecParsing:
    def test_with_slash(self):
        client = ChatOpenCode(model="anthropic/claude-3-sonnet")
        spec = client._parse_model_spec()

        assert spec == {"providerID": "anthropic", "modelID": "claude-3-sonnet"}

    def test_without_slash(self):
        client = ChatOpenCode(model="gpt-4o")
        spec = client._parse_model_spec()

        assert spec == {"providerID": "openai", "modelID": "gpt-4o"}


class TestResponseParsing:
    def test_extract_text_single_part(self):
        client = ChatOpenCode()
        data = {"parts": [{"type": "text", "text": "Hello world"}]}

        text = client._extract_text_from_response(data)
        assert text == "Hello world"

    def test_extract_text_multiple_parts(self):
        client = ChatOpenCode()
        data = {
            "parts": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]
        }

        text = client._extract_text_from_response(data)
        assert text == "Part 1\nPart 2"

    def test_extract_text_empty(self):
        client = ChatOpenCode()
        data: dict = {"parts": []}

        text = client._extract_text_from_response(data)
        assert text == ""


class TestGenerate:
    @patch("urllib.request.urlopen")
    @patch.object(ChatOpenCode, "_health_check", return_value=True)
    @patch.object(ChatOpenCode, "_ensure_session", return_value="test-session")
    def test_generate_success(self, mock_session, mock_health, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"parts": [{"type": "text", "text": "Response text"}]}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        client = ChatOpenCode()
        messages: list = [HumanMessage(content="Test")]
        result = client._generate(messages)

        assert len(result.generations) == 1
        assert result.generations[0].message.content == "Response text"

    @patch.object(ChatOpenCode, "_health_check", return_value=False)
    def test_generate_health_check_fails(self, mock_health):
        client = ChatOpenCode()
        messages: list = [HumanMessage(content="Test")]

        with pytest.raises(RuntimeError, match="OpenCode server not reachable"):
            client._generate(messages)

    @patch("urllib.request.urlopen")
    @patch.object(ChatOpenCode, "_health_check", return_value=True)
    @patch.object(ChatOpenCode, "_ensure_session", return_value="test-session")
    def test_generate_sends_correct_payload(self, mock_session, mock_health, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"parts": [{"type": "text", "text": "OK"}]}).encode()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=mock_response)
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        client = ChatOpenCode(model="openai/gpt-4o")
        messages: list = [HumanMessage(content="Hello")]
        client._generate(messages)

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        body = json.loads(request.data.decode())

        assert body["model"] == {"providerID": "openai", "modelID": "gpt-4o"}
        assert len(body["parts"]) == 1
        assert body["parts"][0]["text"] == "Hello"


@pytest.mark.integration
class TestOpenCodeLiveServer:
    """Integration tests against a live OpenCode server.

    Requires: opencode serve running on localhost:4096.
    Run with: pytest tests/agents/test_opencode_chat.py -m integration -v
    """

    @pytest.fixture(autouse=True)
    def setup_env(self):
        base_url = os.environ.get("OPENCODE_BASE_URL", "http://localhost:4096")
        self.base_url = base_url
        self.password = os.environ.get("OPENCODE_SERVER_PASSWORD")

    def test_health_check_returns_healthy(self):
        import urllib.request
        from base64 import b64encode

        req = urllib.request.Request(f"{self.base_url}/global/health")
        if self.password:
            creds = b64encode(f"opencode:{self.password}".encode()).decode()
            req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        assert data["healthy"] is True
        assert "version" in data

    def test_create_session_returns_id(self):
        import urllib.request
        from base64 import b64encode

        headers = {"Content-Type": "application/json"}
        if self.password:
            creds = b64encode(f"opencode:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        body = json.dumps({"title": "Integration Test"}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/session",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        assert "id" in data
        assert data["id"]

    def test_send_prompt_returns_response(self):
        import urllib.request
        from base64 import b64encode

        headers = {"Content-Type": "application/json"}
        if self.password:
            creds = b64encode(f"opencode:{self.password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        session_body = json.dumps({"title": "Prompt Test"}).encode()
        session_req = urllib.request.Request(
            f"{self.base_url}/session",
            data=session_body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(session_req, timeout=10) as resp:
            session_id = json.loads(resp.read().decode())["id"]

        prompt_body = json.dumps(
            {
                "parts": [{"type": "text", "text": "Reply with exactly: PING"}],
                "model": {"providerID": "opencode-go", "modelID": "qwen3.6-plus"},
            }
        ).encode()
        prompt_req = urllib.request.Request(
            f"{self.base_url}/session/{session_id}/message",
            data=prompt_body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(prompt_req, timeout=120) as resp:
            data = json.loads(resp.read().decode())

        assert "parts" in data
        texts = [p["text"] for p in data["parts"] if p.get("type") == "text"]
        assert any("PING" in t for t in texts), f"Expected PING in response, got: {texts}"

    def test_chat_adapter_health_check(self):
        client = ChatOpenCode(base_url=self.base_url, password=self.password)
        assert client._health_check() is True

    def test_chat_adapter_create_session(self):
        client = ChatOpenCode(base_url=self.base_url, password=self.password)
        session_id = client._ensure_session()
        assert session_id is not None
        assert client._session_id == session_id

    def test_chat_adapter_generate(self):
        client = ChatOpenCode(
            model="opencode-go/qwen3.6-plus",
            base_url=self.base_url,
            password=self.password,
        )
        messages: list = [HumanMessage(content="Reply with exactly: PONG")]
        result = client._generate(messages)

        assert len(result.generations) == 1
        content = result.generations[0].message.content
        assert "PONG" in content, f"Expected PONG in response, got: {content}"

    def test_chat_adapter_with_system_message(self):
        client = ChatOpenCode(
            model="opencode-go/qwen3.6-plus",
            base_url=self.base_url,
            password=self.password,
        )
        messages: list = [
            SystemMessage(content="You are a test assistant. Always reply with: SYSTEM_OK"),
            HumanMessage(content="Test message"),
        ]
        result = client._generate(messages)

        assert len(result.generations) == 1
        content = result.generations[0].message.content
        assert "SYSTEM_OK" in content, f"Expected SYSTEM_OK in response, got: {content}"

    def test_chat_adapter_reuses_session(self):
        client = ChatOpenCode(
            model="opencode-go/qwen3.6-plus",
            base_url=self.base_url,
            password=self.password,
        )
        result1 = client._generate([HumanMessage(content="Say FIRST")])
        session_id_1 = client._session_id

        result2 = client._generate([HumanMessage(content="Say SECOND")])
        session_id_2 = client._session_id

        assert session_id_1 == session_id_2
        assert len(result1.generations) == 1
        assert len(result2.generations) == 1
