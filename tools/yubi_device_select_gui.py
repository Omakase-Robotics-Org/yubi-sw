#!/usr/bin/env python3
"""Interactive GUI to assign LEFT/RIGHT (+ optional CENTER/head) roles to USB
cameras and a pair of ESP32-based encoders, and to capture each encoder's MIN
value. The result is printed as JSON on stdout for downstream udev-rule
generation.

Variant-aware: ``--variant stationary`` (default) expects 2 cameras (L/R);
``--variant portable`` expects 3 cameras (L/R/center) because the portable
head camera is the same USB model as the hand cameras.

The L/R assignment is bootstrapped from two pieces of side-channel information:
  1. The encoder firmware may emit lines prefixed with "L001," / "R001,". When
     present, that prefix tells us which side a given /dev/ttyACM* belongs to.
  2. Each L/R encoder shares a USB hub with its matching camera. Once we know
     the encoder's side from (1), we propagate the assignment to its hub-mate
     camera by comparing USB topology paths (e.g. "1-1.4.x"). In portable
     mode the remaining (un-matched) camera is the CENTER/head camera.

Both signals are best-effort. The user can correct any assignment with the
LEFT<->RIGHT Swap buttons, or with the per-camera pulldown (which auto-swaps
the conflicting slot to keep all three roles distinct).

Heavy GUI dependencies (cv2/serial/tkinter/PIL/matplotlib) are imported lazily
inside the runtime classes, so unit tests can import the module-level pure
helpers (parse_line, usb_hub_of, validate_cam_count, assign_camera_roles,
resolve_camera_pick, can_apply_portable, build_apply_payload) without
installing those packages.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Pure helpers — no heavy deps, safe to import from tests.
# ---------------------------------------------------------------------------

# Matches a USB topology path component such as "1-1.4" or "1-1.4.2".
_USB_LOC_RE = re.compile(r"^\d+-[\d.]+$")

SUPPORTED_VARIANTS = ("stationary", "portable")
CAM_SIDES_BY_VARIANT: Dict[str, Tuple[str, ...]] = {
    "stationary": ("left", "right"),
    "portable": ("left", "right", "center"),
}
EXPECTED_CAM_COUNT: Dict[str, int] = {
    "stationary": 2,
    "portable": 3,
}


def cam_sides_for(variant: str) -> Tuple[str, ...]:
    """Return the ordered tuple of camera role names for the given variant."""
    if variant not in CAM_SIDES_BY_VARIANT:
        raise ValueError(f"unknown variant: {variant!r}")
    return CAM_SIDES_BY_VARIANT[variant]


def validate_cam_count(variant: str, count: int) -> Optional[str]:
    """Return an error message if the count is wrong for the variant, else None.

    portable requires exactly 3 cameras (L/R/center, all same USB model).
    stationary requires exactly 2 cameras (L/R).
    """
    if variant not in EXPECTED_CAM_COUNT:
        return (
            f"unknown variant {variant!r}; "
            f"expected one of {sorted(EXPECTED_CAM_COUNT)}"
        )
    expected = EXPECTED_CAM_COUNT[variant]
    if count != expected:
        return (
            f"variant={variant} expects exactly {expected} cameras, "
            f"found {count}"
        )
    return None


def parse_line(line: bytes) -> Tuple[Optional[str], Optional[float]]:
    """Parse a serial line into (side, value).

    Accepts both labelled and unlabelled forms:
      "L001,0.12345" -> ("left", 0.12345)
      "R001,0.12345" -> ("right", 0.12345)
      "0.12345"      -> (None, 0.12345)

    The label form is what auto-assignment hinges on; the unlabelled form is
    still common in older firmware so we accept it and rely on the user to set
    the side manually via Swap.
    """
    try:
        s = line.decode("utf-8", errors="ignore").strip()
    except Exception:
        return None, None
    if not s or s.startswith("#"):
        return None, None
    side: Optional[str] = None
    num_str = s
    if "," in s:
        # Labelled form: split on the first comma so the head can be inspected.
        # Use [:1] (not [0]) so an empty head doesn't raise IndexError.
        head, num_str = s.split(",", 1)
        head = head.strip()
        if head[:1] in ("L", "l"):
            side = "left"
        elif head[:1] in ("R", "r"):
            side = "right"
    try:
        return side, float(num_str.strip())
    except ValueError:
        return side, None


def usb_hub_of(loc: Optional[str]) -> Optional[str]:
    """Return the parent hub of a USB topology location.

    '1-1.4.2' -> '1-1.4'   (downstream of hub 1-1.4)
    '1-2'     -> '1'       (root hub)

    Two devices share a hub iff this returns the same string for both. That
    equality is what ``assign_camera_roles`` uses to pair cameras to encoders.
    """
    if not loc:
        return None
    if "." in loc:
        return loc.rsplit(".", 1)[0]
    if "-" in loc:
        return loc.split("-", 1)[0]
    return loc


def assign_camera_roles(
    cam_devs_with_hub: List[Tuple[str, Optional[str]]],
    left_esp_hub: Optional[str],
    right_esp_hub: Optional[str],
    variant: str,
) -> Dict[str, Any]:
    """Decide which physical camera dev plays each role for the given variant.

    Inputs:
      cam_devs_with_hub: ordered list of (dev_path, hub) for every camera.
                         Order is treated as the fallback ordering when hub
                         matching fails.
      left_esp_hub, right_esp_hub: hub strings (output of usb_hub_of) of the
                                   left/right ESP devices. May be None when
                                   unknown — auto-match is skipped in that case.
      variant: "stationary" (assign left/right) or "portable" (also center).

    Returns:
      {
        "left": dev|None,
        "right": dev|None,
        "center": dev|None,        # always present for portable; None for stationary
        "matched_left": bool,      # True iff a hub-mate camera was found for L_esp
        "matched_right": bool,
      }

    Determinism: if multiple cameras share the same hub as L_esp (or hub equals
    R's hub), the FIRST one in input order wins. Ambiguity is reported only via
    the matched_* flags; callers are expected to surface a warning.
    """
    if variant not in CAM_SIDES_BY_VARIANT:
        raise ValueError(f"unknown variant: {variant!r}")
    expected = EXPECTED_CAM_COUNT[variant]
    if len(cam_devs_with_hub) != expected:
        raise ValueError(
            f"variant={variant} expects {expected} cameras, "
            f"got {len(cam_devs_with_hub)}"
        )

    devs = [d for (d, _h) in cam_devs_with_hub]
    hubs = {d: h for (d, h) in cam_devs_with_hub}

    left_dev: Optional[str] = None
    right_dev: Optional[str] = None
    matched_left = False
    matched_right = False

    if left_esp_hub:
        for d in devs:
            if hubs.get(d) == left_esp_hub:
                left_dev = d
                matched_left = True
                break

    if right_esp_hub:
        for d in devs:
            if d == left_dev:
                continue
            if hubs.get(d) == right_esp_hub:
                right_dev = d
                matched_right = True
                break

    # Fallback: fill any unset role from the remaining devices in input order.
    used = {x for x in (left_dev, right_dev) if x is not None}
    remaining_iter = iter([d for d in devs if d not in used])
    if left_dev is None:
        left_dev = next(remaining_iter, None)
        if left_dev is not None:
            used.add(left_dev)
    if right_dev is None:
        # rebuild iter so we skip the freshly-claimed left_dev
        remaining_iter = iter([d for d in devs if d not in used])
        right_dev = next(remaining_iter, None)
        if right_dev is not None:
            used.add(right_dev)

    center_dev: Optional[str] = None
    if variant == "portable":
        # The remaining camera is the head camera.
        leftover = [d for d in devs if d not in used]
        center_dev = leftover[0] if leftover else None

    return {
        "left": left_dev,
        "right": right_dev,
        "center": center_dev,
        "matched_left": matched_left,
        "matched_right": matched_right,
    }


def resolve_camera_pick(
    roles: Dict[str, Optional[str]],
    side: str,
    new_dev: str,
    sides: Tuple[str, ...],
) -> Dict[str, Optional[str]]:
    """Apply a pulldown selection on `side`, auto-swapping any conflicting slot.

    Returns a new dict; does not mutate the input.

    - If `new_dev` is already in `roles[side]`, the dict is unchanged (modulo
      copy).
    - If `new_dev` currently lives in another slot `other`, we swap the two
      slots so all three sides remain assigned to distinct devices.
    - If `new_dev` is not assigned anywhere yet (shouldn't happen in normal
      flow, since the pulldown options are the same dev pool), we still set
      `roles[side] = new_dev`. The previously-held dev at `side` becomes
      unassigned — caller may surface that as a warning.
    """
    if side not in sides:
        raise ValueError(f"side {side!r} not in {sides!r}")
    out = {s: roles.get(s) for s in sides}
    current = out.get(side)
    if current == new_dev:
        return out
    other_side: Optional[str] = None
    for s in sides:
        if s == side:
            continue
        if out.get(s) == new_dev:
            other_side = s
            break
    out[side] = new_dev
    if other_side is not None:
        out[other_side] = current
    return out


def can_apply_portable(
    esp_side_detected: Dict[str, Optional[str]],
    cam_role: Dict[str, Optional[str]],
) -> Optional[str]:
    """Return an error string if portable APPLY is not ready, else None.

    Checks (in priority order):
      1. Both ESP devnodes have observed a labelled side ("L001," / "R001,").
         This is the "gripper L/R info must be flowing" requirement.
      2. All three camera roles (left/right/center) are set.
      3. The three camera devs are mutually distinct.
    """
    missing_label = [dev for dev, side in esp_side_detected.items() if not side]
    if missing_label:
        return (
            "Encoder L/R label not observed yet on: "
            + ", ".join(sorted(missing_label))
            + ". Wait for 'L001,' / 'R001,' lines from both grippers before APPLY."
        )

    unset = [s for s in ("left", "right", "center") if not cam_role.get(s)]
    if unset:
        return f"Camera assignment incomplete: {', '.join(unset)} unset."

    devs = [cam_role["left"], cam_role["right"], cam_role["center"]]
    if len(set(devs)) != 3:
        return (
            f"Camera roles must be distinct devices, got "
            f"left={cam_role['left']} right={cam_role['right']} "
            f"center={cam_role['center']}."
        )
    return None


def build_apply_payload(
    cam_role: Dict[str, Optional[str]],
    esp_role: Dict[str, Optional[str]],
    esp_serials: Dict[str, str],
    calib_min: Dict[str, Optional[float]],
    variant: str,
) -> Dict[str, Any]:
    """Build the JSON payload emitted by the GUI on APPLY.

    Stationary keeps the original shape (no "center" key). Portable adds
    ``camera.center.dev``.
    """
    out: Dict[str, Any] = {
        "camera": {
            "left": {"dev": cam_role["left"]},
            "right": {"dev": cam_role["right"]},
        },
        "esp32": {
            "left": {
                "dev": esp_role["left"],
                "serial": esp_serials[esp_role["left"]],
                "min": calib_min[esp_role["left"]],
            },
            "right": {
                "dev": esp_role["right"],
                "serial": esp_serials[esp_role["right"]],
                "min": calib_min[esp_role["right"]],
            },
        },
        "encoder_node": {
            "ros__parameters": {
                "left_min_raw": calib_min[esp_role["left"]],
                "right_min_raw": calib_min[esp_role["right"]],
            }
        },
    }
    if variant == "portable":
        out["camera"]["center"] = {"dev": cam_role["center"]}
    return out


# ---------------------------------------------------------------------------
# Below this line: runtime code that needs cv2/serial/Tk/PIL/matplotlib.
# Imports are deferred into functions/classes so tests can use the module
# without those packages.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EspDev:
    device: str          # /dev/ttyACM*
    vid: int
    pid: int
    serial: str          # serial_number (ID_SERIAL_SHORT相当). Used for stable udev rules.
    location: str        # USB topology path (e.g. "1-1.4.1") - used to pair with camera by hub.


def find_esp_devices(vid_hex: str, pid_hex: str) -> List[EspDev]:
    from serial.tools import list_ports  # lazy

    vid = int(vid_hex, 16)
    pid = int(pid_hex, 16)
    out: List[EspDev] = []
    for p in list_ports.comports():
        if p.vid is None or p.pid is None:
            continue
        if int(p.vid) != vid or int(p.pid) != pid:
            continue
        out.append(
            EspDev(
                device=p.device,
                vid=int(p.vid),
                pid=int(p.pid),
                serial=(p.serial_number or ""),
                location=(p.location or ""),
            )
        )
    out.sort(key=lambda d: (d.serial or "", d.device))
    return out


def video_dev_usb_location(dev_path: str) -> Optional[str]:
    """Return the USB topology location (e.g. '1-1.4.2') for a /dev/video* path.

    Mirrors the format pyserial reports as ``ListPortInfo.location`` for
    ttyACM devices, so the result can be compared directly against
    ``EspDev.location`` to decide whether a camera and an ESP share a USB hub.
    """
    name = os.path.basename(dev_path)
    sysfs = f"/sys/class/video4linux/{name}/device"
    try:
        real = os.path.realpath(sysfs)
    except OSError:
        return None
    last: Optional[str] = None
    for p in real.split("/"):
        if _USB_LOC_RE.match(p):
            last = p
    return last


def _run_gui(args: argparse.Namespace) -> None:
    """Build the Tk app and run the mainloop. All heavy imports stay in here."""
    import queue
    import threading
    import time

    import cv2
    import serial
    import tkinter as tk
    from tkinter import ttk, messagebox

    from PIL import Image, ImageTk
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

    class ScrollableFrame(ttk.Frame):
        """Vertical scrollbar wrapper to keep the UI usable on small displays."""

        def __init__(self, parent):
            super().__init__(parent)
            self.canvas = tk.Canvas(self, highlightthickness=0)
            self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
            self.canvas.configure(yscrollcommand=self.scrollbar.set)

            self.canvas.grid(row=0, column=0, sticky="nsew")
            self.scrollbar.grid(row=0, column=1, sticky="ns")
            self.columnconfigure(0, weight=1)
            self.rowconfigure(0, weight=1)

            self.body = ttk.Frame(self.canvas)
            self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

            self.body.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
            self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))

            self.canvas.bind("<MouseWheel>", self._on_mousewheel)
            self.canvas.bind("<Button-4>", self._on_mousewheel)
            self.canvas.bind("<Button-5>", self._on_mousewheel)

        def _on_mousewheel(self, event):
            if event.delta:
                direction = -1 if event.delta > 0 else 1
                self.canvas.yview_scroll(direction, "units")
            elif event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")

    class EspSerialReader(threading.Thread):
        def __init__(self, devnode: str, baud: int, out_q: "queue.Queue"):
            super().__init__(daemon=True)
            self.devnode = devnode
            self.baud = baud
            self.out_q = out_q
            self.stop_flag = threading.Event()
            self.ser: Optional[serial.Serial] = None

        def stop(self):
            self.stop_flag.set()

        def run(self):
            try:
                self.ser = serial.Serial(self.devnode, baudrate=self.baud, timeout=0.2)
                # ESP32 boards reset on DTR/RTS toggle by default; force the lines
                # low at open so opening the port does not reboot the firmware.
                try:
                    self.ser.dtr = False
                    self.ser.rts = False
                except Exception:
                    pass
                try:
                    self.ser.reset_input_buffer()
                except Exception:
                    pass
            except Exception as e:
                self.out_q.put(("err", self.devnode, f"open failed: {e}"))
                return

            while not self.stop_flag.is_set():
                try:
                    line = self.ser.readline()
                    if not line:
                        continue
                    side, v = parse_line(line)
                    if v is not None:
                        self.out_q.put(("val", self.devnode, time.time(), side, v))
                except Exception as e:
                    self.out_q.put(("err", self.devnode, f"read failed: {e}"))
                    break

            try:
                if self.ser:
                    self.ser.close()
            except Exception:
                pass

    class CamReader(threading.Thread):
        def __init__(self, dev: str, out_q: "queue.Queue", label: str):
            super().__init__(daemon=True)
            self.dev = dev
            self.label = label
            self.out_q = out_q
            self.stop_flag = threading.Event()
            self.cap = None

        def stop(self):
            self.stop_flag.set()

        def run(self):
            try:
                idx = int(self.dev.replace("/dev/video", ""))
                self.cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
                if not self.cap.isOpened():
                    self.out_q.put(("err", self.dev, f"camera open failed: {self.dev}"))
                    return
            except Exception as e:
                self.out_q.put(("err", self.dev, f"camera open failed: {e}"))
                return

            while not self.stop_flag.is_set():
                ok, frame = self.cap.read()
                if not ok or frame is None:
                    continue
                frame = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
                self.out_q.put(("frame", self.dev, time.time(), frame))
                time.sleep(0.03)

            try:
                if self.cap:
                    self.cap.release()
            except Exception:
                pass

    class App:
        """Variant-aware LEFT/RIGHT (+CENTER) setup UI.

        Architectural invariant: the role dicts (``cam_role`` and ``esp_role``)
        are the single source of truth for which physical device plays which
        side. All UI widgets are keyed by side name, so swapping reduces to
        swapping values in those dicts.
        """

        def __init__(
            self,
            root: tk.Tk,
            cam_devs: List[str],
            esp_a: EspDev,
            esp_b: EspDev,
            baud: int,
            variant: str,
        ):
            self.root = root
            self.cam_devs = list(cam_devs)
            self.esp_a = esp_a
            self.esp_b = esp_b
            self.baud = baud
            self.variant = variant
            self.cam_sides: Tuple[str, ...] = cam_sides_for(variant)

            self.q: "queue.Queue" = queue.Queue()

            # Default ordering as a starting point. Replaced by try_auto_assign()
            # when both encoders emit labelled lines.
            initial_roles: Dict[str, Optional[str]] = {}
            for i, side in enumerate(self.cam_sides):
                initial_roles[side] = self.cam_devs[i] if i < len(self.cam_devs) else None
            self.cam_role: Dict[str, Optional[str]] = initial_roles
            self.esp_role: Dict[str, Optional[str]] = {
                "left": esp_a.device,
                "right": esp_b.device,
            }

            self.esp_side_detected: Dict[str, Optional[str]] = {
                esp_a.device: None,
                esp_b.device: None,
            }
            self.auto_assigned: bool = False

            self.data: Dict[str, List[Tuple[float, float]]] = {
                esp_a.device: [],
                esp_b.device: [],
            }
            self.latest: Dict[str, Optional[float]] = {
                esp_a.device: None,
                esp_b.device: None,
            }
            self.calib_min: Dict[str, Optional[float]] = {
                esp_a.device: None,
                esp_b.device: None,
            }

            # Widget refs keyed by side
            self.cam_canvas: Dict[str, ttk.Label] = {}
            self.cam_path_lbl: Dict[str, ttk.Label] = {}
            self.cam_picker: Dict[str, ttk.Combobox] = {}
            self.cam_picker_var: Dict[str, tk.StringVar] = {}
            self.esp_path_lbl: Dict[str, ttk.Label] = {}
            self.val_lbl: Dict[str, ttk.Label] = {}
            self.ax: Dict[str, object] = {}
            self.line: Dict[str, object] = {}
            self.plot_canvas: Dict[str, FigureCanvasTkAgg] = {}
            self.tkimg: Dict[str, object] = {side: None for side in self.cam_sides}

            title = "Yubi Device Setup"
            title += f" — variant={variant}"
            root.title(title)
            root.columnconfigure(0, weight=1)
            root.rowconfigure(0, weight=1)

            scroll = ScrollableFrame(root)
            scroll.grid(row=0, column=0, sticky="nsew")

            outer = ttk.Frame(scroll.body, padding=10)
            outer.grid(row=0, column=0, sticky="nsew")
            scroll.body.columnconfigure(0, weight=1)
            outer.columnconfigure(0, weight=1)

            cols = ttk.Frame(outer)
            cols.grid(row=0, column=0, sticky="nsew")
            cols.columnconfigure(0, weight=1)
            cols.columnconfigure(2, weight=1)

            # Row 0: CENTER camera (portable only) at the top, spanning all 3 cols.
            row_cursor = 0
            if "center" in self.cam_sides:
                ttk.Label(
                    cols, text="CENTER (HEAD)", font=("Sans", 12, "bold"),
                    anchor="center",
                ).grid(row=row_cursor, column=0, columnspan=3, sticky="ew")
                row_cursor += 1
                self._build_camera_section(cols, "center").grid(
                    row=row_cursor, column=0, columnspan=3,
                    sticky="nsew", padx=4, pady=4,
                )
                row_cursor += 1

            # L/R column headers
            ttk.Label(cols, text="LEFT", font=("Sans", 12, "bold"), anchor="center").grid(
                row=row_cursor, column=0, sticky="ew"
            )
            ttk.Label(cols, text="RIGHT", font=("Sans", 12, "bold"), anchor="center").grid(
                row=row_cursor, column=2, sticky="ew"
            )
            row_cursor += 1

            # Camera row (L/R)
            self._build_camera_section(cols, "left").grid(
                row=row_cursor, column=0, sticky="nsew", padx=4, pady=4
            )
            ttk.Button(cols, text="Swap\nCameras", command=self.swap_cam, width=12).grid(
                row=row_cursor, column=1, padx=8
            )
            self._build_camera_section(cols, "right").grid(
                row=row_cursor, column=2, sticky="nsew", padx=4, pady=4
            )
            row_cursor += 1

            # Encoder row
            self._build_encoder_section(cols, "left").grid(
                row=row_cursor, column=0, sticky="nsew", padx=4, pady=4
            )
            ttk.Button(cols, text="Swap\nEncoders", command=self.swap_esp, width=12).grid(
                row=row_cursor, column=1, padx=8
            )
            self._build_encoder_section(cols, "right").grid(
                row=row_cursor, column=2, sticky="nsew", padx=4, pady=4
            )

            self.auto_status_lbl = ttk.Label(
                outer,
                text="Auto-assignment: waiting for labelled line (Lxxx,...) from encoder",
                foreground="#666",
            )
            self.auto_status_lbl.grid(row=1, column=0, sticky="w", pady=(8, 0))

            bottom = ttk.Frame(outer)
            bottom.grid(row=2, column=0, sticky="ew", pady=(10, 0))
            bottom.columnconfigure(0, weight=1)
            bottom.columnconfigure(1, weight=1)
            ttk.Button(bottom, text="APPLY (print JSON and exit)", command=self.apply).grid(
                row=0, column=0, sticky="ew"
            )
            ttk.Button(bottom, text="Cancel", command=self.cancel).grid(
                row=0, column=1, sticky="ew", padx=(10, 0)
            )

            self.refresh_role_labels()

            # Threads
            self.cam_readers: List[CamReader] = [
                CamReader(d, self.q, chr(ord("A") + i)) for i, d in enumerate(self.cam_devs)
            ]
            self.esp_reader_a = EspSerialReader(self.esp_a.device, baud, self.q)
            self.esp_reader_b = EspSerialReader(self.esp_b.device, baud, self.q)

            for r in self.cam_readers:
                r.start()
            self.esp_reader_a.start()
            self.esp_reader_b.start()

            self.root.after(50, self.tick)

        def _build_camera_section(self, parent, side: str) -> ttk.Frame:
            label = "Camera (Center / Head)" if side == "center" else "Camera"
            f = ttk.LabelFrame(parent, text=label, padding=8)
            f.columnconfigure(0, weight=1)

            self.cam_path_lbl[side] = ttk.Label(f, text="(unset)")
            self.cam_path_lbl[side].grid(row=0, column=0, sticky="w")

            self.cam_canvas[side] = ttk.Label(f)
            self.cam_canvas[side].grid(row=1, column=0, pady=(4, 0))

            var = tk.StringVar(value=self.cam_role.get(side) or "")
            self.cam_picker_var[side] = var
            picker = ttk.Combobox(
                f,
                values=list(self.cam_devs),
                textvariable=var,
                state="readonly",
            )
            picker.grid(row=2, column=0, sticky="ew", pady=(4, 0))
            picker.bind(
                "<<ComboboxSelected>>",
                lambda _e, s=side: self.on_cam_pick(s),
            )
            self.cam_picker[side] = picker
            return f

        def _build_encoder_section(self, parent, side: str) -> ttk.Frame:
            f = ttk.LabelFrame(parent, text="Encoder", padding=8)
            f.columnconfigure(0, weight=1)

            self.esp_path_lbl[side] = ttk.Label(f, text="(unset)")
            self.esp_path_lbl[side].grid(row=0, column=0, sticky="w")

            fig = Figure(figsize=(4, 2.2), dpi=100)
            ax = fig.add_subplot(111)
            ax.set_title("last 5s")
            ax.set_xlabel("t (s)")
            ax.set_ylabel("value")
            (line,) = ax.plot([], [])
            canvas = FigureCanvasTkAgg(fig, master=f)
            canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", pady=(4, 0))
            self.ax[side] = ax
            self.line[side] = line
            self.plot_canvas[side] = canvas

            self.val_lbl[side] = ttk.Label(f, text="value: -    min: -")
            self.val_lbl[side].grid(row=2, column=0, sticky="w", pady=(4, 0))

            ttk.Button(f, text="Capture MIN", command=lambda s=side: self.cap_min_side(s)).grid(
                row=3, column=0, sticky="ew", pady=(4, 0)
            )
            return f

        def refresh_role_labels(self):
            for side in self.cam_sides:
                cam = self.cam_role.get(side)
                self.cam_path_lbl[side].config(text=cam or "(unset)")
                if side in self.cam_picker_var:
                    self.cam_picker_var[side].set(cam or "")
            for side in ("left", "right"):
                esp = self.esp_role.get(side)
                self.esp_path_lbl[side].config(text=esp or "(unset)")

        def swap_cam(self):
            self.cam_role["left"], self.cam_role["right"] = (
                self.cam_role["right"],
                self.cam_role["left"],
            )
            self.refresh_role_labels()

        def swap_esp(self):
            self.esp_role["left"], self.esp_role["right"] = (
                self.esp_role["right"],
                self.esp_role["left"],
            )
            self.refresh_role_labels()

        def on_cam_pick(self, side: str):
            new_dev = self.cam_picker_var[side].get()
            if not new_dev:
                return
            new_roles = resolve_camera_pick(
                self.cam_role, side, new_dev, self.cam_sides
            )
            self.cam_role = new_roles
            self.refresh_role_labels()

        def try_auto_assign(self):
            """Bootstrap L/R/center using detected encoder labels + USB-hub pairing.

            Runs at most once (guarded by ``self.auto_assigned``); after that
            the user owns the assignment via Swap or pulldown.
            """
            if self.auto_assigned:
                return

            side_a = self.esp_side_detected.get(self.esp_a.device)
            side_b = self.esp_side_detected.get(self.esp_b.device)

            if not side_a or not side_b:
                if side_a or side_b:
                    self.auto_status_lbl.config(
                        text=f"Auto-assignment: detected A={side_a or '?'}, B={side_b or '?'}; "
                             "waiting for the other encoder...",
                        foreground="#666",
                    )
                return

            if side_a == side_b:
                self.auto_assigned = True
                self.auto_status_lbl.config(
                    text=f"Auto-assignment FAILED: conflicting labels (A={side_a}, B={side_b})",
                    foreground="#a33",
                )
                messagebox.showerror(
                    "Encoder side conflict",
                    f"Both ESP devices reported the same side ({side_a.upper()}):\n\n"
                    f"  A ({self.esp_a.device}): {side_a}\n"
                    f"  B ({self.esp_b.device}): {side_b}\n\n"
                    "Check the encoder firmware / wiring and restart this tool.",
                )
                return

            if side_a == "left":
                left_esp, right_esp = self.esp_a, self.esp_b
            else:
                left_esp, right_esp = self.esp_b, self.esp_a

            self.esp_role["left"] = left_esp.device
            self.esp_role["right"] = right_esp.device

            cam_devs_with_hub: List[Tuple[str, Optional[str]]] = [
                (d, usb_hub_of(video_dev_usb_location(d))) for d in self.cam_devs
            ]
            assignment = assign_camera_roles(
                cam_devs_with_hub,
                usb_hub_of(left_esp.location),
                usb_hub_of(right_esp.location),
                self.variant,
            )

            self.cam_role["left"] = assignment["left"]
            self.cam_role["right"] = assignment["right"]
            if "center" in self.cam_sides:
                self.cam_role["center"] = assignment["center"]

            if assignment["matched_left"] and assignment["matched_right"]:
                hub_msg = " (cameras paired by USB hub)"
            else:
                hub_msg = " (could not match all cameras by USB hub; verify and use pulldown if needed)"

            self.auto_assigned = True
            self.refresh_role_labels()
            self.auto_status_lbl.config(
                text=f"Auto-assigned from encoder labels: A={side_a}, B={side_b}{hub_msg}",
                foreground="#070" if "could not" not in hub_msg else "#a63",
            )

        def cap_min_side(self, side: str):
            dev = self.esp_role[side]
            if not dev:
                messagebox.showerror("No device", f"No ESP assigned to {side.upper()}")
                return
            v = self.latest.get(dev)
            if v is None:
                messagebox.showerror("No data", f"No data yet from {side.upper()} ESP ({dev})")
                return
            self.calib_min[dev] = float(v)

        def tick(self):
            now = time.time()
            while True:
                try:
                    typ, dev, *rest = self.q.get_nowait()
                except queue.Empty:
                    break

                if typ == "frame":
                    _, frame = rest
                    self.update_cam_frame(dev, frame)
                elif typ == "val":
                    t, side, v = rest
                    self.data[dev].append((t, v))
                    self.latest[dev] = v
                    if side and not self.esp_side_detected.get(dev):
                        self.esp_side_detected[dev] = side
                        self.try_auto_assign()
                else:
                    err = rest[0] if rest else "unknown"
                    messagebox.showerror("Device error", f"{dev}:\n{err}")

            for dev in (self.esp_a.device, self.esp_b.device):
                self.data[dev] = [(t, v) for (t, v) in self.data[dev] if now - t <= 5.0]

            for side in ("left", "right"):
                dev = self.esp_role[side]
                data = self.data.get(dev, []) if dev else []
                x = [t - now for (t, _) in data]
                y = [v for (_, v) in data]
                self.line[side].set_data(x, y)
                self.ax[side].relim()
                self.ax[side].autoscale_view()
                self.plot_canvas[side].draw_idle()

                latest = self.latest.get(dev) if dev else None
                mi = self.calib_min.get(dev) if dev else None
                self.val_lbl[side].config(
                    text=f"value: {latest if latest is not None else '-'}    "
                         f"min: {mi if mi is not None else '-'}"
                )

            self.root.after(50, self.tick)

        def update_cam_frame(self, dev: str, frame_bgr):
            rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            tkimg = ImageTk.PhotoImage(img)

            for side in self.cam_sides:
                if dev == self.cam_role.get(side):
                    self.tkimg[side] = tkimg
                    self.cam_canvas[side].configure(image=tkimg)
                    return

        def apply(self):
            # Cameras
            for side in self.cam_sides:
                if not self.cam_role.get(side):
                    messagebox.showerror("Missing", f"Please set Camera {side.upper()}.")
                    return
            cam_devs_assigned = [self.cam_role[s] for s in self.cam_sides]
            if len(set(cam_devs_assigned)) != len(cam_devs_assigned):
                messagebox.showerror(
                    "Invalid", "Camera roles must be assigned to distinct devices."
                )
                return

            # Encoders
            if not self.esp_role["left"] or not self.esp_role["right"]:
                messagebox.showerror("Missing", "Please set ESP LEFT and RIGHT.")
                return
            if self.esp_role["left"] == self.esp_role["right"]:
                messagebox.showerror("Invalid", "ESP LEFT and RIGHT cannot be the same.")
                return

            for side in ("left", "right"):
                devnode = self.esp_role[side]
                mi = self.calib_min.get(devnode)
                if mi is None:
                    messagebox.showerror("Missing", f"Please capture MIN for ESP {side} ({devnode}).")
                    return

            # Portable-only: gripper L/R labels must have been observed (so we know
            # the encoder side mapping is correct), and re-check distinct devs +
            # center set via the shared helper.
            if self.variant == "portable":
                err = can_apply_portable(self.esp_side_detected, self.cam_role)
                if err:
                    messagebox.showerror("Not ready", err)
                    return

            esp_by_dev = {self.esp_a.device: self.esp_a, self.esp_b.device: self.esp_b}
            left_dev = esp_by_dev[self.esp_role["left"]]
            right_dev = esp_by_dev[self.esp_role["right"]]
            if not left_dev.serial or not right_dev.serial:
                messagebox.showerror(
                    "Missing serial",
                    "ESP serial_number is missing; cannot create stable udev rules.",
                )
                return

            out = build_apply_payload(
                cam_role=self.cam_role,
                esp_role=self.esp_role,
                esp_serials={
                    self.esp_a.device: self.esp_a.serial,
                    self.esp_b.device: self.esp_b.serial,
                },
                calib_min=self.calib_min,
                variant=self.variant,
            )

            self.stop_all()
            print(json.dumps(out))
            self.root.destroy()

        def cancel(self):
            self.stop_all()
            self.root.destroy()

        def stop_all(self):
            try:
                for r in self.cam_readers:
                    r.stop()
                self.esp_reader_a.stop()
                self.esp_reader_b.stop()
            except Exception:
                pass

    # ---- entry point inside _run_gui ----
    cam_devs = [args.cam1, args.cam2]
    if args.variant == "portable":
        if not args.cam3:
            print("ERROR: --cam3 is required when --variant=portable", flush=True)
            raise SystemExit(2)
        cam_devs.append(args.cam3)

    err = validate_cam_count(args.variant, len(cam_devs))
    if err:
        print(f"ERROR: {err}", flush=True)
        raise SystemExit(2)

    esp_devs = find_esp_devices(args.esp_vid, args.esp_pid)
    if len(esp_devs) != 2:
        msg = (
            f"Expected exactly 2 ESP32 tty devices with VID:PID="
            f"{args.esp_vid}:{args.esp_pid}, found {len(esp_devs)}\n"
        )
        for d in esp_devs:
            msg += f"  - {d.device} serial={d.serial} location={d.location}\n"
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("ESP32 device count error", msg)
        raise SystemExit(3)

    root = tk.Tk()
    App(root, cam_devs, esp_devs[0], esp_devs[1], args.baud, args.variant)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cam-vid", required=True)
    ap.add_argument("--cam-pid", required=True)
    ap.add_argument("--cam1", required=True)
    ap.add_argument("--cam2", required=True)
    ap.add_argument("--cam3", default=None, help="Required for --variant=portable")
    ap.add_argument("--esp-vid", required=True)
    ap.add_argument("--esp-pid", required=True)
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument(
        "--variant",
        choices=list(SUPPORTED_VARIANTS),
        default="stationary",
        help="Robot variant (default: stationary). 'portable' expects 3 cameras.",
    )
    args = ap.parse_args()
    _run_gui(args)


if __name__ == "__main__":
    main()
