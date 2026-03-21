"""FastAPI server with OpenAI-compatible endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .cli_to_openai import cli_result_to_openai, create_done_chunk
from .openai_to_cli import openai_to_cli
from .session_manager import session_manager
from .subprocess_manager import ClaudeSubprocess

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Claude Max API Proxy",
        description="OpenAI-compatible proxy wrapping Claude Code CLI",
        version=__version__,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("%s %s", request.method, request.url.path)
        return await call_next(request)

    @app.on_event("startup")
    async def startup():
        await session_manager.load()
        asyncio.create_task(_periodic_cleanup())

    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(3600)
            session_manager.cleanup()

    # ── GET /health ─────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "provider": "claude-code-cli",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        }

    # ── GET /v1/models ──────────────────────────────────────────────────

    @app.get("/v1/models")
    async def list_models():
        now = int(time.time())
        return {
            "object": "list",
            "data": [
                {"id": "claude-opus-4", "object": "model", "owned_by": "anthropic", "created": now},
                {"id": "claude-sonnet-4", "object": "model", "owned_by": "anthropic", "created": now},
                {"id": "claude-sonnet-5", "object": "model", "owned_by": "anthropic", "created": now},
                {"id": "claude-haiku-4", "object": "model", "owned_by": "anthropic", "created": now},
            ],
        }

    # ── POST /v1/chat/completions ───────────────────────────────────────

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        request_id = uuid.uuid4().hex[:24]
        body = await request.json()
        stream = body.get("stream") is True

        messages = body.get("messages")
        if not messages or not isinstance(messages, list) or len(messages) == 0:
            return JSONResponse(status_code=400, content={
                "error": {
                    "message": "messages is required and must be a non-empty array",
                    "type": "invalid_request_error",
                    "code": "invalid_messages",
                }
            })

        try:
            cli_input = openai_to_cli(body)

            if stream:
                return StreamingResponse(
                    _handle_streaming(cli_input, request_id),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Request-Id": request_id},
                )
            else:
                return await _handle_non_streaming(cli_input, request_id)

        except Exception as e:
            logger.error("Error: %s", e)
            return JSONResponse(status_code=500, content={
                "error": {"message": str(e), "type": "server_error", "code": None}
            })

    # ── Streaming ───────────────────────────────────────────────────────

    async def _handle_streaming(cli_input, request_id: str):
        yield ":ok\n\n"

        subprocess = ClaudeSubprocess()
        queue: asyncio.Queue = asyncio.Queue()

        is_first = True
        last_model = "claude-sonnet-4"
        is_complete = False

        def on_content_delta(event: dict):
            nonlocal is_first
            text = event.get("event", {}).get("delta", {}).get("text", "")
            if not text:
                return
            delta: dict = {"content": text}
            if is_first:
                delta["role"] = "assistant"
                is_first = False
            chunk = {
                "id": f"chatcmpl-{request_id}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": last_model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            queue.put_nowait(("data", chunk))

        def on_assistant(message: dict):
            nonlocal last_model
            last_model = message.get("message", {}).get("model", last_model)

        def on_result(_result: dict):
            nonlocal is_complete
            is_complete = True
            queue.put_nowait(("data", create_done_chunk(request_id, last_model)))
            queue.put_nowait(("done", None))

        def on_error(error):
            logger.error("Stream error: %s", error)
            queue.put_nowait(("error", str(error)))

        def on_close(code: int):
            if not is_complete and code != 0:
                queue.put_nowait(("error", f"Process exited with code {code}"))
            queue.put_nowait(("done", None))

        subprocess.on("content_delta", on_content_delta)
        subprocess.on("assistant", on_assistant)
        subprocess.on("result", on_result)
        subprocess.on("error", on_error)
        subprocess.on("close", on_close)

        try:
            await subprocess.start(cli_input.prompt, model=cli_input.model, session_id=cli_input.session_id)
        except RuntimeError as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'server_error', 'code': 'cli_not_found'}})}\n\n"
            yield "data: [DONE]\n\n"
            return

        done = False
        while not done:
            try:
                event_type, payload = await asyncio.wait_for(queue.get(), timeout=300)
            except asyncio.TimeoutError:
                yield f"data: {json.dumps({'error': {'message': 'Stream timeout', 'type': 'server_error', 'code': None}})}\n\n"
                subprocess.kill()
                break

            if event_type == "data":
                yield f"data: {json.dumps(payload)}\n\n"
            elif event_type == "error":
                yield f"data: {json.dumps({'error': {'message': payload, 'type': 'server_error', 'code': None}})}\n\n"
            elif event_type == "done":
                yield "data: [DONE]\n\n"
                done = True

    # ── Non-streaming ───────────────────────────────────────────────────

    async def _handle_non_streaming(cli_input, request_id: str):
        subprocess = ClaudeSubprocess()
        result_future: asyncio.Future = asyncio.get_event_loop().create_future()
        final_result: dict | None = None
        error_msg: str | None = None

        def on_result(result: dict):
            nonlocal final_result
            final_result = result

        def on_error(error):
            nonlocal error_msg
            error_msg = str(error)

        def on_close(code: int):
            if not result_future.done():
                result_future.set_result(code)

        subprocess.on("result", on_result)
        subprocess.on("error", on_error)
        subprocess.on("close", on_close)

        try:
            await subprocess.start(cli_input.prompt, model=cli_input.model, session_id=cli_input.session_id)
        except RuntimeError as e:
            return JSONResponse(status_code=500, content={
                "error": {"message": str(e), "type": "server_error", "code": None}
            })

        code = await result_future

        if error_msg:
            return JSONResponse(status_code=500, content={
                "error": {"message": error_msg, "type": "server_error", "code": None}
            })
        if final_result:
            return JSONResponse(content=cli_result_to_openai(final_result, request_id))

        return JSONResponse(status_code=500, content={
            "error": {"message": f"Claude CLI exited with code {code} without response", "type": "server_error", "code": None}
        })

    # ── 404 ─────────────────────────────────────────────────────────────

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def not_found(path: str):
        return JSONResponse(status_code=404, content={
            "error": {"message": "Not found", "type": "invalid_request_error", "code": "not_found"}
        })

    return app
