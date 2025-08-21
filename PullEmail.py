import os
from simple_salesforce import Salesforce
from dotenv import load_dotenv
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

load_dotenv()

sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "test")
)

template_name = "MyFakeEmail"

# Query the template
query = sf.query(f"""
    SELECT Id, DeveloperName, Name, HtmlValue, FolderId,
           RelatedEntityType, Description, Encoding, Subject
    FROM EmailTemplate
    WHERE Name = '{template_name}'
    AND UiType = 'SFX'
    LIMIT 1
""")

if not query["records"]:
    print(f"Template '{template_name}' not found.")
    exit()

tpl = query["records"][0]

# ─── Generate XML ───
root = Element("EmailTemplate", xmlns="http://soap.sforce.com/2006/04/metadata")

# Required and used fields
SubElement(root, "apiVersion").text = "59.0"
SubElement(root, "description").text = tpl.get("Description", "")
SubElement(root, "encoding").text = tpl.get("Encoding", "UTF-8")
SubElement(root, "folder").text = tpl.get("FolderId")
SubElement(root, "name").text = tpl.get("Name")
SubElement(root, "subject").text = tpl.get("Subject", "")
SubElement(root, "uiType").text = "SFX"
SubElement(root, "templateType").text = "custom"
SubElement(root, "isActive").text = "true"

if tpl.get("RelatedEntityType"):
    SubElement(root, "relatedEntityType").text = tpl["RelatedEntityType"]

# HTML body is stored in a separate file in actual metadata deployments,
# but you can optionally embed it here if you're doing a custom export:
html = tpl.get("HtmlValue", "")
if html:
    SubElement(root, "htmlValue").text = html

# ─── Write to XML File ───
rough_string = tostring(root, encoding="utf-8")
pretty_xml = parseString(rough_string).toprettyxml(indent="  ")

xml_file = tpl["DeveloperName"] + ".emailTemplate-meta.xml"
with open(xml_file, "w", encoding="utf-8") as f:
    f.write(pretty_xml)

print(f"✅ Metadata XML saved to {xml_file}")
