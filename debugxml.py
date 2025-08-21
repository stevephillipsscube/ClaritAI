import xml.etree.ElementTree as ET
from pathlib import Path

FIELD_FILE = Path("force-app/main/default/objects/MUSW__Application2__c/fields/MUSW__Type__c.field-meta.xml")

if not FIELD_FILE.exists():
    raise FileNotFoundError(f"Field file not found: {FIELD_FILE}")

tree = ET.parse(FIELD_FILE)
root = tree.getroot()

print(f"Root tag: {root.tag}")
print("\nTop-level children in the XML:")
for child in root:
    print(f"- {child.tag}: {child.text}")
