from simple_salesforce import Salesforce
from dotenv import load_dotenv
import os

load_dotenv(override=True)
sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "login")
)

profiles = sf.query("SELECT Id, Name FROM Profile WHERE Name LIKE '%Admin%'")
for p in profiles["records"]:
    print(f"{p['Id']} - {p['Name']}")
