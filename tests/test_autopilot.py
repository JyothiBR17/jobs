"""Autopilot policy and real-confirmation boundary tests (no employer calls)."""
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from app.agents import Verify, verify
from app.api import app
from app.autopilot import (
    AutoPolicy,
    apply_lever,
    job_sponsorship_evidence,
    run_cycle,
    safe_public_source,
)
from app.db import database
from app.discovery import ingest

TOKEN = "autopilot-test-access-token-32characters"
PROFILE = {
    "name": "Example Candidate",
    "summary": "Azure security engineer",
    "facts": [{"text": "Implemented Azure Policy and RBAC",
               "category": "security", "employer": "Sample Co"}],
    "notice_days": 30,
    "source_verified": False,
}
POSTING = {
    "source": "lever:sampleco",
    "source_id": "123e4567-e89b-12d3-a456-426614174000",
    "url": "https://jobs.lever.co/sampleco/123e4567-e89b-12d3-a456-426614174000",
    "employer": "Sample Co",
    "title": "Senior Azure Security Engineer",
    "location": "London, United Kingdom",
    "description": "Azure Policy and RBAC. Visa sponsorship is available. Applicants from India are welcome.",
}


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = patch.dict(os.environ, {
            "DATABASE_URL": "", "LOCAL_DB": folder.name + "/case.db",
            "PRIVATE_DIR": folder.name + "/private",
            "BLOB_ACCOUNT_URL": "", "OPENAI_API_KEY": "",
            "LEVER_APPLY_KEYS_JSON": "",
            "APP_ENV": "development",
            "LOCAL_ACCESS_TOKEN": TOKEN,
            "ENABLE_SCHEDULER": "false",
        })
        settings.start()
        self.addCleanup(settings.stop)
        client = TestClient(app, headers={"Authorization": "Bearer " + TOKEN})
        client.__enter__()
        self.client = client
        self.addCleanup(client.__exit__, None, None, None)
        with database(write=True) as db:
            self.job_id = ingest(db, POSTING)

    def provision(self):
        self.assertEqual(200, self.client.put("/api/profile", json=PROFILE).status_code)
        self.assertEqual(200, self.client.post(
            "/api/resume", files={"file": ("cv.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")}
        ).status_code)
        self.assertEqual(200, self.client.put(
            "/api/profile", json=dict(PROFILE, source_verified=True)
        ).status_code)
        with database(write=True) as db:
            job = db.one("SELECT * FROM vacancy WHERE id=?", (self.job_id,))
            from app.agents import JOB_QUERY
            job = db.one(JOB_QUERY + " WHERE v.id=?", (self.job_id,))
            verify(db, job, Verify(status="confirmed",
                evidence="Visa sponsorship is available.", source=job["url"],
                india_considered=True), "test")
            db.execute("INSERT INTO matching VALUES (?,?,?)", (
                self.job_id, 90, json.dumps({"seniority":"senior", "sponsorship_points": 15})
            ))
        self.assertEqual(200, self.client.post(
            f"/api/jobs/{self.job_id}/prepare"
        ).status_code)

    def policy(self, enabled=True):
        return {
            "enabled": enabled, "consent": True, "email": "candidate@example.com",
            "phone": "", "min_score": 75, "max_daily": 2,
            "sources": ["lever:sampleco"],
        }

    def test_disabled_by_default_and_requires_verified_profile(self):
        current = self.client.get("/api/autopilot")
        self.assertEqual(200, current.status_code)
        self.assertFalse(current.json()["policy"]["enabled"])
        attempt = self.client.put("/api/autopilot", json=self.policy())
        self.assertEqual(422, attempt.status_code)

    def test_disabled_policy_does_not_send(self):
        self.provision()
        self.assertEqual(0, run_cycle()["processed"])
        with database() as db:
            self.assertEqual([], db.all("SELECT * FROM submission"))

    def test_missing_employer_key_never_claims_submission(self):
        self.provision()
        response = self.client.put("/api/autopilot", json=self.policy())
        self.assertEqual(200, response.status_code, response.text)
        run_cycle()
        with database() as db:
            attempt = db.one("SELECT * FROM autopilot_attempt WHERE vacancy_id=?", (self.job_id,))
            self.assertEqual("needs_review", attempt["state"])
            self.assertIsNone(db.one("SELECT * FROM submission WHERE vacancy_id=?", (self.job_id,)))

    def test_real_confirmation_records_exactly_once(self):
        self.provision()
        self.assertEqual(200, self.client.put("/api/autopilot", json=self.policy()).status_code)
        with patch.dict(os.environ, {
            "LEVER_APPLY_KEYS_JSON": json.dumps({
                "lever:sampleco": {"key": "employer-issued-test-key",
                                   "standard_form_confirmed": True}
            })
        }):
            with patch("app.autopilot.apply_lever", return_value=(
                "confirmed", "Employer API confirmed receipt", "application-id-from-employer"
            )) as call:
                run_cycle()
                run_cycle()
                self.assertEqual(1, call.call_count)
        with database() as db:
            submission = db.one("SELECT * FROM submission WHERE vacancy_id=?", (self.job_id,))
            self.assertEqual("lever_api", submission["channel"])
            self.assertEqual("application-id-from-employer", submission["reference"])

    def test_timeout_is_uncertain_not_retried(self):
        self.provision()
        self.client.put("/api/autopilot", json=self.policy())
        with patch.dict(os.environ, {"LEVER_APPLY_KEYS_JSON": json.dumps({
            "lever:sampleco": {"key": "employer-issued-test-key",
                               "standard_form_confirmed": True}
        })}):
            with patch("app.autopilot.apply_lever", side_effect=httpx.ReadTimeout("timeout")) as apply:
                run_cycle()
                run_cycle()
                self.assertEqual(1, apply.call_count)
        with database() as db:
            attempt = db.one("SELECT * FROM autopilot_attempt WHERE vacancy_id=?", (self.job_id,))
            self.assertEqual("unknown", attempt["state"])
            self.assertIsNone(db.one("SELECT * FROM submission WHERE vacancy_id=?", (self.job_id,)))

    def test_india_jobs_always_excluded(self):
        job = dict(POSTING, country="IN", source="lever:sampleco")
        self.assertFalse(safe_public_source(job, AutoPolicy(**self.policy())))
        job["country"] = "GB"
        job["location"] = "Remote, Bangalore India"
        self.assertFalse(safe_public_source(job, AutoPolicy(**self.policy())))

    def test_literal_sponsorship_no_inference(self):
        posting = dict(POSTING, status=None)
        self.assertIsNotNone(job_sponsorship_evidence(posting))
        posting["description"] = "No visa sponsorship is available. Applicants from India welcome."
        self.assertIsNone(job_sponsorship_evidence(posting))
        posting["description"] = "Employer is on UK sponsor register."
        self.assertIsNone(job_sponsorship_evidence(posting))

    def test_lever_requires_employer_key_and_confirmation(self):
        fake = Mock()
        fake.post.return_value = httpx.Response(200, json={
            "ok": True, "applicationId": "live-employer-id"
        })
        draft = {"answers": json.dumps({"name":"Example Candidate"}),
                 "letter": "I am applying to your role."}
        with patch.dict(os.environ, {"LEVER_APPLY_KEYS_JSON": json.dumps({
            "lever:sampleco": {"key": "employer-issued-test-key", "standard_form_confirmed": True}
        })}):
            result = apply_lever(POSTING, draft, AutoPolicy(**self.policy()),
                                 b"%PDF-1.4\n%%EOF", client=fake)
        self.assertEqual("confirmed", result[0])
        self.assertEqual("live-employer-id", result[2])
        self.assertEqual("resume", next(iter(fake.post.call_args.kwargs["files"])))


if __name__ == "__main__":
    unittest.main()
