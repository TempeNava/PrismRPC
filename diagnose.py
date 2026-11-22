"""`--diagnose`: a read-only report of what PrismRPC sees for an instance.

Nothing in here starts a presence, writes a setting or edits Prism's config. It
only reads (settings, Prism's config, the instance folder and its log) and then
prints the facts plus the reasons a status might not be showing up.

    PrismRPC --diagnose [instance_dir] [--log FILE] [--json]
"""
import importlib.util
import json
import platform
import sys
from pathlib import Path

import online
import presence
import settings as st
from config import GITHUB_REPO, VERSION

MODULES = ("pypresence", "pystray", "PIL", "customtkinter", "tkinter")


def _has_module(name):
    """True when the module is importable (already imported counts: the frozen
    app imports pypresence lazily, but only bundles what is installed)."""
    if name in sys.modules:
        return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def log_report(path):
    """What the instance's latest.log adds up to right now."""
    path = Path(path)
    report = {"path": str(path), "exists": path.is_file()}
    if not report["exists"]:
        return report
    watcher = presence.tail_log(path)
    try:
        report["size"] = path.stat().st_size
    except OSError:
        report["size"] = 0
    report.update(mode=watcher.mode, server=watcher.server,
                  status=watcher.details())
    return report


def prism_report():
    cfg = st.prism_cfg_path()
    lines = []
    if cfg and cfg.is_file():
        try:
            lines = cfg.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
    commands = {}
    for key in ("PreLaunchCommand", "PostExitCommand"):
        value = st.cfg_get(lines, key) if lines else ""
        commands[key] = {"value": value, "ours": presence.is_ours(value)}
    return {
        "config": str(cfg) if cfg else None,
        "config_exists": bool(cfg and cfg.is_file()),
        "running": st.prism_running(),
        "instances_dir": str(st.instances_dir() or ""),
        "instances": [name for _key, name in st.list_instances()],
        "commands": commands,
    }


def instance_report(inst_dir):
    inst_dir = Path(inst_dir)
    mc_dir = presence.find_mc_dir(inst_dir)
    has_rpc_mod, mod_count = presence.scan_mods(mc_dir)
    mc_version, loader = presence.read_pack_info(inst_dir)
    pf = presence.pid_file(inst_dir)
    source, pid = online.managed_pack(inst_dir)
    return {
        "name": st.instance_name(inst_dir),
        "key": inst_dir.name,
        "dir": str(inst_dir),
        "minecraft_dir": str(mc_dir),
        "minecraft": mc_version,
        "loader": loader,
        "mods": mod_count,
        "rich_presence_mod": has_rpc_mod,
        "pid_file": pf.read_text().strip() if pf.is_file() else None,
        "pack": {"source": source, "id": pid} if source else None,
        "log": log_report(mc_dir / "logs" / "latest.log"),
    }


def collect(inst_dir=None, log_path=None):
    """Every fact the report shows, as a plain dict (safe to json.dumps)."""
    s = st.load()
    if inst_dir is None:
        inst_dir = presence.resolve_instance()[1]
    report = {
        "version": VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "data_dir": str(st.data_dir()),
        "settings": {"path": str(st.settings_path()), "values": s},
        "modules": {name: _has_module(name) for name in MODULES},
        "update_check": {"repo": online.repo_slug(GITHUB_REPO),
                         "enabled": online.UPDATE_CHECK_ENABLED},
        "prism": prism_report(),
        "instance": instance_report(inst_dir) if inst_dir else None,
    }
    if report["instance"]:
        report["instance"]["enabled"] = st.instance_enabled(s, report["instance"]["key"])
        if log_path:
            report["instance"]["log"] = log_report(log_path)
    return report


def problems(report):
    """The likely blockers, in plain sentences."""
    notes = []
    if not report["modules"]["pypresence"]:
        notes.append("pypresence is not installed, so nothing can be sent to Discord "
                     "(pip install -r requirements.txt).")
    prism = report["prism"]
    if not prism["config_exists"]:
        notes.append("prismlauncher.cfg was not found: start Prism Launcher once, close it, "
                     "then run the installer.")
    for key, command in prism["commands"].items():
        if not command["value"]:
            notes.append(f"Prism's {key} is empty: run the installer to add it.")
        elif not command["ours"]:
            notes.append(f"Prism's {key} points somewhere else, so PrismRPC is never started.")
    if not report["settings"]["values"]["enabled"]:
        notes.append("PrismRPC is switched off in the settings.")
    inst = report["instance"]
    if inst:
        if not inst["enabled"]:
            notes.append(f"This instance ({inst['name']}) is unticked in the settings, "
                         "so its status stays hidden.")
        if inst["rich_presence_mod"]:
            notes.append("A Rich Presence mod is installed for this instance, so PrismRPC "
                         "stays out of the way.")
        if not inst["minecraft"]:
            notes.append("mmc-pack.json has no net.minecraft component, so there is no "
                         "version to show.")
        if not inst["log"]["exists"]:
            notes.append("logs/latest.log does not exist yet: launch the game once, then "
                         "run this again.")
    return notes


def _yes(flag):
    return "yes" if flag else "no"


def render(report):
    """The human-readable lines for `collect(...)`."""
    _m = report["modules"]
    lines = [
        f"PrismRPC {report['version']} diagnose",
        f"  python      {report['python']} ({report['platform']})",
        f"  data dir    {report['data_dir']}",
        f"  settings    {report['settings']['path']}",
        "  modules     " + ", ".join(f"{n}: {'yes' if ok else 'no'}" for n, ok in _m.items()),
        f"  update      {report['update_check']['repo'] or '(not configured)'}",
        "",
        "Prism Launcher",
        f"  config      {report['prism']['config'] or '(unknown)'}"
        f" ({'found' if report['prism']['config_exists'] else 'missing'})",
        f"  running     {_yes(report['prism']['running'])}",
    ]
    for key, command in report["prism"]["commands"].items():
        state = "(empty)" if not command["value"] else ("ours" if command["ours"] else "not ours")
        lines.append(f"  {key:<16} {state}: {command['value'] or '-'}")
    lines.append(f"  instances   {len(report['prism']['instances'])}")

    inst = report["instance"]
    if not inst:
        lines += ["", "Instance: none given (pass the instance folder to check one)"]
    else:
        lines += [
            "",
            f"Instance: {inst['name']} ({inst['key']})",
            f"  dir         {inst['dir']}",
            f"  minecraft   {inst['minecraft'] or '(unknown)'} ({inst['loader']})",
            f"  mods        {inst['mods']} jar(s)"
            + (", Rich Presence mod present" if inst["rich_presence_mod"] else ""),
            f"  enabled     {_yes(inst['enabled'])}",
            f"  pid file    {inst['pid_file'] or 'none (not running)'}",
            f"  pack        "
            + (f"{inst['pack']['source']} {inst['pack']['id']}" if inst["pack"] else "not a managed pack"),
            f"  log         {inst['log']['path']}"
            + (f" ({inst['log']['size']} bytes)" if inst["log"]["exists"] else " (missing)"),
        ]
        if inst["log"]["exists"]:
            lines.append(f"  status      {inst['log']['status']}")

    notes = problems(report)
    lines.append("")
    if notes:
        lines.append("Check:")
        lines += [f"  - {note}" for note in notes]
    else:
        lines.append("Check: nothing obvious is in the way.")
    return lines


def run(argv):
    """Entry point for `--diagnose`. Returns the process exit code."""
    want_json = "--json" in argv
    rest = [a for a in argv if a not in ("--diagnose", "--json")]
    log_path = None
    if "--log" in rest:
        at = rest.index("--log")
        log_path = rest[at + 1] if at + 1 < len(rest) else ""
        del rest[at:at + 2]
        if not log_path:
            print("--diagnose: --log needs a file", file=sys.stderr)
            return 2
    dirs = [a for a in rest if not a.startswith("-")]
    if dirs and not Path(dirs[0]).is_dir():
        print(f"--diagnose: no such instance folder: {dirs[0]}", file=sys.stderr)
        return 2
    report = collect(Path(dirs[0]) if dirs else None, log_path)
    if want_json:
        print(json.dumps(report, indent=2))
    else:
        print("\n".join(render(report)))
    return 0
