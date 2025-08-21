# deploy_object_and_flow.py
import subprocess, shutil
from pathlib import Path

ORG = "steve.phillips@scubeenterprise.com.owa.clariti"
OBJ_DIR = Path(__file__).parent / "force-app/main/default/objects/MUSW__Application2__c"
FLOW   = "Modular_Home_Permit"
SF     = shutil.which("sf") or shutil.which("sfdx")

if not SF:
    raise EnvironmentError("Salesforce CLI not in PATH")

def run(cmd):
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)

# 1️⃣ deploy the object folder (includes all fields)
run([SF, "project", "deploy", "start",
     "--source-dir", str(OBJ_DIR),
     "--target-org", ORG])

# 2️⃣ deploy the flow
run([SF, "project", "deploy", "start",
     "--metadata", f"Flow:{FLOW}",
     "--target-org", ORG])


if not SF:
    raise EnvironmentError("CLI not in PATH")

project = Path(__file__).parent
obj_dir = project / "force-app" / "main" / "default" / "objects" / OBJ
flow_md = f"Flow:{FLOW}"

# -------- manifest helpers ----------
def write_manifest(xml: str) -> Path:
    f = tempfile.NamedTemporaryFile("w+", delete=False, suffix=".xml")
    f.write(textwrap.dedent(xml).lstrip())
    f.close()
    return Path(f.name)

# 1️⃣ manifest for the object (includes all its children automatically)
obj_manifest = write_manifest(f"""
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types>
    <members>{OBJ}</members>
    <name>CustomObject</name>
  </types>
  <version>59.0</version>
</Package>
""")

# 2️⃣ manifest for the flow only
flow_manifest = write_manifest(f"""
<Package xmlns="http://soap.sforce.com/2006/04/metadata">
  <types>
    <members>{FLOW}</members>
    <name>Flow</name>
  </types>
  <version>59.0</version>
</Package>
""")

def deploy(manifest: Path, label: str):
    print(f"🚀 Deploying {label} …")
    subprocess.run([
        SF, "project", "deploy", "start",
        "--manifest", str(manifest),
        "--target-org", ORG
    ], check=True)
    print(f"✅ {label} deploy command finished\n")

# ---------- run the two passes ----------
deploy(obj_manifest, "object (and pick-list)")
deploy(flow_manifest, "flow")
