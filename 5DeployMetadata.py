import os
import subprocess
from pathlib import Path
import shutil
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
ORG_ALIAS = os.getenv("SF_ORG_ALIAS", "clarit-org")

# Metadata files to deploy
metadata_files = [
    "force-app/main/default/globalValueSets/MUSW__Application_Types.globalValueSet-meta.xml",
    "force-app/main/default/objects/MUSW__Application2__c/fields/MUSW__Type2__c.field-meta.xml",
    "force-app/main/default/objects/MUSW__Application2__c/recordTypes/Business_License.recordType-meta.xml",
    "force-app/main/default/objects/MUSW__Application2__c/recordTypes/Mobile_Home_Permit.recordType-meta.xml"
]

# Filter out files that don’t exist
existing_files = []
for f in metadata_files:
    if Path(f).exists():
        print(f"✅ Queued for deployment: {f}")
        existing_files.append(f)
    else:
        print(f"⚠️ Skipping missing file: {f}")

# Abort if nothing to deploy
if not existing_files:
    print("❌ No valid metadata files found to deploy.")
    exit(1)


# Resolve full path to the Salesforce CLI
sf_path = shutil.which("sf")
if sf_path is None:
    print("❌ Salesforce CLI 'sf' command not found in PATH.")
    exit(1)

# Updated deploy command using full path
deploy_cmd = [
    sf_path, "project", "deploy", "start",
    "--metadata", "GlobalValueSet:MUSW__Application_Types",
    "--metadata", "CustomField:MUSW__Application2__c.MUSW__Type2__c",
    "--metadata", "RecordType:MUSW__Application2__c.Business_License",
    "--target-org", ORG_ALIAS
]


print("🚀 Deploying metadata to Salesforce...")
subprocess.run(deploy_cmd, check=True)
