import re
import html
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape as xml_escape
import argparse

IN_DIR = Path("generated_code")
OUT_DIR = Path("generated_code_replaced")
NS_URI = "http://soap.sforce.com/2006/04/metadata"

# ---------------- Hard-coded replacements ----------------
# Matches are case-insensitive and bracket-aware.
REPLACEMENTS = [
    (re.compile(
        r"[\(\[\{]\s*insert\s+information\s+from\s+application\s+completeness\s+check\s+comment\s+box\s*[\)\]\}]",
        re.IGNORECASE
    ), "{{{MUSW__Milestone__c.Comments_External__c}}}"),

    (re.compile(
        r"[\{\[\(]\s*(?:30|thirty)\s*(?:calendar\s*)?[-\s]*days?\s*from\s*the\s*date\s*of\s*(?:this\s*)?email\s*[\}\]\)]",
        re.IGNORECASE
    ), "{{{MUSW__Milestone__c.X30_Days__c}}}"),

    (re.compile(
        r"\b(?:30|thirty)\s*(?:calendar\s*)?[-\s]*days?\s*from\s*the\s*date\s*of\s*(?:this\s*)?email\b",
        re.IGNORECASE
    ), "{{{MUSW__Milestone__c.X30_Days__c}}}"),

    (re.compile(r"[\{\[\(]{0,3}\s*\b5\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X5_Business_Days__c}}}"),
    (re.compile(r"[\{\[\(]{0,3}\s*\b10\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X10_Business_Days__c}}}"),
    (re.compile(r"[\{\[\(]{0,3}\s*\b20\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
     "{{{MUSW__Milestone__c.X20_Business_Days__c}}}"),

    (re.compile(r"[\{\[\(]\s*comments?\s*[\}\]\)]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Comments_External__c}}}"),

    (re.compile(r"\[\s*record\s*type\s*\]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_Entity__c}}}"),
    (re.compile(r"\brecord\s*type\b", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_Entity__c}}}"),

    (re.compile(r"\[\s*application\s*(?:number|#)\s*\]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_To_Entity__c}}}"),
    (re.compile(r"\bapplication\s*(?:number|#)\b", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Related_To_Entity__c}}}"),

    (re.compile(r"[\{\[\(]\s*expiration\s*date\s*[\}\]\)]", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Expiration_Date__c}}}"),
    (re.compile(r"\bexpiration\s*date\b", re.IGNORECASE),
     "{{{MUSW__Milestone__c.Expiration_Date__c}}}"),
]


def normalize_html_to_site(html_body: str, *, bold_reference: bool = False, bold_merge_vars: bool = False) -> str:
    s = (html_body or "").strip()

    # Ensure skeleton & styles
    if "<html" in s:
        s = re.sub(r"<html\b[^>]*>", '<html style="overflow-y: hidden;">', s, flags=re.I)
    else:
        s = (
            '<html style="overflow-y: hidden;">\n<head>\n\t<title></title>\n</head>\n'
            f'<body style="height: auto; min-height: auto;">{s}</body>\n</html>'
        )
    s = re.sub(r"<body\b[^>]*>", '<body style="height: auto; min-height: auto;">', s, flags=re.I)

    # --- Unwrap pre-line div BUT keep its line breaks as <br /> ---
    def _preline_repl(m):
        inner = m.group(1)
        inner = inner.replace("\r\n", "\n").replace("\r", "\n")
        inner = inner.replace("\n", "<br />\n")
        return inner
    s = re.sub(r'<div[^>]*white-space\s*:\s*pre-line[^>]*>(.*?)</div>', _preline_repl, s, flags=re.I | re.S)

    # Non-breaking space after merge var before "has"
    s = re.sub(r'(\}\}\})(\s+)has\b', r'\1&nbsp;has', s)

    # Non-breaking space before "Your reference number is"
    s = re.sub(r'(\.)(\s+)Your reference number is', r'\1 &nbsp;Your reference number is', s, flags=re.I)

    # --- Normalize away any existing bold around merge-vars ---
    s = re.sub(r'<strong>\s*(\{\{\{[^}]+\}\}\})\s*</strong>', r'\1', s, flags=re.I)
    s = re.sub(r'<b>\s*(\{\{\{[^}]+\}\}\})\s*</b>', r'\1', s, flags=re.I)

    # Optional bolding
    if bold_merge_vars:
        s = re.sub(r'(\{\{\{[^}]+\}\}\})', r'<strong>\1</strong>', s)
    if bold_reference and not bold_merge_vars:
        def _bold_ref(m):
            prefix, inner = m.group(1), m.group(2)
            return f'{prefix}<strong>{{{{{{{inner}}}}}}}</strong>'
        s = re.sub(r'(Your reference number is\s+)(?:<strong>\s*)?\{\{\{([^}]+)\}\}\}(?:\s*</strong>)?',
                   _bold_ref, s, flags=re.I)
        s = re.sub(r'(</strong>)\s*\.', r'\1', s)

    # --- Ensure EXACTLY ONE blank row before </body> (keep existing paragraphs) ---
    trailing_blanks = re.compile(
        r'(?is)(?:\s*(?:<br\s*/?>|&nbsp;|&#160;|<p>\s*(?:&nbsp;|\s)*</p>))+\s*(?=</body>)'
    )
    if trailing_blanks.search(s):
        s = trailing_blanks.sub('\n<br />\n', s)
    else:
        s = re.sub(r'(?i)</body>', '\n<br />\n</body>', s, count=1)

    return s


def update_file(path: Path, *, bold_reference: bool = False, bold_merge_vars: bool = False) -> tuple[bool, int]:
    tree = ET.parse(path)
    root = tree.getroot()
    hv = root.find(f".//{{{NS_URI}}}htmlValue")
    if hv is None:
        return (False, 0)

    html_src = html.unescape(hv.text or "")
    total_repl = 0
    new_html = html_src

    for patt, repl in REPLACEMENTS:
        new_html, n = patt.subn(repl, new_html)
        total_repl += n

    new_html = normalize_html_to_site(new_html, bold_reference=bold_reference, bold_merge_vars=bold_merge_vars)

    if new_html == html_src:
        return (False, 0)

    hv.text = xml_escape(new_html)
    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty = parseString(xml_bytes).toprettyxml(indent="  ")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / path.name).write_text(pretty, encoding="utf-8")
    return (True, total_repl)


def main():
    parser = argparse.ArgumentParser(description="Replace tokens in EmailTemplate XML and normalize HTML.")
    parser.add_argument("--bold-reference", action="store_true",
                        help="Bold the merge-var in the 'Your reference number is ...' sentence (default: off)")
    parser.add_argument("--bold-merge-vars", action="store_true",
                        help="Bold ALL {{{...}}} merge variables (default: off)")
    args = parser.parse_args()

    files = sorted(IN_DIR.glob("*.emailTemplate-meta.xml"))
    if not files:
        print(f"No EmailTemplate XML files found in {IN_DIR.resolve()}")
        return

    changed = 0
    total = 0
    for f in files:
        did_change, n = update_file(
            f,
            bold_reference=args.bold_reference,
            bold_merge_vars=args.bold_merge_vars
        )
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