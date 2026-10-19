"""Bounded TCP/serial I/O. Reconnection is owned by the gateway reader."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import socket
import time

import serialx

from .const import LOGGER


@dataclass
class AsyncConnection:
    host: str
    port: int | None
    serial_baud: int = 9600
    connect_timeout: float = 5.0
    io_timeout: float = 2.0
    reconnect_backoff: tuple[float, float] = (1.0, 30.0)

    def __post_init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._last_activity_mono = time.monotonic()
        self.last_rx: float | None = None
        self.generation = 0

    async def open(self) -> None:
        """One attempt only; callers decide when to retry."""
        if self._writer is not None:
            raise RuntimeError("Close the previous connection before opening")
        if self.port is None:
            operation = serialx.open_serial_connection(
                url=self.host, baudrate=self.serial_baud
            )
        else:
            operation = asyncio.open_connection(self.host, self.port)
        self._reader, self._writer = await asyncio.wait_for(
            operation, self.connect_timeout
        )
        sock = self._writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Optional OS-specific settings; serial and other OSes still work.
                for name, value in (("TCP_KEEPIDLE", 60), ("TCP_KEEPINTVL", 10), ("TCP_KEEPCNT", 3)):
                    if hasattr(socket, name):
                        sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
            except OSError:
                LOGGER.debug("TCP keepalive tuning unavailable", exc_info=True)
        self.generation += 1
        self._connected = True
        self.last_rx = None
        self._touch()

    def invalidate(self, generation: int | None = None) -> None:
        """Wake the reader without ever closing a newer connection."""
        if generation is not None and generation != self.generation:
            return
        self._connected = False
        if self._writer is not None:
            self._writer.close()

    async def close(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        self._connected = False
        if writer is None:
            return
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), self.io_timeout)
        except asyncio.CancelledError:
            writer.transport.abort()
            raise
        except Exception:
            writer.transport.abort()
            LOGGER.debug("Connection close required abort", exc_info=True)

    def _is_connected(self) -> bool:
        return self._connected

    def _touch(self) -> None:
        self._last_activity_mono = time.monotonic()

    def idle_since(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity_mono)

    async def send(self, data: bytes) -> int:
        writer = self._writer
        generation = self.generation
        if not self._connected or writer is None:
            raise ConnectionError("Connection is not ready")
        try:
            writer.write(data)
            await asyncio.wait_for(writer.drain(), self.io_timeout)
        except asyncio.CancelledError:
            self.invalidate(generation)
            raise
        except Exception:
            self.invalidate(generation)
            raise
        if generation != self.generation or not self._connected:
            raise ConnectionError("Connection changed during transmission")
        self._touch()
        LOGGER.debug("Sending: %s", data.hex())
        return len(data)

    async def recv(self, nbytes: int, timeout: float = 1.0) -> bytes:
        reader = self._reader
        if not self._connected or reader is None:
            raise ConnectionError("Connection is not ready")
        try:
            async with asyncio.timeout(timeout):
                chunk = await reader.read(nbytes)
        except asyncio.TimeoutError:
            return b""  # Quiet bus is not a disconnected bus.
        if not chunk:
            raise ConnectionError("Peer closed the connection (EOF)")
        self.last_rx = time.monotonic()
        self._touch()
        return chunk
