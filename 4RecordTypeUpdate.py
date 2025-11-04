# 4RecordTypeUpdate.py
import openai, re, sys, textwrap, xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

# ========================= 0) Constants (namespace) =========================
PACKAGE_NS  = "MUSW__"
META_NS     = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", META_NS)

# ========================= 0a) Mode / args =========================
def _find_project_root(start: Path | None = None) -> Path:
    start = start or Path.cwd()
    for p in [start, *start.parents]:
        if (p / "sfdx-project.json").exists():
            return p
        if (p / "force-app").exists():
            return p
    # fallback to start, but warn
    print(f"⚠️  Could not find sfdx-project.json/force-app above {start}. Using {start} as project root.")
    return start

PROJECT_ROOT = _find_project_root(Path(__file__).resolve().parent)

def _parse_mode_and_table(argv: list[str]) -> tuple[bool, str]:
    """
    Returns (is_permit, raw_table_text). Defaults to application (False).
    Recognizes:
      --permit | -p
      --application | -a
      --is-permit=true|false|1|0|yes|no
    Remaining args (after removing flags) are treated as TSV text if present;
    otherwise, stdin is read.
    """
    is_permit = False
    remain: list[str] = []
    for a in argv:
        al = a.lower().strip()
        if al in ("--permit", "-p", "permit"):
            is_permit = True
            continue
        if al in ("--application", "-a", "application", "app"):
            is_permit = False
            continue
        if al.startswith("--is-permit="):
            v = al.split("=", 1)[1].strip()
            is_permit = v in ("1", "true", "yes", "y")
            continue
        # not a mode flag → keep
        remain.append(a)

    if remain:
        raw_table = " ".join(remain)
    else:
        print("Paste the tab-delimited field list, finish with Ctrl-D / Ctrl-Z:")
        raw_table = sys.stdin.read()
    return is_permit, raw_table

is_permit, raw_table = _parse_mode_and_table(sys.argv[1:])

OBJECT_API = "MUSW__Permit2__c" if is_permit else "MUSW__Application2__c"
FIELDS_DIR = PROJECT_ROOT / f"force-app/main/default/objects/{OBJECT_API}/fields"
print(f"[MODE] {'PERMIT' if is_permit else 'APPLICATION'} -> {OBJECT_API}")
print(f"[WRITE] Fields directory: {FIELDS_DIR}")
print(f"[ARGS] {sys.argv[1:]}")
print(f"[PARSED] is_permit={is_permit}  OBJECT_API={OBJECT_API}")
print(f"[WRITE] Fields directory: {FIELDS_DIR}")


# ========================= 2) TSV → pipe (with filtering) =========================


def tsv_to_pipe(tsv: str) -> str:
    rows = []
    for line in tsv.splitlines():
        if not line.strip():
            continue
        cols = re.split(r"\t+", line.strip())

        # Skip filtered keywords in any column (case-insensitive)
        skip_keywords = {"type", "sub type", "phase", "address", "applicant"}
        if any(any(k in c.lower() for k in skip_keywords) for c in cols):
            print(f"⏭️  Skipping row containing filtered keyword: {cols}")
            continue

        rows.append(" | ".join(cols))

    if len(rows) >= 2 and not re.match(r"-{3,}", rows[1]):  # add header rule row
        rows.insert(1, " | ".join("---" for _ in rows[0].split("|")))
    return "\n".join(rows)

pipe_table = tsv_to_pipe(raw_table)

# ========================= 3) GPT prompts =========================
SYSTEM = (
    "You are a Salesforce metadata expert. "
    "Return one <CustomField> XML block per row in the table. "
    "If Required or Type are blank assume Required=true and Type=Text. "
    "NO markdown, no commentary – XML only."
    "Sample Output"
      "<CustomField>"
      "<fullName>Bond_Issuer__c</fullName>"
      "<label>Bond Issuer</label>"
      "<required>true</required>"
      "<type>Text</type>"
      "</CustomField>"
)
USER   = f"You are configuring **{OBJECT_API}**.\n\n{pipe_table}"

resp = openai.ChatCompletion.create(
    model              = "gpt-4o",
    temperature        = 0,
    top_p              = 1,
    frequency_penalty  = 0,
    presence_penalty   = 0,
    messages=[
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": USER},
    ],
)["choices"][0]["message"]["content"]

# ========================= 4) Helpers =========================
def blocks(xml: str):
    return re.findall(r"(<CustomField.*?</CustomField>)", xml, flags=re.DOTALL)

def ensure_child(root: ET.Element, tag: str, value: str):
    el = root.find(tag)
    if el is None:
        el = ET.SubElement(root, tag)
    el.text = value
    return el

def sanitize_api_name(raw: str, object_api: str, ns: str = PACKAGE_NS) -> str:
    """
    Build a valid subscriber API name:
      - strip namespace prefix (e.g., MUSW__)
      - if 'Object.Field' given, keep the Field side
      - prohibit equal to object name
      - keep [A-Za-z0-9_], spaces -> _
      - ensure __c suffix
    """
    raw = (raw or "").strip()

    # If namespaced/object-qualified, keep trailing token
    if "." in raw:
        raw = raw.split(".")[-1].strip()

    # Strip namespace prefix
    if ns and raw.startswith(ns):
        raw = raw[len(ns):]

    # Cannot equal the object API
    if raw == object_api:
        raise ValueError(f"Invalid API name: cannot equal object API '{object_api}'")

    # Normalize
    raw = re.sub(r"\s+", "_", raw)
    raw = re.sub(r"[^A-Za-z0-9_]", "", raw)

    # Ensure suffix
    if not raw.endswith("__c"):
        raw += "__c"

    # Final sanity
    if raw == "__c" or not re.match(r"^[A-Za-z][A-Za-z0-9_]*__c$", raw):
        raise ValueError(f"Invalid API name after sanitize: '{raw}'")
    return raw

# --- helper: keep only the FIRST default xmlns on <CustomField ...> -----------
_XMLNS_DEFAULT_RE = re.compile(r'\s+xmlns\s*=\s*(?:"[^"]*"|\'[^\']*\')', re.IGNORECASE)

def _strip_extra_default_xmlns(txt: str) -> str:
    m = re.search(r'<CustomField\b[^>]*>', txt, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return txt
    opening = m.group(0)

    # find all default xmlns=... in the opening tag
    matches = list(_XMLNS_DEFAULT_RE.finditer(opening))
    if len(matches) <= 1:
        return txt  # nothing to clean

    # keep the first xmlns, drop the rest
    keep_start, keep_end = matches[0].span()
    pieces, idx = [], 0
    for i, mm in enumerate(matches):
        if i == 0:
            pieces.append(opening[idx:keep_end])  # keep first xmlns
            idx = keep_end
        else:
            pieces.append(opening[idx:mm.start()])  # skip extra xmlns
            idx = mm.end()
    pieces.append(opening[idx:])
    new_opening = ''.join(pieces)

    # splice cleaned opening tag back into the text
    return txt[:m.start()] + new_opening + txt[m.end():]


# ========================= 5) Patch each XML block =========================
def fix_block(block: str) -> ET.Element:
    """
    Parse a <CustomField> block, normalize it, and return a NEW Element
    with a SINGLE default xmlns. This guarantees no duplicate xmlns.
    """
    # Parse incoming block (it may or may not include xmlns)
    try:
        src_root = ET.fromstring(block)
    except ET.ParseError:
        # last-ditch: strip all default xmlns in the opening tag and retry
        block = re.sub(r'\s+xmlns\s*=\s*(?:"[^"]*"|\'[^\']*\')', '', block, flags=re.I)
        src_root = ET.fromstring(block)

    # Build a fresh root with exactly one default xmlns
    root = ET.Element("CustomField", {"xmlns": META_NS})

    # Copy children (elements only) into the fresh root
    for child in list(src_root):
        # clone element without any attributes on the root tag
        new = ET.Element(child.tag)
        # copy text/tail
        new.text = child.text
        new.tail = child.tail
        # copy child children
        for g in list(child):
            new.append(g)
        root.append(new)

    # ---- Normalizations on the new root ----
    # 1) <active> -> <isActive>
    for bad in root.findall(".//active"):
        bad.tag = "isActive"

    # 2) Ensure type defaults / lengths
    ftype = (root.findtext("type") or "").strip()
    def ensure(tag, value):
        el = root.find(tag)
        if el is None:
            el = ET.SubElement(root, tag)
        el.text = value

    if ftype in ("Text", ""):
        ensure("length", "30")
    elif ftype == "LongTextArea":
        ensure("length", "256")
        ensure("visibleLines", "3")
        req = root.find("required")
        if req is not None:
            root.remove(req)
    elif ftype == "Number":
        ensure("precision", "18")
        ensure("scale", "0")
    elif ftype == "Address":
        req = root.find("required")
        if req is not None:
            root.remove(req)

    return root


xml_blocks = blocks(resp)
print(f"[GEN] CustomField blocks: {len(xml_blocks)}")
if not xml_blocks:
    print("[GEN] No blocks generated. Prompt preview:")
    print(USER[:400])

print(f"[CHECK] Listing current files in {FIELDS_DIR}:")
for p in sorted(FIELDS_DIR.glob("*.field-meta.xml")):
    try:
        ts = datetime.fromtimestamp(p.stat().st_mtime).isoformat(sep=" ", timespec="seconds")
    except Exception:
        ts = "n/a"
    print(f" - {p.name}    mtime={ts}")

# ========================= 6) Write fields =========================
FIELDS_DIR.mkdir(parents=True, exist_ok=True)

def derive_api_from_label(label_text: str) -> str:
    return sanitize_api_name(label_text, object_api=OBJECT_API)

for raw in blocks(resp):
    try:
        # normalized element with a single default xmlns
        root = fix_block(raw)

        # ensure <fullName> exists and is valid
        fn = root.find("fullName")
        lbl = root.find("label")

        if fn is None or not (fn.text or "").strip():
            label_text = (lbl.text or "").strip() if lbl is not None else ""
            if not label_text:
                m = re.search(r"<label>(.*?)</label>", raw, flags=re.DOTALL|re.IGNORECASE)
                label_text = (m.group(1).strip() if m else "")
            if not label_text:
                print("⛔  No <fullName> and no usable <label>; skipping block.")
                continue
            safe_name = derive_api_from_label(label_text)
            fn = ET.SubElement(root, "fullName")
            fn.text = safe_name
        else:
            safe_name = sanitize_api_name(fn.text, object_api=OBJECT_API)
            fn.text = safe_name

        # Normalize a few common user-friendly type strings
        ty = (root.findtext("type") or "").strip()
        if ty.lower() in ("phone number", "phone"):
            ensure_child(root, "type", "Phone")
        elif ty.lower() in ("e-mail", "email"):
            ensure_child(root, "type", "Email")
        elif ty.lower() in ("checkbox", "check box", "boolean"):
            ensure_child(root, "type", "Checkbox")
        elif ty.lower() == "currency":
            ensure_child(root, "type", "Currency")
        elif ty.lower() == "date":
            ensure_child(root, "type", "Date")
        elif ty.lower().startswith("lookup"):
            ensure_child(root, "type", "Lookup")
            m = re.search(r"lookup\s*\(([^)]+)\)", ty, flags=re.I)
            target = (m.group(1).strip() if m else "")
            if target:
                ensure_child(root, "referenceTo", target)

        # Serialize (ElementTree will emit exactly one default xmlns)
        xml_txt = ET.tostring(root, encoding="unicode")
        xml_txt = re.sub(r">\s*<", ">\n<", xml_txt)
        final_xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_txt

        out_file = FIELDS_DIR / f"{fn.text}.field-meta.xml"
        out_file.write_text(final_xml, encoding="utf-8")
        out_file.touch()
        print(f"✅  Saved {out_file}")

    except ValueError as ve:
        print(f"⛔  Skipping field due to invalid API name: {ve}")
    except ET.ParseError as pe:
        print(f"⛔  Skipping field due to XML parse error: {pe}")

# --- Post-write sanity check: duplicate default xmlns on <CustomField ...> ---
def _read_opening_tag(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        buf = []
        while True:
            ch = f.read(1)
            if not ch:
                break
            buf.append(ch)
            if ch == ">":
                break
    return "".join(buf)

dup = 0
for p in sorted(FIELDS_DIR.glob("*.field-meta.xml")):
    opening = _read_opening_tag(p)
    # count *default* xmlns=... on the opening <CustomField ...> tag
    matches = re.findall(r"\bxmlns\s*=\s*(?:\"[^\"]*\"|'[^']*')", opening, flags=re.IGNORECASE)
    if len(matches) > 1:
        print(f"❗ STILL DUPLICATE XMLNS in {p.name}: {opening.strip()[:220]} …")
        dup += 1

print(f"[CHECK] files with duplicate default xmlns: {dup}")
