# cmappy

[![PyPI version](https://img.shields.io/pypi/v/claude-max-proxy-py)](https://pypi.org/project/claude-max-proxy-py/)
[![Python](https://img.shields.io/pypi/pyversions/claude-max-proxy-py)](https://pypi.org/project/claude-max-proxy-py/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

OpenAI-compatible API proxy for **Claude Max** subscribers. Wraps the Claude Code CLI as a subprocess and exposes a standard `/v1/chat/completions` endpoint — zero API keys, zero extra cost.

```
Your App (any OpenAI-compatible client)
         ↓
    HTTP Request (OpenAI format)
         ↓
   cmappy (FastAPI, port 3456)
         ↓
   Claude Code CLI (subprocess)
         ↓
   OAuth Token (from Max subscription)
         ↓
   Anthropic API
         ↓
   Response → OpenAI format → Your App
```

## Prerequisites

1. **Claude Max subscription** ($100 or $200/month)
2. **Claude Code CLI** installed and authenticated:
   ```bash
   npm install -g @anthropic-ai/claude-code
   claude auth login
   ```
3. **Python 3.10+** and [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Quick start

```bash
# install (pick one)
uv tool install claude-max-proxy-py   # uv
pip install claude-max-proxy-py       # pip

# run
cmappy
```

The server starts on `http://127.0.0.1:3456`. Point any OpenAI-compatible client at it.

## Install

```bash
# from PyPI
uv tool install claude-max-proxy-py   # uv (recommended)
pip install claude-max-proxy-py       # pip

# from source
git clone https://github.com/p-sumann/claude-max-proxy-py.git
cd claude-max-proxy
uv tool install .   # or: pip install .
```

### Uninstall

```bash
uv tool uninstall claude-max-proxy-py   # uv
pip uninstall claude-max-proxy-py       # pip
```

## Usage

```bash
# default
cmappy

# custom port
cmappy --port 8080

# bind to all interfaces
cmappy --host 0.0.0.0

# skip auth check on startup
cmappy --skip-auth-check

# dev mode (auto-reload)
cmappy --reload
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/v1/models` | List available models |
| `POST` | `/v1/chat/completions` | Chat completions (streaming + non-streaming) |

### Models

| Model ID | CLI Alias |
|----------|-----------|
| `claude-opus-4` | `opus` |
| `claude-sonnet-4` | `sonnet` |
| `claude-sonnet-5` | `sonnet` |
| `claude-haiku-4` | `haiku` |

### curl

```bash
curl -X POST http://localhost:3456/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hello!"}]}'
```

### Streaming

```bash
curl -N -X POST http://localhost:3456/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hello!"}], "stream": true}'
```

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:3456/v1", api_key="not-needed")
response = client.chat.completions.create(
    model="claude-sonnet-4",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

## How it works

The proxy spawns the `claude` CLI with these flags:

```
claude --print --output-format stream-json --verbose \
  --include-partial-messages --model <alias> \
  --no-session-persistence "<prompt>"
```

It reads stdout as NDJSON and classifies each line into events: `content_block_delta` (text chunks for SSE), `assistant` (model name), and `result` (final response with usage stats). These are converted on-the-fly into OpenAI-format responses.

## Development

```bash
git clone https://github.com/p-sumann/claude-max-proxy-py.git
cd claude-max-proxy

# install with dev deps
uv sync --dev

# run without installing
uv run cmappy

# run tests
uv run pytest

# lint
uv run ruff check .
```

## Contributing

Contributions are welcome! Please read the [contributing guide](CONTRIBUTING.md) before opening a PR.

## Security

If you discover a security vulnerability, please see our [security policy](SECURITY.md). Do **not** open a public issue for security reports.

## License

[MIT](LICENSE)
