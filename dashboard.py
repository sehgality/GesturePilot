# dashboard.py
# Serves a live web dashboard at http://<jetson-ip>:8080 showing the camera
# feed with gesture overlays and an event log.
#
# Import and call start() from jetson_gestures.py to launch the server.
# Call push_event() to broadcast gesture events to the browser.
# Call update_dashboard_frame() each frame to push the latest JPEG.

import threading
import queue
import json
import time
import cv2
import jetson.utils
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DASHBOARD_PORT = 8080

_frame_lock  = threading.Lock()
_latest_jpeg = None
_event_queue = queue.Queue(maxsize=100)

# -------------------------------------------------------
# HELPERS
# -------------------------------------------------------
def get_keypoint(pose, name):
    idx = pose.FindKeypoint(name)
    return None if idx < 0 else pose.Keypoints[idx]

def push_event(event_dict):
    try:
        _event_queue.put_nowait(event_dict)
    except queue.Full:
        pass

def update_dashboard_frame(img, pose):
    global _latest_jpeg
    try:
        frame = cv2.cvtColor(jetson.utils.cudaToNumpy(img), cv2.COLOR_RGBA2BGR)
        if pose:
            for kp in pose.Keypoints:
                x, y = int(kp.x), int(kp.y)
                if x > 0 and y > 0:
                    cv2.circle(frame, (x, y), 6, (0, 255, 136), -1)
            for a_name, b_name in [
                ("left_wrist",    "left_shoulder"),
                ("right_wrist",   "right_shoulder"),
                ("left_shoulder", "right_shoulder"),
            ]:
                a = get_keypoint(pose, a_name)
                b = get_keypoint(pose, b_name)
                if a and b and a.x > 0 and b.x > 0:
                    cv2.line(frame, (int(a.x), int(a.y)), (int(b.x), int(b.y)), (0, 180, 100), 2)
        ok, jpeg_buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with _frame_lock:
                _latest_jpeg = jpeg_buf.tobytes()
    except Exception:
        pass

# -------------------------------------------------------
# HTML
# -------------------------------------------------------
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

# -------------------------------------------------------
# HTTP SERVER
# -------------------------------------------------------
class _DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path == '/':         self._serve_html()
        elif self.path == '/stream': self._serve_mjpeg()
        elif self.path == '/events': self._serve_sse()
        else:                        self.send_error(404)

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

# -------------------------------------------------------
# ENTRY POINT
# -------------------------------------------------------
def start(port=DASHBOARD_PORT):
    threading.Thread(
        target=lambda: ThreadingHTTPServer(('0.0.0.0', port), _DashboardHandler).serve_forever(),
        daemon=True
    ).start()
    print(f"Dashboard: http://<jetson-ip>:{port}")
