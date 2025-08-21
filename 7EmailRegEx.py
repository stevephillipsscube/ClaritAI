import re
import html
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape as xml_escape

IN_DIR = Path("generated_code")
OUT_DIR = Path("generated_code_replaced")
NS_URI = "http://soap.sforce.com/2006/04/metadata"

# ---------------- Hard-coded replacements ----------------
# Matches are case-insensitive and bracket-aware.
REPLACEMENTS = [
    # --- Replace the whole instruction phrase, including its brackets ---
    (re.compile(
        r"[\(\[\{]\s*insert\s+information\s+from\s+application\s+completeness\s+check\s+comment\s+box\s*[\)\]\}]",
        re.IGNORECASE
    ), "{{{MUSW__Milestone__c.Comments_External__c}}}"),

    # --- "[30 days from the date of this email]" (bracketed) ---
    (re.compile(
        r"[\{\[\(]\s*30\s*(?:business\s*)?days?\s*from\s*the\s*date\s*of\s*this\s*email\s*[\}\]\)]",
        re.IGNORECASE
    ), "{{{MUSW__Milestone__c.X30_Business_Days__c}}}"),

    # --- "30 days from the date of this email" (unbracketed) ---
    (re.compile(
        r"\b30\s*(?:business\s*)?days?\s*from\s*the\s*date\s*of\s*this\s*email\b",
        re.IGNORECASE
    ), "{{{MUSW__Milestone__c.X30_Business_Days__c}}}"),

    # --- Business days (bracketed or not) ---
    (re.compile(r"[\{\[\(]{0,3}\s*\b5\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X5_Business_Days__c}}}"),
    (re.compile(r"[\{\[\(]{0,3}\s*\b10\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X10_Business_Days__c}}}"),
    (re.compile(r"[\{\[\(]{0,3}\s*\b20\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X20_Business_Days__c}}}"),
    (re.compile(r"[\{\[\(]{0,3}\s*\b30\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X30_Business_Days__c}}}"),

    # --- Only replace COMMENTS when it’s explicitly bracketed (avoid normal prose) ---
    (re.compile(r"[\{\[\(]\s*comments?\s*[\}\]\)]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Comments_External__c}}}"),

    # --- RECORD TYPE (with or without brackets) ---
    (re.compile(r"\[\s*record\s*type\s*\]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_Entity__c}}}"),
    (re.compile(r"\brecord\s*type\b", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_Entity__c}}}"),

    # --- APPLICATION NUMBER / APPLICATION # (with or without brackets) ---
    (re.compile(r"\[\s*application\s*(?:number|#)\s*\]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_To_Entity__c}}}"),
    (re.compile(r"\bapplication\s*(?:number|#)\b", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_To_Entity__c}}}"),

        # --- EXPIRATION DATE ---
    # Bracketed: [expiration date] / (expiration date) / {expiration date}
    (re.compile(r"[\{\[\(]\s*expiration\s*date\s*[\}\]\)]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Expiration_Date__c}}}"),
    # Unbracketed: expiration date
    (re.compile(r"\bexpiration\s*date\b", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Expiration_Date__c}}}"),

]


def update_file(path: Path) -> tuple[bool, int]:
    """Return (changed?, num_replacements)."""
    tree = ET.parse(path)
    root = tree.getroot()

    # Find <htmlValue> with namespace
    hv = root.find(f".//{{{NS_URI}}}htmlValue")
    if hv is None or hv.text is None:
        return (False, 0)

    # Unescape the HTML stored in the XML
    html_src = html.unescape(hv.text)
    total_repl = 0
    new_html = html_src

    for patt, repl in REPLACEMENTS:
        new_html, n = patt.subn(repl, new_html)
        total_repl += n

    if total_repl == 0:
        return (False, 0)

    # Re-escape for XML storage and pretty print
    hv.text = xml_escape(new_html)
    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty = parseString(xml_bytes).toprettyxml(indent="  ")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / path.name
    out_path.write_text(pretty, encoding="utf-8")
    return (True, total_repl)

def main():
    files = sorted(IN_DIR.glob("*.emailTemplate-meta.xml"))
    if not files:
        print(f"No EmailTemplate XML files found in {IN_DIR.resolve()}")
        return

    changed = 0
    total = 0
    for f in files:
        did_change, n = update_file(f)
        total += n
        if did_change:
            changed += 1
            print(f"Updated {f.name} ({n} replacements)")
        else:
            print(f"No changes {f.name}")

    print(f"Done. {changed}/{len(files)} files updated; {total} replacements total.")
    print(f"Output -> {OUT_DIR.resolve()}")

if __name__ == "__main__":
    main()
