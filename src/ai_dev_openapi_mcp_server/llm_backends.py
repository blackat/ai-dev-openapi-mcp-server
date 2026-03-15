"""LLM backend abstraction: Ollama (local) and Google Gemini."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class LLMBackend(ABC):
    """Minimal interface: given a prompt + tool specs, return tool calls."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> "LLMResponse":
        ...


class LLMResponse:
    """Normalised response from any LLM backend."""

    def __init__(self, text: str | None, tool_calls: list[dict[str, Any]]):
        self.text = text
        self.tool_calls = tool_calls  # [{"name": ..., "arguments": {...}}]

    def __repr__(self) -> str:
        return f"LLMResponse(text={self.text!r}, tool_calls={self.tool_calls})"


# ---------------------------------------------------------------------------
# Ollama backend
# ---------------------------------------------------------------------------

class OllamaBackend(LLMBackend):
    """Use a locally running Ollama instance."""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3.2"):
        import ollama as _ollama  # lazy import
        self._ollama = _ollama
        self._base_url = base_url
        self._model = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        # Convert MCP-style tool defs to Ollama function specs
        ollama_tools = [_mcp_tool_to_ollama(t) for t in tools]

        client = self._ollama.AsyncClient(host=self._base_url)
        response = await client.chat(
            model=self._model,
            messages=messages,
            tools=ollama_tools,
        )

        msg = response.message
        text: str | None = msg.content or None
        tool_calls: list[dict[str, Any]] = []

        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or {},
                    }
                )

        return LLMResponse(text=text, tool_calls=tool_calls)


def _mcp_tool_to_ollama(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool spec to Ollama's function-calling format."""
    props: dict[str, Any] = {}
    required: list[str] = []

    for param in tool.get("parameters", []):
        name = param["name"]
        schema = param.get("schema", {})
        props[name] = {
            "type": schema.get("type", "string"),
            "description": param.get("description", ""),
        }
        if param.get("required"):
            required.append(name)

    # Also expose request body fields as flat parameters
    rb = tool.get("request_body")
    if rb:
        content = rb.get("content", {})
        json_schema = content.get("application/json", {}).get("schema", {})
        for fname, fschema in json_schema.get("properties", {}).items():
            props[fname] = {
                "type": fschema.get("type", "string"),
                "description": fschema.get("description", ""),
            }

    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


# ---------------------------------------------------------------------------
# Google Gemini backend
# ---------------------------------------------------------------------------

class GeminiBackend(LLMBackend):
    """Use Google Gemini via the google-generativeai SDK."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        import google.generativeai as genai  # lazy import
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model_name = model

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        import asyncio
        from google.generativeai.types import FunctionDeclaration, Tool  # type: ignore

        gemini_tools = [_mcp_tool_to_gemini_declaration(t) for t in tools]
        gemini_tool_obj = Tool(function_declarations=gemini_tools)

        model = self._genai.GenerativeModel(
            model_name=self._model_name,
            tools=[gemini_tool_obj],
        )

        # Convert messages to Gemini Content format
        # Only "user" and "model" roles are valid in Gemini
        history = []
        last_user = None
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            history.append({"role": role, "parts": [m["content"]]})

        # Pop the last user message to send via send_message
        if history and history[-1]["role"] == "user":
            last_user = history.pop()["parts"][0]
        else:
            last_user = ""

        chat = model.start_chat(history=history)

        # Gemini SDK is synchronous – run in executor
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, chat.send_message, last_user)

        text: str | None = None
        tool_calls: list[dict[str, Any]] = []

        for part in response.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                fc = part.function_call
                tool_calls.append(
                    {
                        "name": fc.name,
                        "arguments": dict(fc.args),
                    }
                )
            elif hasattr(part, "text") and part.text:
                text = (text or "") + part.text

        return LLMResponse(text=text, tool_calls=tool_calls)


def _mcp_tool_to_gemini_declaration(tool: dict[str, Any]) -> Any:
    from google.generativeai.types import FunctionDeclaration  # type: ignore

    props: dict[str, Any] = {}
    required: list[str] = []

    for param in tool.get("parameters", []):
        name = param["name"]
        schema = param.get("schema", {})
        ptype = schema.get("type", "string").upper()
        props[name] = {"type": ptype, "description": param.get("description", "")}
        if param.get("required"):
            required.append(name)

    rb = tool.get("request_body")
    if rb:
        content = rb.get("content", {})
        json_schema = content.get("application/json", {}).get("schema", {})
        for fname, fschema in json_schema.get("properties", {}).items():
            ptype = fschema.get("type", "string").upper()
            props[fname] = {"type": ptype, "description": fschema.get("description", "")}

    parameters_schema = {"type": "OBJECT", "properties": props}
    if required:
        parameters_schema["required"] = required

    return FunctionDeclaration(
        name=tool["name"],
        description=tool["description"],
        parameters=parameters_schema,
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_backend(config: dict[str, Any]) -> LLMBackend:
    """Instantiate the correct backend from a config dict."""
    backend = config.get("llm_backend", "ollama").lower()
    if backend == "ollama":
        return OllamaBackend(
            base_url=config.get("ollama_base_url", "http://localhost:11434"),
            model=config.get("ollama_model", "llama3.2"),
        )
    elif backend == "gemini":
        key = config.get("gemini_api_key")
        if not key:
            raise ValueError("GEMINI_API_KEY is required when using the Gemini backend.")
        return GeminiBackend(
            api_key=key,
            model=config.get("gemini_model", "gemini-1.5-flash"),
        )
    else:
        raise ValueError(f"Unknown LLM backend: {backend!r}. Choose 'ollama' or 'gemini'.")
