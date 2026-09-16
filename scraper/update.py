"""
Checks ayby.org for the monthly luach PDF, parses it if it's new or changed,
and rebuilds docs/data/schedule.json for the web app.

    python scraper/update.py

Exits with an error if the data doesn't cover tomorrow, so the GitHub job
fails (and emails you) when the shul's new luach hasn't been picked up.
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

from luach_parser import MONTHS, parse_pdf

SITE = "https://www.ayby.org/"
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
SOURCES_DIR = DATA_DIR / "sources"
# Old luachs are dropped once their last day is this far in the past.
KEEP_DAYS_AFTER_END = 30


def fetch(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 (shul zmanim app updater)"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def find_luach_links():
    """PDF links on the homepage whose file name mentions a month, e.g. september_2026.pdf"""
    html = fetch(SITE).decode("utf-8", errors="replace")
    links = []
    for href in re.findall(r'href="([^"]+\.pdf)"', html, re.IGNORECASE):
        name = unquote(href.rsplit("/", 1)[-1]).lower()
        if any(month in name for month in MONTHS):
            url = urljoin(SITE, href)
            if url not in links:
                links.append(url)
    return links


def update_source(url):
    """Downloads one PDF and re-parses it only if its contents changed."""
    file_name = unquote(url.rsplit("/", 1)[-1])
    source_path = SOURCES_DIR / (re.sub(r"[^a-z0-9_-]+", "_", Path(file_name).stem.lower()) + ".json")

    pdf_bytes = fetch(url)
    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    if source_path.exists() and json.loads(source_path.read_text("utf-8"))["sha256"] == sha256:
        print(f"Unchanged: {file_name}")
        return

    print(f"Parsing: {file_name}")
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "luach.pdf"
        pdf_path.write_bytes(pdf_bytes)
        parsed = parse_pdf(pdf_path)

    source = {
        "file": file_name,
        "url": url,
        "sha256": sha256,
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "month": parsed["month"],
        "days": parsed["days"],
    }
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    source_path.write_text(json.dumps(source, ensure_ascii=False, indent=2), "utf-8")
    print(f"  {len(parsed['days'])} days, {parsed['days'][0]['date']} to {parsed['days'][-1]['date']}")


def rebuild_schedule():
    """Merges all saved luachs into one file. Where two luachs overlap, the newer one wins."""
    cutoff = (date.today() - timedelta(days=KEEP_DAYS_AFTER_END)).isoformat()
    sources = []
    for path in SOURCES_DIR.glob("*.json"):
        source = json.loads(path.read_text("utf-8"))
        if source["days"][-1]["date"] < cutoff:
            print(f"Removing old luach: {source['file']}")
            path.unlink()
        else:
            sources.append(source)
    sources.sort(key=lambda s: (s["days"][0]["date"], s["fetched"]))

    days = {}
    for source in sources:
        for day in source["days"]:
            days[day["date"]] = day

    schedule = {
        "updated": max((s["fetched"] for s in sources), default=None),
        "pdf_url": sources[-1]["url"] if sources else SITE,
        "days": [days[d] for d in sorted(days)],
    }
    (DATA_DIR / "schedule.json").write_text(json.dumps(schedule, ensure_ascii=False, indent=2), "utf-8")
    return schedule


def main():
    links = find_luach_links()
    if not links:
        print(f"No luach PDF links found on {SITE}")
    for url in links:
        update_source(url)

    schedule = rebuild_schedule()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    last_day = schedule["days"][-1]["date"] if schedule["days"] else None
    if not last_day or last_day < tomorrow:
        sys.exit(f"Data runs out on {last_day}. The shul's next luach may not be posted yet, "
                 f"or it's linked under a name this script doesn't recognize.")
    print(f"OK: times available through {last_day}")


if __name__ == "__main__":
    main()
