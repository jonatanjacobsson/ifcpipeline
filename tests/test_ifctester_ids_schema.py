"""The ifctester worker reads an IDS without the network.

ifctester's own IDS schema imports W3C schemas over HTTP, and W3C throttles
repeated automated fetches. Every validation job runs in a fresh spawn child,
so before ``tasks._local_ids_schema`` every job fetched from www.w3.org again.
"""

import socket
import sys
import threading
from pathlib import Path

import pytest

pytest.importorskip("ifctester")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "ifctester-worker"))

import tasks  # noqa: E402
from ifctester import ids  # noqa: E402

EXAMPLES = ROOT / "shared" / "examples"
IFC = EXAMPLES / "Building-Architecture.ifc"
IDS = EXAMPLES / "IDS-example.ids"


@pytest.fixture
def network_attempts(monkeypatch):
    """Drop ifctester's cached schema; fail this thread's DNS lookups and connects.

    Returns the attempts. Refusing alone proves nothing: on a refused connection
    xmlschema falls back to its own copies of the W3C schemas without a warning,
    so ifctester's network build would pass unnoticed. Tests assert none were made.
    """
    attempts = []
    caller = threading.get_ident()

    def refused(real):
        def call(*args, **kwargs):
            if threading.get_ident() != caller:  # another thread's traffic is not this test's
                return real(*args, **kwargs)
            attempts.append(args)
            raise OSError("no network in this test")

        return call

    monkeypatch.setattr(ids, "schema", None)
    monkeypatch.setattr(socket, "getaddrinfo", refused(socket.getaddrinfo))
    monkeypatch.setattr(socket.socket, "connect", refused(socket.socket.connect))
    return attempts


def test_a_validation_job_reads_the_ids_without_the_network(network_attempts, tmp_path):
    payload, _results, _passed, _failed = tasks._validate_and_report(
        str(IFC), str(IDS), str(tmp_path / "report.json"), "json"
    )
    assert payload["success"] and payload["total_specifications"] == 2
    assert network_attempts == []


def test_a_broken_ids_is_still_rejected(network_attempts, tmp_path):
    broken = tmp_path / "broken.ids"
    broken.write_text(
        IDS.read_text(encoding="utf-8").replace('ifcVersion="IFC2X3"', 'ifcVersion="IFC5"', 1),
        encoding="utf-8",
    )
    with pytest.raises(ids.IdsXmlValidationError):
        tasks._validate_and_report(str(IFC), str(broken), str(tmp_path / "report.json"), "json")
    assert network_attempts == []
