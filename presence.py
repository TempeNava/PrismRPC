"""Runtime side: instance info, log watching, background process, presence loop."""
import atexit
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from config import (CLIENT_ID, FALLBACK_IMAGE, LOADERS, MIN_UPDATE_INTERVAL,
                    POLL_SECONDS, RECONNECT_INTERVAL, RPC_MODS,
                    SHOW_SERVER_ADDRESS, VERSION)

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


def resolve_instance():
    """(name, instance_dir) from the command line, falling back to Prism's env vars."""
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name = args[0] if args else os.environ.get("INST_NAME", "Modded Minecraft")
    inst_dir = Path(args[1] if len(args) > 1 else os.environ.get("INST_DIR", ""))
    return name, inst_dir


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
    try:
        pid_file(inst_dir).unlink()
    except OSError:
        pass


def spawn_detached(name, inst_dir):
    cmd = self_cmd() + ["--child", name, str(inst_dir)]
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


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


def pack_icon_url(inst_dir):
    """Icon of a Modrinth-managed modpack, if any."""
    try:
        text = (inst_dir / "instance.cfg").read_text(encoding="utf-8")
        ptype = re.search(r"^ManagedPackType=(\w+)", text, re.M | re.I)
        pid = re.search(r"^ManagedPackID=(\S+)", text, re.M)
        if ptype and pid and ptype.group(1).lower() == "modrinth":
            req = urllib.request.Request(
                f"https://api.modrinth.com/v2/project/{pid.group(1)}",
                headers={"User-Agent": f"PrismRPC/{VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8")).get("icon_url")
    except Exception:
        pass
    return None


# --- game status from latest.log (read incrementally) -----------------------
class LogWatcher:
    CONNECT = re.compile(r"Connecting to ([^,\s]+), (\d+)")
    START = ("Starting integrated minecraft server",)
    LEAVE = ("Stopping singleplayer server", "Stopping server", "Stopping!", "Disconnecting")

    def __init__(self, path, started_at):
        self.path = path
        self.started_at = started_at
        self.pos = 0
        self.ino = None
        self.first = True
        self.mode = "menu"  # menu | single | multi
        self.server = None

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
                return
        elif st.st_ino != self.ino or st.st_size < self.pos:
            # Log was rotated by a new game start.
            self.ino, self.pos = st.st_ino, 0
            self.mode, self.server = "menu", None
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
        for line in chunk[:end].decode("utf-8", "ignore").splitlines():
            self._handle(line)

    def _handle(self, line):
        m = self.CONNECT.search(line)
        if m:
            self.mode, self.server = "multi", m.group(1)
        elif any(s in line for s in self.START):
            self.mode, self.server = "single", None
        elif any(s in line for s in self.LEAVE):
            self.mode, self.server = "menu", None

    def details(self):
        if self.mode == "single":
            return "Playing Singleplayer"
        if self.mode == "multi":
            if SHOW_SERVER_ADDRESS and self.server:
                return f"Playing on {self.server}"
            return "Playing Multiplayer"
        return "Main Menu"


# --- the presence loop --------------------------------------------------------
def run(is_child):
    name, inst_dir = resolve_instance()
    if not inst_dir.is_dir():
        return
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
        from pypresence import Presence
    except ImportError:
        return

    pf = pid_file(inst_dir)
    my_pid = str(os.getpid())
    pf.write_text(my_pid)
    atexit.register(lambda: pf.exists() and pf.read_text() == my_pid and pf.unlink())

    started = time.time()
    mc_version, loader = read_pack_info(inst_dir)
    version_tag = f"Minecraft {mc_version} ({loader})".replace("  ", " ").strip()
    mods_text = f" • {mod_count} mod{'s' if mod_count != 1 else ''}" if mod_count else ""
    state = version_tag + mods_text

    icon = pack_icon_url(inst_dir)
    large_image = icon or FALLBACK_IMAGE
    small_image = FALLBACK_IMAGE if icon else None

    watcher = LogWatcher(mc_dir / "logs" / "latest.log", started)
    rpc = None
    last_payload, last_sent, last_attempt = None, 0.0, 0.0
    supports_name = True

    while True:
        try:
            if not pf.exists() or pf.read_text().strip() != my_pid:
                break  # stopped by post-exit command

            watcher.poll()
            now = time.time()

            if rpc is None and now - last_attempt >= RECONNECT_INTERVAL:
                last_attempt = now
                try:
                    candidate = Presence(CLIENT_ID)
                    candidate.connect()
                    rpc, last_payload = candidate, None
                except Exception:
                    rpc = None  # Discord not running yet

            if rpc is not None:
                payload = dict(details=watcher.details(), state=state)
                if payload != last_payload and now - last_sent >= MIN_UPDATE_INTERVAL:
                    kwargs = dict(payload, large_image=large_image,
                                  large_text=name, start=int(started))
                    if small_image:
                        kwargs.update(small_image=small_image, small_text=loader)
                    try:
                        if supports_name:
                            try:
                                rpc.update(name=name, **kwargs)
                            except TypeError:  # older pypresence without `name`
                                supports_name = False
                                rpc.update(**kwargs)
                        else:
                            rpc.update(**kwargs)
                        last_payload, last_sent = payload, now
                    except Exception:
                        try:
                            rpc.close()
                        except Exception:
                            pass
                        rpc = None  # reconnect on next cycle
        except Exception:
            pass
        time.sleep(POLL_SECONDS)

    if rpc is not None:
        try:
            rpc.clear()
            rpc.close()
        except Exception:
            pass
