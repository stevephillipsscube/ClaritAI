#!/usr/bin/env python3
import re, sys, os, io, argparse, xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

# optional: simple-salesforce check
try:
    from simple_salesforce import Salesforce
except Exception:
    Salesforce = None

FLOW_NS = "http://soap.sforce.com/2006/04/metadata"
def q(tag): return f"{{{FLOW_NS}}}{tag}"

# regexes for quick scraping
RE_RECORD_FIELD = re.compile(r"(?:\$(?:Record|Record__Prior))\.(?P<field>[A-Za-z0-9_]+)")
RE_LABEL        = re.compile(r"\$Label\.(?P<label>[A-Za-z0-9_.]+)")

def text_of(el):
    return (el.text or "").strip() if el is not None else ""

def parse_flow(flow_path: Path):
    tree = ET.parse(flow_path)
    root = tree.getroot()

    # Object type (start element context) from processMetadataValues
    object_type = None
    for pmv in root.findall(q("processMetadataValues")):
        name = text_of(pmv.find(q("name")))
        if name == "ObjectType":
            v = pmv.find(q("value"))
            sv = v.find(q("stringValue")) if v is not None else None
            object_type = (text_of(sv) if sv is not None else text_of(v)) or None
            break

    # Collect raw text to scrape $Record and $Label
    raw = ET.tostring(root, encoding="utf-8").decode("utf-8", errors="replace")
    record_fields = sorted(set(m.group("field") for m in RE_RECORD_FIELD.finditer(raw)))
    labels        = sorted(set(m.group("label") for m in RE_LABEL.finditer(raw)))

    # Common structural dependencies
    sobjects = set()
    apex = set()
    subflows = set()
    email_templates = set()

    # Any <object> tags (record lookups/creates/updates often have them)
    for el in root.findall(".//" + q("object")):
        if text_of(el):
            sobjects.add(text_of(el))

    # A few apex/subflow tag names seen in Flow metadata
    for el in root.findall(".//" + q("apexPluginCalls")) + root.findall(".//" + q("apexCall")) + root.findall(".//" + q("apexAction")):
        # try various fields
        for name_tag in ("apexClass", "apexClassName", "name", "actionName"):
            n = el.find(q(name_tag))
            if n is not None and text_of(n):
                apex.add(text_of(n))

    for el in root.findall(".//" + q("subflows")) + root.findall(".//" + q("flowSubflow")):
        n = el.find(q("flowName")) or el.find(q("name"))
        if n is not None and text_of(n):
            subflows.add(text_of(n))

    # Email templates sometimes appear as <template> or in action params
    for el in root.findall(".//" + q("template")):
        if text_of(el):
            email_templates.add(text_of(el))
    # loose scan for “EmailTemplate” names
    for m in re.finditer(r"EmailTemplate(?:Name|Id)?[\"'>:\s]+([A-Za-z0-9_]+)", raw):
        email_templates.add(m.group(1))

    return {
        "flow_file": str(flow_path),
        "object_type": object_type,
        "record_fields": record_fields,   # from $Record.*
        "sobjects": sorted(sobjects),
        "apex": sorted(apex),
        "subflows": sorted(subflows),
        "labels": labels,
        "email_templates": sorted(email_templates),
    }

def check_missing_fields(report, sf):
    """Return list of 'Object.Field' that are missing in target org."""
    obj = report["object_type"]
    if not obj or not report["record_fields"] or sf is None:
        return []

    try:
        desc = sf.restful(f"sobjects/{obj}/describe")
        have = {f["name"] for f in desc.get("fields", [])}
    except Exception as e:
        print(f"[WARN] describe({obj}) failed: {e}", file=sys.stderr)
        return []

    missing = []
    for fld in report["record_fields"]:
        if fld not in have:
            missing.append(f"{obj}.{fld}")
    return missing

def main():
    ap = argparse.ArgumentParser(description="Scan a Flow (.flow) for dependencies.")
    ap.add_argument("--file", required=True, help="Path to *.flow")
    ap.add_argument("--check", action="store_true", help="Check missing $Record fields using SF creds in .env")
    args = ap.parse_args()

    # UTF-8 safe output
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    load_dotenv(find_dotenv(usecwd=True), override=True)

    flow_path = Path(args.file)
    if not flow_path.exists():
        sys.exit(f"❌ Not found: {flow_path}")

    report = parse_flow(flow_path)

    print(f"\nFlow: {Path(report['flow_file']).name}")
    print(f"  ObjectType: {report['object_type'] or '—'}")
    print(f"  $Record fields: {', '.join(report['record_fields']) or '—'}")
    print(f"  SObjects (ops): {', '.join(report['sobjects']) or '—'}")
    print(f"  Subflows: {', '.join(report['subflows']) or '—'}")
    print(f"  Invocable Apex: {', '.join(report['apex']) or '—'}")
    print(f"  Email templates: {', '.join(report['email_templates']) or '—'}")
    print(f"  Labels: {', '.join(report['labels']) or '—'}")

    if args.check:
        if Salesforce is None:
            print("\n[WARN] simple-salesforce not installed; skipping --check.")
            return
        try:
            sf = Salesforce(
                username=os.getenv("SF_USERNAME"),
                password=os.getenv("SF_PASSWORD"),
                security_token=os.getenv("SF_SECURITY_TOKEN"),
                domain=os.getenv("SF_DOMAIN", "test"),
            )
        except Exception as e:
            print(f"\n[WARN] Could not connect to Salesforce for --check: {e}")
            return

        missing = check_missing_fields(report, sf)
        if missing:
            print("\n[!!] Missing fields in target org:")
            for m in missing:
                print(f"   - {m}")
        else:
            print("\n[OK] No missing $Record fields detected.")

if __name__ == "__main__":
    main()
