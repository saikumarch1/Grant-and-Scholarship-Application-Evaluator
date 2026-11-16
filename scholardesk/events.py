"""Event bus. Agents and tools emit events as they work; the server streams them to the browser live."""
import asyncio
import json
import time

TERMINAL = ("run_end", "run_error")


class EventBus:
    def __init__(self, log=None):
        self.log = log
        self.history: dict = {}
        self.subscribers: dict = {}

    def emit(self, run_id: str, type_: str, agent: str = None, **data) -> dict:
        events = self.history.setdefault(run_id, [])
        event = {"seq": len(events) + 1, "ts": round(time.time(), 3), "run_id": run_id, "type": type_, "agent": agent, "data": data}
        events.append(event)
        for queue in self.subscribers.get(run_id, []):
            queue.put_nowait(event)
        if self.log:
            self.log.append(run_id, agent or "System", type_, json.dumps(data, default=str)[:600])
        return event

    async def stream(self, run_id: str, idle: float = None):
        """Replay everything so far, then follow live until the run ends. Yields None after `idle` quiet seconds."""
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.setdefault(run_id, []).append(queue)
        replay = list(self.history.get(run_id, []))
        last = 0
        try:
            for event in replay:
                last = event["seq"]
                yield event
                if event["type"] in TERMINAL:
                    return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), idle) if idle else await queue.get()
                except asyncio.TimeoutError:
                    yield None
                    continue
                if event["seq"] <= last:
                    continue
                yield event
                if event["type"] in TERMINAL:
                    return
        finally:
            self.subscribers[run_id].remove(queue)
