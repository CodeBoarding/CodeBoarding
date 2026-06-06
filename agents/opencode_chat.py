"""LangChain adapter for OpenCode server API."""

import asyncio
import json
import logging
import os
import time
import urllib.error
import urllib.request
from base64 import b64encode
from typing import Any, Callable, Sequence, Union

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


class ChatOpenCode(BaseChatModel):
    """LangChain-compatible wrapper for OpenCode server.

    Communicates with a running OpenCode instance via its HTTP API.
    Supports MCP-based tool execution when OpenCode is launched with
    CodeBoarding's MCP server.

    Args:
        model: OpenCode model specifier, e.g. "anthropic/claude-3-5-sonnet-20241022".
               Internally split into providerID/modelID for the OpenCode API.
        base_url: OpenCode server URL (default: http://localhost:4096).
        password: Optional server password for basic auth.
        temperature: Sampling temperature.
        max_tokens: Maximum tokens in the response.
        timeout: HTTP request timeout in seconds.
    """

    model: str = "anthropic/claude-3-5-sonnet-20241022"
    base_url: str = "http://localhost:4096"
    password: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout: int = 120

    _session_id: str | None = None
    _last_health_check: float = 0
    _cached_healthy: bool = False
    _health_check_interval: int = 60

    @property
    def _llm_type(self) -> str:
        return "opencode"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def _get_auth_header(self) -> dict[str, str]:
        if not self.password:
            return {}
        creds = b64encode(f"opencode:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {creds}"}

    def _health_check(self) -> bool:
        now = time.time()
        if now - self._last_health_check < self._health_check_interval:
            return self._cached_healthy
        try:
            url = f"{self.base_url}/global/health"
            req = urllib.request.Request(url, headers=self._get_auth_header())
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                self._last_health_check = now
                self._cached_healthy = data.get("healthy", False)
                return self._cached_healthy
        except Exception as e:
            logger.warning(f"OpenCode health check failed: {e}")
            self._last_health_check = now
            self._cached_healthy = False
            return False

    def _ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
        try:
            url = f"{self.base_url}/session"
            body = json.dumps({"title": "CodeBoarding Session"}).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    **self._get_auth_header(),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                self._session_id = data["id"]
                logger.info(f"Created OpenCode session: {self._session_id}")
                return self._session_id
        except Exception as e:
            raise RuntimeError(f"Failed to create OpenCode session: {e}") from e

    def _build_parts(self, messages: list[BaseMessage]) -> list[dict[str, Any]]:
        parts = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                parts.append({"type": "text", "text": f"System: {msg.content}"})
            elif isinstance(msg, HumanMessage):
                parts.append({"type": "text", "text": str(msg.content)})
            elif isinstance(msg, AIMessage):
                if msg.content:
                    parts.append({"type": "text", "text": str(msg.content)})
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_call_part: dict[str, Any] = {
                            "type": "tool_call",
                            "tool": tc["name"],
                            "input": tc["args"],
                            "id": tc.get("id") or tc["name"],
                        }
                        parts.append(tool_call_part)
            elif isinstance(msg, ToolMessage):
                parts.append(
                    {
                        "type": "tool_result",
                        "tool": msg.tool_call_id,
                        "output": str(msg.content),
                        "id": msg.tool_call_id,
                    }
                )
            else:
                parts.append({"type": "text", "text": str(msg.content)})
        return parts

    def _parse_model_spec(self) -> dict[str, str]:
        if "/" in self.model:
            provider_id, model_id = self.model.split("/", 1)
        else:
            provider_id = "openai"
            model_id = self.model
        return {"providerID": provider_id, "modelID": model_id}

    def _extract_text_from_response(self, data: dict) -> str:
        parts = data.get("parts", [])
        texts = []
        for part in parts:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
        return "\n".join(texts) if texts else ""

    def _extract_tool_calls_from_response(self, data: dict) -> list[dict]:
        """Extract tool calls from OpenCode response parts."""
        parts = data.get("parts", [])
        tool_calls = []
        for part in parts:
            if part.get("type") == "tool_call":
                tool_calls.append(
                    {
                        "name": part.get("tool", ""),
                        "args": part.get("input", {}),
                        "id": part.get("id", ""),
                    }
                )
        return tool_calls

    def _extract_tool_results_from_response(self, data: dict) -> list[dict]:
        """Extract tool results from OpenCode response parts."""
        parts = data.get("parts", [])
        tool_results = []
        for part in parts:
            if part.get("type") == "tool_result":
                tool_results.append(
                    {
                        "tool": part.get("tool", ""),
                        "output": part.get("output", ""),
                        "id": part.get("id", ""),
                    }
                )
        return tool_results

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if not self._health_check():
            raise RuntimeError(
                f"OpenCode server not reachable at {self.base_url}. "
                "Ensure 'opencode serve' is running or an OpenCode TUI session is active."
            )

        session_id = self._ensure_session()
        parts = self._build_parts(messages)
        model_spec = self._parse_model_spec()

        body = {
            "parts": parts,
            "model": model_spec,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens

        url = f"{self.base_url}/session/{session_id}/message"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                **self._get_auth_header(),
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode()
                data = json.loads(raw)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            raise RuntimeError(f"OpenCode API error ({e.code}): {error_body}") from e
        except Exception as e:
            raise RuntimeError(f"OpenCode request failed: {e}") from e

        text = self._extract_text_from_response(data)
        tool_calls = self._extract_tool_calls_from_response(data)

        message_kwargs: dict[str, Any] = {"content": text}
        if tool_calls:
            message_kwargs["tool_calls"] = tool_calls

        message = AIMessage(**message_kwargs)
        generation = ChatGeneration(message=message)

        if run_manager:
            run_manager.on_llm_new_token(text)

        return ChatResult(generations=[generation])

    def bind_tools(
        self,
        tools: Sequence[Union[dict[str, Any], type, Callable[..., Any], BaseTool]],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> BaseChatModel:
        """Bind tools for OpenCode MCP integration.

        Since OpenCode handles tool execution via MCP servers, this is a no-op.
        Tools are registered with the MCP server at launch time, not passed
        to the LLM directly.
        """
        logger.debug(f"bind_tools called with {len(tools)} tools (no-op for OpenCode MCP)")
        return self

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await asyncio.to_thread(self._generate, messages, stop, run_manager, **kwargs)
