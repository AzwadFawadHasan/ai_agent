from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
from django.conf import settings


@dataclass
class ToolRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolRequest] = field(default_factory=list)
    assistant_content: Any = None
    token_usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = ""


class LLMConfigurationError(RuntimeError):
    pass


class AnthropicToolClient:
    def __init__(self) -> None:
        if not settings.ANTHROPIC_API_KEY:
            raise LLMConfigurationError("ANTHROPIC_API_KEY is required for Anthropic mode.")

        from anthropic import Anthropic

        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ANTHROPIC_MODEL

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1200,
            system=system,
            messages=messages,
            tools=tools or [],
        )

        text_parts = []
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolRequest(
                        id=block.id,
                        name=block.name,
                        arguments=block.input or {},
                    )
                )

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return LLMResponse(
            text="\n".join(text_parts).strip(),
            tool_calls=tool_calls,
            assistant_content=response.content,
            token_usage=usage,
            finish_reason=response.stop_reason or "",
        )


class OpenAICompatibleToolClient:
    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float,
        max_tokens: int,
    ) -> None:
        self.provider = provider.lower()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    def respond(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        if self.provider == "ollama":
            # Ollama thinking models (like Qwen3) can be extremely slow when reasoning is enabled.
            # The OpenAI-compatible endpoint supports reasoning_effort; default to "none" for speed.
            payload["reasoning_effort"] = settings.OLLAMA_REASONING_EFFORT

        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout_s)
        except httpx.TimeoutException as exc:
            if self.provider == "ollama":
                hint = "Increase OLLAMA_TIMEOUT_S (seconds) or lower LLM_MAX_TOKENS."
            else:
                hint = "Increase OPENAI_COMPAT_TIMEOUT_S (seconds) or lower LLM_MAX_TOKENS."
            raise RuntimeError(
                f"{self.provider} chat/completions request timed out after {self.timeout_s}s. {hint}"
            ) from exc

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(self._format_http_error(response)) from exc
        data = response.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        raw_content = message.get("content") or ""
        if isinstance(raw_content, list):
            text = "\n".join(
                str(part.get("text", ""))
                for part in raw_content
                if isinstance(part, dict)
            ).strip()
        else:
            text = str(raw_content).strip()

        tool_calls: list[ToolRequest] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            name = function.get("name") or ""
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolRequest(
                    id=raw_call.get("id") or "",
                    name=name,
                    arguments=arguments,
                )
            )

        usage = {}
        raw_usage = data.get("usage") or {}
        if raw_usage:
            usage = {
                "input_tokens": int(raw_usage.get("prompt_tokens") or 0),
                "output_tokens": int(raw_usage.get("completion_tokens") or 0),
                "total_tokens": int(raw_usage.get("total_tokens") or 0),
            }

        assistant_message = {
            "role": "assistant",
            "content": message.get("content"),
        }
        if message.get("tool_calls"):
            assistant_message["tool_calls"] = message["tool_calls"]
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            assistant_content=assistant_message,
            token_usage=usage,
            finish_reason=str(choice.get("finish_reason") or ""),
        )

    def _format_http_error(self, response: httpx.Response) -> str:
        status = response.status_code
        request_id = response.headers.get("x-request-id") or response.headers.get("request-id") or ""

        retry_after = (
            response.headers.get("retry-after-ms")
            or response.headers.get("retry-after")
            or response.headers.get("Retry-After")
            or ""
        )
        reset_requests = response.headers.get("x-ratelimit-reset-requests") or ""
        reset_tokens = response.headers.get("x-ratelimit-reset-tokens") or ""
        remaining_requests = response.headers.get("x-ratelimit-remaining-requests") or ""
        remaining_tokens = response.headers.get("x-ratelimit-remaining-tokens") or ""

        error_message = ""
        error_type = ""
        error_code = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                error_message = str(error.get("message") or "")
                error_type = str(error.get("type") or "")
                error_code = str(error.get("code") or "")
        except Exception:
            pass

        parts = [
            f"{self.provider} chat/completions failed ({status}).",
        ]
        if error_type or error_code:
            parts.append(f"type={error_type or 'unknown'} code={error_code or 'unknown'}")
        if error_message:
            parts.append(error_message)
        if retry_after or reset_requests or reset_tokens:
            parts.append(
                "rate_limit:"
                f" retry_after={retry_after or 'n/a'}"
                f" reset_requests={reset_requests or 'n/a'}"
                f" reset_tokens={reset_tokens or 'n/a'}"
                f" remaining_requests={remaining_requests or 'n/a'}"
                f" remaining_tokens={remaining_tokens or 'n/a'}"
            )
        if request_id:
            parts.append(f"request_id={request_id}")

        # Practical hint for the common OpenAI 429 modes.
        if status == 429 and (error_type == "insufficient_quota" or error_code == "insufficient_quota"):
            parts.append(
                "This usually means the API account has no active credits/pay-as-you-go or hit a spend cap."
            )
        elif status == 429:
            parts.append("This is a rate limit; wait for reset or reduce request/token rate.")

        return " ".join(part for part in parts if part)


def build_llm_client(provider: str) -> AnthropicToolClient | OpenAICompatibleToolClient:
    provider = provider.lower()
    if provider == "anthropic":
        return AnthropicToolClient()
    if provider == "groq":
        if not settings.GROQ_API_KEY:
            raise LLMConfigurationError("GROQ_API_KEY is required for Groq mode.")
        return OpenAICompatibleToolClient(
            provider="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            timeout_s=settings.OPENAI_COMPAT_TIMEOUT_S,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise LLMConfigurationError("OPENAI_API_KEY is required for OpenAI mode.")
        return OpenAICompatibleToolClient(
            provider="openai",
            base_url=settings.OPENAI_BASE_URL,
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
            timeout_s=settings.OPENAI_COMPAT_TIMEOUT_S,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    if provider == "ollama":
        return OpenAICompatibleToolClient(
            provider="ollama",
            base_url=settings.OLLAMA_BASE_URL,
            api_key="",
            model=settings.OLLAMA_MODEL,
            timeout_s=settings.OLLAMA_TIMEOUT_S,
            max_tokens=settings.LLM_MAX_TOKENS,
        )

    raise LLMConfigurationError(f"Unknown LLM provider: {provider}")
