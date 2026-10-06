"""Лёгкий WebSocket→TCP прокси для noVNC.

Не поднимает Flask и не ходит в БД. Билет подписан SECRET_KEY.
Слушает только 127.0.0.1 — снаружи Nginx.

Запуск: python -m app.vnc_worker
"""

from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from app.services.vnc_token import VncTokenError, load_ticket

logger = logging.getLogger("bawh.vnc")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 6080
DEFAULT_MAX_SESSIONS = 4
TCP_CONNECT_TIMEOUT = 8.0
PIPE_CHUNK = 65536


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class _SessionGate:
    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._active = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1


def _ws_path(connection) -> str:
    request = getattr(connection, "request", None)
    if request is not None:
        path = getattr(request, "path", None)
        if path:
            return str(path)
    path = getattr(connection, "path", None)
    return str(path or "/")


def _token_from_path(path: str) -> str:
    parsed = urlparse(path)
    values = parse_qs(parsed.query).get("token") or []
    return (values[0] if values else "").strip()


async def _pipe_tcp_to_ws(reader: asyncio.StreamReader, websocket) -> None:
    while True:
        data = await reader.read(PIPE_CHUNK)
        if not data:
            break
        await websocket.send(data)


async def _pipe_ws_to_tcp(websocket, writer: asyncio.StreamWriter) -> None:
    async for message in websocket:
        if isinstance(message, str):
            payload = message.encode("latin-1")
        else:
            payload = message
        if not payload:
            continue
        writer.write(payload)
        await writer.drain()


async def _reject(websocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason[:120])
    except Exception:  # noqa: BLE001
        logger.debug("close after reject failed", exc_info=True)


async def handle_client(websocket, path: str | None = None, *, gate: _SessionGate) -> None:
    raw_path = path if path is not None else _ws_path(websocket)
    token = _token_from_path(raw_path)
    try:
        ticket = load_ticket(token)
    except VncTokenError as exc:
        logger.info("vnc ticket rejected: %s", exc)
        await _reject(websocket, 4401, str(exc))
        return

    if not await gate.acquire():
        logger.warning("vnc session limit reached")
        await _reject(websocket, 1013, "Слишком много сессий VNC, попробуйте позже.")
        return

    writer = None
    try:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ticket.ip, ticket.port),
                timeout=TCP_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            logger.info("vnc tcp %s:%s failed: %s", ticket.ip, ticket.port, exc)
            await _reject(
                websocket,
                1011,
                f"Нет VNC на {ticket.ip}:{ticket.port}. Проверьте агент и файрвол.",
            )
            return

        logger.info(
            "vnc session device=%s user=%s %s:%s",
            ticket.device_id,
            ticket.user_id,
            ticket.ip,
            ticket.port,
        )
        tcp_task = asyncio.create_task(_pipe_tcp_to_ws(reader, websocket))
        ws_task = asyncio.create_task(_pipe_ws_to_tcp(websocket, writer))
        done, pending = await asyncio.wait(
            {tcp_task, ws_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        for task in pending:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for task in done:
            exc = task.exception() if not task.cancelled() else None
            if exc is not None:
                logger.debug("vnc pipe stopped: %s", exc)
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        await gate.release()


async def run_server() -> None:
    host = (os.environ.get("VNC_LISTEN_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST
    port = _int_env("VNC_LISTEN_PORT", DEFAULT_PORT)
    max_sessions = _int_env("VNC_MAX_SESSIONS", DEFAULT_MAX_SESSIONS)
    gate = _SessionGate(max_sessions)

    async def handler(websocket, path=None):
        await handle_client(websocket, path, gate=gate)

    try:
        from websockets.asyncio.server import serve
    except ImportError:  # websockets 11/12
        from websockets.server import serve

    logger.info(
        "VNC proxy %s:%s (max sessions %s)",
        host,
        port,
        max_sessions,
    )
    async with serve(
        handler,
        host,
        port,
        max_size=2 * 1024 * 1024,
        ping_interval=30,
        ping_timeout=30,
        compression=None,
    ):
        await asyncio.Future()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    secret = (os.environ.get("SECRET_KEY") or "").strip()
    if not secret:
        logger.error("SECRET_KEY пуст — прокси VNC не запущен.")
        raise SystemExit(1)
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("остановка VNC proxy")


if __name__ == "__main__":
    main()
