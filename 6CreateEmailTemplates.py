# 4RecordTypeUpdate.py
import openai, re, sys, textwrap, xml.etree.ElementTree as ET
from pathlib import Path

# ╭─────────────── 1. Read tab-delimited field list ───────────────────╮
if len(sys.argv) >= 3:                      # record-type + table on CLI
    raw_table = " ".join(sys.argv[2:])      # everything after RT name
elif len(sys.argv) == 2:                    # only table on CLI
    raw_table = sys.argv[1]
else:                                       # piped in
    print("Paste the tab-delimited field list, finish with Ctrl-D / Ctrl-Z:")
    raw_table = sys.stdin.read()

# ╭─────────────── 2. Helper: TSV → pipe table for GPT ─────────────────╮
def tsv_to_pipe(tsv: str) -> str:
    rows = [" | ".join(re.split(r"\t+", r.strip()))
            for r in tsv.splitlines() if r.strip()]
    if len(rows) >= 2 and not re.match(r"-{3,}", rows[1]):          # add header line
        rows.insert(1, " | ".join("---" for _ in rows[0].split("|")))
    return "\n".join(rows)

pipe_table = tsv_to_pipe(raw_table)

# ╭─────────────── 3. GPT prompts (deterministic) ──────────────────────╮
SYSTEM = ("You are a Salesforce metadata expert. "
          "Return **one** <CustomField> XML block per row in the table. "
          "If Required or Type are blank assume Required=true and Type=Text. "
          "NO markdown, no commentary – XML only."
          "Sample Output"
            "<CustomField>"
            "<fullName>Bond_Issuer__c</fullName>"
            "<label>Bond Issuer</label>"
            "<required>true</required>"
            "<type>Text</type>"
            "</CustomField>")
USER   = f"You are configuring **MUSW__Application2__c**.\n\n{pipe_table}"

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

# ╭─────────────── 4. Split returned XML into blocks ───────────────────╮
def blocks(xml: str):
    return re.findall(r"(<CustomField.*?</CustomField>)", xml, flags=re.DOTALL)

# ╭─────────────── 5. Helper to guarantee a child tag exists ───────────╮
def ensure_child(root: ET.Element, tag: str, value: str):
    """Ensure <tag> exists and has .text = value."""
    el = root.find(tag)
    if el is None:
        el = ET.SubElement(root, tag)
    el.text = value
    return el

# ╭─────────────── 6. Patch every block for SFDC rules ─────────────────╮
# ----------------------------------------------------------------------
# REPLACE the old fix_block() with THIS version
# ----------------------------------------------------------------------
def fix_block(block: str) -> str:
    """
    - Adds length ONLY to Text-based fields
    - For LongTextArea uses the legal minimum (256) and strips <required>
    - Adds precision/scale defaults for Number
    - Removes <required> from Address
    - Leaves Email, Phone, Date, Lookup, etc. untouched
    """
    ns = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", ns)
    root  = ET.fromstring(block)
    ftype = (root.findtext("type") or "").strip()

    # utility ------------------------------------------------------------
    def ensure(tag, value):
        el = root.find(tag)
        if el is None:
            el = ET.SubElement(root, tag)
        el.text = value

    # -------- TEXT ------------------------------------------------------
    if ftype in ("Text", ""):                     # "" == unknown → treat as Text
        ensure("length", "30")

    # -------- LONG TEXT -------------------------------------------------
    elif ftype == "LongTextArea":
        ensure("length",       "256")             # Salesforce minimum
        ensure("visibleLines", "3")
        req = root.find("required")               # LTA cannot be required
        if req is not None:
            root.remove(req)

    # -------- NUMBER ----------------------------------------------------
    elif ftype == "Number":
        ensure("precision", "18")
        ensure("scale",     "0")

    # -------- ADDRESS ---------------------------------------------------
    elif ftype == "Address":
        req = root.find("required")
        if req is not None:
            root.remove(req)

    # -------- everything else (Email, Phone, Date, Lookup…) ------------
    # no automatic length, no extra tweaks

    # pretty-print (optional)
    xml = ET.tostring(root, encoding="unicode")
    return textwrap.indent(re.sub(r">\s*<", ">\n<", xml), "    ").strip()


# ╭─────────────── 7. Write each field to disk ──────────────────────────╮
FIELDS_DIR = Path("force-app/main/default/objects/MUSW__Application2__c/fields")
FIELDS_DIR.mkdir(parents=True, exist_ok=True)

def api_name(block: str):
    m = re.search(r"<fullName>(.*?)</fullName>", block)
    return m.group(1) if m else None

for raw in blocks(resp):
    patched = fix_block(raw)
    name    = api_name(patched)
    if not name:
        print("⚠️  Skipping block without <fullName>")
        continue
    out_file = FIELDS_DIR / f"{name}.field-meta.xml"
    out_file.write_text(patched, encoding="utf-8")
    print(f"✅  Saved {out_file}")

