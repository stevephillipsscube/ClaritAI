import openai
from pathlib import Path
import os
import sys

# Get NEW_TYPE from CLI
if len(sys.argv) < 2:
    print("Error: NEW_TYPE not provided.")
    sys.exit(1)

NEW_TYPE = sys.argv[1]
FIELD_NAME = "MUSW__Type2__c"
print(f"[Generating] Picklist value mapping for: {NEW_TYPE} on Record Type: Business_License")

# File path
OUTPUT_FILE = Path("force-app/main/default/objects/MUSW__Application2__c/recordTypes/Business_License.recordType-meta.xml")

# GPT Prompting
SYSTEM_PROMPT = """You are a Salesforce metadata expert. Output only valid Salesforce RecordType XML.
Do not explain or add markdown.
"""

USER_PROMPT_TEMPLATE = """
Update the following RecordType metadata XML to include a new <picklistValues> mapping.
The target field is "{field_name}" and the new picklist value is "{new_type}".

The new block should look like this and go inside <RecordType>:
<picklistValues>
    <picklist>{field_name}</picklist>
    <values>
        <fullName>{new_type}</fullName>
        <default>false</default>
        <active>true</active>
    </values>
</picklistValues>

Here is the existing RecordType XML:
{existing_xml}
"""

def load_xml(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def call_gpt(system_prompt, user_prompt):
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    return response["choices"][0]["message"]["content"]

def save_output(content, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[Generating] Updated RecordType XML: {path}")

def main():
    existing_xml = load_xml(OUTPUT_FILE)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        new_type=NEW_TYPE,
        field_name=FIELD_NAME,
        existing_xml=existing_xml
    )
    updated_xml = call_gpt(SYSTEM_PROMPT, user_prompt)
    save_output(updated_xml, OUTPUT_FILE)

if __name__ == "__main__":
    main()
