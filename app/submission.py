"""Sandbox submission workflow. Public discovery adapters have no submission capability."""

import json
import os
from datetime import datetime, timedelta, timezone

from .agents import JOB_QUERY, eligible
from .db import audit, database, now


def sandbox_submit(job_id, sandbox):
    if os.getenv("APP_ENV") == "production" or os.getenv("ENABLE_SANDBOX_SUBMISSIONS") != "yes":
        raise ValueError("Sandbox submissions are disabled")
    with database(write=True) as db:
        policy_row = db.one("SELECT value FROM checkpoint WHERE key='sandbox_policy'")
        policy = json.loads(policy_row["value"]) if policy_row else {}
        job = db.one(JOB_QUERY + " WHERE v.id=?", (job_id,))
        draft = db.one("SELECT * FROM draft WHERE vacancy_id=?", (job_id,))
        if not (
            policy.get("enabled") is True
            and job
            and draft
            and eligible(job)
            and job["source"].startswith("mock:")
            and job["url"].startswith("https://example.test/")
            and job["status"] == "confirmed"
            and job["india_considered"] == 1
            and job["employer"] in policy.get("employers", [])
            and job["title"] in policy.get("roles", [])
            and draft["state"] == "review"
            and json.loads(draft["unresolved"]) == []
        ):
            raise ValueError("Application fails sandbox submission policy")
        if set(json.loads(draft["answers"])) != {"name", "notice_days"}:
            raise ValueError("Application answers need review")
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        count = db.one(
            "SELECT COUNT(*) AS n FROM submission WHERE channel='sandbox' AND recorded_at>=?", (start,)
        )["n"]
        if count >= min(5, max(0, int(policy.get("daily_limit", 0)))):
            raise ValueError("Sandbox submission limit reached")
        confirmation = sandbox(job_id, f"sandbox:{job_id}:{draft['revision']}")
        if not isinstance(confirmation, str) or not confirmation.startswith("sandbox:"):
            raise ValueError("Sandbox confirmation missing")
        db.execute("INSERT INTO submission VALUES (?,?,?,?)", (job_id, "sandbox", confirmation, now()))
        db.execute("UPDATE draft SET state='submitted' WHERE vacancy_id=?", (job_id,))
        audit(db, "sandbox-worker", "sandbox_submission_confirmed", job_id)
        return confirmation
