# CreateOrUpdateRecordType.py  — minimal, no validators, no file edits
from simple_salesforce import Salesforce
from dotenv import load_dotenv
from pathlib import Path
import os, sys, io, re, shutil, subprocess, xml.etree.ElementTree as ET

# --- UTF-8 console (Windows-safe)
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Args:  CreateOrUpdateRecordType.py [--permit|--application] "Record Type Label" [--no-profile]
argv = sys.argv[1:]
if not argv:
    print("Usage:")
    print('  python CreateOrUpdateRecordType.py --permit "Record Type Label" [--no-profile]')
    print('  python CreateOrUpdateRecordType.py --application "Record Type Label" [--no-profile]')
    sys.exit(1)

is_permit = None
label = None
update_profile = True
i = 0
while i < len(argv):
    a = argv[i].lower()
    if a in ("--permit", "-p"):
        is_permit = True
        i += 1
    elif a in ("--application", "-a"):
        is_permit = False
        i += 1
    elif a == "--no-profile":
        update_profile = False
        i += 1
    else:
        label = argv[i]
        i += 1

if is_permit is None or not label:
    print("❌ Need a mode (--permit/--application) and a record type label.")
    sys.exit(1)

def to_dev_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return s

# --- Env
load_dotenv(override=True)
username = os.getenv("SF_USERNAME")
password = os.getenv("SF_PASSWORD")
security_token = os.getenv("SF_SECURITY_TOKEN")
domain = os.getenv("SF_DOMAIN", "login")
org_alias = (os.getenv("SF_ORG_ALIAS") or os.getenv("SF_TARGET_ORG") or "clarit-org").strip()

if not all([username, password, security_token]):
    print("❌ Missing Salesforce creds in .env (SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN)")
    sys.exit(1)

# --- Mode
sobject_type = "MUSW__Permit2__c" if is_permit else "MUSW__Application2__c"
developer_name = to_dev_name(label)
record_type_api_name = f"{sobject_type}.{developer_name}"
print(f"[MODE] {'PERMIT' if is_permit else 'APPLICATION'} → {sobject_type}")
print(f"[INFO] Label: {label} | DeveloperName: {developer_name}")

# --- Connect
try:
    sf = Salesforce(username=username, password=password, security_token=security_token, domain=domain)
except Exception as e:
    print(f"❌ Login failed: {e}")
    sys.exit(1)

# --- Create Record Type if missing
q = sf.query_all(f"SELECT Id FROM RecordType WHERE SobjectType = '{sobject_type}' AND DeveloperName = '{developer_name}'")
if q.get("totalSize", 0) > 0:
    print(f"[SKIP] RecordType exists: {record_type_api_name}")
else:
    payload = {
        "DeveloperName": developer_name,
        "Name": label,
        "SobjectType": sobject_type,
        "Description": f"Created by script for {label}",
        # No BusinessProcessId for custom objects; omit unless needed
    }
    try:
        r = sf.RecordType.create(payload)
        if r.get("success"):
            print(f"[OK] Created RecordType {label} (Id: {r['id']})")
        else:
            print(f"[ERR] Failed to create RecordType: {r}")
            sys.exit(1)
    except Exception as e:
        print(f"[ERR] Failed to create RecordType: {e}")
        sys.exit(1)

# --- (Optional) Add Admin.profile recordTypeVisibilities
if update_profile:
    ns = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", ns)
    profile_path = Path("force-app/main/default/profiles/Admin.profile-meta.xml")
    if profile_path.exists():
        try:
            tree = ET.parse(profile_path)
            root = tree.getroot()
            found = False
            for el in root.findall(f"{{{ns}}}recordTypeVisibilities"):
                rt = el.find(f"{{{ns}}}recordType")
                if rt is not None and rt.text == record_type_api_name:
                    found = True
                    break
            if not found:
                rtv = ET.Element(f"{{{ns}}}recordTypeVisibilities")
                ET.SubElement(rtv, f"{{{ns}}}recordType").text = record_type_api_name
                ET.SubElement(rtv, f"{{{ns}}}default").text = "false"
                ET.SubElement(rtv, f"{{{ns}}}visible").text = "true"
                root.append(rtv)
                tree.write(profile_path, encoding="UTF-8", xml_declaration=True)
                print("[OK] Admin.profile: added recordType visibility")
            else:
                print("[SKIP] Admin.profile already has recordType visibility")
        except Exception as e:
            print(f"[WARN] Could not update Admin.profile: {e}")
    else:
        print("[WARN] Admin.profile not found; skipping profile update")
else:
    print("[SKIP] --no-profile set; not touching Admin.profile")

# --- Deploy (profile if present) + object fields (NO VALIDATION, NO EDITS)
sf_cli = shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"
if not Path(sf_cli).exists():
    print(f"❌ sf CLI not found at: {sf_cli}")
    sys.exit(1)

fields_dir = Path(f"force-app/main/default/objects/{sobject_type}/fields")
args = [sf_cli, "project", "deploy", "start", "--target-org", org_alias]

# include Admin.profile only if it exists and we didn’t disable profile step
profile_path = Path("force-app/main/default/profiles/Admin.profile-meta.xml")
if update_profile and profile_path.exists():
    args += ["--source-dir", str(profile_path)]

# include fields dir if it exists (we do not touch/modify/validate files)
if fields_dir.exists():
    args += ["--source-dir", str(fields_dir)]
else:
    print(f"[WARN] No fields dir at {fields_dir}; deploying profile only (if any)")

print("[DEPLOY]", " ".join(args))
res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8")
print(res.stdout)
if res.returncode != 0:
    print("[ERR] Deployment failed:")
    print(res.stderr)
    sys.exit(res.returncode)

print("[DONE] Deployment successful.")
