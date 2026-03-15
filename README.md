# OpenAPI MCP Server

Expose **any REST API** (described by an OpenAPI 3.x spec) as an MCP server,
so any MCP-compatible LLM client (Claude Desktop, etc.) can call it as tools.

Supports **Ollama** (local) and **Google Gemini** as LLM backends.

## Scaffolding

```bash
ai-dev-openapi-mcp-server/
├── pyproject.toml               ← uv-compatible, src layout
├── .env.example                 ← all config options documented
├── README.md
├── claude_desktop_config.example.json
├── src/openapi_mcp_server/
│   ├── cli.py                   ← typer CLI (serve / chat / list-tools)
│   ├── server.py                ← MCP server + agentic loop
│   ├── spec_loader.py           ← loads & dereferences any OpenAPI spec
│   ├── api_client.py            ← async httpx REST caller
│   └── llm_backends.py          ← Ollama + Gemini, swappable factory
└── tests/
    └── test_spec_loader.py
```

## Quick start

```bash
# Install dependencies and create .venv folder
uv sync

# Run with Ollama (local)
uv run openapi-mcp serve --spec https://petstore3.swagger.io/api/v3/openapi.json \
    --llm ollama --ollama-model llama3.2

# Run with Gemini
uv run openapi-mcp serve --spec ./my-api.yaml \
    --llm gemini --gemini-key YOUR_KEY

# Or use a .env file
cp .env.example .env   # fill in values
uv run openapi-mcp serve --spec ./my-api.yaml
```

## VSCode

VSCode extensions: 

- `ms-python.python`: the official Microsoft extension. Handles IntelliSense, debugging, test discovery, and environment selection.
- `ms-python.vscode-pylance`: the language server that powers type checking and autocomplete. Usually installed automatically with the Python extension but worth confirming it is active.
- `charliermarsh.ruff`: linter and formatter, written in Rust, extremely fast.
- `tamasfe.even-better-toml`: syntax highlighting and validation for pyproject.toml, which is where uv stores all its configuration.

Edit `settings.json`:

```json
{
  "python.defaultInterpreterPath": ".venv/bin/python",
  "python.terminal.activateEnvironment": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  }
}
```

Since uv creates a `.venv` folder by default with `uv venv` or `uv sync`, VS Code picks it up automatically when you open the project, no extension needed.

This gives you auto-format and auto-import sorting on save using `Ruff`, with `uv` managing the environment underneath.

## Configuration

All options can be set via CLI flags **or** a `.env` file:

| Env var | CLI flag | Description |
|---|---|---|
| `OPENAPI_SPEC` | `--spec` | URL or path to OpenAPI JSON/YAML |
| `LLM_BACKEND` | `--llm` | `ollama` or `gemini` |
| `OLLAMA_BASE_URL` | `--ollama-url` | Default `http://localhost:11434` |
| `OLLAMA_MODEL` | `--ollama-model` | Default `llama3.2` |
| `GEMINI_API_KEY` | `--gemini-key` | Your Google Gemini API key |
| `GEMINI_MODEL` | `--gemini-model` | Default `gemini-1.5-flash` |
| `API_BASE_URL` | `--api-base` | Override the API base URL |
| `API_KEY` | `--api-key` | Bearer token for the target API |
| `MCP_HOST` | `--host` | MCP server host (default `127.0.0.1`) |
| `MCP_PORT` | `--port` | MCP server port (default `8765`) |

## What is an MCP server?

An MCP (Model Context Protocol) server is a small service that exposes tools, named functions with typed inputs, to an LLM client using a standard protocol (JSON over stdio or HTTP). The LLM decides when to call a tool and what arguments to pass; the MCP server handles the actual execution. This cleanly separates _"the model thinks"_ from _"the model acts"_.

Think of it like a USB-C standard for AI plugins: one protocol, any tool.

## Architecture Diagram

![Architecture diagram](./img/openapi_mcp_architecture.svg)

### How it works (the diagram above)

1. The CLI reads your `.env` flags, then loads the OpenAPI spec via `spec_loader.py`, which dereferences all `$ref` pointers and turns every operation into a named tool.
2. The MCP server registers those tools and listens on stdio for `list_tools` / `call_tool` messages from the client (Claude Desktop or your own app).
3. When a tool is called, `api_client.py` maps the arguments to `path/query/body parameters` and fires the real HTTP request.
4. In chat mode, the agentic loop asks the LLM backend (Ollama or Gemini, switchable via config) which tool to call, feeds the result back, and repeats until the model gives a final answer.

## In depth

### 1. Named tool

When the spec loader reads your OpenAPI file, every HTTP operation becomes a **named tool**, a function the LLM can call by name.
For example, an OpenAPI spec describes your API like this in YAML (or JSON):

```yaml
paths:
  /pets/{id}:
    get:
      operationId: getPetById
      summary: Find a pet by ID
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: integer
```

The spec loader reads that and creates a **named tool**, essentially a function card that says: *"there exists a callable thing named `getPetById`, it takes one integer argument called `id`, and here's what it does."* That card gets handed to the LLM so it knows the tool exists and how to invoke it. Every `operationId` in your spec becomes one tool name.

![OpenAPI to Tools](./img/openapi_to_tools.svg)


## 2. Dereferencing `$ref` pointers

OpenAPI specs often reuse definitions with `$ref` to avoid repetition:

```yaml
parameters:
  - $ref: '#/components/parameters/PetId'   # a pointer, not the real thing

components:
  parameters:
    PetId:
      name: id
      in: path
      required: true
      schema:
        type: integer
```

The `$ref` is just a pointer, like a symbolic link in a filesystem. _"Dereferencing"_ means following every pointer and replacing it with the actual content it points to, so the code that reads the spec sees one flat, complete structure with no dangling references. Without this step, you'd crash trying to read `param["name"]` on a `{"$ref": "..."}` object.

![Dereferencing $refs](./img/deref_refs.svg)


## 3. `serve` vs `chat` — what each mode actually does

These are two completely different use cases:

**`serve` mode** — you start the process and leave it running. It speaks the MCP protocol and waits for a client (like Claude Desktop) to connect to it and send requests. You never type into it yourself. It's a background service.

**`chat` mode** — you get an interactive terminal prompt. You type a question in plain English, the server figures out which API to call, calls it, and prints the answer back to you. It's a command-line chatbot wired directly to your API.---

![Serve vs. chat](./img/serve_vs_chat.svg)


## 4. "The MCP server registers those tools" — which tools?

Exactly the tools the spec loader extracted — one per API endpoint. When the MCP server starts up, it tells the MCP protocol layer: *"here is my list of available tools."* That list is the direct output of `extract_tools()` running on your OpenAPI spec. If your spec has 30 operations, the MCP server registers 30 tools. Nothing more, nothing less.


## 5. stdio — what is it?

`stdio` (standard input/output) is the simplest possible communication channel between two processes: one process writes text to its standard output, the other reads it from its standard input. It's the same mechanism as piping commands in a shell (`cat file | grep foo`).

The MCP protocol uses this because it's universally available and requires zero networking setup. Claude Desktop just spawns the MCP server as a child process and the two talk through a pipe.

![stdio pipe](./img/stdio_pipe.svg)


## 6. `list_tools` and `call_tool` — the two MCP messages

The MCP protocol is intentionally tiny. There are really only two messages that matter here:

**`list_tools`** — the client (Claude Desktop) asks: *"what can you do?"* The server replies with the full catalogue: names, descriptions, and input schemas for every registered tool. This is how Claude knows `getPetById` exists and what arguments it needs.

**`call_tool`** — the client says: *"run this tool with these arguments."* The server executes the real HTTP call and returns the result.So the full conversation between Claude Desktop and your MCP server looks like this: on startup it asks "what tools do you have?" and gets back the catalogue. Then each time the LLM decides to use one, it sends a `call_tool` message, your server makes the HTTP call, and sends the result back. That's the entire protocol.

![MCP Messages Sequence](./img/mcp_messages_sequence.svg)
