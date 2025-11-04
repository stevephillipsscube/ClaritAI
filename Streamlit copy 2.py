import streamlit as st
import subprocess
from pathlib import Path
import sys
import os
from dotenv import load_dotenv, find_dotenv

# 🔹 Load .env early so we can use the alias in titles
load_dotenv(find_dotenv(usecwd=True), override=False)
ORG_ALIAS = (os.getenv("SF_ORG_ALIAS") or "").strip() or "NOT SET"
DOMAIN = (os.getenv("SF_DOMAIN") or "").strip() or "test"

# ✅ MUST BE FIRST STREAMLIT COMMAND
st.set_page_config(
    page_title=f"Clariti Environment — {ORG_ALIAS}",
    layout="wide"
)

# (optional) keep this diag if you like
st.write(f"Python path: {sys.executable}")

# Sidebar navigation (unchanged)
st.sidebar.title("Script Selector")
script_option = st.sidebar.selectbox("Choose a tool to run:", [
    "Create Permit Type",
    "Insert Permit Type",
    "Create Custom Fields",
    "Insert Custom Fields",
    "Clone Email",
    "Email Formatter",
    "Email Regex",
    "Email Insert",
    "Update Email Flow",
    "Deploy FLow"
])

# 🔹 H1 title shows the org alias inline
st.title(f"Clariti Environment — {ORG_ALIAS}")
# (optional) small caption under the title
st.caption(f"Domain: {DOMAIN}")


# === Base Permit Script ===
if script_option == "Create Permit Type":
    st.header("Create Base Permit")

    with st.form("base_permit_form"):
        new_type = st.text_input("🏷️ Enter New Application Type", value="Mobile Home Permit", key="base_permit_input")
        submitted_base = st.form_submit_button("Generate Permit Code")

    if submitted_base:
        if not new_type.strip():
            st.warning("Please enter a valid application type.")
        else:
            with st.spinner(f"Generating Base Permit code for: {new_type}..."):
                result = subprocess.run(
                    ["python", "1GlobalValueXML.py", new_type],
                    capture_output=True,
                    text=True,
                    encoding="utf-8"  # ✅ add this
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


# === Record Type Script ===
elif script_option == "Insert Permit Type":
    st.header("🗂️ Insert Permit Type")

    with st.form("record_type_form"):
        new_type = st.text_input("🏷️ Enter New Application Type", value="Mobile Home Permit", key="record_type_input")
        submitted_record = st.form_submit_button("🚀 Push To Clariti")

    if submitted_record:
        if not new_type.strip():
            st.warning("Please enter a valid application type.")
        else:
            with st.spinner(f"Generating Record Type code for: {new_type}..."):
                python_exe = sys.executable  # This ensures we use the active Python environment
            result = subprocess.run(
                [python_exe, "3CreateRecord.py", new_type],
                capture_output=True,
                text=True,
                encoding="utf-8"  # ✅ add this
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
                st.error("❌ Code generation failed.")
                st.code(result.stderr)


# === Update Record Type Script ===
# === Update Record Type Script ===
elif script_option == "Create Custom Fields":
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
                result = subprocess.run(
                    ["python", "4RecordTypeUpdate.py", new_type, field_description],
                    capture_output=True,
                    text=True,
                    encoding="utf-8"  # ✅ add this
                )

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

# === Record Type Script ===
elif script_option == "Insert Custom Fields":
    st.header("🗂️ Insert Permit Type")

    with st.form("record_type_form"):
        new_type = st.text_input("🏷️ Enter New Application Type", value="Mobile Home Permit", key="record_type_input")
        submitted_record = st.form_submit_button("🚀 Push To Clariti")

    if submitted_record:
        if not new_type.strip():
            st.warning("Please enter a valid application type.")
        else:
            with st.spinner(f"Generating Record Type code for: {new_type}..."):
                python_exe = sys.executable  # This ensures we use the active Python environment
            result = subprocess.run(
                [python_exe, "5UpdateCustomFields.py", new_type],
                capture_output=True,
                text=True,
                encoding="utf-8"  # ✅ add this
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
                st.error("❌ Code generation failed.")
                st.code(result.stderr)

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
            result = subprocess.run(
                [python_exe, "CloneEmailTemplatesList.py", new_type, field_description],
                capture_output=True,
                text=True,
                encoding="utf-8"  # ✅ add this
            )



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

# === Update Record Type Script ===
elif script_option == "Email Formatter":
    st.header("🛠️ Email Formatter")

    with st.form("update_record_form"):
        new_type = st.text_input("🏷️ Enter New Application Type", value="Mobile Home Permit", key="update_record_input")
        field_description = st.text_area("📝 Optional Field Metadata", height=400, placeholder="Paste field definitions or picklist values here...", key="update_record_extra")
        submitted_update = st.form_submit_button("🔁 Email Formatter")

    if submitted_update:
        if not new_type.strip():
            st.warning("Please enter a valid application type.")
        else:
            with st.spinner(f"Updating Record Type for: {new_type}..."):
                result = subprocess.run(
                    ["python", "6EmailFormatter.py", new_type, field_description],
                    capture_output=True,
                    text=True,
                    encoding="utf-8"  # ✅ add this
                )

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
elif script_option == "Email Insert":
    st.header("🗂️ Email Insert")

    if st.button("🚀 Push To Clariti"):
        with st.spinner("Deploying email templates to Clariti…"):
            python_exe = sys.executable  # use current venv's Python
            result = subprocess.run(
                [python_exe, "8EmailInsert.py"],  # no args needed
                capture_output=True,
                text=True,
                encoding="utf-8"
            )

        if result.returncode == 0:
            st.success("✅ Deployment finished.")
            st.code(result.stdout or "(no output)")
        else:
            st.error("❌ Deployment failed.")
            st.code(result.stderr or result.stdout)

# === Update Flow ===
elif script_option == "Update Email Flow":
    st.header("🗂️ Update Email Flow")

    if st.button("🚀 Push To Clariti"):
        with st.spinner("Deploying email templates to Clariti…"):
            python_exe = sys.executable  # use current venv's Python
            result = subprocess.run(
                [python_exe, "9SetEmailFlow.py"],  # no args needed
                capture_output=True,
                text=True,
                encoding="utf-8"
            )

        if result.returncode == 0:
            st.success("✅ Deployment finished.")
            st.code(result.stdout or "(no output)")
        else:
            st.error("❌ Deployment failed.")
            st.code(result.stderr or result.stdout)

# === Deploy Flow ===
elif script_option == "Deploy FLow":
    st.header("🗂️ Deploy FLow")

    if st.button("🚀 Push To Clariti"):
        with st.spinner("Deploying email templates to Clariti…"):
            python_exe = sys.executable  # use current venv's Python
            result = subprocess.run(
                [python_exe, "10FlowDeploy.py"],  # no args needed
                capture_output=True,
                text=True,
                encoding="utf-8"
            )

        if result.returncode == 0:
            st.success("✅ Deployment finished.")
            st.code(result.stdout or "(no output)")
        else:
            st.error("❌ Deployment failed.")
            st.code(result.stderr or result.stdout)


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
            with st.spinner("Running createticket.py..."):
                result = subprocess.run(
                    ["python", "createticket.py", type_value, account_id, description],
                    capture_output=True,
                    text=True
                )

            if result.returncode == 0:
                st.success("✅ Ticket script ran successfully!")
                st.code(result.stdout)
            else:
                st.error("❌ Ticket creation failed.")
                st.code(result.stderr)


