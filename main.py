"""PrismRPC - Discord Rich Presence for Prism Launcher.

Command line (Prism runs the last two itself; name/dir default to INST_NAME / INST_DIR):

  (no arguments)               Install: copy the app and register it in Prism
                               (close Prism first)
  --uninstall                  Remove it from Prism's launch commands
  --stop                       Stop this instance's presence (post-exit hook)
  [--child] [name] [dir]       Start the presence (pre-launch hook)
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from presence import (IS_FROZEN, SELF_PATH, resolve_instance, run, self_cmd,
                      stop_instance)


# --- small UI helpers ---------------------------------------------------------
def say(msg):
    """Print, or show a message box when running as a windowed .exe."""
    print(msg)
    if os.name == "nt" and not (sys.stdout and sys.stdout.isatty()):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "PrismRPC", 0x40)
        except Exception:
            pass


def pause():
    if sys.stdin and sys.stdin.isatty():
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass


def install_permanent_copy():
    """Copy the program into %LOCALAPPDATA%\\PrismRPC\\app and return the exe path.

    The Setup exe carries the real program as a bundled 'payload' folder. The
    installed program is a plain folder build (not a self-extracting one-file
    exe), so nothing is unpacked into Temp on every game launch.
    """
    if not IS_FROZEN:
        return SELF_PATH
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home(), "PrismRPC")
    dest = root / "app"
    payload = Path(getattr(sys, "_MEIPASS", ""), "payload")
    source = payload if payload.is_dir() else SELF_PATH.parent
    if source.resolve() != dest.resolve():
        shutil.copytree(source, dest, dirs_exist_ok=True)
    try:  # leftover from older one-file versions
        (root / "PrismRPC.exe").unlink()
    except OSError:
        pass
    return dest / "PrismRPC.exe"


# --- Prism config handling ------------------------------------------------------
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


def is_ours(value):
    v = value.lower().replace("_", "")
    return bool(value) and ("prismrpc" in v or SELF_PATH.name.lower() in value.lower())


def edit_prism_cfg(install):
    cfg = prism_cfg_path()
    if not cfg or not cfg.is_file():
        say("Could not find prismlauncher.cfg. Start Prism Launcher once, close it, then try again.")
        return False
    if prism_running():
        say("Prism Launcher is running and would overwrite the change when it closes.\n"
            "Close Prism completely and run this again.")
        return False

    text = cfg.read_text(encoding="utf-8")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    pre_new = post_new = None
    if install:
        try:
            exe = install_permanent_copy()
        except OSError as e:
            say(f"Could not copy PrismRPC to its permanent folder: {e}")
            return False
        pre_new = subprocess.list2cmdline(self_cmd(exe))
        post_new = subprocess.list2cmdline(self_cmd(exe) + ["--stop"])

    for key, new in (("PreLaunchCommand", pre_new), ("PostExitCommand", post_new)):
        current = cfg_get(lines, key)
        if current and not is_ours(current):
            say(f"{key} is already set to:\n  {current}\n"
                "Not overwriting it. Clear it in Prism's settings (Settings > Custom Commands) "
                "or chain the commands yourself.")
            return False
        cfg_set(lines, key, new)

    cfg.with_suffix(".cfg.bak").write_text(text, encoding="utf-8")
    cfg.write_text(eol.join(lines) + eol, encoding="utf-8")
    say("PrismRPC installed. Start Prism and launch an instance." if install
        else "PrismRPC removed from Prism's launch commands.")
    return True


# --- entry point ----------------------------------------------------------------
def main():
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    launched_by_prism = "INST_DIR" in os.environ

    if "--uninstall" in flags:
        edit_prism_cfg(install=False)
        pause()
    elif "--install" in flags or (len(sys.argv) == 1 and not launched_by_prism):
        edit_prism_cfg(install=True)
        pause()
    elif "--stop" in flags:
        stop_instance(resolve_instance()[1])
    else:
        run("--child" in flags)


if __name__ == "__main__":
    main()
