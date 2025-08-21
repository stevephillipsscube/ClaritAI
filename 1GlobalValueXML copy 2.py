import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET
import shutil

# Constants
GVS_NAME = "MUSW__Application_Types"
METADATA_TYPE = f"GlobalValueSet:{GVS_NAME}"
GVS_PATH = Path(f"force-app/main/default/globalValueSets/{GVS_NAME}.globalValueSet-meta.xml")

# Read NEW_TYPE from command line
if len(sys.argv) < 2:
    print("Error: NEW_TYPE not provided.")
    sys.exit(1)

NEW_TYPE = sys.argv[1]
print(f"[INFO] Adding new Global Value: {NEW_TYPE}")

# Step 1: Locate sf CLI
sf_path = shutil.which("sf")
if not sf_path:
    # Fallback path if not in PATH
    sf_path = r"C:\Program Files\sf\bin\sf.cmd"

if not Path(sf_path).exists():
    print(f"[ERROR] sf CLI not found at: {sf_path}")
    sys.exit(1)

# Step 2: Pull the latest Global Value Set from the org
print("[INFO] Pulling latest GlobalValueSet XML...")
subprocess.run([
    sf_path, "project", "retrieve", "start",
    "--metadata", METADATA_TYPE,
    "--target-org", "clarit-org"
], check=True)

# Step 3: Parse XML and add new <customValue>
print("[INFO] Updating XML file...")
tree = ET.parse(GVS_PATH)
root = tree.getroot()

# Register namespace
ns_uri = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", ns_uri)

# Check for duplicates
existing_values = [cv.find(f"{{{ns_uri}}}fullName").text for cv in root.findall(f"{{{ns_uri}}}customValue")]
if NEW_TYPE in existing_values:
    print(f"[SKIPPED] '{NEW_TYPE}' already exists in the Global Value Set.")
    sys.exit(0)

# Create new <customValue> element
custom_value = ET.Element(f"{{{ns_uri}}}customValue")
ET.SubElement(custom_value, "fullName").text = NEW_TYPE
ET.SubElement(custom_value, "default").text = "false"
ET.SubElement(custom_value, "label").text = NEW_TYPE

# Append it before <masterLabel> to avoid structural issues
insert_index = next((i for i, child in enumerate(root) if child.tag.endswith("masterLabel")), len(root))
root.insert(insert_index, custom_value)

# Step 4: Write back to file
tree.write(GVS_PATH, encoding="UTF-8", xml_declaration=True)
print(f"[SUCCESS] Updated XML saved: {GVS_PATH}")
