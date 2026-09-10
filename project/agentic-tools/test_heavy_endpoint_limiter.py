"""Tests for the _heavy_endpoint concurrency limiter in api_server.

The limiter caps how many memory-heavy sync endpoints run at once so a burst
of Risk Loop calls can't OOM-kill the worker. These tests exercise the
decorator directly against a fresh BoundedSemaphore rather than the module
global, so limit/timeout can be controlled per-case.
"""
import threading
import time

import pytest
from fastapi import HTTPException

import api_server


@pytest.fixture
def limiter(monkeypatch):
    """Bind a fresh 2-slot semaphore with a short queue timeout."""
    monkeypatch.setattr(api_server, "_heavy_semaphore", threading.BoundedSemaphore(2))
    monkeypatch.setattr(api_server, "_HEAVY_LIMIT", 2)
    monkeypatch.setattr(api_server, "_HEAVY_QUEUE_TIMEOUT", 0.2)
    return api_server._heavy_endpoint


def test_passes_through_args_and_result(limiter):
    @limiter
    def endpoint(req, ticker="x"):
        return {"req": req, "ticker": ticker}

    assert endpoint(5, ticker="NVDA") == {"req": 5, "ticker": "NVDA"}


def test_signature_preserved_for_fastapi(limiter):
    import inspect

    def endpoint(req: int, ticker: str = "x"):
        return req

    wrapped = limiter(endpoint)
    assert str(inspect.signature(wrapped)) == "(req: int, ticker: str = 'x')"
    assert wrapped.__name__ == "endpoint"


def test_slot_released_after_success(limiter):
    @limiter
    def endpoint():
        return "ok"

    for _ in range(10):
        assert endpoint() == "ok"
    # both slots should still be free
    assert api_server._heavy_semaphore.acquire(blocking=False)
    assert api_server._heavy_semaphore.acquire(blocking=False)


def test_slot_released_after_exception(limiter):
    @limiter
    def endpoint():
        raise HTTPException(status_code=500, detail="boom")

    for _ in range(5):
        with pytest.raises(HTTPException):
            endpoint()
    assert api_server._heavy_semaphore.acquire(blocking=False)
    assert api_server._heavy_semaphore.acquire(blocking=False)


def test_sheds_load_with_503_when_full(limiter):
    release = threading.Event()

    @limiter
    def slow():
        release.wait(timeout=2)
        return "done"

    threads = [threading.Thread(target=slow) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.05)  # let both acquire their slot

    with pytest.raises(HTTPException) as ei:
        slow()
    assert ei.value.status_code == 503

    release.set()
    for t in threads:
        t.join()


def test_disabled_when_semaphore_is_none(monkeypatch):
    monkeypatch.setattr(api_server, "_heavy_semaphore", None)

    @api_server._heavy_endpoint
    def endpoint():
        return "unlimited"

    assert endpoint() == "unlimited"
