from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import sys

# Load credentials
load_dotenv()

sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "login")
)

# 🧠 DeveloperName from metadata
ORIGINAL_DEVELOPER_NAME = "MyFakeEmail_1753986103857"
NEW_DEVELOPER_NAME = "MyFakeEmail_Clone"
NEW_TEMPLATE_LABEL = "Cloned Email Template"

# Step 1: Query the original Lightning Email Template
template_q = sf.query(f"""
    SELECT Id, DeveloperName, Name, HtmlValue, FolderId, RelatedEntityType
    FROM EmailTemplate
    WHERE DeveloperName = '{ORIGINAL_DEVELOPER_NAME}'
    AND UiType = 'SFX'
""")

if not template_q['records']:
    print(f"❌ Email Template '{ORIGINAL_DEVELOPER_NAME}' not found.")
    sys.exit(1)

tpl = template_q['records'][0]

# Step 2: Create the new EmailTemplate record
new_template = {
    "DeveloperName": NEW_DEVELOPER_NAME,
    "Name": NEW_TEMPLATE_LABEL,
    "FolderId": tpl["FolderId"],
    "TemplateType": "custom",
    "RelatedEntityType": tpl.get("RelatedEntityType", "Account"),
    "UiType": "SFX",  # Lightning
    "IsActive": True
}

create_resp = sf.EmailTemplate.create(new_template)

if not create_resp.get("success"):
    print("❌ Failed to create new EmailTemplate.")
    sys.exit(1)

new_id = create_resp["id"]
print(f"[✅] Created Email Template: {NEW_DEVELOPER_NAME} → {new_id}")

# Step 3: Add content (HTML) via ContentVersion
html_body = tpl.get("HtmlValue", "<p>No content</p>")
file_name = f"{NEW_DEVELOPER_NAME}.html"

sf.ContentVersion.create({
    "Title": NEW_TEMPLATE_LABEL,
    "PathOnClient": file_name,
    "VersionData": html_body.encode("utf-8").decode("utf-8"),
    "FirstPublishLocationId": new_id
})

print(f"[✅] Cloned HTML content to new template.")
