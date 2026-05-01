"""
FastAPI backend for the Yoyaku scraper.

Endpoints:
  POST /scrape            {styles, concurrency} → {job_id}
  GET  /stream/{job_id}   SSE log stream, terminated by a "done" event
  GET  /results/{job_id}  Release[] JSON once the job is complete

Run:
  pip install fastapi uvicorn
  uvicorn api:app --reload
"""

import asyncio
import json
import uuid
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from yoyaku_scraper import run_scraper

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# In-memory job store.
# {job_id: {"queue": asyncio.Queue, "results": list | None, "error": str | None}}
_jobs: dict[str, dict] = {}


class ScrapeRequest(BaseModel):
    styles: list[str]
    concurrency: int = 10


@app.post("/scrape")
async def start_scrape(body: ScrapeRequest):
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"queue": asyncio.Queue(), "results": None, "error": None}
    asyncio.create_task(_run_job(job_id, body.styles, body.concurrency))
    return {"job_id": job_id}


async def _run_job(job_id: str, styles: list[str], concurrency: int) -> None:
    queue: asyncio.Queue = _jobs[job_id]["queue"]

    def log_fn(text: str, msg_type: str = "") -> None:
        queue.put_nowait({"type": msg_type, "text": text})

    try:
        releases = await run_scraper(styles, concurrency=concurrency, log_fn=log_fn)
        _jobs[job_id]["results"] = [asdict(r) for r in releases]
    except Exception as exc:
        log_fn(f"[error] {exc}", "err")
        _jobs[job_id]["error"] = str(exc)
    finally:
        queue.put_nowait(None)  # sentinel — event_gen stops on None


@app.get("/stream/{job_id}")
async def stream_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")

    async def event_gen():
        queue: asyncio.Queue = _jobs[job_id]["queue"]
        while True:
            item = await queue.get()
            if item is None:
                yield "event: done\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/results/{job_id}")
async def get_results(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    job = _jobs[job_id]
    if job["error"]:
        raise HTTPException(500, job["error"])
    if job["results"] is None:
        raise HTTPException(202, "Job still running")
    return job["results"]
