"""CLI entrypoint for the OpenAPI MCP server."""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()  # Load .env if present

app = typer.Typer(
    name="openapi-mcp",
    help="Expose any OpenAPI REST API as an MCP server.",
    add_completion=False,
)
console = Console()


def _build_config(
    spec: str,
    llm: str,
    ollama_url: str,
    ollama_model: str,
    gemini_key: Optional[str],
    gemini_model: str,
    api_base: Optional[str],
    api_key: Optional[str],
    host: str,
    port: int,
) -> dict:
    return {
        "openapi_spec": spec or os.environ.get("OPENAPI_SPEC", ""),
        "llm_backend": llm or os.environ.get("LLM_BACKEND", "ollama"),
        "ollama_base_url": ollama_url
        or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "ollama_model": ollama_model or os.environ.get("OLLAMA_MODEL", "llama3.2"),
        "gemini_api_key": gemini_key or os.environ.get("GEMINI_API_KEY"),
        "gemini_model": gemini_model
        or os.environ.get("GEMINI_MODEL", "gemini-1.5-flash"),
        "api_base_url": api_base or os.environ.get("API_BASE_URL", ""),
        "api_key": api_key or os.environ.get("API_KEY"),
        "mcp_host": host,
        "mcp_port": port,
    }


@app.command()
def serve(
    spec: str = typer.Option(
        "", "--spec", "-s", help="URL or path to the OpenAPI spec"
    ),
    api_base: Optional[str] = typer.Option(
        None, "--api-base", help="Override API base URL"
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="Bearer token for target API"
    ),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    """Start the MCP server (stdio transport) for use with Cursor, Claude Desktop, etc.

    The LLM that reasons over tools is the MCP CLIENT (e.g. Cursor).
    This server only exposes tools and executes HTTP calls — it has no LLM of its own.
    To chat via a local LLM instead, use: openapi-mcp chat
    """
    from mcp.server.stdio import stdio_server

    config = {
        "openapi_spec": spec or os.environ.get("OPENAPI_SPEC", ""),
        "api_base_url": api_base or os.environ.get("API_BASE_URL", ""),
        "api_key": api_key or os.environ.get("API_KEY"),
        "mcp_host": host,
        "mcp_port": port,
    }

    if not config["openapi_spec"]:
        console.print(
            "[red]Error:[/red] --spec is required (or set OPENAPI_SPEC in .env)"
        )
        raise typer.Exit(1)

    console.print(
        Panel(
            f"[bold]OpenAPI MCP Server[/bold]\n"
            f"Spec : [cyan]{config['openapi_spec']}[/cyan]\n"
            f"Mode : [green]serve[/green] — waiting for MCP client (Cursor, Claude Desktop …)\n"
            f"LLM  : [dim]provided by the client, not this server[/dim]",
            title="[bold blue]Starting[/bold blue]",
        )
    )

    async def run() -> None:
        from .server import OpenAPIMCPServer

        srv = OpenAPIMCPServer(config)
        await srv.startup()
        async with stdio_server() as (read_stream, write_stream):
            init_opts = srv.mcp.create_initialization_options()
            await srv.mcp.run(read_stream, write_stream, init_opts)
        await srv.shutdown()

    asyncio.run(run())


@app.command()
def chat(
    spec: str = typer.Option(
        "", "--spec", "-s", help="URL or path to the OpenAPI spec"
    ),
    llm: str = typer.Option("ollama", "--llm", help="LLM backend: ollama or gemini"),
    ollama_url: str = typer.Option("http://localhost:11434", "--ollama-url"),
    ollama_model: str = typer.Option("llama3.2", "--ollama-model"),
    gemini_key: Optional[str] = typer.Option(
        None, "--gemini-key", envvar="GEMINI_API_KEY"
    ),
    gemini_model: str = typer.Option("gemini-1.5-flash", "--gemini-model"),
    api_base: Optional[str] = typer.Option(None, "--api-base"),
    api_key: Optional[str] = typer.Option(None, "--api-key"),
) -> None:
    """Interactive chat loop: talk to the API via natural language."""
    config = _build_config(
        spec,
        llm,
        ollama_url,
        ollama_model,
        gemini_key,
        gemini_model,
        api_base,
        api_key,
        "127.0.0.1",
        8765,
    )

    if not config["openapi_spec"]:
        console.print(
            "[red]Error:[/red] --spec is required (or set OPENAPI_SPEC in .env)"
        )
        raise typer.Exit(1)

    _print_banner(config)

    async def run() -> None:
        from .server import OpenAPIMCPServer

        srv = OpenAPIMCPServer(config)
        await srv.startup()
        srv.startup_llm()  # only chat mode needs an LLM

        console.print(
            Panel(
                "[bold green]Chat started[/bold green] — type [bold]quit[/bold] to exit"
            )
        )
        history: list[dict] = []

        while True:
            try:
                user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if user_input.lower() in {"quit", "exit", "q"}:
                break
            if not user_input:
                continue

            with console.status("Thinking…"):
                answer = await srv.chat(user_input, history)
            console.print(f"[bold yellow]Assistant:[/bold yellow] {answer}\n")
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": answer})

        await srv.shutdown()
        console.print("[dim]Goodbye.[/dim]")

    asyncio.run(run())


@app.command("list-tools")
def list_tools(
    spec: str = typer.Argument(..., help="URL or path to the OpenAPI spec"),
) -> None:
    """Print all tools that would be registered from an OpenAPI spec."""
    from .spec_loader import extract_tools, load_spec

    console.print(f"Loading spec from [cyan]{spec}[/cyan] …\n")
    spec_data = load_spec(spec)
    tools = extract_tools(spec_data)

    table = Table(title=f"{len(tools)} tools found", show_lines=True)
    table.add_column("Name", style="bold cyan", no_wrap=True)
    table.add_column("Method", style="green")
    table.add_column("Path")
    table.add_column("Description")

    for t in tools:
        table.add_row(t["name"], t["method"], t["path"], t["description"][:80])

    console.print(table)


def _print_banner(config: dict) -> None:
    backend = config["llm_backend"]
    if backend == "ollama":
        llm_info = (
            f"Ollama  model={config['ollama_model']}  url={config['ollama_base_url']}"
        )
    else:
        llm_info = f"Gemini  model={config['gemini_model']}"

    console.print(
        Panel(
            f"[bold]OpenAPI MCP Server[/bold]\n"
            f"Spec : [cyan]{config['openapi_spec']}[/cyan]\n"
            f"LLM  : [green]{llm_info}[/green]",
            title="[bold blue]Starting[/bold blue]",
        )
    )


if __name__ == "__main__":
    app()
