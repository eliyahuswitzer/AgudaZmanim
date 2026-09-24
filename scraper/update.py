"""
Checks each shul's website for its monthly calendar PDF, parses anything new,
and rebuilds the data files the web app reads.

    python scraper/update.py

Exits with an error if a required shul's times don't reach tomorrow, so the
GitHub job fails (and emails you) when a new luach hasn't been picked up.
Optional shuls only print a warning.
"""

import hashlib
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin
from urllib.request import Request, urlopen

import aguda_parser
import ahavas_parser
from parsing import MONTHS

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
SOURCES_DIR = DATA_DIR / "sources"
# Old calendars are dropped once their last day is this far in the past.
KEEP_DAYS_AFTER_END = 30

SHULS = [
    {
        "id": "aguda",
        "name": "Agudas Yisroel Bircas Yaakov",
        "short_name": "Aguda",
        "address": "262 Terhune Ave, Passaic, NJ 07055",
        "website": "https://www.ayby.org/",
        "calendar_page": "https://www.ayby.org/",
        # The luach is posted as e.g. september_2026.pdf
        "link_pattern": re.compile("|".join(MONTHS), re.IGNORECASE),
        "parser": aguda_parser.parse_pdf,
        "default_on": True,
        "required": True,
    },
    {
        "id": "ahavas",
        "name": "Ahavas Israel",
        "short_name": "Ahavas Israel",
        "address": "181 Van Houten Ave, Passaic, NJ 07055",
        "website": "https://www.ahavasisrael.org/",
        "calendar_page": "https://www.ahavasisrael.org/ahavas-calendar",
        # ShulCloud posts the calendar as e.g. calendar0926.pdf
        "link_pattern": re.compile(r"calendar\d{4}\.pdf", re.IGNORECASE),
        "parser": ahavas_parser.parse_pdf,
        "default_on": False,
        "required": False,
    },
]


# Ahavas Israel's host (ShulCloud) answers 406 unless the request looks like a browser.
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch(url):
    with urlopen(Request(url, headers=HEADERS), timeout=60) as response:
        return response.read()


def find_calendar_links(shul):
    """The calendar PDFs linked from this shul's site."""
    html = fetch(shul["calendar_page"]).decode("utf-8", errors="replace")
    links = []
    for href in re.findall(r'href="([^"]+\.pdf)"', html, re.IGNORECASE):
        name = unquote(href.rsplit("/", 1)[-1])
        if shul["link_pattern"].search(name):
            url = urljoin(shul["calendar_page"], href)
            if url not in links:
                links.append(url)
    return links


def update_source(shul, url):
    """Downloads one calendar and re-parses it only if its contents changed."""
    file_name = unquote(url.rsplit("/", 1)[-1])
    source_dir = SOURCES_DIR / shul["id"]
    source_path = source_dir / (re.sub(r"[^a-z0-9_-]+", "_", Path(file_name).stem.lower()) + ".json")

    pdf_bytes = fetch(url)
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    if source_path.exists() and json.loads(source_path.read_text("utf-8"))["sha256"] == sha256:
        print(f"  unchanged: {file_name}")
        return

    print(f"  parsing: {file_name}")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "calendar.pdf"
        pdf_path.write_bytes(pdf_bytes)
        parsed = shul["parser"](pdf_path)

    source = {
        "file": file_name,
        "url": url,
        "sha256": sha256,
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "month": parsed["month"],
        "legend": parsed.get("legend", []),
        "notes": parsed.get("notes", []),
        "days": parsed["days"],
    }
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), "utf-8")
    print(f"    {len(parsed['days'])} days, {parsed['days'][0]['date']} to {parsed['days'][-1]['date']}")


def rebuild_shul(shul):
    """Merges this shul's saved calendars into one file. Where they overlap, the newer wins."""
    cutoff = (date.today() - timedelta(days=KEEP_DAYS_AFTER_END)).isoformat()
    sources = []
    for path in sorted((SOURCES_DIR / shul["id"]).glob("*.json")):
        source = json.loads(path.read_text("utf-8"))
        if source["days"][-1]["date"] < cutoff:
            print(f"  removing old calendar: {source['file']}")
            path.unlink()
        else:
            sources.append(source)
    sources.sort(key=lambda s: (s["days"][0]["date"], s["fetched"]))

    days = {}
    for source in sources:
        for day in source["days"]:
            days[day["date"]] = day

    newest = sources[-1] if sources else None
    data = {
        "id": shul["id"],
        "name": shul["name"],
        "address": shul["address"],
        "website": shul["website"],
        "pdf_url": newest["url"] if newest else shul["calendar_page"],
        "updated": max((s["fetched"] for s in sources), default=None),
        "legend": newest["legend"] if newest else [],
        "notes": newest["notes"] if newest else [],
        "days": [days[d] for d in sorted(days)],
    }
    (DATA_DIR / f"{shul['id']}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return data


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    index, problems = [], []

    for shul in SHULS:
        print(f"{shul['name']}:")
        try:
            links = find_calendar_links(shul)
            if not links:
                raise RuntimeError(f"no calendar PDF linked from {shul['calendar_page']}")
            for url in links:
                update_source(shul, url)
        except Exception as error:  # a broken optional shul shouldn't stop the others
            print(f"  problem: {error}")
            problems.append((shul, str(error)))

        data = rebuild_shul(shul)
        last_day = data["days"][-1]["date"] if data["days"] else None
        stale = not last_day or last_day < tomorrow
        if stale:
            print(f"  warning: times only run to {last_day}")
            problems.append((shul, f"times only run to {last_day}"))
        else:
            print(f"  ok: times through {last_day}")

        index.append({
            "id": shul["id"],
            "name": shul["name"],
            "short_name": shul["short_name"],
            "default_on": shul["default_on"],
            "file": f"{shul['id']}.json",
            "updated": data["updated"],
            "stale": stale,
        })

    (DATA_DIR / "shuls.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), "utf-8")

    required = [f"{shul['name']}: {message}" for shul, message in problems if shul["required"]]
    if required:
        sys.exit("Required shul out of date -> " + "; ".join(required))


if __name__ == "__main__":
    main()
