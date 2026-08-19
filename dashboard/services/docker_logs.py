"""Read-only Docker Engine API access for the live log viewer.

Reaches the engine over its unix socket, the same way Dozzle does. Two hard rules:

* **Read-only endpoints only.** Nothing here writes; the socket grants far more than
  that, so the surface is kept deliberately narrow.
* **Scoped to this compose project.** Streaming is restricted to containers carrying the
  dashboard's own ``com.docker.compose.project`` label. Without that, a crafted
  ``?container=<id>`` would stream any container on the host — n8n, cde, baserow — and
  their logs carry credentials.

The socket is optional: when it is absent the log pages degrade to an explanatory panel
rather than erroring, so the dashboard still runs without it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator, Iterator

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Docker multiplexes non-TTY output: [stream_type:1][000:3][size:4 big-endian][payload].
_HEADER = 8
_STDOUT, _STDERR = 1, 2


def available() -> bool:
    return os.path.exists(settings.DOCKER_SOCKET)


def _transport() -> httpx.HTTPTransport:
    return httpx.HTTPTransport(uds=settings.DOCKER_SOCKET)


def _client() -> httpx.Client:
    return httpx.Client(transport=_transport(), base_url="http://docker", timeout=10.0)


def _async_client(timeout: httpx.Timeout | float | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(uds=settings.DOCKER_SOCKET),
        base_url="http://docker",
        timeout=timeout if timeout is not None else httpx.Timeout(10.0, read=None),
    )


def _name(c: dict) -> str:
    names = c.get("Names") or []
    return (names[0] if names else c.get("Id", "")[:12]).lstrip("/")


def _project_of(c: dict) -> str:
    return (c.get("Labels") or {}).get("com.docker.compose.project", "")


_own_project: str | None = None


def own_project() -> str:
    """Compose project of the container the dashboard itself runs in.

    Falls back to ``COMPOSE_PROJECT_NAME``, then to the ifcpipeline default, so the
    allowlist still has a boundary when the dashboard runs outside compose.
    """
    global _own_project
    if _own_project is not None:
        return _own_project

    hostname = os.getenv("HOSTNAME", "")
    if hostname and available():
        try:
            with _client() as c:
                d = c.get(f"/containers/{hostname}/json").json()
            _own_project = (d.get("Config", {}).get("Labels") or {}).get(
                "com.docker.compose.project", ""
            )
        except Exception as e:
            logger.debug("Could not read own compose project: %s", e)
            _own_project = ""
    else:
        _own_project = ""

    if not _own_project:
        _own_project = os.getenv("COMPOSE_PROJECT_NAME", "ifcpipeline")
    return _own_project


def list_containers() -> list[dict]:
    """Running containers in this compose project: id, name, service, state."""
    if not available():
        return []
    try:
        with _client() as c:
            raw = c.get("/containers/json", params={"all": 0}).json()
    except Exception as e:
        logger.warning("Docker container list failed: %s", e)
        return []

    project = own_project()
    out = []
    for x in raw:
        if _project_of(x) != project:
            continue
        labels = x.get("Labels") or {}
        out.append(
            {
                "id": x.get("Id", ""),
                "short_id": x.get("Id", "")[:12],
                "name": _name(x),
                "service": labels.get("com.docker.compose.service", ""),
                "state": x.get("State", ""),
                "status": x.get("Status", ""),
            }
        )
    out.sort(key=lambda c: c["name"])
    return out


def allowed_ids() -> set[str]:
    """Full container ids the log endpoints may stream."""
    return {c["id"] for c in list_containers()}


def resolve(ref: str) -> dict | None:
    """Resolve a container id (full or short), name, or RQ worker hostname."""
    if not ref:
        return None
    for c in list_containers():
        if c["id"] == ref or c["short_id"] == ref[:12] or c["name"] == ref:
            return c
    return None


def containers_for_queue(queue: str, workers: list[dict]) -> list[dict]:
    """Containers hosting a live worker that listens on *queue*.

    *workers* comes from ``redis_service.get_overview()`` — each carries the container's
    short id in ``hostname``, which is how an RQ worker maps back to its container.
    """
    hosts = {
        str(w.get("hostname") or "")[:12]
        for w in workers
        if queue in (w.get("queues") or [])
    }
    hosts.discard("")
    return [c for c in list_containers() if c["short_id"] in hosts]


class _Demuxer:
    """Reassemble Docker's framed log stream into whole lines.

    Chunks arrive without regard for frame or line boundaries, so both are buffered.
    """

    def __init__(self, tty: bool):
        self.tty = tty
        self.buf = b""
        self.partial: dict[int, bytes] = {_STDOUT: b"", _STDERR: b""}

    def feed(self, chunk: bytes) -> Iterator[tuple[int, str]]:
        self.buf += chunk
        if self.tty:
            yield from self._lines(_STDOUT, self.buf)
            self.buf = b""
            return
        while len(self.buf) >= _HEADER:
            stream = self.buf[0]
            size = int.from_bytes(self.buf[4:_HEADER], "big")
            if len(self.buf) < _HEADER + size:
                break
            payload = self.buf[_HEADER : _HEADER + size]
            self.buf = self.buf[_HEADER + size :]
            if stream not in (_STDOUT, _STDERR):
                stream = _STDOUT
            yield from self._lines(stream, payload)

    def _lines(self, stream: int, payload: bytes) -> Iterator[tuple[int, str]]:
        data = self.partial[stream] + payload
        *complete, self.partial[stream] = data.split(b"\n")
        for line in complete:
            text = line.decode("utf-8", "replace").rstrip("\r")
            if text:
                yield stream, text

    def flush(self) -> Iterator[tuple[int, str]]:
        for stream, rest in self.partial.items():
            if rest:
                yield stream, rest.decode("utf-8", "replace").rstrip("\r")
        self.partial = {_STDOUT: b"", _STDERR: b""}


def _split_timestamp(line: str) -> tuple[str, str]:
    """Docker prefixes each line with an RFC3339Nano timestamp when timestamps=1."""
    ts, sep, rest = line.partition(" ")
    if sep and len(ts) >= 20 and ts[4] == "-" and ts.endswith("Z"):
        return ts, rest
    return "", line


async def _pump(
    container: dict, tail: int, follow: bool, out: asyncio.Queue, stop: asyncio.Event
) -> None:
    """Stream one container's logs into the shared queue."""
    params = {
        "stdout": 1,
        "stderr": 1,
        "timestamps": 1,
        "tail": str(tail),
        "follow": 1 if follow else 0,
    }
    try:
        async with _async_client() as client:
            info = await client.get(f"/containers/{container['id']}/json")
            tty = bool(info.json().get("Config", {}).get("Tty"))
            dec = _Demuxer(tty)
            async with client.stream(
                "GET", f"/containers/{container['id']}/logs", params=params
            ) as resp:
                if resp.status_code != 200:
                    await out.put(
                        {
                            "kind": "error",
                            "container": container["name"],
                            "message": f"docker returned {resp.status_code}",
                        }
                    )
                    return
                async for chunk in resp.aiter_bytes():
                    if stop.is_set():
                        break
                    for stream, line in dec.feed(chunk):
                        ts, text = _split_timestamp(line)
                        await out.put(
                            {
                                "kind": "log",
                                "container": container["name"],
                                "service": container["service"],
                                "stream": "stderr" if stream == _STDERR else "stdout",
                                "ts": ts,
                                "message": text,
                            }
                        )
                for stream, line in dec.flush():
                    ts, text = _split_timestamp(line)
                    await out.put(
                        {
                            "kind": "log",
                            "container": container["name"],
                            "service": container["service"],
                            "stream": "stderr" if stream == _STDERR else "stdout",
                            "ts": ts,
                            "message": text,
                        }
                    )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        await out.put(
            {"kind": "error", "container": container["name"], "message": str(e)}
        )


async def stream(
    containers: list[dict], tail: int = 200, follow: bool = True
) -> AsyncIterator[str]:
    """Yield SSE frames merging every container's log stream.

    Emits a periodic comment so the client notices a dead connection, and a final
    ``end`` event when a non-following read runs out.
    """
    if not containers:
        yield 'event: error\ndata: {"message":"no containers to stream"}\n\n'
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(_pump(c, tail, follow, queue, stop)) for c in containers
    ]

    meta = json.dumps(
        {"containers": [{"name": c["name"], "service": c["service"]} for c in containers]}
    )
    yield f"event: meta\ndata: {meta}\n\n"

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                if all(t.done() for t in tasks) and queue.empty():
                    break
                yield ": keepalive\n\n"
                continue
            event = "error" if item.get("kind") == "error" else "log"
            yield f"event: {event}\ndata: {json.dumps(item)}\n\n"
            if all(t.done() for t in tasks) and queue.empty():
                break
    except asyncio.CancelledError:
        raise
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    yield 'event: end\ndata: {}\n\n'
