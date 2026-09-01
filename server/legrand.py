#!/usr/bin/env python3
"""
Legrand LC7001 <-> Hubitat bridge.

Python port of legrand.js. The HTTP contract with LegrandConnect.groovy is
unchanged:

    POST /init     {"hubIP": ..., "apiServerUrl": ...}  -> {"initReceived": true}
    GET  /status                                        -> {"initHubConnected": bool}
    POST /command  <Legrand command JSON>               -> {"message": "Command received"}

Outbound, LC7001 messages are POSTed to apiServerUrl. Payloads without a
Service key get Service="WebServerUpdate", which is what the hub app switches on.

Differences from the Node version, all deliberate:
  * The LC7001 socket is read into a buffer and only complete NUL-delimited
    messages are parsed. The Node version assumed one read == one message and
    crashed the process when TCP split a message across reads.
  * Reconnects use exponential backoff (1s -> 60s) instead of retrying instantly.
  * hubConnected=false is POSTed only on an actual transition, not on every
    failed retry.
  * /command reports honestly when the LC7001 is not connected.
"""

import json
import logging
import os
import queue
import signal
import socket
import sys
import threading

import requests
from flask import Flask, jsonify, request
from waitress import serve

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

SERVER_PORT = 21120           # port this bridge listens on, for the hub
LC7001_PORT = 2112            # port on the Legrand LC7001 controller
DELIMITER = b"\x00"           # LC7001 frames JSON messages with a NUL byte

CONNECT_TIMEOUT = 10.0        # seconds to wait for the LC7001 TCP handshake
READ_TIMEOUT = 30.0           # recv() timeout; a timeout is not an error
INITIAL_RETRY = 1.0           # first reconnect delay
MAX_RETRY = 60.0              # reconnect delay ceiling
RECV_BUFFER = 64 * 1024       # bytes per recv(); big enough for a ListZones burst
MAX_RX_BUFFER = 1024 * 1024   # bail out if we never see a delimiter
HUB_POST_TIMEOUT = 10.0       # timeout when POSTing to the Hubitat
RESTART_CONNECT_DELAY = 2.0   # matches the Node version's startup delay

CONFIG_PATH = os.environ.get("LEGRAND_CONFIG", "./config.json")

# The Service the hub app switches on for anything that is not raw LC7001 output.
DEFAULT_SERVICE = "WebServerUpdate"

# LC7001 chatter that the hub app has no case for. Not forwarded.
IGNORED_SERVICES = frozenset({
    "ping",
    "EliotErrors",
    "BroadcastDiagnostics",
    "BroadcastMemory",
    "SystemPropertiesChanged",
})

MAX_SAFE_INTEGER = 2 ** 53 - 1   # matches the Node commandID wrap point

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("legrand")


# ----------------------------------------------------------------------------
# Outbound notifications to the Hubitat
# ----------------------------------------------------------------------------

class HubNotifier:
    """POSTs messages to the Hubitat from a single worker thread.

    A dedicated thread keeps the socket reader from ever blocking on HTTP, and
    a single consumer preserves ordering, which matters: the hub app expects
    ListZones before the ReportZoneProperties replies it triggers.
    """

    def __init__(self):
        self._queue = queue.Queue(maxsize=1000)
        self._session = requests.Session()
        self.url = ""
        self._thread = threading.Thread(target=self._run, name="hub-notifier", daemon=True)
        self._thread.start()

    def send(self, payload):
        """Queue a payload for delivery. Never raises, never blocks."""
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            log.warning("Hub notify queue full; dropping %s",
                        payload.get("Service", DEFAULT_SERVICE))

    def send_blocking(self, payload):
        """Deliver immediately on the calling thread. Used for shutdown."""
        self._post(payload)

    def _run(self):
        while True:
            payload = self._queue.get()
            try:
                self._post(payload)
            except Exception:
                log.exception("Unexpected error posting to hub")

    def _post(self, payload):
        # The single point where a payload becomes an HTTP body, so the default
        # Service is applied here rather than at each sender.
        if "Service" not in payload:
            payload = dict(payload, Service=DEFAULT_SERVICE)

        url = self.url
        if not url:
            log.warning("No apiServerUrl configured; dropping %s", payload["Service"])
            return
        try:
            resp = self._session.post(url, json=payload, timeout=HUB_POST_TIMEOUT)
            if resp.status_code >= 400:
                log.warning("Hub returned HTTP %s for %s", resp.status_code, payload["Service"])
        except requests.RequestException as exc:
            log.error("Failed to POST to hub: %s", exc)


# ----------------------------------------------------------------------------
# LC7001 TCP client
# ----------------------------------------------------------------------------

class LC7001Client:
    """Maintains one TCP connection to the LC7001 and reframes its output."""

    def __init__(self, notifier):
        self._notifier = notifier
        self._sock = None
        self._sock_lock = threading.Lock()   # guards _sock for sendall()
        self._rx = bytearray()
        self._scan_from = 0                  # how much of _rx we already searched

        self.host = ""

        self._wake = threading.Event()       # nudges the loop to reconnect now
        self._stopping = threading.Event()
        self._initial_delay = 0.0

        self._command_id = 0
        self._id_lock = threading.Lock()

        self._thread = threading.Thread(target=self._run, name="lc7001", daemon=True)

    # -- public API ----------------------------------------------------------

    def start(self, delay=0.0):
        """Start the connect loop, optionally holding off for `delay` seconds."""
        self._initial_delay = delay
        self._thread.start()

    @property
    def connected(self):
        return self._sock is not None

    def send_command(self, command):
        """Stamp the LC7001 protocol fields onto a command and send it.

        Returns False if not connected. The ID and ZID rules live here rather
        than in the HTTP layer so that every sender produces valid frames.
        """
        command["ID"] = self._next_command_id()
        if "ZID" in command:
            command["ZID"] = self._coerce_zid(command["ZID"])

        body = json.dumps(command)
        log.info("Sending command to Legrand hub: %s", body)

        payload = body.encode("utf-8") + DELIMITER
        with self._sock_lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall(payload)
                return True
            except OSError as exc:
                log.error("send failed: %s", exc)
                self._drop_socket_locked()
                return False

    def reconnect(self):
        """Drop the current connection and reconnect promptly."""
        with self._sock_lock:
            self._drop_socket_locked()
        self._wake.set()

    def stop(self):
        self._stopping.set()
        with self._sock_lock:
            self._drop_socket_locked()
        self._wake.set()

    # -- internals -----------------------------------------------------------

    def _next_command_id(self):
        with self._id_lock:
            self._command_id = 1 if self._command_id >= MAX_SAFE_INTEGER else self._command_id + 1
            return self._command_id

    @staticmethod
    def _coerce_zid(zid):
        """Match the Node version's ZID coercion exactly."""
        if zid == "null":
            return 0
        try:
            return int(zid)
        except (TypeError, ValueError):
            log.warning("Could not parse ZID %r; leaving as-is", zid)
            return zid

    def _reset_rx(self):
        self._rx = bytearray()
        self._scan_from = 0

    def _drop_socket_locked(self):
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _run(self):
        delay = INITIAL_RETRY

        # Hold until the startup delay elapses, or until a /init reconnect
        # releases us early.
        self._wake.wait(timeout=self._initial_delay)
        self._wake.clear()

        while not self._stopping.is_set():
            host = self.host
            if not host:
                log.info("No Legrand hub IP configured yet; waiting for /init")
                self._wake.wait()
                self._wake.clear()
                continue

            if self._connect(host):
                delay = INITIAL_RETRY          # reset backoff after a good connect
                self._read_until_closed()      # blocks until the socket dies

            if self._stopping.is_set():
                break

            log.info("Re-establishing connection to Legrand hub in %.0fs", delay)
            if self._wake.wait(timeout=delay):
                # reconnect() was called: retry now and reset the backoff
                self._wake.clear()
                delay = INITIAL_RETRY
            else:
                delay = min(delay * 2, MAX_RETRY)

    def _connect(self, host):
        log.info("Connecting to Legrand hub at %s:%d", host, LC7001_PORT)
        try:
            sock = socket.create_connection((host, LC7001_PORT), timeout=CONNECT_TIMEOUT)
        except (OSError, socket.timeout) as exc:
            log.error("Connect attempt failed: %s", exc)
            return False

        sock.settimeout(READ_TIMEOUT)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        with self._sock_lock:
            self._sock = sock
            self._reset_rx()

        log.info("Connected to hub successfully")
        self._notifier.send({"hubConnected": True})
        return True

    def _read_until_closed(self):
        last_error = ""

        while not self._stopping.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                chunk = sock.recv(RECV_BUFFER)
            except socket.timeout:
                continue                      # idle period, not an error
            except OSError as exc:
                last_error = str(exc)
                log.error("Socket error: %s", exc)
                break

            if not chunk:
                log.info("Legrand hub closed the connection")
                break

            self._consume(chunk)

        with self._sock_lock:
            self._drop_socket_locked()

        log.info("Socket closed%s", (" after error: " + last_error) if last_error else " cleanly")

        # We only reach this method after a successful connect, so this is always
        # a real transition. The Node version fired it on every failed retry too,
        # which POSTed to the hub every ~10s for the whole outage.
        self._notifier.send({"hubConnected": False, "error": last_error})

    def _consume(self, chunk):
        """Accumulate bytes and dispatch only complete messages.

        _rx is a bytearray and _scan_from records how far we already searched,
        so a message split across many reads is neither recopied nor rescanned
        once per read.
        """
        self._rx += chunk

        while True:
            end = self._rx.find(DELIMITER, self._scan_from)
            if end < 0:
                self._scan_from = len(self._rx)
                break

            raw = bytes(self._rx[:end])
            del self._rx[:end + 1]
            self._scan_from = 0
            if not raw:
                continue

            try:
                text = raw.decode("utf-8")
                message = json.loads(text)
            except (ValueError, UnicodeDecodeError) as exc:
                log.warning("Skipping unparseable message (%d bytes): %s", len(raw), exc)
                continue
            self._dispatch(message, text)

        if len(self._rx) > MAX_RX_BUFFER:
            log.error("rxBuffer exceeded %d bytes with no delimiter; discarding", MAX_RX_BUFFER)
            self._reset_rx()

    def _dispatch(self, message, text):
        if not isinstance(message, dict):
            log.warning("Ignoring non-object message: %r", text[:120])
            return
        if message.get("Service") in IGNORED_SERVICES or message.get("DebugStr"):
            return
        log.info("Notifying hub with POST update: %s", text)
        self._notifier.send(message)


# ----------------------------------------------------------------------------
# Config persistence
# ----------------------------------------------------------------------------

def load_config():
    try:
        with open(CONFIG_PATH) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.info("Config file does not exist")
        return None
    except (ValueError, OSError) as exc:
        log.error("There has been an error reading your config: %s", exc)
        return None
    log.info("Read config data")
    return data


def save_config(hub_ip, api_server_url):
    try:
        with open(CONFIG_PATH, "w") as fh:
            json.dump({"hubIP": hub_ip, "apiServerUrl": api_server_url}, fh)
        log.info("Configuration file saved successfully.")
    except OSError as exc:
        log.error("Error saving your configuration data: %s", exc)


# ----------------------------------------------------------------------------
# HTTP server
# ----------------------------------------------------------------------------

notifier = HubNotifier()
lc7001 = LC7001Client(notifier)

app = Flask(__name__)


def apply_config(hub_ip, api_server_url):
    """Point the bridge at a hub. Used by /init and by startup."""
    notifier.url = api_server_url
    lc7001.host = hub_ip


@app.post("/init")
def http_init():
    body = request.get_json(force=True, silent=True) or {}
    hub_ip = body.get("hubIP", "")
    api_server_url = body.get("apiServerUrl", "")

    log.info("Hub IP: %s", hub_ip)
    log.info("API Server URL: %s", api_server_url)

    apply_config(hub_ip, api_server_url)
    save_config(hub_ip, api_server_url)

    lc7001.reconnect()          # always reconnect, matching the Node behaviour

    return jsonify({"initReceived": True})


@app.get("/status")
def http_status():
    return jsonify({"initHubConnected": lc7001.connected})


@app.post("/command")
def http_command():
    command = request.get_json(force=True, silent=True)
    if not isinstance(command, dict):
        return jsonify({"message": "Malformed command", "sent": False}), 400

    if not lc7001.send_command(command):
        log.warning("Rejecting /command: not connected to Legrand hub")
        return jsonify({"message": "Not connected to Legrand hub", "sent": False}), 503

    return jsonify({"message": "Command received", "sent": True})


# ----------------------------------------------------------------------------
# Shutdown
# ----------------------------------------------------------------------------

def send_hail_mary(signum, _frame):
    log.info("Sending Hail Mary (signal %s)", signum)
    try:
        notifier.send_blocking({
            "hubConnected": False,
            "error": "Legrand web server is about to die.",
        })
        log.info("Sent Hail Mary to hub.")
    except Exception as exc:
        log.error("Hail Mary failed: %s", exc)
    lc7001.stop()
    os._exit(0)


def main():
    signal.signal(signal.SIGTERM, send_hail_mary)
    signal.signal(signal.SIGINT, send_hail_mary)

    config = load_config()
    if config:
        # We restarted, so by definition we are not connected to the LC7001.
        apply_config(config.get("hubIP", ""), config.get("apiServerUrl", ""))
        notifier.send({"hubConnected": False, "error": "Web server restarted"})

    # Give the hub a moment to send its own disconnect/reconnect message.
    lc7001.start(delay=RESTART_CONNECT_DELAY if config else 0)

    log.info("Server listening on port %d", SERVER_PORT)
    serve(app, host="0.0.0.0", port=SERVER_PORT, threads=8, _quiet=True)


if __name__ == "__main__":
    main()
