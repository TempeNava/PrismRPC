import json
import os
import re
import sys
import time
import urllib.request
from pypresence import Presence

CLIENT_ID = "1573820368626385046"
instance_name = sys.argv[1] if len(sys.argv) > 1 else "Modded Minecraft"
instance_dir = sys.argv[2] if len(sys.argv) > 2 else ""

# --- AUTO-CONFIGURE PRISM LAUNCHER ON FIRST RUN ---
def auto_configure_prism():
    if os.name != "nt":
        return
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return
    
    cfg_path = os.path.join(appdata, "PrismLauncher", "prismlauncher.cfg")
    if not os.path.isfile(cfg_path):
        return
    
    exe_path = os.path.abspath(sys.argv[0]) if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
    
    pre_cmd = f'cmd /c start /b "" "{exe_path}" "$INST_NAME" "$INST_DIR"'
    post_cmd = f'taskkill /F /IM {os.path.basename(exe_path)}'
    
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        updated = False
        # Check and set PreLaunchCommand
        if "PreLaunchCommand=" in content:
            if pre_cmd not in content:
                content = re.sub(r'PreLaunchCommand=.*', f'PreLaunchCommand={pre_cmd}', content)
                updated = True
        else:
            content += f"\nPreLaunchCommand={pre_cmd}\n"
            updated = True
            
        # Check and set PostExitCommand
        if "PostExitCommand=" in content:
            if post_cmd not in content:
                content = re.sub(r'PostExitCommand=.*', f'PostExitCommand={post_cmd}', content)
                updated = True
        else:
            content += f"\nPostExitCommand={post_cmd}\n"
            updated = True
            
        if updated:
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(content)
    except Exception:
        pass

# Run the auto-config check immediately
auto_configure_prism()

RPC_MODS = (
    "craftpresence",
    "simple-rpc",
    "simplerpc",
    "discordrpc",
    "discord-rpc",
    "mc-rpc",
    "presence",
)

# 1. Skip if a Rich Presence mod is already installed, and count active mods
mod_count = 0
for subpath in ("mods", os.path.join(".minecraft", "mods")):
    mods_folder = os.path.join(instance_dir, subpath)
    if os.path.isdir(mods_folder):
        for filename in os.listdir(mods_folder):
            lower_name = filename.lower()
            if lower_name.endswith(".jar"):
                if any(mod in lower_name for mod in RPC_MODS):
                    sys.exit(0)
                mod_count += 1

# 2. Read Minecraft version and Modloader from Prism's mmc-pack.json
mc_version = ""
loader_name = "Vanilla"
pack_json = os.path.join(instance_dir, "mmc-pack.json")

if os.path.isfile(pack_json):
    try:
        with open(pack_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        loaders = {
            "net.fabricmc.fabric-loader": "Fabric",
            "net.neoforged": "NeoForge",
            "net.minecraftforge": "Forge",
            "org.quiltmc.quilt-loader": "Quilt",
        }
        for comp in data.get("components", []):
            uid = comp.get("uid", "")
            if uid == "net.minecraft":
                mc_version = comp.get("version", "")
            elif uid in loaders:
                loader_name = loaders[uid]
    except Exception:
        pass

# 3. Determine Image
large_image_url = "mc_icon"
cfg_path = os.path.join(instance_dir, "instance.cfg")

try:
    if os.path.isfile(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg_text = f.read()
        platform_match = re.search(r"ManagedPackType=(modrinth|flame|curseforge)", cfg_text, re.IGNORECASE)
        id_match = re.search(r"ManagedPackID=([^\s]+)", cfg_text)
        if platform_match and id_match and platform_match.group(1).lower() == "modrinth":
            pack_id = id_match.group(1).strip()
            req = urllib.request.Request(
                f"https://api.modrinth.com/v2/project/{pack_id}",
                headers={"User-Agent": "PrismRPC/1.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                proj = json.loads(resp.read().decode("utf-8"))
                if proj.get("icon_url"):
                    large_image_url = proj["icon_url"]
except Exception:
    pass

# 4. Helper to read Singleplayer / Multiplayer status from latest.log
def get_game_status(base_dir):
    status = "Main Menu"
    for subpath in (os.path.join(".minecraft", "logs", "latest.log"), os.path.join("logs", "latest.log")):
        log_path = os.path.join(base_dir, subpath)
        if os.path.isfile(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if "Starting integrated minecraft server" in line or "Saving chunks for level 'ServerLevel" in line:
                            status = "Playing Singleplayer"
                        elif "Connecting to " in line:
                            status = "Playing Multiplayer"
                        elif "Stopping!" in line or "Disconnecting" in line or "Stopping singleplayer server" in line:
                            status = "Main Menu"
            except Exception:
                pass
            break
    return status

rpc = Presence(CLIENT_ID)
rpc.connect()
start_time = int(time.time())
version_tag = f"Minecraft {mc_version} ({loader_name})".strip()

while True:
    game_mode = get_game_status(instance_dir)
    bottom_line = f"{version_tag} • {mod_count} mods" if mod_count > 0 else version_tag

    rpc.update(
        name=instance_name,
        details=game_mode,
        state=bottom_line,
        large_image=large_image_url,
        large_text=instance_name,
        start=start_time,
    )
    time.sleep(10)