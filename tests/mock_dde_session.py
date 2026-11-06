"""Protocol-only subprocess fixture. Does not connect to any application."""
import json
import sys

counter=0
for line in sys.stdin:
    request=json.loads(line)
    if request.get("action")=="shutdown":
        break
    counter+=1
    token="wrong-request" if request["request"].get("mock_wrong_id") else request["id"]
    print(json.dumps({"id":token,"result":{"connected":True,"fixture_counter":counter}}),flush=True)
