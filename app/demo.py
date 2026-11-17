"""Run only against a disposable local database. Seeds synthetic records for UI review."""

import json
import os

from .db import database, migrate
from .discovery import ingest


def main():
    if os.environ.get("APP_ENV") == "production" or os.environ.get("DATABASE_URL"):
        raise SystemExit("The demo requires a disposable local database")
    migrate()
    rows = [
        (
            "Senior Azure Cloud Architect",
            "Northwind Cloud Labs",
            "London, United Kingdom",
            "Design Azure Landing Zones and review Terraform modules. We offer visa sponsorship for this vacancy.",
        ),
        (
            "Cloud Security Architect",
            "Harbour Platform Labs",
            "Dublin, Ireland",
            "Build cloud security standards with Azure Policy and Defender for Cloud.",
        ),
        (
            "Lead Azure Platform Engineer",
            "Alpine Platform Labs",
            "Germany",
            "Implement Terraform and Azure governance in an enterprise environment.",
        ),
    ]
    with database(write=True) as db:
        for i, (title, employer, location, description) in enumerate(rows):
            job = ingest(
                db,
                {
                    "source": "mock:demo",
                    "source_id": str(i),
                    "url": f"https://example.test/{i}",
                    "employer": employer,
                    "title": title,
                    "location": location,
                    "description": description,
                },
            )
            detail = {
                "seniority": "senior",
                "preferred_profile": "architecture",
                "architecture": {
                    "requirement": "Design Azure Landing Zones" if i == 0 else None,
                    "evidence": "Designed Azure Landing Zones in synthetic projects" if i == 0 else None,
                    "awarded": 25 if i == 0 else 0,
                    "weight": 25,
                },
            }
            db.execute(
                "INSERT INTO matching VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (job, 78 - i * 9, json.dumps(detail)),
            )


if __name__ == "__main__":
    main()
