import subprocess, shutil, xml.etree.ElementTree as ET
from pathlib import Path

ORG = "steve.phillips@scubeenterprise.com.owa.clariti"
FIELD_API = "MUSW__Application2__c.MUSW__Type2__c"
NEW_VALUE = "Mobile Home Application"

SF = shutil.which("sf") or exit("sf CLI not found")

# 1) Retrieve the field metadata
subprocess.run([
    SF, "project", "retrieve", "start",
    "--metadata", f"CustomField:{FIELD_API}",
    "--target-org", ORG
], check=True)

field_xml = Path(
    "force-app/main/default/objects/MUSW__Application2__c/fields/"
    "MUSW__Type2__c.field-meta.xml"
)
if not field_xml.exists():
    exit(f"field file missing: {field_xml}")

ns = {"sf": "http://soap.sforce.com/2006/04/metadata"}
ET.register_namespace('', ns['sf'])
tree = ET.parse(field_xml)
root = tree.getroot()

# 2) Determine whether it's a GVS or local pick-list
gvs = root.find("sf:valueSet/sf:valueSetName", ns)
if gvs is not None:
    gvs_name = gvs.text
    print(f"Using global value set: {gvs_name}")

    subprocess.run([
        SF, "project", "retrieve", "start",
        "--metadata", f"GlobalValueSet:{gvs_name}",
        "--target-org", ORG
    ], check=True)

    gvs_xml = Path(f"force-app/main/default/globalValueSets/{gvs_name}.globalValueSet-meta.xml")
    gtree = ET.parse(gvs_xml)
    groot = gtree.getroot()

    full_names = [v.find("sf:fullName", ns).text for v in groot.findall("sf:value", ns)]
    if NEW_VALUE not in full_names:
        v = ET.SubElement(groot, f"{{{ns['sf']}}}value")
        ET.SubElement(v, f"{{{ns['sf']}}}fullName").text = NEW_VALUE
        ET.SubElement(v, f"{{{ns['sf']}}}default").text = "false"
        ET.SubElement(v, f"{{{ns['sf']}}}label").text  = NEW_VALUE
        gtree.write(gvs_xml, encoding="utf-8", xml_declaration=True)
        deploy_src = gvs_xml.parent
        print("✔ added to GVS")
    else:
        deploy_src = gvs_xml.parent
        print("ℹ value already in GVS")

else:                           # local pick-list
    vs = root.find("sf:valueSet", ns)
    full_names = [v.find("sf:fullName", ns).text for v in vs.findall("sf:value", ns)]
    if NEW_VALUE not in full_names:
        v = ET.SubElement(vs, f"{{{ns['sf']}}}value")
        ET.SubElement(v, f"{{{ns['sf']}}}fullName").text = NEW_VALUE
        ET.SubElement(v, f"{{{ns['sf']}}}default").text = "false"
        ET.SubElement(v, f"{{{ns['sf']}}}label").text  = NEW_VALUE
        tree.write(field_xml, encoding="utf-8", xml_declaration=True)
        deploy_src = field_xml.parent.parent  # deploy the whole object folder
        print("✔ added to local pick-list")
    else:
        deploy_src = field_xml.parent.parent
        print("ℹ value already in pick-list")

# 3) Deploy the change
subprocess.run([
    SF, "project", "deploy", "start",
    "--source-dir", str(deploy_src),
    "--target-org", ORG
], check=True)

print("✅ pick-list ready – rerun createticket.py")
