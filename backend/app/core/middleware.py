from __future__ import annotations

import asyncio
import shutil
import tempfile

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_payload


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyTimeout(Exception):
    pass


class LocalRequestGuardMiddleware:
    """Reject oversized bodies and cross-origin mutations before route handling."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        allowed_origins: set[str],
        max_concurrent_uploads: int,
        body_timeout_seconds: float,
        min_free_space_bytes: int,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.allowed_origins = allowed_origins
        self.max_concurrent_uploads = max_concurrent_uploads
        self.body_timeout_seconds = body_timeout_seconds
        self.min_free_space_bytes = min_free_space_bytes
        self._multipart_active = 0
        self._multipart_guard = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw_headers = [(key.lower(), value) for key, value in scope.get("headers", [])]
        sensitive: dict[bytes, list[bytes]] = {
            name: [value for key, value in raw_headers if key == name]
            for name in (b"content-length", b"content-type", b"origin")
        }
        if any(len(values) > 1 for values in sensitive.values()):
            await self._reject(scope, send, 400, "DUPLICATE_HEADER", "Security-sensitive headers cannot be repeated.")
            return
        content_length = sensitive[b"content-length"][0] if sensitive[b"content-length"] else None
        declared_length: int | None = None
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._reject(scope, send, 400, "INVALID_CONTENT_LENGTH", "Content-Length must be an integer.")
                return
            if declared_length < 0 or declared_length > self.max_bytes:
                await self._reject(scope, send, 413, "REQUEST_TOO_LARGE", "Request body exceeds the configured limit.")
                return
        method = str(scope.get("method", "GET")).upper()
        origin_bytes = sensitive[b"origin"][0] if sensitive[b"origin"] else None
        if method not in {"GET", "HEAD", "OPTIONS"} and origin_bytes is not None:
            origin = origin_bytes.decode("latin-1")
            if origin not in self.allowed_origins:
                await self._reject(scope, send, 403, "ORIGIN_REJECTED", "Request origin is not allowed.")
                return
        content_type = sensitive[b"content-type"][0] if sensitive[b"content-type"] else b""
        is_multipart = content_type.strip().lower().startswith(b"multipart/form-data")
        admitted = False
        if is_multipart:
            expected_temp_bytes = declared_length if declared_length is not None else self.max_bytes
            if shutil.disk_usage(tempfile.gettempdir()).free < self.min_free_space_bytes + expected_temp_bytes:
                await self._reject(
                    scope,
                    send,
                    507,
                    "INSUFFICIENT_TEMP_STORAGE",
                    "Temporary storage is too low for upload parsing.",
                )
                return
            async with self._multipart_guard:
                if self._multipart_active >= self.max_concurrent_uploads:
                    await self._reject(
                        scope,
                        send,
                        429,
                        "UPLOAD_BUSY",
                        "The concurrent upload limit has been reached.",
                    )
                    return
                self._multipart_active += 1
                admitted = True
        consumed = 0

        async def limited_receive() -> Message:
            nonlocal consumed
            try:
                message = await asyncio.wait_for(receive(), timeout=self.body_timeout_seconds)
            except TimeoutError as exc:
                raise RequestBodyTimeout from exc
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await self._reject(scope, send, 413, "REQUEST_TOO_LARGE", "Request body exceeds the configured limit.")
        except RequestBodyTimeout:
            await self._reject(scope, send, 408, "REQUEST_TIMEOUT", "Request body reception timed out.")
        finally:
            if admitted:
                async with self._multipart_guard:
                    self._multipart_active -= 1

    @staticmethod
    async def _reject(scope: Scope, send: Send, status: int, code: str, message: str) -> None:
        response = JSONResponse(error_payload(code, message), status_code=status)
        await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}
