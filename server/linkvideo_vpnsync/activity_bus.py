from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from typing import Any, Iterator


class ActivityBus:
    """Small in-process fan-out bus for event-driven desktop activity updates.

    PostgreSQL remains authoritative. The bus is only a wake-up/notification
    channel so clients know when to request fresh rows; it never stores history.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()

    @contextmanager
    def subscribe(self, *, maxsize: int = 128) -> Iterator[queue.Queue[dict[str, Any]]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(8, int(maxsize)))
        with self._lock:
            self._subscribers.add(subscriber)
        try:
            yield subscriber
        finally:
            with self._lock:
                self._subscribers.discard(subscriber)

    def publish(self, event: dict[str, Any]) -> None:
        payload = dict(event or {})
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(payload)
            except queue.Full:
                # Drop the oldest wake-up rather than block RouterOS sync. The
                # desktop refreshes from PostgreSQL after any received event, so
                # losing one notification cannot lose historical data.
                try:
                    subscriber.get_nowait()
                except queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(payload)
                except queue.Full:
                    pass


activity_bus = ActivityBus()
