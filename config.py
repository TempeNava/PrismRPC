"""Settings you may want to tweak (rebuild the exe after changing)."""

VERSION = "1.0.0"

# Discord application ID. If you fork this, create your own application at
# https://discord.com/developers/applications, upload a Rich Presence art asset
# named FALLBACK_IMAGE below, and put your own ID here.
CLIENT_ID = "1573820368626385046"

POLL_SECONDS = 5            # how often the game log is checked
MIN_UPDATE_INTERVAL = 15    # Discord rate-limits presence updates
RECONNECT_INTERVAL = 15     # retry delay while Discord is closed
SHOW_SERVER_ADDRESS = True  # set False to only show "Playing Multiplayer"
FALLBACK_IMAGE = "mc_icon"  # asset key uploaded to the Discord application

# If one of these mods is installed, PrismRPC stays out of the way.
# (Bare "presence" is NOT listed: it matches unrelated mods like Presence Footsteps.)
RPC_MODS = (
    "craftpresence",
    "simple-rpc",
    "simplerpc",
    "discordrpc",
    "discord-rpc",
    "mc-rpc",
)

LOADERS = {
    "net.fabricmc.fabric-loader": "Fabric",
    "net.neoforged": "NeoForge",
    "net.minecraftforge": "Forge",
    "org.quiltmc.quilt-loader": "Quilt",
}
