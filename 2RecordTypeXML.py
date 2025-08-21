import openai
from pathlib import Path
import os
import sys

# Validate input
if len(sys.argv) < 2:
    print("Error: NEW_TYPE not provided.")
    sys.exit(1)

NEW_TYPE = sys.argv[1]
RECORD_TYPE = "Business_License"  # Can be made dynamic in the future
print(f"[Generating] ValueSettings for: {NEW_TYPE} under Record Type: {RECORD_TYPE}")

# File paths
OUTPUT_FILE = Path("force-app/main/default/objects/MUSW__Application2__c/fields/MUSW__Type2__c.field-meta.xml")

# Prompts
SYSTEM_PROMPT = """You are an expert Salesforce XML metadata generator. Output only valid Salesforce field XML.
Do not explain or add markdown.
"""

USER_PROMPT_TEMPLATE = """
Update the following CustomField XML file to include a new <valueSettings> block scoped to the controllingFieldValue "{record_type}" for value "{new_type}".
Ensure the valueSet section still references MUSW__Application_Types and remains restricted.

Here is the existing field XML:
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
    print(f"[Generating] Updated field metadata XML: {path}")

def main():
    existing_xml = load_xml(OUTPUT_FILE)
    user_prompt = USER_PROMPT_TEMPLATE.format(
        record_type=RECORD_TYPE,
        new_type=NEW_TYPE,
        existing_xml=existing_xml
    )
    updated_xml = call_gpt(SYSTEM_PROMPT, user_prompt)
    save_output(updated_xml, OUTPUT_FILE)

if __name__ == "__main__":
    main()
