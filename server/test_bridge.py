"""End-to-end test: fake LC7001 + fake Hubitat, driving the real bridge."""
import json, socket, threading, time, sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["LEGRAND_CONFIG"] = "/tmp/test_config.json"
if os.path.exists("/tmp/test_config.json"):
    os.remove("/tmp/test_config.json")

# ---- fake Hubitat: records every POST the bridge sends -------------------
received = []
class HubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(n)))
        self.send_response(200); self.send_header("Content-Type","application/json")
        self.end_headers(); self.wfile.write(b'{"ok":true}')
    def log_message(self, *a): pass

hub = HTTPServer(("127.0.0.1", 0), HubHandler)
HUB_PORT = hub.server_address[1]
threading.Thread(target=hub.serve_forever, daemon=True).start()

# ---- fake LC7001: accepts one client, echoes scripted frames ------------
lc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
lc_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
lc_sock.bind(("127.0.0.1", 0))
LC_PORT = lc_sock.getsockname()[1]
lc_sock.listen(1)
commands_seen = []

def lc_server():
    conn, _ = lc_sock.accept()
    msgs = [
        {"Service":"ping"},                                        # must be filtered
        {"Service":"BroadcastMemory","Free":1234},                 # must be filtered
        {"Service":"SystemInfo","Model":"LC7001","FirmwareVersion":"2.1.7",
         "MACAddress":"AA:BB:CC:DD:EE:FF","HouseID":42,"UpdateState":{"a":1}},
        {"Service":"ListZones","ZoneList":[{"ZID":4},{"ZID":7}]},
        {"Service":"ReportZoneProperties","ZID":4,
         "PropertyList":{"Name":"Master Bathroom Fan","Power":True,"PowerLevel":75}},
        {"Service":"ReportZoneProperties","ZID":7,"DebugStr":"noisy"},  # filtered
    ]
    stream = b"".join(json.dumps(m).encode() + b"\x00" for m in msgs)
    # worst case: one byte per send, so every message is split across reads
    for i in range(len(stream)):
        conn.sendall(stream[i:i+1])
    # now read whatever commands the bridge sends us
    buf = b""
    conn.settimeout(6)
    try:
        while True:
            d = conn.recv(4096)
            if not d: break
            buf += d
            parts = buf.split(b"\x00"); buf = parts.pop()
            for p in parts:
                if p: commands_seen.append(json.loads(p.decode()))
    except socket.timeout:
        pass

threading.Thread(target=lc_server, daemon=True).start()

# ---- start the bridge under test ----------------------------------------
import legrand
legrand.LC7001_PORT = LC_PORT
BRIDGE_PORT = 21999
threading.Thread(
    target=lambda: __import__("waitress").serve(
        legrand.app, host="127.0.0.1", port=BRIDGE_PORT, threads=4, _quiet=True),
    daemon=True).start()
legrand.lc7001.start(delay=0)
time.sleep(0.4)

import requests
BASE = f"http://127.0.0.1:{BRIDGE_PORT}"
API_URL = f"http://127.0.0.1:{HUB_PORT}/HubNotify?access_token=test"
fails = []
def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got {got!r}" + ("" if ok else f" want {want!r}"))
    if not ok: fails.append(label)

# 1. /init
r = requests.post(f"{BASE}/init", json={"hubIP":"127.0.0.1","apiServerUrl":API_URL}, timeout=5)
check("POST /init status", r.status_code, 200)
check("POST /init body", r.json(), {"initReceived": True})
time.sleep(1.5)

# 2. /status
r = requests.get(f"{BASE}/status", timeout=5)
check("GET /status connected", r.json(), {"initHubConnected": True})

# 3. /command with string ZID
r = requests.post(f"{BASE}/command",
                  json={"Service":"SetZoneProperties","ZID":"4","PropertyList":{"Power":True}}, timeout=5)
check("POST /command status", r.status_code, 200)
check("POST /command sent", r.json()["sent"], True)

# 4. ZID "null" coercion
requests.post(f"{BASE}/command", json={"Service":"ReportZoneProperties","ZID":"null"}, timeout=5)
time.sleep(1.5)

services = [m.get("Service") for m in received]
print("\n--- hub received:", services)
print("--- lc7001 received:", commands_seen)

check("filtered ping/BroadcastMemory/DebugStr", 
      [s for s in services if s in ("ping","BroadcastMemory")], [])
check("SystemInfo forwarded", "SystemInfo" in services, True)
check("ListZones forwarded", "ListZones" in services, True)
check("ReportZoneProperties forwarded once", services.count("ReportZoneProperties"), 1)
check("hubConnected=true posted", 
      any(m.get("Service")=="WebServerUpdate" and m.get("hubConnected") is True for m in received), True)
zone4 = next((m for m in received if m.get("Service")=="ReportZoneProperties"), {})
check("fragmented payload intact", zone4.get("PropertyList",{}).get("Name"), "Master Bathroom Fan")
check("ZID coerced to int", commands_seen[0]["ZID"], 4)
check("ZID 'null' -> 0", commands_seen[1]["ZID"], 0)
check("command IDs increment", [c["ID"] for c in commands_seen[:2]], [1,2])

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
