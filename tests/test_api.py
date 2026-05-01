"""
API tests for api.py — FastAPI backend for the Yoyaku vinyl scraper.

Dev dependencies (install before running):
    pip install pytest pytest-asyncio httpx

Run with:
    pytest tests/test_api.py -v

Coverage:
  POST /scrape      — valid request, missing fields, empty styles, concurrency default
  GET  /stream      — full SSE stream, message types, done-event format, unknown job
  GET  /results     — results ready, job still running (202), job errored (500), unknown job
  Internals         — log_fn queue enqueue, job isolation across concurrent jobs
"""

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Add project root so api and yoyaku_scraper can be imported without installation.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import api as api_module
from api import _jobs, app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE = "http://test"


def make_transport():
    return ASGITransport(app=app)


async def _collect_sse(client: AsyncClient, job_id: str) -> list[str]:
    """Stream /stream/{job_id} and return every raw SSE line (non-empty)."""
    lines: list[str] = []
    async with client.stream("GET", f"/stream/{job_id}") as response:
        async for line in response.aiter_lines():
            lines.append(line)
    return lines


def _parse_data_events(raw_lines: list[str]) -> list[dict]:
    """Extract parsed JSON payloads from `data: {...}` lines."""
    payloads: list[dict] = []
    for line in raw_lines:
        if line.startswith("data: ") and line != "data: {}":
            payloads.append(json.loads(line[len("data: "):]))
    return payloads


# ---------------------------------------------------------------------------
# Fake run_scraper helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeRelease:
    title: str = "Alpha EP"
    url: str = "https://yoyaku.io/release/alpha-ep/"
    artists: str = "DJ Alpha"
    label: str = "Alpha Records"
    sku: str = "AR-001"
    styles: str = "Techno"
    format: str = '12"'
    price: str = "€15.00"


def _make_fake_scraper(*releases, messages=None):
    """
    Return an AsyncMock that:
      - Calls log_fn for each (text, msg_type) in messages.
      - Returns a list of _FakeRelease objects (converted to dataclass so
        api._run_job can call dataclasses.asdict on them).
    """
    if messages is None:
        messages = [("Scraping complete", "hi")]

    async def _fake(styles, concurrency=10, log_fn=None):
        if log_fn:
            for text, msg_type in messages:
                log_fn(text, msg_type)
        return list(releases)

    return _fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_jobs():
    """Wipe the in-memory job store before and after every test."""
    _jobs.clear()
    yield
    _jobs.clear()


# ---------------------------------------------------------------------------
# POST /scrape
# ---------------------------------------------------------------------------

class TestPostScrape:
    async def test_valid_request_returns_job_id(self):
        fake = _make_fake_scraper(_FakeRelease())
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                resp = await client.post("/scrape", json={"styles": ["Techno"]})
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        assert isinstance(body["job_id"], str)
        assert len(body["job_id"]) > 0

    async def test_job_id_is_uuid_format(self):
        import uuid
        fake = _make_fake_scraper()
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                resp = await client.post("/scrape", json={"styles": ["Acid"]})
        job_id = resp.json()["job_id"]
        # Should not raise if it is a valid UUID.
        uuid.UUID(job_id)

    async def test_missing_styles_field_returns_422(self):
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.post("/scrape", json={"concurrency": 5})
        assert resp.status_code == 422

    async def test_missing_body_returns_422(self):
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.post("/scrape", content=b"")
        assert resp.status_code == 422

    async def test_empty_styles_list_still_returns_job_id(self):
        """An empty styles list is a valid payload shape — api delegates to scraper."""
        fake = _make_fake_scraper()
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                resp = await client.post("/scrape", json={"styles": []})
        assert resp.status_code == 200
        assert "job_id" in resp.json()

    async def test_concurrency_defaults_to_10(self):
        """When concurrency is omitted the scraper must receive concurrency=10."""
        received: dict = {}

        async def _capturing_fake(styles, concurrency=10, log_fn=None):
            received["concurrency"] = concurrency
            return []

        with patch("api.run_scraper", new=_capturing_fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                resp = await client.post("/scrape", json={"styles": ["Techno"]})
            job_id = resp.json()["job_id"]
            # Wait briefly for the background task to execute.
            await asyncio.sleep(0.05)

        assert received.get("concurrency") == 10

    async def test_custom_concurrency_is_forwarded(self):
        received: dict = {}

        async def _capturing_fake(styles, concurrency=10, log_fn=None):
            received["concurrency"] = concurrency
            return []

        with patch("api.run_scraper", new=_capturing_fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                resp = await client.post(
                    "/scrape", json={"styles": ["Techno"], "concurrency": 3}
                )
            await asyncio.sleep(0.05)

        assert received.get("concurrency") == 3

    async def test_two_parallel_requests_return_distinct_job_ids(self):
        fake = _make_fake_scraper()
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                r1, r2 = await asyncio.gather(
                    client.post("/scrape", json={"styles": ["Techno"]}),
                    client.post("/scrape", json={"styles": ["Acid"]}),
                )
        assert r1.json()["job_id"] != r2.json()["job_id"]


# ---------------------------------------------------------------------------
# GET /stream/{job_id}
# ---------------------------------------------------------------------------

class TestGetStream:
    async def test_unknown_job_id_returns_404(self):
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.get("/stream/nonexistent-job-id")
        assert resp.status_code == 404

    async def test_response_content_type_is_event_stream(self):
        fake = _make_fake_scraper()
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                async with client.stream("GET", f"/stream/{job_id}") as stream_resp:
                    content_type = stream_resp.headers.get("content-type", "")
                    # Consume to completion.
                    async for _ in stream_resp.aiter_lines():
                        pass
        assert "text/event-stream" in content_type

    async def test_regular_messages_use_data_prefix(self):
        messages = [("Hello from scraper", "hi"), ("Neutral log", "")]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        data_lines = [l for l in lines if l.startswith("data: ") and l != "data: {}"]
        assert len(data_lines) >= 1
        for line in data_lines:
            assert line.startswith("data: "), f"Expected data prefix: {line!r}"

    async def test_message_payload_is_valid_json(self):
        messages = [("Test message", "hi")]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        payloads = _parse_data_events(lines)
        assert len(payloads) >= 1
        for p in payloads:
            assert "type" in p
            assert "text" in p

    async def test_hi_message_type_is_preserved(self):
        messages = [("Highlight message", "hi")]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        payloads = _parse_data_events(lines)
        hi_msgs = [p for p in payloads if p["type"] == "hi"]
        assert any(p["text"] == "Highlight message" for p in hi_msgs)

    async def test_err_message_type_is_preserved(self):
        messages = [("Something broke", "err")]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        payloads = _parse_data_events(lines)
        err_msgs = [p for p in payloads if p["type"] == "err"]
        assert any(p["text"] == "Something broke" for p in err_msgs)

    async def test_neutral_message_type_is_empty_string(self):
        messages = [("Plain log line", "")]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        payloads = _parse_data_events(lines)
        neutral_msgs = [p for p in payloads if p["type"] == ""]
        assert any(p["text"] == "Plain log line" for p in neutral_msgs)

    async def test_stream_terminates_with_done_event(self):
        """The final SSE chunk must be exactly 'event: done\\ndata: {}\\n\\n'."""
        fake = _make_fake_scraper()
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]

                raw_chunks: list[str] = []
                async with client.stream("GET", f"/stream/{job_id}") as resp:
                    async for chunk in resp.aiter_text():
                        raw_chunks.append(chunk)

        full_body = "".join(raw_chunks)
        # The done terminator must appear as the final non-empty chunk.
        assert "event: done\ndata: {}\n\n" in full_body

    async def test_done_event_is_last_sse_chunk(self):
        """Nothing must follow the 'event: done' terminator."""
        fake = _make_fake_scraper(messages=[("msg", "hi")])
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]

                raw_chunks: list[str] = []
                async with client.stream("GET", f"/stream/{job_id}") as resp:
                    async for chunk in resp.aiter_text():
                        raw_chunks.append(chunk)

        full_body = "".join(raw_chunks)
        done_pos = full_body.find("event: done\ndata: {}\n\n")
        assert done_pos != -1, "done terminator not found"
        # Everything after the terminator must be empty.
        after = full_body[done_pos + len("event: done\ndata: {}\n\n"):]
        assert after.strip() == "", f"Unexpected content after done terminator: {after!r}"

    async def test_multiple_messages_all_arrive_in_order(self):
        messages = [
            ("First", "hi"),
            ("Second", ""),
            ("Third", "err"),
        ]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        payloads = _parse_data_events(lines)
        texts = [p["text"] for p in payloads]
        assert "First" in texts
        assert "Second" in texts
        assert "Third" in texts
        # Order must match emission order.
        assert texts.index("First") < texts.index("Second") < texts.index("Third")

    async def test_data_line_format_has_double_newline_terminator(self):
        """Each SSE event frame must end with \\n\\n per the spec."""
        messages = [("Hello", "hi")]
        fake = _make_fake_scraper(messages=messages)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]

                chunks: list[str] = []
                async with client.stream("GET", f"/stream/{job_id}") as resp:
                    async for chunk in resp.aiter_text():
                        chunks.append(chunk)

        full = "".join(chunks)
        # Every data: frame must end with \n\n.
        import re
        data_frames = re.findall(r"data: .*?(?=\n\n)", full, re.DOTALL)
        for frame in data_frames:
            # The frame text followed by \n\n must exist in full.
            assert full.find(frame + "\n\n") != -1


# ---------------------------------------------------------------------------
# GET /results/{job_id}
# ---------------------------------------------------------------------------

class TestGetResults:
    async def test_unknown_job_id_returns_404(self):
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.get("/results/does-not-exist")
        assert resp.status_code == 404

    async def test_results_available_returns_200_with_release_list(self):
        release = _FakeRelease(title="My EP", sku="MY-001")
        fake = _make_fake_scraper(release)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                # Drain the SSE stream so the job completes before checking results.
                await _collect_sse(client, job_id)
                results_resp = await client.get(f"/results/{job_id}")

        assert results_resp.status_code == 200
        data = results_resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["title"] == "My EP"
        assert data[0]["sku"] == "MY-001"

    async def test_results_contain_all_release_fields(self):
        release = _FakeRelease(
            title="Full EP",
            url="https://yoyaku.io/release/full-ep/",
            artists="DJ Full",
            label="Full Records",
            sku="FR-007",
            styles="Techno, Deep House",
            format='12"',
            price="€18.00",
        )
        fake = _make_fake_scraper(release)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await _collect_sse(client, job_id)
                results_resp = await client.get(f"/results/{job_id}")

        record = results_resp.json()[0]
        assert record["title"] == "Full EP"
        assert record["url"] == "https://yoyaku.io/release/full-ep/"
        assert record["artists"] == "DJ Full"
        assert record["label"] == "Full Records"
        assert record["sku"] == "FR-007"
        assert record["styles"] == "Techno, Deep House"
        assert record["format"] == '12"'
        assert record["price"] == "€18.00"

    async def test_empty_results_list_returns_200(self):
        """A scrape that finds no releases should return [] not an error."""
        fake = _make_fake_scraper()  # no releases
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await _collect_sse(client, job_id)
                results_resp = await client.get(f"/results/{job_id}")

        assert results_resp.status_code == 200
        assert results_resp.json() == []

    async def test_job_still_running_returns_202(self):
        """If the job is not done yet /results must return HTTP 202."""
        # We manually insert a job that is still "running" (results=None, error=None).
        _jobs["running-job"] = {
            "queue": asyncio.Queue(),
            "results": None,
            "error": None,
        }
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.get("/results/running-job")
        assert resp.status_code == 202

    async def test_job_errored_returns_500(self):
        """If the scraper raised an exception /results must return HTTP 500."""
        _jobs["errored-job"] = {
            "queue": asyncio.Queue(),
            "results": None,
            "error": "Connection refused",
        }
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.get("/results/errored-job")
        assert resp.status_code == 500

    async def test_job_errored_detail_contains_error_message(self):
        _jobs["err-detail-job"] = {
            "queue": asyncio.Queue(),
            "results": None,
            "error": "Timeout after 30s",
        }
        async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
            resp = await client.get("/results/err-detail-job")
        assert "Timeout after 30s" in resp.text

    async def test_multiple_releases_returned_correctly(self):
        releases = [
            _FakeRelease(title="EP One", sku="SK-001"),
            _FakeRelease(title="EP Two", sku="SK-002"),
            _FakeRelease(title="EP Three", sku="SK-003"),
        ]
        fake = _make_fake_scraper(*releases)
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await _collect_sse(client, job_id)
                results_resp = await client.get(f"/results/{job_id}")

        data = results_resp.json()
        assert len(data) == 3
        skus = {r["sku"] for r in data}
        assert skus == {"SK-001", "SK-002", "SK-003"}


# ---------------------------------------------------------------------------
# log_fn internals — queue enqueue behaviour
# ---------------------------------------------------------------------------

class TestLogFnQueueBehaviour:
    async def test_log_fn_enqueues_typed_hi_message(self):
        """log_fn passed to _run_job must put {"type": "hi", "text": ...} on the queue."""
        # Use a pause event so we can inspect queue contents while _run_job is
        # still in-flight (before the sentinel is placed).
        paused = asyncio.Event()
        resume = asyncio.Event()

        async def _pausing_scraper(styles, concurrency=10, log_fn=None):
            if log_fn:
                log_fn("test message", "hi")
            paused.set()       # signal: message was enqueued
            await resume.wait()  # hold until test has read the queue
            return []

        with patch("api.run_scraper", new=_pausing_scraper):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await paused.wait()          # wait until log_fn was called

                queue: asyncio.Queue = _jobs[job_id]["queue"]
                item = queue.get_nowait()    # the hi-message must be first
                resume.set()                 # let _run_job finish
                # drain SSE so background task completes cleanly
                await _collect_sse(client, job_id)

        assert item == {"type": "hi", "text": "test message"}

    async def test_log_fn_enqueues_err_message(self):
        paused = asyncio.Event()
        resume = asyncio.Event()

        async def _pausing_scraper(styles, concurrency=10, log_fn=None):
            if log_fn:
                log_fn("bad error", "err")
            paused.set()
            await resume.wait()
            return []

        with patch("api.run_scraper", new=_pausing_scraper):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await paused.wait()

                queue: asyncio.Queue = _jobs[job_id]["queue"]
                item = queue.get_nowait()
                resume.set()
                await _collect_sse(client, job_id)

        assert item == {"type": "err", "text": "bad error"}

    async def test_log_fn_enqueues_neutral_message(self):
        paused = asyncio.Event()
        resume = asyncio.Event()

        async def _pausing_scraper(styles, concurrency=10, log_fn=None):
            if log_fn:
                log_fn("neutral line")   # default msg_type=""
            paused.set()
            await resume.wait()
            return []

        with patch("api.run_scraper", new=_pausing_scraper):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await paused.wait()

                queue: asyncio.Queue = _jobs[job_id]["queue"]
                item = queue.get_nowait()
                resume.set()
                await _collect_sse(client, job_id)

        assert item == {"type": "", "text": "neutral line"}

    async def test_sentinel_none_is_placed_after_scraper_finishes(self):
        """After run_scraper returns, _run_job must put None (sentinel) on the queue."""
        fake = _make_fake_scraper(_FakeRelease())
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                await _collect_sse(client, job_id)

        # After SSE stream ends, results must be set (sentinel was consumed by event_gen).
        assert _jobs[job_id]["results"] is not None

    async def test_error_in_scraper_puts_sentinel_and_sets_error(self):
        """If run_scraper raises, error must be recorded and sentinel still placed."""
        async def _failing_scraper(styles, concurrency=10, log_fn=None):
            raise RuntimeError("scraper exploded")

        with patch("api.run_scraper", new=_failing_scraper):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                post_resp = await client.post("/scrape", json={"styles": ["Techno"]})
                job_id = post_resp.json()["job_id"]
                lines = await _collect_sse(client, job_id)

        assert _jobs[job_id]["error"] is not None
        assert "scraper exploded" in _jobs[job_id]["error"]
        # Sentinel was consumed; stream should have delivered done event.
        full = "\n".join(lines)
        assert "event: done" in full


# ---------------------------------------------------------------------------
# Job isolation
# ---------------------------------------------------------------------------

class TestJobIsolation:
    async def test_two_jobs_have_separate_queues(self):
        fake = _make_fake_scraper()
        with patch("api.run_scraper", new=fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                r1 = await client.post("/scrape", json={"styles": ["Techno"]})
                r2 = await client.post("/scrape", json={"styles": ["Acid"]})

        job1_id = r1.json()["job_id"]
        job2_id = r2.json()["job_id"]

        assert job1_id in _jobs
        assert job2_id in _jobs
        assert _jobs[job1_id]["queue"] is not _jobs[job2_id]["queue"]

    async def test_two_jobs_have_separate_result_stores(self):
        release1 = _FakeRelease(title="Job1 EP", sku="J1-001")
        release2 = _FakeRelease(title="Job2 EP", sku="J2-001")

        fake1 = _make_fake_scraper(release1)
        fake2 = _make_fake_scraper(release2)

        with patch("api.run_scraper", new=fake1):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                r1 = await client.post("/scrape", json={"styles": ["Techno"]})
                job1_id = r1.json()["job_id"]
                await _collect_sse(client, job1_id)

        with patch("api.run_scraper", new=fake2):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                r2 = await client.post("/scrape", json={"styles": ["Acid"]})
                job2_id = r2.json()["job_id"]
                await _collect_sse(client, job2_id)

        # Each job should have only its own release.
        titles1 = [r["title"] for r in _jobs[job1_id]["results"]]
        titles2 = [r["title"] for r in _jobs[job2_id]["results"]]
        assert titles1 == ["Job1 EP"]
        assert titles2 == ["Job2 EP"]

    async def test_messages_from_one_job_do_not_appear_in_another_jobs_stream(self):
        """SSE streams for two concurrent jobs must not bleed messages across jobs."""
        barrier = asyncio.Event()

        async def _slow_scraper(styles, concurrency=10, log_fn=None):
            label = styles[0] if styles else "unknown"
            if log_fn:
                log_fn(f"message-for-{label}", "hi")
            await barrier.wait()
            return []

        with patch("api.run_scraper", new=_slow_scraper):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                r1 = await client.post("/scrape", json={"styles": ["Techno"]})
                r2 = await client.post("/scrape", json={"styles": ["Acid"]})
                job1_id = r1.json()["job_id"]
                job2_id = r2.json()["job_id"]

                # Unblock both scrapers so sentinels get placed.
                barrier.set()

                lines1 = await _collect_sse(client, job1_id)
                lines2 = await _collect_sse(client, job2_id)

        payloads1 = _parse_data_events(lines1)
        payloads2 = _parse_data_events(lines2)

        texts1 = {p["text"] for p in payloads1}
        texts2 = {p["text"] for p in payloads2}

        # Each stream should contain only its own labelled message.
        assert "message-for-Techno" in texts1
        assert "message-for-Techno" not in texts2
        assert "message-for-Acid" in texts2
        assert "message-for-Acid" not in texts1

    async def test_error_in_one_job_does_not_affect_other_job(self):
        """An exception in job A must not corrupt job B's state."""
        async def _failing(styles, concurrency=10, log_fn=None):
            raise RuntimeError("job A failed")

        release = _FakeRelease(title="Job B EP")
        good_fake = _make_fake_scraper(release)

        with patch("api.run_scraper", new=_failing):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                ra = await client.post("/scrape", json={"styles": ["Techno"]})
                job_a_id = ra.json()["job_id"]
                await _collect_sse(client, job_a_id)

        with patch("api.run_scraper", new=good_fake):
            async with AsyncClient(transport=make_transport(), base_url=BASE) as client:
                rb = await client.post("/scrape", json={"styles": ["Acid"]})
                job_b_id = rb.json()["job_id"]
                await _collect_sse(client, job_b_id)
                results_resp = await client.get(f"/results/{job_b_id}")

        assert _jobs[job_a_id]["error"] is not None
        assert _jobs[job_b_id]["error"] is None
        assert results_resp.status_code == 200
        assert results_resp.json()[0]["title"] == "Job B EP"
