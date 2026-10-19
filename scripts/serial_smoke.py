"""Exercise the real serialx transport against a Linux pseudo-terminal."""
import asyncio
import os
from pathlib import Path
import pty
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.kocom_wallpad.transport import AsyncConnection


async def main():
    master, slave = pty.openpty()
    conn = AsyncConnection(os.ttyname(slave), None)
    try:
        await conn.open()
        os.write(master, b"wallpad")
        assert await conn.recv(7) == b"wallpad"
        assert await conn.recv(7, 0.01) == b""
        assert conn._is_connected()
        await conn.send(b"reply")
        assert await asyncio.wait_for(asyncio.to_thread(os.read, master, 5), 2) == b"reply"
        await conn.close()
        await conn.open()
        os.write(master, b"again")
        assert await conn.recv(5) == b"again"
        assert conn.generation == 2
        print("PASS: serialx PTY receive, quiet timeout, send, close and reopen")
    finally:
        await conn.close()
        os.close(master)
        os.close(slave)


asyncio.run(main())
