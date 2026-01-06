import re
import html
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape as xml_escape
import argparse
import os
import sys
from dotenv import load_dotenv, find_dotenv

# -------------------------------------------------------------------
# Load .env and ENV
# -------------------------------------------------------------------
DOTENV_PATH = find_dotenv(usecwd=True)
if not load_dotenv(DOTENV_PATH, override=True):
    print("⚠️ .env not found (proceeding with OS env only)", file=sys.stderr)

ENV = os.getenv("ENV", "").strip().lower()
print(f"[DEBUG] 7EmailRegEx.py ENV = {ENV!r} (from {DOTENV_PATH or 'OS env only'})")

IN_DIR = Path("generated_code")
OUT_DIR = Path("generated_code_replaced")
NS_URI = "http://soap.sforce.com/2006/04/metadata"

REPLACEMENTS: list[tuple[re.Pattern, str]] = []

def build_replacements(merge_prefix: str) -> list[tuple[re.Pattern, str]]:
    """
    Build the regex -> merge-var replacement list using the given
    merge_prefix, e.g. 'MUSW__Milestone__c', 'MUSW__Permit2__c',
    or 'MUSW__Application2__c'.
    """
    env = os.getenv("ENV", "").strip().lower()
    print(f"[DEBUG] build_replacements: ENV={env!r}, merge_prefix={merge_prefix}")

    replacements: list[tuple[re.Pattern, str]] = [
        # Comments / completeness check box
        (
            re.compile(
                r"[\(\[\{]\s*insert\s+information\s+from\s+application\s+completeness\s+check\s+comment\s+box\s*[\)\]\}]",
                re.IGNORECASE
            ),
            f"{{{{{{{merge_prefix}.Comments_External__c}}}}}}"
        ),

        # 30 calendar days from date of email
        (
            re.compile(
                r"[\{\[\(]\s*(?:30|thirty)\s*(?:calendar\s*)?[-\s]*days?\s*from\s*the\s*date\s*of\s*(?:this\s*)?email\s*[\}\]\)]",
                re.IGNORECASE
            ),
            f"{{{{{{{merge_prefix}.X30_Days__c}}}}}}"
        ),
        (
            re.compile(
                r"\b(?:30|thirty)\s*(?:calendar\s*)?[-\s]*days?\s*from\s*the\s*date\s*of\s*(?:this\s*)?email\b",
                re.IGNORECASE
            ),
            f"{{{{{{{merge_prefix}.X30_Days__c}}}}}}"
        ),

        # 5 / 10 / 20 business days
        (
            re.compile(r"[\{\[\(]{0,3}\s*\b5\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.X5_Business_Days__c}}}}}}"
        ),
        (
            re.compile(r"[\{\[\(]{0,3}\s*\b10\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.X10_Business_Days__c}}}}}}"
        ),
        (
            re.compile(r"[\{\[\(]{0,3}\s*\b20\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.X20_Business_Days__c}}}}}}"
        ),

        # Comments keyword
        (
            re.compile(r"[\{\[\(]\s*comments?\s*[\}\]\)]", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Comments_External__c}}}}}}"
        ),

        # Record Type
        (
            re.compile(r"\[\s*record\s*type\s*\]", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Related_Entity__c}}}}}}"
        ),
        (
            re.compile(r"\brecord\s*type\b", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Related_Entity__c}}}}}}"
        ),

        # Application number – bracketed and bare
        # [Application #], [application #], [Application number]
        (
            re.compile(r"\[\s*application\s*(?:number|#)\s*\]", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Related_To_Entity__c}}}}}}"
        ),
        # plain "application #", "application number" (no brackets)
        (
            re.compile(r"\bapplication\s*(?:number|#)\b", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Related_To_Entity__c}}}}}}"
        ),
        # bare [Application]
        (
            re.compile(r"\[\s*application\s*\]", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Related_To_Entity__c}}}}}}"
        ),

        # Expiration date
        (
            re.compile(r"[\{\[\(]\s*expiration\s*date\s*[\}\]\)]", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Expiration_Date__c}}}}}}"
        ),
        (
            re.compile(r"\bexpiration\s*date\b", re.IGNORECASE),
            f"{{{{{{{merge_prefix}.Expiration_Date__c}}}}}}"
        ),
    ]

    # --- ADDRESS REPLACEMENT (ENV-AWARE) ---
    if env == "joco":
        address_merge = f"{{{{{{{merge_prefix}.Permit_or_Application_Address__c}}}}}}"
    else:
        address_merge = f"{{{{{{{merge_prefix}.Permit_or_Application_Address__c}}}}}}"

    print(f"[DEBUG] Using address field for {merge_prefix}: {address_merge}")

    # 1) Convert *existing* Address__c merge vars to env-specific address field
    addr_merge_var_pattern = re.compile(
        r"\{\{\{\s*" + re.escape(merge_prefix) + r"\.Address__c\s*\}\}\}",
        re.IGNORECASE,
    )
    replacements.append((addr_merge_var_pattern, address_merge))

    # 2) Also handle text placeholders like [address], (address), etc.
    replacements += [
        (re.compile(r"\[\s*address\s*\]", re.IGNORECASE), address_merge),
        (re.compile(r"[\{\[\(]\s*address\s*[\}\]\)]", re.IGNORECASE), address_merge),
        (re.compile(r"\[\s*property\s+address\s*\]", re.IGNORECASE), address_merge),
        (re.compile(r"[\{\[\(]\s*property\s+address\s*[\}\]\)]", re.IGNORECASE), address_merge),
    ]

    return replacements


# -------------------------------------------------------------------
# HTML normalization + update logic
# -------------------------------------------------------------------

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

    # --- Unwrap pre-line <div> but convert its line breaks into <br /> ---
    def _preline_repl(m):
        inner = m.group(1)
        inner = inner.replace("\r\n", "\n").replace("\r", "\n")
        inner = inner.replace("\n", "<br />\n")
        return inner

    s = re.sub(
        r'<div[^>]*white-space\s*:\s*pre-line[^>]*>(.*?)</div>',
        _preline_repl,
        s,
        flags=re.I | re.S
    )

    # Non-breaking space after merge var before "has"
    s = re.sub(r'(\}\}\})(\s+)has\b', r'\1&nbsp;has', s)

    # Non-breaking space before "Your reference number is"
    s = re.sub(r'(\.)(\s+)Your reference number is', r'\1 &nbsp;Your reference number is', s, flags=re.I)

    # Strip existing bold tags around merge vars so we control bolding
    s = re.sub(r'<strong>\s*(\{\{\{[^}]+\}\}\})\s*</strong>', r'\1', s, flags=re.I)
    s = re.sub(r'<b>\s*(\{\{\{[^}]+\}\}\})</b>', r'\1', s, flags=re.I)

    # Bold all merge vars if requested
    if bold_merge_vars:
        s = re.sub(r'(\{\{\{[^}]+\}\}\})', r'<strong>\1</strong>', s)

    # Or just bold the reference number merge var
    if bold_reference and not bold_merge_vars:
        def _bold_ref(m):
            prefix, inner = m.group(1), m.group(2)
            return f'{prefix}<strong>{{{{{{{inner}}}}}}}</strong>'
        s = re.sub(
            r'(Your reference number is\s+)(?:<strong>\s*)?\{\{\{([^}]+)\}\}\}(?:\s*</strong>)?',
            _bold_ref,
            s,
            flags=re.I
        )
        s = re.sub(r'(</strong>)\s*\.', r'\1', s)

    # Ensure exactly one blank row before </body>
    trailing_blanks = re.compile(
        r'(?is)(?:\s*(?:<br\s*/?>|&nbsp;|&#160;|<p>\s*(?:&nbsp;|\s)*</p>))+\s*(?=</body>)'
    )
    if trailing_blanks.search(s):
        s = trailing_blanks.sub('\n<br />\n', s)
    else:
        s = re.sub(r'(?i)</body>', '\n<br />\n</body>', s, count=1)

    return s


def update_file(
    path: Path,
    *,
    bold_reference: bool = False,
    bold_merge_vars: bool = False,
    default_related_entity_type: str = "MUSW__Milestone__c",
    default_merge_prefix: str = "MUSW__Milestone__c"
) -> tuple[bool, int]:
    tree = ET.parse(path)
    root = tree.getroot()

    # DO NOT TRIM <name> ANYMORE
    name_trimmed = False

    # Check for existing relatedEntityType in the XML and use it if valid
    rel_el = root.find(f".//{{{NS_URI}}}relatedEntityType")
    existing_type = (rel_el.text or "").strip() if rel_el is not None else ""

    if existing_type == "MUSW__Inspection__c":
        current_related_entity = "MUSW__Inspection__c"
        current_merge_prefix = "MUSW__Inspection__c"
        print(f"[INFO] Found Inspection entity in XML: {path.name}")
    elif existing_type == "MUSW__Milestone__c":
        current_related_entity = "MUSW__Milestone__c"
        current_merge_prefix = "MUSW__Milestone__c"
    elif existing_type == "MUSW__Permit2__c":
        current_related_entity = "MUSW__Permit2__c"
        current_merge_prefix = "MUSW__Permit2__c"
    elif existing_type == "MUSW__Application2__c":
        current_related_entity = "MUSW__Application2__c"
        current_merge_prefix = "MUSW__Application2__c"
    else:
        # Fallback to defaults passed in
        current_related_entity = default_related_entity_type
        current_merge_prefix = default_merge_prefix

    # Build replacements for this specific file
    local_replacements = build_replacements(current_merge_prefix)

    # --- Apply replacements to <subject> text as well ---
    subject_changed = False
    subj_el = root.find(f".//{{{NS_URI}}}subject")
    if subj_el is not None and subj_el.text:
        subj_src = subj_el.text
        subj_new = subj_src
        for patt, repl in local_replacements:
            subj_new, _ = patt.subn(repl, subj_new)
        if subj_new != subj_src:
            subj_el.text = subj_new
            subject_changed = True

    # --- Ensure relatedEntityType matches the selected entity ---
    rel_changed = False
    if current_related_entity:
        rel_el = root.find(f".//{{{NS_URI}}}relatedEntityType")
        if rel_el is None:
            rel_el = ET.SubElement(root, f"{{{NS_URI}}}relatedEntityType")
            rel_el.text = current_related_entity
            rel_changed = True
        elif (rel_el.text or "").strip() != current_related_entity:
            rel_el.text = current_related_entity
            rel_changed = True

    # --- Process htmlValue ---
    hv = root.find(f".//{{{NS_URI}}}htmlValue")
    if hv is None:
        # Still write out if we changed subject or relatedEntityType
        if subject_changed or rel_changed:
            xml_bytes = ET.tostring(root, encoding="utf-8")
            pretty = parseString(xml_bytes).toprettyxml(indent="  ")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            (OUT_DIR / path.name).write_text(pretty, encoding="utf-8")
            return (True, 0)
        return (False, 0)

    html_src = html.unescape(hv.text or "")
    total_repl = 0
    new_html = html_src

    # Apply all merge-field replacements in HTML
    for patt, repl in local_replacements:
        new_html, n = patt.subn(repl, new_html)
        total_repl += n

    # Normalize HTML (wrapper, spacing, bolding, etc.)
    new_html = normalize_html_to_site(
        new_html,
        bold_reference=bold_reference,
        bold_merge_vars=bold_merge_vars
    )

    html_changed = (new_html != html_src)
    if html_changed:
        hv.text = xml_escape(new_html)

    # Serialize & pretty-print (includes any <subject> / relatedEntityType changes)
    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty = parseString(xml_bytes).toprettyxml(indent="  ")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / path.name).write_text(pretty, encoding="utf-8")

    did_change = html_changed or name_trimmed or subject_changed or rel_changed
    return (did_change, total_repl)

# -------------------------------------------------------------------
# main()
# -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Replace tokens in EmailTemplate XML and normalize HTML."
    )
    parser.add_argument(
        "--bold-reference",
        action="store_true",
        help="Bold the merge-var in the 'Your reference number is ...' sentence (default: off)"
    )
    parser.add_argument(
        "--bold-merge-vars",
        action="store_true",
        help="Bold ALL {{{...}}} merge variables (default: off)"
    )
    parser.add_argument(
        "--entity",
        choices=["Milestone", "Permit", "Application"],
        default="Milestone",
        help="Base related entity type for merge fields (controls MUSW__<Object>__c prefix and relatedEntityType)."
    )
    args = parser.parse_args()

    print("7EmailRegEx.py starting...")
    print(f"Working directory: {Path.cwd()}")
    print(f"IN_DIR resolved to: {IN_DIR.resolve()}")
    print(f"Selected entity type: {args.entity}")

    # Map CLI entity choice to actual merge prefix AND relatedEntityType
    entity_choice = args.entity.lower()
    if entity_choice == "milestone":
        default_merge_prefix = "MUSW__Milestone__c"
        default_related_entity_type = "MUSW__Milestone__c"
    elif entity_choice == "permit":
        default_merge_prefix = "MUSW__Permit2__c"
        default_related_entity_type = "MUSW__Permit2__c"
    else:  # application
        default_merge_prefix = "MUSW__Application2__c"
        default_related_entity_type = "MUSW__Application2__c"

    # 1) Just list files — NO renaming, NO trimming
    files = sorted(IN_DIR.glob("*.emailTemplate-meta.xml"))
    if not files:
        print(f"No EmailTemplate XML files found in {IN_DIR.resolve()}")
        return

    changed = 0
    total = 0

    # 2) Process each XML file
    for f in files:
        did_change, n = update_file(
            f,
            bold_reference=args.bold_reference,
            bold_merge_vars=args.bold_merge_vars,
            default_related_entity_type=default_related_entity_type,
            default_merge_prefix=default_merge_prefix
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
