"""OS-backed advisory locks: released by the operating system on process exit."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile


class FileLease:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.stream = None

    def acquire(self) -> bool:
        if self.stream is not None:
            raise RuntimeError('Lease already acquired')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            stream.close()
            return False
        except OSError as error:
            stream.close()
            if error.errno in (11, 13, 36):
                return False
            raise
        self.stream = stream
        return True

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def lease_held(path: Path) -> bool:
    if not path.is_file():
        return False
    lease = FileLease(path)
    try:
        return not lease.acquire()
    finally:
        lease.close()


def session_lock_path() -> Path:
    # All work directories for this Windows user share one conservative CAD gate.
    return Path(tempfile.gettempdir()) / 'lucidshape-mcp-locks' / 'native-session.lock'


@asynccontextmanager
async def session_gate(path=None):
    lease = FileLease(path or session_lock_path())
    try:
        while not lease.acquire():
            await asyncio.sleep(.1)
        yield
    finally:
        lease.close()
