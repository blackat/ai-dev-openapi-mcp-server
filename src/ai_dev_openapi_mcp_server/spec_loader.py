"""Load and parse an OpenAPI 3.x spec from a URL or local file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import jsonref
import yaml


def load_spec(source: str) -> dict[str, Any]:
    """Return a fully-dereferenced OpenAPI spec dict.

    Args:
        source: A URL (http/https) or a local file path (.json / .yaml).
    """
    if source.startswith("http://") or source.startswith("https://"):
        response = httpx.get(source, follow_redirects=True, timeout=30)
        response.raise_for_status()
        raw = response.text
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = yaml.safe_load(raw)
    else:
        path = Path(source)
        raw = path.read_text(encoding="utf-8")
        if path.suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(raw)
        else:
            data = json.loads(raw)

    # Dereference all $ref pointers so callers see a flat structure
    return jsonref.replace_refs(data)  # type: ignore[return-value]


def extract_tools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract each API operation as a tool definition.

    Returns a list of dicts with keys:
        name        – operationId (slugified)
        description – summary + description from the spec
        method      – HTTP method (GET, POST, …)
        path        – URL path template
        parameters  – list of OpenAPI parameter objects
        request_body – OpenAPI requestBody object (or None)
    """
    tools: list[dict[str, Any]] = []
    paths: dict[str, Any] = spec.get("paths", {})

    for path, path_item in paths.items():
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            operation: dict[str, Any] | None = path_item.get(method)
            if operation is None:
                continue

            op_id: str = operation.get("operationId") or _make_op_id(method, path)
            summary = operation.get("summary", "")
            description = operation.get("description", "")
            desc = f"{summary}\n{description}".strip()

            tools.append(
                {
                    "name": _slug(op_id),
                    "description": desc or f"{method.upper()} {path}",
                    "method": method.upper(),
                    "path": path,
                    "parameters": operation.get("parameters", []),
                    "request_body": operation.get("requestBody"),
                }
            )

    return tools


def _make_op_id(method: str, path: str) -> str:
    parts = [method] + [p for p in path.split("/") if p and not p.startswith("{")]
    return "_".join(parts)


def _slug(name: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_]", "_", name)[:64]


def resolve_base_url(spec: dict[str, Any], spec_source: str) -> str:
    """Extract a fully-qualified base URL from the spec's servers list.

    OpenAPI specs sometimes list a relative URL like '/api/v3' instead of
    'https://host/api/v3'.  When the spec was fetched from a URL we can
    resolve the relative path against that origin.  If resolution is not
    possible we return an empty string and let the caller raise a clear error.
    """
    from urllib.parse import urlparse, urlunparse

    servers: list[dict[str, Any]] = spec.get("servers", [])
    if not servers:
        return ""

    url: str = servers[0].get("url", "").strip()
    if not url:
        return ""

    # Already absolute — nothing to do
    if url.startswith("http://") or url.startswith("https://"):
        return url.rstrip("/")

    # Relative URL — try to resolve against the spec source origin
    if spec_source.startswith("http://") or spec_source.startswith("https://"):
        parsed = urlparse(spec_source)
        origin = urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))
        resolved = origin + ("/" if not url.startswith("/") else "") + url
        return resolved.rstrip("/")

    # Relative URL but spec came from a local file — cannot resolve
    return ""
