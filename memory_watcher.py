import argparse
import ctypes
import json
import math
from pathlib import Path
import queue
import sys
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from PIL import ImageGrab, ImageTk
from pyboy import PyBoy  # noqa


WINDOW_TITLE = "Game Boy Memory Map"
EMULATION_SPEED = 1
EMULATION_FRAMES_PER_UPDATE = 1
SOUND_VOLUME = 100
SOUND_SAMPLE_RATE = 48000
MIN_BYTES_PER_ROW = 16
MAX_BYTES_PER_ROW = 64
MIN_VISIBLE_ROWS = 20
UI_REFRESH_MS = 16
UI_RENDER_DIVISOR = 2
INPUT_DIFF_WINDOW_FRAMES = 12
HISTORY_LENGTH = 96
EVENT_WINDOW_TICKS = 90
RARITY_DECAY = 0.985
FLASH_DECAY = 0.84
RECENT_DECAY = 0.965
EVENT_DECAY = 0.92
TEXT_COLOR = "#f8f5ec"
MUTED_TEXT = "#b8b0a1"
PANEL_BG = "#14110f"
CANVAS_BG = "#181411"
GRID_BASE = "#241d17"
ACCENT = "#ffb703"
EVENT_COLOR = "#fb5607"
RARE_COLOR = "#80ed99"
HOT_COLOR = "#ff5d73"
CLICK_OUTLINE = "#f4f1de"
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 760
GRID_ORIGIN_X = 74
GRID_ORIGIN_Y = 28
CELL_WIDTH = 20
CELL_HEIGHT = 18
CELL_GAP = 1
ADDRESS_TEXT_X = 10
HEADER_TEXT_Y = 10

REGIONS = {
    "WRAM": (0xC000, 0xE000),
    "HRAM": (0xFF80, 0xFFFF),
}

XINPUT_GAMEPAD_DPAD_UP = 0x0001
XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
XINPUT_GAMEPAD_START = 0x0010
XINPUT_GAMEPAD_BACK = 0x0020
XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_B = 0x2000
XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE = 7849
SNAPSHOT_DIR = Path("snapshots")
GLOBAL_SITE_PACKAGES = Path(
    r"C:\Users\Casim\AppData\Local\Programs\Python\Python310\Lib\site-packages"
)

try:
    import sounddevice as sd
except ImportError:
    if GLOBAL_SITE_PACKAGES.exists():
        sys.path.append(str(GLOBAL_SITE_PACKAGES))
    try:
        import sounddevice as sd
    except ImportError:
        sd = None


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def hex_color(red, green, blue):
    return f"#{red:02x}{green:02x}{blue:02x}"


def lerp_color(color_a, color_b, amount):
    amount = clamp(amount, 0.0, 1.0)
    red = int(color_a[0] + (color_b[0] - color_a[0]) * amount)
    green = int(color_a[1] + (color_b[1] - color_a[1]) * amount)
    blue = int(color_a[2] + (color_b[2] - color_a[2]) * amount)
    return hex_color(red, green, blue)


def rgb(red, green, blue):
    return (red, green, blue)


class XInputGamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XInputState(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", XInputGamepad),
    ]


class XboxController:
    def __init__(self):
        self._xinput = None
        for dll_name in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
            try:
                self._xinput = ctypes.WinDLL(dll_name)
                break
            except OSError:
                continue
        self.connected = False

    def poll_buttons(self):
        if self._xinput is None:
            self.connected = False
            return set()

        state = XInputState()
        result = self._xinput.XInputGetState(0, ctypes.byref(state))
        if result != 0:
            self.connected = False
            return set()

        self.connected = True
        buttons = set()
        gamepad = state.Gamepad

        if gamepad.wButtons & XINPUT_GAMEPAD_DPAD_UP:
            buttons.add("up")
        if gamepad.wButtons & XINPUT_GAMEPAD_DPAD_DOWN:
            buttons.add("down")
        if gamepad.wButtons & XINPUT_GAMEPAD_DPAD_LEFT:
            buttons.add("left")
        if gamepad.wButtons & XINPUT_GAMEPAD_DPAD_RIGHT:
            buttons.add("right")
        if gamepad.wButtons & XINPUT_GAMEPAD_A:
            buttons.add("a")
        if gamepad.wButtons & XINPUT_GAMEPAD_B:
            buttons.add("b")
        if gamepad.wButtons & XINPUT_GAMEPAD_START:
            buttons.add("start")
        if gamepad.wButtons & XINPUT_GAMEPAD_BACK:
            buttons.add("select")

        # Left stick can stand in for the D-pad if that's more comfortable.
        if gamepad.sThumbLX <= -XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE:
            buttons.add("left")
        elif gamepad.sThumbLX >= XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE:
            buttons.add("right")
        if gamepad.sThumbLY <= -XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE:
            buttons.add("down")
        elif gamepad.sThumbLY >= XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE:
            buttons.add("up")

        return buttons


def build_parser():
    parser = argparse.ArgumentParser(
        description="Visual, stream-friendly RAM observer for PyBoy."
    )
    parser.add_argument("rom", help="Path to the ROM file")
    parser.add_argument(
        "--window",
        choices=["SDL2", "OpenGL", "GLFW", "null"],
        default="null",
        help="PyBoy window mode",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=3,
        help="PyBoy window scale",
    )
    parser.add_argument(
        "--region",
        choices=tuple(REGIONS.keys()),
        default="WRAM",
        help="Initial memory region to display",
    )
    return parser


@dataclass
class CellState:
    value: int
    previous_value: int
    flash: float = 0.0
    recent: float = 0.0
    event: float = 0.0
    activity: float = 0.0
    history: deque | None = None
    last_changed_tick: int = -1

    def __post_init__(self):
        if self.history is None:
            self.history = deque([self.value], maxlen=HISTORY_LENGTH)

    def note_change(self, new_value, tick, event_active):
        self.previous_value = self.value
        self.value = new_value
        self.flash = 1.0
        self.recent = 1.0
        self.activity = min(self.activity + 1.0, 20.0)
        self.last_changed_tick = tick
        if event_active:
            self.event = 1.0
        self.history.append(new_value)

    def note_stable(self):
        self.history.append(self.value)

    def decay(self):
        self.flash *= FLASH_DECAY
        self.recent *= RECENT_DECAY
        self.event *= EVENT_DECAY
        self.activity *= RARITY_DECAY

    def decay_frames(self, frames):
        self.flash *= FLASH_DECAY ** frames
        self.recent *= RECENT_DECAY ** frames
        self.event *= EVENT_DECAY ** frames
        self.activity *= RARITY_DECAY ** frames

    @property
    def rarity_bias(self):
        # Low long-term activity means high rarity.
        return clamp(1.0 - (self.activity / 6.0), 0.0, 1.0)


class MemoryWatcherApp:
    def __init__(self, root, args):
        self.root = root
        self.args = args
        self.pyboy = PyBoy(
            args.rom,
            window=args.window,
            scale=args.scale,
            sound_volume=SOUND_VOLUME,
            sound_emulated=True,
            sound_sample_rate=SOUND_SAMPLE_RATE,
        )
        self.pyboy.set_emulation_speed(EMULATION_SPEED)

        self.region_name = args.region
        self.page_index = 0
        self.event_mark_tick = None
        self.global_tick = 0
        self.selected_address = None
        self.running = True
        self.bytes_per_row = 32
        self.visible_rows = 32
        self.screen_photo = None
        self.popout_photo = None
        self.preview_popout = None
        self.preview_popout_canvas = None
        self.frames_per_update = EMULATION_FRAMES_PER_UPDATE
        self.emulation_paused = False
        self.audio_muted = False
        self.controller = XboxController()
        self.desired_buttons = set()
        self.applied_buttons = set()
        self.previous_controller_buttons = set()
        self.pending_input_diff = None
        self.audio_stream = None
        self.audio_queue = queue.Queue(maxsize=32)
        self.audio_leftover = np.zeros((0, 2), dtype=np.float32)
        self.ui_render_counter = 0
        self.last_edit_prefill_address = None

        self.base_rgb = rgb(36, 29, 23)
        self.recent_rgb = rgb(242, 204, 143)
        self.hot_rgb = rgb(255, 93, 115)
        self.rare_rgb = rgb(128, 237, 153)
        self.event_rgb = rgb(251, 86, 7)

        self.region_states = {}
        for region_name, (start, end) in REGIONS.items():
            self.region_states[region_name] = {
                address: CellState(value=self.pyboy.memory[address], previous_value=self.pyboy.memory[address])
                for address in range(start, end)
            }

        self.root.title(WINDOW_TITLE)
        self.root.configure(bg=PANEL_BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build_layout()
        self.bind_keys()
        self.initialize_audio()
        self.root.focus_force()
        self.refresh_region_labels()
        self.redraw_full_page()
        self.update_loop()

    @property
    def region_start(self):
        return REGIONS[self.region_name][0]

    @property
    def region_end(self):
        return REGIONS[self.region_name][1]

    @property
    def region_size(self):
        return self.region_end - self.region_start

    @property
    def visible_bytes(self):
        return self.bytes_per_row * self.visible_rows

    @property
    def max_page_index(self):
        return max(0, math.ceil(self.region_size / self.visible_bytes) - 1)

    @property
    def page_start(self):
        return self.region_start + (self.page_index * self.visible_bytes)

    @property
    def current_states(self):
        return self.region_states[self.region_name]

    def build_layout(self):
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)
        self.root.grid_rowconfigure(1, weight=1)

        header = tk.Frame(self.root, bg=PANEL_BG, padx=14, pady=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(2, weight=1)

        self.title_label = tk.Label(
            header,
            text="Living Memory Map",
            font=("Consolas", 18, "bold"),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.region_label = tk.Label(
            header,
            text="",
            font=("Consolas", 11, "bold"),
            fg=ACCENT,
            bg=PANEL_BG,
        )
        self.region_label.grid(row=0, column=1, sticky="w", padx=(18, 0))

        self.status_label = tk.Label(
            header,
            text="Xbox controls game  |  P freeze  |  Q/E page  |  R switch region  |  M mark event  |  S snapshot",
            font=("Consolas", 10),
            fg=MUTED_TEXT,
            bg=PANEL_BG,
        )
        self.status_label.grid(row=0, column=2, sticky="e")

        self.canvas = tk.Canvas(
            self.root,
            width=CANVAS_WIDTH,
            height=CANVAS_HEIGHT,
            bg=CANVAS_BG,
            bd=0,
            highlightthickness=0,
            takefocus=0,
        )
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=(12, 8), pady=(0, 12))

        side_panel = tk.Frame(self.root, bg=PANEL_BG, padx=8, pady=8, width=240)
        side_panel.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 12))
        side_panel.grid_propagate(False)

        self.legend_label = tk.Label(
            side_panel,
            text=(
                "Bright = changed now\n"
                "Warm = changed recently\n"
                "Green = rare / interesting\n"
                "Orange = changed after mark\n"
                "\nKeyboard: Q/E page, R region, M mark"
            ),
            justify="left",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.legend_label.pack(anchor="nw", pady=(0, 12))

        self.event_label = tk.Label(
            side_panel,
            text="No event mark yet",
            justify="left",
            font=("Consolas", 10, "bold"),
            fg=EVENT_COLOR,
            bg=PANEL_BG,
        )
        self.event_label.pack(anchor="nw", pady=(0, 12))

        self.controller_label = tk.Label(
            side_panel,
            text="Controller: checking...",
            justify="left",
            font=("Consolas", 10),
            fg=MUTED_TEXT,
            bg=PANEL_BG,
        )
        self.controller_label.pack(anchor="nw", pady=(0, 12))

        self.input_diff_label = tk.Label(
            side_panel,
            text="Input diff: waiting for controller action",
            justify="left",
            font=("Consolas", 10),
            fg=MUTED_TEXT,
            bg=PANEL_BG,
            wraplength=220,
        )
        self.input_diff_label.pack(anchor="nw", pady=(0, 12))
        self.input_diff_lines = ["Waiting for controller-triggered diff..."]

        self.pause_label = tk.Label(
            side_panel,
            text="Emulation: running",
            justify="left",
            font=("Consolas", 10, "bold"),
            fg=ACCENT,
            bg=PANEL_BG,
        )
        self.pause_label.pack(anchor="nw", pady=(0, 12))

        self.mute_button = tk.Button(
            side_panel,
            text="Mute Audio",
            command=self.toggle_audio_mute,
            font=("Consolas", 9),
            fg=TEXT_COLOR,
            bg="#241d17",
            activeforeground=TEXT_COLOR,
            activebackground="#33281f",
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            takefocus=0,
        )
        self.mute_button.pack(anchor="nw", pady=(0, 12))

        self.snapshot_label = tk.Label(
            side_panel,
            text="Snapshot: none yet",
            justify="left",
            font=("Consolas", 10),
            fg=MUTED_TEXT,
            bg=PANEL_BG,
        )
        self.snapshot_label.pack(anchor="nw", pady=(0, 12))

        self.selection_label = tk.Label(
            side_panel,
            text="No byte selected",
            justify="left",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.selection_label.pack(anchor="nw", pady=(0, 8))

        self.edit_label = tk.Label(
            side_panel,
            text="Edit selected byte (hex)",
            justify="left",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.edit_label.pack(anchor="nw", pady=(0, 4))

        edit_row = tk.Frame(side_panel, bg=PANEL_BG)
        edit_row.pack(anchor="nw", pady=(0, 8))

        self.edit_value_var = tk.StringVar(value="")
        self.edit_entry = tk.Entry(
            edit_row,
            textvariable=self.edit_value_var,
            width=8,
            font=("Consolas", 10),
            bg="#0f0d0c",
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief="flat",
            bd=1,
            takefocus=0,
        )
        self.edit_entry.pack(side="left")

        self.apply_edit_button = tk.Button(
            edit_row,
            text="Apply",
            command=self.apply_selected_memory_edit,
            font=("Consolas", 9),
            fg=TEXT_COLOR,
            bg="#241d17",
            activeforeground=TEXT_COLOR,
            activebackground="#33281f",
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            takefocus=0,
        )
        self.apply_edit_button.pack(side="left", padx=(8, 0))

        self.sparkline = tk.Canvas(
            side_panel,
            width=220,
            height=128,
            bg="#0f0d0c",
            bd=0,
            highlightthickness=1,
            highlightbackground="#3a2e24",
            takefocus=0,
        )
        self.sparkline.pack(anchor="nw")

        self.preview_label = tk.Label(
            side_panel,
            text="Live game view",
            justify="left",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.preview_label.pack(anchor="nw", pady=(14, 6))

        self.speed_label = tk.Label(
            side_panel,
            text="Playback speed",
            justify="left",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.speed_label.pack(anchor="nw", pady=(0, 4))

        speed_row = tk.Frame(side_panel, bg=PANEL_BG)
        speed_row.pack(anchor="nw", pady=(0, 8))

        self.slower_button = tk.Button(
            speed_row,
            text="-",
            command=lambda: self.adjust_speed(-1),
            font=("Consolas", 10, "bold"),
            fg=TEXT_COLOR,
            bg="#241d17",
            activeforeground=TEXT_COLOR,
            activebackground="#33281f",
            relief="flat",
            bd=0,
            width=3,
            padx=4,
            pady=4,
            takefocus=0,
        )
        self.slower_button.pack(side="left")

        self.speed_value_label = tk.Label(
            speed_row,
            text="",
            justify="center",
            font=("Consolas", 10, "bold"),
            fg=ACCENT,
            bg=PANEL_BG,
            width=12,
        )
        self.speed_value_label.pack(side="left", padx=6)

        self.faster_button = tk.Button(
            speed_row,
            text="+",
            command=lambda: self.adjust_speed(1),
            font=("Consolas", 10, "bold"),
            fg=TEXT_COLOR,
            bg="#241d17",
            activeforeground=TEXT_COLOR,
            activebackground="#33281f",
            relief="flat",
            bd=0,
            width=3,
            padx=4,
            pady=4,
            takefocus=0,
        )
        self.faster_button.pack(side="left")

        self.popout_button = tk.Button(
            side_panel,
            text="Pop Out Game View",
            command=self.toggle_preview_popout,
            font=("Consolas", 9),
            fg=TEXT_COLOR,
            bg="#241d17",
            activeforeground=TEXT_COLOR,
            activebackground="#33281f",
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            takefocus=0,
        )
        self.popout_button.pack(anchor="nw", pady=(0, 8))

        self.preview_canvas = tk.Canvas(
            side_panel,
            width=220,
            height=198,
            bg="#0f0d0c",
            bd=0,
            highlightthickness=1,
            highlightbackground="#3a2e24",
            takefocus=0,
        )
        self.preview_canvas.pack(anchor="nw")

        self.diff_panel_label = tk.Label(
            side_panel,
            text="Recent action diff",
            justify="left",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        self.diff_panel_label.pack(anchor="nw", pady=(12, 6))

        self.diff_panel = tk.Text(
            side_panel,
            width=28,
            height=10,
            font=("Consolas", 9),
            bg="#0f0d0c",
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief="flat",
            bd=1,
            highlightthickness=1,
            highlightbackground="#3a2e24",
            wrap="none",
        )
        self.diff_panel.pack(anchor="nw")
        self.diff_panel.insert("1.0", self.input_diff_lines[0])
        self.diff_panel.configure(state="disabled")

        self.footer_label = tk.Label(
            side_panel,
            text="Page 1/1",
            justify="left",
            font=("Consolas", 10),
            fg=MUTED_TEXT,
            bg=PANEL_BG,
        )
        self.footer_label.pack(anchor="nw", pady=(12, 0))

        self.cell_items = {}
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.refresh_speed_label()

    def bind_keys(self):
        self.root.bind_all("<KeyPress-q>", self.handle_previous_page_key)
        self.root.bind_all("<KeyPress-Q>", self.handle_previous_page_key)
        self.root.bind_all("<KeyPress-e>", self.handle_next_page_key)
        self.root.bind_all("<KeyPress-E>", self.handle_next_page_key)
        self.root.bind_all("<KeyPress-Tab>", self.handle_next_page_key)
        self.root.bind_all("<KeyPress-BackSpace>", self.handle_previous_page_key)
        self.root.bind_all("<KeyPress-r>", self.toggle_region)
        self.root.bind_all("<KeyPress-R>", self.toggle_region)
        self.root.bind_all("<KeyPress-m>", self.mark_event)
        self.root.bind_all("<KeyPress-M>", self.mark_event)
        self.root.bind_all("<KeyPress-p>", self.toggle_emulation_pause)
        self.root.bind_all("<KeyPress-P>", self.toggle_emulation_pause)
        self.root.bind_all("<KeyPress-s>", self.save_snapshot)
        self.root.bind_all("<KeyPress-S>", self.save_snapshot)
        self.root.bind_all("<KeyPress-bracketleft>", self.previous_page)
        self.root.bind_all("<KeyPress-bracketright>", self.next_page)
        self.root.bind_all("<Escape>", lambda _event: self.on_close())

    def refresh_region_labels(self):
        page_text = (
            f"Page {self.page_index + 1}/{self.max_page_index + 1}  |  "
            f"{self.bytes_per_row}x{self.visible_rows}  |  {self.visible_bytes} bytes"
        )
        region_text = (
            f"{self.region_name} "
            f"{self.page_start:04X}-{min(self.page_start + self.visible_bytes, self.region_end):04X}"
        )
        self.region_label.configure(text=region_text)
        self.footer_label.configure(text=page_text)

        if self.event_mark_tick is None:
            self.event_label.configure(text="No event mark yet")
        else:
            age = self.global_tick - self.event_mark_tick
            self.event_label.configure(
                text=f"Event mark at tick {self.event_mark_tick}\nAge: {age} ticks"
            )
        controller_text = "Controller: connected" if self.controller.connected else "Controller: not detected"
        controller_color = ACCENT if self.controller.connected else MUTED_TEXT
        self.controller_label.configure(text=controller_text, fg=controller_color)
        pause_text = "Emulation: paused" if self.emulation_paused else "Emulation: running"
        pause_color = EVENT_COLOR if self.emulation_paused else ACCENT
        self.pause_label.configure(text=pause_text, fg=pause_color)

    def refresh_speed_label(self):
        self.speed_value_label.configure(
            text=f"{self.frames_per_update} frames/update"
        )

    def adjust_speed(self, delta):
        self.frames_per_update = clamp(self.frames_per_update + delta, 1, 8)
        self.refresh_speed_label()
        self.root.focus_force()

    def initialize_audio(self):
        if sd is None:
            self.snapshot_label.configure(text="Audio: backend unavailable", fg=MUTED_TEXT)
            return
        try:
            def audio_callback(outdata, frames, _time, _status):
                written = 0

                if len(self.audio_leftover):
                    take = min(frames, len(self.audio_leftover))
                    outdata[:take] = self.audio_leftover[:take]
                    self.audio_leftover = self.audio_leftover[take:]
                    written += take

                while written < frames:
                    try:
                        chunk = self.audio_queue.get_nowait()
                    except queue.Empty:
                        outdata[written:] = 0
                        break

                    take = min(frames - written, len(chunk))
                    outdata[written:written + take] = chunk[:take]
                    written += take

                    if take < len(chunk):
                        self.audio_leftover = chunk[take:]
                        break

            self.audio_stream = sd.OutputStream(
                samplerate=SOUND_SAMPLE_RATE,
                channels=2,
                dtype="float32",
                blocksize=0,
                latency="low",
                callback=audio_callback,
            )
            self.audio_stream.start()
        except Exception:
            self.audio_stream = None
            self.snapshot_label.configure(text="Audio: failed to start", fg=MUTED_TEXT)

    def play_audio_frame(self):
        if self.audio_stream is None or self.audio_muted:
            return
        try:
            frame_count = self.pyboy.sound.raw_buffer_length // 2
            samples = self.pyboy.sound.raw_ndarray[:frame_count].astype("float32") / 128.0
            if len(samples):
                try:
                    self.audio_queue.put_nowait(samples.copy())
                except queue.Full:
                    try:
                        self.audio_queue.get_nowait()
                    except queue.Empty:
                        pass
                    self.audio_queue.put_nowait(samples.copy())
        except Exception:
            pass

    def handle_next_page_key(self, _event=None):
        return self.next_page()

    def handle_previous_page_key(self, _event=None):
        return self.previous_page()

    def toggle_emulation_pause(self, _event=None):
        self.emulation_paused = not self.emulation_paused
        self.refresh_region_labels()
        self.root.focus_force()
        return "break"

    def toggle_audio_mute(self):
        self.audio_muted = not self.audio_muted
        self.mute_button.configure(
            text="Unmute Audio" if self.audio_muted else "Mute Audio"
        )
        self.root.focus_force()

    def capture_input_diff_snapshot(self):
        return {
            region_name: np.array(
                [self.pyboy.memory[address] for address in range(start, end)],
                dtype=np.int16,
            )
            for region_name, (start, end) in REGIONS.items()
        }

    def summarize_controller_diff(self, before_snapshot, after_snapshot, buttons):
        changed_rows = []
        for region_name, (start, _end) in REGIONS.items():
            before = before_snapshot[region_name]
            after = after_snapshot[region_name]
            changed_indices = np.flatnonzero(before != after)
            for index in changed_indices:
                before_value = int(before[index])
                after_value = int(after[index])
                changed_rows.append(
                    (
                        region_name,
                        start + int(index),
                        before_value,
                        after_value,
                        after_value - before_value,
                    )
                )

        changed_rows.sort(key=lambda item: abs(item[4]), reverse=True)
        if not changed_rows:
            return [f"{buttons}  no changes"]

        lines = [f"{buttons}  changed={len(changed_rows)}"]
        for region_name, address, before_value, after_value, delta in changed_rows[:6]:
            lines.append(
                f"{region_name[0]} 0x{address:04X} {before_value:02X}->{after_value:02X} {delta:+d}"
            )
        return lines

    def update_input_diff_panel(self, lines):
        self.input_diff_lines = lines
        self.input_diff_label.configure(text=lines[0], fg=ACCENT)
        self.diff_panel.configure(state="normal")
        self.diff_panel.delete("1.0", "end")
        self.diff_panel.insert("1.0", "\n".join(lines))
        self.diff_panel.configure(state="disabled")

    def apply_selected_memory_edit(self):
        if self.selected_address is None:
            self.snapshot_label.configure(text="Edit failed:\nno byte selected", fg=EVENT_COLOR)
            self.root.focus_force()
            return

        raw_value = self.edit_value_var.get().strip().replace("0x", "").replace("0X", "")
        if not raw_value:
            self.snapshot_label.configure(text="Edit failed:\nenter a hex byte", fg=EVENT_COLOR)
            self.root.focus_force()
            return

        try:
            value = int(raw_value, 16)
        except ValueError:
            self.snapshot_label.configure(text="Edit failed:\ninvalid hex", fg=EVENT_COLOR)
            self.root.focus_force()
            return

        if not 0 <= value <= 0xFF:
            self.snapshot_label.configure(text="Edit failed:\nbyte must be 00-FF", fg=EVENT_COLOR)
            self.root.focus_force()
            return

        try:
            self.pyboy.memory[self.selected_address] = value
            for region_name, states in self.region_states.items():
                if self.selected_address in states:
                    state = states[self.selected_address]
                    state.previous_value = state.value
                    state.value = value
                    state.history.append(value)
                    state.flash = 1.0
                    state.recent = 1.0
                    break
            self.edit_value_var.set(f"{value:02X}")
            self.last_edit_prefill_address = self.selected_address
            self.update_visible_page()
            self.draw_selection_panel()
            self.snapshot_label.configure(
                text=f"Wrote 0x{value:02X} to\n0x{self.selected_address:04X}",
                fg=ACCENT,
            )
        except Exception:
            self.snapshot_label.configure(text="Edit failed:\naddress not writable", fg=EVENT_COLOR)

        self.root.focus_force()

    def update_density(self):
        canvas_width = max(self.canvas.winfo_width(), CANVAS_WIDTH)
        canvas_height = max(self.canvas.winfo_height(), CANVAS_HEIGHT)

        usable_width = max(0, canvas_width - GRID_ORIGIN_X - 14)
        raw_columns = usable_width // CELL_WIDTH
        snapped_columns = max(MIN_BYTES_PER_ROW, raw_columns)
        snapped_columns = min(snapped_columns, MAX_BYTES_PER_ROW)
        snapped_columns = max(MIN_BYTES_PER_ROW, (snapped_columns // 16) * 16)

        usable_height = max(0, canvas_height - GRID_ORIGIN_Y - 12)
        rows = max(MIN_VISIBLE_ROWS, usable_height // CELL_HEIGHT)

        layout_changed = (
            snapped_columns != self.bytes_per_row or rows != self.visible_rows
        )
        self.bytes_per_row = snapped_columns
        self.visible_rows = rows
        self.page_index = min(self.page_index, self.max_page_index)
        return layout_changed

    def redraw_full_page(self):
        self.update_density()
        self.canvas.delete("all")
        self.cell_items.clear()

        self.canvas.create_text(
            ADDRESS_TEXT_X,
            HEADER_TEXT_Y,
            text="Addr",
            fill=MUTED_TEXT,
            anchor="nw",
            font=("Consolas", 9, "bold"),
        )

        for column in range(self.bytes_per_row):
            self.canvas.create_text(
                GRID_ORIGIN_X + (column * CELL_WIDTH) + ((CELL_WIDTH - CELL_GAP) / 2),
                HEADER_TEXT_Y,
                text=f"{column:02X}",
                fill=MUTED_TEXT,
                anchor="n",
                font=("Consolas", 8, "bold"),
            )

        for row in range(self.visible_rows):
            row_address = self.page_start + (row * self.bytes_per_row)
            if row_address >= self.region_end:
                break

            self.canvas.create_text(
                ADDRESS_TEXT_X,
                GRID_ORIGIN_Y + (row * CELL_HEIGHT) + 9,
                text=f"{row_address:04X}",
                fill=MUTED_TEXT,
                anchor="w",
                font=("Consolas", 9),
            )

            for column in range(self.bytes_per_row):
                address = row_address + column
                if address >= self.region_end:
                    continue

                x1 = GRID_ORIGIN_X + (column * CELL_WIDTH)
                y1 = GRID_ORIGIN_Y + (row * CELL_HEIGHT)
                x2 = x1 + CELL_WIDTH - CELL_GAP
                y2 = y1 + CELL_HEIGHT - CELL_GAP

                rect_id = self.canvas.create_rectangle(
                    x1,
                    y1,
                    x2,
                    y2,
                    fill=GRID_BASE,
                    outline="#33281f",
                    width=1,
                )
                text_id = self.canvas.create_text(
                    (x1 + x2) / 2,
                    (y1 + y2) / 2,
                    text=f"{self.current_states[address].value:02X}",
                    fill=TEXT_COLOR,
                    font=("Consolas", 8, "bold"),
                )
                self.cell_items[address] = (rect_id, text_id, (x1, y1, x2, y2))

        self.draw_selection_panel()

    def page_addresses(self):
        for offset in range(self.visible_bytes):
            address = self.page_start + offset
            if address >= self.region_end:
                break
            yield address

    def update_loop(self):
        if not self.running:
            return

        if not self.emulation_paused:
            self.desired_buttons = self.controller.poll_buttons()
            new_buttons = self.desired_buttons - self.previous_controller_buttons
            if new_buttons and self.pending_input_diff is None:
                self.pending_input_diff = {
                    "buttons": "+".join(sorted(new_buttons)),
                    "tick": self.global_tick,
                    "snapshot": self.capture_input_diff_snapshot(),
                }
            effective_buttons = self.desired_buttons

            for button in effective_buttons - self.applied_buttons:
                self.pyboy.button_press(button)
            for button in self.applied_buttons - effective_buttons:
                self.pyboy.button_release(button)
            self.applied_buttons = set(effective_buttons)

            if not self.pyboy.tick(self.frames_per_update, True):
                self.on_close()
                return

            self.global_tick += self.frames_per_update
            self.play_audio_frame()
            event_active = (
                self.event_mark_tick is not None
                and (self.global_tick - self.event_mark_tick) <= EVENT_WINDOW_TICKS
            )

            for region_name, (start, end) in REGIONS.items():
                region_states = self.region_states[region_name]
                for address in range(start, end):
                    state = region_states[address]
                    value = self.pyboy.memory[address]
                    if value != state.value:
                        state.note_change(value, self.global_tick, event_active)
                    else:
                        state.note_stable()
                    state.decay_frames(self.frames_per_update)

            if (
                self.pending_input_diff is not None
                and (self.global_tick - self.pending_input_diff["tick"]) >= INPUT_DIFF_WINDOW_FRAMES
            ):
                after_snapshot = self.capture_input_diff_snapshot()
                self.update_input_diff_panel(
                    self.summarize_controller_diff(
                        self.pending_input_diff["snapshot"],
                        after_snapshot,
                        self.pending_input_diff["buttons"],
                    )
                )
                self.pending_input_diff = None
        else:
            if self.applied_buttons:
                for button in list(self.applied_buttons):
                    self.pyboy.button_release(button)
                self.applied_buttons.clear()
            self.desired_buttons = set()

        self.previous_controller_buttons = set(self.desired_buttons)
        self.ui_render_counter = (self.ui_render_counter + 1) % UI_RENDER_DIVISOR
        if self.ui_render_counter == 0:
            self.update_visible_page()
            self.refresh_region_labels()
            self.draw_selection_panel()
            self.draw_preview()

        self.root.after(UI_REFRESH_MS, self.update_loop)

    def update_visible_page(self):
        for address in self.page_addresses():
            rect_id, text_id, _bounds = self.cell_items[address]
            state = self.current_states[address]

            # Rare changes lean green, frequent changes lean red. Recent changes
            # add warmth, and event-linked bytes punch orange.
            color = self.base_rgb
            if state.recent > 0.01:
                recent_mix = max(state.recent, state.flash)
                rare_target = self.rare_rgb if state.rarity_bias > 0.55 else self.hot_rgb
                target = rare_target
                if state.event > 0.12:
                    target = self.event_rgb
                color = tuple(
                    int(component)
                    for component in [
                        color[index] + (target[index] - color[index]) * recent_mix
                        for index in range(3)
                    ]
                )
            fill = hex_color(*color)

            outline = CLICK_OUTLINE if address == self.selected_address else "#33281f"
            outline_width = 2 if address == self.selected_address else 1
            if state.event > 0.25:
                outline = EVENT_COLOR
                outline_width = 2

            self.canvas.itemconfigure(rect_id, fill=fill, outline=outline, width=outline_width)
            self.canvas.itemconfigure(text_id, text=f"{state.value:02X}")

    def draw_selection_panel(self):
        self.sparkline.delete("all")

        if self.selected_address is None:
            self.selection_label.configure(text="No byte selected")
            self.edit_value_var.set("")
            self.last_edit_prefill_address = None
            self.sparkline.create_text(
                140,
                80,
                text="Click a byte to track it",
                fill=MUTED_TEXT,
                font=("Consolas", 12),
            )
            return

        state = self.current_states.get(self.selected_address)
        if state is None:
            self.selection_label.configure(text="Selection is outside this region")
            return

        self.selection_label.configure(
            text=(
                f"Selected: 0x{self.selected_address:04X}\n"
                f"Value: {state.value:3d} / 0x{state.value:02X}\n"
                f"Prev:  {state.previous_value:3d} / 0x{state.previous_value:02X}\n"
                f"Rarity bias: {state.rarity_bias:0.2f}"
            )
        )
        if self.last_edit_prefill_address != self.selected_address:
            self.edit_value_var.set(f"{state.value:02X}")
            self.last_edit_prefill_address = self.selected_address

        values = list(state.history)
        if len(values) < 2:
            return

        width = 220
        height = 128
        padding = 14
        self.sparkline.create_rectangle(
            padding,
            padding,
            width - padding,
            height - padding,
            outline="#3a2e24",
            width=1,
        )

        min_value = min(values)
        max_value = max(values)
        if min_value == max_value:
            max_value += 1

        points = []
        usable_width = width - (padding * 2)
        usable_height = height - (padding * 2)
        for index, value in enumerate(values):
            x = padding + (index / (len(values) - 1)) * usable_width
            normalized = (value - min_value) / (max_value - min_value)
            y = height - padding - (normalized * usable_height)
            points.extend([x, y])

        self.sparkline.create_line(points, fill=ACCENT, width=2, smooth=True)
        self.sparkline.create_text(
            padding,
            4,
            text=f"{min_value}",
            fill=MUTED_TEXT,
            anchor="nw",
            font=("Consolas", 9),
        )
        self.sparkline.create_text(
            width - padding,
            4,
            text=f"{max_value}",
            fill=MUTED_TEXT,
            anchor="ne",
            font=("Consolas", 9),
        )

    def draw_preview(self):
        self.preview_canvas.delete("all")

        image = self.pyboy.screen.image.copy()
        image = image.resize((220, 198), resample=0)
        self.screen_photo = ImageTk.PhotoImage(image)
        self.preview_canvas.create_image(0, 0, image=self.screen_photo, anchor="nw")

        if self.preview_popout is not None and self.preview_popout_canvas is not None:
            popout_image = image.resize((480, 432), resample=0)
            self.popout_photo = ImageTk.PhotoImage(popout_image)
            self.preview_popout_canvas.delete("all")
            self.preview_popout_canvas.create_image(
                0, 0, image=self.popout_photo, anchor="nw"
            )

    def toggle_preview_popout(self):
        if self.preview_popout is not None:
            self.close_preview_popout()
            return

        self.preview_popout = tk.Toplevel(self.root)
        self.preview_popout.title("Game Boy Preview")
        self.preview_popout.configure(bg=PANEL_BG)
        self.preview_popout.protocol("WM_DELETE_WINDOW", self.close_preview_popout)
        self.preview_popout.bind("<FocusIn>", lambda _event: self.root.focus_force())

        label = tk.Label(
            self.preview_popout,
            text="Detached game view",
            font=("Consolas", 10),
            fg=TEXT_COLOR,
            bg=PANEL_BG,
        )
        label.pack(anchor="nw", padx=10, pady=(10, 6))

        self.preview_popout_canvas = tk.Canvas(
            self.preview_popout,
            width=480,
            height=432,
            bg="#0f0d0c",
            bd=0,
            highlightthickness=1,
            highlightbackground="#3a2e24",
            takefocus=0,
        )
        self.preview_popout_canvas.pack(anchor="nw", padx=10, pady=(0, 10))
        self.popout_button.configure(text="Dock Game View")
        self.draw_preview()
        self.root.focus_force()

    def close_preview_popout(self):
        if self.preview_popout is not None:
            self.preview_popout.destroy()
        self.preview_popout = None
        self.preview_popout_canvas = None
        self.popout_photo = None
        self.popout_button.configure(text="Pop Out Game View")
        self.root.focus_force()

    def on_canvas_click(self, event):
        self.root.focus_force()
        clicked = self.canvas.find_closest(event.x, event.y)
        if not clicked:
            return

        target_id = clicked[0]
        for address, (rect_id, text_id, _bounds) in self.cell_items.items():
            if target_id in (rect_id, text_id):
                self.selected_address = address
                self.update_visible_page()
                self.draw_selection_panel()
                return

    def on_canvas_resize(self, _event):
        if self.update_density():
            self.refresh_region_labels()
            self.redraw_full_page()

    def toggle_region(self, _event=None):
        names = list(REGIONS.keys())
        current_index = names.index(self.region_name)
        self.region_name = names[(current_index + 1) % len(names)]
        self.page_index = 0
        self.selected_address = None
        self.edit_value_var.set("")
        self.last_edit_prefill_address = None
        self.refresh_region_labels()
        self.redraw_full_page()
        return "break"

    def previous_page(self, _event=None):
        if self.page_index > 0:
            self.page_index -= 1
            self.selected_address = None
            self.edit_value_var.set("")
            self.last_edit_prefill_address = None
            self.refresh_region_labels()
            self.redraw_full_page()
        return "break"

    def next_page(self, _event=None):
        if self.page_index < self.max_page_index:
            self.page_index += 1
            self.selected_address = None
            self.edit_value_var.set("")
            self.last_edit_prefill_address = None
            self.refresh_region_labels()
            self.redraw_full_page()
        return "break"

    def mark_event(self, _event=None):
        self.event_mark_tick = self.global_tick
        self.refresh_region_labels()
        return "break"

    def save_snapshot(self, _event=None):
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_path = SNAPSHOT_DIR / f"memory_snapshot_{timestamp}"

        self.root.update_idletasks()
        left = self.root.winfo_rootx()
        top = self.root.winfo_rooty()
        right = left + self.root.winfo_width()
        bottom = top + self.root.winfo_height()

        screenshot = ImageGrab.grab(bbox=(left, top, right, bottom))
        screenshot_path = base_path.with_suffix(".png")
        screenshot.save(screenshot_path)

        memory_dump = {
            region_name: {
                f"0x{address:04X}": self.region_states[region_name][address].value
                for address in range(start, end)
            }
            for region_name, (start, end) in REGIONS.items()
        }

        metadata = {
            "timestamp": timestamp,
            "tick": self.global_tick,
            "region": self.region_name,
            "page_index": self.page_index,
            "page_start": f"0x{self.page_start:04X}",
            "visible_bytes": self.visible_bytes,
            "selected_address": (
                f"0x{self.selected_address:04X}"
                if self.selected_address is not None
                else None
            ),
            "controller_connected": self.controller.connected,
            "frames_per_update": self.frames_per_update,
            "memory": memory_dump,
        }

        metadata_path = base_path.with_suffix(".json")
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        self.snapshot_label.configure(
            text=f"Snapshot saved:\n{screenshot_path.name}",
            fg=ACCENT,
        )
        self.root.focus_force()
        return "break"

    def on_close(self):
        self.running = False
        if self.preview_popout is not None:
            self.close_preview_popout()
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
        try:
            self.pyboy.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    args = build_parser().parse_args()

    root = tk.Tk()
    app = MemoryWatcherApp(root, args)
    try:
        root.mainloop()
    finally:
        if app.running:
            app.on_close()


if __name__ == "__main__":
    sys.exit(main())
