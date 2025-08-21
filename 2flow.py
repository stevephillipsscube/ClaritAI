import os
import base64
import requests
import zipfile
import io
from simple_salesforce import Salesforce
from dotenv import load_dotenv

# Load creds
load_dotenv(override=True)
username = os.getenv("SF_USERNAME")
password = os.getenv("SF_PASSWORD")
security_token = os.getenv("SF_SECURITY_TOKEN")
domain = os.getenv("SF_DOMAIN", "login").strip()


# Authenticate
sf = Salesforce(username=username, password=password, security_token=security_token, domain=domain)
access_token = sf.session_id
instance_url = sf.sf_instance


# Build deploy ZIP
zip_buffer = io.BytesIO()
with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write("package.xml", "package.xml")
    zf.write("flows/Modular_Home_Permit_Flow.flow-meta.xml", "flows/Modular_Home_Permit_Flow.flow-meta.xml")

zip_b64 = base64.b64encode(zip_buffer.getvalue()).decode()

# Build SOAP deploy request
soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:xsd="http://www.w3.org/2001/XMLSchema"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
              xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
  <env:Header>
    <SessionHeader xmlns="http://soap.sforce.com/2006/04/metadata">
      <sessionId>{access_token}</sessionId>
    </SessionHeader>
  </env:Header>
  <env:Body>
    <deploy xmlns="http://soap.sforce.com/2006/04/metadata">
      <ZipFile>{zip_b64}</ZipFile>
    <DeployOptions>
    <rollbackOnError>true</rollbackOnError>
    <singlePackage>true</singlePackage>
    </DeployOptions>

    </deploy>
  </env:Body>
</env:Envelope>
"""

# Post to Metadata API
# Post to Metadata API
url = f"https://{instance_url}/services/Soap/m/59.0"
headers = {
    "Content-Type": "text/xml",
    "SOAPAction": "deploy"
}
response = requests.post(url, headers=headers, data=soap_body)

print("✅ Deployment Request Sent")

# Extract Deployment ID from response
import xml.etree.ElementTree as ET
root = ET.fromstring(response.text)
ns = {'sf': 'http://soap.sforce.com/2006/04/metadata', 'soapenv': 'http://schemas.xmlsoap.org/soap/envelope/'}
deploy_id_elem = root.find('.//sf:id', ns)
if deploy_id_elem is None:
    print("❌ Failed to get Deployment ID")
    print(response.text)
    exit()

deploy_id = deploy_id_elem.text
print(f"🆔 Deployment ID: {deploy_id}")

# Optional wait before checking status
import time
time.sleep(3)

# Check Deployment Status
status_soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<env:Envelope xmlns:xsd="http://www.w3.org/2001/XMLSchema"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
              xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
  <env:Header>
    <SessionHeader xmlns="http://soap.sforce.com/2006/04/metadata">
      <sessionId>{access_token}</sessionId>
    </SessionHeader>
  </env:Header>
  <env:Body>
    <checkDeployStatus xmlns="http://soap.sforce.com/2006/04/metadata">
      <id>{deploy_id}</id>
      <includeDetails>true</includeDetails>
    </checkDeployStatus>
  </env:Body>
</env:Envelope>
"""

status_headers = {
    "Content-Type": "text/xml",
    "SOAPAction": "checkDeployStatus"
}
status_response = requests.post(url, headers=status_headers, data=status_soap_body)

print("📦 Deployment Status Response:")
print(status_response.text)
