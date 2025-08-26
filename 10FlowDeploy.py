# 11FlowDeploy.py
import os, re, json
import argparse, shutil, subprocess, sys, io
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from simple_salesforce import Salesforce

# --- Load .env robustly (works regardless of where you run the script) ---
DOTENV_PATH = find_dotenv(usecwd=True)
if not load_dotenv(DOTENV_PATH, override=True):
    print("⚠️  .env not found (using OS env only)", file=sys.stderr)

# Connect to Salesforce (not used for deploy, but kept if you need API calls)
sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "test"),
)

# --- UTF-8-safe console on Windows ---
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

FLOWS_DIR_DEFAULT = Path(r"force-app\main\default\flows")

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)

def summarize_deploy(stdout: str, returncode: int) -> str:
    """
    Summarize an sf deploy. Uses JSON when available to show an accurate file count,
    list each deployed file path, and print a total at the end.
    """
    try:
        data = json.loads(stdout or "{}")
        res = data.get("result", data)

        # High-level status / id
        status = res.get("status") or res.get("state") or ("Succeeded" if returncode == 0 else "Failed")
        job_id = res.get("id") or res.get("deploymentId") or res.get("deployId") or res.get("jobId")

        # Prefer 'deployedSource' – it contains filePath values for changed files
        ds = res.get("deployedSource") or []
        if isinstance(ds, dict):
            ds = [ds]

        raw_paths = [
            d.get("filePath") or d.get("sourcePath") or d.get("path") or d.get("fullName") or "?"
            for d in ds
        ]

        # Deduplicate while preserving order
        seen, paths = set(), []
        for p in raw_paths:
            if p not in seen:
                seen.add(p)
                paths.append(p)

        # Fallback: derive from component successes if deployedSource is missing/empty
        details = res.get("details") or {}
        succ = details.get("componentSuccesses") or []
        fail = details.get("componentFailures") or []
        if isinstance(succ, dict): succ = [succ]
        if isinstance(fail, dict): fail = [fail]

        if not paths and succ:
            for s in succ:
                p = s.get("fileName") or s.get("fullName")
                if p and p not in seen:
                    seen.add(p)
                    paths.append(p)

        files_count = len(paths)

        # Tests (when present)
        num_tests = res.get("numberTestsTotal") or (res.get("tests") or {}).get("total") or 0
        num_failed_tests = res.get("numberTestErrors") or (res.get("tests") or {}).get("failures") or 0

        # Header
        parts = [f"Status: {status}"]
        if job_id:
            parts.append(f"ID: {job_id}")
        parts.append(f"Files: {files_count}")
        if num_tests:
            parts.append(f"Tests: {num_tests} (fail {num_failed_tests})")

        lines = [" | ".join(parts)]

        # List files + explicit total line
        if paths:
            lines.append("Files deployed:")
            for p in paths:
                lines.append(f" - {p}")
        else:
            lines.append("Files deployed: (none)")

        lines.append(f"Total files deployed: {files_count}")

        # Show first few component failures (if any)
        for f in fail[:5]:
            path = f.get("fileName") or f.get("fullName") or "?"
            msg = f.get("problem") or f.get("message") or "Failed"
            lines.append(f" - {path}: {msg}")

        return "\n".join(lines)

    except json.JSONDecodeError:
        # Compact fallback when --json isn't present
        text = strip_ansi(stdout or "")
        keep = []
        for line in text.splitlines():
            if any(k in line for k in (
                "Status:", "Deploy ID:", "Target Org:", "Elapsed Time:",
                "Components:", "Error", "Failed", "Succeeded"
            )):
                keep.append(line.strip())
        return "\n".join(keep[-20:])
    except Exception as e:
        return f"[WARN] Could not parse deploy output ({e}). Raw:\n{strip_ansi(stdout or '')}"

def find_sf_cli() -> str:
    return shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"

def pick_latest_flow(flows_dir: Path) -> str:
    """Return Flow API name (file stem) of the newest Milestone_Emails_*.flow-meta.xml."""
    candidates = sorted(
        flows_dir.glob("Milestone_Emails_*.flow-meta.xml"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(f"No Milestone_Emails_*.flow-meta.xml found in {flows_dir.resolve()}")
    return candidates[0].name[:-len(".flow-meta.xml")]

def deploy_flow(source_paths: list[Path], org_alias: str, dry_run: bool, compact: bool = True):
    cli = find_sf_cli()
    cmd = [cli, "project", "deploy", "start"]
    for p in source_paths:
        cmd += ["--source-dir", str(p)]
    cmd += ["--target-org", org_alias]
    if dry_run:
        cmd.append("--dry-run")

    env = os.environ.copy()
    if compact:
        cmd += ["--json", "--wait", "5"]  # concise JSON + wait up to 5 minutes
        env["TERM"] = "dumb"              # disable TTY spinners
        env["NO_COLOR"] = "1"             # drop ANSI color

    print("[INFO] Running:", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env)

def resolve_org_alias(cli_arg: str | None) -> str:
    """
    Resolve org alias: prefer CLI flag, else .env (SF_ORG_ALIAS or SF_TARGET_ORG).
    Fail fast if none provided.
    """
    env_alias = (os.getenv("SF_ORG_ALIAS") or os.getenv("SF_TARGET_ORG") or "").strip()
    alias = (cli_arg or env_alias).strip()
    if not alias:
        print("❌ No org alias. Pass --org or set SF_ORG_ALIAS in .env", file=sys.stderr)
        sys.exit(2)
    return alias

def main():
    ap = argparse.ArgumentParser(description="Deploy a Flow to Clariti via SF CLI.")
    ap.add_argument("--flows-dir", default=str(FLOWS_DIR_DEFAULT),
                    help="Folder containing *.flow-meta.xml (default: force-app/main/default/flows)")
    ap.add_argument("--flow-name", default="", help="Flow API name or filename (extension optional)")
    ap.add_argument("--org", default=None, help="sf CLI org alias (overrides .env SF_ORG_ALIAS)")
    ap.add_argument("--with-subflow", action="store_true",
                    help="Also deploy Email_Autolaunched_Flow")
    ap.add_argument("--dry-run", action="store_true", help="Do a check-only deploy")
    args = ap.parse_args()

    flows_dir = Path(args.flows_dir)
    org_alias = resolve_org_alias(args.org)
    print(f"[INFO] Target org alias: {org_alias}")

    # Normalize flow name input (accept with or without suffix)
    if args.flow_name.strip():
        fn = args.flow_name.strip()
        lower = fn.lower()
        if lower.endswith(".flow-meta.xml"):
            flow_name = fn[:-len(".flow-meta.xml")]
        elif lower.endswith(".flow-meta"):
            flow_name = fn[:-len(".flow-meta")]
        elif lower.endswith(".xml"):
            flow_name = fn[:-len(".xml")]
        else:
            flow_name = fn
    else:
        flow_name = pick_latest_flow(flows_dir)

    flow_file = flows_dir / f"{flow_name}.flow-meta.xml"
    if not flow_file.exists():
        nearby = sorted(p.name for p in flows_dir.glob("*.flow-meta.xml"))
        raise SystemExit(f"Flow file not found: {flow_file}\nAvailable flows:\n  " + "\n  ".join(nearby))

    # Optionally include the subflow file
    source_paths = [flow_file]
    if getattr(args, "with_subflow", False):
        subflow_file = flows_dir / "Email_Autolaunched_Flow.flow-meta.xml"
        if subflow_file.exists():
            source_paths.append(subflow_file)
        else:
            print(f"[WARN] Subflow not found: {subflow_file} (skipping)")

    print(f"[INFO] Deploying: {', '.join(p.name for p in source_paths)} -> org {org_alias}")
    res = deploy_flow(source_paths, org_alias, args.dry_run, compact=True)

    # Compact summary with file list + total count
    print(summarize_deploy(res.stdout, res.returncode))
    if res.returncode == 0:
        print("[✅] Flow deployment successful.")
    else:
        print("[❌] Deployment failed.")
        if res.stderr.strip():
            print(res.stderr)
        sys.exit(res.returncode)

if __name__ == "__main__":
    main()
