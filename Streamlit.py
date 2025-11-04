# ==== TOP OF FILE ============================================================
import streamlit as st
import os, sys
from pathlib import Path
from dotenv import dotenv_values
import subprocess
import shutil, tempfile
import re
from textwrap import dedent

# Must be the first Streamlit call (and only once)
st.set_page_config(page_title="Clariti Environment", layout="wide")

# ----- ENV (parent folder of this script only) -------------------------------
try:
    SCRIPT_DIR = Path(__file__).resolve().parent
except NameError:
    # Fallback if __file__ is not set (rare)
    SCRIPT_DIR = Path.cwd()

ENV_ROOT = SCRIPT_DIR.parent  # <<< one directory up
ENV_PATTERNS = [".env", ".env.*", "*.env", "*.env.*"]  # in ENV_ROOT only

def _find_env_files():
    seen = []
    for pat in ENV_PATTERNS:
        for p in ENV_ROOT.glob(pat):
            if p.is_file():
                r = p.resolve()
                if r not in seen:
                    seen.append(r)
    # Prefer plain `.env`, then alphabetical for stability
    def sort_key(p: Path):
        return (0 if p.name == ".env" else 1, p.name.lower())
    return sorted(seen, key=sort_key)

def _load_env_snapshot(kv: dict):
    # clear previously loaded keys
    prev = st.session_state.get("__ENV_KEYS__", set())
    for k in prev:
        os.environ.pop(k, None)
    # set new ones
    for k, v in kv.items():
        if v is not None:
            os.environ[k] = str(v)
    st.session_state["__ENV_KEYS__"] = set(kv.keys())
    st.session_state["__ENV_SNAPSHOT__"] = kv

def _apply_saved_or_default():
    if "__ENV_SNAPSHOT__" in st.session_state:
        _load_env_snapshot(st.session_state["__ENV_SNAPSHOT__"])
    else:
        # auto-load ENV_ROOT/.env if present; otherwise wait for user selection
        default = ENV_ROOT / ".env"
        if default.exists():
            _load_env_snapshot(dotenv_values(default))
            st.session_state["__ENV_PATH__"] = str(default)

_apply_saved_or_default()

ACTIVE_DOTENV = (ENV_ROOT / ".env")

def _activate_selected_dotenv(selected_path: Path) -> bool:
    """Make the chosen env the active .env at ENV_ROOT/.env, atomically."""
    try:
        txt = Path(selected_path).read_text(encoding="utf-8")  # keep encoding
        with tempfile.NamedTemporaryFile("w", delete=False, dir=ENV_ROOT, encoding="utf-8") as tmp:
            tmp.write(txt)
            tmp_path = Path(tmp.name)
        tmp_path.replace(ACTIVE_DOTENV)
        return True
    except Exception as e:
        st.error(f"Failed to activate .env: {e}")
        return False

def _sub_env():
    """Build env for child processes; keep/extend what you already have."""
    env = os.environ.copy()
    # ensure alias fallback consistency
    if not env.get("SF_ORG_ALIAS") and env.get("SF_TARGET_ORG"):
        env["SF_ORG_ALIAS"] = env["SF_TARGET_ORG"]
    # keep stdout/stderr UTF-8 in child Python processes
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env

def _run(cmd: list[str], **kwargs):
    """
    Safe subprocess runner:
      - text=True, encoding='utf-8'
      - capture_output=True (you can override)
      - env=_sub_env()
      - cwd=SCRIPT_DIR (so find_dotenv(usecwd=True) resolves to ENV_ROOT/.env)
    Pass any subprocess.run kwargs to override defaults (timeout, check, etc).
    """
    kwargs.setdefault("text", True)
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("env", _sub_env())
    kwargs.setdefault("cwd", SCRIPT_DIR)
    return subprocess.run(cmd, **kwargs)

def _run_py(script: str, *args, **kwargs):
    """
    Run a Python script (next to this Streamlit file by default) using the
    current interpreter, with the same safe defaults as _run().
    """
    # Resolve relative paths against SCRIPT_DIR so child scripts are found
    script_path = str((SCRIPT_DIR / script).resolve()) if not os.path.isabs(script) else script
    # Ensure all positional args are strings
    argv = [str(a) for a in args]
    return _run([sys.executable, script_path, *argv], **kwargs)


# ───────────────── SF CLI output helpers (JSON-first) ─────────────────
import json, re

_ANSI_RE   = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
_CTRL_RE   = re.compile(r'[\r\b]')  # spinner backspaces
_HEADING   = "─────────────── Deploying Metadata ───────────────"
_STATUS_RE = re.compile(r'^Status:\s*(.+)$', re.MULTILINE)
_ID_RE     = re.compile(r'^Deploy ID:\s*(\S+)$', re.MULTILINE)
_ORG_RE    = re.compile(r'^Target Org:\s*(.+)$', re.MULTILINE)
_TIME_RE   = re.compile(r'^Elapsed Time:\s*(.+)$', re.MULTILINE)

def _strip_ansi(text: str) -> str:
    return _CTRL_RE.sub('', _ANSI_RE.sub('', text or ""))

def _last_block(text: str) -> str:
    text = _strip_ansi(text)
    i = text.rfind(_HEADING)
    return text[i:] if i >= 0 else text

def _try_parse_sf_json(stdout: str):
    """
    Try to parse SF CLI --json output. Returns dict or None.
    """
    s = (stdout or "").strip()
    if not s.startswith("{"):
        return None
    try:
        data = json.loads(s)
    except Exception:
        return None
    # common shapes: { "status": 0/1, "result": {...}, "message": "...", "name": "..."}
    return data

def _compact_from_text(text: str) -> tuple[str, str]:
    """
    Fallback parser for non-JSON output. Returns (status, summary_text).
    """
    block = _last_block(text)
    def last(rx):
        ms = list(rx.finditer(block))
        return ms[-1].group(1).strip() if ms else ""
    status  = last(_STATUS_RE)
    deploy  = last(_ID_RE)
    org     = last(_ORG_RE)
    elapsed = last(_TIME_RE)

    parts = []
    if status:  parts.append(f"Status: {status}")
    if deploy:  parts.append(f"Deploy ID: {deploy}")
    if org:     parts.append(f"Target Org: {org}")
    if elapsed: parts.append(f"Elapsed Time: {elapsed}")
    return (status or ""), ("\n".join(parts) if parts else "(no summary parsed)")

def _component_failures(text_or_json, max_chars: int = 4000) -> str:
    """
    Extract error detail either from JSON (preferred) or from text block.
    """
    if isinstance(text_or_json, dict):
        # Typical JSON error fields
        err_txt = []
        # v2 deploy shape
        res = text_or_json.get("result") or {}
        # component failures (Metadata API) often appear under "files" with problems
        files = res.get("files") or []
        for f in files:
            problems = f.get("problems") or []
            for p in problems:
                msg = p.get("message") or p.get("problem") or ""
                comp = p.get("name") or p.get("fullName") or f.get("filePath") or ""
                typ  = p.get("type") or f.get("type") or ""
                if msg:
                    err_txt.append(f"{typ} {comp} — {msg}")
        # generic error
        if not err_txt and text_or_json.get("message"):
            err_txt.append(text_or_json["message"])
        txt = "\n".join(err_txt).strip()
        return txt or "(no details)"
    # text fallback
    block = _last_block(text_or_json)
    i = block.find("Component Failures")
    if i >= 0:
        return block[i:i+max_chars].rstrip()
    tail = "\n".join(block.strip().splitlines()[-120:])
    return tail or "(no details)"

def show_sf_result(result, title: str = "Salesforce Command"):
    """
    Streamlit-friendly renderer:
      • Prefer structured --json output if present.
      • Otherwise summarize the last 'Deploying Metadata' block.
      • Only show big logs AFTER final Succeeded/Failed.
    """
    import streamlit as st
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    data = _try_parse_sf_json(stdout)

    st.subheader(title)

    if data:  # JSON path
        # Normalize status
        status_code = data.get("status")
        # Some sf commands use "result.status" too
        res = data.get("result") or {}
        final_status = (res.get("status") or "").lower()  # "Succeeded"/"Failed" sometimes
        # Try to derive "Succeeded"/"Failed" string
        if isinstance(status_code, int):
            status_str = "Succeeded" if status_code == 0 else "Failed"
        elif isinstance(status_code, str):
            status_str = "Succeeded" if status_code.lower() in ("0","succeeded","success") else "Failed"
        elif final_status:
            status_str = final_status.capitalize()
        else:
            status_str = ""

        # Build compact summary
        deploy_id = res.get("id") or res.get("deployId") or data.get("deployId") or ""
        org = data.get("orgId") or res.get("username") or res.get("orgUsername") or ""
        elapsed = res.get("elapsedTime") or ""
        parts = []
        if status_str: parts.append(f"Status: {status_str}")
        if deploy_id:  parts.append(f"Deploy ID: {deploy_id}")
        if org:        parts.append(f"Org/User: {org}")
        if elapsed:    parts.append(f"Elapsed: {elapsed}")
        summary = "\n".join(parts) if parts else "(no summary parsed)"

        succeeded = status_str.lower() == "succeeded"
        if succeeded:
            st.success("✅ Status: Succeeded")
            st.code(summary or "(no summary)")
            with st.expander("Logs (collapsed)"):
                st.code(stdout.strip() or "(no output)")
        else:
            st.error("❌ Status: Failed")
            st.code(summary or "(no summary)")
            st.subheader("Error details")
            st.code(_component_failures(data))
            with st.expander("Full JSON"):
                st.code(stdout.strip() or "(no output)")
        return

    # TEXT fallback
    full = (stdout + ("\n" + stderr if stderr else "")).strip()
    status, summary = _compact_from_text(full)
    if status.lower() == "succeeded":
        st.success("✅ Status: Succeeded")
        st.code(summary or "(no summary)")
        with st.expander("Logs (collapsed)"):
            st.code(_last_block(_strip_ansi(full)) or "(no output)")
    elif status.lower() == "failed":
        st.error("❌ Status: Failed")
        st.code(summary or "(no summary)")
        st.subheader("Error details")
        st.code(_component_failures(full))
        with st.expander("Full logs"):
            st.code(_last_block(_strip_ansi(full)) or "(no output)")
    else:
        st.warning("⚠️ No final status detected (CLI may have returned early).")
        st.code(summary or "(no summary)")
        with st.expander("Logs (collapsed)"):
            st.code(_last_block(_strip_ansi(full)) or "(no output)")



def _mask(v: str | None):
    if not v: return "—"
    if len(v) <= 4: return "*" * len(v)
    return v[:2] + "…" + v[-2:]

# Header + switcher
ORG_ALIAS = (os.getenv("SF_ORG_ALIAS") or "").strip() or "NOT SET"
DOMAIN = (os.getenv("SF_DOMAIN") or "").strip() or "test"
st.title(f"Clariti Environment — {ORG_ALIAS}")
st.caption(f"Domain: {DOMAIN}")

st.subheader("Environment")

files = _find_env_files()
if not files:
    st.warning(f"No .env files found in parent dir: {ENV_ROOT}")
    st.caption("Expected names like .env, .env.dev, ClaytonDev.env, etc.")
else:
    labels = [p.name for p in files]
    # keep current selection if we have one
    idx = 0
    if "__ENV_PATH__" in st.session_state:
        try:
            idx = files.index(Path(st.session_state["__ENV_PATH__"]).resolve())
        except Exception:
            idx = 0

    col1, col2 = st.columns([3, 1])
    choice = col1.selectbox(
        "Pick a .env to load",
        options=list(range(len(labels))),
        format_func=lambda i: labels[i],
        index=idx,
        key="__env_select__"
    )

    if col2.button("Load selected", key="__env_load__"):
        p = files[choice]
        st.session_state["__ENV_PATH__"] = str(p)

        # 1) load into this Streamlit process
        _load_env_snapshot(dotenv_values(p))

        # 2) promote to ENV_ROOT/.env so child scripts that call load_dotenv(find_dotenv)
        #    pick up THIS environment automatically
        if _activate_selected_dotenv(Path(p)):
            st.success(f"Activated environment: {Path(p).name} → {ACTIVE_DOTENV.name}")
        else:
            st.warning("Environment loaded in-memory, but failed to update ENV_ROOT/.env")

        st.rerun()


# Summary
ORG_ALIAS = (os.getenv("SF_ORG_ALIAS") or "").strip() or "NOT SET"
DOMAIN = (os.getenv("SF_DOMAIN") or "").strip() or "test"
st.caption(
    f"Current: **{ORG_ALIAS}** (Domain: {DOMAIN}) · "
    f"User: {_mask(os.getenv('SF_USERNAME'))} · "
    f"Org: {_mask(os.getenv('SF_ORG'))} · "
    f"Search dir: {ENV_ROOT} · "
    f"Path: {st.session_state.get('__ENV_PATH__', '(none)')}"
)

st.write(f"Python path: {sys.executable}")
# ==== YOUR EXISTING SIDEBAR & TOOL LOGIC CONTINUES BELOW =====================


# Sidebar navigation (unchanged)
st.sidebar.title("Script Selector")
script_option = st.sidebar.selectbox("Choose a tool to run:", [
    "Create App or Permit Type",
    "Create Custom Fields",
    "Insert Custom Fields",
    "Clone Email",
    "Email Formatter",
    "Email Regex",
    "Email Insert",
    "Update Email Flow",
    "Deploy FLow",
    "FlowDownloader",
    "FlowUploader"
])

# 🔹 H1 title shows the org alias inline
#st.title(f"Clariti Environment — {ORG_ALIAS}")
# (optional) small caption under the title
#st.caption(f"Domain: {DOMAIN}")


# === Base Permit Script ===
if script_option == "Create App or Permit Type":
    st.header("Create Base Permit / App")

    with st.form("base_permit_form"):
        new_type = st.text_input("🏷️ Enter New Type", value="Mobile Home Permit", key="base_permit_input")
        is_permit = st.checkbox("Is this a Permit?", value=True, help="Uncheck if this should be an Application type.")
        submitted_base = st.form_submit_button("Generate Code")

    if submitted_base:
        if not new_type.strip():
            st.warning("Please enter a valid type name.")
        else:
            with st.spinner(f"Generating {'Permit' if is_permit else 'Application'} code for: {new_type}..."):
                #result = _run_py("1GlobalValueXML.py", new_type, str(is_permit).lower())
                result = _run_py("CreateRecordType.py", new_type, str(is_permit).lower())

            if result.returncode == 0:
                st.success("✅ Updated metadata (GVS + Admin profile).")
                st.info("This script updates files inside force-app/... so there’s nothing to preview here.")
            else:
                st.error("❌ Code generation failed.")
                st.code(result.stderr or result.stdout or "No output")



# === Update Record Type Script ===
# === Update Record Type Script ===
elif script_option == "Create Custom Fields":
    st.header("🛠️ Create Custom Fields")

    # ---------- FORM ----------
    with st.form("update_record_form"):
        new_type = st.text_input(
            "🏷️ Enter New Type",
            value="Mobile Home Permit",
            key="update_record_input"
        )

        is_permit = st.checkbox(
            "Is this a Permit?",
            value=False,
            help="Check if this should update MUSW__Permit2__c instead of MUSW__Application2__c."
        )

        field_description = st.text_area(
            "📝 Field Metadata (TSV)",
            height=400,
            placeholder="Label\tType\tRequired\tHelp Text\nStatus\tPicklist\ttrue\t...\n...",
            key="update_record_extra"
        )

        submitted_update = st.form_submit_button("🔁 Create Custom Fields")

    # ---------- HANDLE SUBMIT (persist logs) ----------
    if submitted_update:
        if not new_type.strip():
            st.warning("Please enter a valid type name.")
        elif not field_description.strip():
            st.warning("Paste your field definitions (TSV) before running.")
        else:
            mode = "--permit" if is_permit else "--application"
            st.session_state["ccf_running"] = True
            with st.spinner(f"Updating Record Type for: {new_type} "
                            f"({'Permit' if is_permit else 'Application'})…"):
                # Prefer stdin for long TSVs
                from pathlib import Path
                script_path = (SCRIPT_DIR / "4RecordTypeUpdate.py").resolve()
                result = _run(
                    [sys.executable, str(script_path), mode],
                    input=field_description  # <-- TSV via stdin
                )
            # Save outputs so they survive the rerun after submit
            st.session_state["ccf_running"] = False
            st.session_state["ccf_stdout"] = result.stdout or ""
            st.session_state["ccf_stderr"] = result.stderr or ""
            st.session_state["ccf_returncode"] = result.returncode

    # ---------- SHOW LAST RESULT ----------
    if st.session_state.get("ccf_returncode") is not None:
        rc = st.session_state["ccf_returncode"]
        if rc == 0:
            st.success("✅ Custom fields generated.")
        else:
            st.error("❌ Generation failed (see logs below).")

        st.subheader("4RecordTypeUpdate output (stdout)")
        st.code(st.session_state.get("ccf_stdout", "") or "(no stdout)")

        err = st.session_state.get("ccf_stderr", "")
        if err.strip():
            st.subheader("stderr")
            st.code(err)

        # Optional quick checks to make the target obvious
        with st.expander("Debug"):
            st.code("\n".join([
                f"mode = {'--permit' if st.session_state.get('ccf_returncode') is not None and is_permit else '--application'}",
                f"new_type = {new_type!r}",
                f"stdout_len = {len(st.session_state.get('ccf_stdout',''))}",
                f"stderr_len = {len(st.session_state.get('ccf_stderr',''))}",
            ]))


# === Update Record Type Script ===
# === Insert Custom Fields ===
elif script_option == "Insert Custom Fields":
    st.header("🗂️ Insert Custom Fields")

    # ✅ Let user confirm/enter the label; default to session state if present
    label = st.text_input(
        "Record Type Label",
        value=(st.session_state.get("base_permit_type") or "").strip(),
        key="icf_label",
        help="Exact Record Type label used when you created the Record Type"
    ).strip()

    is_permit = st.checkbox(
        "Use Permit object (MUSW__Permit2__c)",
        value=st.session_state.get("base_is_permit", False),
        key="icf_is_permit",
        help="Unchecked = Application object (MUSW__Application2__c)"
    )

    if st.button("🚀 Push To Clariti", key="icf_push_btn"):
        if not label:
            st.warning("Please enter the Record Type label above.")
        else:
            # persist for later steps
            st.session_state["base_permit_type"] = label
            st.session_state["base_is_permit"] = is_permit

            flag = "--permit" if is_permit else "--application"
            script_name = "5UpdateCustomFields.py"   # or "5UpdateCustomFields.py"
            script_path = (SCRIPT_DIR / script_name).resolve()

            if not script_path.exists():
                st.error(f"❌ Script not found: {script_path}")
            else:
                cmd = [sys.executable, str(script_path), flag, label]
                st.caption(f"Running: `{' '.join(cmd)}`")

                with st.spinner(f"Deploying fields to {'Permit' if is_permit else 'Application'}…"):
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        cwd=SCRIPT_DIR,
                        env=os.environ.copy()
                    )

                if result.returncode == 0:
                    st.success("✅ Deployment finished.")
                    show_sf_result(result, title="Deploy")
                else:
                    st.error("❌ Deployment failed.")
                    st.code(result.stderr or result.stdout or "No error output")


# === Clone Email ===
# === Clone Email Templates ===
elif script_option == "Clone Email":
    st.header("🛠️ Create Custom Fields")

    with st.form("update_record_form"):
        new_type = st.text_input("🏷️ Enter New Application Type", value="Mobile Home Permit", key="update_record_input")
        field_description = st.text_area("📝 Optional Field Metadata", height=400, placeholder="Paste field definitions or picklist values here...", key="update_record_extra")
        submitted_update = st.form_submit_button("🔁 Create Custom Fields")

    if submitted_update:
        if not new_type.strip():
            st.warning("Please enter a valid application type.")
        else:
            with st.spinner(f"Updating Record Type for: {new_type}..."):
                python_exe = sys.executable  # this guarantees the same environment Streamlit is using
            result = _run_py("CloneEmailTemplatesList.py", new_type)


            if result.returncode == 0:
                st.success("✅ Record type updated successfully!")
                output_dir = Path("generated_code")
                if output_dir.exists():
                    for file in output_dir.glob("*"):
                        st.subheader(f"📄 {file.name}")
                        content = file.read_text(encoding="utf-8")
                        st.code(content, language="xml" if file.suffix == ".xml" else "java")
            else:
                st.error("❌ Record type update failed.")
                st.code(result.stderr)

# === Flow Downloader ===
elif script_option == "FlowDownloader":
    st.header("⬇️ Download Salesforce Flow")

    flow_api = st.text_input(
        "Flow API Name (DeveloperName)",
        placeholder="My_Flow",
        key="flow_dl_name"
    )

    # New options (checked by default)
    extract_zip = st.checkbox("Extract ZIP after download (--extract)", value=True, key="flow_dl_extract")
    with_fields = st.checkbox("Also export field dependencies (--with-fields)", value=True, key="flow_dl_with_fields")

    if st.button("Download", key="flow_dl_btn"):
        name = (flow_api or "").strip()
        if not name:
            st.warning("Please enter the Flow API name (e.g., My_Flow).")
        else:
            # Try common downloader filenames next to this Streamlit file
            script_dir = Path(__file__).resolve().parent
            candidates = [
                script_dir / "download_flow.py",   # name from our earlier script
                script_dir / "FlowDownloader.py",  # your original name
            ]
            script_path = next((p for p in candidates if p.exists()), None)

            if not script_path:
                st.error(
                    "Downloader script not found.\n"
                    f"Tried: {', '.join(str(p) for p in candidates)}"
                )
            else:
                cmd = [sys.executable, str(script_path), "--flow", name]
                if extract_zip:
                    cmd.append("--extract")
                if with_fields:
                    cmd.append("--with-fields")

                with st.spinner(f"Downloading {name}…"):
                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        encoding="utf-8"
                    )

                if result.returncode == 0:
                    st.success("✅ Download finished")
                    show_sf_result(result, title="Deploy")
                else:
                    st.error("❌ Download failed")
                    st.code(result.stderr or result.stdout or "No error output")


# === Flow Uploader ===
# === Flow Uploader ===
elif script_option == "FlowUploader":
    st.header("⬆️ Upload Flow to Target Org")
    org_alias = (os.getenv("SF_ORG_ALIAS") or os.getenv("SF_TARGET_ORG") or "").strip()
    if org_alias:
        st.caption(f"Target org alias: {org_alias}")

    if st.button("🚀 Upload Flow", key="flow_upload_btn"):
        with st.spinner("Deploying flow artifact from ./out …"):
            python_exe = sys.executable
            script_path = Path(__file__).with_name("upload_flow.py")
            if not script_path.exists():
                script_path = Path(__file__).with_name("FlowUploader.py")
            if not script_path.exists():
                st.error("Uploader script not found.")
            else:
                result = subprocess.run(
                    [python_exe, str(script_path), "--autostub-fields"],  # 👈 add flag
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=os.environ.copy(),
                )
                if result.returncode == 0:
                    st.success("✅ Deployment finished.")
                    show_sf_result(result, title="Deploy")
                else:
                    st.error("❌ Deployment failed.")
                    st.code(result.stderr or result.stdout or "No error output")


# === Email Formatter ===
elif script_option == "Email Formatter":
    st.header("🛠️ Email Formatter")

    with st.form("email_formatter_form"):
        permit_name = st.text_input(
            "🏷️ Enter New Application Type",
            value=st.session_state.get("base_permit_type", "Mobile Home Permit"),
            key="email_fmt_permit_name"
        )

        tsv = st.text_area(
            "Paste tab-delimited or spreadsheet text (Title\tSubject\tBody per row)",
            height=300,
            key="email_fmt_tsv"
        )

        submitted = st.form_submit_button("🔁 Run Email Formatter")

    if submitted:
        if not permit_name.strip():
            st.warning("Please enter a permit/application type.")
        elif not tsv.strip():
            st.warning("Please paste at least one row (Title\\tSubject\\tBody).")
        else:
            with st.spinner("Formatting templates…"):
                # persist for other tools
                st.session_state["base_permit_type"] = permit_name.strip()

                env = os.environ.copy()
                env.setdefault("PYTHONIOENCODING", "utf-8")

                result = subprocess.run(
                    [sys.executable, "6EmailFormatter.py",
                     st.session_state["base_permit_type"],  # <Base Name>
                     tsv],                                   # <Pasted Table Text>
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    cwd=SCRIPT_DIR,
                    env=env
                )

            if result.returncode == 0:
                st.success("✅ Email templates generated (see ./generated_code)")
                show_sf_result(result, title="Deploy")
                out_dir = Path("generated_code")
                if out_dir.exists():
                    for file in out_dir.glob("*"):
                        st.subheader(f"📄 {file.name}")
                        content = file.read_text(encoding="utf-8")
                        st.code(content[:1000] + ("…\n" if len(content) > 1000 else ""))
            else:
                st.error("❌ Email Formatter failed.")
                st.code(result.stderr or result.stdout or "No error output")


# === Email Regex ===
if script_option == "Email Regex":
    st.header("Email Regex")

    # Unchecked by default
    is_bold = st.checkbox("Is Bold", value=False)

    if st.button("Generate Permit Code"):
        new_type = st.session_state.get("base_permit_type", "Mobile Home Permit")

        # Build the command. Only add the bold flag if checked.
        cmd = ["python", "7EmailRegEx.py"]
        if is_bold:
            cmd.append("--bold-merge-vars")  # <-- new flag
        # If your script doesn't accept a positional arg, don't pass new_type.
        # If it DOES, uncomment the next line:
        # cmd.append(new_type)

        with st.spinner(f"Generating Base Permit code for: {new_type}..."):
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8"
            )

        if result.returncode == 0:
            st.success("✅ Code generated successfully!")
            output_dir = Path("generated_code")
            if output_dir.exists():
                for file in output_dir.glob("*"):
                    st.subheader(f"📄 {file.name}")
                    content = file.read_text(encoding="utf-8")
                    st.code(content, language="xml" if file.suffix == ".xml" else "java")
        else:
            st.error("Code generation failed.")
            st.code(result.stderr)


# === Email Insert ===
# === Email Insert ===
elif script_option == "Email Insert":
    st.header("🗂️ Email Insert")

    if st.button("🚀 Push To Clariti"):
        with st.spinner("Deploying email templates to Clariti…"):
            result = _run_py("8EmailInsert.py")  # ✅ no args
            # or, if you didn't add _run/_run_py helpers:
            # result = subprocess.run(
            #     [sys.executable, "8EmailInsert.py"],
            #     capture_output=True, text=True, encoding="utf-8",
            #     cwd=SCRIPT_DIR, env=os.environ.copy()
            # )

    if 'result' in locals():
        if result.returncode == 0:
            st.success("✅ Deployment finished.")
            show_sf_result(result, title="Deploy")
        else:
            st.error("❌ Deployment failed.")
            st.code(result.stderr or result.stdout or "No error output")


# === Update Flow ===
elif script_option == "Update Email Flow":
    st.header("🗂️ Update Email Flow")

    # Current name is fine to display/persist, but we won't pass it
    cur_type = (st.session_state.get("base_permit_type") or "Mobile Home Permit").strip()

    # Bool switch — True = Application (default), False = Permit
    is_application = st.checkbox(
        "Use 'Application' terminology (uncheck for 'Permit')",
        value=st.session_state.get("email_flow_is_application", True),
        key="email_flow_is_application",
    )
    term = "application" if is_application else "permit"
    st.session_state["email_flow_terminology"] = term

    st.caption(f"Using permit type: {cur_type} · Terminology: **{term.title()}**")

    if st.button("🚀 Update Flow"):
        with st.spinner("Updating flow from email templates…"):
            # ✅ pass ONLY the flag(s) that 9SetEmailFlow.py knows about
            result = _run_py("9SetEmailFlow.py", "--terminology", term)

    if 'result' in locals():
        if result.returncode == 0:
            st.success("✅ Update finished.")
            show_sf_result(result, title="Deploy")
        else:
            st.error("❌ Update failed.")
            st.code(result.stderr or result.stdout or "No error output")





# === Deploy Flow ===
elif script_option == "Deploy FLow":
    st.header("🗂️ Deploy FLow")

    # Safely get the chosen permit/application type
    cur_type = (st.session_state.get("base_permit_type") or "Mobile Home Permit").strip()
    st.caption(f"Using permit type: {cur_type}")

    # Build Flow API name: Milestone_Emails_<underscored_name>
    flow_api = "Milestone_Emails_" + "".join(ch if ch.isalnum() else "_" for ch in cur_type)

    if st.button("🚀 Push To Clariti"):
        with st.spinner("Deploying email templates to Clariti…"):
            # Prefer the newer script if present
            deploy_script = "11FlowDeploy.py" if Path("11FlowDeploy.py").exists() else "10FlowDeploy.py"

            # EITHER deploy the specific flow by name:
            result = _run_py(deploy_script, "--flow-name", flow_api)

            # OR, if you want the script to auto-pick the latest flow, use:
            # result = _run_py(deploy_script)  # no args

    if 'result' in locals():
        if result.returncode == 0:
            st.success("✅ Deployment finished.")
            show_sf_result(result, title="Deploy")
        else:
            st.error("❌ Deployment failed.")
            st.code(result.stderr or result.stdout or "No error output")




# === Deploy Metadata Script ===
elif script_option == "Deploy Metadata":
    st.header("📤 Deploy Metadata to Salesforce")

    with st.form("deploy_metadata_form"):
        confirmed = st.checkbox("✅ I confirm I want to deploy to Salesforce", value=False)
        submitted_deploy = st.form_submit_button("🚀 Run Deployment")

    if submitted_deploy:
        if not confirmed:
            st.warning("You must confirm the deployment before continuing.")
        else:
            with st.spinner("Deploying metadata to Salesforce..."):
                result = subprocess.run(
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File", "deploy_all_metadata.ps1"],
                    capture_output=True,
                    text=True
                )

            if result.returncode == 0:
                st.success("✅ Deployment completed successfully!")
                st.code(result.stdout)
            else:
                st.error("❌ Deployment failed.")
                st.code(result.stderr)



# === Create Ticket Script ===
elif script_option == "Create Ticket":
    st.header("🎫 Create Clariti Ticket")
    with st.form("create_ticket_form"):
        type_value = st.text_input("📌 Permit Type", value="Tree Removal Permit")
        account_id = st.text_input("🏢 Account ID", placeholder="e.g. 001xxxxxxxxxxxxxxx")
        description = st.text_area("📝 Description", value="Testing Tree Removal Permit")
        submitted_ticket = st.form_submit_button("🚀 Create Ticket")

        if submitted_ticket:
            if not type_value.strip() or not account_id.strip() or not description.strip():
                st.warning("Please fill out all required fields.")
            else:
                st.session_state["base_permit_type"] = type_value.strip()
                with st.spinner("Running createticket.py..."):
                    result = _run_py("createticket.py", type_value, account_id, description)


            if result.returncode == 0:
                st.success("✅ Ticket script ran successfully!")
                show_sf_result(result, title="Deploy")
            else:
                st.error("❌ Ticket creation failed.")
                st.code(result.stderr or result.stdout or "No error output")


