#!/usr/bin/env python3
"""Scrape IANA Root Zone Database - fetch every TLD subpage and save as JSON."""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.iana.org"
LIST_URL = f"{BASE_URL}/domains/root/db"
OUTPUT_FILE = Path("iana_tlds.json")
MAX_WORKERS = 5
MAX_RETRIES = 5
BACKOFF_BASE = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; IANA-TLD-Scraper/1.0)"
}

session = requests.Session()
session.headers.update(HEADERS)

counter_lock = Lock()
counter = {"done": 0, "errors": 0}


def fetch(url: str) -> str:
    for attempt in range(MAX_RETRIES):
        resp = session.get(url, timeout=30)
        if resp.status_code == 429:
            wait = BACKOFF_BASE ** (attempt + 1)
            print(f"  429 rate-limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.text
    resp.raise_for_status()


def parse_list_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tld-table")
    if not table:
        raise RuntimeError("Could not find tld-table on the list page")
    tlds = []
    for row in table.find("tbody").find_all("tr"):
        cells = row.find_all("td")
        link = cells[0].find("a")
        if not link:
            continue
        tld = link.get_text(strip=True)
        href = link["href"]
        tld_type = cells[1].get_text(strip=True)
        manager = cells[2].get_text(strip=True)
        tlds.append({
            "tld": tld,
            "type": tld_type,
            "manager": manager,
            "detail_url": BASE_URL + href if href.startswith("/") else href,
        })
    return tlds


def parse_detail_page(html: str, tld_info: dict) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    result = {
        "tld": tld_info["tld"],
        "type": tld_info["type"],
        "manager": tld_info["manager"],
        "detail_url": tld_info["detail_url"],
        "domain": "",
        "sponsoring_organisation": "",
        "admin_contact": {},
        "tech_contact": {},
        "name_servers": [],
        "whois_server": "",
        "rdap_server": "",
        "registration_url": "",
        "record_last_updated": "",
        "registration_date": "",
    }

    h1 = soup.find("h1")
    if h1:
        m = re.search(r"\.([A-Z0-9]+)", h1.get_text())
        if m:
            result["domain"] = "." + m.group(1).lower()

    for h2 in soup.find_all("h2"):
        title = h2.get_text(strip=True)
        if title == "Sponsoring Organisation":
            text_nodes = []
            for sib in h2.next_siblings:
                if sib.name == "h2":
                    break
                text_nodes.append(sib.get_text(" ", strip=True))
            result["sponsoring_organisation"] = " ".join(text_nodes).strip()
        elif title == "Administrative Contact":
            result["admin_contact"] = _parse_contact(h2)
        elif title == "Technical Contact":
            result["tech_contact"] = _parse_contact(h2)
        elif title == "Name Servers":
            table = h2.find_next("table", class_="iana-table")
            if table:
                for row in table.find("tbody").find_all("tr"):
                    cols = row.find_all("td")
                    if len(cols) >= 2:
                        host = cols[0].get_text(strip=True)
                        ips = [ip.strip() for ip in cols[1].get_text("\n", strip=True).split("\n") if ip.strip()]
                        result["name_servers"].append({"host": host, "ip_addresses": ips})

    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        m = re.search(r"WHOIS Server:\s*(\S+)", text)
        if m:
            result["whois_server"] = m.group(1)
        m = re.search(r"RDAP Server:\s*(\S+)", text)
        if m:
            result["rdap_server"] = m.group(1)
        a = p.find("a", href=True)
        if a and "registration" in text.lower():
            result["registration_url"] = a["href"]

    all_text = soup.get_text(" ", strip=True)
    m = re.search(r"Record last updated\s+(\d{4}-\d{2}-\d{2})", all_text)
    if m:
        result["record_last_updated"] = m.group(1)
    m = re.search(r"Registration date\s+(\d{4}-\d{2}-\d{2})", all_text)
    if m:
        result["registration_date"] = m.group(1)

    return result


def _parse_contact(h2) -> dict:
    lines = []
    for sib in h2.next_siblings:
        if sib.name == "h2":
            break
        lines.append(sib.get_text(" ", strip=True))
    block = " ".join(lines)
    contact = {}
    m = re.search(r"(?:Email|E-mail):\s*(\S+)", block, re.I)
    if m:
        contact["email"] = m.group(1)
    m = re.search(r"Voice:\s*(.+?)(?:Fax|$)", block)
    if m:
        contact["voice"] = m.group(1).strip()
    m = re.search(r"Fax:\s*(.+?)$", block)
    if m:
        contact["fax"] = m.group(1).strip()
    if lines:
        contact["name"] = lines[0].strip()
    return contact


def fetch_tld(idx: int, total: int, tld_info: dict) -> dict:
    tld_name = tld_info["tld"]
    try:
        html = fetch(tld_info["detail_url"])
        parsed = parse_detail_page(html, tld_info)
        with counter_lock:
            counter["done"] += 1
            d = counter["done"]
        print(f"[{d}/{total}] {tld_name} OK", flush=True)
        return parsed
    except Exception as e:
        with counter_lock:
            counter["errors"] += 1
            counter["done"] += 1
            d = counter["done"]
        print(f"[{d}/{total}] {tld_name} ERROR: {e}", flush=True)
        return {**tld_info, "error": str(e)}


def main():
    print("Fetching TLD list page...")
    list_html = fetch(LIST_URL)
    tlds = parse_list_page(list_html)
    total = len(tlds)
    print(f"Found {total} TLDs. Fetching detail pages with {MAX_WORKERS} threads...")

    results = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_tld, i, total, tld_info): tld_info["tld"]
            for i, tld_info in enumerate(tlds, 1)
        }
        for future in as_completed(futures):
            tld_name = futures[future]
            results[tld_name] = future.result()

    ordered = [results[tld_info["tld"]] for tld_info in tlds]
    OUTPUT_FILE.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")
    err_count = sum(1 for r in ordered if isinstance(r, dict) and "error" in r)
    print(f"\nDone! {len(ordered)} TLDs ({err_count} errors) saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
