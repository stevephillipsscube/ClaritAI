# CreateOrUpdateRecordType.py
from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
import subprocess
import shutil
import io
import re

# --- Salesforce metadata namespace (define ONCE, used everywhere) ------------
META_NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", META_NS)   # ensures a single default xmlns on serialize
NS_FNAME = f"{{{META_NS}}}fullName"


# --- Ensure UTF-8 console (Windows-safe) -------------------------------------
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# --- Load environment variables ---------------------------------------------
load_dotenv(override=True)

username = os.getenv("SF_USERNAME")
password = os.getenv("SF_PASSWORD")
security_token = os.getenv("SF_SECURITY_TOKEN")
domain = os.getenv("SF_DOMAIN", "login")
org_alias = (os.getenv("SF_ORG_ALIAS") or os.getenv("SF_TARGET_ORG") or "clarit-org").strip()

if not all([username, password, security_token]):
    print("❌ Missing Salesforce credentials in .env (SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN)")
    sys.exit(1)

# --- Args --------------------------------------------------------------------
# Accept:
#   python CreateOrUpdateRecordType.py "Record Type Label" [true|false]
#   python CreateOrUpdateRecordType.py --permit "Record Type Label"
#   python CreateOrUpdateRecordType.py --application "Record Type Label"
argv = sys.argv[1:]

if not argv:
    print("❌ Usage:")
    print("   python CreateOrUpdateRecordType.py \"Record Type Label\" [is_permit]")
    print("   python CreateOrUpdateRecordType.py --permit \"Record Type Label\"")
    print("   python CreateOrUpdateRecordType.py --application \"Record Type Label\"")
    sys.exit(1)

is_permit = False
label = None

# flag forms
if argv[0].lower() in ("--permit", "-p"):
    is_permit = True
    if len(argv) < 2:
        print("❌ Missing label after --permit")
        sys.exit(1)
    label = argv[1]
elif argv[0].lower() in ("--application", "-a"):
    is_permit = False
    if len(argv) < 2:
        print("❌ Missing label after --application")
        sys.exit(1)
    label = argv[1]
else:
    # positional label first; optional bool second
    label = argv[0]
    if len(argv) >= 2:
        is_permit = argv[1].lower() in ("true", "1", "yes", "y")

def to_dev_name(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return s

developer_name = to_dev_name(label)
sobject_type = "MUSW__Permit2__c" if is_permit else "MUSW__Application2__c"
record_type_api_name = f"{sobject_type}.{developer_name}"

print(f"[MODE] {'PERMIT' if is_permit else 'APPLICATION'} → using object {sobject_type}")
print(f"[INFO] Label: {label} | DeveloperName: {developer_name}")

# --- Connect to Salesforce ----------------------------------------------------
try:
    sf = Salesforce(username=username, password=password, security_token=security_token, domain=domain)
except Exception as e:
    print(f"❌ Login failed: {e}")
    sys.exit(1)

# --- Create RecordType in org if missing -------------------------------------
existing = sf.query_all(f"""
    SELECT Id FROM RecordType
    WHERE SobjectType = '{sobject_type}' AND DeveloperName = '{developer_name}'
""")

if existing.get("totalSize", 0) > 0:
    print(f"⚠️ RecordType '{record_type_api_name}' already exists. Skipping creation.")
else:
    payload = {
        "DeveloperName": developer_name,
        "Name": label,
        "SobjectType": sobject_type,
        "Description": f"Programmatically created record type for {label}"
    }
    try:
        response = sf.RecordType.create(payload)
        if response.get("success"):
            print(f"[✅] Created RecordType: {label} (Id: {response['id']})")
        else:
            print(f"[❌] Failed to create RecordType: {response}")
            sys.exit(1)
    except Exception as e:
        print(f"[❌] Failed to create RecordType: {e}")
        sys.exit(1)

# --- Update Admin Profile with RecordType visibility -------------------------
ns = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", ns)
profile_path = Path("force-app/main/default/profiles/Admin.profile-meta.xml")

if not profile_path.exists():
    print(f"[❌] Profile XML not found: {profile_path}")
    sys.exit(1)

tree = ET.parse(profile_path)
root = tree.getroot()

existing_rtv = any(
    el.find(f"{{{ns}}}recordType") is not None and el.find(f"{{{ns}}}recordType").text == record_type_api_name
    for el in root.findall(f"{{{ns}}}recordTypeVisibilities")
)

if not existing_rtv:
    rtv = ET.Element(f"{{{ns}}}recordTypeVisibilities")
    ET.SubElement(rtv, f"{{{ns}}}recordType").text = record_type_api_name
    ET.SubElement(rtv, f"{{{ns}}}default").text = "false"
    ET.SubElement(rtv, f"{{{ns}}}visible").text = "true"
    root.append(rtv)
    tree.write(profile_path, encoding="UTF-8", xml_declaration=True)
    print("[✅] Profile XML updated with recordType visibility.")
else:
    print("[SKIPPED] Profile already has recordType visibility.")

# --- Find custom fields for THIS object --------------------------------------
fields_dir = Path(f"force-app/main/default/objects/{sobject_type}/fields")
custom_field_paths = list(fields_dir.glob("*.field-meta.xml"))
# --- SANITY: show absolute path so we're deploying the files we think we are
print(f"[PATH] fields_dir = {fields_dir.resolve()}")

# --- BRUTAL REPAIR PASS: ensure exactly one default xmlns on every file ------
import textwrap

_OPENING_CF_RE = re.compile(r"<\s*CustomField\b[^>]*>", re.IGNORECASE | re.DOTALL)
_XMLNS_DEFAULT_ATTR_RE = re.compile(r"""\bxmlns\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)

def _brutal_repair_xmlns(p: Path) -> bool:
    """
    1) Strip *all* default xmlns=... from the opening <CustomField ...> tag (text)
    2) Parse with ET
    3) Retag into META_NS (never set root.set('xmlns', ...))
    4) Delete any xmlns* attributes from attrib
    5) Re-serialize so ET emits a single default xmlns from the tag namespace
    Returns True if file changed.
    """
    original = p.read_text(encoding="utf-8", errors="ignore")

    m = _OPENING_CF_RE.search(original)
    if m:
        opening = m.group(0)
        opening_clean = _XMLNS_DEFAULT_ATTR_RE.sub("", opening)
        opening_clean = re.sub(r"\s{2,}", " ", opening_clean).replace(" >", ">")
        txt = original[:m.start()] + opening_clean + original[m.end():]
    else:
        txt = original

    changed = (txt != original)

    try:
        root = ET.fromstring(txt)
    except ET.ParseError as e:
        print(f"❌ {p.name}: still not parseable after brutal repair: {e}")
        # Show what the opening tag looks like
        om = _OPENING_CF_RE.search(txt)
        if om:
            opening = om.group(0).replace("\n", " ")
            print(f"   ↳ opening tag: {opening[:200]}{' …' if len(opening)>200 else ''}")
        return changed

    # Retag into namespace (DO NOT set xmlns attribute directly)
    if not root.tag.startswith("{"):
        root.tag = f"{{{META_NS}}}{root.tag}"
        changed = True

    # Remove any direct xmlns / xmlns:* attributes
    for k in list(root.attrib):
        if k == "xmlns" or k.startswith("{http://www.w3.org/2000/xmlns/}"):
            del root.attrib[k]
            changed = True

    if changed:
        out = ET.tostring(root, encoding="unicode")
        out = re.sub(r">\s*<", ">\n<", out)
        out = '<?xml version="1.0" encoding="UTF-8"?>\n' + out
        p.write_text(out, encoding="utf-8")

    return changed

print("[REPAIR] Forcing single-default-xmlns on all field files …")
repaired = 0
for p in sorted(custom_field_paths):
    if _brutal_repair_xmlns(p):
        repaired += 1
        print(f"   • repaired {p.name}")
print(f"[REPAIR] repaired files: {repaired}/{len(custom_field_paths)}")


if not custom_field_paths:
    print("⚠️ No custom fields found to deploy for this object.")
else:
    print(f"[INFO] Found {len(custom_field_paths)} custom field(s) to deploy.")

# --- Field XML normalizer + validator (runs before deploy) -------------------

# match default xmlns in either " or ' quotes

# --- Strong cleaner: remove ALL default xmlns= from the opening <CustomField ...> tag
_OPENING_CF_RE = re.compile(r"<\s*CustomField\b[^>]*>", re.IGNORECASE | re.DOTALL)
_XMLNS_DEFAULT_ATTR_RE = re.compile(r"""\bxmlns\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)

def _strip_all_default_xmlns_in_opening_tag(txt: str) -> str:
    """
    Find the opening <CustomField ...> tag (case-insensitive, multiline),
    strip *all* default xmlns=... attributes from it, and splice it back.
    We’ll set the single correct default xmlns after parsing.
    """
    m = _OPENING_CF_RE.search(txt)
    if not m:
        return txt
    opening = m.group(0)

    # Remove every default xmlns=... from the opening tag
    cleaned_opening = _XMLNS_DEFAULT_ATTR_RE.sub("", opening)

    # Also collapse any excessive whitespace introduced
    cleaned_opening = re.sub(r"\s{2,}", " ", cleaned_opening)
    cleaned_opening = cleaned_opening.replace(" >", ">")

    return txt[:m.start()] + cleaned_opening + txt[m.end():]



def _normalize_field_file(p: Path) -> tuple[bool, str]:
    """
    1) Remove ALL default xmlns=... from opening <CustomField ...> (text-level)
    2) Parse with ET
    3) Force exactly one default xmlns (META_NS)
    4) Ensure exactly one <fullName> (namespaced)
    5) Pretty reserialize
    """
    original = p.read_text(encoding="utf-8")
    txt = _strip_all_default_xmlns_in_opening_tag(original)
    changed = (txt != original)

    # Parse now that the duplicate attributes are gone
    try:
        root = ET.fromstring(txt)
    except ET.ParseError as e:
        return (False, f"XML parse error pre-normalize: {e}")

    # Force single default xmlns on root
 # Ensure namespaced tag, never set xmlns directly
# 3) ensure element is in META_NS by changing the tag, not by setting an attribute
    if not root.tag.startswith("{"):
        root.tag = f"{{{META_NS}}}{root.tag}"
        changed = True

    # Remove any explicit xmlns attributes (default ns will be emitted from register_namespace)
    for k in list(root.attrib):
        if k == "xmlns" or k.startswith("{http://www.w3.org/2000/xmlns/}"):
            del root.attrib[k]
            changed = True


    # Enforce exactly one <fullName> (namespaced)
    fns = root.findall(NS_FNAME)
    reason_bits = []
    if len(fns) == 0:
        ET.SubElement(root, NS_FNAME).text = p.stem
        changed = True
        reason_bits.append("added missing <fullName> (ns)")
    elif len(fns) > 1:
        keep = fns[0]
        for extra in fns[1:]:
            root.remove(extra)
        changed = True
        reason_bits.append(f"removed {len(fns)-1} duplicate <fullName> (ns)")

    # Write back only if changed or if the opener was modified
    if changed:
        out = ET.tostring(root, encoding="unicode")
        # Pretty-ish
        out = re.sub(r">\s*<", ">\n<", out)
        out = '<?xml version="1.0" encoding="UTF-8"?>\n' + out
        p.write_text(out, encoding="utf-8")
        try:
            p.touch()
        except Exception:
            pass

    return (changed, "; ".join(reason_bits) or "ok")

# --- SANITY: show absolute path so we're deploying the files we think we are
print(f"[PATH] fields_dir = {fields_dir.resolve()}")

# --- BRUTAL REPAIR PASS: ensure exactly one default xmlns on every file ------
import textwrap

_OPENING_CF_RE = re.compile(r"<\s*CustomField\b[^>]*>", re.IGNORECASE | re.DOTALL)
_XMLNS_DEFAULT_ATTR_RE = re.compile(r"""\bxmlns\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)

def _brutal_repair_xmlns(p: Path) -> bool:
    """
    1) Strip *all* default xmlns=... from the opening <CustomField ...> tag (text)
    2) Parse with ET
    3) Retag into META_NS (never set root.set('xmlns', ...))
    4) Delete any xmlns* attributes from attrib
    5) Re-serialize so ET emits a single default xmlns from the tag namespace
    Returns True if file changed.
    """
    original = p.read_text(encoding="utf-8", errors="ignore")

    m = _OPENING_CF_RE.search(original)
    if m:
        opening = m.group(0)
        opening_clean = _XMLNS_DEFAULT_ATTR_RE.sub("", opening)
        opening_clean = re.sub(r"\s{2,}", " ", opening_clean).replace(" >", ">")
        txt = original[:m.start()] + opening_clean + original[m.end():]
    else:
        txt = original

    changed = (txt != original)

    try:
        root = ET.fromstring(txt)
    except ET.ParseError as e:
        print(f"❌ {p.name}: still not parseable after brutal repair: {e}")
        om = _OPENING_CF_RE.search(txt)
        if om:
            opening = om.group(0).replace("\n", " ")
            print(f"   ↳ opening tag: {opening[:200]}{' …' if len(opening)>200 else ''}")
        return changed

    # Retag into namespace (DO NOT set an 'xmlns' attribute directly)
    if not root.tag.startswith("{"):
        root.tag = f"{{{META_NS}}}{root.tag}"
        changed = True

    # Remove any direct xmlns / xmlns:* attributes
    for k in list(root.attrib):
        if k == "xmlns" or k.startswith("{http://www.w3.org/2000/xmlns/}"):
            del root.attrib[k]
            changed = True

    if changed:
        out = ET.tostring(root, encoding="unicode")
        out = re.sub(r">\s*<", ">\n<", out)
        out = '<?xml version="1.0" encoding="UTF-8"?>\n' + out
        p.write_text(out, encoding="utf-8")

    return changed

print("[REPAIR] Forcing single-default-xmlns on all field files …")
repaired = 0
for p in sorted(custom_field_paths):
    if _brutal_repair_xmlns(p):
        repaired += 1
        print(f"   • repaired {p.name}")
print(f"[REPAIR] repaired files: {repaired}/{len(custom_field_paths)}")


def _validate_fields_dir(dir_path: Path) -> bool:
    ok = True
    for p in sorted(dir_path.glob("*.field-meta.xml")):
        raw = p.read_text(encoding="utf-8", errors="ignore")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            m = re.search(r"<\s*CustomField\b[^>]*>", raw, flags=re.IGNORECASE | re.DOTALL)
            opening = (m.group(0) if m else "<no opening tag>")
            opening = opening.replace("\n", " ")
            print(f"❌ {p}: XML parse error: {e}")
            print(f"   ↳ opening tag seen by parser: {opening[:200]}{' …' if len(opening)>200 else ''}")
            ok = False
            continue

        # Exactly one <fullName> (namespaced or bare)
        namespaced_count = len(root.findall(f"{{{META_NS}}}fullName"))
        bare_count = len(root.findall("fullName"))
        count = namespaced_count or bare_count
        if count != 1:
            print(f"❌ {p}: has {count} <fullName> elements (expected 1)")
            ok = False
    return ok

# Show what the first line(s) look like post-repair
for p in sorted(custom_field_paths):
    head = Path(p).read_text(encoding="utf-8", errors="ignore").splitlines()[:2]
    opening = " ".join(head)
    if opening:
        dup = len(re.findall(r'\bxmlns\s*=', opening))
        print(f"[HEAD] {p.name}: xmlns_count_in_first_two_lines={dup}")



def _opening_tag_xmlns_count(p: Path) -> int:
    """Return how many default xmlns=... occur in the opening <CustomField ...> tag."""
    txt = p.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"<CustomField\b[^>]*>", txt, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return 0
    opening = m.group(0)
    return len(re.findall(r"\bxmlns\s*=\s*(?:\"[^\"]*\"|'[^']*')", opening))


# --- Clean up bad <fullName> before deploy (for this object only) ------------
if custom_field_paths:
    print(f"[PRECHECK] normalizing <fullName> in {fields_dir} …")
    fixed = 0
    for p in custom_field_paths:
        changed, reason = _normalize_field_file(p)
        if changed:
            fixed += 1
            print(f"   • {p.name}: {reason}")
    print(f"[PRECHECK] normalized files: {fixed}/{len(custom_field_paths)}")

    print(f"[PRECHECK] validating {fields_dir} …")
    if not _validate_fields_dir(fields_dir):
        print("⛔ Fix the above field files and re-run (validator failed).")
        sys.exit(1)

    # Extra: show any file that still has >1 default xmlns after cleaning
leftovers = []
for p in custom_field_paths:
    c = _opening_tag_xmlns_count(p)
    if c > 1:
        leftovers.append((p.name, c))
        txt = Path(p).read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"<CustomField\b[^>]*>", txt, flags=re.IGNORECASE | re.DOTALL)
        opening = (m.group(0).replace("\n", " ")[:300] + "...") if m else "<no opening tag>"
        print(f"❗ {p.name}: opening tag still has {c} default xmlns -> {opening}")

if leftovers:
    print(f"[PRECHECK] Files with extra xmlns: {len(leftovers)} (see above).")


# --- Deploy metadata (profile + fields) --------------------------------------
print("[INFO] Deploying metadata to Salesforce...")
sf_cli = shutil.which("sf") or r"C:\Program Files\sf\bin\sf.cmd"
if not Path(sf_cli).exists():
    print(f"[ERROR] sf CLI not found at: {sf_cli}")
    sys.exit(1)

deploy_paths = [str(profile_path)] + [str(p) for p in custom_field_paths]

deploy_cmd = [
    sf_cli, "project", "deploy", "start",
    "--source-dir", str(profile_path),
    "--source-dir", str(fields_dir),
    "--target-org", org_alias
]

deploy_result = subprocess.run(
    deploy_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8"
)

# Print compactly; your Streamlit layer can further summarize this
print(deploy_result.stdout)
if deploy_result.returncode == 0:
    print("[✅] Metadata deployment successful.")
else:
    print("[❌] Deployment failed:")
    print(deploy_result.stderr)

# --- Output final status echo (for Streamlit parsers) -------------------------
if deploy_result.returncode == 0:
    print("[✅] Deployment successful.")
else:
    print("[❌] Deployment failed.")

def _dedupe_single(root: ET.Element, tag: str):
    els = root.findall(tag)
    if not els:
        return None
    keep = els[0]
    # remove any extras
    for extra in els[1:]:
        parent = root  # fullName is top-level in CustomField
        parent.remove(extra)
    return keep


# --- Page Layout updater ------------------------------------------------------
def update_page_layout(custom_field_names, layout_path):
    ns = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", ns)

    tree = ET.parse(layout_path)
    root = tree.getroot()

    detail_sections = root.findall(f".//{{{ns}}}detailLayoutSections")
    if not detail_sections:
        print("[⚠️] No <detailLayoutSections> found in layout.")
        return

    target_section = detail_sections[-1]  # append to last section

    for field in custom_field_names:
        row = ET.SubElement(target_section, f"{{{ns}}}layoutRows")

        item = ET.SubElement(row, f"{{{ns}}}layoutItems")
        ET.SubElement(item, f"{{{ns}}}field").text = field
        ET.SubElement(item, f"{{{ns}}}behavior").text = "Edit"
        ET.SubElement(item, f"{{{ns}}}required").text = "false"

        ET.SubElement(row, f"{{{ns}}}numItems").text = "1"
        ET.SubElement(row, f"{{{ns}}}tabOrder").text = "Left-Right"

    tree.write(layout_path, encoding="UTF-8", xml_declaration=True)
    print(f"[✅] Updated layout: {layout_path.name}")

# --- Push new fields into the record-type-specific layout --------------------
layout_name = f"{sobject_type}-{developer_name}.layout-meta.xml"
layout_path = Path("force-app/main/default/layouts") / layout_name

if not layout_path.exists():
    print(f"[⚠️] Expected layout file not found: {layout_path}. "
          f"Generate or retrieve the layout first, then re-run this script.")
else:
    custom_field_api_names = [p.stem for p in custom_field_paths]  # stems are fullName values
    update_page_layout(custom_field_api_names, layout_path)

    print(f"[INFO] Deploying updated layout {layout_name} …")
    layout_deploy = subprocess.run(
        [
            sf_cli, "project", "deploy", "start",
            "--source-dir", str(layout_path),
            "--target-org", org_alias
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8"
    )

    if layout_deploy.returncode == 0:
        print("[✅] Layout deployment successful.")
    else:
        print("[❌] Layout deployment failed:")
        print(layout_deploy.stderr)
