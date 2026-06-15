# laptop_listener.py
# Runs on the laptop and listens for gesture commands sent over UDP from the
# Jetson Nano. Automatically detects the OS and executes the appropriate action:
#   Mac:     uses osascript (no Accessibility permissions needed for volume;
#            Accessibility must be granted to Terminal for keypresses)
#   Windows: uses pyautogui for all keypresses and media keys

import socket
import pyautogui
import subprocess
import sys

# -------------------------------------------------------
# SETUP — auto-detect OS on startup
# -------------------------------------------------------
print("=== Gesture Remote Listener Setup ===")

LISTEN_IP   = "0.0.0.0"
LISTEN_PORT = 5005

os_choice = "mac" if sys.platform == "darwin" else "windows"
print(f"Detected OS: {os_choice}")
print(f"Listening on port {LISTEN_PORT}...\n")

# -------------------------------------------------------
# COMMAND MAP
# Mac:     osascript for all commands (arrow keys, space, volume)
# Windows: pyautogui for all commands
# -------------------------------------------------------
def osascript(cmd):
    subprocess.run(["osascript", "-e", cmd], capture_output=True)

if os_choice == "mac":
    COMMAND_MAP = {
        "RIGHT_ARROW":  lambda: osascript('tell application "System Events" to key code 124'),
        "LEFT_ARROW":   lambda: osascript('tell application "System Events" to key code 123'),
        "SPACEBAR":     lambda: osascript('tell application "System Events" to key code 49'),
        "VOLUME_UP":    lambda: osascript("set volume output volume (output volume of (get volume settings) + 10)"),
        "VOLUME_DOWN":  lambda: osascript("set volume output volume (output volume of (get volume settings) - 10)"),
    }
else:  # windows
    COMMAND_MAP = {
        "RIGHT_ARROW":  lambda: pyautogui.press("right"),
        "LEFT_ARROW":   lambda: pyautogui.press("left"),
        "SPACEBAR":     lambda: pyautogui.press("space"),
        "VOLUME_UP":    lambda: pyautogui.press("volumeup"),
        "VOLUME_DOWN":  lambda: pyautogui.press("volumedown"),
    }

# -------------------------------------------------------
# SOCKET SETUP
# Create a UDP socket and bind it to the listen address.
# UDP is used because it's lightweight and fast — we don't
# need a handshake or guaranteed delivery for gesture commands.
# -------------------------------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((LISTEN_IP, LISTEN_PORT))


# -------------------------------------------------------
# MAIN LOOP
# Continuously wait for incoming UDP packets from the Jetson.
# Each packet contains a command string. Decode it, look it
# up in the command map, and execute the corresponding keypress.
# -------------------------------------------------------
while True:
    # Wait for a packet (up to 1024 bytes)
    data, addr = sock.recvfrom(1024)

    # Decode the bytes into a string and strip any whitespace
    command = data.decode("utf-8").strip()

    print(f"Received command: '{command}' from {addr}")

    # Look up the command and execute it if recognized
    if command in COMMAND_MAP:
        COMMAND_MAP[command]()
    else:
        # Log any unrecognized commands for debugging
        print(f"  Unknown command: '{command}' — ignoring")
