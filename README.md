# PrismRPC

Discord Rich Presence for [Prism Launcher](https://prismlauncher.org/), with no mod to install in your modpacks.

PrismRPC shows your Prism instance name, the Minecraft version and loader, your mod count, and whether you're on the main menu, in singleplayer or on a server. It installs once and then works for every instance.

## Features

- **Instance name as the title.** Your profile shows e.g. *Cube Warper* instead of a generic "Minecraft".
- **Live status.** Reads the game log to switch between `Main Menu`, `Playing Singleplayer` and `Playing on <server>` (the server address can be turned off).
- **Pack details.** Minecraft version, loader (Fabric, Forge, NeoForge, Quilt), mod count, and the pack's icon for Modrinth-managed modpacks.
- **One-click setup.** Run the installer once; it registers itself in Prism's global launch commands.
- **Stays out of the way.** If an instance already has a Rich Presence mod (CraftPresence, Simple RPC, ...), PrismRPC does nothing for that instance.
- **Resilient.** Reconnects if Discord is restarted, and clears your status when the game closes.

## Install

1. Download `PrismRPC-Setup.exe` from the [Releases](../../releases) page.
2. Close Prism Launcher completely.
3. Run `PrismRPC-Setup.exe` once. A message confirms it's installed.
4. Launch any instance from Prism.

The installer is unsigned, so Windows SmartScreen may warn you the first time. Choose **More info → Run anyway**.

### What the installer does

- Copies the app to `%LOCALAPPDATA%\PrismRPC\app`.
- Sets Prism's global **Pre-launch** and **Post-exit** commands (Settings → Custom Commands), after saving a backup as `prismlauncher.cfg.bak`.
- If you already use your own commands there, it leaves them untouched and tells you instead of overwriting them.

It adds no startup entry, touches no registry keys and only uses the network to fetch a Modrinth pack icon.

### Uninstall

Close Prism, then run:

```
"%LOCALAPPDATA%\PrismRPC\app\PrismRPC.exe" --uninstall
```

and delete the `%LOCALAPPDATA%\PrismRPC` folder.

## How it works

When an instance starts, Prism runs PrismRPC as its pre-launch command. It starts a small background process and returns immediately, so game start-up isn't delayed. That process follows the instance's `latest.log` to work out what you're doing and updates Discord. When the game closes, Prism runs PrismRPC with `--stop` and the status is cleared. Each instance is tracked separately, so several can run at once.

## Configuration

Edit [`config.py`](config.py) and rebuild:

| Setting | Purpose |
|---|---|
| `SHOW_SERVER_ADDRESS` | `False` shows just "Playing Multiplayer" |
| `POLL_SECONDS`, `MIN_UPDATE_INTERVAL` | How often the log is checked / Discord is updated |
| `RPC_MODS` | Mod file names that make PrismRPC step aside |
| `CLIENT_ID`, `FALLBACK_IMAGE` | Discord application and art asset |

**Forking?** Create your own application in the [Discord Developer Portal](https://discord.com/developers/applications), upload a Rich Presence art asset named `mc_icon` (or change `FALLBACK_IMAGE`), and set your own `CLIENT_ID`.

## Build from source

Requires Python 3.8+ on Windows.

```
pip install -r requirements.txt
build.bat
```

This produces `dist\PrismRPC-Setup.exe`. It first builds the app as a folder, then wraps that folder in a one-file installer. The installed app is a folder build on purpose: a one-file exe unpacks itself to a temp folder on every launch, which is slow and can trigger "Failed to remove temporary directory" warnings when Prism runs it for a split second.

For development you can run `python main.py` instead. That registers the script itself (not an exe) with Prism.

## Troubleshooting

- **No status in Discord.** Discord must be running, and *Settings → Activity Privacy → Share your detected activity* must be on.
- **Installer says a command is already set.** You have your own Pre-launch/Post-exit command in Prism. Clear it (Settings → Custom Commands) or chain the commands yourself, then run the installer again.
- **Installer says Prism is running.** Prism rewrites its settings when it exits, which would undo the change. Close it completely, including the tray icon, and retry.
- **Wrong or missing server/singleplayer state.** The state comes from `logs/latest.log`; unusual launch setups or log-changing mods may hide the lines it looks for.

## Platform support

The installer is Windows-only. The Python source also contains Linux and macOS paths for Prism's config but this has not been tested.
