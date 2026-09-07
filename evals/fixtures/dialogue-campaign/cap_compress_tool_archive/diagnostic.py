import json
for i in range(360):
 print(json.dumps({"tenant":f"tenant-{i:03d}","actual_error":"E_POOL_STARVATION" if i==97 else None,"request_id":"rq-77109" if i==97 else f"rq-{i}","details":"observability payload "*12}))
