import sys
from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import re



# Load .env with SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN, SF_DOMAIN
load_dotenv()

# Connect to Salesforce
sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "test")
)

# ─── Handle input from Streamlit ───
if len(sys.argv) >= 3:
    original_template = sys.argv[1].strip()
    raw_new_names = " ".join(sys.argv[2:])  # joins multiline input passed as one long arg
else:
    print("Usage: python CloneEmailTemplatesList.py <template_to_clone> <newline-separated-names>")
    sys.exit(1)


if not raw_new_names:
    print("No new template names provided.")
    sys.exit(1)

new_names = [line.strip() for line in raw_new_names.splitlines() if line.strip()]
cloned_names = []

# ─── Query the original template once ───
# ─── Resolve actual DeveloperName from human-readable Name ───
name_lookup = sf.query(f"""
    SELECT DeveloperName
    FROM EmailTemplate
    WHERE Name = '{original_template}'
    AND UiType = 'SFX'
    LIMIT 1
""")

if not name_lookup["records"]:
    print(f"Could not resolve Lightning DeveloperName for '{original_template}'")
    sys.exit(1)

resolved_devname = name_lookup["records"][0]["DeveloperName"]

# Use resolved_devname for actual template fetch
query = sf.query(f"""
    SELECT Id, DeveloperName, Name, HtmlValue, FolderId, RelatedEntityType, Description, Encoding, Subject
    FROM EmailTemplate
    WHERE DeveloperName = '{resolved_devname}'
    AND UiType = 'SFX'
    LIMIT 1
""")

if not query["records"]:
    print(f"Source template '{original_template}' not found or not Lightning.")
    sys.exit(1)

tpl = query["records"][0]


for new_devname in new_names:
    # 1. Replace invalid characters with _
    safe_devname = re.sub(r"[^a-zA-Z0-9]", "_", new_devname)

    # 2. Replace multiple underscores with a single _
    safe_devname = re.sub(r"_+", "_", safe_devname)

    # 3. Strip leading/trailing underscores
    safe_devname = safe_devname.strip("_")

    # 4. Ensure it starts with a letter (Salesforce requires this)
    if not safe_devname[0].isalpha():
        safe_devname = "T" + safe_devname  # Prefix with a letter like 'T'

    new_label = new_devname.replace("_", " ")
    print(f"\nCloning '{original_template}' -> '{safe_devname}'")

    # Step 1: Create new EmailTemplate
    new_tpl = {
        "DeveloperName": safe_devname,
        "Name": new_label,
        "FolderId": tpl["FolderId"],
        "HtmlValue": tpl["HtmlValue"],
        "Encoding": tpl["Encoding"],
        "Subject": tpl["Subject"],
        "TemplateType": "custom",
        "RelatedEntityType": tpl.get("RelatedEntityType", "Account"),
        "UiType": "SFX",
        "IsActive": True
    }

    try:
        create_resp = sf.EmailTemplate.create(new_tpl)
        if not create_resp.get("success"):
            print(f"Failed to create template '{new_devname}'")
            continue

        new_id = create_resp["id"]
        print(f"[OK] Created: {new_devname} -> {new_id}")
    except Exception as e:
        print(f"Exception creating template '{new_devname}': {e}")
        continue

# ─── Write out cloned template names for Streamlit ───
with open("cloned_templates.txt", "w", encoding="utf-8") as f:
    for name in cloned_names:
        f.write(name + "\n")
