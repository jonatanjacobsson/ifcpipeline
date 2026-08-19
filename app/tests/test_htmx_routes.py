"""HTTP smoke tests for the /htmx/* routes and the JSON API they share.

These exercise route wiring and rendering. Pages degrade to an inline error panel
when Redis/Postgres/n8n are unreachable, so the assertions check for a 200 with
HTML rather than for populated data.
"""

import os
import sys
import unittest

# app/tests -> app -> ifcpipeline
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
_DASH = os.path.join(os.path.dirname(_APP), "dashboard")
for p in (_DASH, _APP):
    if p not in sys.path:
        sys.path.insert(0, p)

SHELL_PAGES = (
    "/htmx/",
    "/htmx/jobs/",
    "/htmx/jobs/?queue=ifcclash&state=failed",
    "/htmx/history/",
    "/htmx/workers/",
    "/htmx/logs/",
    "/htmx/network-share/",
    "/htmx/n8n/",
    "/htmx/database/",
)

FRAGMENTS = (
    "/htmx/overview",
    "/htmx/jobs/table?state=live",
    "/htmx/header-status",
    "/htmx/jobs/table",
    "/htmx/history/table",
    "/htmx/workers/content",
    "/htmx/n8n/content",
    "/htmx/database/content",
)

REDIRECTS = (
    ("/htmx", "/htmx/"),
    ("/htmx/jobs", "/htmx/jobs/"),
    ("/htmx/history", "/htmx/history/"),
    ("/htmx/workers", "/htmx/workers/"),
    ("/htmx/logs", "/htmx/logs/"),
    ("/htmx/network-share", "/htmx/network-share/"),
    ("/htmx/n8n", "/htmx/n8n/"),
    ("/htmx/database", "/htmx/database/"),
)


class TestHtmxRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from main import app

        self.client = TestClient(app)

    def test_root_redirects_to_htmx(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertIn(r.status_code, (301, 302, 307, 308))
        self.assertEqual(r.headers.get("location"), "/htmx/")

    def test_trailing_slash_redirects(self):
        for path, target in REDIRECTS:
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertIn(r.status_code, (301, 302, 307, 308))
                self.assertEqual(r.headers.get("location"), target)

    def test_shell_pages_return_html(self):
        for path in SHELL_PAGES:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/html", r.headers.get("content-type", ""))
                self.assertIn("htmx.org", r.text)

    def test_fragments_return_html(self):
        for path in FRAGMENTS:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertIn("text/html", r.headers.get("content-type", ""))

    def test_sidebar_lists_only_the_supported_pages(self):
        r = self.client.get("/htmx/")
        for label in ("Jobs", "History", "Workers", "Logs", "Network Share", "n8n", "Database"):
            self.assertIn(f">{label}<", r.text.replace("\n", ""), label)
        # Dropped subsystems must not reappear via a stale nav entry.
        for gone in ("Interaxo", "StreamBIM", "Revit Analytics", "IFC Analysis", "Classic dashboard"):
            self.assertNotIn(gone, r.text, gone)

    def test_json_api_surface(self):
        for path in (
            "/api/rq/overview",
            "/api/rq/jobs",
            "/api/rq/history",
            "/api/system/health",
            "/api/system/db/stats",
            "/api/n8n/status",
            "/api/network-share/status",
            "/api/logs/status",
            "/api/logs/containers",
        ):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)

    def test_dropped_routes_are_gone(self):
        for path in (
            "/htmx/interaxo/",
            "/htmx/streambim/",
            "/htmx/revit-analytics/",
            "/htmx/ifc-analysis/",
            "/htmx/worker-analytics/",
            "/api/interaxo/status",
            "/api/streambim/auth-status",
            "/api/rq/log-summary",
        ):
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertEqual(r.status_code, 404, path)

    def test_invalid_job_id_is_rejected(self):
        r = self.client.get("/api/rq/jobs/not a valid id")
        self.assertIn(r.status_code, (400, 404))


class TestRqCompat(unittest.TestCase):
    """The dashboard has no worker task code, so payload access must never raise."""

    def test_status_value_unwraps_enum(self):
        from rq.job import JobStatus

        from services.rq_compat import status_value

        self.assertEqual(status_value(JobStatus.FINISHED), "finished")
        self.assertEqual(status_value("queued"), "queued")
        self.assertIsNone(status_value(None))

    def test_accessors_tolerate_undeserializable_jobs(self):
        from rq.exceptions import DeserializationError

        from services.rq_compat import safe_args, safe_description, safe_func_name, safe_status

        class Undeserializable:
            @property
            def func_name(self):
                raise DeserializationError()

            @property
            def description(self):
                raise DeserializationError()

            @property
            def args(self):
                raise DeserializationError()

            def get_status(self):
                raise DeserializationError()

        job = Undeserializable()
        self.assertIsNone(safe_func_name(job))
        self.assertEqual(safe_description(job), "")
        self.assertIsNone(safe_args(job))
        self.assertEqual(safe_status(job), "unknown")


class TestEscaping(unittest.TestCase):
    """FastHTML escapes text and attributes itself; escaping again corrupts output.

    Job descriptions are Python reprs full of `'`, so a stray `html.escape()` renders
    as a literal `&#x27;` on screen. The one exception is raw-HTML output wrapped in
    NotStr, which has to escape its own input.
    """

    def test_components_are_not_double_escaped(self):
        from fastcore.xml import to_xml
        from fasthtml.components import Td

        payload = "tasks.run_ingest({'a': 1, 'b': '<x>'})"
        html = to_xml(Td(payload, title=payload))
        self.assertNotIn("&amp;#x27;", html)
        self.assertNotIn("&amp;lt;", html)
        self.assertIn("{'a': 1", html)      # apostrophes survive verbatim
        self.assertIn("&lt;x&gt;", html)    # angle brackets still neutralised

    def test_renderers_do_not_hand_escape(self):
        """Only the network-share page builds raw HTML by hand and may call escape()."""
        import pathlib

        renderers = pathlib.Path(__file__).resolve().parents[1] / "pipeline_ui" / "renderers"
        allowed = {"network_share_htmx.py", "htmx_common.py"}
        offenders = [
            p.name
            for p in renderers.glob("*.py")
            if p.name not in allowed and "escape(" in p.read_text()
        ]
        self.assertEqual(offenders, [], f"hand-escaping re-introduced in {offenders}")

    def test_json_block_escapes_its_input(self):
        """highlight_json_html returns raw HTML for NotStr, so it must escape."""
        from pipeline_ui.renderers.htmx_common import highlight_json_html

        out = highlight_json_html({"note": "<script>alert(1)</script>", "n": 3})
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn('<span class="jh-key">', out)  # highlighting still applied
        self.assertIn('<span class="jh-num">', out)


class TestCacheSingleflight(unittest.TestCase):
    """A follower must never receive None: it produced a 500 on the overview page."""

    def test_followers_get_the_leaders_value(self):
        import threading

        from services import cache

        started = threading.Barrier(8)
        calls = []

        def slow():
            calls.append(1)
            import time

            time.sleep(0.05)
            return {"ok": True}

        results = [None] * 8

        def worker(i):
            started.wait()
            results[i] = cache.get_or_set("sf_test", 30, slow)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        cache.invalidate("sf_test", 30)
        self.assertEqual(len(calls), 1, "singleflight should call fn once")
        self.assertTrue(all(r == {"ok": True} for r in results), results)

    def test_follower_error_propagates(self):
        import threading

        from services import cache

        started = threading.Barrier(4)
        errors = [None] * 4

        def boom():
            import time

            time.sleep(0.05)
            raise RuntimeError("upstream down")

        def worker(i):
            started.wait()
            try:
                cache.get_or_set("sf_err", 30, boom)
            except Exception as e:
                errors[i] = str(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(e == "upstream down" for e in errors), errors)


class TestLogScoping(unittest.TestCase):
    """The docker socket grants far more than log reads, so the endpoints must only
    ever stream this compose project's own containers."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from main import app

        self.client = TestClient(app)

    def test_stream_requires_a_target(self):
        from services import docker_logs

        if not docker_logs.available():
            self.skipTest("docker socket not mounted")
        r = self.client.get("/api/logs/stream")
        self.assertEqual(r.status_code, 400)

    def test_foreign_container_is_refused(self):
        from services import docker_logs

        if not docker_logs.available():
            self.skipTest("docker socket not mounted")
        # A plausible id and a plausible name, neither in this project.
        for ref in ("0" * 64, "cde-backend-1", "some-other-stack-db-1"):
            with self.subTest(ref=ref):
                r = self.client.get(f"/api/logs/stream?container={ref}&follow=false")
                self.assertEqual(r.status_code, 404)

    def test_listed_containers_are_all_in_this_project(self):
        from services import docker_logs

        if not docker_logs.available():
            self.skipTest("docker socket not mounted")
        project = docker_logs.own_project()
        self.assertTrue(project, "compose project must be known to scope the allowlist")
        r = self.client.get("/api/logs/containers")
        for c in r.json():
            self.assertIn("id", c)
            self.assertTrue(docker_logs.resolve(c["name"]), c["name"])

    def test_unknown_queue_is_refused(self):
        from services import docker_logs

        if not docker_logs.available():
            self.skipTest("docker socket not mounted")
        r = self.client.get("/api/logs/stream?queue=definitely-not-a-queue&follow=false")
        self.assertEqual(r.status_code, 404)

    def test_logs_page_degrades_without_the_socket(self):
        """The page must explain itself, not 500, when the socket is absent."""
        from unittest.mock import patch

        with patch("services.docker_logs.available", return_value=False):
            r = self.client.get("/htmx/logs/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("docker socket", r.text.lower())


class TestLogDemux(unittest.TestCase):
    """Docker frames non-TTY output; chunks split frames and lines arbitrarily."""

    def test_frames_split_across_chunks_reassemble(self):
        from services.docker_logs import _Demuxer

        def frame(stream, payload):
            b = payload.encode()
            return bytes([stream, 0, 0, 0]) + len(b).to_bytes(4, "big") + b

        blob = frame(1, "hello world\n") + frame(2, "an error\n") + frame(1, "tail")
        d = _Demuxer(tty=False)
        got = []
        # Feed one byte at a time: the worst case for a stateful parser.
        for i in range(len(blob)):
            got.extend(d.feed(blob[i : i + 1]))
        got.extend(d.flush())

        self.assertEqual(
            got, [(1, "hello world"), (2, "an error"), (1, "tail")]
        )

    def test_tty_streams_are_passed_through(self):
        from services.docker_logs import _Demuxer

        d = _Demuxer(tty=True)
        got = list(d.feed(b"plain line\nsecond\n"))
        self.assertEqual(got, [(1, "plain line"), (1, "second")])

    def test_timestamp_is_split_off(self):
        from services.docker_logs import _split_timestamp

        ts, msg = _split_timestamp("2026-08-14T09:22:22.635860188Z Worker started")
        self.assertEqual(ts, "2026-08-14T09:22:22.635860188Z")
        self.assertEqual(msg, "Worker started")
        # A line that merely starts with a word must not lose it.
        ts2, msg2 = _split_timestamp("Worker started")
        self.assertEqual(ts2, "")
        self.assertEqual(msg2, "Worker started")


class TestJobsLiveFilter(unittest.TestCase):
    def test_live_excludes_finished(self):
        """`live` is what makes Jobs different from History; it must not include finished."""
        import inspect

        from services import redis_service

        src = inspect.getsource(redis_service.get_jobs)
        self.assertIn('live = state == "live"', src)
        # the finished branch is the one line that must not be gated on `live`
        finished_line = next(
            l for l in src.splitlines() if '"finished")' in l and "state in" in l
        )
        self.assertNotIn("live or", finished_line)


if __name__ == "__main__":
    unittest.main()
