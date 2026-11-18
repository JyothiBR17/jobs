import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.agents import Analysis, Profile, evaluate, evidence_only_analysis, process
from app.api import app
from app.db import checkpoint, database
from app.discovery import country_for, feed, ingest
from app.registry import lookup
from app.submission import sandbox_submit
from app.worker import drain

PROFILE = {
    "name": "Alex Example",
    "summary": "Cloud architecture and engineering",
    "notice_days": 90,
    "source_verified": False,
    "facts": [
        {"text": "Designed Azure Landing Zones", "category": "architecture", "employer": "Example"},
        {"text": "Automated deployments with Terraform", "category": "automation", "employer": "Example"},
        {"text": "Implemented Azure Policy and RBAC", "category": "security", "employer": "Example"},
        {"text": "8 years of cloud engineering", "category": "experience", "employer": "Example"},
    ],
}
POSTING = {
    "source": "mock:employer",
    "source_id": "1",
    "url": "https://example.test/1?utm_source=search",
    "employer": "Sandbox Employer",
    "title": "Senior Azure Architect",
    "location": "London, United Kingdom",
    "description": "Azure Landing Zones. Terraform. Azure Policy. 8 years. We offer visa sponsorship for this vacancy.",
}


class Workflow(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        context = patch.dict(
            os.environ,
            {
                "LOCAL_DB": self.folder.name + "/test.db",
                "PRIVATE_DIR": self.folder.name + "/files",
                "LOCAL_ACCESS_TOKEN": "synthetic-local-token-32-characters",
                "APP_ENV": "development",
                "DATABASE_URL": os.getenv("TEST_POSTGRES_URL", ""),
                "BLOB_ACCOUNT_URL": "",
            },
        )
        context.start()
        self.addCleanup(context.stop)
        self.client = TestClient(app, headers={"Authorization": "Bearer synthetic-local-token-32-characters"})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        if os.getenv("TEST_POSTGRES_URL"):
            with database(write=True) as db:
                db.execute(
                    "TRUNCATE autopilot_attempt,employer_sponsor,model_usage,tracking,submission,approval,draft,matching,sponsorship,vacancy,employer,candidate,audit,checkpoint,outbox"
                )
        with database(write=True) as db:
            self.id = ingest(db, POSTING)

    def test_discovery_button_runs_authenticated_worker(self):
        completed = Event()
        with patch("app.worker.main", side_effect=lambda: completed.set()):
            result = self.client.post("/api/discovery/run")
            self.assertEqual(202, result.status_code)
            self.assertTrue(completed.wait(5), "Discovery worker was not triggered")
        status = self.client.get("/api/discovery/status")
        self.assertEqual(200, status.status_code)
        self.assertIn(status.json()["status"], ("running", "completed"))
        unauthenticated = self.client.get("/api/discovery/status", headers={"Authorization": ""})
        self.assertEqual(401, unauthenticated.status_code)

    def test_no_key_job_match_uses_literal_facts(self):
        profile = Profile.model_validate(PROFILE)
        result = evidence_only_analysis(POSTING, profile)
        self.assertEqual(100, result.architecture.points)
        self.assertEqual(100, result.automation.points)
        self.assertEqual(100, result.security.points)
        self.assertEqual(100, result.experience.points)
        self.assertEqual(0, result.location_compensation.points)
        self.assertEqual("unknown", result.seniority)

    def test_offline_pipeline_creates_a_scored_record(self):
        self.assertEqual(200, self.client.put("/api/profile", json=PROFILE).status_code)
        self.assertEqual(200, self.client.post(
            "/api/resume", files={"file": ("original.pdf", b"%PDF-1.4\\n%%EOF", "application/pdf")}
        ).status_code)
        self.assertEqual(200, self.client.put(
            "/api/profile", json=dict(PROFILE, source_verified=True)
        ).status_code)
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            process(self.id)
        with database() as db:
            saved = db.one("SELECT score,detail FROM matching WHERE vacancy_id=?", (self.id,))
        self.assertIsNotNone(saved)
        self.assertEqual(80, saved["score"])
        self.assertEqual("literal_evidence_no_model", json.loads(saved["detail"])["analysis_mode"])

    def provision(self):
        self.assertEqual(200, self.client.put("/api/profile", json=PROFILE).status_code)
        self.assertEqual(
            200,
            self.client.post(
                "/api/resume", files={"file": ("original.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")}
            ).status_code,
        )
        self.assertEqual(
            200, self.client.put("/api/profile", json=dict(PROFILE, source_verified=True)).status_code
        )
        with database(write=True) as db:
            db.execute(
                "INSERT INTO matching VALUES (?,?,?)",
                (
                    self.id,
                    78,
                    json.dumps(
                        {
                            "preferred_profile": "security",
                            "seniority": "senior",
                            "security": {
                                "evidence": "Implemented Azure Policy and RBAC",
                                "requirement": "Azure Policy",
                                "awarded": 20,
                            },
                        }
                    ),
                ),
            )

    def verify(self, status="confirmed", evidence="We offer visa sponsorship for this vacancy."):
        return self.client.put(
            f"/api/jobs/{self.id}/sponsorship",
            json={
                "status": status,
                "evidence": evidence,
                "source": "https://example.test/1",
                "india_considered": True,
            },
        )

    def ready(self):
        self.provision()
        self.assertEqual(200, self.verify().status_code)
        result = self.client.post(f"/api/jobs/{self.id}/prepare")
        self.assertEqual(200, result.status_code, result.text)
        return result.json()

    def decision(self, revision=1, items=None):
        return self.client.post(
            "/api/decisions",
            json={
                "decision": "approved",
                "items": items
                or [
                    {"vacancy_id": self.id, "revision": revision},
                ],
            },
        )

    def test_authentication(self):
        self.assertEqual(
            401, self.client.get("/api/jobs", headers={"Authorization": "Bearer invalid"}).status_code
        )
        self.assertEqual(200, self.client.get("/health").status_code)

    def test_exclude_india_and_deduplicate(self):
        self.assertEqual("", country_for("London or Bengaluru", "GB"))
        self.assertEqual("", country_for("Remote", "IN"))
        self.assertEqual("", country_for("Worldwide"))
        self.assertEqual("CA", country_for("Remote in Canada"))
        with database(write=True) as db:
            self.assertIsNone(ingest(db, dict(POSTING, location="Chennai, India")))
            self.assertEqual(
                self.id, ingest(db, dict(POSTING, source="mock:another", url="https://example.test/1#apply"))
            )
            self.assertEqual(1, len(db.all("SELECT * FROM vacancy")))

    def test_global_catalog_keeps_technical_roles_not_general_marketing(self):
        with database(write=True) as db:
            irrelevant = dict(POSTING, source_id="marketing-global",
                              title="Marketing Director",
                              description="Our cloud platform uses Azure Security services.")
            self.assertIsNone(ingest(db, irrelevant))
            security = dict(POSTING, source_id="soc-global",
                            title="Senior SOC Analyst",
                            description="SIEM incident triage and security monitoring.",
                            url="https://example.test/global-soc")
            self.assertIsNotNone(ingest(db, security))

    def test_public_feed_parsing(self):
        def respond(request):
            self.assertEqual("boards-api.greenhouse.io", request.url.host)
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 42,
                            "title": "Azure Architect",
                            "location": {"name": "London"},
                            "absolute_url": "https://example.test/42",
                            "content": "<p>Azure Design &amp; Security</p>",
                        }
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            row = list(feed({"provider": "greenhouse", "slug": "test", "employer": "Sandbox"}, client))[0]
        self.assertEqual("Azure Design & Security", row["description"])
        self.assertEqual("42", row["source_id"])

    def test_grounded_scoring_rejects_fabricated_quotes(self):
        match = {
            name: {"requirement": requirement, "fact_index": index, "points": 100}
            for name, requirement, index in [
                ("architecture", "Azure Landing Zones", 0),
                ("automation", "Terraform", 1),
                ("security", "Secret certification", 2),
                ("experience", "8 years", 3),
                ("location_compensation", "London", -1),
            ]
        }
        score, evidence = evaluate(
            POSTING,
            Profile.model_validate(PROFILE),
            Analysis.model_validate(
                dict(
                    match,
                    seniority="senior",
                    experience_years=8,
                    salary=None,
                    work_arrangement=None,
                    deadline=None,
                    preferred_profile="architecture",
                )
            ),
        )
        self.assertEqual(60, score)
        self.assertIsNone(evidence["security"]["evidence"])

    def test_sponsorship_gate_and_evidence(self):
        self.provision()
        self.assertEqual(422, self.verify(evidence="Made-up promise").status_code)
        self.assertEqual(200, self.verify("licensed_only").status_code)
        self.assertEqual(409, self.client.post(f"/api/jobs/{self.id}/prepare").status_code)
        self.assertEqual(200, self.verify("unavailable").status_code)
        self.assertFalse(self.client.get("/api/jobs").json()[0]["eligible"])

    def test_candidate_upload_and_private_permissions(self):
        self.assertEqual(
            409, self.client.put("/api/profile", json=dict(PROFILE, source_verified=True)).status_code
        )
        self.provision()
        pdf = next(Path(self.folder.name, "files").glob("*.pdf"))
        self.assertEqual(0o600, pdf.stat().st_mode & 0o777)
        self.assertTrue(self.client.get("/api/profile").json()["resume_stored"])

    def test_complete_bulk_review_tracking_and_audit(self):
        draft = self.ready()
        self.assertIn("Implemented Azure Policy and RBAC", draft["cv"])
        self.assertIn("Sandbox Employer", draft["letter"])
        self.assertEqual(200, self.decision().status_code)
        self.assertEqual(409, self.decision().status_code)
        result = self.client.post(f"/api/submissions/{self.id}", json={"reference": "confirmation-123"})
        self.assertEqual("candidate_reported", result.json()["verification"])
        self.assertEqual(
            409,
            self.client.post(
                f"/api/submissions/{self.id}", json={"reference": "confirmation-123"}
            ).status_code,
        )
        result = self.client.post(
            f"/api/tracking/{self.id}",
            json={
                "status": "interview",
                "description": "Technical round",
                "event_time": "2026-12-01T10:00:00Z",
            },
        )
        self.assertEqual(200, result.status_code)
        self.assertEqual("interview", self.client.get("/api/tracking").json()["events"][0]["status"])
        self.assertIn("draft_approved", [row["action"] for row in self.client.get("/api/audit").json()])

    def test_bulk_failure_rolls_back(self):
        self.ready()
        result = self.decision(
            items=[{"vacancy_id": self.id, "revision": 1}, {"vacancy_id": "missing", "revision": 1}]
        )
        self.assertEqual(404, result.status_code)
        self.assertEqual("review", self.client.get("/api/drafts").json()[0]["state"])

    def test_edit_requires_fresh_approval(self):
        self.ready()
        self.assertEqual(200, self.decision().status_code)
        result = self.client.put(
            f"/api/drafts/{self.id}/answers", json={"revision": 1, "answers": {"name": "Alex Example"}}
        )
        self.assertEqual(200, result.status_code)
        self.assertEqual(409, self.decision().status_code)
        self.assertEqual(200, self.decision(2).status_code)

    def test_changed_posting_revokes_approval(self):
        self.ready()
        self.assertEqual(200, self.decision().status_code)
        with database(write=True) as db:
            ingest(db, dict(POSTING, description=POSTING["description"] + " New requirement."))
        self.assertEqual("stale", self.client.get("/api/drafts").json()[0]["state"])

    def test_sandbox_gates_and_confirmation(self):
        self.ready()
        with patch.dict(os.environ, {"ENABLE_SANDBOX_SUBMISSIONS": "yes"}):
            with self.assertRaises(ValueError):
                sandbox_submit(self.id, lambda *_: self.fail("Unapproved sender was called"))
            with database(write=True) as db:
                checkpoint(
                    db,
                    "sandbox_policy",
                    {
                        "enabled": True,
                        "daily_limit": 1,
                        "employers": ["Sandbox Employer"],
                        "roles": ["Senior Azure Architect"],
                    },
                )
            with self.assertRaises(ValueError):
                sandbox_submit(self.id, lambda *_: self.fail("Unresolved sender was called"))
            with database(write=True) as db:
                db.execute("UPDATE draft SET unresolved='[]' WHERE vacancy_id=?", (self.id,))
            with self.assertRaises(ValueError):
                sandbox_submit(self.id, lambda *_: "result missing")
            self.assertEqual("sandbox:receipt", sandbox_submit(self.id, lambda *_: "sandbox:receipt"))
            with self.assertRaises(ValueError):
                sandbox_submit(self.id, lambda *_: self.fail("Duplicate sender was called"))

    def test_uk_public_register_is_licence_only(self):
        def respond(request):
            if request.url.host == "www.gov.uk":
                return httpx.Response(
                    200,
                    text='<a href="https://assets.publishing.service.gov.uk/register-of-sponsors.csv">CSV</a>',
                )
            return httpx.Response(
                200,
                text="Organisation Name,Route,Type & Rating\nSandbox Employer,Skilled Worker,Worker (A rating)\n",
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            entity = lookup(client, "GB", {"sandbox employer"})["sandbox employer"]
        with database(write=True) as db:
            db.execute(
                "INSERT INTO employer_sponsor VALUES (?,?,?,?,?,?,?)",
                ("Sandbox Employer", "GB", *entity, "2026-11-17"),
            )
        result = self.client.get("/api/jobs").json()[0]
        self.assertEqual("licensed_only", result["status"])
        self.assertFalse(result["eligible"])

    def test_netherlands_register_row_headers(self):
        def respond(request):
            self.assertEqual("ind.nl", request.url.host)
            return httpx.Response(
                200, text='<table><tr><th scope="row">Example B.V.</th><td>01234567</td></tr></table>'
            )

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            entity = lookup(client, "NL", {"example b.v."})["example b.v."]
        self.assertEqual("KVK 01234567", entity[3])

    def test_notification_retry(self):
        with database(write=True) as db:
            db.execute(
                "INSERT INTO outbox(id,message,channel,available) VALUES ('daily','counts','email','2026-01-01')"
            )
        with patch("app.worker.deliver", side_effect=OSError):
            drain()
        with database(write=True) as db:
            self.assertEqual(1, db.one("SELECT attempts FROM outbox WHERE id='daily'")["attempts"])
            db.execute("UPDATE outbox SET available='2026-01-01'")
        with patch("app.worker.deliver") as send:
            drain()
            send.assert_called_once()
        with database() as db:
            self.assertEqual("sent", db.one("SELECT state FROM outbox WHERE id='daily'")["state"])
