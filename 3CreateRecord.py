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

if not all([username, password, security_token]):
    print("❌ Missing Salesforce credentials in .env")
    sys.exit(1)

# --- Input: new record type name ---
if len(sys.argv) < 2:
    print("❌ Usage: python script.py 'Record Type Label'")
    sys.exit(1)

label = sys.argv[1]
developer_name = label.replace(" ", "_")
sobject_type = "MUSW__Application2__c"
record_type_api_name = f"{sobject_type}.{developer_name}"

# --- Connect to Salesforce ---
try:
    sf = Salesforce(username=username, password=password, security_token=security_token, domain=domain)
except Exception as e:
    print(f"❌ Login failed: {e}")
    sys.exit(1)

# --- Check for existing RecordType ---
existing = sf.query_all(f"""
    SELECT Id FROM RecordType
    WHERE SobjectType = '{sobject_type}' AND DeveloperName = '{developer_name}'
""")

if existing["totalSize"] > 0:
    print(f"⚠️ RecordType '{developer_name}' already exists. Skipping creation.")
else:
    payload = {
        "DeveloperName": developer_name,
        "Name": label,
        "SobjectType": sobject_type,
        "Description": f"Programmatically created record type for {label}"
    }
    response = sf.RecordType.create(payload)

    if response.get("success"):
        print(f"[✅] Created RecordType: {label} (Id: {response['id']})")
    else:
        print(f"[❌] Failed to create RecordType: {response}")
        sys.exit(1)

# --- Update System Admin Profile XML ---
ns = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", ns)
profile_path = Path("force-app/main/default/profiles/Admin.profile-meta.xml")

if not profile_path.exists():
    print(f"[❌] Profile XML not found: {profile_path}")
    sys.exit(1)

tree = ET.parse(profile_path)
root = tree.getroot()

existing_rtv = any(
    el.find(f"{{{ns}}}recordType") is not None and el.find(f"{{{ns}}}recordType").text == record_type_api_name
    for el in root.findall(f"{{{ns}}}recordTypeVisibilities")
)

if not existing_rtv:
    rtv = ET.Element(f"{{{ns}}}recordTypeVisibilities")
    ET.SubElement(rtv, f"{{{ns}}}recordType").text = record_type_api_name
    ET.SubElement(rtv, f"{{{ns}}}default").text = "false"
    ET.SubElement(rtv, f"{{{ns}}}visible").text = "true"
    root.append(rtv)
    tree.write(profile_path, encoding="UTF-8", xml_declaration=True)
    print("[✅] Profile XML updated.")
else:
    print("[SKIPPED] Profile already has recordType visibility.")

# --- Deploy profile changes ---
print("[INFO] Deploying profile metadata...")
sf_cli = shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"
if not Path(sf_cli).exists():
    print(f"[ERROR] sf CLI not found at: {sf_cli}")
    sys.exit(1)

print("[INFO] Deploying profile metadata...")
# Deploy only the System Administrator profile file
deploy_result = subprocess.run([
    sf_cli, "project", "deploy", "start",
    "--source-dir", f"force-app/main/default/profiles/Admin.profile-meta.xml",
    "--target-org", "clarit-org"
], check=True)



print(deploy_result.stdout)
if deploy_result.returncode == 0:
    print("[✅] Profile deployment successful.")
else:
    print("[❌] Deployment failed:")
    print(deploy_result.stderr)
