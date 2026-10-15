"""Exercise the built container against a synthetic peer; no real controller access.

Run on Linux from repository root: .venv/bin/python scripts/docker-smoke.py
Requires local image mtunifi:dev. Uses host networking solely for a loopback fixture.
"""

import json
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode

KEY = bytes.fromhex("1234567890abcdef1234567890abcdef")
seen = []


class Peer(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        key = BOOTSTRAP_KEY if not seen else KEY
        packet = decode(self.rfile.read(int(self.headers["Content-Length"])), key)
        seen.append(packet)
        body = (
            {"_type": "setparam", "mgmt_cfg": f"authkey={KEY.hex()}\nuse_aes_gcm=1"}
            if len(seen) == 1
            else {"_type": "noop", "interval": 10}
        )
        response = encode(body, packet.mac, key, mode=packet.mode)
        self.send_response(200)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout


def main():
    volume = "mtunifi-smoke-" + uuid.uuid4().hex
    server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    docker("volume", "create", volume)
    try:
        arguments = [
            "run",
            "--rm",
            "--network",
            "host",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-v",
            f"{volume}:/state",
            "-e",
            f"MTUNIFI_INFORM_URL=http://127.0.0.1:{server.server_port}/inform",
            "mtunifi:dev",
        ]
        docker(*arguments, "run", "--once")
        docker(*arguments, "run", "--once")
        public = json.loads(docker(*arguments, "diagnose"))
        assert public["phase"] == "exchanging"
        assert public["has_private_key"] is True
        assert "authkey" not in public
        docker(*arguments, "health")
        assert len(seen) == 2 and seen[1].mode == "gcm"
        assert seen[0].mac == seen[1].mac
        assert len(seen[1].body["port_table"]) == 8
        uid = docker("run", "--rm", "--entrypoint", "id", "mtunifi:dev", "-u").strip()
        assert uid == "10001"
        print("PASS: container HTTP exchange, key/identity persistence, GCM, health, UID 10001")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        docker("volume", "rm", volume)  # Only the unique volume created by this script.


if __name__ == "__main__":
    main()
