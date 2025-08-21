#!/usr/bin/env python3
"""
update_gvs.py  – ensure 'Mobile Home Application' exists in the
Global Value Set 'MUSW__Application_Types' and deploy it.

requirements:
  • sf CLI in PATH
  • simple_salesforce already installed for auth (env-vars)
"""

import shutil, subprocess, sys
from pathlib import Path
import xml.etree.ElementTree as ET

# ────────────────────────────── CONFIG ──────────────────────────────
ORG_ALIAS      = "steve.phillips@scubeenterprise.com.owa.clariti"
GVS_NAME       = "MUSW__Application_Types"
NEW_VALUE      = "Mobile Home Application"
PROJECT_DIR    = Path(__file__).resolve().parent
GVS_FILE       = PROJECT_DIR / "force-app" / "main" / "default" \
                              / "globalValueSets" \
                              / f"{GVS_NAME}.globalValueSet-meta.xml"
# ────────────────────────────────────────────────────────────────────

SF = shutil.which("sf")
if not SF:
    sys.exit("❌  sf CLI not found in PATH")

def run(cmd: list[str]) -> None:
    """Helper → run a subprocess, bubble errors."""
    print(" ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)

def fetch_from_org():
    """Retrieve the GVS XML from the org if it isn’t on disk."""
    if GVS_FILE.exists():
        return
    print(f"📥 Retrieving {GVS_NAME} from org …")
    run([
        SF, "project", "retrieve", "start",
        "--metadata", f"GlobalValueSet:{GVS_NAME}",
        "--target-org", ORG_ALIAS
    ])
    if not GVS_FILE.exists():
        sys.exit(f"❌  {GVS_FILE} still not found after retrieve.")

def validate_xml(path: Path, msg: str):
    """Parse XML to ensure it is well-formed."""
    try:
        ET.parse(path)
    except ET.ParseError as e:
        sys.exit(f"🚨  {msg}\n    {e}")

def add_value_if_needed():
    ns = {"sf": "http://soap.sforce.com/2006/04/metadata"}
    ET.register_namespace("", ns["sf"])   # preserve pretty output

    tree = ET.parse(GVS_FILE)
    root = tree.getroot()

    existing = [v.find("sf:fullName", ns).text
                for v in root.findall("sf:customValue", ns)]

    if NEW_VALUE in existing:
        print(f"ℹ️  '{NEW_VALUE}' already present – no change made.")
        return False    # nothing to deploy

    print(f"➕  adding '{NEW_VALUE}' to {GVS_NAME} …")
    new_val = ET.SubElement(root, f"{{{ns['sf']}}}customValue")
    ET.SubElement(new_val, f"{{{ns['sf']}}}fullName").text = NEW_VALUE
    ET.SubElement(new_val, f"{{{ns['sf']}}}default").text  = "false"
    ET.SubElement(new_val, f"{{{ns['sf']}}}label").text    = NEW_VALUE
    tree.write(GVS_FILE, encoding="utf-8", xml_declaration=True)
    return True         # changed

def deploy():
    print("🚀  Deploying GlobalValueSet …")
    run([
        SF, "project", "deploy", "start",
        "--source-dir", str(GVS_FILE.parent),
        "--target-org", ORG_ALIAS
    ])
    print("✅  Deploy finished.")

# ─────────────────────────── main flow ──────────────────────────────
fetch_from_org()
validate_xml(GVS_FILE, "XML is still malformed – fix before deploying.")
changed = add_value_if_needed()
validate_xml(GVS_FILE, "XML became malformed after edit – aborting.")

if changed:
    deploy()
else:
    print("✅  Nothing to deploy – pick-list already up-to-date.")
