# laptop_listener.py
# This program runs on the laptop and listens for gesture commands sent over
# the local network from the Jetson Nano. When a command is received (e.g.
# "RIGHT_ARROW"), it executes the corresponding keypress using pyautogui,
# simulating a real keyboard input. This is the "receiver" side of the project.

import socket
import pyautogui

# -------------------------------------------------------
# CONFIGURATION
# Set the IP and port this laptop will listen on.
# Use 0.0.0.0 to accept messages from any device on the network.
# The port must match whatever port jetson_gestures.py sends to.
# -------------------------------------------------------
LISTEN_IP = "0.0.0.0"
LISTEN_PORT = 5005

# -------------------------------------------------------
# COMMAND MAP
# Maps the command strings sent by the Jetson to actual
# pyautogui keypresses. Add or change mappings here if needed.
# -------------------------------------------------------
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

print(f"Listening for commands on port {LISTEN_PORT}...")

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
