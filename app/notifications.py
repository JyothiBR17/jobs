import os

from .db import now


def enqueue(db, event, message):
    for channel, environment in (("telegram", "TELEGRAM_BOT_TOKEN"), ("email", "SMTP_HOST")):
        if os.getenv(environment) or channel in os.getenv("NOTIFICATION_CHANNELS", "").split(","):
            db.execute(
                "INSERT INTO outbox(id,message,channel,available) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                (f"{event}:{channel}", message, channel, now()),
            )
