"""Entity-owned control tasks, with a permanent gate when unloading starts."""

import asyncio
from functools import wraps


class EntityOperations:
    """Track callers, including nested service/control work in the same task."""

    def __init__(self):
        self.closed = False
        self.tasks = set()

    async def close(self):
        self.closed = True
        current = asyncio.current_task()
        pending = tuple(task for task in self.tasks if task is not current)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def entity_operation(method):
    """Do not permit old callbacks or service calls to operate after removal."""
    @wraps(method)
    async def guarded(self, *args, **kwargs):
        operations = self._operations
        if operations.closed:
            return None
        task = asyncio.current_task()
        owner = task not in operations.tasks
        operations.tasks.add(task)
        try:
            return await method(self, *args, **kwargs)
        finally:
            if owner:
                operations.tasks.discard(task)
    return guarded
