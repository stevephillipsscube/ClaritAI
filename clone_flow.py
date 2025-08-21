import subprocess
from pathlib import Path
import shutil

# Configuration
SOURCE_NAME = "Permit_Selection_Screen"
TARGET_NAME = "Modular_Home_Permit"
ORG_ALIAS = "steve.phillips@scubeenterprise.com.owa.clariti"

# Locate the Salesforce CLI
SF_CLI = shutil.which("sf")
if SF_CLI is None:
    raise EnvironmentError("❌ Salesforce CLI 'sf' not found in PATH.")

# Paths
PROJECT_DIR = Path(__file__).parent
FLOWS_DIR = PROJECT_DIR / "force-app" / "main" / "default" / "flows"
SOURCE_PATH = FLOWS_DIR / f"{SOURCE_NAME}.flow-meta.xml"
TARGET_PATH = FLOWS_DIR / f"{TARGET_NAME}.flow-meta.xml"

# Step 1: Pull the flow from the org
print(f"📥 Retrieving flow: {SOURCE_NAME} from {ORG_ALIAS}...")
subprocess.run([
    SF_CLI, "project", "retrieve", "start",
    "--metadata", f"Flow:{SOURCE_NAME}",
    "--target-org", ORG_ALIAS
], check=True)

# Step 2: Clone and modify flow
if not SOURCE_PATH.exists():
    raise FileNotFoundError(f"❌ Source flow not found at: {SOURCE_PATH}")

with open(SOURCE_PATH, "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace(SOURCE_NAME, TARGET_NAME)
content = content.replace("<label>Permit Selection", "<label>Modular Home Permit Flow")
content = content.replace("<interviewLabel>Permit Selection", "<interviewLabel>Modular Home Permit Flow")

with open(TARGET_PATH, "w", encoding="utf-8") as f:
    f.write(content)

print(f"✅ Flow cloned: {TARGET_PATH.relative_to(PROJECT_DIR)}")
