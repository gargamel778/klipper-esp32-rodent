#!/usr/bin/env python3
"""Minimal client for klippy's unix-socket API, for stress testing.

Protocol: JSON objects separated by 0x03. gcode is sent as
{"id":N,"method":"gcode/script","params":{"script":"..."}}
"""
import json
import socket
import sys
import time

SOCK = "/tmp/klippy_uds"
SEP = b"\x03"


class Klippy:
    def __init__(self, path=SOCK, timeout=30.0):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(timeout)
        self.s.connect(path)
        self.buf = b""
        self.nid = 0

    def _send(self, obj):
        self.s.sendall(json.dumps(obj).encode() + SEP)

    def _recv_id(self, want, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            while SEP in self.buf:
                raw, self.buf = self.buf.split(SEP, 1)
                try:
                    m = json.loads(raw)
                except Exception:
                    continue
                if m.get("id") == want:
                    return m
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                break
            if not d:
                break
            self.buf += d
        return None

    def call(self, method, params=None, timeout=30.0):
        self.nid += 1
        self._send({"id": self.nid, "method": method, "params": params or {}})
        return self._recv_id(self.nid, timeout)

    def gcode(self, script, timeout=60.0):
        return self.call("gcode/script", {"script": script}, timeout)

    def status(self, objects):
        return self.call("objects/query", {"objects": objects})

    def close(self):
        self.s.close()


if __name__ == "__main__":
    k = Klippy()
    info = k.call("info")
    print("state:", (info or {}).get("result", {}).get("state"),
          "|", (info or {}).get("result", {}).get("state_message", "").strip()[:80])
    for cmd in sys.argv[1:]:
        r = k.gcode(cmd)
        res = (r or {}).get("result", (r or {}).get("error"))
        print(f"  {cmd!r} -> {res}")
    k.close()
