"""Runtime side: instance info, log watching, background process, presence loop."""
import asyncio
import atexit
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from config import (CLIENT_ID, FALLBACK_IMAGE, LOADERS, MIN_UPDATE_INTERVAL,
                    POLL_SECONDS, RECONNECT_INTERVAL, RPC_MODS)
from online import check_for_update, lookup_pack
from settings import LiveSettings, instance_enabled

IS_FROZEN = getattr(sys, "frozen", False)
# The program Prism should run: the .exe when frozen, otherwise main.py.
SELF_PATH = Path(sys.executable if IS_FROZEN else sys.argv[0]).resolve()


# --- how to re-launch ourselves ---------------------------------------------
def self_cmd(path=None):
    if IS_FROZEN:
        return [str(path or SELF_PATH)]
    exe = Path(sys.executable)
    if os.name == "nt":  # pythonw = no console window
        pyw = exe.with_name("pythonw.exe")
        if pyw.is_file():
            exe = pyw
    return [str(exe), str(path or SELF_PATH)]


def spawn(args):
    """Start ourselves again with extra arguments, fully detached."""
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(self_cmd() + list(args), **kwargs)


def resolve_instance():
    """(name, instance_dir) from the command line, falling back to Prism's env vars.

    The directory is None when neither the command line nor INST_DIR gives one,
    so a bare run can never mistake the current working directory for an
    instance (and write its PID file there).
    """
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    name = args[0] if args else os.environ.get("INST_NAME") or "Modded Minecraft"
    raw = args[1] if len(args) > 1 else os.environ.get("INST_DIR") or ""
    inst_dir = Path(raw) if str(raw).strip() else None
    return name, inst_dir


def is_ours(value):
    """True when a Prism launch command is one of our own (so we may replace it)."""
    if not value:
        return False
    low = value.lower()
    # Frozen builds are named PrismRPC.exe. From source, match the full path to
    # this script rather than the bare name "main.py", which other tools use too.
    return "prismrpc" in low.replace("_", "") or str(SELF_PATH).lower() in low


def find_mc_dir(inst_dir):
    env = os.environ.get("INST_MC_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for sub in ("minecraft", ".minecraft"):
        if (inst_dir / sub).is_dir():
            return inst_dir / sub
    return inst_dir / "minecraft"


# --- background process, one per instance, tracked by a PID file -----------
def pid_file(inst_dir):
    return inst_dir / "prism_rpc.pid"


def stop_instance(inst_dir):
    # Removing the PID file is the stop signal: the running process notices it
    # within a few seconds, clears the Discord status and exits cleanly.
    if inst_dir is None:
        return
    try:
        pid_file(inst_dir).unlink()
    except OSError:
        pass


def spawn_detached(name, inst_dir):
    spawn(["--child", name, str(inst_dir)])


# --- instance information ---------------------------------------------------
def scan_mods(mc_dir):
    """Returns (has_rpc_mod, mod_count)."""
    mods_dir = mc_dir / "mods"
    count = 0
    if mods_dir.is_dir():
        for f in mods_dir.iterdir():
            n = f.name.lower()
            if n.endswith(".jar"):
                if any(m in n for m in RPC_MODS):
                    return True, 0
                count += 1
    return False, count


def read_pack_info(inst_dir):
    """Returns (minecraft_version, loader_name) from mmc-pack.json."""
    mc_version, loader = "", "Vanilla"
    try:
        data = json.loads((inst_dir / "mmc-pack.json").read_text(encoding="utf-8"))
        for comp in data.get("components", []):
            uid = comp.get("uid", "")
            if uid == "net.minecraft":
                mc_version = comp.get("version", "")
            elif uid in LOADERS:
                loader = LOADERS[uid]
    except Exception:
        pass
    return mc_version, loader


def saved_playtime(inst_dir):
    """Seconds Prism has recorded for the instance (it updates this when the game closes)."""
    try:
        text = (inst_dir / "instance.cfg").read_text(encoding="utf-8")
        m = re.search(r"^totalTimePlayed=(\d+)", text, re.M)
        return int(m.group(1)) if m else 0
    except (OSError, ValueError):
        return 0


def format_playtime(seconds):
    """Short text that changes slowly ("25 min", "3.4 h", "52 h"), so the status
    is not re-sent to Discord every minute."""
    if seconds < 3600:
        return f"{max(int(seconds // 60), 1)} min"
    hours = seconds / 3600
    return f"{hours:.1f} h" if hours < 10 else f"{round(hours)} h"


# --- game status from latest.log (read incrementally) -----------------------
class LogWatcher:
    CONNECT = re.compile(r"Connecting to ([^,\s]+), (\d+)")
    START = ("Starting integrated minecraft server",)
    LEAVE = ("Stopping singleplayer server", "Stopping server", "Stopping!", "Disconnecting")
    HEAD_BYTES = 128

    def __init__(self, path, started_at):
        self.path = path
        self.started_at = started_at
        self.pos = 0
        self.ino = None
        self.head = b""             # first bytes of the file we are following
        self.first = True
        self.mode = "menu"          # menu | single | multi
        self.server = None
        self.since = started_at     # when the current mode/server began

    def _set(self, mode, server=None):
        if (mode, server) != (self.mode, self.server):
            self.mode, self.server, self.since = mode, server, time.time()

    def _read_head(self):
        try:
            with open(self.path, "rb") as f:
                return f.read(self.HEAD_BYTES)
        except OSError:
            return b""

    def _head_changed(self):
        if not self.head:
            return False
        # A rotated or truncated file no longer starts with what we saw before.
        return not self._read_head().startswith(self.head)

    def poll(self):
        try:
            st = self.path.stat()
        except OSError:
            return
        if self.first:
            self.first = False
            self.ino = st.st_ino
            if st.st_mtime < self.started_at - 2:
                # Leftover log from a previous session: ignore its content.
                self.pos = st.st_size
                self.head = self._read_head()
                return
        elif (st.st_ino != self.ino or st.st_size < self.pos
              or self._head_changed()):
            # Log was rotated by a new game start.
            self.ino, self.pos, self.head = st.st_ino, 0, b""
            self._set("menu")
        if st.st_size == self.pos:
            return
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                chunk = f.read()
        except OSError:
            return
        end = chunk.rfind(b"\n")
        if end == -1:
            return  # incomplete line, wait for the rest
        self.pos += end + 1
        if len(self.head) < self.HEAD_BYTES:
            self.head = self._read_head()
        for line in chunk[:end].decode("utf-8", "ignore").splitlines():
            self._handle(line)

    def _handle(self, line):
        if "[CHAT]" in line:
            return
        m = self.CONNECT.search(line)
        if m:
            self._set("multi", m.group(1))
        elif any(s in line for s in self.START):
            self._set("single")
        elif any(s in line for s in self.LEAVE):
            self._set("menu")

    def details(self, show_server=True):
        if self.mode == "single":
            return "Playing Singleplayer"
        if self.mode == "multi":
            if show_server and self.server:
                return f"Playing on {self.server}"
            return "Playing Multiplayer"
        return "Main Menu"


def tail_log(path, max_bytes=1 << 20):
    """A LogWatcher that has already replayed the end of `path`.

    Used by --diagnose to show the status the log adds up to right now, without
    waiting for a game session. Only the last `max_bytes` are read.
    """
    watcher = LogWatcher(path, 0)
    try:
        st = Path(path).stat()
    except OSError:
        return watcher
    start = max(0, st.st_size - max_bytes)
    if start:
        try:
            with open(path, "rb") as f:
                f.seek(start)
                f.readline()          # drop the half line we started inside
                start = f.tell()
        except OSError:
            return watcher
    watcher.ino = st.st_ino
    watcher.pos = start
    watcher.first = False             # we asked for this content: do not skip it
    watcher.poll()
    return watcher


# --- state shared between the presence loop and the tray icon ---------------
class Session:
    def __init__(self, name, inst_dir):
        self.name = name
        self.inst_dir = inst_dir
        self.key = inst_dir.name         # instance folder name, used in settings
        self.paused = False
        self.stop = threading.Event()
        self.pack = {}                   # {'icon': url, 'url': url} once looked up
        self.update = None               # (tag, url) when a newer release exists
        self.on_change = lambda: None    # tray hooks these in
        self.notify = lambda message: None
        self.launched = time.time()      # when this presence started (about the game launch)
        self.base_seconds = 0            # Prism's saved total, read once at start

    def total_seconds(self):
        return self.base_seconds + (time.time() - self.launched)


def build_activity(session, s, watcher, mc_version, loader, mod_count, launched):
    state = f"Minecraft {mc_version} ({loader})".replace("  ", " ").strip()
    if s["show_mod_count"] and mod_count:
        state += f" • {mod_count} mod{'s' if mod_count != 1 else ''}"
    activity = {
        "details": watcher.details(s["show_server_address"]),
        "state": state,
        "large_text": session.name,
    }
    if s["show_total_time"]:  # shown when hovering the pack icon
        activity["large_text"] += f" • {format_playtime(session.total_seconds())} played in total"
    icon = session.pack.get("icon")
    if icon:
        activity.update(large_image=icon, small_image=FALLBACK_IMAGE, small_text=loader)
    else:
        activity["large_image"] = FALLBACK_IMAGE

    start = None
    if s["timer"] == "game":
        start = launched
    elif s["timer"] == "world" and watcher.mode != "menu":
        start = watcher.since
    if start:
        activity["start"] = int(start)

    if s["show_button"] and session.pack.get("url"):
        activity["buttons"] = [{"label": "View modpack", "url": session.pack["url"]}]
    return activity


def presence_loop(session, live, mc_dir, mod_count, mc_version, loader, my_pid):
    try:
        from pypresence import Presence
    except ImportError:
        return

    # pypresence needs an event loop that belongs to this thread.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    pf = pid_file(session.inst_dir)
    launched = session.launched
    watcher = LogWatcher(mc_dir / "logs" / "latest.log", launched)
    rpc = None
    sent = None                      # activity currently shown on Discord (None = cleared)
    last_sent = last_attempt = 0.0
    supports_name = True
    replaced = False                 # True when a newer session took over this instance

    def send(activity):
        nonlocal supports_name
        if activity is None:
            rpc.clear()
        elif supports_name:
            try:
                rpc.update(name=session.name, **activity)
            except TypeError:  # older pypresence without `name`
                supports_name = False
                rpc.update(**activity)
        else:
            rpc.update(**activity)

    while not session.stop.is_set():
        try:
            if not pf.exists():
                break  # stopped by the post-exit command
            if pf.read_text().strip() != my_pid:
                replaced = True  # a newer process owns this instance now
                break

            s = live.get()
            watcher.poll()
            now = time.time()
            active = instance_enabled(s, session.key) and not session.paused
            desired = (build_activity(session, s, watcher, mc_version, loader,
                                      mod_count, launched) if active else None)

            if rpc is None and desired is not None and now - last_attempt >= RECONNECT_INTERVAL:
                last_attempt = now
                candidate = None
                try:
                    candidate = Presence(CLIENT_ID, loop=loop)
                    candidate.connect()
                    rpc, sent = candidate, None
                except Exception:
                    rpc = None  # Discord not running yet
                    if candidate is not None:
                        try:
                            candidate.close()  # don't leak a half-open connection
                        except Exception:
                            pass

            if rpc is not None and desired != sent:
                # Clearing is immediate; updates are rate-limited.
                if desired is None or now - last_sent >= MIN_UPDATE_INTERVAL:
                    try:
                        send(desired)
                        sent, last_sent = desired, now
                    except Exception:
                        try:
                            rpc.close()
                        except Exception:
                            pass
                        rpc, sent = None, None  # reconnect on the next cycle
        except Exception:
            pass
        session.stop.wait(POLL_SECONDS)

    if rpc is not None:
        try:
            if not replaced:
                rpc.clear()
            rpc.close()
        except Exception:
            pass
    try:
        loop.close()
    except Exception:
        pass


def start_lookups(session, live):
    """Fetch the modpack icon/link and check for updates without blocking."""
    def pack():
        try:
            session.pack = lookup_pack(session.inst_dir)
        except Exception:
            return
        session.on_change()

    def update():
        if not live.get()["check_updates"]:
            return
        try:
            found = check_for_update()
        except Exception:
            return
        if found:
            session.update = found
            session.on_change()
            session.notify(f"PrismRPC {found[0]} is available")

    for target in (pack, update):
        threading.Thread(target=target, daemon=True).start()


# --- entry point for the pre-launch command ---------------------------------
def run(is_child):
    name, inst_dir = resolve_instance()
    if inst_dir is None or not inst_dir.is_dir():
        return
    live = LiveSettings()
    settings = live.get()
    if not instance_enabled(settings, inst_dir.name):
        return  # PrismRPC is switched off (globally or for this instance)

    mc_dir = find_mc_dir(inst_dir)
    has_rpc_mod, mod_count = scan_mods(mc_dir)
    if has_rpc_mod:
        return  # a mod already handles Rich Presence

    if not is_child:
        # Prism waits for this command, so hand off to a detached process
        # immediately. Network calls etc. then never delay the game launch.
        stop_instance(inst_dir)
        spawn_detached(name, inst_dir)
        return

    try:
        import pypresence  # noqa: F401
    except ImportError:
        return

    session = Session(name, inst_dir)
    session.base_seconds = saved_playtime(inst_dir)
    pf = pid_file(inst_dir)
    my_pid = str(os.getpid())
    pf.write_text(my_pid)
    atexit.register(lambda: pf.exists() and pf.read_text() == my_pid and pf.unlink())

    mc_version, loader = read_pack_info(inst_dir)

    def worker():
        start_lookups(session, live)  # after the tray hooks exist, so notify() works
        presence_loop(session, live, mc_dir, mod_count, mc_version, loader, my_pid)

    if settings["show_tray"]:
        import tray
        if tray.run(session, worker):
            return
    worker()
