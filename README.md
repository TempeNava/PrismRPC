# Prism Discord Rich Presence (PrismRPC)

A lightweight, zero-dependency background utility for **Prism Launcher** that brings rich, dynamic Discord Rich Presence to your modded Minecraft instances—**without requiring you to install a Rich Presence mod into every modpack**.

## Features

* **Dynamic Modpack Title:** Sets the main bold title on your Discord profile to your exact Prism instance name (e.g., *Cube Warper*) instead of generic "Modded Minecraft".
* **Live Game Status:** Automatically reads local Minecraft logs to switch between `Main Menu`, `Playing Singleplayer`, and `Playing Multiplayer`.
* **Pack Artwork & Stats:** Pulls official modpack logos automatically from Modrinth, displays your Minecraft version and loader (Forge, Fabric, NeoForge, Quilt), and shows your active mod count.
* **Zero-Config Auto-Setup:** Automatically injects itself into Prism Launcher's global configuration file on first run so users don't have to manually copy-paste custom commands.
* **Smart Conflict Skipping:** Automatically detects if a modpack already has a built-in Rich Presence mod (like *CraftPresence* or *Simple RPC*) and exits instantly to prevent overlapping statuses.

---

## Installation & Usage
1. Download the compiled `prism_rpc.exe` from the [Releases](https://github.com/YOUR_USERNAME/YOUR_REPO/releases) page.
2. Place it anywhere on your computer (e.g., `C:\Tools\prism_rpc.exe`).
3. Run the `.exe` once. It will automatically detect your Prism Launcher configuration and link itself.
4. Launch any modpack through Prism Launcher and enjoy your dynamic Discord status!