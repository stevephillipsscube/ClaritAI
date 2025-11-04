from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import subprocess
import shutil

# --- Load environment variables ---
load_dotenv(override=True)

username = os.getenv("SF_USERNAME")
password = os.getenv("SF_PASSWORD")
security_token = os.getenv("SF_SECURITY_TOKEN")
domain = os.getenv("SF_DOMAIN", "login")
org_alias = os.getenv("SF_ORG_ALIAS", "clarit-org")

if not all([username, password, security_token]):
    print("❌ Missing Salesforce credentials in .env")
    sys.exit(1)

# --- Args --------------------------------------------------------------
if len(sys.argv) < 2:
    print("❌ Usage: python 3CreateRecord.py \"Record Type Label\" [is_permit]")
    sys.exit(1)

label = sys.argv[1].strip()
is_permit = False
if len(sys.argv) >= 3:
    is_permit = sys.argv[2].lower() in ("true", "1", "yes", "y")

# pick object based on flag
if is_permit:
    sobject_type = "MUSW__Permit2__c"
    print(f"[MODE] PERMIT → using object {sobject_type}")
else:
    sobject_type = "MUSW__Application2__c"
    print(f"[MODE] APPLICATION → using object {sobject_type}")

developer_name = label.replace(" ", "_")
record_type_api_name = f"{sobject_type}.{developer_name}"

# --- Connect to Salesforce --------------------------------------------
try:
    sf = Salesforce(
        username=username,
        password=password,
        security_token=security_token,
        domain=domain
    )
except Exception as e:
    print(f"❌ Login failed: {e}")
    sys.exit(1)

# --- Create record type in org (if missing) ---------------------------
existing = sf.query_all(f"""
    SELECT Id FROM RecordType
    WHERE SobjectType = '{sobject_type}' AND DeveloperName = '{developer_name}'
""")

if existing["totalSize"] > 0:
    print(f"⚠️ RecordType '{record_type_api_name}' already exists in org. Skipping create.")
else:
    payload = {
        "DeveloperName": developer_name,
        "Name": label,
        "SobjectType": sobject_type,
        "Description": f"Programmatically created record type for {label}"
    }
    resp = sf.RecordType.create(payload)
    if resp.get("success"):
        print(f"[✅] Created RecordType in org: {record_type_api_name}")
    else:
        print(f"[❌] Failed to create RecordType: {resp}")
        sys.exit(1)

# --- Get ALL record types in org for our 2 objects --------------------
# we'll use this to CLEAN the profile before deploy
rt_query = sf.query_all("""
    SELECT SobjectType, DeveloperName
    FROM RecordType
    WHERE SobjectType IN ('MUSW__Application2__c','MUSW__Permit2__c')
""")

valid_recordtypes = {
    f"{rt['SobjectType']}.{rt['DeveloperName']}"
    for rt in rt_query["records"]
}
print(f"[INFO] Org has {len(valid_recordtypes)} valid record types for App/Permit.")

# --- Load and CLEAN the Admin profile XML -----------------------------
ns = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", ns)
profile_path = Path("force-app/main/default/profiles/Admin.profile-meta.xml")

if not profile_path.exists():
    print(f"[❌] Profile XML not found: {profile_path}")
    sys.exit(1)

tree = ET.parse(profile_path)
root = tree.getroot()

rtv_nodes = root.findall(f"{{{ns}}}recordTypeVisibilities")
removed = 0

for rtv in list(rtv_nodes):  # list() so we can remove while iterating
    rt_el = rtv.find(f"{{{ns}}}recordType")
    if rt_el is None:
        continue
    rt_name = rt_el.text
    if rt_name not in valid_recordtypes:
        # 👇 THIS is what will delete MUSW__Permit2__c.Dumpster if it doesn't exist in org
        print(f"[CLEAN] Removing stale recordTypeVisibilities for: {rt_name}")
        root.remove(rtv)
        removed += 1

# now ensure OUR record type is present
exists_in_profile = any(
    (rtv.find(f"{{{ns}}}recordType") is not None and
     rtv.find(f"{{{ns}}}recordType").text == record_type_api_name)
    for rtv in root.findall(f"{{{ns}}}recordTypeVisibilities")
)

if not exists_in_profile:
    rtv = ET.Element(f"{{{ns}}}recordTypeVisibilities")
    ET.SubElement(rtv, f"{{{ns}}}recordType").text = record_type_api_name
    ET.SubElement(rtv, f"{{{ns}}}default").text = "false"
    ET.SubElement(rtv, f"{{{ns}}}visible").text = "true"
    root.append(rtv)
    print(f"[✅] Added recordTypeVisibilities for {record_type_api_name}")
else:
    print(f"[SKIPPED] Profile already had recordTypeVisibilities for {record_type_api_name}")

# write back
tree.write(profile_path, encoding="UTF-8", xml_declaration=True)
print(f"[INFO] Profile XML saved. Removed {removed} stale entries.")

# --- Deploy profile changes via sf CLI --------------------------------
sf_cli = shutil.which("sf") or r"C:\\Program Files\\sf\\bin\\sf.cmd"
if not Path(sf_cli).exists():
    print(f"[ERROR] sf CLI not found at: {sf_cli}")
    sys.exit(1)

deploy_cmd = [
    sf_cli,
    "project", "deploy", "start",
    "--metadata", "Profile:Admin",
    "--target-org", org_alias,
]

print("[INFO] Running:", " ".join(deploy_cmd))

deploy_result = subprocess.run(
    deploy_cmd,
    text=True,
    encoding="utf-8",
    capture_output=True
)

print(deploy_result.stdout)
if deploy_result.returncode == 0:
    print("[✅] Profile deployment successful.")
else:
    print("[❌] Deployment failed.")
    print("STDERR:\n", deploy_result.stderr)
    sys.exit(1)
