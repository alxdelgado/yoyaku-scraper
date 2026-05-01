# High-Severity Fixes — api.py

Sourced from the code review of the FastAPI backend. Five issues, ordered by implementation dependency (later tasks may build on earlier ones).

---

## Task 1 — Store `create_task` reference to prevent GC

**Severity:** High
**File:** `api.py`
**Lines:** 49

### Problem

```python
asyncio.create_task(_run_job(job_id, body.styles, body.concurrency))
```

The task returned by `create_task` is discarded immediately. Python's asyncio documentation explicitly warns that tasks not held by a strong reference may be garbage-collected before they complete. CPython's GC won't collect a running task in practice today, but it is an undefined-behaviour hazard — and more critically, without a stored reference there is no way to cancel the task later (required by Task 3 and Task 4).

### Fix

Store the task inside the job dict at creation time:

```python
task = asyncio.create_task(_run_job(job_id, body.styles, body.concurrency))
_jobs[job_id]["task"] = task
```

Update the job dict schema in `start_scrape` to include the `"task"` key:

```python
_jobs[job_id] = {
    "queue":   asyncio.Queue(),
    "results": None,
    "error":   None,
    "task":    None,   # filled immediately below
}
task = asyncio.create_task(_run_job(job_id, body.styles, body.concurrency))
_jobs[job_id]["task"] = task
```

### Acceptance criteria

- `_jobs[job_id]["task"]` is an `asyncio.Task` instance immediately after `POST /scrape` returns.
- The task is the same object later referenced by cleanup (Task 3) and disconnect cancellation (Task 4).
- All existing tests pass.

---

## Task 2 — Replace `HTTPException(202)` with a proper `JSONResponse`

**Severity:** High
**File:** `api.py`
**Lines:** 97–98

### Problem

```python
raise HTTPException(202, "Job still running")
```

`HTTPException` is the FastAPI mechanism for error responses. HTTP 202 is a success status (Accepted / in-progress) and must never be raised as an exception. Using it here produces a `{"detail": "Job still running"}` body with status 202, which is misleading to clients that inspect the response shape and may break client libraries that treat `HTTPException`-shaped bodies as errors.

Additionally the condition logic is currently:

```python
if job["results"] is None:
    if job["error"]:
        raise HTTPException(500, job["error"])
    raise HTTPException(202, "Job still running")
```

The two-level nesting makes the control flow hard to follow and would silently return 202 even if `job["error"]` is a falsy non-`None` value (e.g. an empty string).

### Fix

Flatten the condition and return a proper response for the in-progress case:

```python
from fastapi.responses import JSONResponse

@app.get("/results/{job_id}")
async def get_results(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    job = _jobs[job_id]
    if job["error"] is not None:
        raise HTTPException(500, job["error"])
    if job["results"] is None:
        return JSONResponse(status_code=202, content={"status": "running"})
    return job["results"]
```

Note: check `error is not None` explicitly so an empty string error (edge case) still surfaces as a 500.

### Acceptance criteria

- `GET /results/{id}` returns HTTP 202 with body `{"status": "running"}` when the job is still running.
- `GET /results/{id}` returns HTTP 500 when `job["error"]` is any non-`None` value.
- `GET /results/{id}` returns HTTP 200 with the release list when `job["results"]` is set.
- The existing `test_job_still_running_returns_202` test passes against the new response body shape (update the test assertion if it checks the body).
- No `HTTPException` is raised for HTTP 202.

---

## Task 3 — Add TTL-based cleanup for `_jobs`

**Severity:** High
**File:** `api.py`
**Lines:** 37, 48

### Problem

`_jobs` is a module-level dict that grows without bound. Every `POST /scrape` call adds an entry. Each entry holds:

- An `asyncio.Queue` with every log line emitted during the scrape.
- The full `Release[]` result list.
- A reference to the background `asyncio.Task`.

A scrape of many styles can produce hundreds of queue items and dozens of release dicts. Under sustained use (or a basic scraping loop calling the API repeatedly) the process will exhaust available memory with no indication to the operator.

### Fix

Add a `"created_at"` timestamp to every job at creation and run a background cleanup coroutine on startup that evicts stale jobs:

```python
import time

# In start_scrape:
_jobs[job_id] = {
    "queue":      asyncio.Queue(),
    "results":    None,
    "error":      None,
    "task":       None,
    "created_at": time.monotonic(),
}
```

```python
JOB_TTL_SECONDS = 3600  # 1 hour

@app.on_event("startup")
async def _start_cleanup_loop() -> None:
    asyncio.create_task(_cleanup_jobs())

async def _cleanup_jobs() -> None:
    while True:
        await asyncio.sleep(300)  # run every 5 minutes
        cutoff = time.monotonic() - JOB_TTL_SECONDS
        stale = [
            jid for jid, job in _jobs.items()
            if job.get("created_at", 0) < cutoff
        ]
        for jid in stale:
            task = _jobs[jid].get("task")
            if task and not task.done():
                task.cancel()
            del _jobs[jid]
```

`JOB_TTL_SECONDS` should be a named constant so it can be adjusted without hunting through the code.

### Acceptance criteria

- Every job dict contains a `"created_at"` key set to `time.monotonic()` at creation.
- Jobs older than `JOB_TTL_SECONDS` are removed from `_jobs` within one cleanup interval after expiry.
- Running tasks are cancelled before their job dict is deleted.
- A test verifies that a job past its TTL is evicted (inject a stale `created_at` value and call `_cleanup_jobs()` directly).

---

## Task 4 — Cancel `_run_job` task on SSE client disconnect

**Severity:** High
**File:** `api.py`
**Lines:** 74–87

### Problem

When the SSE client disconnects (browser tab closed, network drop, `EventSource` explicitly closed), FastAPI's `StreamingResponse` stops iterating the generator, but `_run_job` continues running as an independent `asyncio.Task`. The scraper makes all its HTTP requests, parses all cards, and stores results in `_jobs` — for a client that is no longer listening. Under sustained use with many disconnecting clients this creates a backlog of orphaned scrape tasks consuming network, CPU, and memory.

Additionally the `asyncio.Queue` for the disconnected job will fill indefinitely (though it is unbounded, so it won't block), and results will persist in `_jobs` until the TTL cleanup runs (Task 3).

### Fix

Catch `GeneratorExit` inside `event_gen` and cancel the associated task. Requires Task 1 (task reference stored in job dict).

```python
@app.get("/stream/{job_id}")
async def stream_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")

    async def event_gen():
        queue: asyncio.Queue = _jobs[job_id]["queue"]
        try:
            while True:
                item = await queue.get()
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except GeneratorExit:
            task = _jobs[job_id].get("task")
            if task and not task.done():
                task.cancel()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### Acceptance criteria

- Closing the SSE connection mid-stream results in `_run_job` task being cancelled within one event-loop tick.
- `task.cancelled()` returns `True` after disconnect.
- Normal (full) stream completion does not trigger cancellation.
- A test simulates disconnect by breaking out of the generator early and asserts the task is cancelled.

---

## Task 5 — Prevent concurrent consumers from racing on the same job queue

**Severity:** High
**File:** `api.py`
**Lines:** 74–87

### Problem

`asyncio.Queue` is a single-consumer data structure. If two clients call `GET /stream/{job_id}` for the same job simultaneously (browser retry, duplicate tab, curl in parallel), they race on `queue.get()`:

- Messages are split non-deterministically between the two consumers.
- The `None` sentinel is consumed by whichever client wins the final `queue.get()` — the other client's `event_gen` then blocks on `queue.get()` indefinitely with no way to terminate.
- The stream for the losing client hangs until the server or client times out.

This is a silent correctness failure: the hanging client receives no error, no done event, and no indication that it is receiving a partial stream.

### Fix

Track whether a stream consumer is already active for a job. Return HTTP 409 if a second consumer attempts to connect.

Add a `"streaming"` flag to the job dict:

```python
_jobs[job_id] = {
    "queue":     asyncio.Queue(),
    "results":   None,
    "error":     None,
    "task":      None,
    "created_at": time.monotonic(),
    "streaming": False,
}
```

Guard `stream_job` with a check-and-set:

```python
@app.get("/stream/{job_id}")
async def stream_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    if _jobs[job_id]["streaming"]:
        raise HTTPException(409, "Stream already active for this job")
    _jobs[job_id]["streaming"] = True

    async def event_gen():
        queue: asyncio.Queue = _jobs[job_id]["queue"]
        try:
            while True:
                item = await queue.get()
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        except GeneratorExit:
            task = _jobs[job_id].get("task")
            if task and not task.done():
                task.cancel()
        finally:
            _jobs[job_id]["streaming"] = False

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

The `finally` block resets the flag so a reconnect is possible after the stream ends normally or the client disconnects.

### Acceptance criteria

- A second `GET /stream/{job_id}` while the first is active returns HTTP 409.
- After the first stream ends (normally or by disconnect), a new `GET /stream/{job_id}` succeeds.
- The `"streaming"` flag is `False` in the job dict after any stream termination path (done event, disconnect, exception).
- A test verifies the 409 response and the flag reset.

---

## Implementation order

Tasks have the following dependencies:

```
Task 1 (store task ref)
    └── Task 4 (cancel on disconnect)   — needs _jobs[id]["task"]
    └── Task 3 (TTL cleanup)            — needs _jobs[id]["task"] to cancel on evict

Task 2 (202 response)                  — independent

Task 5 (single-consumer guard)
    └── Task 4 (disconnect handling)   — share the same event_gen / finally block
```

Recommended order: **1 → 2 → 3 → 4 → 5**, implementing and testing each before moving to the next.
