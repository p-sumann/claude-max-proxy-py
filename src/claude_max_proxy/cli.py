"""CLI entry point.

Usage:
    cmappy
    cmappy --port 8080
    cmappy --host 0.0.0.0
"""

from __future__ import annotations

import asyncio
import sys

import click
import uvicorn

from . import __version__
from .subprocess_manager import verify_auth, verify_claude


def _ok(msg: str) -> None:
    click.echo(click.style(f"  ✓ {msg}", fg="green"))


def _fail(msg: str) -> None:
    click.echo(click.style(f"  ✗ {msg}", fg="red"))


def _hint(msg: str) -> None:
    click.echo(click.style(f"    → {msg}", fg="yellow"))


@click.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
@click.option("--port", "-p", default=3456, show_default=True, help="Bind port")
@click.option("--reload", is_flag=True, help="Auto-reload on code changes (dev mode)")
@click.option("--skip-auth-check", is_flag=True, help="Skip the auth check on startup")
@click.version_option(version=__version__)
def main(host: str, port: int, reload: bool, skip_auth_check: bool) -> None:
    """cmappy — Use your Claude Max subscription with any OpenAI client."""

    click.echo("")
    click.echo(click.style("  cmappy", bold=True) + f"  v{__version__}")
    click.echo("  " + "─" * 30)
    click.echo("")

    # ── Check Claude CLI ──────────────────────────────────────────────
    click.echo("  Checking Claude CLI...")
    cli_check = asyncio.run(verify_claude())
    if not cli_check["ok"]:
        _fail("Claude CLI not found")
        _hint("Install it:  npm install -g @anthropic-ai/claude-code")
        _hint("Then retry:  cmappy")
        click.echo("")
        sys.exit(1)
    _ok(f"Claude CLI: {cli_check.get('version', 'OK')}")

    # ── Check authentication ──────────────────────────────────────────
    if skip_auth_check:
        click.echo(click.style("  ⏭ Skipping auth check (--skip-auth-check)", fg="yellow"))
    else:
        click.echo("  Checking authentication...")
        auth_check = asyncio.run(verify_auth())
        if not auth_check["ok"]:
            error = auth_check.get("error", "Unknown error")
            _fail("Authentication failed")
            for line in error.split("\n"):
                _hint(line.strip())
            click.echo("")
            _hint("Fix:  claude auth login")
            _hint("Or:   cmappy --skip-auth-check  (to start anyway)")
            click.echo("")
            sys.exit(1)
        _ok("Authenticated")

    click.echo("")
    click.echo(f"  Server:    http://{host}:{port}")
    click.echo(f"  Endpoint:  http://{host}:{port}/v1/chat/completions")
    click.echo("")
    click.echo("  Test with:")
    click.echo(click.style(f"    curl -X POST http://localhost:{port}/v1/chat/completions \\", dim=True))
    click.echo(click.style('      -H "Content-Type: application/json" \\', dim=True))
    click.echo(click.style("      -d '{\"model\": \"claude-sonnet-4\", \"messages\": [{\"role\": \"user\", \"content\": \"Hello!\"}]}'", dim=True))
    click.echo("")

    uvicorn.run(
        "claude_max_proxy.server:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
