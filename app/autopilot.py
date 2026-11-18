"""Auditable, opt-in automated international application orchestrator.

Provider submission is allowed only with explicit candidate authorization AND
employer-authorized API access. No browser anti-bot bypasses, fabricated answers,
or implicit successful submission status.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from .agents import JOB_QUERY, Profile, Strict, Verify, eligible, prepare, verify
from .db import audit, checkpoint, database, now
from .files import load_resume
from .notifications import enqueue


class AutoPolicy(Strict):
    enabled: bool = False
    consent: bool = False
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=45)
    min_score: int = Field(default=75, ge=70, le=100)
    max_daily: int = Field(default=5, ge=1, le=20)
    sources: list[str] = Field(default_factory=list, max_length=50)


def policy_from(db):
    row = db.one("SELECT value FROM checkpoint WHERE key='autopilot:policy'")
    return AutoPolicy.model_validate_json(row["value"]) if row else AutoPolicy()


def check_email(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def configure(data, actor):
    if len(set(data.sources)) != len(data.sources):
        raise ValueError("Duplicate source")
    for source in data.sources:
        if not re.fullmatch(r"(?:lever|lever-eu|ashby):[A-Za-z0-9_-]{1,90}", source):
            raise ValueError("Only named, supported provider boards can be authorized")
    if data.enabled:
        if not data.consent:
            raise ValueError("Explicit authorization is required for automatic external applications")
        if not check_email(data.email):
            raise ValueError("A genuine applicant email is required")
        with database() as db:
            candidate = db.one("SELECT * FROM candidate WHERE id=1")
        if not candidate or not candidate["resume_key"] or not Profile.model_validate_json(
            candidate["profile"]
        ).source_verified:
            raise ValueError("Upload your actual PDF and verify the corresponding profile first")
        if not data.sources:
            raise ValueError("Select at least one authorized job board")
    with database(write=True) as db:
        checkpoint(db, "autopilot:policy", data.model_dump())
        audit(db, actor, "autopilot_enabled" if data.enabled else "autopilot_paused", "settings")
    return {"enabled": data.enabled}


def safe_public_source(job, policy):
    if job["source"] not in policy.sources:
        return False
    if job["country"].upper() in {"IN", "IND"}:
        return False
    if re.search(r"\b(india|chennai|bengaluru|bangalore|hyderabad|mumbai|pune|delhi)\b", job["location"], re.I):
        return False
    if job["source"].startswith("mock:"):
        return False
    provider, slug = job["source"].split(":", 1)
    return provider in {"lever", "lever-eu", "ashby"} and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,90}", slug))


def job_sponsorship_evidence(job):
    """Only literal, vacancy-level evidence can approve sponsorship for auto-apply.

    A general sponsor register match or an LLM inference is not sufficient.
    'From India' is about applicant eligibility, not job location.
    """
    if job.get("status"):
        return None
    description = job["description"]
    sentences = re.split(r"(?<=[.!?])\s+", description)
    positive = re.compile(r"\b(?:visa sponsorship (?:is )?(?:available|provided|offered)|sponsor(?:ing|ship)? (?:work )?visas?|work visa sponsorship)\b", re.I)
    negative = re.compile(r"\b(?:no|not|without|cannot|don't|doesn't|unable to)\b.{0,40}\b(?:visa|sponsor)", re.I)
    global_eligibility = re.compile(r"\b(?:applicants? from (?:india|any country|all countries|anywhere in the world)|candidates? from (?:india|all countries|any country|worldwide)|international applicants? (?:are )?welcome|open to applicants? worldwide)\b", re.I)
    for sentence in sentences:
        if positive.search(sentence) and not negative.search(sentence) and global_eligibility.search(description):
            return sentence.strip()[:3000]
    return None


def board_capabilities(source):
    """Keys can only be supplied from a separately configured secret store.

    Map: {"lever:board":{"key":"...","standard_form_confirmed":true}}.
    Only the named board with employer-granted permission may be submitted.
    """
    try:
        records = json.loads(os.getenv("LEVER_APPLY_KEYS_JSON", "{}"))
    except json.JSONDecodeError:
        return None
    entry = records.get(source) if isinstance(records, dict) else None
    if not isinstance(entry, dict) or not entry.get("standard_form_confirmed") or not isinstance(
        entry.get("key"), str
    ) or len(entry["key"]) < 12:
        return None
    return entry


def apply_lever(job, draft, policy, resume, client=None):
    provider, board = job["source"].split(":", 1)
    if provider not in {"lever", "lever-eu"}:
        raise ValueError("Provider does not have an authorized application adapter")
    settings = board_capabilities(job["source"])
    if settings is None:
        raise ValueError("Employer-authorized Lever API key and standard-form confirmation required")
    posting_id = job["source_id"]
    if not re.fullmatch(r"[A-Za-z0-9-]{1,90}", posting_id):
        raise ValueError("Unexpected posting identifier")
    if len(resume) > 8 * 1024 * 1024 or not resume.startswith(b"%PDF-"):
        raise ValueError("A valid original PDF is required")
    host = "api.eu.lever.co" if provider == "lever-eu" else "api.lever.co"
    url = f"https://{host}/v0/postings/{board}/{posting_id}"
    fields = {"name": json.loads(draft["answers"]).get("name", ""),
              "email": policy.email, "comments": draft["letter"]}
    if policy.phone:
        fields["phone"] = policy.phone
    if not fields["name"] or not check_email(fields["email"]):
        raise ValueError("Candidate name and real email are required")
    owns = client is None
    client = client or httpx.Client(timeout=35, follow_redirects=False)
    try:
        # Never log the API key or the actual applicant payload.
        response = client.post(url, params={"key": settings["key"]},
                               data=fields, files={"resume": ("resume.pdf", resume, "application/pdf")})
        if response.status_code == 429:
            return "rate_limited", "Provider returned 429; safe to retry later", None
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns:
            client.close()
    if payload.get("ok") is True and isinstance(payload.get("applicationId"), str) and payload["applicationId"]:
        return "confirmed", "Employer API confirmed receipt", payload["applicationId"]
    return "needs_review", "Provider did not return a confirmed application ID", None


def _mark(db, job, state, detail, reference=None):
    moment = now()
    db.execute(
        """INSERT INTO autopilot_attempt(vacancy_id,source,state,detail,reference,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(vacancy_id) DO UPDATE SET
           state=excluded.state,detail=excluded.detail,reference=excluded.reference,
           updated_at=excluded.updated_at""",
        (job["id"], job["source"], state, detail[:500], reference, moment, moment),
    )
    audit(db, "autopilot", "application_" + state, job["id"])


def run_cycle(limit=15):
    """Run one bounded scan in the same single-instance scheduled process.

    An in-flight/unknown result is never retried automatically: avoids duplicates
    when a remote API accepted a request but the connection timed out.
    """
    with database() as db:
        policy = policy_from(db)
        candidate = db.one("SELECT * FROM candidate WHERE id=1")
    if not policy.enabled or not policy.consent or not candidate or not candidate["resume_key"]:
        return {"state": "disabled_or_unready", "processed": 0}
    profile = Profile.model_validate_json(candidate["profile"])
    if not profile.source_verified or not check_email(policy.email):
        return {"state": "unready", "processed": 0}
    with database(write=True) as db:
        jobs = db.all(
            JOB_QUERY + """ WHERE v.active=1 AND v.country!='IN' AND m.score IS NOT NULL
                AND s.status IS NULL ORDER BY m.score DESC LIMIT 100"""
        )
        for job in jobs:
            if safe_public_source(job, policy):
                evidence = job_sponsorship_evidence(job)
                if evidence:
                    verify(db, job, Verify(status="confirmed", evidence=evidence, source=job["url"],
                        india_considered=True), "autopilot-evidence")
    processed = 0
    start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with database() as db:
        candidates = db.all(
            JOB_QUERY + """ WHERE v.active=1 AND v.country!='IN' AND m.score>=?
                AND d.state IN ('review','approved') AND s.status='confirmed'
                AND NOT EXISTS(SELECT 1 FROM submission z WHERE z.vacancy_id=v.id)
                ORDER BY m.score DESC LIMIT 100""", (policy.min_score,)
        )
    for job in candidates:
        if processed >= limit:
            break
        if not safe_public_source(job, policy) or not eligible(job):
            continue
        with database(write=True) as db:
            attempts = db.one("SELECT * FROM autopilot_attempt WHERE vacancy_id=?", (job["id"],))
            if attempts and attempts["state"] not in {"rate_limited"}:
                continue
            submitted = db.one(
                "SELECT COUNT(*) AS n FROM submission WHERE channel='lever_api' AND recorded_at>=?", (start,)
            )["n"]
            if submitted >= policy.max_daily:
                break
            draft = db.one("SELECT * FROM draft WHERE vacancy_id=?", (job["id"],))
            current = db.one(JOB_QUERY + " WHERE v.id=?", (job["id"],))
            if not draft or not current or not eligible(current) or draft["state"] not in {"review","approved"}:
                continue
            if not current["source"].startswith(("lever:", "lever-eu:")):
                _mark(db, job, "needs_review", "No verified employer-authorized submission adapter")
                continue
            if board_capabilities(current["source"]) is None:
                _mark(db, job, "needs_review", "Missing employer-issued Lever API authorization")
                continue
            # Reserve before opening a network connection. This is a durable duplicate guard.
            _mark(db, job, "in_flight", "Attempt reserved before contacting the employer API")
        processed += 1
        try:
            resume = load_resume(candidate["resume_key"])
            state, explanation, ref = apply_lever(current, draft, policy, resume)
        except (httpx.TimeoutException, httpx.TransportError):
            state, explanation, ref = "unknown", "Network outcome uncertain; do not retry automatically", None
        except (httpx.HTTPStatusError, ValueError, KeyError, json.JSONDecodeError):
            state, explanation, ref = "needs_review", "Provider rejected or could not validate submission", None
        with database(write=True) as db:
            _mark(db, current, state, explanation, ref)
            if state == "confirmed":
                db.execute("INSERT INTO submission VALUES (?,?,?,?)",
                    (current["id"], "lever_api", ref, now()))
                db.execute("UPDATE draft SET state='submitted' WHERE vacancy_id=?", (current["id"],))
                enqueue(db, "autopilot-submitted:" + current["id"],
                        "An international job application was confirmed by the employer API.")
    with database(write=True) as db:
        checkpoint(db, "autopilot:last_cycle",
                   {"time": now(), "attempts": processed, "state": "completed"})
    return {"state": "completed", "processed": processed}


def status():
    with database() as db:
        policy = policy_from(db)
        candidate = db.one("SELECT * FROM candidate WHERE id=1")
        attempts = db.all(
            """SELECT a.*, v.title,v.employer,v.country,v.url
               FROM autopilot_attempt a JOIN vacancy v ON v.id=a.vacancy_id
               ORDER BY a.updated_at DESC LIMIT 75"""
        )
        counts = db.all("SELECT state,COUNT(*) AS n FROM autopilot_attempt GROUP BY state")
        last = db.one("SELECT value FROM checkpoint WHERE key='autopilot:last_cycle'")
    has_resume = bool(candidate and candidate["resume_key"])
    verified = bool(candidate and Profile.model_validate_json(candidate["profile"]).source_verified)
    return {
        "policy": policy.model_dump(),
        "ready": has_resume and verified and check_email(policy.email) and policy.consent,
        "original_resume_uploaded": has_resume,
        "profile_verified": verified,
        "openai_enabled": bool(os.environ.get("OPENAI_API_KEY")),
        "model": os.environ.get("OPENAI_MODEL", "gpt-5-mini") if os.environ.get("OPENAI_API_KEY") else None,
        "provider_api_ready": [source for source in policy.sources if board_capabilities(source)],
        "counts": {r["state"]: r["n"] for r in counts},
        "attempts": attempts,
        "last_cycle": json.loads(last["value"]) if last else None,
        "scope": "All explicitly classified non-India locations in configured boards",
    }
