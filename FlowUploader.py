#!/usr/bin/env python3
"""
upload_flow.py — Deploy a Flow artifact left by the downloader to a target org.

Adds optional auto-stub for missing fields:
  --autostub-fields -> on deploy failure, create Text(80) stub fields in target org and retry.

Also fixes common package.xml/filename mismatches for Flow members, and now
handles "$Record.<Field__c>" missing element errors by reading the Flow's ObjectType.

Usage:
  python upload_flow.py --org MyTarget --flow My_Flow --autostub-fields
"""

import os, re, json, argparse, shutil, subprocess, sys, io, zipfile, tempfile, xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple, List, Iterable, Set, Dict
from dotenv import load_dotenv, find_dotenv

# --- .env ---
DOTENV_PATH = find_dotenv(usecwd=True)
load_dotenv(DOTENV_PATH, override=True)

# --- UTF-8 console ---
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

# ----- version bump on duplicate ---------------------------------------------
_DUPLICATE_RE = re.compile(r"duplicate value found", re.I)

def get_next_flow_version(org_alias: str, flow_api: str) -> int:
    """
    Ask the target org for the latest version number of this FlowDefinition,
    return latest+1 (or 1 if it doesn't exist).
    """
    query = f"SELECT DeveloperName, LatestVersion.VersionNumber FROM FlowDefinition WHERE DeveloperName = '{flow_api}'"
    sf = which_sf()
    sfdx = which_sfdx()

    if sf:
        cp = subprocess.run(
            [sf, "data", "query", "--target-org", org_alias, "-q", query, "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8"
        )
    elif sfdx:
        cp = subprocess.run(
            [sfdx, "data:soql:query", "--targetusername", org_alias, "-q", query, "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8"
        )
    else:
        print("[WARN] No sf/sfdx CLI found; defaulting next version = 1")
        return 1

    try:
        data = json.loads(cp.stdout or "{}")
        res = data.get("result", data)
        records = res.get("records") or []
        if records:
            latest = ((records[0].get("LatestVersion") or {}).get("VersionNumber")) or 0
            nxt = int(latest) + 1
            print(f"[INFO] Next version for {flow_api} in {org_alias}: {nxt} (latest={latest})")
            return nxt
    except Exception:
        pass
    return 1


def _flow_stem_to_base(stem: str) -> Tuple[str, Optional[int]]:
    """'My_Flow-12' -> ('My_Flow', 12); 'My_Flow' -> ('My_Flow', None)"""
    m = re.match(r"^(?P<base>.+?)(?:-(?P<ver>\d+))?$", stem)
    if m:
        base = m.group("base")
        ver = m.group("ver")
        return base, (int(ver) if ver else None)
    return stem, None

def set_package_xml_flow_members_to(deploy_dir: Path, member_names: List[str]) -> None:
    """Rewrite package.xml 'Flow' members to the provided names."""
    pkg = deploy_dir / "package.xml"
    if not pkg.exists():
        found = list(deploy_dir.rglob("package.xml"))
        if not found:
            print("[WARN] package.xml not found; cannot set Flow members.", file=sys.stderr)
            return
        pkg = found[0]
    METANS = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", METANS)
    tree = ET.parse(pkg)
    root = tree.getroot()
    def q(tag: str) -> str: return f"{{{METANS}}}{tag}"

    flow_types = None
    for t in root.findall(q("types")):
        name_el = t.find(q("name"))
        if name_el is not None and (name_el.text or "").strip() == "Flow":
            flow_types = t
            break
    if flow_types is None:
        flow_types = ET.SubElement(root, q("types"))
        ET.SubElement(flow_types, q("name")).text = "Flow"

    for m in list(flow_types.findall(q("members"))):
        flow_types.remove(m)
    for name in member_names:
        ET.SubElement(flow_types, q("members")).text = name

    tree.write(pkg, encoding="utf-8", xml_declaration=True)

def bump_flow_versions_and_rewrite(deploy_dir: Path, org_alias: str) -> List[str]:
    """
    For each flows/*.flow file:
      - compute base name
      - ask target org for next version
      - rename file to base-<next>.flow
    Then rewrite package.xml Flow members accordingly.
    Returns list of new member names.
    """
    flows = sorted(deploy_dir.rglob("flows/*.flow"))
    if not flows:
        print("[WARN] No .flow files to bump.")
        return []
    new_members: List[str] = []
    for f in flows:
        base, _ = _flow_stem_to_base(f.stem)
        next_ver = get_next_flow_version(org_alias, base)
        new_name = f"{base}-{next_ver}.flow"
        new_path = f.with_name(new_name)
        if new_path != f:
            f.rename(new_path)
        new_members.append(new_path.stem)
        print(f"[INFO] Bumped {f.name} -> {new_name} (next={next_ver})")
    set_package_xml_flow_members_to(deploy_dir, new_members)
    return new_members


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s or "")

def which_sf() -> Optional[str]:
    p = shutil.which("sf")
    if p: return p
    cand = Path(r"C:\Program Files\sf\bin\sf.cmd")
    return str(cand) if cand.exists() else None

def which_sfdx() -> Optional[str]:
    p = shutil.which("sfdx")
    if p: return p
    cand = Path(r"C:\Program Files\sfdx\bin\sfdx.cmd")
    return str(cand) if cand.exists() else None

def resolve_org_alias(cli_arg: Optional[str]) -> str:
    env_alias = (os.getenv("SF_ORG_ALIAS") or os.getenv("SF_TARGET_ORG") or "").strip()
    alias = (cli_arg or env_alias).strip()
    if not alias:
        print("❌ No org alias. Pass --org or set SF_ORG_ALIAS / SF_TARGET_ORG in .env", file=sys.stderr)
        sys.exit(2)
    return alias

def supports_sf_deploy_metadata(sf_path: str) -> bool:
    try:
        p = subprocess.run([sf_path, "deploy", "metadata", "--help"], capture_output=True, text=True, encoding="utf-8")
        return p.returncode == 0 or "deploy metadata" in (p.stdout + p.stderr).lower()
    except Exception:
        return False

def supports_sf_metadata_deploy(sf_path: str) -> bool:
    try:
        p = subprocess.run([sf_path, "metadata", "deploy", "--help"], capture_output=True, text=True, encoding="utf-8")
        return p.returncode == 0 or "metadata deploy" in (p.stdout + p.stderr).lower()
    except Exception:
        return False

def run(cmd: list[str], *, json_wait: bool, wait_minutes: int) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    if json_wait:
        if "--json" not in cmd: cmd += ["--json"]
        if "--wait" not in cmd: cmd += ["--wait", str(wait_minutes)]
    print("[INFO]", " ".join(cmd))
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", env=env)

def summarize_deploy(stdout: str, returncode: int) -> str:
    try:
        data = json.loads(stdout or "{}")
        res = data.get("result", data)
        status = res.get("status") or res.get("state") or ("Succeeded" if returncode == 0 else "Failed")
        job_id = res.get("id") or res.get("deploymentId") or res.get("deployId") or res.get("jobId")
        paths = []
        ds = res.get("deployedSource") or []
        if isinstance(ds, dict): ds = [ds]
        for d in ds:
            p = d.get("filePath") or d.get("sourcePath") or d.get("path") or d.get("fullName")
            if p: paths.append(p)
        details = res.get("details") or {}
        succ = details.get("componentSuccesses")
        if succ:
            if isinstance(succ, dict): succ = [succ]
            for s in succ:
                p = s.get("fileName") or s.get("fullName")
                if p: paths.append(p)
        seen, uniq_paths = set(), []
        for p in paths:
            if p not in seen:
                seen.add(p); uniq_paths.append(p)
        fail = details.get("componentFailures") or []
        if isinstance(fail, dict): fail = [fail]
        parts = [f"Status: {status}"]
        if job_id: parts.append(f"ID: {job_id}")
        parts.append(f"Files: {len(uniq_paths)}")
        lines = [" | ".join(parts)]
        lines.append("Files deployed:" if uniq_paths else "Files deployed: (none)")
        for p in uniq_paths:
            lines.append(f" - {p}")
        for f in fail[:5]:
            path = f.get("fileName") or f.get("fullName") or "?"
            msg = f.get("problem") or f.get("message") or "Failed"
            lines.append(f" - {path}: {msg}")
        return "\n".join(lines)
    except Exception as e:
        text = strip_ansi(stdout)
        tail = "\n".join(text.splitlines()[-25:])
        return f"[WARN] Could not parse JSON output ({e}). Tail of output:\n{tail}"

# ----- package.xml normalization (Flow members) ------------------------------
def normalize_package_xml_flows(deploy_dir: Path) -> None:
    """
    Ensure package.xml Flow members match actual files in ./flows.
    - If files are unversioned (My_Flow.flow), members become 'My_Flow'.
    - If files include versions (My_Flow-1.flow), members become 'My_Flow-1'.
    """
    pkg = deploy_dir / "package.xml"
    if not pkg.exists():
        found = list(deploy_dir.rglob("package.xml"))
        if not found:
            print("[WARN] package.xml not found to normalize.", file=sys.stderr)
            return
        pkg = found[0]

    flows_dir = deploy_dir / "flows"
    if not flows_dir.exists():
        flows_dir = pkg.parent / "flows"
        if not flows_dir.exists():
            print("[INFO] No flows/ directory next to package.xml; skipping normalization.")
            return

    flow_files = sorted(flows_dir.glob("*.flow"))
    if not flow_files:
        print("[INFO] No *.flow files found; skipping normalization.")
        return

    members_from_files = [f.stem for f in flow_files]  # e.g., My_Flow or My_Flow-1
    print(f"[INFO] Normalizing package.xml Flow members to match files: {members_from_files}")

    METANS = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", METANS)
    tree = ET.parse(pkg)
    root = tree.getroot()
    def q(tag: str) -> str: return f"{{{METANS}}}{tag}"

    flow_types = None
    for t in root.findall(q("types")):
        name_el = t.find(q("name"))
        if name_el is not None and (name_el.text or "").strip() == "Flow":
            flow_types = t
            break
    if flow_types is None:
        flow_types = ET.SubElement(root, q("types"))
        ET.SubElement(flow_types, q("name")).text = "Flow"

    for m in list(flow_types.findall(q("members"))):
        flow_types.remove(m)
    for m in members_from_files:
        ET.SubElement(flow_types, q("members")).text = m

    tree.write(pkg, encoding="utf-8", xml_declaration=True)

# ----- Flow object discovery --------------------------------------------------
FLOW_NS = "http://soap.sforce.com/2006/04/metadata"
def _q(tag: str) -> str: return f"{{{FLOW_NS}}}{tag}"

def get_flow_object_types(deploy_dir: Path) -> Dict[Path, Optional[str]]:
    """
    Scan all .flow files under deploy_dir and return {flow_path: object_api or None}.
    We look for processMetadataValues[name='ObjectType']/value/stringValue.
    """
    obj_by_file: Dict[Path, Optional[str]] = {}
    for flow_path in deploy_dir.rglob("flows/*.flow"):
        try:
            tree = ET.parse(flow_path)
            root = tree.getroot()
            obj_api = None
            for pmv in root.findall(_q("processMetadataValues")):
                name_el = pmv.find(_q("name"))
                if name_el is not None and (name_el.text or "").strip() == "ObjectType":
                    val = pmv.find(_q("value"))
                    if val is not None:
                        # value may contain <stringValue> under the same namespace
                        sv = val.find(_q("stringValue"))
                        if sv is None:
                            # some orgs export as <value>text</value>
                            obj_api = (val.text or "").strip() or None
                        else:
                            obj_api = (sv.text or "").strip() or None
                    break
            obj_by_file[flow_path] = obj_api
        except Exception:
            obj_by_file[flow_path] = None
    return obj_by_file

def pick_single_flow_object(deploy_dir: Path) -> Optional[str]:
    """If exactly one object type is discovered across flows, return it; else None."""
    objs = {obj for obj in get_flow_object_types(deploy_dir).values() if obj}
    if len(objs) == 1:
        return next(iter(objs))
    return None

# ----- detect missing fields & auto-stub -------------------------------------
_MISSING_PATTERNS = [
    # The field "Department__c" for the object "Obj__c" doesn't exist.
    re.compile(r'The field\s+"(?P<field>[A-Za-z0-9_]+)"\s+for the object\s+"(?P<object>[A-Za-z0-9_]+)"\s+doesn\'t exist', re.I),
    # No such column 'Department__c' on sobject 'Obj__c'
    re.compile(r"No such column\s+'(?P<field>[A-Za-z0-9_]+)'\s+on sobject\s+'(?P<object>[A-Za-z0-9_]+)'", re.I),
    # Can't find field 'Department__c' on object 'Obj__c'
    re.compile(r"Can't find field\s+'(?P<field>[A-Za-z0-9_]+)'\s+on object\s+'(?P<object>[A-Za-z0-9_]+)'", re.I),
    # The "$Record.Applicant_Email__c" element doesn't exist. (or $Record__Prior)
    re.compile(r'The\s+"(?:\$Record|\\?\$Record(?:__Prior)?)\.(?P<field>[A-Za-z0-9_]+)"\s+element\s+doesn\'t exist', re.I),
]

def parse_missing_fields(text: str) -> Set[Tuple[Optional[str], str]]:
    """
    Returns a set of (object_api or None, field_api).
    For $Record.<field>, object_api will be None here; we'll enrich from the Flow XML later.
    """
    found: Set[Tuple[Optional[str], str]] = set()
    for line in (text or "").splitlines():
        for pat in _MISSING_PATTERNS:
            m = pat.search(line)
            if m:
                fld = m.group("field")
                obj = m.groupdict().get("object")
                # basic sanity
                if fld and fld.endswith("__c"):
                    if obj and not obj.endswith("__c"):
                        # ignore non-custom objects (can't create fields there via metadata)
                        continue
                    found.add((obj, fld))
    return found

def extract_missing_fields_from_result(stdout: str, stderr: str) -> Set[Tuple[Optional[str], str]]:
    # Try JSON first
    try:
        data = json.loads(stdout or "{}")
        res = data.get("result", data)
        details = res.get("details") or {}
        fails = details.get("componentFailures") or []
        if isinstance(fails, dict): fails = [fails]
        all_msgs = []
        for f in fails:
            msg = f.get("problem") or f.get("message") or ""
            if msg: all_msgs.append(msg)
        text = "\n".join(all_msgs)
        miss = parse_missing_fields(text)
        if miss: return miss
    except Exception:
        pass
    # Fallback to raw text
    return parse_missing_fields(strip_ansi((stdout or "") + "\n" + (stderr or "")))

def enrich_missing_with_flow_object(missing: Set[Tuple[Optional[str], str]], deploy_dir: Path) -> Set[Tuple[str, str]]:
    """
    If any items have object=None (from $Record.<Field__c>), try to fill with the Flow's ObjectType.
    If multiple flows/objects found, leave those entries unresolved (they won't be stubbed).
    """
    resolved: Set[Tuple[str, str]] = set()
    single_obj = pick_single_flow_object(deploy_dir)
    for obj, fld in missing:
        if obj:
            resolved.add((obj, fld))
        else:
            if single_obj:
                resolved.add((single_obj, fld))
    return resolved

def write_stub_field(object_api: str, field_api: str, root: Path, *, length: int = 80) -> Path:
    """Create DX metadata for a Text stub field."""
    fld_dir = root / "objects" / object_api / "fields"
    fld_dir.mkdir(parents=True, exist_ok=True)
    label = field_api[:-3] if field_api.endswith("__c") else field_api
    label = label.replace("_", " ").strip() or field_api
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">
  <fullName>{field_api}</fullName>
  <label>{label}</label>
  <type>Text</type>
  <length>{length}</length>
  <required>false</required>
  <unique>false</unique>
</CustomField>
"""
    path = fld_dir / f"{field_api}.field-meta.xml"
    path.write_text(xml, encoding="utf-8")
    return path

def deploy_stub_fields(stubs: Iterable[Tuple[str, str]], org_alias: str, *, wait_minutes: int) -> subprocess.CompletedProcess:
    """Generate and deploy stub fields as DX source via sf project deploy start."""
    temp_root = Path(tempfile.mkdtemp(prefix="stub_fields_"))
    for obj, fld in stubs:
        write_stub_field(obj, fld, temp_root, length=80)

    print(f"[INFO] Creating stub fields: {', '.join(f'{o}.{f}' for o, f in stubs)}")
    sf = which_sf()
    sfdx = which_sfdx()

    if sf:
        cmd = [sf, "project", "deploy", "start", "--source-dir", str(temp_root), "--target-org", org_alias]
        return run(cmd, json_wait=True, wait_minutes=wait_minutes)

    if sfdx:
        cmd = [sfdx, "force:source:deploy", "--sourcepath", str(temp_root), "--targetusername", org_alias]
        return run(cmd, json_wait=True, wait_minutes=wait_minutes)

    return subprocess.CompletedProcess(args=[], returncode=127, stdout="", stderr="No Salesforce CLI found (sf/sfdx)")

# ----- artifact selection -----------------------------------------------------
def choose_artifact(out_dir: Path, flow_name: Optional[str]) -> Tuple[str, Path, Optional[Path]]:
    if not out_dir.exists():
        raise SystemExit(f"❌ No ./out directory found at {out_dir.resolve()}")
    dirs: List[Path] = [p for p in out_dir.iterdir() if p.is_dir()]
    zips: List[Path] = list(out_dir.glob("*.zip"))
    if flow_name:
        prefix = f"{flow_name}-"
        dirs = [d for d in dirs if d.name.startswith(prefix)]
        zips = [z for z in zips if z.stem.startswith(prefix)]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    zips.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for d in dirs:
        if (d / "package.xml").exists() or list(d.rglob("package.xml")):
            return "dir", d, d
    if zips:
        return "zip", zips[0], None
    hint = f" for flow '{flow_name}'" if flow_name else ""
    raise SystemExit(f"❌ No deployable artifacts found in {out_dir}{hint}.")

# ----- deploy metadata dir via CLI -------------------------------------------
def deploy_metadata_dir(md_dir: Path, org_alias: str, *, check_only: bool, wait_minutes: int) -> subprocess.CompletedProcess:
    sf = which_sf()
    sfdx = which_sfdx()
    if sf and supports_sf_deploy_metadata(sf):
        cmd = [sf, "deploy", "metadata", "--metadata-dir", str(md_dir), "--target-org", org_alias]
        if check_only: cmd.append("--check-only")
        return run(cmd, json_wait=True, wait_minutes=wait_minutes)
    if sf and supports_sf_metadata_deploy(sf):
        cmd = [sf, "metadata", "deploy", "--metadata-dir", str(md_dir), "--target-org", org_alias]
        if check_only: cmd.append("--check-only")
        return run(cmd, json_wait=True, wait_minutes=wait_minutes)
    if sfdx:
        cmd = [sfdx, "force:mdapi:deploy", "--deploydir", str(md_dir), "--targetusername", org_alias]
        if check_only: cmd.append("--checkonly")
        return run(cmd, json_wait=True, wait_minutes=wait_minutes)
    return subprocess.CompletedProcess(args=[], returncode=127, stdout="", stderr="No Salesforce CLI found (sf/sfdx)")

# ----- main ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Deploy a Flow artifact from ./out to a target org.")
    ap.add_argument("--org", help="sf/sfdx org alias (overrides .env SF_ORG_ALIAS / SF_TARGET_ORG)")
    ap.add_argument("--flow", help="Flow API name to select the artifact (prefix match like <Flow>-<ver>)")
    ap.add_argument("--dry-run", action="store_true", help="Check-only deploy")
    ap.add_argument("--wait", type=int, default=10, help="Minutes to wait for the deploy (default: 10)")
    ap.add_argument("--keep", action="store_true", help="Keep the deployed folder after success")
    ap.add_argument("--autostub-fields", action="store_true",
                    help="If deploy fails due to missing CustomField(s), create Text(80) stubs and retry.")
    args = ap.parse_args()

    org_alias = resolve_org_alias(args.org)
    out_dir = Path("out")
    kind, path, cleanup_dir = choose_artifact(out_dir, args.flow)

    # Prepare deploy dir (extract if we only have a ZIP)
    if kind == "zip":
        tmp = Path(tempfile.mkdtemp(prefix="mdapi_extract_"))
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(tmp)
        if (tmp / "package.xml").exists():
            deploy_dir = tmp
        elif (tmp / "unpackaged" / "package.xml").exists():
            deploy_dir = tmp / "unpackaged"
        else:
            found = list(tmp.rglob("package.xml"))
            if not found:
                shutil.rmtree(tmp, ignore_errors=True)
                raise SystemExit("❌ package.xml not found inside ZIP after extraction.")
            deploy_dir = found[0].parent
        cleanup_dir = tmp
        print(f"[INFO] Using extracted directory: {deploy_dir}")
    else:
        deploy_dir = path
        print(f"[INFO] Using existing directory: {deploy_dir}")

    # Align Flow members with files
    normalize_package_xml_flows(deploy_dir)

    print(f"[INFO] Target org alias: {org_alias}")
    print(f"[INFO] Deploying from: {deploy_dir}")

    # --- First attempt
    res = deploy_metadata_dir(deploy_dir, org_alias, check_only=args.dry_run, wait_minutes=args.wait)
    summary = summarize_deploy(res.stdout, res.returncode)
    print(summary)

    # --- If failed, optionally auto-stub missing fields and retry
    if res.returncode != 0 and args.autostub_fields and not args.dry_run:
        missing_raw = extract_missing_fields_from_result(res.stdout, res.stderr)
        if missing_raw:
            # Fill in object for $Record.<field> using Flow's ObjectType when unambiguous
            missing = enrich_missing_with_flow_object(missing_raw, deploy_dir)
            if missing:
                print(f"[INFO] Missing fields detected: {', '.join(f'{o}.{f}' for o, f in missing)}")
                stub_res = deploy_stub_fields(missing, org_alias, wait_minutes=args.wait)
                stub_summary = summarize_deploy(stub_res.stdout, stub_res.returncode)
                print(stub_summary)
                if stub_res.returncode == 0:
                    print("[INFO] Retrying flow deployment after stubbing fields…")
                    res = deploy_metadata_dir(deploy_dir, org_alias, check_only=False, wait_minutes=args.wait)
                    summary = summarize_deploy(res.stdout, res.returncode)
                    print(summary)
                else:
                    print("[WARN] Stub field deployment failed; not retrying flow.")
            else:
                print("[INFO] Could not resolve object for $Record.<field>; skipping auto-stub.")
        else:
            print("[INFO] No parsable 'missing field' errors found; not attempting stubs.")

    # --- If still failing, handle duplicate-version: bump version and retry once
    if res.returncode != 0 and not args.dry_run:
        if _DUPLICATE_RE.search(strip_ansi((res.stdout or "") + (res.stderr or ""))):
            print("[INFO] Duplicate version detected. Bumping Flow version and retrying …")
            bump_flow_versions_and_rewrite(deploy_dir, org_alias)
            res = deploy_metadata_dir(deploy_dir, org_alias, check_only=False, wait_minutes=args.wait)
            summary = summarize_deploy(res.stdout, res.returncode)
            print(summary)

    # --- Final outcome / cleanup
    if res.returncode == 0:
        print("[✅] Deployment successful.")
        if cleanup_dir and not args.keep:
            try:
                safe_parents = {out_dir.resolve(), Path(tempfile.gettempdir()).resolve()}
                if cleanup_dir.resolve().parent.resolve() in safe_parents or cleanup_dir.resolve() in safe_parents:
                    shutil.rmtree(cleanup_dir, ignore_errors=True)
                    print(f"[🧹] Cleaned: {cleanup_dir}")
                else:
                    print(f"[WARN] Skipping cleanup outside allowed locations: {cleanup_dir}")
            except Exception as e:
                print(f"[WARN] Cleanup failed for {cleanup_dir}: {e}")
    else:
        print("[❌] Deployment failed.")
        if res.stderr.strip():
            print(res.stderr)
        sys.exit(res.returncode)


if __name__ == "__main__":
    main()
