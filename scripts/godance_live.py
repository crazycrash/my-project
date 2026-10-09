"""Go&Dance live crawler: fetches source pages on execution, never search index caches.
State is resumable and records uncertainty. No login or scraping bypasses."""
import csv
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

BASE = "https://www.goandance.com"
SECTIONS = [
    "/en/festivals", "/en/events", "/en/concerts", "/en/parties",
    "/en/workshops", "/en/events/bachata", "/en/events/kizomba",
    "/en/events/salsa",
]
OUT = Path("data/godance")
OUT.mkdir(parents=True, exist_ok=True)
STATE_FILE = OUT / "state.json"
RECORD_FILE = OUT / "events.json"
MAX_PAGES = int(os.getenv("MAX_PAGES_PER_RUN", "60"))
MAX_DETAILS = int(os.getenv("MAX_DETAILS_PER_RUN", "160"))
HEADERS = {"User-Agent": "FestivalCatalogResearch/1.0 (contact: GitHub issues; public pages)"}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except FileNotFoundError:
        return default

def save(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf8")
    tmp.replace(path)

def get(url):
    r = SESSION.get(url, timeout=28)
    r.raise_for_status()
    if "text/html" not in r.headers.get("content-type", "").lower():
        raise ValueError(f"Non-HTML response: {url}")
    return BeautifulSoup(r.text, "html.parser"), r.url

def is_event(href):
    return bool(re.match(r"^/(?:en/)?event/\d+/", urlparse(href).path))

def parse_listing(soup):
    items = {}
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if is_event(href):
            url = urljoin(BASE, href.split("?")[0])
            name = a.get_text(" ", strip=True)
            if len(name) > len(items.get(url, "")):
                items[url] = name
    text = soup.get_text(" ", strip=True)
    m = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", text, re.I)
    return items, (int(m.group(1)), int(m.group(2))) if m else None

def details(soup, url):
    title = soup.select_one("h1")
    canonical = soup.select_one('link[rel="canonical"]')
    desc = soup.select_one('meta[name="description"]')
    data = {
        "url": url, "name": title.get_text(" ", strip=True) if title else None,
        "canonical": canonical.get("href") if canonical else None,
        "description": desc.get("content") if desc else None,
        "text_excerpt": soup.get_text(" ", strip=True)[:2800],
        "page_links": sorted({
            urljoin(BASE, a.get("href")) for a in soup.select("a[href]")
            if re.search(r"instagram\.com|facebook\.com|https?://", a.get("href", ""), re.I)
        })[:60],
    }
    return data

def upload_sheet(records, status):
    secret = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sheet_id = os.getenv("GODANCE_SHEET_ID", "")
    if not (secret and sheet_id):
        return "not_configured"
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(secret),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    session = AuthorizedSession(credentials)
    # Write to dedicated tabs to avoid damaging manually maintained masters.
    root = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    metadata = session.get(root, timeout=30)
    metadata.raise_for_status()
    tabs = {s["properties"]["title"] for s in metadata.json().get("sheets", [])}
    need = [n for n in ("GITHUB_LIVE_EVENTS", "GITHUB_LIVE_STATUS") if n not in tabs]
    if need:
        result = session.post(
            root + ":batchUpdate",
            json={"requests": [{"addSheet": {"properties": {"title": n}}} for n in need]},
            timeout=30,
        )
        result.raise_for_status()
    rows = [["URL", "Name", "Source sections", "Last live check", "Status", "Excerpt"]]
    for url, d in sorted(records.items()):
        rows.append([url, d.get("name", ""), "; ".join(d.get("sections", [])),
                     d.get("last_checked", ""), d.get("status", ""),
                     (d.get("description") or d.get("text_excerpt") or "")[:400]])
    status_rows = [["Timestamp UTC", "Section", "Page", "Cycle", "Status", "Last error"],
                   [status["last_run"], status["section"], status["page"],
                    status["cycle"], status["status"], status.get("last_error", "")]]
    for name, values in (("GITHUB_LIVE_EVENTS", rows),
                         ("GITHUB_LIVE_STATUS", status_rows)):
        # Full rewrite of only our dedicated tabs; all other tabs stay untouched.
        clear = session.post(root + "/values/" + name + "!A:Z:clear", json={}, timeout=30)
        clear.raise_for_status()
        for start in range(0, len(values), 250):
            chunk = values[start:start+250]
            rng = f"{name}!A{start+1}"
            put = session.put(root + "/values/" + rng,
                              params={"valueInputOption": "RAW"},
                              json={"range": rng, "majorDimension": "ROWS", "values": chunk},
                              timeout=45)
            put.raise_for_status()
    return "updated"

def main():
    state = load(STATE_FILE, {"section_index": 0, "page": 1, "cycle": 1})
    records = load(RECORD_FILE, {})
    pages = 0
    detail_calls = 0
    errors = []
    timestamp = datetime.now(timezone.utc).isoformat()
    while pages < MAX_PAGES and detail_calls < MAX_DETAILS:
        ix = state["section_index"]
        if ix >= len(SECTIONS):
            state.update(section_index=0, page=1, cycle=state["cycle"] + 1)
            break  # next run starts a new full cycle
        section = SECTIONS[ix]
        page = state["page"]
        url = BASE + section + (f"?page={page-1}" if page > 1 else "")
        try:
            soup, final_url = get(url)
            links, pagination = parse_listing(soup)
            if not links:
                raise RuntimeError("No event cards found; do not mark page verified")
            # Do not advance until every card on this page has been inspected.
            for event_url, listing_name in links.items():
                seen = records.get(event_url, {})
                if seen.get("last_cycle") == state["cycle"] and section in seen.get("sections", []):
                    continue
                if detail_calls >= MAX_DETAILS:
                    break
                try:
                    detail_soup, resolved = get(event_url)
                    d = details(detail_soup, resolved)
                    d["name"] = d["name"] or listing_name
                    d["sections"] = sorted(set(seen.get("sections", []) + [section]))
                    d["last_checked"] = timestamp
                    d["last_cycle"] = state["cycle"]
                    d["status"] = "LIVE_DETAIL_FETCHED"
                    records[event_url] = d
                    detail_calls += 1
                    time.sleep(0.4)
                except Exception as ex:
                    errors.append(f"{event_url}: {type(ex).__name__}: {ex}")
                    # This page must be retried, not marked complete.
                    break
            else:
                pages += 1
                if pagination and page < pagination[1]:
                    state["page"] = page + 1
                elif pagination and page == pagination[1]:
                    state.update(section_index=ix+1, page=1)
                else:
                    # No reliable pagination detected: avoid assuming coverage.
                    errors.append(f"{url}: pagination unverified")
                    break
                save(RECORD_FILE, records)
                save(STATE_FILE, state)
                time.sleep(1)
                continue
            break
        except Exception as ex:
            errors.append(f"{url}: {type(ex).__name__}: {ex}")
            break
    status = {
        **state, "section": SECTIONS[state["section_index"]] if state["section_index"] < len(SECTIONS) else "end",
        "last_run": timestamp, "pages_fetched": pages, "details_fetched": detail_calls,
        "events_total": len(records), "status": "PARTIAL" if state["section_index"] < len(SECTIONS) or errors else "CYCLE_DONE",
        "last_error": "; ".join(errors[:10]),
    }
    save(STATE_FILE, state)
    save(RECORD_FILE, records)
    save(OUT / "last_run.json", status)
    try:
        status["google_sheets"] = upload_sheet(records, status)
    except Exception as ex:
        status["google_sheets"] = f"ERROR: {type(ex).__name__}: {ex}"
        save(OUT / "last_run.json", status)
        raise
    save(OUT / "last_run.json", status)
    print(json.dumps(status, ensure_ascii=False))
    if errors:
        raise RuntimeError("Incomplete live crawl; checkpoint retained")

if __name__ == "__main__":
    main()
