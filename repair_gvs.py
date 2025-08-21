# repair_gvs.py (updated)
from xml.etree import ElementTree as ET
from pathlib import Path

GVS = Path("force-app/main/default/globalValueSets/MUSW__Application_Types.globalValueSet-meta.xml")

tree = ET.parse(GVS)
root = tree.getroot()

# Extract and deduplicate all <customValue> blocks
custom_values = root.findall("customValue")
seen = set()
new_custom_values = []

for val in custom_values:
    name = val.find("fullName").text
    if name not in seen:
        seen.add(name)
        new_custom_values.append(val)

# Remove all <customValue> elements from the root
for val in custom_values:
    root.remove(val)

# Insert deduplicated <customValue> blocks back at the top
for val in reversed(new_custom_values):  # reverse to maintain original order
    root.insert(0, val)

# Save the cleaned XML
tree.write(GVS, encoding="utf-8", xml_declaration=True)
print("✅ GVS cleaned – now run update_gvs.py")
