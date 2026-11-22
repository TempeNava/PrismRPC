"""User settings, cache, and Prism Launcher's own config / instance list."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# --- user settings (settings.json) -------------------------------------------
DEFAULTS = {
    "enabled": True,              # master switch
    "disabled_instances": [],     # instance folder names that get no status
    "show_tray": True,            # tray icon while a game is running
    "show_server_address": True,  # "Playing on <server>" vs "Playing Multiplayer"
    "show_mod_count": True,
    "show_button": True,          # "View modpack" button
    "timer": "game",              # game | world | off
    "check_updates": True,
}
TIMER_CHOICES = ("game", "world", "off")


def data_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base, "PrismRPC")


def resource_path(name):
    """A file bundled with the program (works from source and when frozen)."""
    base = getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent
    return Path(base, name)


def _read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)  # atomic: readers never see a half-written file
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def settings_path():
    return data_dir() / "settings.json"


def load():
    """Settings with defaults filled in and wrong-typed values ignored."""
    data = dict(DEFAULTS)
    for key, value in _read_json(settings_path()).items():
        if key in DEFAULTS and type(value) is type(DEFAULTS[key]):
            data[key] = value
    if data["timer"] not in TIMER_CHOICES:
        data["timer"] = DEFAULTS["timer"]
    return data


def update(**changes):
    """Change some settings and save. Re-reads the file first so that two open
    windows or processes can't overwrite each other's changes."""
    data = load()
    data.update(changes)
    _write_json(settings_path(), data)
    return data


class LiveSettings:
    """Settings that reload themselves when the file changes on disk."""

    def __init__(self):
        self._stamp = object()
        self._data = dict(DEFAULTS)

    def get(self):
        try:
            st = settings_path().stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._stamp = stamp
            self._data = load()
        return self._data


def instance_enabled(settings, key):
    return settings["enabled"] and key not in settings["disabled_instances"]


# --- cache (cache.json): pack icons/links, update check -----------------------
def load_cache():
    return _read_json(data_dir() / "cache.json")


def save_cache(cache):
    _write_json(data_dir() / "cache.json", cache)


def cache_fresh(entry, max_age):
    return isinstance(entry, dict) and time.time() - entry.get("t", 0) < max_age


# --- Prism Launcher's own config ----------------------------------------------
def prism_cfg_path():
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base, "PrismLauncher", "prismlauncher.cfg") if base else None
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/PrismLauncher/prismlauncher.cfg"
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(data, "PrismLauncher", "prismlauncher.cfg")


def prism_running():
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq prismlauncher.exe"],
                capture_output=True, text=True, creationflags=0x08000000,
            ).stdout.lower()
            return "prismlauncher.exe" in out
        return subprocess.run(["pgrep", "-fi", "prismlauncher"],
                              capture_output=True).returncode == 0
    except Exception:
        return False


def qt_escape(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def qt_unescape(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return re.sub(r"\\(.)", r"\1", value)


def general_range(lines):
    """(start, end) line indexes of the body of the [General] section."""
    start = next((i for i, l in enumerate(lines) if l.strip() == "[General]"), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("["):
            end = i
            break
    return start + 1, end


def cfg_get(lines, key):
    rng = general_range(lines)
    if rng:
        for i in range(*rng):
            if lines[i].startswith(key + "="):
                return qt_unescape(lines[i][len(key) + 1:])
    return ""


def cfg_set(lines, key, value):
    """Set (or remove when value is None) a key inside [General]."""
    rng = general_range(lines)
    if rng is None:
        if value is None:
            return
        lines.extend(["[General]"])
        rng = (len(lines), len(lines))
    for i in range(*rng):
        if lines[i].startswith(key + "="):
            if value is None:
                del lines[i]
            else:
                lines[i] = f"{key}={qt_escape(value)}"
            return
    if value is not None:
        lines.insert(rng[0], f"{key}={qt_escape(value)}")


# --- Prism instances ----------------------------------------------------------
def instances_dir():
    cfg = prism_cfg_path()
    if not cfg:
        return None
    try:
        value = cfg_get(cfg.read_text(encoding="utf-8").splitlines(), "InstanceDir")
    except OSError:
        value = ""
    if value:
        path = Path(value)
        return path if path.is_absolute() else cfg.parent / path
    return cfg.parent / "instances"


def instance_name(inst_dir):
    try:
        text = (inst_dir / "instance.cfg").read_text(encoding="utf-8")
        m = re.search(r"^name=(.*)$", text, re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    except OSError:
        pass
    return inst_dir.name


def list_instances():
    """[(folder_name, display_name), ...] for every instance in Prism."""
    root = instances_dir()
    found = []
    if root and root.is_dir():
        for d in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if (d.is_dir() and not d.name.startswith(("_", "."))
                    and (d / "instance.cfg").is_file()):
                found.append((d.name, instance_name(d)))
    return found
