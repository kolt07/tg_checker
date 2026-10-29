import asyncio
import time


class Pacer:
    """Тримає мінімальний інтервал між початками викликів."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next = 0.0

    async def wait(self) -> None:
        now = time.monotonic()
        if self._next > now:
            await asyncio.sleep(self._next - now)
        self._next = time.monotonic() + self.interval
