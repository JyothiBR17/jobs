import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .db import audit, now


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(html):
    parser = Text()
    parser.feed(str(html)[:150000])
    return " ".join(" ".join(parser.parts).split())[:28000]


def canonical(raw):
    url = urlsplit(raw)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.port:
        raise ValueError("A public HTTPS vacancy URL is required")
    query = sorted(
        (k, v)
        for k, v in parse_qsl(url.query)
        if not k.startswith("utm_") and k not in {"source", "gh_src", "lever-source"}
    )
    return urlunsplit(("https", url.hostname, url.path.rstrip("/") or "/", urlencode(query), ""))


COUNTRIES = {
    "united kingdom": "GB",
    "london": "GB",
    "uk": "GB",
    "ireland": "IE",
    "dublin": "IE",
    "germany": "DE",
    "berlin": "DE",
    "netherlands": "NL",
    "amsterdam": "NL",
    "luxembourg": "LU",
    "switzerland": "CH",
    "belgium": "BE",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "france": "FR",
    "poland": "PL",
    "austria": "AT",
    "spain": "ES",
    "portugal": "PT",
    "uae": "AE",
    "dubai": "AE",
    "united arab emirates": "AE",
    "saudi arabia": "SA",
    "qatar": "QA",
    "bahrain": "BH",
    "oman": "OM",
    "kuwait": "KW",
    "singapore": "SG",
    "australia": "AU",
    "new zealand": "NZ",
    "japan": "JP",
    "malaysia": "MY",
    "hong kong": "HK",
    "united states": "US",
    "usa": "US",
    "new york": "US",
    "san francisco": "US",
    "seattle": "US",
    "washington dc": "US",
    "toronto": "CA",
    "vancouver": "CA",
    "sydney": "AU",
    "melbourne": "AU",
    "paris": "FR",
    "tokyo": "JP",
    "riyadh": "SA",
    "canada": "CA",
}
COUNTRIES.update({
    "italy": "IT", "rome": "IT", "milan": "IT",
    "romania": "RO", "bucharest": "RO",
    "bulgaria": "BG", "sofia": "BG",
    "czechia": "CZ", "czech republic": "CZ", "prague": "CZ",
    "slovakia": "SK", "bratislava": "SK",
    "hungary": "HU", "budapest": "HU",
    "slovenia": "SI", "ljubljana": "SI",
    "croatia": "HR", "zagreb": "HR",
    "serbia": "RS", "belgrade": "RS",
    "estonia": "EE", "tallinn": "EE",
    "latvia": "LV", "riga": "LV",
    "lithuania": "LT", "vilnius": "LT",
    "greece": "GR", "athens": "GR",
    "turkey": "TR", "istanbul": "TR",
    "israel": "IL", "tel aviv": "IL",
    "south africa": "ZA", "cape town": "ZA", "johannesburg": "ZA",
    "kenya": "KE", "nairobi": "KE",
    "nigeria": "NG", "lagos": "NG",
    "egypt": "EG", "cairo": "EG",
    "morocco": "MA", "casablanca": "MA",
    "brazil": "BR", "sao paulo": "BR", "são paulo": "BR",
    "mexico": "MX", "mexico city": "MX",
    "argentina": "AR", "buenos aires": "AR",
    "chile": "CL", "santiago": "CL",
    "colombia": "CO", "bogota": "CO", "bogotá": "CO",
    "peru": "PE", "lima": "PE",
    "costa rica": "CR",
    "south korea": "KR", "republic of korea": "KR", "seoul": "KR",
    "taiwan": "TW", "taipei": "TW",
    "thailand": "TH", "bangkok": "TH",
    "vietnam": "VN", "ho chi minh city": "VN", "hanoi": "VN",
    "indonesia": "ID", "jakarta": "ID",
    "philippines": "PH", "manila": "PH",
    "china": "CN", "shanghai": "CN", "beijing": "CN",
    "pakistan": "PK", "karachi": "PK",
    "sri lanka": "LK", "colombo": "LK",
    "nepal": "NP", "kathmandu": "NP",
    "iceland": "IS", "reykjavik": "IS",
})

INDIA = re.compile(
    r"\b(india|chennai|bengaluru|bangalore|hyderabad|mumbai|pune|gurugram|gurgaon|noida|delhi|kolkata|ahmedabad|kochi|trivandrum|remote[,/-]\s*in)\b",
    re.I,
)
ROLE = re.compile(r"azure|cloud|devsecops|devops|infrastructure|platform|terraform|landing zone", re.I)


def country_for(location, explicit=""):
    if INDIA.search(location) or explicit.strip().upper() in {"IN", "IND", "INDIA"}:
        return ""
    explicit = explicit.strip()
    if explicit and re.fullmatch(r"[A-Za-z]{2}", explicit):
        return explicit.upper()
    hits = {code for name, code in COUNTRIES.items() if re.search(r"\b" + name + r"\b", location, re.I)}
    return hits.pop() if len(hits) == 1 else ""


def feed(board, client):
    provider, slug, employer = board["provider"], board["slug"], board["employer"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,90}", slug):
        raise ValueError("Invalid board identifier")
    endpoints = {
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
        "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
        "lever-eu": f"https://api.eu.lever.co/v0/postings/{slug}?mode=json",
        "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
    }
    response = client.get(endpoints[provider])
    response.raise_for_status()
    body = response.json()
    for job in body if isinstance(body, list) else body["jobs"]:
        if provider == "greenhouse":
            location = (job.get("location") or {}).get("name", "")
            fields = job["id"], job["title"], location, job["absolute_url"], job.get("content", ""), ""
        elif provider.startswith("lever"):
            category = job.get("categories") or {}
            location = " / ".join(category.get("allLocations") or [category.get("location") or ""])
            fields = (
                job["id"],
                job["text"],
                location,
                job["hostedUrl"],
                job.get("descriptionPlain") or job.get("description", ""),
                job.get("country") or "",
            )
        else:
            if job.get("isListed") is False:
                continue
            locations = [job.get("location") or ""] + [
                x.get("location") or "" for x in job.get("secondaryLocations", [])
            ]
            location = " / ".join(filter(None, locations))
            country_values = {
                ((address or {}).get("postalAddress") or {}).get("addressCountry", "")
                for address in [
                    job.get("address"),
                    *(x.get("address") for x in job.get("secondaryLocations", [])),
                ]
            }
            country_values.discard("")
            if any(value.strip().upper() in {"IN", "IND", "INDIA"} for value in country_values):
                continue
            country_code = next(iter(country_values)) if len(country_values) == 1 else ""
            fields = (
                job.get("id") or job["jobUrl"],
                job["title"],
                location,
                job["jobUrl"],
                job.get("descriptionPlain") or job.get("descriptionHtml", ""),
                country_code,
            )
        key, title, location, url, description, country_code = fields
        yield dict(
            source=f"{provider}:{slug}",
            source_id=str(key),
            employer=employer,
            title=title,
            location=location,
            url=url,
            description=plain(description),
            country_code=country_code,
        )


def ingest(db, row):
    location = row["location"]
    description = row["description"]
    country = country_for(location, row.get("country_code", ""))
    if not country or re.search(r"\b(?:based in|remote in|located in) india\b", description, re.I):
        return None
    if not ROLE.search(row["title"] + " " + description):
        return None
    url = canonical(row["url"])
    key = hashlib.sha256((row["source"] + "\0" + row["source_id"]).encode()).hexdigest()[:32]
    db.execute("INSERT INTO employer(name) VALUES (?) ON CONFLICT DO NOTHING", (row["employer"],))
    existing = db.one("SELECT * FROM vacancy WHERE id=? OR url=?", (key, url))
    if existing:
        if existing["id"] == key and any(
            existing[field] != value
            for field, value in {
                "url": url,
                "description": description,
                "location": location,
                "country": country,
                "title": row["title"],
                "employer": row["employer"],
            }.items()
        ):
            db.execute(
                "UPDATE vacancy SET title=?,country=?,location=?,description=?,url=?,employer=?,active=1 WHERE id=?",
                (row["title"], country, location, description, url, row["employer"], key),
            )
            db.execute("DELETE FROM matching WHERE vacancy_id=?", (key,))
            db.execute("DELETE FROM sponsorship WHERE vacancy_id=?", (key,))
            db.execute("UPDATE draft SET state='stale' WHERE vacancy_id=? AND state!='submitted'", (key,))
            audit(db, "discovery", "vacancy_changed", key)
        elif existing["id"] == key:
            db.execute("UPDATE vacancy SET active=1 WHERE id=?", (key,))
        return existing["id"]
    db.execute(
        "INSERT INTO vacancy VALUES (?,?,?,?,?,?,?,?,?,?,1)",
        (
            key,
            row["source"],
            row["source_id"],
            url,
            row["employer"],
            row["title"],
            country,
            location,
            description,
            now(),
        ),
    )
    audit(db, "discovery", "vacancy_discovered", key)
    return key


def boards(raw):
    return json.loads(raw or "[]")


def fetch_board(board):
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        return list(feed(board, client))
