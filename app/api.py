import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .agents import JOB_QUERY, Profile, Strict, Verify, classify, eligible, prepare, verify
from .auth import user
from .autopilot import AutoPolicy, configure as configure_autopilot, status as autopilot_status
from .db import audit, database, migrate, now
from .files import store_resume
from .notifications import enqueue

_worker_lock = Lock()
_worker_state = {"status": "idle", "started": None, "finished": None, "error": None}


def _worker_cycle():
    try:
        from .worker import main as run_pipeline

        run_pipeline()
    except Exception as exc:
        logging.exception("Job Hunter discovery failed")
        _worker_state.update(status="failed", error=type(exc).__name__)
    else:
        _worker_state.update(status="completed", error=None)
    finally:
        _worker_state["finished"] = now()
        _worker_lock.release()


def _launch_worker():
    if not _worker_lock.acquire(blocking=False):
        return False
    _worker_state.update(status="running", started=now(), finished=None, error=None)
    Thread(target=_worker_cycle, daemon=True, name="jobhunter-discovery").start()
    return True


def _scheduled_worker(stop: Event):
    """Single-process scheduler; only one discovery cycle may run at a time."""
    if stop.wait(15):
        return
    while not stop.is_set():
        _launch_worker()
        if stop.wait(30 * 60):
            break


@asynccontextmanager
async def lifespan(app):
    migrate()
    stop = Event()
    if os.getenv("ENABLE_SCHEDULER") == "true":
        Thread(target=_scheduled_worker, args=(stop,), daemon=True, name="jobhunter-scheduler").start()
    try:
        yield
    finally:
        stop.set()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def secure_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; connect-src 'self' https://login.microsoftonline.com; frame-src 'self' https://login.microsoftonline.com; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    )
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    with database() as db:
        db.one("SELECT 1")
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {
        "production": os.getenv("APP_ENV") == "production",
        "tenant": os.getenv("ENTRA_TENANT"),
        "client": os.getenv("ENTRA_CLIENT_ID"),
        "scope": os.getenv("ENTRA_SCOPE"),
    }


@app.get("/api/session")
def session(actor=Depends(user)):
    return {"actor": actor}

@app.get("/api/discovery/status")
def discovery_status(actor=Depends(user)):
    return dict(_worker_state)


@app.post("/api/discovery/run", status_code=202)
def discovery_run(actor=Depends(user)):
    if not _launch_worker():
        raise HTTPException(409, "A discovery cycle is already running")
    return dict(_worker_state)




@app.get("/api/autopilot")
def get_autopilot(actor=Depends(user)):
    return autopilot_status()


@app.put("/api/autopilot")
def update_autopilot(data: AutoPolicy, actor=Depends(user)):
    try:
        configure_autopilot(data, actor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return autopilot_status()


@app.post("/api/autopilot/run", status_code=202)
def run_autopilot(actor=Depends(user)):
    if not _launch_worker():
        raise HTTPException(409, "A discovery and application cycle is already running")
    return dict(_worker_state)


@app.get("/api/jobs")
def jobs(actor=Depends(user)):
    with database() as db:
        rows = db.all(JOB_QUERY + " ORDER BY v.discovered DESC LIMIT 1000")
    for item in rows:
        item["eligible"] = eligible(item)
        if item["status"] in (None, "unknown") and item["sponsor_register"]:
            item["status"] = "licensed_only"
        item["evidence_candidates"] = classify(item["description"])
        item["detail"] = json.loads(item["detail"] or "{}")
    return rows


@app.get("/api/profile")
def profile(actor=Depends(user)):
    with database() as db:
        row = db.one("SELECT * FROM candidate WHERE id=1")
    return (
        {"profile": json.loads(row["profile"]), "resume_stored": bool(row["resume_key"])}
        if row
        else {"profile": None, "resume_stored": False}
    )


@app.put("/api/profile")
def update_profile(data: Profile, actor=Depends(user)):
    with database(write=True) as db:
        previous = db.one("SELECT * FROM candidate WHERE id=1")
        if data.source_verified and (not previous or not previous["resume_key"]):
            raise HTTPException(409, "Upload the original résumé before verifying your profile")
        value = data.model_dump_json()
        if previous and previous["profile"] == value:
            return {"saved": True}
        db.execute(
            "INSERT INTO candidate(id,profile) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET profile=excluded.profile",
            (value,),
        )
        db.execute("DELETE FROM matching")
        db.execute("UPDATE draft SET state='stale' WHERE state IN ('review','approved')")
        audit(db, actor, "profile_updated", "1")
    return {"saved": True}


@app.post("/api/resume")
def resume(file: UploadFile, actor=Depends(user)):
    data = file.file.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "Maximum résumé size is 8 MB")
    with database(write=True) as db:
        row = db.one("SELECT * FROM candidate WHERE id=1")
        if row is None:
            raise HTTPException(409, "Save your candidate profile first")
        if row["resume_key"]:
            raise HTTPException(409, "A résumé is already stored. Replacement requires an administrator.")
        try:
            key = store_resume(data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.execute("UPDATE candidate SET resume_key=? WHERE id=1", (key,))
        audit(db, actor, "resume_uploaded", "1")
    return {"stored": True}


def get_job(db, job_id):
    job = db.one(JOB_QUERY + " WHERE v.id=?", (job_id,))
    if not job:
        raise HTTPException(404, "Vacancy not found")
    return job


@app.put("/api/jobs/{job_id}/sponsorship")
def review_sponsorship(job_id: str, data: Verify, actor=Depends(user)):
    with database(write=True) as db:
        job = get_job(db, job_id)
        try:
            verify(db, job, data, actor)
            reviewed = get_job(db, job_id)
            candidate = db.one("SELECT profile FROM candidate WHERE id=1")
            if candidate and eligible(reviewed):
                profile = Profile.model_validate_json(candidate["profile"])
                if profile.source_verified:
                    prepare(db, reviewed, profile, actor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {"saved": True}


@app.post("/api/jobs/{job_id}/prepare")
def prepare_job(job_id: str, actor=Depends(user)):
    with database(write=True) as db:
        job = get_job(db, job_id)
        candidate = db.one("SELECT profile FROM candidate WHERE id=1")
        if not candidate:
            raise HTTPException(409, "Candidate profile required")
        try:
            return prepare(db, job, Profile.model_validate_json(candidate["profile"]), actor)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc


@app.put("/api/employers/{name}/exclude")
def exclude(name: str, actor=Depends(user)):
    with database(write=True) as db:
        if not db.one("SELECT name FROM employer WHERE name=?", (name,)):
            raise HTTPException(404, "Employer not found")
        db.execute("UPDATE employer SET excluded=1 WHERE name=?", (name,))
        db.execute(
            """UPDATE draft SET state='stale' WHERE state IN ('review','approved')
            AND vacancy_id IN (SELECT id FROM vacancy WHERE employer=?)""",
            (name,),
        )
        audit(db, actor, "employer_excluded", name)
    return {"excluded": True}


@app.get("/api/drafts")
def drafts(actor=Depends(user)):
    with database() as db:
        rows = db.all("""SELECT d.*,v.title,v.employer,v.country,v.url,s.status,m.score
            FROM draft d JOIN vacancy v ON v.id=d.vacancy_id
            LEFT JOIN sponsorship s ON s.vacancy_id=v.id LEFT JOIN matching m ON m.vacancy_id=v.id
            ORDER BY v.discovered DESC""")
    for row in rows:
        row["answers"] = json.loads(row["answers"])
        row["unresolved"] = json.loads(row["unresolved"])
    return rows


class Answers(Strict):
    revision: int
    answers: dict[str, str | int] = Field(max_length=30)


@app.put("/api/drafts/{job_id}/answers")
def edit_answers(job_id: str, data: Answers, actor=Depends(user)):
    value = json.dumps(data.answers)
    if len(value) > 6000:
        raise HTTPException(422, "Answers exceed the supported size")
    with database(write=True) as db:
        result = db.execute(
            """UPDATE draft SET answers=?,revision=revision+1,state='review'
            WHERE vacancy_id=? AND revision=? AND state IN ('review','approved')""",
            (value, job_id, data.revision),
        )
        if result.rowcount != 1:
            raise HTTPException(409, "Refresh the application before editing")
        audit(db, actor, "answers_edited", job_id)
    return {"saved": True}


class ReviewItem(Strict):
    vacancy_id: str
    revision: int


class Decision(Strict):
    items: list[ReviewItem] = Field(min_length=1, max_length=50)
    decision: Literal["approved", "rejected"]


@app.post("/api/decisions")
def decide(data: Decision, actor=Depends(user)):
    if len({i.vacancy_id for i in data.items}) != len(data.items):
        raise HTTPException(422, "Duplicate selection")
    with database(write=True) as db:
        for item in data.items:
            job = get_job(db, item.vacancy_id)
            current = db.one("SELECT revision,state FROM draft WHERE vacancy_id=?", (item.vacancy_id,))
            if not current or current != {"revision": item.revision, "state": "review"}:
                raise HTTPException(409, "The selection changed. Refresh and review again.")
            if data.decision == "approved" and not eligible(job):
                raise HTTPException(409, "A selected vacancy is no longer eligible")
            db.execute(
                "INSERT INTO approval VALUES (?,?,?,?,?)",
                (item.vacancy_id, item.revision, actor, data.decision, now()),
            )
            db.execute("UPDATE draft SET state=? WHERE vacancy_id=?", (data.decision, item.vacancy_id))
            audit(db, actor, f"draft_{data.decision}", item.vacancy_id)
    return {"changed": len(data.items), "decision": data.decision}


class Confirmation(Strict):
    reference: str = Field(min_length=5, max_length=300)


@app.post("/api/submissions/{job_id}")
def record_submission(job_id: str, data: Confirmation, actor=Depends(user)):
    ref = data.reference.strip()
    if len(ref) < 5:
        raise HTTPException(422, "Enter an employer confirmation reference")
    with database(write=True) as db:
        job = get_job(db, job_id)
        if job["draft_state"] != "approved":
            raise HTTPException(409, "Approval is required before recording submission")
        db.execute("INSERT INTO submission VALUES (?,?,?,?)", (job_id, "candidate_reported", ref, now()))
        db.execute("UPDATE draft SET state='submitted' WHERE vacancy_id=?", (job_id,))
        audit(db, actor, "manual_confirmation_recorded", job_id)
        enqueue(db, f"submission:{job_id}", "A candidate-reported submission was recorded in Job Hunter.")
    return {"recorded": True, "verification": "candidate_reported"}


class Status(Strict):
    status: Literal["recruiter_response", "interview", "rejected", "follow_up", "offer"]
    description: str = Field(min_length=1, max_length=2000)
    event_time: str = Field(min_length=10, max_length=36)


@app.post("/api/tracking/{job_id}")
def track(job_id: str, data: Status, actor=Depends(user)):
    with database(write=True) as db:
        if not db.one("SELECT vacancy_id FROM submission WHERE vacancy_id=?", (job_id,)):
            raise HTTPException(404, "Submission not found")
        db.execute(
            "INSERT INTO tracking VALUES (?,?,?,?,?)",
            (str(uuid4()), job_id, data.status, data.description, data.event_time),
        )
        audit(db, actor, data.status, job_id)
        if data.status in {"interview", "recruiter_response"}:
            enqueue(
                db,
                f"{data.status}:{job_id}:{data.event_time}",
                "An application update was recorded in Job Hunter.",
            )
    return {"saved": True}


@app.get("/api/tracking")
def tracking(actor=Depends(user)):
    with database() as db:
        return {
            "submissions": db.all("""SELECT s.*,v.title,v.employer FROM submission s
                 JOIN vacancy v ON v.id=s.vacancy_id ORDER BY recorded_at DESC"""),
            "events": db.all("SELECT * FROM tracking ORDER BY event_time DESC"),
        }


@app.get("/api/usage")
def usage(actor=Depends(user)):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    with database() as db:
        counts = db.one("SELECT * FROM model_usage WHERE month=?", (month,))
    return {
        "month": month,
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        **(counts or {}),
        "request_limit": max(0, int(os.getenv("MAX_MONTHLY_MODEL_REQUESTS", "1000"))),
    }


@app.get("/api/audit")
def audit_log(actor=Depends(user)):
    with database() as db:
        return db.all("SELECT * FROM audit ORDER BY time DESC LIMIT 300")


static = Path(__file__).with_name("static")
if static.is_dir():
    app.mount("/", StaticFiles(directory=static, html=True), name="dashboard")
