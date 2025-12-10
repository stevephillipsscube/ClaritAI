#RedmineAPI
from __future__ import annotations
import os
import json
import argparse
import logging
from typing import Any, Dict, Optional, List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except Exception:
    # dotenv is optional; script also works with plain env vars
    pass

# -------------------------
# Config & Logging
# -------------------------
REDMINE_URL = os.getenv("REDMINE_URL", "").rstrip("/")
API_KEY = os.getenv("REDMINE_API_KEY", "")
VERIFY_SSL = os.getenv("REDMINE_VERIFY_SSL", "true").strip().lower() not in {"0", "false", "no"}

if not REDMINE_URL or not API_KEY:
    raise SystemExit("Please set REDMINE_URL and REDMINE_API_KEY environment variables (e.g., in a .env file).")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("redmine")

# -------------------------
# HTTP Session with retries
# -------------------------
def make_session(timeout: int = 30) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "X-Redmine-API-Key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "RedmineClient/1.0",
    })
    # Retry on transient errors & rate limits
    retry = Retry(
        total=5,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "PUT", "POST", "DELETE", "OPTIONS", "PATCH"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.request = _with_timeout(session.request, timeout=timeout)  # type: ignore
    return session

def _with_timeout(func, timeout=30):
    def wrapper(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return func(method, url, **kwargs)
    return wrapper

# -------------------------
# Helpers
# -------------------------
def _url(path: str) -> str:
    return f"{REDMINE_URL}{path}"

def _handle_response(r: requests.Response) -> Dict[str, Any]:
    # Raise for 401/403 quickly for auth clarity
    if r.status_code in (401, 403):
        raise RuntimeError(f"Auth failed or forbidden ({r.status_code}). Check API key and permissions.\n{r.text}")
    if r.status_code >= 400:
        # Try to surface Redmine error messages
        try:
            data = r.json()
            msg = data.get("errors") or data
        except Exception:
            msg = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {msg}")
    if r.status_code == 204 or not r.content:
        return {}
    try:
        return r.json()
    except json.JSONDecodeError:
        # Some endpoints may return empty body / non-json
        return {}

session = make_session()

# -------------------------
# API Operations
# -------------------------
def api_get_projects(limit=100, offset=0) -> Dict[str, Any]:
    r = session.get(_url(f"/projects.json"), params={"limit": limit, "offset": offset}, verify=VERIFY_SSL)
    return _handle_response(r)

def api_get_issue(issue_id: int) -> Dict[str, Any]:
    r = session.get(_url(f"/issues/{issue_id}.json"), verify=VERIFY_SSL)
    return _handle_response(r)

def api_create_issue(
    project_id: str | int,
    subject: str,
    description: Optional[str] = None,
    tracker_id: Optional[int] = None,
    priority_id: Optional[int] = None,
    status_id: Optional[int] = None,
    assigned_to_id: Optional[int] = None,
    custom_fields: Optional[List[Dict[str, Any]]] = None,
    uploads: Optional[List[Dict[str, Any]]] = None,  # from uploads token API
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "issue": {
            "project_id": project_id,
            "subject": subject,
        }
    }
    if description: payload["issue"]["description"] = description
    if tracker_id: payload["issue"]["tracker_id"] = tracker_id
    if priority_id: payload["issue"]["priority_id"] = priority_id
    if status_id: payload["issue"]["status_id"] = status_id
    if assigned_to_id: payload["issue"]["assigned_to_id"] = assigned_to_id
    if custom_fields: payload["issue"]["custom_fields"] = custom_fields
    if uploads: payload["issue"]["uploads"] = uploads

    r = session.post(_url("/issues.json"), data=json.dumps(payload), verify=VERIFY_SSL)
    return _handle_response(r)

def api_update_issue(
    issue_id: int,
    **fields: Any,
) -> Dict[str, Any]:
    # fields can include: subject, notes, description, status_id, priority_id, assigned_to_id, custom_fields, uploads...
    payload = {"issue": fields}
    r = session.put(_url(f"/issues/{issue_id}.json"), data=json.dumps(payload), verify=VERIFY_SSL)
    return _handle_response(r)

def api_time_entry(
    issue_id: int,
    hours: float,
    activity_id: Optional[int] = None,
    comments: Optional[str] = None,
    spent_on: Optional[str] = None,  # YYYY-MM-DD
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "time_entry": {
            "issue_id": issue_id,
            "hours": hours,
        }
    }
    if activity_id: payload["time_entry"]["activity_id"] = activity_id
    if comments: payload["time_entry"]["comments"] = comments
    if spent_on: payload["time_entry"]["spent_on"] = spent_on
    if user_id: payload["time_entry"]["user_id"] = user_id

    r = session.post(_url("/time_entries.json"), data=json.dumps(payload), verify=VERIFY_SSL)
    return _handle_response(r)

def api_upload_file(path: str) -> Dict[str, Any]:
    """
    Redmine file upload is a two-step process:
      1) POST /uploads.json with raw binary and header Content-Type: application/octet-stream
      2) Use returned 'upload.token' when creating or updating issue as 'uploads': [{'token': '...', 'filename': 'x', 'content_type': 'image/png'}]
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File not found: {path}")
    with open(path, "rb") as f:
        headers = {
            # Override content-type for this special endpoint
            "Content-Type": "application/octet-stream",
            "X-Redmine-API-Key": API_KEY,
            "Accept": "application/json",
            "User-Agent": "RedmineClient/1.0",
        }
        r = requests.post(_url("/uploads.json"), data=f, headers=headers, verify=VERIFY_SSL)
    data = _handle_response(r)
    return data  # contains {"upload": {"token": "..."}}

def api_my_account() -> Dict[str, Any]:
    r = session.get(_url("/users/current.json"), verify=VERIFY_SSL)
    return _handle_response(r)

# -------------------------
# CLI
# -------------------------
def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Redmine REST API client")
    sub = p.add_subparsers(dest="resource", required=True)

    # projects
    sp = sub.add_parser("projects", help="Project operations")
    sps = sp.add_subparsers(dest="action", required=True)
    sps.add_parser("list", help="List projects").add_argument("--limit", type=int, default=100)
    sps.add_parser("list", help="List projects").add_argument("--offset", type=int, default=0)

    # issues
    si = sub.add_parser("issues", help="Issue operations")
    sii = si.add_subparsers(dest="action", required=True)

    g = sii.add_parser("get", help="Get issue")
    g.add_argument("--id", type=int, required=True)

    c = sii.add_parser("create", help="Create issue")
    c.add_argument("--project-id", required=True)
    c.add_argument("--subject", required=True)
    c.add_argument("--description")
    c.add_argument("--tracker-id", type=int)
    c.add_argument("--priority-id", type=int)
    c.add_argument("--status-id", type=int)
    c.add_argument("--assigned-to-id", type=int)

    u = sii.add_parser("update", help="Update issue")
    u.add_argument("--id", type=int, required=True)
    u.add_argument("--subject")
    u.add_argument("--description")
    u.add_argument("--status-id", type=int)
    u.add_argument("--priority-id", type=int)
    u.add_argument("--assigned-to-id", type=int)
    u.add_argument("--notes")

    # time entries
    st = sub.add_parser("time", help="Time entry operations")
    stt = st.add_subparsers(dest="action", required=True)
    a = stt.add_parser("add", help="Add time entry")
    a.add_argument("--issue-id", type=int, required=True)
    a.add_argument("--hours", type=float, required=True)
    a.add_argument("--activity-id", type=int)
    a.add_argument("--comments")
    a.add_argument("--spent-on")

    # uploads
    su = sub.add_parser("uploads", help="Upload file")
    suu = su.add_subparsers(dest="action", required=True)
    f = suu.add_parser("file", help="Upload a file and return token")
    f.add_argument("--path", required=True)

    # me
    sm = sub.add_parser("me", help="Show current user")
    sm.add_subparsers(dest="action", required=False)
    return p

def main():
    parser = build_cli()
    args = parser.parse_args()

    try:
        if args.resource == "projects":
            data = api_get_projects(limit=getattr(args, "limit", 100), offset=getattr(args, "offset", 0))
            print(json.dumps(data, indent=2))

        elif args.resource == "issues":
            if args.action == "get":
                data = api_get_issue(args.id)
                print(json.dumps(data, indent=2))
            elif args.action == "create":
                data = api_create_issue(
                    project_id=args.project_id,
                    subject=args.subject,
                    description=args.description,
                    tracker_id=args.tracker_id,
                    priority_id=args.priority_id,
                    status_id=args.status_id,
                )
                print(json.dumps(data, indent=2))
            elif args.action == "update":
                fields = {}
                for fld in ("subject", "description", "status_id", "priority_id", "assigned_to_id", "notes"):
                    v = getattr(args, fld, None)
                    if v is not None:
                        # Redmine expects "notes" for journal entries
                        fields["notes" if fld == "notes" else fld] = v
                data = api_update_issue(args.id, **fields)
                print(json.dumps(data, indent=2))

        elif args.resource == "time":
            if args.action == "add":
                data = api_time_entry(
                    issue_id=args.issue_id,
                    hours=args.hours,
                    activity_id=args.activity_id,
                    comments=args.comments,
                    spent_on=args.spent_on,
                )
                print(json.dumps(data, indent=2))

        elif args.resource == "uploads":
            if args.action == "file":
                up = api_upload_file(args.path)
                print(json.dumps(up, indent=2))
                token = up.get("upload", {}).get("token")
                if token:
                    logger.info("Use this token when creating/updating an issue, e.g.:")
                    example = {
                        "issue": {
                            "subject": "With attachment",
                            "uploads": [
                                {
                                    "token": token,
                                    "filename": os.path.basename(args.path),
                                    "content_type": "application/octet-stream"
                                }
                            ]
                        }
                    }
                    print(json.dumps(example, indent=2))

        elif args.resource == "me":
            data = api_my_account()
            print(json.dumps(data, indent=2))

        else:
            parser.print_help()

    except Exception as e:
        logger.error(str(e))
        raise SystemExit(1)

if __name__ == "__main__":
    main()