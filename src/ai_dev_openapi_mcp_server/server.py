"""Core MCP server: registers every OpenAPI operation as an MCP tool."""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.types import (
    CallToolResult,
    ListToolsResult,
    TextContent,
    Tool,
)

from .api_client import APIClient
from .llm_backends import LLMBackend, create_backend
from .spec_loader import extract_tools, load_spec, resolve_base_url


class OpenAPIMCPServer:
    """Wraps an OpenAPI-described REST API as an MCP server."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.mcp = Server("openapi-mcp-server")
        self._tools: list[dict[str, Any]] = []
        self._tool_index: dict[str, dict[str, Any]] = {}
        self._api_client: APIClient | None = None
        self._llm: LLMBackend | None = None

        # Register MCP handlers
        self.mcp.list_tools()(self._list_tools)
        self.mcp.call_tool()(self._call_tool)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Load spec, build tool index and create HTTP client.

        Does NOT touch the LLM — in serve mode the MCP client is the LLM.
        Call startup_llm() explicitly only in chat mode.
        """
        spec_source: str = self.config["openapi_spec"]
        print(f"[openapi-mcp] Loading spec from {spec_source!r} …")
        spec = load_spec(spec_source)

        # Determine base URL
        base_url: str = self.config.get("api_base_url", "").strip()

        if base_url and not (
            base_url.startswith("http://") or base_url.startswith("https://")
        ):
            raise ValueError(
                f"API_BASE_URL {base_url!r} is missing the scheme. "
                "Use 'https://...' or 'http://...'"
            )

        if not base_url:
            spec_source: str = self.config["openapi_spec"]
            base_url = resolve_base_url(spec, spec_source)

        if not base_url:
            raise ValueError(
                "Could not determine a valid API base URL.\n"
                "The spec's servers[0].url is missing or relative with no origin to resolve against.\n"
                "Fix: pass --api-base https://your-api.com or set API_BASE_URL in .env"
            )

        print(f"[openapi-mcp] Base URL: {base_url}")

        self._tools = extract_tools(spec)
        self._tool_index = {t["name"]: t for t in self._tools}
        print(f"[openapi-mcp] Registered {len(self._tools)} tools.")

        self._api_client = APIClient(
            base_url=base_url,
            api_key=self.config.get("api_key"),
        )
        # LLM backend is NOT started here.
        # In serve mode the MCP client (Cursor etc.) is the LLM.
        # Call startup_llm() explicitly only when running chat mode.

    def startup_llm(self) -> None:
        """Initialise the LLM backend. Called only in chat mode."""
        self._llm = create_backend(self.config)
        backend_name = self.config.get("llm_backend", "ollama")
        print(f"[openapi-mcp] LLM backend: {backend_name}")

    async def shutdown(self) -> None:
        if self._api_client:
            await self._api_client.aclose()

    # ------------------------------------------------------------------
    # MCP handlers
    # ------------------------------------------------------------------

    async def _list_tools(self) -> ListToolsResult:
        mcp_tools = [_tool_to_mcp(t) for t in self._tools]
        return ListToolsResult(tools=mcp_tools)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        tool_def = self._tool_index.get(name)
        if tool_def is None:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name!r}")],
                isError=True,
            )

        try:
            result = await self._api_client.call(  # type: ignore[union-attr]
                method=tool_def["method"],
                path_template=tool_def["path"],
                parameters=tool_def["parameters"],
                arguments=arguments,
            )
            text = json.dumps(result, indent=2, ensure_ascii=False)
            return CallToolResult(content=[TextContent(type="text", text=text)])
        except Exception as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: {exc}")],
                isError=True,
            )

    # ------------------------------------------------------------------
    # Chat helper (for CLI interactive mode)
    # ------------------------------------------------------------------

    async def chat(
        self, user_message: str, history: list[dict[str, Any]] | None = None
    ) -> str:
        """Run one turn of the agentic loop: LLM → tool call → LLM."""
        messages: list[dict[str, Any]] = list(history or [])
        messages.append({"role": "user", "content": user_message})

        max_iterations = 5
        for _ in range(max_iterations):
            llm_resp = await self._llm.chat(messages=messages, tools=self._tools)  # type: ignore[union-attr]

            if llm_resp.tool_calls:
                # Execute every tool call and feed results back
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"[Calling tools: {[tc['name'] for tc in llm_resp.tool_calls]}]",
                    }
                )
                for tc in llm_resp.tool_calls:
                    result = await self._call_tool(tc["name"], tc["arguments"])
                    result_text = " ".join(
                        c.text for c in result.content if hasattr(c, "text")
                    )
                    messages.append(
                        {"role": "user", "content": f"Tool result:\n{result_text}"}
                    )
            else:
                # Final text answer
                return llm_resp.text or "(no response)"

        return "(max iterations reached)"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _tool_to_mcp(tool: dict[str, Any]) -> Tool:
    """Convert an internal tool dict to an MCP Tool object."""
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

    rb = tool.get("request_body")
    if rb:
        content = rb.get("content", {})
        json_schema = content.get("application/json", {}).get("schema", {})
        for fname, fschema in json_schema.get("properties", {}).items():
            props[fname] = {
                "type": fschema.get("type", "string"),
                "description": fschema.get("description", ""),
            }

    return Tool(
        name=tool["name"],
        description=tool["description"],
        inputSchema={
            "type": "object",
            "properties": props,
            "required": required,
        },
    )
