"""Read-only health check for public ATS discovery boards.

Never posts applications or obtains employer API keys. Checked boards are
discovery sources ONLY, not authorized submission targets.
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "lever-eu": "https://api.eu.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
SLUG = re.compile(r"[A-Za-z0-9_-]{1,90}\Z")


def check(board):
    provider = board["provider"]
    slug = board["slug"]
    if provider not in ENDPOINTS or not SLUG.fullmatch(slug):
        return {"source": f"{provider}:{slug}", "employer": board["employer"],
                "status": "rejected", "reason": "unsupported provider or invalid slug"}
    url = ENDPOINTS[provider].format(slug=slug)
    try:
        with httpx.Client(timeout=13, follow_redirects=False) as client:
            response = client.get(url, headers={"User-Agent": "JobHunter-BoardDirectory/1.0"})
        if response.status_code != 200:
            return {"source": f"{provider}:{slug}", "employer": board["employer"],
                    "status": "rejected", "reason": f"HTTP {response.status_code}"}
        data = response.json()
        entries = data if provider.startswith("lever") else (
            data.get("jobs") if isinstance(data, dict) else None
        )
        if not isinstance(entries, list):
            raise ValueError("response did not contain the expected postings array")
        # Real ATS board public feed validated, including currently empty boards.
        return {"source": f"{provider}:{slug}", "employer": board["employer"],
                "status": "verified", "jobs_at_check": len(entries)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {"source": f"{provider}:{slug}", "employer": board["employer"],
                "status": "rejected", "reason": type(exc).__name__}


def main():
    boards = json.loads(Path("config/discovery-board-candidates.json").read_text())
    if len(boards) > 1500 or len(boards) != len(set(
        (x["provider"], x["slug"]) for x in boards
    )):
        raise SystemExit("Invalid board count or duplicate board IDs")
    results = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = {pool.submit(check, board): board for board in boards}
        for task in as_completed(tasks):
            results.append(task.result())
    results.sort(key=lambda x: x["source"])
    for result in results:
        if result["status"] == "verified":
            print("VALID", result["source"], "jobs:", result["jobs_at_check"])
        else:
            print("UNVERIFIED", result["source"], "reason:", result["reason"])
    verified = {x["source"] for x in results if x["status"] == "verified"}
    final = [board for board in boards
             if board["provider"] + ":" + board["slug"] in verified]
    Path("validated-global-boards.json").write_text(json.dumps(final, indent=2) + "\n")
    print("VALIDATED_COUNT", len(final))
    print("VALIDATED_PUBLIC_DISCOVERY_IDS", ",".join(sorted(verified)))
    print("SUBMISSION_AUTHORIZATION", "none; public feeds do not grant permission to autoapply")
    if not final:
        raise SystemExit("No live public job boards confirmed")


if __name__ == "__main__":
    main()
