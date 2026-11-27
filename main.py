"""PrismRPC - Discord Rich Presence for Prism Launcher.

Command line (Prism runs the last two itself; name/dir default to INST_NAME / INST_DIR):

  (no arguments)               Install: copy the app and register it in Prism
                               (close Prism first)
  --settings                   Open the settings window
  --uninstall                  Remove it from Prism's launch commands
  --stop                       Stop this instance's presence (post-exit hook)
  --version                    Print the version
  --diagnose [dir] [--log F] [--json]
                               Show what PrismRPC sees for an instance, and why
                               a status might be missing (read-only)
  [--child] [name] [dir]       Start the presence (pre-launch hook)
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import VERSION
from presence import (IS_FROZEN, SELF_PATH, is_ours, resolve_instance, run,
                      self_cmd, stop_instance)
from settings import cfg_get, cfg_set, data_dir, prism_cfg_path, prism_running


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


# --- installing the program -----------------------------------------------------
def install_permanent_copy():
    """Copy the program into %LOCALAPPDATA%\\PrismRPC\\app and return the exe path.

    The Setup exe carries the real program as a bundled 'payload' folder. The
    installed program is a plain folder build (not a self-extracting one-file
    exe), so nothing is unpacked into Temp on every game launch.
    """
    if not IS_FROZEN:
        return SELF_PATH
    root = data_dir()
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


def shortcut_path():
    base = os.environ.get("APPDATA")
    if os.name != "nt" or not base:
        return None
    return Path(base, "Microsoft", "Windows", "Start Menu", "Programs",
                "PrismRPC Settings.lnk")


def make_shortcut(exe):
    """Start Menu entry that opens the settings window (best effort)."""
    lnk = shortcut_path()
    if not lnk or not exe.is_file():
        return
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:PRISMRPC_LNK);"
          "$s.TargetPath=$env:PRISMRPC_EXE;$s.Arguments='--settings';"
          "$s.IconLocation=$env:PRISMRPC_EXE;$s.Save()")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            env=dict(os.environ, PRISMRPC_LNK=str(lnk), PRISMRPC_EXE=str(exe)),
            capture_output=True, timeout=20, creationflags=0x08000000)
    except Exception:
        pass


def remove_shortcut():
    lnk = shortcut_path()
    if lnk:
        try:
            lnk.unlink()
        except OSError:
            pass


# --- Prism config handling ------------------------------------------------------
CMD_KEYS = ("PreLaunchCommand", "PostExitCommand")


def edit_prism_cfg(install):
    """Add or remove our commands in Prism's config. Returns the exe path on
    success when installing, True on success when uninstalling, else None."""
    cfg = prism_cfg_path()
    if not cfg or not cfg.is_file():
        say("Could not find prismlauncher.cfg. Start Prism Launcher once, close it, then try again.")
        return None
    if prism_running():
        say("Prism Launcher is running and would overwrite the change when it closes.\n"
            "Close Prism completely and run this again.")
        return None

    # Read bytes so the line endings survive (read_text() would turn CRLF into LF).
    try:
        raw = cfg.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        say(f"Could not read {cfg}: {e}")
        return None
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    # Check for the user's own commands BEFORE copying anything, so a refused
    # install doesn't leave a copy of the app behind.
    for key in CMD_KEYS:
        current = cfg_get(lines, key)
        if install and current and not is_ours(current):
            say(f"{key} is already set to:\n  {current}\n"
                "Not overwriting it. Clear it in Prism's settings (Settings > Custom Commands) "
                "or chain the commands yourself.")
            return None

    exe = None
    pre_new = post_new = None
    if install:
        try:
            exe = install_permanent_copy()
        except OSError as e:
            why = "some files are in use" if isinstance(e, shutil.Error) else e
            say(f"PrismRPC could not be installed ({why}).\n\n"
                "Close PrismRPC (its settings window or tray icon) and any running "
                "Minecraft game, then run the installer again.")
            return None
        pre_new = subprocess.list2cmdline(self_cmd(exe))
        post_new = subprocess.list2cmdline(self_cmd(exe) + ["--stop"])

    for key, new in zip(CMD_KEYS, (pre_new, post_new)):
        if install or is_ours(cfg_get(lines, key)):
            cfg_set(lines, key, new)

    try:
        bak = cfg.with_suffix(".cfg.bak")
        if not bak.exists():
            bak.write_bytes(raw)
        tmp = cfg.with_name(cfg.name + ".tmp")
        tmp.write_bytes((eol.join(lines) + eol).encode("utf-8"))
        os.replace(tmp, cfg)
    except OSError as e:
        say(f"Could not write {cfg}: {e}")
        return None
    return exe if install else True


def do_install():
    exe = edit_prism_cfg(install=True)
    if not exe:
        return
    make_shortcut(exe)
    say("PrismRPC is installed.\n\n"
        "Start Prism and launch an instance. To change options later, open "
        "\"PrismRPC Settings\" from the Start menu, or use the tray icon while playing.")
    try:  # first-run: show the settings window right away
        subprocess.Popen([str(exe), "--settings"], close_fds=True,
                         creationflags=0x00000008 if os.name == "nt" else 0)
    except Exception:
        pass


def do_uninstall():
    if edit_prism_cfg(install=False):
        remove_shortcut()
        say("PrismRPC removed from Prism's launch commands.\n"
            "You can now delete the %LOCALAPPDATA%\\PrismRPC folder.")


def open_settings():
    try:
        import gui
        gui.open_window()
    except ImportError:
        say("The settings window needs tkinter and customtkinter "
            "(pip install -r requirements.txt).\n"
            f"Edit {data_dir() / 'settings.json'} instead.")


# --- entry point ----------------------------------------------------------------
def main():
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    launched_by_prism = "INST_DIR" in os.environ

    if "--version" in flags:
        say(f"PrismRPC {VERSION}")
    elif "--diagnose" in flags:
        import diagnose
        return diagnose.run(sys.argv[1:])
    elif "--settings" in flags:
        open_settings()
    elif "--uninstall" in flags:
        do_uninstall()
        pause()
    elif "--install" in flags or (len(sys.argv) == 1 and not launched_by_prism):
        do_install()
        pause()
    elif "--stop" in flags:
        stop_instance(resolve_instance()[1])
    else:
        run("--child" in flags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
