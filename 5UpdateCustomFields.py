from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import subprocess
import shutil
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


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

# --- Find custom fields ---
fields_dir = Path("force-app/main/default/objects/MUSW__Application2__c/fields")
custom_field_paths = list(fields_dir.glob("*.field-meta.xml"))

if not custom_field_paths:
    print("⚠️ No custom fields found to deploy.")
else:
    print(f"[INFO] Found {len(custom_field_paths)} custom field(s) to deploy.")

# --- Deploy metadata ---
print("[INFO] Deploying metadata to Salesforce...")
sf_cli = shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"
if not Path(sf_cli).exists():
    print(f"[ERROR] sf CLI not found at: {sf_cli}")
    sys.exit(1)

# Construct all paths to deploy
deploy_paths = [str(profile_path)] + [str(p) for p in custom_field_paths]

#deploy_result = subprocess.run([
#    sf_cli, "project", "deploy", "start",
#    "--source-dir", *deploy_paths,
#    "--target-org", "clarit-org"
#], capture_output=True, text=True)

deploy_result = subprocess.run([
    sf_cli, "project", "deploy", "start",
    "--source-dir", *deploy_paths,
    "--target-org", "clarit-org"
], stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")

print(deploy_result.stdout)
if deploy_result.returncode == 0:
    print("[✅] Metadata deployment successful.")
else:
    print("[❌] Deployment failed:")
    print(deploy_result.stderr)


# --- Output results ---
if deploy_result.returncode == 0:
    print("[✅] Deployment successful.")
    print(deploy_result.stdout)
else:
    print("[❌] Deployment failed:")
    print(deploy_result.stderr)


def update_page_layout(custom_field_names, layout_path):
    ns = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", ns)

    tree = ET.parse(layout_path)
    root = tree.getroot()

    detail_sections = root.findall(f".//{{{ns}}}detailLayoutSections")
    if not detail_sections:
        print("[⚠️] No <detailLayoutSections> found in layout.")
        return

    target_section = detail_sections[-1]

    for field in custom_field_names:
        row = ET.SubElement(target_section, f"{{{ns}}}layoutRows")

        item = ET.SubElement(row, f"{{{ns}}}layoutItems")
        ET.SubElement(item, f"{{{ns}}}field").text = field
        ET.SubElement(item, f"{{{ns}}}behavior").text = "Edit"
        ET.SubElement(item, f"{{{ns}}}required").text = "false"

        ET.SubElement(row, f"{{{ns}}}numItems").text = "1"
        ET.SubElement(row, f"{{{ns}}}tabOrder").text = "Left-Right"

    tree.write(layout_path, encoding="UTF-8", xml_declaration=True)
    print(f"[✅] Updated layout: {layout_path.name}")


# ------------------------------------------------------------------
# STEP: push the new fields into the Record-Type-specific page layout
# ------------------------------------------------------------------

layout_name = f"{sobject_type}-{developer_name}.layout-meta.xml"  # e.g. MUSW__Application2__c-Mobile_Home_Permit.layout-meta.xml
layout_path = Path("force-app/main/default/layouts") / layout_name

if not layout_path.exists():
    print(f"[⚠️] Expected layout file not found: {layout_path}. "
          f"Generate or retrieve the layout first, then re-run this script.")
else:
    # List of API names (field fullName) — used by update_page_layout()
    custom_field_api_names = [p.stem for p in custom_field_paths]

    update_page_layout(custom_field_api_names, layout_path)

    # 🔄 Deploy ONLY the changed layout
    print(f"[INFO] Deploying updated layout {layout_name} …")
    layout_deploy = subprocess.run(
        [
            sf_cli, "project", "deploy", "start",
            "--source-dir", str(layout_path),
            "--target-org", "clarit-org"
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8"
    )

    if layout_deploy.returncode == 0:
        print("[✅] Layout deployment successful.")
    else:
        print("[❌] Layout deployment failed:")
        print(layout_deploy.stderr)

