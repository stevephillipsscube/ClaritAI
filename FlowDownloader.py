#!/usr/bin/env python3
import argparse
import base64
import io
import os
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Tuple, Optional, Set, DefaultDict
from collections import defaultdict

import requests
from simple_salesforce import Salesforce
from dotenv import load_dotenv, find_dotenv

FLOW_NS = "http://soap.sforce.com/2006/04/metadata"
def _q(tag: str) -> str: return f"{{{FLOW_NS}}}{tag}"

# ---------- env + console ----------
DOTENV_PATH = find_dotenv(usecwd=True)
load_dotenv(DOTENV_PATH, override=True)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    import io as _io
    sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------- helpers ----------
def getenv_any(names: Tuple[str, ...], *, default: Optional[str]=None, required=False) -> Optional[str]:
    for n in names:
        v = os.getenv(n)
        if v: return v
    if required and default is None:
        raise SystemExit(f"❌ Missing required environment variable: {' or '.join(names)}")
    return default

def connect_sf(username: str, password: str, token: str, domain: str) -> Tuple[str, str]:
    sf = Salesforce(username=username, password=password, security_token=token, domain=domain)
    return sf.session_id, f"https://{sf.sf_instance}"

def metadata_endpoint(base_url: str, api_version: str) -> str:
    return f"{base_url}/services/Soap/m/{api_version}"

def tooling_query(base_url: str, session_id: str, api_version: str, soql: str) -> Dict:
    url = f"{base_url}/services/data/v{api_version}/tooling/query"
    headers = {"Authorization": f"Bearer {session_id}"}
    resp = requests.get(url, params={"q": soql}, headers=headers, timeout=60)
    if resp.status_code >= 300:
        raise SystemExit(f"❌ Tooling query HTTP {resp.status_code}: {resp.text[:1000]}")
    return resp.json()

def _soap_post(url: str, envelope_xml: str) -> requests.Response:
    headers = {"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": "''"}
    return requests.post(url, data=envelope_xml.encode("utf-8"), headers=headers, timeout=120, verify=True)

def _xml_find_text(root: ET.Element, path: str, ns: dict) -> str:
    el = root.find(path, ns)
    return el.text if el is not None else ""

def _extract_fault(resp_text: str) -> str:
    try:
        root = ET.fromstring(resp_text)
        ns = {"soapenv": "http://schemas.xmlsoap.org/soap/envelope/"}
        fault = root.find(".//soapenv:Fault", ns)
        if fault is not None:
            code = fault.findtext("faultcode") or ""
            string = fault.findtext("faultstring") or ""
            return f"{code}: {string}"
    except Exception:
        pass
    return resp_text[:1000]

# ---------- retrieve ----------
def resolve_flow_version(base_url: str, session_id: str, api_version: str, flow_api: str) -> int:
    soql = ("SELECT DeveloperName, LatestVersion.VersionNumber, ActiveVersion.VersionNumber "
            f"FROM FlowDefinition WHERE DeveloperName = '{flow_api}'")
    data = tooling_query(base_url, session_id, api_version, soql)
    if not data.get("records"):
        raise SystemExit(f"❌ FlowDefinition not found for DeveloperName='{flow_api}'.")
    rec = data["records"][0]
    latest = (rec.get("LatestVersion") or {}).get("VersionNumber")
    active = (rec.get("ActiveVersion") or {}).get("VersionNumber")
    ver = latest or active
    if not ver:
        raise SystemExit("❌ Could not resolve a version (no Latest/Active).")
    return int(ver)

def start_retrieve_flow(md_url: str, session_id: str, api_version: str, flow_member: str) -> str:
    envelope = f"""<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
  <env:Header>
    <m:SessionHeader xmlns:m="http://soap.sforce.com/2006/04/metadata">
      <m:sessionId>{session_id}</m:sessionId>
    </m:SessionHeader>
  </env:Header>
  <env:Body>
    <m:retrieve xmlns:m="http://soap.sforce.com/2006/04/metadata">
      <m:retrieveRequest>
        <m:apiVersion>{api_version}</m:apiVersion>
        <m:singlePackage>true</m:singlePackage>
        <m:unpackaged>
          <m:types>
            <m:members>{flow_member}</m:members>
            <m:name>Flow</m:name>
          </m:types>
          <m:version>{api_version}</m:version>
        </m:unpackaged>
      </m:retrieveRequest>
    </m:retrieve>
  </env:Body>
</env:Envelope>""".strip()
    resp = _soap_post(md_url, envelope)
    if resp.status_code >= 300:
        raise SystemExit(f"❌ retrieve() HTTP {resp.status_code}:\n{_extract_fault(resp.text)}")
    ns = {"soapenv": "http://schemas.xmlsoap.org/soap/envelope/", "m": "http://soap.sforce.com/2006/04/metadata"}
    root = ET.fromstring(resp.text)
    rid = _xml_find_text(root, ".//m:result/m:id", ns)
    if not rid:
        raise SystemExit(f"❌ retrieve() had no id.\n{_extract_fault(resp.text)}")
    return rid

def poll_retrieve(md_url: str, session_id: str, request_id: str, timeout_sec=300, interval_sec=2.0) -> bytes:
    t0 = time.time()
    ns = {"soapenv": "http://schemas.xmlsoap.org/soap/envelope/", "m": "http://soap.sforce.com/2006/04/metadata"}
    while True:
        env = f"""<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
  <env:Header>
    <m:SessionHeader xmlns:m="http://soap.sforce.com/2006/04/metadata">
      <m:sessionId>{session_id}</m:sessionId>
    </m:SessionHeader>
  </env:Header>
  <env:Body>
    <m:checkRetrieveStatus xmlns:m="http://soap.sforce.com/2006/04/metadata">
      <m:asyncProcessId>{request_id}</m:asyncProcessId>
      <m:includeZip>true</m:includeZip>
    </m:checkRetrieveStatus>
  </env:Body>
</env:Envelope>""".strip()
        resp = _soap_post(md_url, env)
        if resp.status_code >= 300:
            raise SystemExit(f"❌ checkRetrieveStatus HTTP {resp.status_code}:\n{_extract_fault(resp.text)}")
        root = ET.fromstring(resp.text)
        done = _xml_find_text(root, ".//m:result/m:done", ns).lower() == "true"
        status = _xml_find_text(root, ".//m:result/m:status", ns)
        if done:
            if status == "Succeeded":
                z64 = _xml_find_text(root, ".//m:result/m:zipFile", ns)
                if not z64: raise SystemExit("❌ Retrieve OK but zipFile empty.")
                return base64.b64decode(z64)
            else:
                msg = _xml_find_text(root, ".//m:result/m:errorMessage", ns) or status
                raise SystemExit(f"❌ Retrieve failed: {msg}")
        if time.time() - t0 > timeout_sec:
            raise SystemExit("❌ Timeout waiting for retrieve.")
        time.sleep(interval_sec)

# ---------- dependency scraping ----------
_REC_FIELD_RE = re.compile(r"\$(?:Record|Record__Prior)\.([A-Za-z0-9_]+)")

def parse_flow_deps(flow_xml: bytes) -> Tuple[Set[str], Set[str]]:
    """
    Returns (objects, custom_fields) where:
      - objects: object API names referenced (from ObjectType and <object> tags)
      - custom_fields: field API names (Custom fields __c only)
    """
    objects: Set[str] = set()
    fields: Set[str] = set()

    # 1) regex for $Record.* (fast)
    for m in _REC_FIELD_RE.finditer(flow_xml.decode("utf-8", errors="replace")):
        fld = m.group(1)
        if fld.endswith("__c"):
            fields.add(fld)

    # 2) XML parse for ObjectType + <object> and <field> tags
    try:
        root = ET.fromstring(flow_xml)

        # processMetadataValues -> ObjectType
        for pmv in root.findall(_q("processMetadataValues")):
            if (pmv.findtext(_q("name")) or "").strip() == "ObjectType":
                val = pmv.find(_q("value"))
                sv = val.findtext(_q("stringValue")) if val is not None else None
                obj = (sv or (val.text if val is not None else "") or "").strip()
                if obj:
                    objects.add(obj)

        # Any <object> tags inside the flow (record lookups/updates/creates etc.)
        for obj_el in root.findall(".//" + _q("object")):
            txt = (obj_el.text or "").strip()
            if txt:
                objects.add(txt)

        # Any <field> tags (assignments, filters, inputs)
        for f_el in root.findall(".//" + _q("field")):
            txt = (f_el.text or "").strip()
            if txt.endswith("__c"):
                fields.add(txt)

    except Exception:
        pass

    return objects, fields

def write_dx_field(object_api: str, field_def: dict, root: Path) -> Path:
    fld = field_def["name"]
    label = field_def.get("label") or fld
    ftype = field_def.get("type")
    length = field_def.get("length") or 80
    prec = field_def.get("precision") or 18
    scale = field_def.get("scale") or 0
    pick = field_def.get("picklistValues") or []

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<CustomField xmlns="http://soap.sforce.com/2006/04/metadata">',
        f'  <fullName>{fld}</fullName>',
        f'  <label>{label}</label>',
    ]

    def write(out_lines: list[str]) -> Path:
        xml = "\n".join(lines + out_lines + ["</CustomField>\n"])
        out = root / "objects" / object_api / "fields" / f"{fld}.field-meta.xml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(xml, encoding="utf-8")
        return out

    if ftype == "picklist":
        extra = [
            "  <type>Picklist</type>",
            "  <valueSet>",
            "    <valueSetDefinition>",
        ]
        for v in pick:
            if v.get("value"):
                extra += [
                    "      <value>",
                    f"        <fullName>{v['value']}</fullName>",
                    f"        <default>{str(v.get('defaultValue', False)).lower()}</default>",
                    "      </value>",
                ]
        extra += [
            "      <restricted>false</restricted>",
            "    </valueSetDefinition>",
            "  </valueSet>",
        ]
        return write(extra)

    tmap = {
        "string":   ["  <type>Text</type>",           f"  <length>{length}</length>", "  <required>false</required>"],
        "double":   ["  <type>Number</type>",         f"  <precision>{prec}</precision>", f"  <scale>{scale}</scale>"],
        "boolean":  ["  <type>Checkbox</type>",       "  <defaultValue>false</defaultValue>"],
        "date":     ["  <type>Date</type>"],
        "datetime": ["  <type>DateTime</type>"],
        "email":    ["  <type>Email</type>"],
        "phone":    ["  <type>Phone</type>"],
        "currency": ["  <type>Currency</type>",      f"  <precision>{prec}</precision>", f"  <scale>{scale}</scale>"],
        "url":      ["  <type>Url</type>"],
        # fallback below
    }
    if ftype in tmap:
        return write(tmap[ftype])
    # fallback: Text
    return write(["  <type>Text</type>", f"  <length>{length}</length>"])

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description="Download a Salesforce Flow as a ZIP.")
    ap.add_argument("--flow", required=True, help="Flow DeveloperName (API name), e.g., My_Flow")
    ap.add_argument("--version", type=int, default=None, help="Flow version; if omitted, resolves LatestVersion")
    ap.add_argument("--api-version", default=os.getenv("SF_API_VERSION", "60.0"))
    ap.add_argument("--out", default=None, help="Output ZIP path (default: out/<Flow>-<ver>.zip)")
    ap.add_argument("--extract", action="store_true", help="Also extract ZIP to a folder")
    ap.add_argument("--with-fields", action="store_true",
                    help="Also export DX metadata for custom fields the Flow uses (deps_dx/).")
    args = ap.parse_args()

    # creds (SOURCE_* preferred; fallback to SF_*)
    user = getenv_any(("SOURCE_SF_USERNAME","SF_USERNAME"), required=True)
    pwd  = getenv_any(("SOURCE_SF_PASSWORD","SF_PASSWORD"), required=True)
    tok  = getenv_any(("SOURCE_SF_SECURITY_TOKEN","SF_SECURITY_TOKEN"), required=True)
    dom  = getenv_any(("SOURCE_SF_DOMAIN","SF_DOMAIN"), default="test")

    print("🔐 Logging into SOURCE…")
    session_id, base_url = connect_sf(user, pwd, tok, dom)
    print(f"   OK: {base_url}")

    ver = args.version
    if ver is None:
        print(f"🔎 Resolving latest version for '{args.flow}'…")
        ver = resolve_flow_version(base_url, session_id, args.api_version, args.flow)
        print(f"   Using version: {ver}")

    md_url = metadata_endpoint(base_url, args.api_version)

    # try with version first, then fallback to unversioned if no explicit --version
    try_members = [f"{args.flow}-{ver}"]
    if args.version is None:
        try_members.append(args.flow)

    zip_bytes = None
    last_err = None
    for member in try_members:
        try:
            print(f"📦 Retrieving Flow member: {member}")
            rid = start_retrieve_flow(md_url, session_id, args.api_version, member)
            print(f"   requestId: {rid}")
            print("⏳ Waiting…")
            zb = poll_retrieve(md_url, session_id, rid)
            zip_bytes = zb
            break
        except SystemExit as e:
            last_err = str(e)
            print(f"⚠️  Attempt for '{member}' failed:\n{last_err}", file=sys.stderr)
    if zip_bytes is None:
        raise SystemExit(f"❌ All retrieve attempts failed.\n{last_err}")

    out_dir = Path(args.out).parent if args.out else Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_base = try_members[0] if len(try_members)==1 else try_members[-1]
    out_zip = Path(args.out) if args.out else out_dir / f"{out_base}.zip"
    out_zip.write_bytes(zip_bytes)
    print(f"💾 Saved: {out_zip}")

    deps_root: Optional[Path] = None

    # ---- dependency extraction (DX fields) ----
    if args.with_fields:
        # collect per-object fields across all .flow files in the ZIP
        obj_to_fields: DefaultDict[str, Set[str]] = defaultdict(set)
        candidate_objects: Set[str] = set()

        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            flow_entries = [n for n in zf.namelist()
                            if n.lower().startswith("flows/") and n.lower().endswith(".flow")]
            if not flow_entries:
                print("ℹ️  No flows/*.flow in ZIP; skipping --with-fields")
            else:
                for name in flow_entries:
                    flow_xml = zf.read(name)
                    objs, flds = parse_flow_deps(flow_xml)
                    candidate_objects |= objs
                    # If exactly one object discovered, associate all fields to it.
                    if len(objs) == 1:
                        obj = next(iter(objs))
                        for f in flds:
                            obj_to_fields[obj].add(f)
                    else:
                        # Ambiguous: store fields unresolved for now; try assign later
                        for f in flds:
                            obj_to_fields["__UNRESOLVED__"].add(f)

        # If unresolved but only one object overall, assign them to that object
        unresolved = obj_to_fields.pop("__UNRESOLVED__", set())
        if unresolved:
            if len(candidate_objects) == 1:
                only = next(iter(candidate_objects))
                obj_to_fields[only] |= unresolved
            else:
                print(f"ℹ️  Unresolved $Record.* fields ({len(unresolved)}) with multiple/unknown objects; skipping those.")

        if obj_to_fields:
            # connect SF to describe objects
            sf = Salesforce(username=user, password=pwd, security_token=tok, domain=dom)
            deps_root = out_dir / out_base / "deps_dx"
            count_written = 0
            for obj, fields in obj_to_fields.items():
                if not obj or obj == "__UNRESOLVED__":  # safety
                    continue
                try:
                    desc = sf.restful(f"sobjects/{obj}/describe")
                    by_name = {f["name"]: f for f in desc.get("fields", [])}
                except Exception as e:
                    print(f"⚠️  Describe failed for {obj}: {e}")
                    continue
                wanted = [by_name[f] for f in sorted(fields) if f in by_name]
                if not wanted:
                    continue
                for fd in wanted:
                    write_dx_field(obj, fd, deps_root)
                    count_written += 1
            if count_written:
                print(f"🧩 Wrote {count_written} field definition(s) under {deps_root}")
            else:
                print("ℹ️  No matching custom fields found from describe; nothing written.")

    # ---- optional extract ----
    if args.extract:
        extract_dir = out_dir / out_base
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "r") as zf:
            zf.extractall(extract_dir)
        print(f"📂 Extracted to: {extract_dir}")

    print("✅ Done.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
