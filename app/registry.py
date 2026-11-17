import csv
import io
import os
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from .db import audit, database, now

UK = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
NL = "https://ind.nl/en/public-register-recognised-sponsors/public-register-work"


def normalize(name):
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


class RegisterHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        elif tag == "tr":
            self.row = []
        elif tag == "td" and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def read_limited(client, url, max_bytes):
    with client.stream("GET", url, follow_redirects=False) as response:
        response.raise_for_status()
        result = bytearray()
        for chunk in response.iter_bytes():
            result.extend(chunk)
            if len(result) > max_bytes:
                raise ValueError("Registry exceeds its expected size")
    return result.decode("utf-8-sig")


def lookup(client, country, names):
    page_url, route = (UK, "Skilled Worker") if country == "GB" else (NL, "Highly Skilled Migrant")
    parser = RegisterHTML()
    parser.feed(read_limited(client, page_url, 25 * 1024 * 1024))
    result = {}
    if country == "GB":
        csv_url = next(
            url
            for url in parser.links
            if urlsplit(url).scheme == "https"
            and urlsplit(url).netloc == "assets.publishing.service.gov.uk"
            and re.search(r"register.*\.csv$", urlsplit(url).path, re.I)
        )
        records = csv.DictReader(io.StringIO(read_limited(client, csv_url, 30 * 1024 * 1024)))
        if not records.fieldnames or not {"Organisation Name", "Route"}.issubset(records.fieldnames):
            raise ValueError("Unrecognised UK registry schema")
        for row in records:
            legal_name = row["Organisation Name"]
            if legal_name and normalize(legal_name) in names and row["Route"] == route:
                result[normalize(legal_name)] = (legal_name, csv_url, route, row.get("Type & Rating", ""))
    else:
        register_rows = [
            (name, identifier)
            for row in parser.rows
            if len(row) >= 2
            for name, identifier in [row[:2]]
            if re.fullmatch(r"\d{8}", identifier.replace(" ", ""))
        ]
        if not register_rows:
            raise ValueError("Unrecognised Netherlands registry schema")
        for legal_name, identifier in register_rows:
            if normalize(legal_name) in names:
                result[normalize(legal_name)] = (legal_name, page_url, route, f"KVK {identifier}")
    return result


def refresh_registries():
    if os.getenv("VERIFY_PUBLIC_REGISTRIES") != "yes":
        return
    for country in ("GB", "NL"):
        key = f"registry:{country}"
        with database() as db:
            previous = db.one("SELECT value FROM checkpoint WHERE key=?", (key,))
            employers = db.all(
                "SELECT DISTINCT employer FROM vacancy WHERE country=? AND active=1", (country,)
            )
        if not employers or (previous and previous["value"] > datetime.now(timezone.utc).date().isoformat()):
            continue
        entities = {normalize(row["employer"]) for row in employers}
        try:
            with httpx.Client(timeout=35, trust_env=False) as client:
                matches = lookup(client, country, entities)
        except (httpx.HTTPError, StopIteration, ValueError, KeyError, TypeError) as exc:
            with database(write=True) as db:
                audit(db, "sponsorship_registry", "registry_failed", f"{country}/{type(exc).__name__}")
            continue
        with database(write=True) as db:
            db.execute("DELETE FROM employer_sponsor WHERE country=?", (country,))
            for row in employers:
                legal_entity = matches.get(normalize(row["employer"]))
                if legal_entity:
                    db.execute(
                        "INSERT INTO employer_sponsor VALUES (?,?,?,?,?,?,?)",
                        (row["employer"], country, *legal_entity, now()),
                    )
            db.execute(
                "INSERT INTO checkpoint VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, now()),
            )
            audit(db, "sponsorship_registry", "registry_refreshed", country)
