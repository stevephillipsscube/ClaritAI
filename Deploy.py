import subprocess, shutil
from pathlib import Path

FLOW_NAME = "Modular_Home_Permit"
ORG_ALIAS = "steve.phillips@scubeenterprise.com.owa.clariti"
OBJ = "MUSW__Application2__c"  # <- new

SF = shutil.which("sf")
if not SF:
    raise EnvironmentError("sf CLI not in PATH")

project = Path(__file__).parent
obj_dir = project / "force-app" / "main" / "default" / "objects" / OBJ
flow_file = project / "force-app" / "main" / "default" / "flows" / f"{FLOW_NAME}.flow-meta.xml"

def run(cmd):
    print(" ".join(map(str, cmd)))
    subprocess.run(cmd, check=True)

# deploy object (and pick-list) folder
run([SF, "project", "deploy", "start",
     "--source-dir", str(obj_dir),
     "--target-org", ORG_ALIAS])

# deploy flow
run([SF, "project", "deploy", "start",
     "--metadata", f"Flow:{FLOW_NAME}",
     "--target-org", ORG_ALIAS])

print("✅ All done.")
