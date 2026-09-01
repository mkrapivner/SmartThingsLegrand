"""End-to-end test: fake LC7001 + fake Hubitat, driving the real bridge."""
import json, os, socket, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Keep save_config out of the repo working directory.
os.environ["LEGRAND_CONFIG"] = os.path.join(
    tempfile.mkdtemp(prefix="legrand-test-"), "config.json")

import legrand
import requests
from waitress import serve

DELIMITER = legrand.DELIMITER


def wait_until(pred, timeout=10.0, interval=0.01):
    """Poll until pred() is true. Returns False on timeout rather than raising,
    so the check that follows reports what actually failed."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


# ---- fake Hubitat: records every POST the bridge sends -------------------
received = []
bad_paths = []

class HubHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # The bridge must address us at exactly the apiServerUrl path. A doubled
        # slash here is the bug fixed in LegrandConnect.groovy.
        if not self.path.startswith("/HubNotify?"):
            bad_paths.append(self.path)
        n = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(n)))
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a): pass

hub = ThreadingHTTPServer(("127.0.0.1", 0), HubHandler)
HUB_PORT = hub.server_address[1]
threading.Thread(target=hub.serve_forever, daemon=True).start()

# ---- fake LC7001: accepts one client, echoes scripted frames ------------
lc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
    stream = b"".join(json.dumps(m).encode() + DELIMITER for m in msgs)
    # worst case: one byte per send, so every message is split across reads
    for i in range(len(stream)):
        conn.sendall(stream[i:i+1])
    # now read whatever commands the bridge sends us
    buf = b""
    conn.settimeout(10)
    try:
        while True:
            d = conn.recv(4096)
            if not d: break
            buf += d
            parts = buf.split(DELIMITER); buf = parts.pop()
            for p in parts:
                if p: commands_seen.append(json.loads(p.decode()))
    except socket.timeout:
        pass

threading.Thread(target=lc_server, daemon=True).start()

# ---- start the bridge under test ----------------------------------------
legrand.LC7001_PORT = LC_PORT
BRIDGE_PORT = 21999
threading.Thread(
    target=serve, args=(legrand.app,),
    kwargs=dict(host="127.0.0.1", port=BRIDGE_PORT, _quiet=True),
    daemon=True).start()
legrand.lc7001.start(delay=0)

BASE = f"http://127.0.0.1:{BRIDGE_PORT}"
API_URL = f"http://127.0.0.1:{HUB_PORT}/HubNotify?access_token=test"
fails = []
def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: got {got!r}" + ("" if ok else f" want {want!r}"))
    if not ok: fails.append(label)

wait_until(lambda: requests.get(f"{BASE}/status", timeout=1).ok)

# 1. /init
r = requests.post(f"{BASE}/init", json={"hubIP":"127.0.0.1","apiServerUrl":API_URL}, timeout=5)
check("POST /init status", r.status_code, 200)
check("POST /init body", r.json(), {"initReceived": True})
wait_until(lambda: legrand.lc7001.connected)

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

wait_until(lambda: len(received) >= 4)
wait_until(lambda: len(commands_seen) >= 2)

print("\n--- hub received:", [m.get("Service") for m in received])
print("--- lc7001 received:", commands_seen)

# The exact list pins ordering and the absence of the filtered messages
# (ping, BroadcastMemory, and the DebugStr-bearing ReportZoneProperties).
check("hub received, in order", [m.get("Service") for m in received],
      ["WebServerUpdate", "SystemInfo", "ListZones", "ReportZoneProperties"])
check("hubConnected=true posted first", received[0],
      {"hubConnected": True, "Service": "WebServerUpdate"})
check("fragmented payload intact",
      received[3].get("PropertyList", {}).get("Name"), "Master Bathroom Fan")
check("hub addressed at /HubNotify", bad_paths, [])

# Pins ZID coercion ("4" -> 4, "null" -> 0), incrementing IDs, and PropertyList
# passthrough in one shot.
check("commands reached the LC7001", commands_seen, [
    {"Service":"SetZoneProperties","ZID":4,"PropertyList":{"Power":True},"ID":1},
    {"Service":"ReportZoneProperties","ZID":0,"ID":2}])

print("\n" + ("ALL PASSED" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
