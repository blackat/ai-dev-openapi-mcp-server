"""Thin async HTTP client that executes REST calls described by tool definitions."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import httpx


class APIClient:
    """Execute HTTP calls against the target REST API."""

    def __init__(self, base_url: str, api_key: str | None = None):
        headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True)

    async def call(
        self,
        method: str,
        path_template: str,
        parameters: list[dict[str, Any]],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an API call and return a normalised response dict.

        Args:
            method:          HTTP verb (GET, POST, …)
            path_template:   OpenAPI path with {param} placeholders
            parameters:      list of OpenAPI parameter objects for this operation
            arguments:       key/value pairs supplied by the LLM

        Returns:
            {"status": 200, "body": <parsed JSON or text>}
        """
        # Categorise each argument by its parameter location
        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}
        header_params: dict[str, str] = {}
        body: Any = None

        param_index = {p["name"]: p for p in parameters}

        for key, value in arguments.items():
            param = param_index.get(key)
            if param is None:
                # Assume body field if not declared as a parameter
                if body is None:
                    body = {}
                body[key] = value
                continue
            location = param.get("in", "query")
            if location == "path":
                path_params[key] = value
            elif location == "query":
                query_params[key] = value
            elif location == "header":
                header_params[key] = str(value)
            # cookie params are ignored

        # Build URL
        path = path_template
        for k, v in path_params.items():
            path = path.replace(f"{{{k}}}", str(v))

        url = self._base_url + path
        if query_params:
            url = f"{url}?{urlencode(query_params)}"

        # Execute
        resp = await self._client.request(
            method=method,
            url=url,
            headers=header_params or None,
            json=body,
        )

        try:
            response_body = resp.json()
        except Exception:
            response_body = resp.text

        return {"status": resp.status_code, "body": response_body}

    async def aclose(self) -> None:
        await self._client.aclose()
