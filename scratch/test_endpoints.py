import urllib.request
import time
import json

endpoints = [
    ("devices", "http://localhost:8000/api/v1/devices"),
    ("captures", "http://localhost:8000/api/v1/captures?limit=15"),
    ("library", "http://localhost:8000/api/v1/library"),
    ("unknowns", "http://localhost:8000/api/v1/captures?only_unknown=true&limit=50"),
    ("timeline", "http://localhost:8000/api/v1/analytics/timeline"),
]

for name, url in endpoints:
    start = time.time()
    print(f"Testing {name} ({url})...")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            elapsed = time.time() - start
            print(f"  SUCCESS in {elapsed:.3f}s (returned {len(data) if isinstance(data, list) else len(data.get('items', []))} items)")
    except Exception as e:
        elapsed = time.time() - start
        print(f"  FAILED/TIMEOUT in {elapsed:.3f}s: {e}")
