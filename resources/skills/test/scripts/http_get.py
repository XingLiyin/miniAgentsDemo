import requests
import sys

url = sys.argv[1] if len(sys.argv) > 1 else "https://httpbin.org/get"
resp = requests.get(url, timeout=10)
print(f"Status: {resp.status_code}")
print(resp.text[:500])
