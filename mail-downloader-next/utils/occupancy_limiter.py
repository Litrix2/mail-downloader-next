from asyncio import Condition
from contextvars import ContextVar
from typing import Any, Self

__all__ = [
    'OccupancyLimiter'
]


class OccupancyLimiter:
    """Limit the total occupied size of multiple concurrent tasks
    to a certain range.
    """

    def __init__(self, max_size: int | None = None) -> None:
        self._max_size = (int(max_size)
                          if max_size is not None
                          else None)
        self._total_size = 0
        self._count = 0
        self._condition = Condition()
        self._size_ctx: ContextVar[int] = ContextVar('size', default=0)

    @property
    def max_size(self) -> int | None:
        return self._max_size

    def __call__(self, size: int | None) -> Self:
        self._size_ctx.set(size if size is not None else 0)
        return self

    async def __aenter__(self) -> None:
        def predicate():
            if self.max_size is None:
                return True
            if size <= self.max_size:
                return self._total_size+size <= self.max_size
            else:
                return self._count == 0
        size = self._size_ctx.get()
        async with self._condition:
            await self._condition.wait_for(predicate)
            self._count += 1
            self._total_size += size

    async def __aexit__(self, *_: Any) -> None:
        async with self._condition:
            self._total_size -= self._size_ctx.get()
            self._count -= 1
            self._condition.notify_all()
