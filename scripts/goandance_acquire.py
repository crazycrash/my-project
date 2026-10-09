#!/usr/bin/env python3
"""Live, resumable Go&Dance source acquisition. No search-engine snapshots."""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
import requests
from bs4 import BeautifulSoup

BASE = "https://www.goandance.com"
SECTIONS = [
    ("festivals", "/en/festivals"), ("events", "/en/events"),
    ("concerts", "/en/concerts"), ("parties", "/en/parties"),
    ("workshops", "/en/workshops"), ("bachata", "/en/events/bachata"),
    ("kizomba", "/en/events/kizomba"), ("salsa", "/en/events/salsa"),
]
ROOT = Path(os.getenv("DATA_DIR", "data/goandance"))
FIELDS = ["id", "name", "detail_url", "source_section", "source_page",
          "source_url", "list_text", "first_seen_utc", "last_seen_utc",
          "detail_status", "detail_title", "detail_description"]
MAX_PER_RUN = int(os.getenv("PAGES_PER_RUN", "10"))
MAX_DETAIL = int(os.getenv("DETAILS_PER_RUN", "40"))
SLEEP = float(os.getenv("FETCH_DELAY_SECONDS", "1.4"))
UA = "DanceRadarResearchBot/0.1 (+scheduled source verification; no credentials)"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en", "Accept": "text/html"})
now = datetime.now(timezone.utc).isoformat(timespec="seconds")

def read_json(path, fallback):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError): return fallback

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def fetch(url):
    parsed = urlparse(url)
    if parsed.hostname not in ("goandance.com", "www.goandance.com") or parsed.scheme != "https":
        raise ValueError("Blocked non-Go&Dance fetch: " + url)
    response = session.get(url, timeout=28, allow_redirects=True)
    response.raise_for_status()
    if urlparse(response.url).hostname not in ("goandance.com", "www.goandance.com"):
        raise RuntimeError("Redirect left source domain")
    if "html" not in response.headers.get("Content-Type", ""):
        raise RuntimeError("Expected HTML, got " + response.headers.get("Content-Type", "?"))
    if "captcha" in response.url.lower() or "cloudflare" in response.text[:4000].lower():
        raise RuntimeError("Potential access challenge")
    time.sleep(SLEEP)
    return BeautifulSoup(response.text, "html.parser"), response.url

def page_url(path, page):
    # Go&Dance displays page 1 at base URL, page 2 at ?page=1.
    if page == 0: return BASE + path
    return BASE + path + "?" + urlencode({"page": page})

def card_links(soup, url):
    output = {}
    for a in soup.select("a[href]"):
        href = urljoin(url, a.get("href", ""))
        match = re.search(r"/(?:en/)?event/(\\d+)(?:/|$|\\?)", urlparse(href).path)
        if not match: continue
        event_id = match.group(1)
        clean = urlunparse(urlparse(href)._replace(query="", fragment=""))
        title = a.get_text(" ", strip=True)
        if not title: title = a.get("title") or a.get("aria-label") or ""
        if event_id not in output or len(title) > len(output[event_id][1]):
            output[event_id] = (clean, title[:300])
    return output

def pages_advertised(soup):
    pages = []
    for a in soup.find_all("a", href=True):
        try:
            p = urlparse(urljoin(BASE, a["href"]))
            if "goandance.com" not in (p.hostname or ""): continue
            for x in parse_qs(p.query).get("page", []):
                if x.isdigit() and int(x) < 10000: pages.append(int(x))
        except Exception: continue
    text = soup.get_text(" ", strip=True)
    m = re.search(r"Page\\s+\\d+\\s+of\\s+(\\d+)", text, re.I)
    if m: pages.append(int(m.group(1)) - 1)
    return max([0] + pages)

def load_records(path):
    if not path.exists(): return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["id"]: r for r in csv.DictReader(f) if r.get("id")}

def save_records(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=FIELDS)
        wr.writeheader()
        wr.writerows(sorted(records.values(), key=lambda v: int(v["id"])))
    tmp.replace(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=MAX_PER_RUN)
    args = parser.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    state_path = ROOT / "cursor.json"
    state = read_json(state_path, {"section_index": 0, "page_index": 0, "round": 1})
    records_path = ROOT / "events.csv"
    records = load_records(records_path)
    logs_path = ROOT / "runs.json"
    history = read_json(logs_path, [])
    budget = max(1, args.max_pages)
    errors = []
    processed = []
    new_records = 0
    detail_budget = MAX_DETAIL

    while budget > 0:
        section_index = int(state.get("section_index", 0)) % len(SECTIONS)
        slug, path = SECTIONS[section_index]
        page_index = int(state.get("page_index", 0))
        url = page_url(path, page_index)
        try:
            soup, actual_url = fetch(url)
            links = card_links(soup, actual_url)
            highest_page = pages_advertised(soup)
            # An empty nonfirst page may be a stale cursor after pagination changed.
            if page_index and not links:
                raise RuntimeError("No event cards on this page; cannot verify pagination")
            if page_index == 0 and not links:
                raise RuntimeError("No event cards; possible rendering/access issue")
            for event_id, (detail_url, title) in links.items():
                r = records.get(event_id)
                if r is None:
                    r = {key: "" for key in FIELDS}
                    r.update({"id": event_id, "first_seen_utc": now})
                    new_records += 1
                r.update({"name": title or r.get("name", ""), "detail_url": detail_url,
                          "source_section": ";".join(dict.fromkeys(filter(None, (r.get("source_section", "")+";"+slug).split(";")))),
                          "source_page": str(page_index+1),
                          "source_url": actual_url,
                          "last_seen_utc": now})
                records[event_id] = r
            # Detail URLs are fetched live, separately; never fabricate details.
            for event_id, (detail_url, _) in links.items():
                if detail_budget <= 0: break
                r = records[event_id]
                if r.get("detail_status") == "HTTP_200" and r.get("detail_title"): continue
                try:
                    detail, _ = fetch(detail_url)
                    title = detail.title.get_text(" ", strip=True) if detail.title else ""
                    description = detail.find("meta", attrs={"name": "description"})
                    r["detail_title"] = title[:300]
                    r["detail_description"] = (description.get("content", "") if description else "")[:1000]
                    r["detail_status"] = "HTTP_200"
                except Exception as e:
                    r["detail_status"] = "UNVERIFIED: " + str(e)[:150]
                detail_budget -= 1
            processed.append({"section": slug, "page": page_index+1, "url": actual_url,
                              "cards": len(links), "advertised_last_page": highest_page+1})
            # Advance ONLY after successfully storing this page and its records.
            if page_index >= highest_page:
                state["section_index"] = (section_index + 1) % len(SECTIONS)
                state["page_index"] = 0
                if section_index == len(SECTIONS)-1:
                    state["round"] = int(state.get("round", 1))+1
            else:
                state["section_index"] = section_index
                state["page_index"] = page_index+1
            state["updated_utc"] = now
            save_records(records_path, records)
            write_json(state_path, state)
            budget -= 1
        except (requests.RequestException, RuntimeError, ValueError) as e:
            errors.append({"section": slug, "page": page_index+1, "url": url, "error": str(e)[:300]})
            # Blocked page remains the cursor. Do not skip or claim completeness.
            break
    history.append({"timestamp_utc": now, "processed_pages": processed, "new_event_ids": new_records,
                    "total_unique_event_ids": len(records), "errors": errors,
                    "cursor": state.copy()})
    write_json(logs_path, history[-500:])
    print(json.dumps(history[-1], ensure_ascii=False))
    if errors:
        print("Partial acquisition: cursor preserved; source access requires review", file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
