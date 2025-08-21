from pathlib import Path
import xml.etree.ElementTree as ET

GVS_FILE = Path(
    "force-app/main/default/globalValueSets/"
    "MUSW__Application_Types.globalValueSet-meta.xml"
)

txt = GVS_FILE.read_text(encoding="utf-8")

try:
    ET.fromstring(txt)          # full parse
    print("✅ XML parses – nothing to fix.")
    raise SystemExit

except ET.ParseError as e:
    # ---------- robust position extraction ----------
    if getattr(e, "position", None):
        line, col = e.position
        msg = e.msg
    elif len(e.args) > 1 and isinstance(e.args[1], tuple):
        msg, (line, col) = e.args
    else:
        # last resort: line number hidden in the message “… line 144, column 2”
        import re
        m = re.search(r"line\s+(\d+),\s*column\s+(\d+)", e.args[0])
        if not m:
            print("❌ Parser gave no position info – showing last 40 lines.")
            line, col = len(txt.splitlines()), 0
            msg = e.args[0]
        else:
            line, col = map(int, m.groups())
            msg = e.args[0]

    print(f"❌ ParseError: {msg} (line {line}, col {col})")

    LINES = txt.splitlines()
    start = max(line - 21, 0)
    end   = min(line + 20, len(LINES))

    print(f"\n🔎 Context (lines {start+1}-{end}):\n")
    for i in range(start, end):
        pointer = ">>" if i + 1 == line else "  "
        print(f"{pointer}{i+1:4d}: {LINES[i]}")
