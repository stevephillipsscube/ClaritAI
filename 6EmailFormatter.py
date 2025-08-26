# 6EmailFormatter.py
import sys
import os
import re
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString
from xml.sax.saxutils import escape as xml_escape


OUT_DIR = Path("generated_code")

def ensure_outdir():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

def clean_outdir():
    # remove only files; keep the folder
    for p in OUT_DIR.glob("*"):
        if p.is_file():
            p.unlink()


def normalize_title(text: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    # Normalize dashes and collapse whitespace
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text).strip()
    return text

Ticket =12345

def to_safe_developer_name(name: str) -> str:
    """Salesforce DeveloperName-safe: letters/digits/underscore only,
    start with a letter, no double or trailing underscores."""
    safe = re.sub(r"[^A-Za-z0-9]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    if not safe or not safe[0].isalpha():
        safe = f"SCTN_{Ticket}_{safe}" if safe else "T_Template"
    return safe

def pretty_xml(elem: Element) -> str:
    rough = tostring(elem, encoding="utf-8")
    return parseString(rough).toprettyxml(indent="  ")


def build_email_template_xml(display_name: str,
                             subject_text: str,
                             html_body: str,
                             developer_name=None,
                             related_entity: str = "MUSW__Application2__c",
                             api_version: str = "59.0",
                             encoding: str = "UTF-8",
                             ui_type: str = "SFX",
                             template_type: str = "custom",
                             is_active: bool = True,
                             description: str = "",
                             folder: str = "") -> str:
    root = Element("EmailTemplate", xmlns="http://soap.sforce.com/2006/04/metadata")
    SubElement(root, "apiVersion").text = api_version
    SubElement(root, "description").text = description
    SubElement(root, "encoding").text = encoding
    SubElement(root, "folder").text = folder
    SubElement(root, "name").text = display_name
    SubElement(root, "subject").text = subject_text
    SubElement(root, "uiType").text = ui_type
    SubElement(root, "templateType").text = template_type
    SubElement(root, "isActive").text = "true" if is_active else "false"
    SubElement(root, "relatedEntityType").text = related_entity
    _ = developer_name

    html_doc = f"""<html>
<head>
  <title></title>
</head>
<body style="height:auto; min-height:auto;">
  <div style="white-space: pre-line;">{html_body}</div>
</body>
</html>"""
    SubElement(root, "htmlValue").text = xml_escape(html_doc)
    return pretty_xml(root)


def parse_pasted_table(text: str):
    """
    TSV parser for 3 columns:
      Col A = Title
      Col B = Body (can span multiple continuation lines)
      Col C = Subject

    Rules:
      - A non-empty Title (col A) starts a new row.
      - Lines with an empty Title (col A) are continuations of the current row's Body (col B).
      - Subject (col C) is taken from the same row as the Title; if later continuation lines
        provide a non-empty Subject cell, it will overwrite (last non-empty wins).

    Returns list of (title, body, subject).
    """
    import re

    def norm(s: str) -> str:
        s = s.replace("\u00A0", " ")  # NBSP -> space
        s = s.replace("–", "-").replace("—", "-")
        s = re.sub(r"\s+$", "", s)    # trim right
        return s

    rows = []
    current_title = None
    current_body_lines = []
    current_subject = ""

    for raw_line in text.splitlines():
        line = norm(raw_line)

        if "\t" in line:
            # Split into at most 3 cells (A, B, C). Extra tabs remain in C.
            a, b, c = (line.split("\t", 2) + ["", ""])[:3]
            a = a.strip()
            # New row only if Title (A) has content
            if a:
                # commit previous
                if current_title is not None:
                    rows.append((
                        current_title,
                        "\n".join(current_body_lines).strip(),
                        current_subject.strip()
                    ))
                current_title = a
                current_body_lines = [b.lstrip()] if b else []
                current_subject = c.strip()
            else:
                # Continuation line: append to Body; allow Subject override if provided
                if current_title is not None:
                    if b:
                        current_body_lines.append(b.lstrip())
                    if c.strip():
                        current_subject = c.strip()
                else:
                    # ignore stray continuation before first title
                    continue
        else:
            # No tab: treat as body continuation
            if current_title is not None:
                current_body_lines.append(line)
            else:
                continue

    # final commit
    if current_title is not None:
        rows.append((
            current_title,
            "\n".join(current_body_lines).strip(),
            current_subject.strip()
        ))

    # Normalize accidental huge paragraph gaps
    cleaned = []
    for title, body, subject in rows:
        body = re.sub(r"\n{3,}", "\n\n", body)
        cleaned.append((title, body, subject))

    return cleaned


def main():
    if len(sys.argv) < 3:
        print("Usage: python 6EmailFormatter.py <Base Name> <Pasted Table Text>")
        sys.exit(1)

    base_name = sys.argv[1].strip()
    table_text = "\n".join(sys.argv[2:])

    ensure_outdir()

    parsed = parse_pasted_table(table_text)
    if not parsed:
        print("No rows parsed from the pasted table. Ensure it's tab-separated (Excel/Sheets copy).")
        sys.exit(1)

    # ✅ only clear AFTER parse succeeded
    clean_outdir()

    for i, (left_col, body, subject) in enumerate(parsed, start=1):
        # New XML <name>: "<Base> <i> <Title>"
        base_clean  = normalize_title(base_name)
        title_clean = normalize_title(left_col)
        display_name = f"{base_clean} {i} {title_clean}"
        display_name = re.sub(r"\s{2,}", " ", display_name).strip()

        # Filename stays "<Base>_<i>_<Title>"
        safe_base  = to_safe_developer_name(base_clean)
        safe_title = to_safe_developer_name(title_clean)
        file_basename = f"{safe_base}_{i}_{safe_title}"

        # Column C (Subject); fallback if blank
        subject_clean = subject.strip() if subject else "change me"

        xml_str = build_email_template_xml(
            display_name=display_name,
            subject_text=subject_clean,   # ← uses Column C
            html_body=body,
            related_entity="MUSW__Application2__c",
            api_version="59.0",
            encoding="UTF-8",
            ui_type="SFX",
            developer_name=to_safe_developer_name,  # accepted but ignored inside builder
            template_type="custom",
            is_active=True,
            description="",
            folder=""
        )

        out_path = OUT_DIR / f"{file_basename}.emailTemplate-meta.xml"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
        print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()
