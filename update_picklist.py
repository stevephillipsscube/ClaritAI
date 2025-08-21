import xml.etree.ElementTree as ET
from pathlib import Path
import subprocess
import shutil

# Setup
ORG_ALIAS = "steve.phillips@scubeenterprise.com.owa.clariti"
FIELD_FILE = Path("force-app/main/default/objects/MUSW__Application2__c/fields/MUSW__Type2__c.field-meta.xml")

# Check for SF CLI
SF_CLI = shutil.which("sf")
if SF_CLI is None:
    raise EnvironmentError("❌ Salesforce CLI 'sf' not found in PATH.")

# Load field metadata XML
if not FIELD_FILE.exists():
    raise FileNotFoundError(f"❌ Picklist field file not found: {FIELD_FILE}")

tree = ET.parse(FIELD_FILE)
root = tree.getroot()

ns = {"sf": "http://soap.sforce.com/2006/04/metadata"}

# Try global value set first
gvs_ref = root.find("sf:valueSet/sf:valueSetName", ns)
if gvs_ref is not None:
    gvs_name = gvs_ref.text
    print(f"🔍 Found global value set: {gvs_name}")

    # Retrieve GVS metadata
    subprocess.run([
        SF_CLI, "project", "retrieve", "start",
        "--metadata", f"GlobalValueSet:{gvs_name}",
        "--target-org", ORG_ALIAS
    ], check=True)

    # Edit GVS XML
    gvs_file = Path(f"force-app/main/default/globalValueSets/{gvs_name}.globalValueSet-meta.xml")
    if not gvs_file.exists():
        raise FileNotFoundError(f"❌ Retrieved global value set file not found at: {gvs_file}")

    tree = ET.parse(gvs_file)
    root = tree.getroot()
    existing = [v.find("sf:fullName", ns).text for v in root.findall("sf:value", ns)]

    if "Mobile Home Application" in existing:
        print("ℹ️ 'Mobile Home Application' already exists in GVS.")
    else:
        new_val = ET.SubElement(root, f"{{{ns['sf']}}}value")
        ET.SubElement(new_val, f"{{{ns['sf']}}}fullName").text = "Mobile Home Application"
        ET.SubElement(new_val, f"{{{ns['sf']}}}default").text = "false"
        ET.SubElement(new_val, f"{{{ns['sf']}}}label").text = "Mobile Home Application"
        tree.write(gvs_file, encoding="utf-8", xml_declaration=True)
        print(f"✅ Added to global value set: {gvs_file}")

else:
    # Local picklist fallback
    print("ℹ️ No global value set reference. Using local picklist update...")

    value_set = root.find("sf:valueSet", ns)
    if value_set is None:
        raise ValueError("❌ No <valueSet> element found. This field might not be a picklist.")

    existing = [v.find("sf:fullName", ns).text for v in value_set.findall("sf:value", ns)]
    if "Mobile Home Application" in existing:
        print("ℹ️ 'Mobile Home Application' already exists in local picklist.")
    else:
        new_val = ET.SubElement(value_set, f"{{{ns['sf']}}}value")
        ET.SubElement(new_val, f"{{{ns['sf']}}}fullName").text = "Mobile Home Application"
        ET.SubElement(new_val, f"{{{ns['sf']}}}default").text = "false"
        ET.SubElement(new_val, f"{{{ns['sf']}}}label").text = "Mobile Home Application"
        tree.write(FIELD_FILE, encoding="utf-8", xml_declaration=True)
        print(f"✅ Added to local picklist: {FIELD_FILE}")

print("✅ Done.")
