# Computer Controller 🤙

Control your laptop remotely using body gestures, detected in real time by a **Jetson Nano** running PoseNet. Gesture events are sent over UDP to your laptop, which executes the corresponding keyboard/media actions — no physical remote needed.

---

## How It Works

```
[Camera] → [Jetson Nano] → (UDP over WiFi) → [Laptop]
             detects gestures                executes keypresses
```

- `jetson_gestures.py` runs on the **Jetson Nano** — reads the camera, detects body keypoints with PoseNet, and sends commands over UDP
- `dashboard.py` runs alongside it — serves a live browser dashboard at `http://<jetson-ip>:8080` showing the camera feed and gesture log
- `laptop_listener.py` runs on the **laptop** — receives UDP commands and executes the corresponding key/media action

---

## Gestures

| Gesture | Action |
|---|---|
| Right hand swipe right | → Next slide / Right arrow |
| Left hand swipe left | ← Previous slide / Left arrow |
| Both hands raised (hold) | Space / Pause |
| Right hand raised above shoulder (hold) | Volume Up |
| Left hand raised above shoulder (hold) | Volume Down |

---

## Requirements

### Jetson Nano
- [NVIDIA Jetson Nano](https://developer.nvidia.com/embedded/jetson-nano-developer-kit)
- JetPack SDK (includes `jetson.inference` and `jetson.utils`)
- USB or CSI camera connected to the Nano
- Python 3 with the following packages:
  ```bash
  pip3 install opencv-python numpy
  ```

### Laptop (Mac or Windows)
- Python 3
- `pyautogui` (used on Windows):
  ```bash
  pip3 install pyautogui
  ```
- Both the Jetson and laptop must be on the **same WiFi network**

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/sehgality/computer-controller.git
cd computer-controller
```

### 2. Connect the Jetson Nano via VS Code Remote SSH

- Install the [Remote - SSH](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-ssh) extension in VS Code
- Connect to your Jetson: `ssh aidan@<jetson-ip>`
- Copy `jetson_gestures.py` and `dashboard.py` to the Jetson (e.g. your home directory `~/`)

```bash
scp jetson_gestures.py dashboard.py <user>@<jetson-ip>:~/
```

### 3. Find your laptop's IP address

**Mac:**
```bash
ipconfig getifaddr en0
```

**Windows:**
```bash
ipconfig
```
Look for the IPv4 address under your WiFi adapter.

---

## Running

### On the Jetson Nano

```bash
python3 ~/jetson_gestures.py --ip <your-laptop-ip>
```

Example:
```bash
python3 ~/jetson_gestures.py --ip 10.0.0.104
```

Once running, open the live dashboard in your browser:
```
http://<jetson-ip>:8080
```

### On the Laptop

```bash
python3 laptop_listener.py
```

The script automatically detects whether you're on Mac or Windows and uses the appropriate method for each command.

---

## Mac Setup — Required Permission

On macOS, Terminal needs **Accessibility access** to send keypresses (arrow keys, spacebar) to other applications.

1. Go to **System Settings → Privacy & Security → Accessibility**
2. Click **+** and add **Terminal**
3. Make sure the toggle is **on**
4. Restart `laptop_listener.py`

> Volume up/down work without this permission since they use `osascript` directly.

---

## Debug Mode

In `jetson_gestures.py`, `DEBUG_MODE = True` prints detected gestures to the terminal without sending any UDP commands. Set it to `False` to send real commands to the laptop:

```python
DEBUG_MODE = False
```

---

## Project Structure

```
computer-controller/
├── jetson_gestures.py   # Runs on Jetson — gesture detection + UDP sender
├── dashboard.py         # Runs on Jetson — live browser dashboard (imported by jetson_gestures.py)
└── laptop_listener.py   # Runs on laptop — receives UDP and executes keypresses
```
