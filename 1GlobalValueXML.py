import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
import shutil

# Constants
GVS_NAME = "MUSW__Application_Types"
METADATA_TYPE_GVS = f"GlobalValueSet:{GVS_NAME}"
GVS_PATH = Path(f"force-app/main/default/globalValueSets/{GVS_NAME}.globalValueSet-meta.xml")

PROFILE_NAME = "Admin"  # Use exact match from retrieved folder
PROFILE_FILENAME = f"{PROFILE_NAME}.profile-meta.xml"
METADATA_TYPE_PROFILE = f"Profile:{PROFILE_NAME}"
PROFILE_PATH = Path(f"force-app/main/default/profiles/{PROFILE_FILENAME}")

# Read NEW_TYPE from command line
if len(sys.argv) < 2:
    print("Error: NEW_TYPE not provided.")
    sys.exit(1)

NEW_TYPE = sys.argv[1]
developer_name = NEW_TYPE.replace(" ", "_")
record_type_api_name = f"MUSW__Application2__c.{developer_name}"

# Locate sf CLI
sf_path = shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"
if not Path(sf_path).exists():
    print(f"[ERROR] sf CLI not found at: {sf_path}")
    sys.exit(1)

# Pull Global Value Set
print("[INFO] Pulling GVS...")
subprocess.run([
    sf_path, "project", "retrieve", "start",
    "--metadata", METADATA_TYPE_GVS,
    "--target-org", "clarit-org"
], check=True)

# Pull Profile
print(f"[INFO] Pulling Profile: {PROFILE_NAME}")
subprocess.run([
    sf_path, "project", "retrieve", "start",
    "--metadata", METADATA_TYPE_PROFILE,
    "--target-org", "clarit-org"
], check=True)

# Parse GVS XML
print("[INFO] Updating GlobalValueSet XML...")
ns_uri = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", ns_uri)

tree = ET.parse(GVS_PATH)
root = tree.getroot()

existing_values = [
    el.find(f"{{{ns_uri}}}fullName").text
    for el in root.findall(f"{{{ns_uri}}}customValue")
]

if NEW_TYPE not in existing_values:
    new_elem = ET.Element(f"{{{ns_uri}}}customValue")
    ET.SubElement(new_elem, f"{{{ns_uri}}}fullName").text = NEW_TYPE
    ET.SubElement(new_elem, f"{{{ns_uri}}}default").text = "false"
    ET.SubElement(new_elem, f"{{{ns_uri}}}label").text = NEW_TYPE
    insert_at = next((i for i, el in enumerate(root) if el.tag.endswith("masterLabel")), len(root))
    root.insert(insert_at, new_elem)
    tree.write(GVS_PATH, encoding="UTF-8", xml_declaration=True)
    print("[✅] GVS XML updated.")
else:
    print("[SKIPPED] GVS already contains value.")

# Update Profile XML
print("[INFO] Updating Profile XML...")

if not PROFILE_PATH.exists():
    print(f"[❌] Profile file not found: {PROFILE_PATH}")
    sys.exit(1)

tree = ET.parse(PROFILE_PATH)
root = tree.getroot()

record_type_elements = root.findall(f"{{{ns_uri}}}recordTypeVisibilities")
existing_rtv = any(
    rtv.find(f"{{{ns_uri}}}recordType") is not None and
    rtv.find(f"{{{ns_uri}}}recordType").text == record_type_api_name
    for rtv in record_type_elements
)

if not existing_rtv:
    rtv = ET.Element(f"{{{ns_uri}}}recordTypeVisibilities")
    ET.SubElement(rtv, f"{{{ns_uri}}}recordType").text = record_type_api_name
    ET.SubElement(rtv, f"{{{ns_uri}}}default").text = "false"
    ET.SubElement(rtv, f"{{{ns_uri}}}visible").text = "true"
    root.append(rtv)
    tree.write(PROFILE_PATH, encoding="UTF-8", xml_declaration=True)
    print("Profile XML updated with new recordType visibility.")
else:
    print("[SKIPPED] RecordType already present in profile.")
