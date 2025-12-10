#!/usr/bin/env python3
"""
Fetch a Redmine issue by ticket number and show a clean summary, description, and notes.

Usage:
  python redmine_get_issue.py 28854
  python redmine_get_issue.py --id 28854
  python redmine_get_issue.py 28854 --max-notes 5
  python redmine_get_issue.py 28854 --only-latest

Env (.env or system):
  REDMINE_URL=https://your.redmine.server
  REDMINE_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  REDMINE_VERIFY_SSL=true
  REDMINE_TICKET_ID=123
"""

import os
import re
import json
import argparse
import requests
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except Exception:
    pass

REDMINE_URL = os.getenv("REDMINE_URL", "").rstrip("/")
API_KEY = os.getenv("REDMINE_API_KEY", "")
VERIFY_SSL = os.getenv("REDMINE_VERIFY_SSL", "true").strip().lower() not in {"0", "false", "no"}

if not REDMINE_URL or not API_KEY:
    raise SystemExit("Set REDMINE_URL and REDMINE_API_KEY in env or .env.")

def coerce_ticket_id(val: str | None) -> int:
    if not val:
        raise ValueError("No ticket number provided.")
    m = re.search(r"(\d+)$", str(val))
    if not m:
        raise ValueError(f"Could not parse ticket number from: {val!r}")
    return int(m.group(1))

def get_issue(issue_id: int) -> dict:
    url = f"{REDMINE_URL}/issues/{issue_id}.json"
    params = {"include": "journals"}  # grab notes/comments
    headers = {
        "X-Redmine-API-Key": API_KEY,
        "Accept": "application/json",
        "User-Agent": "RedmineGetIssue/clean/1.0",
    }
    r = requests.get(url, headers=headers, params=params, timeout=30, verify=VERIFY_SSL)
    if r.status_code in (401, 403):
        raise SystemExit(f"Auth failed (HTTP {r.status_code}). Check API key/permissions.\n{r.text}")
    if r.status_code == 404:
        raise SystemExit(f"Issue #{issue_id} not found.")
    r.raise_for_status()
    return r.json()

def _fmt_dt(s: str | None) -> str:
    if not s:
        return "-"
    # Keep original if parse fails
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%SZ")
    except Exception:
        return s

def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    # collapse excessive blank lines / trailing spaces
    lines = [ln.rstrip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln.strip() == "":
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()

def print_issue_clean(issue: dict, max_notes: int | None = None, only_latest: bool = False) -> None:
    i = issue.get("issue", {})

    # Fixed-order summary (each printed exactly once)
    summary_order = [
        ("id", i.get("id")),
        ("project", (i.get("project") or {}).get("name")),
        ("tracker", (i.get("tracker") or {}).get("name")),
        ("status", (i.get("status") or {}).get("name")),
        ("priority", (i.get("priority") or {}).get("name")),
        ("subject", i.get("subject")),
        ("assigned_to", (i.get("assigned_to") or {}).get("name")),
        ("author", (i.get("author") or {}).get("name")),
        ("created_on", _fmt_dt(i.get("created_on"))),
        ("updated_on", _fmt_dt(i.get("updated_on"))),
    ]

    print("=== ISSUE SUMMARY ===")
    for k, v in summary_order:
        print(f"{k}: {v if v not in (None, '') else '-'}")

    print("\n=== DESCRIPTION ===")
    desc = _clean_text(i.get("description"))
    print(desc if desc else "(No description)")

    # Notes / journals (unique, sorted by created_on)
    journals = i.get("journals") or []
    # Keep only entries that actually have notes
    journals = [j for j in journals if (j.get("notes") or "").strip()]

    # Sort by timestamp if available
    def key_dt(j):
        s = j.get("created_on")
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min
    journals.sort(key=key_dt)

    # Dedupe identical (created_on, user, notes) to avoid spam
    seen = set()
    unique = []
    for j in journals:
        tup = (
            j.get("created_on"),
            (j.get("user") or {}).get("name"),
            _clean_text(j.get("notes") or ""),
        )
        if tup not in seen:
            seen.add(tup)
            unique.append({"created_on": tup[0], "user": tup[1], "notes": tup[2]})

    if only_latest and unique:
        unique = [unique[-1]]

    if max_notes is not None and max_notes >= 0:
        unique = unique[-max_notes:] if max_notes > 0 else []

    print("\n=== NOTES / JOURNALS ===")
    if not unique:
        print("(No notes found.)")
    else:
        for j in unique:
            ts = _fmt_dt(j["created_on"])
            user = j["user"] or "Unknown"
            notes = j["notes"]
            print(f"\n[{ts}] {user} wrote:\n{notes}")

def main():
    ap = argparse.ArgumentParser(description="Fetch a Redmine issue and print a clean summary.")
    ap.add_argument("id", nargs="?", help="Ticket number (e.g., 123 or #123)")
    ap.add_argument("--id", dest="id_opt", help="Ticket number via flag")
    ap.add_argument("--max-notes", type=int, default=None, help="Limit notes to the last N entries")
    ap.add_argument("--only-latest", action="store_true", help="Show only the latest note")
    args = ap.parse_args()

    ticket_raw = args.id_opt or args.id or os.getenv("REDMINE_TICKET_ID")
    try:
        ticket_id = coerce_ticket_id(ticket_raw)
    except Exception as e:
        raise SystemExit(f"Error: {e}")

    data = get_issue(ticket_id)
    print_issue_clean(data, max_notes=args.max_notes, only_latest=args.only_latest)

if __name__ == "__main__":
    main()
