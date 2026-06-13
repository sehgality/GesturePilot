# jetson_gestures.py
# Captures video from a connected camera, runs PoseNet to detect body keypoints
# in real time, and translates physical gestures into commands sent over UDP
# to the laptop running laptop_listener.py.
#
# Gestures:
#   Swipe left or right (either hand, Y must stay consistent) → LEFT_ARROW / RIGHT_ARROW
#   Both hands open toward camera at shoulder level (hold)    → SPACEBAR
#   Right hand raised straight up (X must stay consistent)   → VOLUME_UP
#   Left hand raised straight up  (X must stay consistent)   → VOLUME_DOWN
#
# DASHBOARD: Open http://<jetson-ip>:8080 in your browser to see the live camera
# feed with gesture overlays.

import jetson.inference
import jetson.utils
import socket
import time
import threading
import queue
import json
import cv2
import numpy as np
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

# -------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------
LAPTOP_IP      = "YOUR_LAPTOP_IP"   # <-- replace this before going live
LAPTOP_PORT    = 5005
DASHBOARD_PORT = 8080

# -------------------------------------------------------
# TUNING THRESHOLDS
# All proportional to shoulder width so they scale with
# distance from the camera automatically.
# -------------------------------------------------------

# SWIPE — wrist must move this fraction of shoulder width horizontally
# with stable Y, using either hand
SWIPE_FRACTION    = 0.5   # increase = needs bigger movement (less sensitive)
SWIPE_Y_TOLERANCE = 0.2   # max Y drift as fraction of shoulder width during a swipe
SWIPE_BUFFER_SIZE = 15    # frames tracked in rolling window
SWIPE_COOLDOWN    = 0.6   # seconds before another swipe can fire

# VOLUME — wrist must be above its shoulder by this fraction of shoulder width
# No X check needed: just straight up relative to the shoulder
RAISE_FRACTION      = 0.5   # increase = hand needs to go higher
VOLUME_REPEAT_DELAY = 0.4   # seconds between repeated volume keypresses while held

# PAUSE — both wrists within this fraction of shoulder width
# of their own shoulder position in both X and Y
PAUSE_FRACTION    = 0.3   # smaller = stricter (hands must be closer to shoulders)
PAUSE_HOLD_FRAMES = 20    # frames to hold before firing

# -------------------------------------------------------
# DEBUG MODE
# True  = prints [DEBUG] command names, no UDP sent
# False = sends real commands to laptop
# -------------------------------------------------------
DEBUG_MODE = True

# -------------------------------------------------------
# STATE
# -------------------------------------------------------
# Swipe buffers store (x, y) tuples so we can check Y consistency
right_wrist_buffer = []
left_wrist_buffer  = []
pause_counter      = 0
last_swipe_time    = 0
last_volume_time   = 0

# -------------------------------------------------------
# NETWORK
# -------------------------------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_command(command):
    if DEBUG_MODE:
        print(f"[DEBUG] {command}")
    else:
        sock.sendto(command.encode("utf-8"), (LAPTOP_IP, LAPTOP_PORT))
        print(f"[SENT] {command}")

# -------------------------------------------------------
# DASHBOARD
# -------------------------------------------------------
_frame_lock  = threading.Lock()
_latest_jpeg = None
_event_queue = queue.Queue(maxsize=100)

def _push_event(event_dict):
    try:
        _event_queue.put_nowait(event_dict)
    except queue.Full:
        pass

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Gesture Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0d0d0d; color: #eee;
    font-family: 'Courier New', monospace;
    display: flex; flex-direction: column; align-items: center;
    min-height: 100vh; padding: 24px 16px;
  }
  h1 { font-size: 18px; color: #555; margin-bottom: 16px; letter-spacing: 3px; text-transform: uppercase; }
  #stream-wrap {
    position: relative; width: 640px; max-width: 100%;
    border-radius: 10px; overflow: hidden;
    border: 2px solid #222; box-shadow: 0 0 40px rgba(0,0,0,0.8);
  }
  #stream { display: block; width: 100%; }
  #gesture-overlay {
    position: absolute; inset: 0;
    display: flex; align-items: center; justify-content: center;
    pointer-events: none;
  }
  #gesture-label {
    font-size: 64px; font-weight: 900; color: #00ff88;
    opacity: 0; transform: scale(0.8);
    transition: opacity 0.08s ease, transform 0.08s ease;
    white-space: nowrap;
  }
  #gesture-label.visible { opacity: 1; transform: scale(1); }
  #log-wrap { width: 640px; max-width: 100%; margin-top: 16px; }
  #log-title { font-size: 11px; color: #444; letter-spacing: 2px; margin-bottom: 6px; }
  #log {
    height: 160px; overflow-y: auto;
    background: #111; border: 1px solid #222;
    border-radius: 6px; padding: 10px; font-size: 12px;
  }
  .log-row { display: flex; gap: 10px; padding: 2px 0; border-bottom: 1px solid #1a1a1a; }
  .log-time { color: #444; flex-shrink: 0; }
  .log-gesture { font-weight: bold; }
</style>
</head>
<body>
<h1>Gesture Dashboard</h1>
<div id="stream-wrap">
  <img id="stream" src="/stream" alt="Camera feed">
  <div id="gesture-overlay"><div id="gesture-label"></div></div>
</div>
<div id="log-wrap">
  <div id="log-title">EVENT LOG</div>
  <div id="log"></div>
</div>
<script>
const gestureLabel = document.getElementById('gesture-label');
const log = document.getElementById('log');
let fadeTimer = null;
const GESTURE_CONFIG = {
  SWIPE_RIGHT:  { label: '→  Swipe Right',  color: '#00aaff' },
  SWIPE_LEFT:   { label: '←  Swipe Left',   color: '#ff6600' },
  PAUSE:        { label: '✋  Pause',         color: '#ffdd00' },
  VOLUME_UP:    { label: '▲  Volume Up',     color: '#00ff88' },
  VOLUME_DOWN:  { label: '▼  Volume Down',   color: '#ff4466' },
};
function showGesture(key) {
  const cfg = GESTURE_CONFIG[key] || { label: key, color: '#ffffff' };
  gestureLabel.textContent = cfg.label;
  gestureLabel.style.color = cfg.color;
  gestureLabel.style.textShadow = `0 0 30px ${cfg.color}, 0 0 60px ${cfg.color}`;
  gestureLabel.classList.add('visible');
  if (fadeTimer) clearTimeout(fadeTimer);
  fadeTimer = setTimeout(() => gestureLabel.classList.remove('visible'), 1200);
  const row = document.createElement('div');
  row.className = 'log-row';
  const ts = new Date().toLocaleTimeString('en-US', { hour12: false });
  row.innerHTML = `<span class="log-time">${ts}</span><span class="log-gesture" style="color:${cfg.color}">${cfg.label}</span>`;
  log.prepend(row);
}
const es = new EventSource('/events');
es.onmessage = (e) => {
  try { const d = JSON.parse(e.data); if (d.type === 'gesture') showGesture(d.gesture); }
  catch (_) {}
};
</script>
</body>
</html>"""


class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path == '/':            self._serve_html()
        elif self.path == '/stream':    self._serve_mjpeg()
        elif self.path == '/events':    self._serve_sse()
        else:                           self.send_error(404)

    def _serve_html(self):
        body = DASHBOARD_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_mjpeg(self):
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        try:
            while True:
                with _frame_lock:
                    jpeg = _latest_jpeg
                if jpeg:
                    header = (b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '
                              + str(len(jpeg)).encode() + b'\r\n\r\n')
                    self.wfile.write(header + jpeg + b'\r\n')
                    self.wfile.flush()
                time.sleep(0.033)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_sse(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        try:
            while True:
                try:
                    event = _event_queue.get(timeout=5.0)
                    self.wfile.write(f'data: {json.dumps(event)}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b': keepalive\n\n')
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


threading.Thread(target=lambda: ThreadingHTTPServer(('0.0.0.0', DASHBOARD_PORT), _DashboardHandler).serve_forever(), daemon=True).start()

# -------------------------------------------------------
# POSENET + CAMERA
# -------------------------------------------------------
net    = jetson.inference.poseNet("resnet18-body", threshold=0.15)
camera = jetson.utils.videoSource("/dev/video0")

# -------------------------------------------------------
# HELPERS
# -------------------------------------------------------
def get_keypoint(pose, name):
    idx = pose.FindKeypoint(name)
    return None if idx < 0 else pose.Keypoints[idx]

def detect_swipe(buffer, x, y, sw):
    """
    Tracks (x, y) across frames. Returns 'RIGHT', 'LEFT', or None.
    Movement threshold and Y tolerance are proportional to shoulder width (sw).
    """
    buffer.append((x, y))
    if len(buffer) > SWIPE_BUFFER_SIZE:
        buffer.pop(0)
    if len(buffer) < SWIPE_BUFFER_SIZE:
        return None

    x_delta     = buffer[-1][0] - buffer[0][0]
    y_values    = [p[1] for p in buffer]
    y_variation = max(y_values) - min(y_values)

    # Reject if Y moved too much relative to shoulder width — it's a raise not a swipe
    if y_variation > SWIPE_Y_TOLERANCE * sw:
        return None

    if x_delta > SWIPE_FRACTION * sw:
        buffer.clear()
        return "RIGHT"
    elif x_delta < -SWIPE_FRACTION * sw:
        buffer.clear()
        return "LEFT"
    return None

def is_volume_gesture(wrist, shoulder, sw):
    """
    Wrist raised above its shoulder by at least RAISE_FRACTION of shoulder width.
    Simple Y-only check — no X restriction needed.
    """
    if not wrist:
        return False
    return (shoulder.y - wrist.y) > RAISE_FRACTION * sw

def is_pause_gesture(lw, rw, ls, rs, sw):
    """
    Both wrists at their own shoulder position in X and Y —
    hands resting in front of shoulders.
    """
    if not (lw and rw):
        return False
    margin = PAUSE_FRACTION * sw
    left_ok  = abs(lw.x - ls.x) < margin and abs(lw.y - ls.y) < margin
    right_ok = abs(rw.x - rs.x) < margin and abs(rw.y - rs.y) < margin
    return left_ok and right_ok

def _update_dashboard_frame(img, pose):
    try:
        frame = cv2.cvtColor(jetson.utils.cudaToNumpy(img), cv2.COLOR_RGBA2BGR)
        if pose:
            for kp in pose.Keypoints:
                x, y = int(kp.x), int(kp.y)
                if x > 0 and y > 0:
                    cv2.circle(frame, (x, y), 6, (0, 255, 136), -1)
            for a_name, b_name in [("left_wrist","left_shoulder"),("right_wrist","right_shoulder"),("left_shoulder","right_shoulder")]:
                a, b = get_keypoint(pose, a_name), get_keypoint(pose, b_name)
                if a and b and a.x > 0 and b.x > 0:
                    cv2.line(frame, (int(a.x), int(a.y)), (int(b.x), int(b.y)), (0, 180, 100), 2)
        ok, jpeg_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with _frame_lock:
                global _latest_jpeg
                _latest_jpeg = jpeg_buf.tobytes()
    except Exception:
        pass

# -------------------------------------------------------
# MAIN LOOP
# -------------------------------------------------------
print(f"Starting. DEBUG_MODE = {DEBUG_MODE}")
print(f"Dashboard: http://<jetson-ip>:{DASHBOARD_PORT}")

while True:
    img = camera.Capture()
    if img is None:
        continue

    poses = net.Process(img)
    pose  = poses[0] if poses else None
    _update_dashboard_frame(img, pose)

    if pose is None:
        continue

    lw  = get_keypoint(pose, "left_wrist")
    rw  = get_keypoint(pose, "right_wrist")
    ls  = get_keypoint(pose, "left_shoulder")
    rs  = get_keypoint(pose, "right_shoulder")

    if ls is None or rs is None:
        continue

    # Shoulder width is the reference unit for all gesture thresholds
    sw = abs(rs.x - ls.x)
    if sw < 10:
        continue  # shoulders too close together to be reliable

    now = time.time()

    # ---------------------------------------------------
    # PAUSE — both wrists at their shoulder position (X and Y)
    # Checked first so volume doesn't fire at the same time
    # ---------------------------------------------------
    if is_pause_gesture(lw, rw, ls, rs, sw):
        pause_counter += 1
        if pause_counter == PAUSE_HOLD_FRAMES:
            send_command("SPACEBAR")
            _push_event({'type': 'gesture', 'gesture': 'PAUSE'})
    else:
        pause_counter = 0

        # ---------------------------------------------------
        # VOLUME — one hand raised above its shoulder (Y only)
        # ---------------------------------------------------
        if is_volume_gesture(rw, rs, sw):
            if now - last_volume_time > VOLUME_REPEAT_DELAY:
                send_command("VOLUME_UP")
                _push_event({'type': 'gesture', 'gesture': 'VOLUME_UP'})
                last_volume_time = now
        elif is_volume_gesture(lw, ls, sw):
            if now - last_volume_time > VOLUME_REPEAT_DELAY:
                send_command("VOLUME_DOWN")
                _push_event({'type': 'gesture', 'gesture': 'VOLUME_DOWN'})
                last_volume_time = now

    # ---------------------------------------------------
    # SWIPE — either hand moves horizontally with stable Y
    # Threshold proportional to shoulder width
    # ---------------------------------------------------
    if now - last_swipe_time > SWIPE_COOLDOWN:
        direction = None

        if rw:
            direction = detect_swipe(right_wrist_buffer, rw.x, rw.y, sw)
        if direction is None and lw:
            direction = detect_swipe(left_wrist_buffer, lw.x, lw.y, sw)

        if direction == "RIGHT":
            send_command("RIGHT_ARROW")
            _push_event({'type': 'gesture', 'gesture': 'SWIPE_RIGHT'})
            last_swipe_time = now
            left_wrist_buffer.clear()
        elif direction == "LEFT":
            send_command("LEFT_ARROW")
            _push_event({'type': 'gesture', 'gesture': 'SWIPE_LEFT'})
            last_swipe_time = now
            right_wrist_buffer.clear()
