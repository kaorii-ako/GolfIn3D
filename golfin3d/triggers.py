"""Swing triggers: GC3 shots via the GSPro OpenConnect protocol, and an impact-sound mic trigger."""
import json
import socket
import threading
import time

import numpy as np


class OpenConnectServer(threading.Thread):
    """Pretends to be GSPro: any OpenConnect connector connects here and sends
    shot JSON. Calls on_shot(shot_dict, t_received) for real shots, on_status(text) otherwise.
    Point the connector at this PC and port (default 921). FSX Play has no such output."""

    def __init__(self, on_shot, on_status, port=921):
        super().__init__(daemon=True)
        self.on_shot, self.on_status, self.port = on_shot, on_status, port
        self._sock = None

    def run(self):
        try:
            self._sock = socket.create_server(("0.0.0.0", self.port), reuse_port=False)
        except OSError as e:
            self.on_status(f"GC3 port {self.port} unavailable: {e.strerror}")
            return
        self.on_status(f"Waiting for shots on :{self.port}")
        while True:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                return
            self.on_status(f"GC3 connected ({addr[0]})")
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        dec, buf = json.JSONDecoder(), ""
        with conn:
            while True:
                try:
                    data = conn.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data.decode("utf-8", "replace")
                # messages arrive back-to-back with no delimiter
                while buf.strip():
                    buf = buf.lstrip()
                    try:
                        msg, end = dec.raw_decode(buf)
                    except ValueError:
                        break
                    buf = buf[end:]
                    self._handle(conn, msg, time.monotonic())
        self.on_status(f"Waiting for shots on :{self.port}")

    def _handle(self, conn, msg, t):
        opts = msg.get("ShotDataOptions", {})
        try:
            conn.sendall(json.dumps({"Code": 200, "Message": "Shot received successfully"}).encode())
        except OSError:
            pass
        if opts.get("IsHeartBeat") or not opts.get("ContainsBallData"):
            return
        self.on_shot(parse_shot(msg), t)

    def stop(self):
        if self._sock:
            self._sock.close()


def parse_shot(msg):
    """OpenConnect message -> flat dict of the numbers we show."""
    b, c = msg.get("BallData", {}), msg.get("ClubData", {})
    pick = {
        "Ball speed": (b, "Speed", "mph"), "Launch": (b, "VLA", "°"), "Direction": (b, "HLA", "°"),
        "Spin": (b, "TotalSpin", "rpm"), "Spin axis": (b, "SpinAxis", "°"), "Carry": (b, "CarryDistance", "yd"),
        "Club speed": (c, "Speed", "mph"), "Path": (c, "Path", "°"), "Face": (c, "FaceToTarget", "°"),
        "AoA": (c, "AngleOfAttack", "°"),
    }
    return {k: [round(float(src[f]), 1), u] for k, (src, f, u) in pick.items() if src.get(f) is not None}


class AudioTrigger:
    """Fires on_impact(t_monotonic) when the mic peak crosses `threshold` (0..1).
    `offset_ms` is the calibration knob for mic/driver latency. `level` is the live peak for a meter."""

    def __init__(self, on_impact, threshold=0.5, offset_ms=0.0, device=None, refractory=2.0):
        self.on_impact, self.threshold, self.offset_ms = on_impact, threshold, offset_ms
        self.device, self.refractory = device, refractory
        self.level = 0.0
        self._last = 0.0
        self._stream = None
        self.error = None

    def start(self):
        try:
            import sounddevice as sd
            self._stream = sd.InputStream(device=self.device, channels=1, samplerate=48000,
                                          blocksize=256, callback=self._cb)
            self._stream.start()
        except Exception as e:  # no mic / PortAudio missing: run without audio trigger
            self.error = str(e)

    def _cb(self, data, frames, time_info, status):
        now = time.monotonic()
        peak = float(np.abs(data).max())
        self.level = max(peak, self.level * 0.9)
        if peak >= self.threshold and now - self._last > self.refractory:
            self._last = now
            # impact is where in the block the peak occurred, not the block end
            at = now - (frames - int(np.abs(data).argmax())) / 48000 - self.offset_ms / 1000
            self.on_impact(at)

    def stop(self):
        if self._stream:
            self._stream.close()


if __name__ == "__main__":
    shots = []
    srv = OpenConnectServer(lambda s, t: shots.append(s), lambda s: None, port=0)
    srv._sock = socket.create_server(("127.0.0.1", 0))
    srv.port = srv._sock.getsockname()[1]
    threading.Thread(target=lambda: srv._serve(srv._sock.accept()[0]), daemon=True).start()
    c = socket.create_connection(("127.0.0.1", srv.port))
    hb = {"ShotDataOptions": {"IsHeartBeat": True, "ContainsBallData": False}}
    shot = {"ShotDataOptions": {"ContainsBallData": True, "ContainsClubData": True},
            "BallData": {"Speed": 150.2, "VLA": 11.3, "TotalSpin": 2500, "HLA": -1.2},
            "ClubData": {"Speed": 104.0, "Path": 2.1}}
    c.sendall((json.dumps(hb) + json.dumps(shot)).encode())
    assert json.loads(c.recv(1024).decode().split("}")[0] + "}")["Code"] == 200
    time.sleep(0.2)
    assert shots == [{"Ball speed": [150.2, "mph"], "Launch": [11.3, "°"], "Direction": [-1.2, "°"],
                      "Spin": [2500.0, "rpm"], "Club speed": [104.0, "mph"], "Path": [2.1, "°"]}], shots
    print("triggers self-check ok")
