import argparse, re, html, shutil, subprocess
from pathlib import Path
import xml.etree.ElementTree as ET
from xml.dom.minidom import parseString
import sys  # <-- add this at the top with the other imports
import io
import os
import json
from dotenv import load_dotenv, find_dotenv
from simple_salesforce import Salesforce

# Load .env regardless of where you run the script from
DOTENV_PATH = find_dotenv(usecwd=True)
if not load_dotenv(DOTENV_PATH, override=True):
    print("⚠️ .env not found (proceeding with OS env only)", file=sys.stderr)


sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "test")
)

# --- ensure UTF-8-safe printing ---
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")  # strip ANSI if we ever fall back to text

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
        # No reliable file list without JSON, just return a compact summary
        return "\n".join(keep[-20:])
    except Exception as e:
        return f"[WARN] Could not parse deploy output ({e}). Raw:\n{strip_ansi(stdout or '')}"

NS = "http://soap.sforce.com/2006/04/metadata"

def delete_folder_tree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        print(f"[CLEAN] Deleted folder tree: {path}")

def purge_eml(path: Path):
    # Delete any accidental/outside-generated *.eml files
    for p in path.rglob("*.eml"):
        try:
            p.unlink()
            print(f"[CLEAN] Removed stray .eml: {p}")
        except Exception as e:
            print(f"[WARN] Could not remove {p}: {e}")


def to_safe_devname(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "_", name)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or not s[0].isalpha():
        s = "T_" + s if s else "T_Template"
    return s

def stage_templates(src_dir: Path, email_root: Path, folder: str, clean: bool) -> Path:
    out_dir = email_root / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        for p in out_dir.glob("*"):
            if p.is_file():
                p.unlink()

    # make sure no stray .eml files linger
    purge_eml(out_dir)
    purge_eml(email_root)
    purge_eml(src_dir)  # ← also purge .eml in the input directory

    files = sorted(src_dir.glob("*.emailTemplate-meta.xml"))
    SRC_SUFFIX = ".emailTemplate-meta.xml"

    processed: list[Path] = []  # ← track successfully staged input files

    for src in files:
        tree = ET.parse(src)
        root = tree.getroot()

        def g(tag: str):
            el = root.find(f"{{{NS}}}{tag}")
            return el.text if el is not None else None

        display_name   = g("name") or src.stem
        subject        = g("subject") or ""
        related_entity = g("relatedEntityType") or ""
        api_version    = g("apiVersion") or "59.0"
        html_value     = g("htmlValue") or ""
        html_body      = html.unescape(html_value)

        # Keep the filename/order exactly as generated by your formatter
        if not src.name.endswith(SRC_SUFFIX):
            print(f"[WARN] Skipping unexpected file: {src.name}")
            continue
        dev = src.name[:-len(SRC_SUFFIX)]  # e.g. Tent_Permit_1_Project_Submitted

        # 1) Body (*.email)
        (out_dir / f"{dev}.email").write_text(html_body, encoding="utf-8")

        # 2) Metadata (*.email-meta.xml) – Lightning (SFX) required fields
        meta = ET.Element("EmailTemplate", xmlns=NS)

        api_version    = g("apiVersion") or "59.0"
        incoming_active = (g("isActive") or g("available") or "true").strip().lower()  # default true if omitted
        is_active = incoming_active in ("true", "1", "yes")

        ET.SubElement(meta, "apiVersion").text = api_version
        ET.SubElement(meta, "description").text = g("description") or ""
        ET.SubElement(meta, "encodingKey").text = "UTF-8"
        ET.SubElement(meta, "style").text = "none"
        ET.SubElement(meta, "type").text = "custom"        # correct element name in metadata
        ET.SubElement(meta, "uiType").text = "SFX"         # Lightning template
        ET.SubElement(meta, "name").text = display_name
        ET.SubElement(meta, "available").text = "true" if is_active else "false"   # 👈 REQUIRED FOR ACTIVATION
        if related_entity:
            ET.SubElement(meta, "relatedEntityType").text = related_entity
        ET.SubElement(meta, "subject").text = subject or "change me"

        pretty = parseString(ET.tostring(meta, encoding="utf-8")).toprettyxml(indent="  ")
        (out_dir / f"{dev}.email-meta.xml").write_text(pretty, encoding="utf-8")

        print(f"[STAGED] {src.name} -> {dev}.email(+meta) in {folder}/")
        processed.append(src)  # ← mark this input file for deletion

    # 🔥 After staging, clean the input directory of processed files
    for p in processed:
        try:
            p.unlink()
            print(f"[CLEAN] Removed from input: {p.name}")
        except Exception as e:
            print(f"[WARN] Could not remove {p}: {e}")

    # Optionally remove the input folder if now empty
    try:
        next(src_dir.iterdir())
    except StopIteration:
        try:
            src_dir.rmdir()
            print(f"[CLEAN] Removed empty input folder: {src_dir}")
        except OSError:
            pass

    return out_dir



def find_sf_cli() -> str:
    return shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"


def deploy_flow(source_paths: list[Path], org_alias: str, dry_run: bool, compact: bool = True):
    cli = find_sf_cli()
    cmd = [cli, "project", "deploy", "start"]
    for p in source_paths:
        cmd += ["--source-dir", str(p)]
    cmd += ["--target-org", org_alias]
    if dry_run:
        cmd.append("--dry-run")

    # 👇 this collapses the noisy progress stream into one JSON result
    env = os.environ.copy()
    if compact:
        cmd += ["--json", "--wait", "5"]  # wait up to 5 minutes; adjust if you like
        env["TERM"] = "dumb"              # belt-and-suspenders: no TTY animations
        env["NO_COLOR"] = "1"             # drop color codes if any

    print("[INFO] Running:", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env)

def deploy(path: Path, org_alias: str, dry_run: bool):
    # wrap single-path deploy into the multi-path function
    return deploy_flow([path], org_alias, dry_run, compact=True)



def resolve_org_alias(cli_arg: str | None) -> str:
    # Prefer CLI flag; else env vars; require one of them
    env_alias = (os.getenv("SF_ORG_ALIAS") or os.getenv("SF_TARGET_ORG") or "").strip()
    alias = (cli_arg or env_alias or "").strip()
    if not alias:
        print("❌ No org alias. Pass --org or set SF_ORG_ALIAS in .env", file=sys.stderr)
        sys.exit(2)
    return alias

def main():
    ap = argparse.ArgumentParser(description="Stage and deploy Salesforce Email Templates (Lightning) only.")
    ap.add_argument("--src", default="generated_code_replaced",
                    help="Folder with *.emailTemplate-meta.xml (from your generator/replacer).")
    ap.add_argument("--email-root", default="force-app/main/default/email",
                    help="SFDX email root in your project.")
    ap.add_argument("--folder", default="unfiled$public",
                    help="Target Email Folder name (must exist in org).")
    ap.add_argument("--org", default=None, 
                    help="sf CLI alias for the target org.")
    ap.add_argument("--clean", action="store_true",
                    help="Clean the target email folder before staging.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Dry run deploy (no changes in org).")
    args = ap.parse_args()

    org_alias = resolve_org_alias(args.org)
    print(f"[INFO] Target org alias: {org_alias}")

    src_dir = Path(args.src)
    email_root = Path(args.email_root)

    delete_folder_tree(email_root / args.folder)
    out_dir = stage_templates(src_dir, email_root, args.folder, args.clean)

    print("[INFO] Deploying email templates…")
    res = deploy(out_dir, org_alias, args.dry_run)
    print(summarize_deploy(res.stdout, res.returncode))  # instead of print(res.stdout)
    if res.returncode == 0:
        print("[✅] Email templates deployment successful.")
    else:
        print("[❌] Deployment failed.")
        if res.stderr.strip():
            print(res.stderr)
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()

