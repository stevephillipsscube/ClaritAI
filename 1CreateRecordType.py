from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import subprocess
import shutil

# ---------------------------------------------------------------------
#  CLI INPUTS
# ---------------------------------------------------------------------
# Usage:
#   python CreateTypeAndRecord.py "Mobile Home Permit" true
# ---------------------------------------------------------------------
if len(sys.argv) < 2:
    print("❌ Usage: python CreateTypeAndRecord.py 'Label' [is_permit]")
    sys.exit(1)

NEW_TYPE = sys.argv[1].strip()
is_permit = len(sys.argv) >= 3 and sys.argv[2].lower() in ("true", "1", "yes", "y")

# ---------------------------------------------------------------------
#  MODE SWITCH: Permit vs Application
# ---------------------------------------------------------------------
if is_permit:
    GVS_NAME = "MUSW__Permit_Types"
    TARGET_OBJECT = "MUSW__Permit2__c"
    print(f"[MODE] PERMIT → {TARGET_OBJECT}")
else:
    GVS_NAME = "MUSW__Application_Types"
    TARGET_OBJECT = "MUSW__Application2__c"
    print(f"[MODE] APPLICATION → {TARGET_OBJECT}")

developer_name = NEW_TYPE.replace(" ", "_")
record_type_api_name = f"{TARGET_OBJECT}.{developer_name}"

PROFILE_NAME = "Admin"
PROFILE_PATH = Path(f"force-app/main/default/profiles/{PROFILE_NAME}.profile-meta.xml")
GVS_PATH = Path(f"force-app/main/default/globalValueSets/{GVS_NAME}.globalValueSet-meta.xml")
sf_cli = shutil.which("sf") or r"C:\\Program Files\\sf\\bin\\sf.cmd"
if not Path(sf_cli).exists():
    print(f"[ERROR] sf CLI not found at: {sf_cli}")
    sys.exit(1)

# ---------------------------------------------------------------------
#  STEP 1: Pull GVS + Profile
# ---------------------------------------------------------------------
print(f"[INFO] Pulling GVS '{GVS_NAME}' and Profile '{PROFILE_NAME}' from org...")
for metadata in [f"GlobalValueSet:{GVS_NAME}", f"Profile:{PROFILE_NAME}"]:
    subprocess.run([sf_cli, "project", "retrieve", "start", "--metadata", metadata, "--target-org", "clarit-org"], check=True)

# ---------------------------------------------------------------------
#  STEP 2: Update GlobalValueSet XML
# ---------------------------------------------------------------------
print("[INFO] Updating GVS XML...")
ns = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", ns)

tree = ET.parse(GVS_PATH)
root = tree.getroot()

existing = [el.find(f"{{{ns}}}fullName").text for el in root.findall(f"{{{ns}}}customValue")]
if NEW_TYPE not in existing:
    cv = ET.Element(f"{{{ns}}}customValue")
    ET.SubElement(cv, f"{{{ns}}}fullName").text = NEW_TYPE
    ET.SubElement(cv, f"{{{ns}}}default").text = "false"
    ET.SubElement(cv, f"{{{ns}}}label").text = NEW_TYPE
    root.append(cv)
    tree.write(GVS_PATH, encoding="UTF-8", xml_declaration=True)
    print(f"[✅] Added '{NEW_TYPE}' to {GVS_NAME}.")
else:
    print("[SKIPPED] GVS already contains value.")

# ---------------------------------------------------------------------
#  STEP 3: Connect to Salesforce + Create Record Type
# ---------------------------------------------------------------------
print("[INFO] Connecting to Salesforce...")
load_dotenv(override=True)
username, password, token = os.getenv("SF_USERNAME"), os.getenv("SF_PASSWORD"), os.getenv("SF_SECURITY_TOKEN")
domain = os.getenv("SF_DOMAIN", "login")
org_alias = os.getenv("SF_ORG_ALIAS", "clarit-org")

sf = Salesforce(username=username, password=password, security_token=token, domain=domain)

q = f"SELECT Id FROM RecordType WHERE SobjectType='{TARGET_OBJECT}' AND DeveloperName='{developer_name}'"
existing_rt = sf.query_all(q)
if existing_rt["totalSize"] == 0:
    payload = {
        "DeveloperName": developer_name,
        "Name": NEW_TYPE,
        "SobjectType": TARGET_OBJECT,
        "Description": f"Programmatically created record type for {NEW_TYPE}"
    }
    resp = sf.RecordType.create(payload)

    if resp.get("success"):
        print(f"[✅] Created RecordType {record_type_api_name}")
    else:
        print(f"[❌] Failed to create RecordType: {resp}")
        sys.exit(1)
else:
    print("[SKIPPED] RecordType already exists.")

# ---------------------------------------------------------------------
#  STEP 4: Clean + Update Admin Profile XML
# ---------------------------------------------------------------------
print("[INFO] Cleaning and updating profile...")
tree = ET.parse(PROFILE_PATH)
root = tree.getroot()

# Get valid RTs from org
valid = sf.query_all("SELECT SobjectType, DeveloperName FROM RecordType WHERE SobjectType IN ('MUSW__Application2__c','MUSW__Permit2__c')")
valid_rts = {f"{r['SobjectType']}.{r['DeveloperName']}" for r in valid["records"]}

removed = 0
for rtv in list(root.findall(f"{{{ns}}}recordTypeVisibilities")):
    name = rtv.find(f"{{{ns}}}recordType")
    if name is not None and name.text not in valid_rts:
        root.remove(rtv)
        removed += 1

# Ensure our new one is present
exists = any(
    (el.find(f"{{{ns}}}recordType") is not None and el.find(f"{{{ns}}}recordType").text == record_type_api_name)
    for el in root.findall(f"{{{ns}}}recordTypeVisibilities")
)
if not exists:
    new_rtv = ET.Element(f"{{{ns}}}recordTypeVisibilities")
    ET.SubElement(new_rtv, f"{{{ns}}}recordType").text = record_type_api_name
    ET.SubElement(new_rtv, f"{{{ns}}}default").text = "false"
    ET.SubElement(new_rtv, f"{{{ns}}}visible").text = "true"
    root.append(new_rtv)
    print(f"[✅] Added recordType visibility for {record_type_api_name}")

tree.write(PROFILE_PATH, encoding="UTF-8", xml_declaration=True)
print(f"[INFO] Saved cleaned profile (removed {removed} stale RTs).")

# ---------------------------------------------------------------------
#  STEP 5: Deploy Profile
# ---------------------------------------------------------------------
print("[INFO] Deploying profile metadata...")
deploy_cmd = [sf_cli, "project", "deploy", "start", "--metadata", f"Profile:{PROFILE_NAME}", "--target-org", org_alias]
proc = subprocess.run(deploy_cmd, text=True, capture_output=True, encoding="utf-8")

print(proc.stdout)
if proc.returncode == 0:
    print("[✅] Deployment successful.")
else:
    print("[❌] Deployment failed.")
    print(proc.stderr)
    sys.exit(1)
