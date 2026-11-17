import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx

from .agents import process
from .db import audit, checkpoint, database, migrate, now
from .discovery import boards, fetch_board, ingest


def discover():
    for board in boards(os.getenv("JOB_BOARDS_JSON")):
        source = f"{board['provider']}:{board['slug']}"
        try:
            rows = fetch_board(board)
            with database(write=True) as db:
                seen = {job_id for row in rows if (job_id := ingest(db, row))}
                for job in db.all("SELECT id FROM vacancy WHERE source=? AND active=1", (source,)):
                    if job["id"] not in seen:
                        db.execute("UPDATE vacancy SET active=0 WHERE id=?", (job["id"],))
                        db.execute(
                            "UPDATE draft SET state='stale' WHERE vacancy_id=? AND state IN ('approved','review')",
                            (job["id"],),
                        )
                checkpoint(db, source, {"last_success": now(), "rows": len(rows)})
                audit(db, "discovery", "board_processed", source)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            with database(write=True) as db:
                audit(db, "discovery", "board_failed", source + "/" + type(exc).__name__)


def enqueue_report():
    date = datetime.now(timezone.utc).date().isoformat()
    with database(write=True) as db:
        review = db.one("SELECT COUNT(*) AS n FROM draft WHERE state='review'")["n"]
        submitted = db.one("SELECT COUNT(*) AS n FROM submission")["n"]
        message = f"AI International Job Hunter report. Awaiting approval: {review}. Submissions recorded: {submitted}."
        for channel, key in (("telegram", "TELEGRAM_BOT_TOKEN"), ("email", "SMTP_HOST")):
            if os.getenv(key):
                db.execute(
                    "INSERT INTO outbox(id,message,channel,available) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                    (f"daily:{date}:{channel}", message, channel, now()),
                )


def deliver(item):
    if item["channel"] == "telegram":
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": item["message"]},
            timeout=15,
        )
        response.raise_for_status()
        if response.json().get("ok") is not True:
            raise ValueError("Telegram did not confirm delivery")
    else:
        message = EmailMessage()
        message["From"] = os.environ["SMTP_FROM"]
        message["To"] = os.environ["SMTP_TO"]
        message["Subject"] = "AI International Job Hunter"
        message.set_content(item["message"])
        with smtplib.SMTP_SSL(
            os.environ["SMTP_HOST"], int(os.getenv("SMTP_PORT", "465")), timeout=15
        ) as smtp:
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            smtp.send_message(message)


def drain():
    with database(write=True) as db:
        rows = db.all(
            "SELECT * FROM outbox WHERE state='pending' AND available<=? ORDER BY available LIMIT 3", (now(),)
        )
        for row in rows:
            try:
                deliver(row)
            except (OSError, httpx.HTTPError, ValueError, KeyError):
                attempts = row["attempts"] + 1
                available = (datetime.now(timezone.utc) + timedelta(minutes=2**attempts)).isoformat()
                db.execute(
                    "UPDATE outbox SET state=?,attempts=?,available=? WHERE id=?",
                    ("dead" if attempts >= 5 else "pending", attempts, available, row["id"]),
                )
            else:
                db.execute("UPDATE outbox SET state='sent' WHERE id=?", (row["id"],))


def main():
    migrate()
    discover()
    if os.getenv("OPENAI_API_KEY"):
        with database() as db:
            jobs = db.all("""SELECT v.id FROM vacancy v LEFT JOIN matching m ON m.vacancy_id=v.id
                LEFT JOIN checkpoint c ON c.key=v.id
                WHERE v.active=1 AND m.vacancy_id IS NULL ORDER BY CASE WHEN c.value IS NULL THEN 0 ELSE 1 END,c.value,v.discovered LIMIT 10""")
        for job in jobs:
            try:
                process(job["id"])
            except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
                with database(write=True) as db:
                    audit(db, "matching", "analysis_failed", job["id"] + "/" + type(exc).__name__)
            finally:
                with database(write=True) as db:
                    checkpoint(db, job["id"], now())
    enqueue_report()
    drain()


if __name__ == "__main__":
    main()
