"""
Doors Death Macro — макрос для фарма смертей в Roblox DOORS (Windows).

Цикл по умолчанию (cycle.txt):
  выход из лифта -> взять ключ -> открыть дверь (записанный маршрут route.txt)
  -> смерть от Eyes (модификатор) -> нажать «Возродиться» -> снова смерть
  -> «Играть снова» -> загрузка -> повтор.

Горячие клавиши (работают, даже когда активно окно Roblox):
  F6 — старт / стоп цикла
  F7 — начать / закончить запись маршрута
  F8 — запомнить точку экрана (позиция курсора + цвет) под выбранным именем
  F9 — аварийная остановка всего
"""

import ctypes
import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

if sys.platform != "win32":
    sys.exit("Этот макрос работает только на Windows (там, где запущен Roblox).")

try:
    from pynput import keyboard, mouse
except ImportError:
    sys.exit("Не установлен pynput. Выполни: pip install pynput")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG = {
    "points": {},
    "tolerance": 30,
    "turn_scale": 1.0,
    "window_title": "Roblox",
    "cycle_file": "cycle.txt",
    "route_file": "route.txt",
}

# --------------------------------------------------------------------------
# WinAPI: ввод через SendInput со скан-кодами (Roblox игнорирует обычные VK)
# --------------------------------------------------------------------------

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
try:
    ctypes.WinDLL("winmm").timeBeginPeriod(1)
except Exception:
    pass

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSE_FLAGS = {
    ("left", True): 0x0002, ("left", False): 0x0004,
    ("right", True): 0x0008, ("right", False): 0x0010,
    ("middle", True): 0x0020, ("middle", False): 0x0040,
}
ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.GetDC.restype = wintypes.HDC
gdi32.GetPixel.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
gdi32.GetPixel.restype = wintypes.DWORD

# Имена клавиш -> виртуальные коды
VK = {"space": 0x20, "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "backspace": 0x08,
      "shift": 0xA0, "rshift": 0xA1, "ctrl": 0xA2, "rctrl": 0xA3,
      "alt": 0xA4, "ralt": 0xA5, "capslock": 0x14,
      "arrow_left": 0x25, "arrow_up": 0x26, "arrow_right": 0x27, "arrow_down": 0x28,
      "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
      "pageup": 0x21, "pagedown": 0x22}
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)
for _i in range(1, 13):
    VK[f"f{_i}"] = 0x6F + _i
VK_NAME = {v: k for k, v in VK.items()}
VK_NAME.update({0x10: "shift", 0x11: "ctrl", 0x12: "alt"})  # обобщённые модификаторы
EXTENDED_VK = {0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0xA3, 0xA5}
HOTKEY_VK = {0x75, 0x76, 0x77, 0x78}  # F6..F9 — не попадают в запись


def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def key_event(name, down):
    vk = VK.get(name.lower())
    if vk is None:
        raise ValueError(f"неизвестная клавиша «{name}»")
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
    if vk in EXTENDED_VK:
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(0, scan, flags, 0, 0)
    _send(inp)


def mouse_button(button, down):
    flag = MOUSE_FLAGS.get((button, down))
    if flag is None:
        raise ValueError(f"неизвестная кнопка мыши «{button}»")
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
    _send(inp)


def mouse_rel(dx, dy):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(int(dx), int(dy), 0, MOUSEEVENTF_MOVE, 0, 0)
    _send(inp)


def cursor_pos():
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def set_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))
    # Roblox замечает курсор только после реального движения
    mouse_rel(1, 0)
    mouse_rel(-1, 0)


def pixel_color(x, y):
    hdc = user32.GetDC(0)
    c = gdi32.GetPixel(hdc, int(x), int(y))
    user32.ReleaseDC(0, hdc)
    return (c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % rgb


def focus_window(title):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows не всегда даёт перехватить фокус; нажатие Alt снимает это ограничение
    key_event("alt", True)
    key_event("alt", False)
    return bool(user32.SetForegroundWindow(hwnd))


# --------------------------------------------------------------------------
# Конфиг
# --------------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            print("Не удалось прочитать config.json:", e)
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(BASE_DIR, path)


# --------------------------------------------------------------------------
# Интерпретатор сценариев
# --------------------------------------------------------------------------

class StopMacro(Exception):
    pass


class ScriptError(Exception):
    pass


class Player:
    """Выполняет текстовый сценарий (см. README / cycle.txt)."""

    def __init__(self, cfg, log, on_count):
        self.cfg = cfg
        self.log = log
        self.on_count = on_count
        self.stop_event = threading.Event()
        self.held_keys = set()
        self.held_buttons = set()
        self.clock = 0.0

    # --- время -----------------------------------------------------------
    def _check(self):
        if self.stop_event.is_set():
            raise StopMacro()

    def _sleep_until(self, deadline):
        while True:
            self._check()
            left = deadline - time.perf_counter()
            if left <= 0:
                return
            time.sleep(min(left, 0.01))

    def wait(self, seconds):
        # Время копится от общего «часов» сценария, поэтому записанный
        # маршрут не съезжает из-за накладных расходов на отправку ввода.
        self.clock += seconds
        self._sleep_until(self.clock)

    def resync(self):
        self.clock = time.perf_counter()

    # --- точки и пиксели -------------------------------------------------
    def point(self, ref):
        name = ref.lstrip("@")
        p = self.cfg["points"].get(name)
        if not p:
            raise ScriptError(f"точка «{name}» не задана — запомни её в окне макроса")
        return p

    def xy(self, args):
        if args and args[0].startswith("@"):
            p = self.point(args[0])
            return p["x"], p["y"], args[1:]
        if len(args) >= 2 and args[0].lstrip("-").isdigit():
            return int(args[0]), int(args[1]), args[2:]
        return None, None, args

    def pixel_matches(self, ref):
        p = self.point(ref)
        want = hex_to_rgb(p["color"])
        got = pixel_color(p["x"], p["y"])
        tol = int(self.cfg.get("tolerance", 30))
        return all(abs(a - b) <= tol for a, b in zip(want, got))

    def wait_pixel(self, ref, timeout, present=True):
        deadline = time.perf_counter() + timeout
        try:
            while time.perf_counter() < deadline:
                self._check()
                if self.pixel_matches(ref) == present:
                    return True
                time.sleep(0.05)
            return False
        finally:
            self.resync()

    # --- ввод ------------------------------------------------------------
    def down(self, key):
        key_event(key, True)
        self.held_keys.add(key)

    def up(self, key):
        key_event(key, False)
        self.held_keys.discard(key)

    def mdown(self, button):
        mouse_button(button, True)
        self.held_buttons.add(button)

    def mup(self, button):
        mouse_button(button, False)
        self.held_buttons.discard(button)

    def release_all(self):
        for k in list(self.held_keys):
            try:
                key_event(k, False)
            except Exception:
                pass
        for b in list(self.held_buttons):
            try:
                mouse_button(b, False)
            except Exception:
                pass
        self.held_keys.clear()
        self.held_buttons.clear()

    # --- запуск ----------------------------------------------------------
    def run_file(self, path):
        self.stop_event.clear()
        self.resync()
        try:
            self.execute(path, depth=0)
            self.log("Сценарий завершён.")
        except StopMacro:
            self.log("Остановлено.")
        except ScriptError as e:
            self.log(f"ОШИБКА: {e}")
        finally:
            self.release_all()

    def execute(self, path, depth):
        if depth > 10:
            raise ScriptError("слишком глубокая вложенность run")
        full = resolve(path)
        if not os.path.exists(full):
            raise ScriptError(f"файл {path} не найден")
        with open(full, encoding="utf-8") as f:
            raw = f.read().splitlines()
        lines, labels = [], {}
        for no, text in enumerate(raw, 1):
            text = text.split("#", 1)[0].strip()
            if not text:
                continue
            parts = text.split()
            if parts[0].lower() == "label":
                if len(parts) < 2:
                    raise ScriptError(f"{path}:{no}: label без имени")
                labels[parts[1]] = len(lines)
            lines.append((no, parts[0].lower(), parts[1:]))

        pc = 0
        while pc < len(lines):
            self._check()
            no, cmd, args = lines[pc]
            pc += 1
            try:
                jump = self.command(cmd, args, depth)
            except (ValueError, IndexError) as e:
                raise ScriptError(f"{path}:{no}: {cmd} {' '.join(args)} — {e}")
            except ScriptError as e:
                raise ScriptError(f"{path}:{no}: {e}")
            if jump == "__stop__":
                return
            if jump is not None:
                if jump not in labels:
                    raise ScriptError(f"{path}:{no}: нет метки «{jump}»")
                pc = labels[jump]

    def command(self, cmd, a, depth):
        if cmd == "label":
            return None
        if cmd == "wait":
            lo = float(a[0])
            hi = float(a[1]) if len(a) > 1 else lo
            self.wait(random.uniform(lo, hi))
        elif cmd == "down":
            self.down(a[0])
        elif cmd == "up":
            self.up(a[0])
        elif cmd == "press":
            self.down(a[0])
            self.wait(float(a[1]) if len(a) > 1 else 0.05)
            self.up(a[0])
        elif cmd == "turn":
            scale = float(self.cfg.get("turn_scale", 1.0))
            dx, dy = float(a[0]) * scale, float(a[1]) * scale
            dur = float(a[2]) if len(a) > 2 else 0.0
            steps = max(1, int(dur / 0.01)) if dur > 0 else 1
            sx = sy = 0
            for i in range(1, steps + 1):
                tx, ty = round(dx * i / steps), round(dy * i / steps)
                mouse_rel(tx - sx, ty - sy)
                sx, sy = tx, ty
                if dur > 0:
                    self.wait(dur / steps)
        elif cmd == "move":
            x, y, _ = self.xy(a)
            if x is None:
                raise ValueError("нужны координаты или @точка")
            set_cursor(x, y)
        elif cmd in ("click", "rclick"):
            button = "right" if cmd == "rclick" else "left"
            x, y, rest = self.xy(a)
            if x is not None:
                set_cursor(x, y)
                self.wait(0.03)
            if rest and rest[0] in ("left", "right", "middle"):
                button = rest[0]
            self.mdown(button)
            self.wait(0.05)
            self.mup(button)
        elif cmd == "mdown":
            self.mdown(a[0] if a else "left")
        elif cmd == "mup":
            self.mup(a[0] if a else "left")
        elif cmd in ("wait_pixel", "wait_no_pixel"):
            timeout = float(a[1]) if len(a) > 1 else 60
            ok = self.wait_pixel(a[0], timeout, present=(cmd == "wait_pixel"))
            if not ok:
                self.log(f"{cmd} {a[0]}: не дождались за {timeout:g} с")
                if len(a) > 2:
                    return a[2]
        elif cmd == "if_pixel":
            if self.pixel_matches(a[0]):
                return a[1]
        elif cmd == "if_no_pixel":
            if not self.pixel_matches(a[0]):
                return a[1]
        elif cmd == "goto":
            return a[0]
        elif cmd == "run":
            self.execute(" ".join(a), depth + 1)
        elif cmd == "count":
            self.on_count()
        elif cmd == "focus":
            title = " ".join(a) or self.cfg.get("window_title", "Roblox")
            if not focus_window(title):
                self.log(f"Окно «{title}» не найдено/не активировано")
            self.wait(0.3)
        elif cmd == "log":
            self.log(" ".join(a))
        elif cmd == "release":
            self.release_all()
        elif cmd == "stop":
            return "__stop__"
        else:
            raise ScriptError(f"неизвестная команда «{cmd}»")
        return None


# --------------------------------------------------------------------------
# Запись маршрута
# --------------------------------------------------------------------------

class Recorder:
    MOVE_BUCKET = 0.025  # движения мыши склеиваются в куски по 25 мс

    def __init__(self):
        self.events = []
        self.mouse_listener = None
        self.pressed = set()
        self.active = False

    def start(self):
        self.events = []
        self.pressed = set()
        self.active = True
        self.mouse_listener = mouse.Listener(on_move=self._on_move, on_click=self._on_click)
        self.mouse_listener.start()

    def stop(self):
        self.active = False
        if self.mouse_listener:
            self.mouse_listener.stop()
            self.mouse_listener = None
        return self.to_script()

    def _on_move(self, x, y, *args):
        if not self.active:
            return
        # Колбэк вызывается внутри низкоуровневого хука, до того как курсор
        # реально сдвинулся, поэтому GetCursorPos — «старая» позиция. Так дельта
        # верна и когда Roblox держит курсор в центре (вид от первого лица).
        px, py = cursor_pos()
        dx, dy = x - px, y - py
        if dx or dy:
            self.events.append((time.perf_counter(), "turn", (dx, dy)))

    def _on_click(self, x, y, button, pressed, *args):
        if not self.active:
            return
        name = {mouse.Button.left: "left", mouse.Button.right: "right",
                mouse.Button.middle: "middle"}.get(button)
        if name:
            self.events.append((time.perf_counter(), "mdown" if pressed else "mup", name))

    def key(self, vk, pressed):
        if not self.active or vk in HOTKEY_VK:
            return
        name = VK_NAME.get(vk)
        if not name:
            return
        if pressed:
            if name in self.pressed:  # автоповтор при удержании
                return
            self.pressed.add(name)
        else:
            self.pressed.discard(name)
        self.events.append((time.perf_counter(), "down" if pressed else "up", name))

    def to_script(self):
        out = []
        last_t = None
        bucket = None  # [t0, dx, dy]

        def emit(t, line):
            nonlocal last_t
            if last_t is None:
                last_t = t
            gap = t - last_t
            if gap >= 0.005:
                out.append(f"wait {gap:.3f}")
                last_t = t
            out.append(line)

        def flush():
            nonlocal bucket
            if bucket and (bucket[1] or bucket[2]):
                emit(bucket[0], f"turn {bucket[1]} {bucket[2]}")
            bucket = None

        for t, kind, data in self.events:
            if kind == "turn":
                if bucket and t - bucket[0] >= self.MOVE_BUCKET:
                    flush()
                if bucket is None:
                    bucket = [t, 0, 0]
                bucket[1] += data[0]
                bucket[2] += data[1]
                continue
            flush()
            emit(t, f"{kind} {data}")
        flush()
        for name in sorted(self.pressed):
            out.append(f"up {name}")
        return out


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class App:
    def __init__(self):
        self.cfg = load_config()
        self.msgs = queue.Queue()
        self.deaths = 0
        self.player = Player(self.cfg, self.log, self._count)
        self.recorder = Recorder()
        self.play_thread = None

        self.root = tk.Tk()
        self.root.title("Doors Death Macro")
        self.root.attributes("-topmost", True)
        self._build()

        self.kb = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self.kb.start()
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.after(100, self._pump)
        self.log("Готово. F6 — старт/стоп, F7 — запись маршрута, F8 — точка, F9 — стоп.")

    # --- интерфейс -------------------------------------------------------
    def _build(self):
        r = self.root
        pad = {"padx": 6, "pady": 3}

        top = ttk.Frame(r)
        top.pack(fill="x", **pad)
        self.status = tk.StringVar(value="Ожидание")
        ttk.Label(top, textvariable=self.status, font=("Segoe UI", 11, "bold")).pack(side="left")
        self.deaths_var = tk.StringVar(value="Смертей: 0")
        ttk.Label(top, textvariable=self.deaths_var, font=("Segoe UI", 11)).pack(side="right")

        btns = ttk.Frame(r)
        btns.pack(fill="x", **pad)
        ttk.Button(btns, text="▶ Старт / Стоп (F6)", command=self.toggle_play).pack(side="left", expand=True, fill="x")
        self.rec_btn = ttk.Button(btns, text="● Запись маршрута (F7)", command=self.toggle_record)
        self.rec_btn.pack(side="left", expand=True, fill="x")
        ttk.Button(btns, text="■ Аварийный стоп (F9)", command=self.stop_all).pack(side="left", expand=True, fill="x")

        files = ttk.LabelFrame(r, text="Файлы")
        files.pack(fill="x", **pad)
        self.cycle_var = tk.StringVar(value=self.cfg["cycle_file"])
        self.route_var = tk.StringVar(value=self.cfg["route_file"])
        for row, (label, var) in enumerate((("Сценарий цикла:", self.cycle_var),
                                            ("Запись маршрута в:", self.route_var))):
            ttk.Label(files, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
            ttk.Entry(files, textvariable=var, width=28).grid(row=row, column=1, padx=4)
            ttk.Button(files, text="Открыть", command=lambda v=var: self._open(v.get())).grid(row=row, column=2, padx=4)

        pts = ttk.LabelFrame(r, text="Точки на экране (кнопки игры)")
        pts.pack(fill="x", **pad)
        ttk.Label(pts, text="Имя:").grid(row=0, column=0, padx=4)
        self.point_name = ttk.Combobox(pts, width=18,
                                       values=["revive", "play_again", "death_screen", "skip"])
        self.point_name.set("revive")
        self.point_name.grid(row=0, column=1, padx=4)
        ttk.Button(pts, text="Захватить через 3 с", command=self.capture_delayed).grid(row=0, column=2, padx=4)
        ttk.Button(pts, text="Удалить", command=self.delete_point).grid(row=0, column=3, padx=4)
        self.points_list = tk.Listbox(pts, height=5, width=60)
        self.points_list.grid(row=1, column=0, columnspan=4, padx=4, pady=4, sticky="we")

        opts = ttk.Frame(r)
        opts.pack(fill="x", **pad)
        ttk.Label(opts, text="Допуск цвета:").pack(side="left")
        self.tol_var = tk.IntVar(value=self.cfg["tolerance"])
        ttk.Spinbox(opts, from_=0, to=255, width=5, textvariable=self.tol_var).pack(side="left", padx=4)
        ttk.Label(opts, text="Множитель поворота:").pack(side="left")
        self.scale_var = tk.DoubleVar(value=self.cfg["turn_scale"])
        ttk.Spinbox(opts, from_=0.1, to=5, increment=0.05, width=5, textvariable=self.scale_var).pack(side="left", padx=4)
        self.topmost = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Поверх окон", variable=self.topmost,
                        command=lambda: r.attributes("-topmost", self.topmost.get())).pack(side="right")

        self.log_box = tk.Text(r, height=12, width=72, state="disabled")
        self.log_box.pack(fill="both", expand=True, **pad)
        self._refresh_points()

    def _refresh_points(self):
        self.points_list.delete(0, "end")
        for name, p in self.cfg["points"].items():
            self.points_list.insert("end", f"@{name}: x={p['x']} y={p['y']} цвет={p['color']}")

    def _open(self, path):
        full = resolve(path)
        if not os.path.exists(full):
            open(full, "w", encoding="utf-8").close()
        os.startfile(full)

    def _sync_cfg(self):
        try:
            self.cfg["tolerance"] = int(self.tol_var.get())
            self.cfg["turn_scale"] = float(self.scale_var.get())
        except (tk.TclError, ValueError):
            pass
        self.cfg["cycle_file"] = self.cycle_var.get().strip() or "cycle.txt"
        self.cfg["route_file"] = self.route_var.get().strip() or "route.txt"
        save_config(self.cfg)

    # --- потокобезопасные уведомления ------------------------------------
    def log(self, text):
        self.msgs.put(("log", text))

    def _count(self):
        self.msgs.put(("count", None))

    def _pump(self):
        while not self.msgs.empty():
            kind, data = self.msgs.get()
            if kind == "log":
                self.log_box.configure(state="normal")
                self.log_box.insert("end", time.strftime("[%H:%M:%S] ") + data + "\n")
                self.log_box.see("end")
                self.log_box.configure(state="disabled")
            elif kind == "count":
                self.deaths += 1
                self.deaths_var.set(f"Смертей: {self.deaths}")
            elif kind == "call":
                data()
        playing = self.play_thread is not None and self.play_thread.is_alive()
        if self.recorder.active:
            self.status.set("● Идёт запись")
        else:
            self.status.set("▶ Работает" if playing else "Ожидание")
        self.root.after(100, self._pump)

    # --- действия --------------------------------------------------------
    def toggle_play(self):
        if self.play_thread and self.play_thread.is_alive():
            self.player.stop_event.set()
            return
        if self.recorder.active:
            self.log("Сначала закончи запись (F7).")
            return
        self._sync_cfg()
        path = self.cfg["cycle_file"]
        self.log(f"Старт: {path}")
        self.play_thread = threading.Thread(target=self.player.run_file, args=(path,), daemon=True)
        self.play_thread.start()

    def toggle_record(self):
        if self.recorder.active:
            lines = self.recorder.stop()
            self._sync_cfg()
            path = resolve(self.cfg["route_file"])
            with open(path, "w", encoding="utf-8") as f:
                f.write("# Записанный маршрут (можно править вручную)\n")
                f.write("\n".join(lines) + "\n")
            self.log(f"Запись сохранена: {self.cfg['route_file']} ({len(lines)} строк)")
            return
        if self.play_thread and self.play_thread.is_alive():
            self.log("Сначала останови цикл (F6).")
            return
        self.recorder.start()
        self.log("Запись пошла: пройди маршрут в игре и нажми F7.")

    def stop_all(self):
        self.player.stop_event.set()
        if self.recorder.active:
            self.toggle_record()

    def capture_point(self):
        name = self.point_name.get().strip().lstrip("@")
        if not name:
            self.log("Укажи имя точки.")
            return
        x, y = cursor_pos()
        color = rgb_to_hex(pixel_color(x, y))
        self.cfg["points"][name] = {"x": x, "y": y, "color": color}
        self._sync_cfg()
        self._refresh_points()
        self.log(f"Точка @{name}: x={x} y={y} цвет={color}")

    def capture_delayed(self):
        self.log("Наведи курсор на кнопку в игре… 3 секунды")
        self.root.after(3000, self.capture_point)

    def delete_point(self):
        name = self.point_name.get().strip().lstrip("@")
        if self.cfg["points"].pop(name, None) is not None:
            self._sync_cfg()
            self._refresh_points()
            self.log(f"Точка @{name} удалена")

    # --- горячие клавиши -------------------------------------------------
    @staticmethod
    def _vk(key):
        vk = getattr(key, "vk", None)
        if vk is None and hasattr(key, "value"):
            vk = getattr(key.value, "vk", None)
        return vk

    def _on_press(self, key, *args):
        vk = self._vk(key)
        actions = {0x75: self.toggle_play, 0x76: self.toggle_record,
                   0x77: self.capture_point, 0x78: self.stop_all}
        if vk in actions:
            self.msgs.put(("call", actions[vk]))
            return
        if vk is not None:
            self.recorder.key(vk, True)

    def _on_release(self, key, *args):
        vk = self._vk(key)
        if vk is not None:
            self.recorder.key(vk, False)

    def _quit(self):
        self.player.stop_event.set()
        if self.recorder.active and messagebox.askyesno("Запись", "Сохранить незаконченную запись?"):
            self.toggle_record()
        self._sync_cfg()
        self.kb.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
