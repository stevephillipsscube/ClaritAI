from simple_salesforce import Salesforce
import os

sf = Salesforce(
    username=os.getenv("SF_USERNAME"),
    password=os.getenv("SF_PASSWORD"),
    security_token=os.getenv("SF_SECURITY_TOKEN"),
    domain=os.getenv("SF_DOMAIN", "login")
)

# 🔍 get Record Type Id for “Permit Application”
rt_id = sf.query("""
SELECT Id FROM RecordType
WHERE SobjectType = 'MUSW__Application2__c'
AND Name = 'Permit Application'
""")['records'][0]['Id']

payload = {
    "RecordTypeId"      : rt_id,                 # ‘Permit Application’ record type
    "MUSW__Type2__c"     : "Mobile Home Application",
    "Name"              : "Auto-Mobile-Home-Permit",  # Optional, will auto-number if omitted
 #   "MUSW__Applicant__c": "First Last",
    "MUSW__Account__c"  : "001fo000002p1aSAAQ",
 #   "MUSW__Parcel__c"   : "789",
    "MUSW__Description__c": "Initial intake created by automation script"
}

try:
    res = sf.MUSW__Application2__c.create(payload)
    print("✅ Created Id:", res['id'])
except Exception as e:
    # Print the first error from Salesforce; it tells you the next missing field
    print("❌", e)

