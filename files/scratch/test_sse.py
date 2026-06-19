import urllib.request

try:
    print("Connecting to SSE endpoint...")
    req = urllib.request.Request("http://localhost:8000/api/v1/events")
    with urllib.request.urlopen(req) as response:
        print("Connected!")
        print("Headers:", response.headers.items())
        for i in range(5):
            line = response.readline()
            print(f"Line {i}: {line.decode('utf-8').strip()}")
except Exception as e:
    print("Error:", e)
