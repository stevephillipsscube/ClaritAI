import re
import html
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape as xml_escape
import argparse

# -------------------------------------------------------------------
# Paths / constants
# -------------------------------------------------------------------
IN_DIR = Path("generated_code")              # Where input XML files live
OUT_DIR = Path("generated_code_replaced")   # Where updated XML files are written
NS_URI = "http://soap.sforce.com/2006/04/metadata"

MAX_DEV_NAME_LEN = 80  # Salesforce DeveloperName limit

# -------------------------------------------------------------------
# DeveloperName / filename helpers
# -------------------------------------------------------------------

def get_developer_name_from_filename(path: Path) -> str:
    """
    For EmailTemplate metadata files, the DeveloperName portion is the
    part before '.emailTemplate-meta.xml'.

    Example:
      Mobile_Home_Permit_Notifications_10_Closeout_email_30_day_notice.emailTemplate-meta.xml
      -> Mobile_Home_Permit_Notifications_10_Closeout_email_30_day_notice
    """
    suffix = ".emailTemplate-meta.xml"
    name = path.name
    if name.endswith(suffix):
        return name[:-len(suffix)]
    return path.stem


def trim_overlong_filenames(max_len: int = MAX_DEV_NAME_LEN) -> list[Path]:
    """
    Scan IN_DIR for *.emailTemplate-meta.xml.

    If the DeveloperName (basename before .emailTemplate-meta.xml) is > max_len,
    trim characters off the END so it is exactly max_len characters, then rename
    the file accordingly.

    Returns the up-to-date list of Path objects after any renames.
    """
    files = sorted(IN_DIR.glob("*.emailTemplate-meta.xml"))
    if not files:
        print(f"No EmailTemplate XML files found in {IN_DIR.resolve()}")
        return []

    for f in files:
        dev = get_developer_name_from_filename(f)
        if len(dev) > max_len:
            # Trim from the end to meet length requirement
            new_dev = dev[:max_len]
            new_name = f"{new_dev}.emailTemplate-meta.xml"
            new_path = f.with_name(new_name)

            # If somehow the target already exists and it's not the same file,
            # log a warning and skip to avoid overwriting.
            if new_path.exists() and new_path != f:
                print(f"WARNING: Target file already exists, skipping rename: {new_path.name}")
                continue

            print(f"Trimming filename: {f.name} -> {new_name}")
            f.rename(new_path)

    # Re-scan the folder after any renames and return the current set of files
    return sorted(IN_DIR.glob("*.emailTemplate-meta.xml"))

# -------------------------------------------------------------------
# Hard-coded replacements (merge fields, dates, etc.)
# -------------------------------------------------------------------

REPLACEMENTS = [
    (
        re.compile(
            r"[\(\[\{]\s*insert\s+information\s+from\s+application\s+completeness\s+check\s+comment\s+box\s*[\)\]\}]",
            re.IGNORECASE
        ),
        "{{{MUSW__Milestone__c.Comments_External__c}}}"
    ),

    (
        re.compile(
            r"[\{\[\(]\s*(?:30|thirty)\s*(?:calendar\s*)?[-\s]*days?\s*from\s*the\s*date\s*of\s*(?:this\s*)?email\s*[\}\]\)]",
            re.IGNORECASE
        ),
        "{{{MUSW__Milestone__c.X30_Days__c}}}"
    ),

    (
        re.compile(
            r"\b(?:30|thirty)\s*(?:calendar\s*)?[-\s]*days?\s*from\s*the\s*date\s*of\s*(?:this\s*)?email\b",
            re.IGNORECASE
        ),
        "{{{MUSW__Milestone__c.X30_Days__c}}}"
    ),

    (
        re.compile(r"[\{\[\(]{0,3}\s*\b5\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
        "{{{MUSW__Milestone__c.X5_Business_Days__c}}}"
    ),
    (
        re.compile(r"[\{\[\(]{0,3}\s*\b10\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
        "{{{MUSW__Milestone__c.X10_Business_Days__c}}}"
    ),
    (
        re.compile(r"[\{\[\(]{0,3}\s*\b20\s*business\s*[-\s]*days?\b\s*[\}\]\)]{0,3}", re.IGNORECASE),
        "{{{MUSW__Milestone__c.X20_Business_Days__c}}}"
    ),

    (
        re.compile(r"[\{\[\(]\s*comments?\s*[\}\]\)]", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Comments_External__c}}}"
    ),

    (
        re.compile(r"\[\s*record\s*type\s*\]", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Related_Entity__c}}}"
    ),
    (
        re.compile(r"\brecord\s*type\b", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Related_Entity__c}}}"
    ),

    (
        re.compile(r"\[\s*application\s*(?:number|#)\s*\]", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Related_To_Entity__c}}}"
    ),
    (
        re.compile(r"\bapplication\s*(?:number|#)\b", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Related_To_Entity__c}}}"
    ),

    (
        re.compile(r"[\{\[\(]\s*expiration\s*date\s*[\}\]\)]", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Expiration_Date__c}}}"
    ),
    (
        re.compile(r"\bexpiration\s*date\b", re.IGNORECASE),
        "{{{MUSW__Milestone__c.Expiration_Date__c}}}"
    ),
]

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
    s = re.sub(r'<b>\s*(\{\{\{[^}]+\}\}\})\s*</b>', r'\1', s, flags=re.I)

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


def update_file(path: Path, *, bold_reference: bool = False, bold_merge_vars: bool = False) -> tuple[bool, int]:
    tree = ET.parse(path)
    root = tree.getroot()

    # --- NEW: enforce <name> length <= 80 as well ---
    name_el = root.find(f".//{{{NS_URI}}}name")
    if name_el is not None and name_el.text:
        original_name = name_el.text
        if len(original_name) > MAX_DEV_NAME_LEN:
            trimmed = original_name[:MAX_DEV_NAME_LEN]
            print(f"Trimming <name> for {path.name}: '{original_name}' -> '{trimmed}'")
            name_el.text = trimmed

    hv = root.find(f".//{{{NS_URI}}}htmlValue")
    if hv is None:
        return (False, 0)

    html_src = html.unescape(hv.text or "")
    total_repl = 0
    new_html = html_src

    # Apply all merge-field replacements
    for patt, repl in REPLACEMENTS:
        new_html, n = patt.subn(repl, new_html)
        total_repl += n

    # Normalize HTML (wrapper, spacing, bolding, etc.)
    new_html = normalize_html_to_site(
        new_html,
        bold_reference=bold_reference,
        bold_merge_vars=bold_merge_vars
    )

    # If HTML didn't change and <name> didn't change, we still may have updated <name>.
    # Easiest: just compare HTML; the <name> change is already in the tree.
    if new_html != html_src:
        hv.text = xml_escape(new_html)

    # Serialize & pretty-print
    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty = parseString(xml_bytes).toprettyxml(indent="  ")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / path.name).write_text(pretty, encoding="utf-8")

    # If HTML didn't change, total_repl will be 0, but we might still have trimmed <name>.
    # For your counters, we'll treat <name> trimming as a "change" too if it happened.
    did_change = (new_html != html_src) or (name_el is not None and len(original_name) > MAX_DEV_NAME_LEN)
    return (did_change, total_repl)


# -------------------------------------------------------------------
# main()
# -------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Replace tokens in EmailTemplate XML and normalize HTML.")
    parser.add_argument("--bold-reference", action="store_true",
                        help="Bold the merge-var in the 'Your reference number is ...' sentence (default: off)")
    parser.add_argument("--bold-merge-vars", action="store_true",
                        help="Bold ALL {{{...}}} merge variables (default: off)")
    args = parser.parse_args()

    print("7EmailRegEx.py starting...")
    print(f"Working directory: {Path.cwd()}")
    print(f"IN_DIR resolved to: {IN_DIR.resolve()}")

    # 1) Trim filenames that would cause DeveloperName > 80 chars
    files = trim_overlong_filenames(max_len=MAX_DEV_NAME_LEN)
    if not files:
        # Nothing to process
        return

    changed = 0
    total = 0

    # 2) Process each (possibly renamed) XML file
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
