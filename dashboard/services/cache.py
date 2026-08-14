"""Thread-safe in-memory TTL cache for dashboard service calls."""

import threading
from cachetools import TTLCache

_lock = threading.Lock()
_caches: dict[str, TTLCache] = {}
_inflight: dict[tuple[str, int], threading.Event] = {}
_inflight_results: dict[tuple[str, int], object] = {}
_inflight_errors: dict[tuple[str, int], BaseException] = {}


def get_or_set(key: str, ttl: int, fn, *args, **kwargs):
    """Return cached value for *key*, or call fn() and cache the result for *ttl* seconds.

    Singleflight: if multiple threads miss the cache for the same (key, ttl), only
    one executes fn() and the others wait and share the result. Prevents thundering
    herd against slow upstreams (n8n API, network share directory walks).
    """
    with _lock:
        if ttl not in _caches:
            _caches[ttl] = TTLCache(maxsize=512, ttl=ttl)
        cache = _caches[ttl]
        if key in cache:
            return cache[key]

        flight_key = (key, ttl)
        event = _inflight.get(flight_key)
        if event is None:
            event = threading.Event()
            _inflight[flight_key] = event
            is_leader = True
        else:
            is_leader = False

    if not is_leader:
        event.wait()
        with _lock:
            if flight_key in _inflight_errors:
                raise _inflight_errors[flight_key]
            # Read the cache first: the leader writes it *before* setting the event and
            # drops _inflight_results immediately after, so a follower that wakes late
            # would otherwise get None — which is not a value fn() ever returned.
            cache = _caches.get(ttl)
            if cache is not None and key in cache:
                return cache[key]
            if flight_key in _inflight_results:
                return _inflight_results[flight_key]
        # Entry already expired again; compute rather than hand back a bogus None.
        return fn(*args, **kwargs)

    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:
        with _lock:
            _inflight_errors[flight_key] = exc
            event.set()
            _inflight.pop(flight_key, None)
        try:
            raise
        finally:
            # Cleanup outside the raise path (best-effort, next caller will retry).
            with _lock:
                _inflight_errors.pop(flight_key, None)

    with _lock:
        cache = _caches[ttl]
        cache[key] = result
        _inflight_results[flight_key] = result
        event.set()
        _inflight.pop(flight_key, None)
    try:
        return result
    finally:
        with _lock:
            _inflight_results.pop(flight_key, None)


def invalidate(key: str, ttl: int):
    """Remove a specific key from the cache bucket for the given TTL."""
    with _lock:
        cache = _caches.get(ttl)
        if cache and key in cache:
            del cache[key]


def invalidate_key(key: str) -> None:
    """Remove *key* from every TTL bucket (same logical key can exist under multiple ttls)."""
    with _lock:
        for cache in _caches.values():
            if key in cache:
                del cache[key]
