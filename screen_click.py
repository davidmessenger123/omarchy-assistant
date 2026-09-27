#!/usr/bin/env python3
import argparse
import fcntl
import json
import math
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_SET_ABSBIT = 0x40045567
EV_SYN = 0x00
EV_KEY = 0x01
EV_ABS = 0x03
SYN_REPORT = 0x00
ABS_X = 0x00
ABS_Y = 0x01
BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BUS_VIRTUAL = 0x06
ABS_CNT = 64


def monitor_bounds(monitor_file=None):
    monitors = json.loads(subprocess.check_output(["/usr/bin/hyprctl", "monitors", "-j"], text=True))
    active = [monitor for monitor in monitors if not monitor.get("disabled", False)]
    if not active:
        raise RuntimeError("no active monitors found")
    requested = ""
    if monitor_file:
        try:
            requested = Path(monitor_file).read_text(encoding="utf-8").strip()
        except OSError:
            requested = ""
    named = [monitor for monitor in active if monitor.get("name") == requested]
    focused = [monitor for monitor in active if monitor.get("focused")]
    monitor = named[0] if named else (focused[0] if focused else active[0])
    scale = max(float(monitor.get("scale") or 1), 0.1)
    width = float(monitor["width"]) / scale
    height = float(monitor["height"]) / scale
    transform = int(monitor.get("transform") or 0)
    if transform in {1, 3, 5, 7}:
        width, height = height, width
    return float(monitor["x"]), float(monitor["y"]), width, height


def event_bytes(event_type, code, value):
    now = time.time_ns()
    seconds = now // 1_000_000_000
    microseconds = (now % 1_000_000_000) // 1_000
    return struct.pack("llHHi", seconds, microseconds, event_type, code, int(value))


def send_click(x, y, button, monitor_file=None):
    left, top, width, height = monitor_bounds(monitor_file)
    if not all(math.isfinite(value) for value in (x, y, left, top, width, height)):
        raise RuntimeError("invalid monitor geometry")
    if width <= 1 or height <= 1:
        raise RuntimeError("invalid monitor dimensions")
    global_x = round(left + min(max(x, 0.0), 1.0) * (width - 1))
    global_y = round(top + min(max(y, 0.0), 1.0) * (height - 1))
    if button == "right":
        button_code = BTN_RIGHT
    else:
        button_code = BTN_LEFT
    device = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
    try:
        for event_type in (EV_SYN, EV_KEY, EV_ABS):
            fcntl.ioctl(device, UI_SET_EVBIT, event_type)
        for axis in (ABS_X, ABS_Y):
            fcntl.ioctl(device, UI_SET_ABSBIT, axis)
        for key in (BTN_LEFT, BTN_RIGHT):
            fcntl.ioctl(device, UI_SET_KEYBIT, key)
        name = b"Omarchy Assistant Click"
        absmax = [0] * ABS_CNT
        absmin = [0] * ABS_CNT
        absfuzz = [0] * ABS_CNT
        absflat = [0] * ABS_CNT
        absmax[ABS_X] = round(width) - 1
        absmax[ABS_Y] = round(height) - 1
        user_dev = struct.pack(
            "80sHHHHI64i64i64i64i",
            name,
            BUS_VIRTUAL,
            0x1234,
            0x5678,
            1,
            0,
            *absmax,
            *absmin,
            *absfuzz,
            *absflat,
        )
        os.write(device, user_dev)
        fcntl.ioctl(device, UI_DEV_CREATE)
        time.sleep(0.08)
        os.write(device, event_bytes(EV_ABS, ABS_X, global_x - left))
        os.write(device, event_bytes(EV_ABS, ABS_Y, global_y - top))
        os.write(device, event_bytes(EV_SYN, SYN_REPORT, 0))
        time.sleep(0.04)
        os.write(device, event_bytes(EV_KEY, button_code, 1))
        os.write(device, event_bytes(EV_SYN, SYN_REPORT, 0))
        time.sleep(0.08)
        os.write(device, event_bytes(EV_KEY, button_code, 0))
        os.write(device, event_bytes(EV_SYN, SYN_REPORT, 0))
        time.sleep(0.08)
        return global_x, global_y
    finally:
        try:
            fcntl.ioctl(device, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(device)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--button", choices=("left", "right"), default="left")
    parser.add_argument("--monitor-file", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not all(math.isfinite(value) for value in (args.x, args.y)) or not 0 <= args.x <= 1 or not 0 <= args.y <= 1:
        raise ValueError("x and y must be finite values between 0 and 1")
    if args.dry_run:
        left, top, width, height = monitor_bounds(args.monitor_file or None)
        print(json.dumps({"x": round(left + args.x * (width - 1)), "y": round(top + args.y * (height - 1))}))
        return
    x, y = send_click(args.x, args.y, args.button, args.monitor_file or None)
    print(json.dumps({"x": x, "y": y, "button": args.button}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
