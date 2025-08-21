from pathlib import Path
import xml.etree.ElementTree as ET, subprocess, shutil, sys

RECORD_TYPE_FILE = Path(
    "force-app/main/default/objects/"
    "MUSW__Application2__c/recordTypes/Permit_Application.recordType-meta.xml"
)
NEW_VALUE = "Mobile Home Application"
SF_CLI   = shutil.which("sf")
ORG      = "steve.phillips@scubeenterprise.com.owa.clariti"

ns = {"sf": "http://soap.sforce.com/2006/04/metadata"}
ET.register_namespace("", ns["sf"])

if not RECORD_TYPE_FILE.exists():
    sys.exit(f"❌ RecordType XML not found: {RECORD_TYPE_FILE}")

tree = ET.parse(RECORD_TYPE_FILE)
root = tree.getroot()

for pv in root.findall("sf:picklistValues", ns):
    pick = pv.find("sf:picklist", ns)
    if pick is not None and pick.text == "MUSW__Type2__c":
        for v in pv.findall("sf:values", ns):
            fn = v.find("sf:fullName", ns)
            if fn is not None and fn.text == NEW_VALUE:
                print("ℹ️ value already present in record type")
                break
        else:
            new = ET.SubElement(pv, f"{{{ns['sf']}}}values")
            ET.SubElement(new, f"{{{ns['sf']}}}fullName").text = NEW_VALUE
            ET.SubElement(new, f"{{{ns['sf']}}}default").text = "false"
            tree.write(RECORD_TYPE_FILE, encoding="UTF-8", xml_declaration=True)
            print("✅ value added to existing picklist block")
        break
else:
    pv = ET.SubElement(root, f"{{{ns['sf']}}}picklistValues")
    ET.SubElement(pv, f"{{{ns['sf']}}}picklist").text = "MUSW__Type2__c"
    val = ET.SubElement(pv, f"{{{ns['sf']}}}values")
    ET.SubElement(val, f"{{{ns['sf']}}}fullName").text = NEW_VALUE
    ET.SubElement(val, f"{{{ns['sf']}}}default").text = "false"
    tree.write(RECORD_TYPE_FILE, encoding="UTF-8", xml_declaration=True)
    print("✅ block + value added")

# deploy just the record type
subprocess.run([
    SF_CLI, "project", "deploy", "start",
    "--source-dir", str(RECORD_TYPE_FILE.parent),
    "--target-org", ORG
], check=True)
print("🚀 deployed record type")
