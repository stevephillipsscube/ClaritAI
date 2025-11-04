# 4RecordTypeUpdate.py
import openai, re, sys, textwrap, xml.etree.ElementTree as ET
from pathlib import Path

# ========================= 0) Constants =========================
PACKAGE_NS  = "MUSW__"                      # managed package namespace to strip
OBJECT_API  = "MUSW__Application2__c"       # target object (app mode)
FIELDS_DIR  = Path(f"force-app/main/default/objects/{OBJECT_API}/fields")
META_NS     = "http://soap.sforce.com/2006/04/metadata"

ET.register_namespace("", META_NS)

# ========================= 1) Read input =========================
if len(sys.argv) >= 3:                      # record-type + table on CLI
    raw_table = " ".join(sys.argv[2:])      # everything after RT name
elif len(sys.argv) == 2:                    # only table on CLI
    raw_table = sys.argv[1]
else:                                       # piped in
    print("Paste the tab-delimited field list, finish with Ctrl-D / Ctrl-Z:")
    raw_table = sys.stdin.read()

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

def sanitize_api_name(raw: str, object_api: str = OBJECT_API, ns: str = PACKAGE_NS) -> str:
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

# ========================= 5) Patch each XML block =========================
def fix_block(block: str) -> str:
    """
    - Add xmlns on root
    - Rename <active> → <isActive> (picklist values)
    - Default lengths/precision for types
    """
    ns = META_NS
    root = ET.fromstring(block)

    # Ensure xmlns on root (without rewriting every tag to ns-prefix)
    if "xmlns" not in root.attrib:
        root.set("xmlns", ns)

    ftype = (root.findtext("type") or "").strip()

    # rename any <active> to <isActive> (top-level or nested)
    for bad in root.findall(".//active"):
        bad.tag = "isActive"

    # Defaults by type
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

    # Pretty print
    xml_txt = ET.tostring(root, encoding="unicode")
    xml_txt = re.sub(r">\s*<", ">\n<", xml_txt)
    return textwrap.indent(xml_txt, "    ").strip()

def api_name(block: str):
    m = re.search(r"<fullName>(.*?)</fullName>", block)
    return m.group(1) if m else None

# ========================= 6) Write fields =========================
FIELDS_DIR.mkdir(parents=True, exist_ok=True)

for raw in blocks(resp):
    patched = fix_block(raw)

    # Enforce sanitized <fullName> in XML and filename
    try:
        root = ET.fromstring(patched)
        # ensure xmlns (if previous pretty-printing stripped it)
        if "xmlns" not in root.attrib:
            root.set("xmlns", META_NS)

        fn = root.find("fullName")
        if fn is None or not (fn.text or "").strip():
            print("⚠️  Skipping block without <fullName>")
            continue

        safe_name = sanitize_api_name(fn.text, object_api=OBJECT_API)
        fn.text = safe_name

        # Write back to text (with declaration)
        final_xml = ET.tostring(root, encoding="unicode")
        final_xml = re.sub(r">\s*<", ">\n<", final_xml)
        final_xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + final_xml

        out_file = FIELDS_DIR / f"{safe_name}.field-meta.xml"
        out_file.write_text(final_xml, encoding="utf-8")
        print(f"✅  Saved {out_file}")

    except ValueError as ve:
        print(f"⛔  Skipping field due to invalid API name: {ve}")
    except ET.ParseError as pe:
        print(f"⛔  Skipping field due to XML parse error: {pe}")
