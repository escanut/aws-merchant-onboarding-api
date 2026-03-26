import json
import uuid
import boto3
import os
import re
import base64
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key
 
dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("TABLE_NAME", "merchants")
table = dynamodb.Table(TABLE_NAME)

# We use a Global secondary index to reduce cost for querty for the GET/ merchants
GSI_NAME = "entity-type-index"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
EMAIL_REGEX = re.compile(r"^[\w\.-]+@[\w\.-]+\.\w+$")


def respond(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
 

def validate_post_body(body):
    required = ["business_name", "cac_number", "contact_email"]
    missing = [f for f in required if not body.get(f, "").strip()]
    if missing:
        return None, f"Missing or empty fields: {', '.join(missing)}"

    email = body["contact_email"].strip().lower()
    if not EMAIL_REGEX.match(email):
        return None, "Invalid contact_email format"

    # CAC is Corporate Affairs Commission in Nigeria for businesses to register
    cac = body["cac_number"].strip()
    if not cac.isdigit() or len(cac) != 6:
        return None, "cac_number must be exactly 6 digits"

    return {
        "business_name": body["business_name"].strip(),
        "cac_number": cac,
        "contact_email": email,
    }, None

def post_merchant(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return respond(400, {"error": "Request body must be valid JSON"})

    validated, error = validate_post_body(body)
    if error:
        return respond(400, {"error": error})
    
    merchant_id = str(uuid.uuid4())
    created_date = datetime.now(timezone.utc).isoformat()

    item = {
        "merchant_id": merchant_id,
        "created_date": created_date,
        "entity_type": "MERCHANT",
        "business_name": validated["business_name"],
        "cac_number": validated["cac_number"],
        "contact_email": validated["contact_email"],
        "status": "active",
    }
    
    table.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(cac_number)",
    )

    return respond(201, {"merchant_id": merchant_id, "created_date": created_date})


def get_all_merchants(event):
    params = event.get("queryStringParameters") or {}

    try:
        limit = min(int(params.get("limit", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    except ValueError:
        return respond(400, {"error": "limit must be an integer"})
    
    query_kwargs = {
        "IndexName": GSI_NAME,
        "KeyConditionExpression": Key("entity_type").eq("MERCHANT"),
        "Limit": limit,
        "ScanIndexForward": False,  # newest first
    }
 
    cursor = params.get("cursor")
    if cursor:
        try:
            exclusive_start_key = json.loads(
                base64.b64decode(cursor.encode()).decode()
            )
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key
        except Exception:
            return respond(400, {"error": "Invalid cursor"})
 
    response = table.query(**query_kwargs)

    next_cursor = None
    if "LastEvaluatedKey" in response:
        next_cursor = base64.b64encode(
            json.dumps(response["LastEvaluatedKey"]).encode()
        ).decode()
 
    return respond(200, {
        "merchants": response["Items"],
        "count": len(response["Items"]),
        "next_cursor": next_cursor,
    })
 

def get_merchant_by_id(merchant_id):
    response = table.query(
        KeyConditionExpression=Key("merchant_id").eq(merchant_id),
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return respond(404, {"error": "Merchant not found"})
    return respond(200, items[0])

ROUTES = {
    ("POST", "/merchants"): post_merchant,
    ("GET", "/merchants"): get_all_merchants,
}

def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    path = event.get("path", "")
    path_params = event.get("pathParameters") or {}
 
    if (method, path) in ROUTES:
        return ROUTES[(method, path)](event)

    # Handle dynamic routes like /merchants/{id}
    if method == "GET" and path_params.get("id"):
        return get_merchant_by_id(path_params["id"])

    return respond(404, {"error": "Route not found"})

