# jetson_gestures.py
# Captures video from a connected camera, runs PoseNet to detect body keypoints
# in real time, and translates physical gestures into commands sent over UDP
# to the laptop running laptop_listener.py.
#
# Gestures:
#   Right hand swipe right                                    → RIGHT_ARROW
#   Left hand swipe left                                      → LEFT_ARROW
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
import argparse

import dashboard
from dashboard import get_keypoint, push_event, update_dashboard_frame

# -------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------
parser = argparse.ArgumentParser(description="Gesture controller for laptop")
parser.add_argument("--ip", required=True, help="Laptop IP address (e.g. 192.168.1.5)")
args = parser.parse_args()

LAPTOP_IP   = args.ip
LAPTOP_PORT = 5005

print(f"Targeting laptop at {LAPTOP_IP}:{LAPTOP_PORT}")

# -------------------------------------------------------
# TUNING THRESHOLDS
# All proportional to shoulder width so they scale with
# distance from the camera automatically.
# -------------------------------------------------------

# SWIPE — right hand triggers RIGHT, left hand triggers LEFT
# Wrist must move this fraction of shoulder width horizontally with stable Y
SWIPE_FRACTION    = 0.325  # fraction of shoulder width wrist must move to count as swipe
SWIPE_Y_TOLERANCE = 0.2   # max Y drift as fraction of shoulder width during a swipe
SWIPE_BUFFER_SIZE = 4     # smaller = detects faster hand movements
SWIPE_COOLDOWN    = 0.2   # seconds before another swipe can fire

# VOLUME — wrist must be above its shoulder by this fraction of shoulder width
# No X check needed: just straight up relative to the shoulder
RAISE_FRACTION      = 0.2   # increase = hand needs to go higher
VOLUME_HOLD_DELAY   = 1.0   # seconds a single wrist must be raised alone before volume fires
VOLUME_REPEAT_DELAY = 0.4   # seconds between repeated volume keypresses while held

# PAUSE — both wrists raised in front of their own shoulders simultaneously
# Checked before volume so two hands up → pause, not volume
PAUSE_RAISE_FRACTION = 0.4   # how far above the shoulder wrist must be (fraction of shoulder width)
PAUSE_HOLD_FRAMES    = 10    # frames both hands must be held up before firing

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
right_raise_start  = None   # timestamp when right wrist first went above shoulder alone
left_raise_start   = None   # timestamp when left wrist first went above shoulder alone

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
# GESTURE HELPERS
# -------------------------------------------------------
def detect_swipe(buffer, x, y, sw, shoulder_mid_x):
    """
    Tracks (x, y) across frames. Returns 'RIGHT', 'LEFT', or None.
    Movement threshold and Y tolerance are proportional to shoulder width (sw).
    Requires the wrist to cross the shoulder midpoint (- to + or + to -)
    so that a windup at the start of a swipe doesn't fire the wrong direction.
    """
    buffer.append((x, y))
    if len(buffer) > SWIPE_BUFFER_SIZE:
        buffer.pop(0)
    if len(buffer) < SWIPE_BUFFER_SIZE:
        return None

    x_start     = buffer[0][0]
    x_end       = buffer[-1][0]
    x_delta     = x_end - x_start
    y_values    = [p[1] for p in buffer]
    y_variation = max(y_values) - min(y_values)

    # Reject if Y moved too much relative to shoulder width — it's a raise not a swipe
    if y_variation > SWIPE_Y_TOLERANCE * sw:
        return None

    if x_delta > SWIPE_FRACTION * sw:
        # Must have started left of midpoint and ended right of it
        if x_start < shoulder_mid_x and x_end > shoulder_mid_x:
            buffer.clear()
            return "RIGHT"
    elif x_delta < -SWIPE_FRACTION * sw:
        # Must have started right of midpoint and ended left of it
        if x_start > shoulder_mid_x and x_end < shoulder_mid_x:
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
    Both wrists raised above their respective shoulders in Y.
    No X check needed — requiring BOTH hands up is enough to distinguish
    from volume (which only requires one hand). Pause is checked first in
    the main loop so two hands up always hits pause before volume fires.
    """
    if lw is None or rw is None:
        return False
    left_raised  = (ls.y - lw.y) > PAUSE_RAISE_FRACTION * sw
    right_raised = (rs.y - rw.y) > PAUSE_RAISE_FRACTION * sw
    return left_raised and right_raised

# -------------------------------------------------------
# STARTUP
# -------------------------------------------------------
dashboard.start()

net    = jetson.inference.poseNet("resnet18-body", threshold=0.15)
camera = jetson.utils.videoSource("/dev/video0")

print(f"Starting. DEBUG_MODE = {DEBUG_MODE}")

# -------------------------------------------------------
# MAIN LOOP
# -------------------------------------------------------
while True:
    img = camera.Capture()
    if img is None:
        continue

    poses = net.Process(img)
    pose  = poses[0] if poses else None
    update_dashboard_frame(img, pose)

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
    # PAUSE — both wrists raised above their own shoulders in Y
    # Checked first so volume doesn't fire at the same time
    # ---------------------------------------------------
    shoulder_mid_x = (ls.x + rs.x) / 2

    if is_pause_gesture(lw, rw, ls, rs, sw):
        pause_counter += 1
        right_raise_start = None
        left_raise_start  = None
        if pause_counter == PAUSE_HOLD_FRAMES:
            send_command("SPACEBAR")
            push_event({'type': 'gesture', 'gesture': 'PAUSE'})
    else:
        pause_counter = 0

        # ---------------------------------------------------
        # VOLUME — one hand raised above its shoulder (Y only)
        # ---------------------------------------------------
        if is_volume_gesture(rw, rs, sw) and not is_volume_gesture(lw, ls, sw):
            if right_raise_start is None:
                right_raise_start = now
            elif now - right_raise_start >= VOLUME_HOLD_DELAY:
                if now - last_volume_time > VOLUME_REPEAT_DELAY:
                    send_command("VOLUME_UP")
                    push_event({'type': 'gesture', 'gesture': 'VOLUME_UP'})
                    last_volume_time = now
        else:
            right_raise_start = None

        if is_volume_gesture(lw, ls, sw) and not is_volume_gesture(rw, rs, sw):
            if left_raise_start is None:
                left_raise_start = now
            elif now - left_raise_start >= VOLUME_HOLD_DELAY:
                if now - last_volume_time > VOLUME_REPEAT_DELAY:
                    send_command("VOLUME_DOWN")
                    push_event({'type': 'gesture', 'gesture': 'VOLUME_DOWN'})
                    last_volume_time = now
        else:
            left_raise_start = None

    # ---------------------------------------------------
    # SWIPE — right hand → RIGHT only, left hand → LEFT only
    # Threshold proportional to shoulder width
    # ---------------------------------------------------
    if now - last_swipe_time > SWIPE_COOLDOWN:
        direction = None

        if rw:
            d = detect_swipe(right_wrist_buffer, rw.x, rw.y, sw, shoulder_mid_x)
            if d == "RIGHT":
                direction = "RIGHT"
        if direction is None and lw:
            d = detect_swipe(left_wrist_buffer, lw.x, lw.y, sw, shoulder_mid_x)
            if d == "LEFT":
                direction = "LEFT"

        if direction == "RIGHT":
            send_command("RIGHT_ARROW")
            push_event({'type': 'gesture', 'gesture': 'SWIPE_RIGHT'})
            last_swipe_time = now
            left_wrist_buffer.clear()
        elif direction == "LEFT":
            send_command("LEFT_ARROW")
            push_event({'type': 'gesture', 'gesture': 'SWIPE_LEFT'})
            last_swipe_time = now
            right_wrist_buffer.clear()
