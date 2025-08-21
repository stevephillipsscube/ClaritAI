# 10FlowFromEmailsHardcoded.py
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.dom.minidom import parseString
import sys, io
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---- Hardcoded paths ----
EMAIL_DIR = Path(r"force-app\main\default\email\unfiled$public")
FLOW_TEMPLATE = Path(r"force-app\main\default\flows\Milestone_Emails_Template.flow-meta.xml")
FLOWS_OUT_DIR = Path(r"force-app\main\default\flows")

# ---- SF Metadata namespace ----
NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)

END_NAME = "End_NoEmail"  # target for rules that shouldn't send an email

def _first_child_index(root: ET.Element, local_names: list[str]) -> int | None:
    """Return index of first child whose local tag name is in local_names."""
    for i, child in enumerate(list(root)):
        # child.tag is like '{ns}variables' – strip ns
        if '}' in child.tag:
            local = child.tag.split('}', 1)[1]
        else:
            local = child.tag
        if local in local_names:
            return i
    return None



def sanitize_end_targets(root: ET.Element, end_name: str) -> int:
    changed = 0
    for tr in root.findall(f".//{{{NS}}}targetReference"):
        if tr.text and tr.text.strip() == "End_NoEmail":
            tr.text = end_name
            changed += 1
    return changed


SUFFIXES = (".email-meta.xml", ".emailTemplate-meta.xml")

def strip_known_suffixes(filename: str) -> str:
    for s in SUFFIXES:
        if filename.endswith(s):
            return filename[:-len(s)]
    return filename

def parse_index_token(tok: str) -> int | None:
    """Map numeric token to assignment index. Supports 2.5→6 and 3.5→7."""
    t = tok.replace("_", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    if abs(v - 2.5) < 1e-9:
        return 6
    if abs(v - 3.5) < 1e-9:
        return 7
    if v.is_integer():
        return int(v)
    return None


def classify_semantic_idx(name_no_meta: str) -> int | None:
    """
    Infer assignment index from filename text when no clear numeric token exists.
    Maps:
      1 → Project Submitted
      2 → Sufficiency Approved (pre-review)
      3 → Sufficiency Disapproved/Failure/Corrections (pre-review)
      6 → Sufficiency Approved (in review)   [2.5]
      7 → Sufficiency Disapproved (in review) [3.5]
      4 → Review Approved
      5 → Review Disapproved/Failure/Corrections
    """
    base = name_no_meta.rsplit("/", 1)[-1]
    base = base.lower().replace("_", " ")

    def has(*words):
        return all(w.lower() in base for w in words)

    # 1) Project submitted
    if has("project", "submitted"):
        return 1

    # 2.5 / 3.5 — in-review sufficiency
    if "sufficiency" in base and any(phrase in base for phrase in ("in review", "review phase")):
        if any(w in base for w in ("approved", "pass")):
            return 6
        if any(w in base for w in ("disapproved", "failure", "corrections")):
            return 7

    # 2 / 3 — pre-review sufficiency
    if "sufficiency" in base:
        if any(w in base for w in ("approved", "pass")):
            return 2
        if any(w in base for w in ("disapproved", "failure", "corrections")):
            return 3

    # 4 / 5 — review outcomes
    if has("review", "approved"):
        return 4
    if "review" in base and any(w in base for w in ("disapproved", "failure", "corrections")):
        return 5

    return None


def pretty_xml(tree: ET.ElementTree) -> str:
    rough = ET.tostring(tree.getroot(), encoding="utf-8")
    return parseString(rough).toprettyxml(indent="  ")



def classify_semantic_idx(name: str) -> int | None:
    """
    Heuristic mapping by keywords when no clean index is available.
    Returns 1..7 or None.
    """
    s = name.lower()
    def has(*words): return all(w in s for w in words)
    # Submittal completed
    if has("submittal") and ("complete" in s or "completed" in s):
        return 1
    # Sufficiency approved / pass (first vs “in review/second pass”)
    if "sufficiency" in s and ("approve" in s or "pass" in s):
        if any(k in s for k in ["2.5", "2_5", "second", "2nd", "cycle 2", "in review"]):
            return 6
        return 2
    # Sufficiency disapproved / corrections / fail
    if "sufficiency" in s and ("disapprove" in s or "correction" in s or "fail" in s):
        if any(k in s for k in ["3.5", "3_5", "second", "2nd", "cycle 2", "in review"]):
            return 7
        return 3
    # Review approved/pass (not sufficiency)
    if "review" in s and ("approve" in s or "pass" in s) and "sufficiency" not in s:
        return 4
    # Review disapproved/corrections/fail (not sufficiency)
    if "review" in s and ("disapprove" in s or "correction" in s or "fail" in s) and "sufficiency" not in s:
        return 5
    return None

def parse_index_token(token: str) -> int | None:
    """
    Accepts integers or decimals like '2.5'/'3.5'. Maps 2.5->6, 3.5->7.
    """
    norm = token.replace("_", ".")
    try:
        f = float(norm)
    except ValueError:
        return None
    # Special mapping for 2.5 and 3.5
    if abs(f - 2.5) < 1e-6:
        return 6
    if abs(f - 3.5) < 1e-6:
        return 7
    return int(f)

def semantic_target_index(name_no_meta: str) -> tuple[int | None, str]:
    """
    Infer assignment index from the filename text (underscores treated as spaces).

    Rules:
      1 → Project Submitted
      2 → Sufficiency Pass (pre-review / before accepted)
      3 → Sufficiency Failure (pre-review / before accepted)
      6 → Sufficiency Pass (in review / review phase)  [2.5]
      7 → Sufficiency Failure (in review / review phase) [3.5]
      4 → Review Passed
      5 → Review Failed
    """
    base = name_no_meta.lower().replace("_", " ")

    def has(*words): return all(w in base for w in words)
    def anyof(*words): return any(w in base for w in words)

    # 1) project submitted
    if has("project", "submitted"):
        return 1, "project submitted"

    # 2.5 / 3.5 — in-review sufficiency (these OVERRIDE file numbers 4/5)
    if "sufficiency" in base and anyof("in review phase", "in review", "review phase"):
        if anyof("pass", "approved"):
            return 6, "sufficiency pass (in review)"
        if anyof("failure", "failed", "disapproved", "corrections"):
            return 7, "sufficiency failure (in review)"

    # 2 / 3 — pre-review sufficiency
    if "sufficiency" in base and anyof("before accepted", "before accepted for review", "pre review", "pre-review"):
        if anyof("pass", "approved"):
            return 2, "sufficiency pass (pre-review)"
        if anyof("failure", "failed", "disapproved", "corrections"):
            return 3, "sufficiency failure (pre-review)"

    # fallback: any sufficiency without explicit 'in review'/'before accepted'
    if "sufficiency" in base:
        if anyof("pass", "approved"):
            return 2, "sufficiency pass (generic → pre-review)"
        if anyof("failure", "failed", "disapproved", "corrections"):
            return 3, "sufficiency failure (generic → pre-review)"

    # 4 / 5 — review outcomes
    if anyof("reviewed", "review"):
        if anyof("passed", "approved"):
            return 4, "review passed"
        if anyof("failed", "disapproved", "corrections"):
            return 5, "review failed"

    return None, "unclassified"

def load_email_mapping():
    """
    Returns:
      permit: str (prefix before the numeric token)
      mapping: dict[int -> str] (assignment index -> email name WITHOUT suffix)
    Scans *.email-meta.xml and *.emailTemplate-meta.xml.
    """
    files = sorted([*EMAIL_DIR.glob("*.email-meta.xml"), *EMAIL_DIR.glob("*.emailTemplate-meta.xml")])
    if not files:
        raise SystemExit(f"No email meta files found in {EMAIL_DIR.resolve()}")

    # Permit prefix (multi-word), underscore, number (int or 2.5/3.5), underscore, rest
    num_pat = re.compile(r"^(?P<prefix>.+?)_(?P<num>\d+(?:[._]\d+)?)_.*$")

    mapping: dict[int, str] = {}
    permit_candidates = set()

    print("[INFO] Building mapping from email filenames:")
    for f in files:
        full = f.name  # with suffix
        base = strip_known_suffixes(full)  # no suffix
        m = num_pat.match(base)

        # Always try semantic mapping first (so 4/5 → 6/7 for in-review)
        sem_idx, reason = semantic_target_index(base)
        picked_idx = None

        if sem_idx is not None:
            picked_idx = sem_idx
            print(f"  [MAP] {full}  ->  template {picked_idx}  ({reason})")


        elif m:
            # Numeric fallback
            idx = parse_index_token(m.group("num"))
            if idx is not None:
                picked_idx = idx
                print(f"  [MAP] {full}  ->  template {picked_idx}  (numeric token)")
        else:
            print(f"  [MAP] {full}  ->  template {picked_idx}  (numeric token)")

        if picked_idx is not None:
            mapping[picked_idx] = base
            if m:
                permit_candidates.add(m.group("prefix"))

    if not mapping:
        raise SystemExit("No usable email filenames matched the expected pattern or keywords.")

    if len(permit_candidates) != 1:
        raise SystemExit(f"Ambiguous permit types: {sorted(permit_candidates)} — ensure they share the same prefix.")

    permit = next(iter(permit_candidates))
    return permit, mapping


def make_assignment_terminal(a: ET.Element):
    """Remove the <connector> from an <assignments> element so the path ends here."""
    conn = a.find(f"./{{{NS}}}connector")
    if conn is not None:
        a.remove(conn)
        print("[ENDMAP] Made assignment terminal (removed connector).")



# ---- helpers --------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.split('}', 1)[1] if '}' in tag else tag

def _first_child_index_of(root: ET.Element, names: list[str]) -> int | None:
    for i, child in enumerate(list(root)):
        if _local(child.tag) in names:
            return i
    return None

def move_all_end_nodes_to_bottom(root: ET.Element):
    """Relocate all <end> nodes to the bottom of <Flow> so the XML order is valid."""
    ends = list(root.findall(f"./{{{NS}}}end"))
    if not ends:
        return
    for e in ends:
        root.remove(e)
    for e in ends:
        root.append(e)
    print(f"[ORDER] Moved {len(ends)} <end> node(s) to the bottom of <Flow>.")

def find_or_create_end_at_bottom(root: ET.Element, preferred_name: str = "End") -> str:
    """Return the existing End name or create one (appended at the bottom)."""
    name_el = root.find(f"./{{{NS}}}end/{{{NS}}}name")
    if name_el is not None and (name_el.text or "").strip():
        move_all_end_nodes_to_bottom(root)
        return name_el.text.strip()

    # Create one at the very end (schema-safe)
    e = ET.SubElement(root, f"{{{NS}}}end")
    ET.SubElement(e, f"{{{NS}}}name").text = preferred_name
    ET.SubElement(e, f"{{{NS}}}label").text = "End"
    ET.SubElement(e, f"{{{NS}}}locationX").text = "0"
    ET.SubElement(e, f"{{{NS}}}locationY").text = "0"
    print(f"[END] Created missing <end> named {preferred_name} at bottom of <Flow>.")
    return preferred_name


def add_end_with_location(root: ET.Element, end_name: str, x: str = "0", y: str = "0") -> ET.Element:
    """Create (or reuse) an <end> with the given name, placed at a schema-safe location."""
    # Reuse existing by name
    for e in root.findall(f"./{{{NS}}}end"):
        n = e.find(f"./{{{NS}}}name")
        if n is not None and (n.text or "").strip() == end_name:
            # Update position if provided
            lx = e.find(f"./{{{NS}}}locationX")
            ly = e.find(f"./{{{NS}}}locationY")
            if lx is None: lx = ET.SubElement(e, f"{{{NS}}}locationX")
            if ly is None: ly = ET.SubElement(e, f"{{{NS}}}locationY")
            lx.text, ly.text = x, y
            return e

    end_el = ET.Element(f"{{{NS}}}end")
    ET.SubElement(end_el, f"{{{NS}}}name").text = end_name
    ET.SubElement(end_el, f"{{{NS}}}label").text = "End"
    ET.SubElement(end_el, f"{{{NS}}}locationX").text = x
    ET.SubElement(end_el, f"{{{NS}}}locationY").text = y

    # Insert in a safe place: after existing <end>s, else before <decisions>, else <subflows>, else <variables>, else append
    kids = list(root)
    last_end_idx = max((i for i, k in enumerate(kids) if _local(k.tag) == "end"), default=None)
    if last_end_idx is not None:
        root.insert(last_end_idx + 1, end_el)
    else:
        ins = _first_child_index_of(root, ["decisions", "subflows", "variables"])
        if ins is not None:
            root.insert(ins, end_el)
        else:
            root.append(end_el)
    return end_el

def find_any_end_name(root: ET.Element) -> str | None:
    e = root.find(f"./{{{NS}}}end/{{{NS}}}name")
    return (e.text or "").strip() if e is not None else None


def remove_assignment_and_point_to(root: ET.Element, idx: int, target_name: str) -> bool:
    """Delete Assign_Email_Template_{idx} and retarget connectors to target_name."""
    assign_name = f"Assign_Email_Template_{idx}"
    removed = False

    # delete the top-level <assignments> with that name
    for el in list(root):
        tag_local = el.tag.split('}', 1)[1] if '}' in el.tag else el.tag
        if tag_local != "assignments":
            continue
        name_el = el.find(f"./{{{NS}}}name")
        if name_el is None or (name_el.text or "").strip() != assign_name:
            continue
        root.remove(el)
        removed = True
        print(f"[ENDMAP] Removed {assign_name} (no mapped email).")

    # retarget any connectors pointing to that assignment
    updated = 0
    for tr in root.findall(f".//{{{NS}}}targetReference"):
        if (tr.text or "").strip() == assign_name:
            tr.text = target_name
            updated += 1
    if updated:
        print(f"[ENDMAP] Retargeted {updated} connector(s) from {assign_name} to {target_name}.")

    if not removed:
        print(f"[WARN] Assignment {assign_name} not found to remove.")
    return removed


def move_all_end_nodes_to_bottom(root: ET.Element):
    """Relocate all <end> nodes to the bottom of <Flow> so the XML order is valid."""
    ends = list(root.findall(f"./{{{NS}}}end"))
    if not ends:
        return
    for e in ends:
        root.remove(e)
    for e in ends:
        root.append(e)
    print(f"[ORDER] Moved {len(ends)} <end> node(s) to the bottom of <Flow>.")

def add_end_with_location(root: ET.Element, end_name: str, x: str = "0", y: str = "0") -> ET.Element:
    """Create (or reuse) an <end> with the given name; always append at the bottom."""
    # Reuse by name if it exists
    for e in root.findall(f"./{{{NS}}}end"):
        n = e.find(f"./{{{NS}}}name")
        if n is not None and (n.text or "").strip() == end_name:
            lx = e.find(f"./{{{NS}}}locationX"); ly = e.find(f"./{{{NS}}}locationY")
            if lx is None: lx = ET.SubElement(e, f"{{{NS}}}locationX")
            if ly is None: ly = ET.SubElement(e, f"{{{NS}}}locationY")
            lx.text, ly.text = x, y
            return e

    e = ET.Element(f"{{{NS}}}end")
    ET.SubElement(e, f"{{{NS}}}name").text = end_name
    ET.SubElement(e, f"{{{NS}}}label").text = "End"
    ET.SubElement(e, f"{{{NS}}}locationX").text = x
    ET.SubElement(e, f"{{{NS}}}locationY").text = y

    # ✅ Append at bottom (schema-safe)
    root.append(e)
    return e

def remove_all_end_nodes(root: ET.Element) -> int:
    """Strip any <end> nodes in the template/output (schema-safe)."""
    ends = list(root.findall(f"./{{{NS}}}end"))
    for e in ends:
        root.remove(e)
    if ends:
        print(f"[CLEAN] Removed {len(ends)} <end> node(s).")
    return len(ends)

def delete_assignment(root: ET.Element, idx: int) -> bool:
    """Delete the top-level <assignments> named Assign_Email_Template_{idx}."""
    target = f"Assign_Email_Template_{idx}"
    removed = False
    for el in list(root):
        if _local(el.tag) != "assignments":
            continue
        n = el.find(f"./{{{NS}}}name")
        if n is not None and (n.text or "").strip() == target:
            root.remove(el)
            removed = True
            print(f"[ENDMAP] Removed {target} (no mapped email).")
    if not removed:
        print(f"[WARN] {target} not found to remove.")
    return removed


def set_single_connector_target(parent_el: ET.Element, target_name: str):
    """Ensure exactly ONE <connector> with exactly ONE <targetReference>=target_name."""
    # Remove ALL existing <connector> children to avoid any dupes/leftovers
    for old in list(parent_el.findall(f"./{{{NS}}}connector")):
        parent_el.remove(old)

    # Create a clean connector with a single target
    conn = ET.SubElement(parent_el, f"{{{NS}}}connector")
    ET.SubElement(conn, f"{{{NS}}}targetReference").text = target_name


def ensure_assignment_points_to_send_email(assign_el: ET.Element):
    # Exactly one connector -> one targetReference -> Send_Email
    set_single_connector_target(assign_el, "Send_Email")



def remove_rule_connector(rule_el: ET.Element):
    """Remove the entire <connector> from a decision <rules> node (implicit End)."""
    conn = rule_el.find(f"./{{{NS}}}connector")
    if conn is not None:
        rule_el.remove(conn)



def update_flow(flow_template: Path, permit: str, mapping: dict[int, str]) -> ET.ElementTree:
    tree = ET.parse(flow_template)
    root = tree.getroot()

    # 🔒 Make sure there are NO <end> nodes at all (schema-safe & avoids parse errors)
    remove_all_end_nodes(root)

    # Labels
    readable_permit = permit.replace("_", " ")
    if (el := root.find(f"./{{{NS}}}label")) is not None:
        el.text = f"Milestone Emails - {readable_permit}"
        print(f"[SET] label -> Milestone Emails - {readable_permit}")
    if (el := root.find(f"./{{{NS}}}interviewLabel")) is not None:
        el.text = f"Milestone Emails - {readable_permit} {{!$Flow.CurrentDateTime}}"
        print(f"[SET] interviewLabel -> Milestone Emails - {readable_permit} {{!$Flow.CurrentDateTime}}")

    # Decision rule → assignment index (from your template)
    rule_to_idx = {
        "Submittal_Completed": 1,
        "Sufficiency_Check_Approved": 2,
        "Sufficiency_Check_Disapproved": 3,
        "Sufficiency_Check_2_Approved": 6,  # 2.5
        "Sufficiency_Check_Disapproved_2": 7,  # 3.5
        "Review_Approved": 4,
        "Review_Disapproved": 5,
    }

    for a in root.findall(f".//{{{NS}}}assignments"):
        name_el = a.find(f"./{{{NS}}}name")
        if not (name_el is not None and name_el.text):
            continue
        m = re.match(r"Assign_Email_Template_(\d+)$", name_el.text.strip())
        if not m:
            continue

        idx = int(m.group(1))
        new_name = mapping.get(idx)

        if new_name:
            sv = a.find(f"./{{{NS}}}assignmentItems/{{{NS}}}value/{{{NS}}}stringValue")
            if sv is not None:
                sv.text = new_name
                print(f"[SET] {name_el.text} -> {new_name}")
            # guarantee only one targetReference under connector
            ensure_assignment_points_to_send_email(a)
        else:
            delete_assignment(root, idx)

    # 2) Route decision rules
    for rule in root.findall(f".//{{{NS}}}decisions/{{{NS}}}rules"):
        rname_el = rule.find(f"./{{{NS}}}name")
        if rname_el is None or not (rname_el.text or "").strip():
            continue
        rname = rname_el.text.strip()
        idx = rule_to_idx.get(rname)
        if idx is None:
            print(f"[INFO] Skipping unmapped rule name: {rname}")
            continue

        if idx in mapping:
            set_single_connector_target(rule, f"Assign_Email_Template_{idx}")
            print(f"[ROUTE] Rule {rname} -> Assign_Email_Template_{idx}")
        else:
            remove_rule_connector(rule)  # implicit End
            print(f"[ROUTE] Rule {rname} -> End (implicit; connector removed)")

    return tree



def main():
    FLOWS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    permit, mapping = load_email_mapping()
    tree = update_flow(FLOW_TEMPLATE, permit, mapping)
    out_path = FLOWS_OUT_DIR / f"Milestone_Emails_{permit}.flow-meta.xml"
    out_path.write_text(pretty_xml(tree), encoding="utf-8")
    print(f"[OK] Wrote: {out_path}")
    print(f"[INFO] Final mapped indices: {sorted(mapping.keys())}")


if __name__ == "__main__":
    main()
