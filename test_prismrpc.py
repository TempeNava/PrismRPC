"""Self-check: python test_prismrpc.py  (no Discord, network, tray or GUI needed)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

os.environ["XDG_DATA_HOME"] = os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()

# Fake pypresence that records what would be sent to Discord.
SENT = []


class FakePresence:
    def __init__(self, client_id, loop=None):
        pass

    def connect(self):
        pass

    def update(self, **kw):
        SENT.append(kw)

    def clear(self):
        SENT.append(None)

    def close(self):
        pass


sys.modules["pypresence"] = types.SimpleNamespace(Presence=FakePresence)

import diagnose  # noqa: E402
import online  # noqa: E402
import presence  # noqa: E402
import settings  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_defaults_update_and_bad_values(self):
        self.assertEqual(settings.load()["timer"], "game")
        settings.update(timer="world", show_button=False)
        settings_path = settings.settings_path()
        data = json.loads(settings_path.read_text())
        data["show_mod_count"] = "yes"       # wrong type: ignored
        data["timer"] = "bogus"              # not a choice: default
        settings_path.write_text(json.dumps(data))
        s = settings.load()
        self.assertTrue(s["show_mod_count"])
        self.assertEqual(s["timer"], "game")
        self.assertFalse(s["show_button"])
        settings_path.unlink()

    def test_per_instance_toggle(self):
        s = dict(settings.DEFAULTS, disabled_instances=["Foo"])
        self.assertFalse(settings.instance_enabled(s, "Foo"))
        self.assertTrue(settings.instance_enabled(s, "Bar"))
        self.assertFalse(settings.instance_enabled(dict(s, enabled=False), "Bar"))


class OnlineTests(unittest.TestCase):
    def pack_dir(self, kind, pid):
        d = Path(tempfile.mkdtemp())
        (d / "instance.cfg").write_text(f"ManagedPackType={kind}\nManagedPackID={pid}\n")
        return d

    def test_modrinth_curseforge_and_cache(self):
        calls = []

        def fake(url, timeout=5):
            calls.append(url)
            if "modrinth" in url:
                return {"icon_url": "http://i/m.png", "slug": "cool", "project_type": "modpack"}
            return {"thumbnail": "http://i/c.png", "urls": {"curseforge": "http://cf/page"}}

        real, online.get_json = online.get_json, fake
        try:
            m = online.lookup_pack(self.pack_dir("modrinth", "abc"))
            self.assertEqual(m, {"icon": "http://i/m.png", "url": "https://modrinth.com/modpack/cool"})
            c = online.lookup_pack(self.pack_dir("flame", "123"))
            self.assertEqual(c, {"icon": "http://i/c.png", "url": "http://cf/page"})
            n = len(calls)
            online.lookup_pack(self.pack_dir("flame", "123"))
            self.assertEqual(len(calls), n)  # served from cache

            def boom(url, timeout=5):
                raise OSError("down")
            online.get_json = boom   # CurseForge lookup fails: link still works
            self.assertEqual(online.lookup_pack(self.pack_dir("flame", "999")),
                             {"icon": "", "url": "https://www.curseforge.com/projects/999"})
        finally:
            online.get_json = real
        self.assertEqual(online.lookup_pack(Path(tempfile.mkdtemp())), {})  # not a managed pack

    def test_is_newer(self):
        self.assertTrue(online.is_newer("v1.2.0", "1.1.0"))
        self.assertTrue(online.is_newer("v1.10.0", "1.9.0"))
        self.assertFalse(online.is_newer("v1.1.0", "1.1.0"))
        self.assertFalse(online.is_newer("nightly", "1.1.0"))
        self.assertTrue(online.is_newer("1.1.0.1", "1.1.0"))     # 4th part counts
        self.assertFalse(online.is_newer("1.2", "1.2.0"))        # missing part is 0
        self.assertTrue(online.is_newer("1.2", "1.1.9"))
        self.assertTrue(online.is_newer("v1.2.0-beta.3", "1.1.0"))  # suffix ignored
        self.assertFalse(online.is_newer("v1.1.0+build7", "1.1.0"))
        self.assertFalse(online.is_newer("", "1.1.0"))

    def test_repo_slug(self):
        cases = {
            "TempeNava/PrismRPC": "TempeNava/PrismRPC",
            "https://github.com/TempeNava/PrismRPC": "TempeNava/PrismRPC",
            "https://github.com/TempeNava/PrismRPC/": "TempeNava/PrismRPC",
            "https://github.com/TempeNava/PrismRPC.git": "TempeNava/PrismRPC",
            "git@github.com:TempeNava/PrismRPC.git": "TempeNava/PrismRPC",
            "  https://www.github.com/TempeNava/PrismRPC/releases  ": "TempeNava/PrismRPC",
            "https://gitlab.com/owner/repo": "",
            "PrismRPC": "",
            "": "",
        }
        for value, expected in cases.items():
            self.assertEqual(online.repo_slug(value), expected, value)
        self.assertEqual(online.repo_url("https://github.com/a/b"), "https://github.com/a/b")
        self.assertEqual(online.repo_url(""), "")


class UpdateCheckTests(unittest.TestCase):
    """The update check used to build .../repos/https://github.com/... (404 on
    every call, swallowed), so it never found a release."""

    def setUp(self):
        settings.save_cache({})

    def tearDown(self):
        settings.save_cache({})

    def swap(self, repo, reply):
        calls = []

        def fake(url, timeout=5):
            calls.append(url)
            if isinstance(reply, Exception):
                raise reply
            return reply

        self.addCleanup(setattr, online, "get_json", online.get_json)
        self.addCleanup(setattr, online, "GITHUB_REPO", online.GITHUB_REPO)
        online.get_json, online.GITHUB_REPO = fake, repo
        return calls

    def test_asks_the_github_api_for_the_configured_repository(self):
        calls = self.swap("TempeNava/PrismRPC",
                          {"tag_name": "v9.9.9", "html_url": "https://github.com/t/tag"})
        self.assertEqual(online.check_for_update(force=True),
                         ("v9.9.9", "https://github.com/t/tag"))
        self.assertEqual(calls,
                         ["https://api.github.com/repos/TempeNava/PrismRPC/releases/latest"])

    def test_a_full_url_is_accepted_and_the_release_link_is_the_repo(self):
        calls = self.swap("https://github.com/TempeNava/PrismRPC/", {"tag_name": "v9.9.9"})
        self.assertEqual(online.check_for_update(force=True),
                         ("v9.9.9", "https://github.com/TempeNava/PrismRPC/releases"))
        self.assertEqual(calls,
                         ["https://api.github.com/repos/TempeNava/PrismRPC/releases/latest"])

    def test_no_repository_means_no_request(self):
        calls = self.swap("", {"tag_name": "v9.9.9"})
        self.assertIsNone(online.check_for_update(force=True))
        self.assertEqual(calls, [])

    def test_offline_run_keeps_using_the_cached_answer(self):
        settings.save_cache({"update": {"t": time.time(), "tag": "v9.9.9", "url": "u"}})
        self.swap("TempeNava/PrismRPC", OSError("offline"))
        self.assertEqual(online.check_for_update(force=True), ("v9.9.9", "u"))

    def test_daily_cache_is_used_without_a_request(self):
        settings.save_cache({"update": {"t": time.time(), "tag": "v9.9.9", "url": "u"}})
        calls = self.swap("TempeNava/PrismRPC", {"tag_name": "v1.0.0"})
        self.assertEqual(online.check_for_update(), ("v9.9.9", "u"))
        self.assertEqual(calls, [])


class PresenceTests(unittest.TestCase):
    def test_activity_options(self):
        inst = Path(tempfile.mkdtemp())
        sess = presence.Session("Pack", inst)
        sess.pack = {"icon": "http://i/x.png", "url": "http://p"}
        w = presence.LogWatcher(inst / "latest.log", 1000.0)
        s = dict(settings.DEFAULTS)
        a = presence.build_activity(sess, s, w, "1.21", "Fabric", 3, 1000.0)
        self.assertEqual(a["start"], 1000)
        self.assertEqual(a["buttons"][0]["url"], "http://p")
        self.assertIn("3 mods", a["state"])
        self.assertEqual(a["large_image"], "http://i/x.png")
        off = dict(s, timer="off", show_button=False, show_mod_count=False)
        a = presence.build_activity(sess, off, w, "1.21", "Fabric", 3, 1000.0)
        self.assertNotIn("start", a)
        self.assertNotIn("buttons", a)
        self.assertNotIn("mods", a["state"])
        w._set("single")  # world timer: only once a world is joined
        a = presence.build_activity(sess, dict(s, timer="world"), w, "1.21", "Fabric", 3, 1000.0)
        self.assertEqual(a["start"], int(w.since))

    def test_loop_pause_settings_and_stop(self):
        presence.POLL_SECONDS = 0.1
        presence.MIN_UPDATE_INTERVAL = 0
        presence.RECONNECT_INTERVAL = 0
        inst = Path(tempfile.mkdtemp())
        (inst / "minecraft" / "logs").mkdir(parents=True)
        sess = presence.Session("Pack", inst)
        sess.pack = {"icon": "", "url": "http://p"}
        pf = presence.pid_file(inst)
        pf.write_text("42")
        live = settings.LiveSettings()
        SENT.clear()
        t = threading.Thread(target=presence.presence_loop, daemon=True,
                             args=(sess, live, inst / "minecraft", 2, "1.21", "Forge", "42"))
        t.start()

        def wait_for(cond):
            for _ in range(60):
                if cond():
                    return True
                time.sleep(0.1)
            return False

        self.assertTrue(wait_for(lambda: SENT and SENT[-1] and SENT[-1]["details"] == "Main Menu"))
        self.assertEqual(SENT[-1]["name"], "Pack")
        sess.paused = True                       # tray "Pause" -> status cleared
        self.assertTrue(wait_for(lambda: SENT[-1] is None))
        sess.paused = False                      # resume -> status back
        self.assertTrue(wait_for(lambda: SENT[-1] is not None))
        settings.update(disabled_instances=[inst.name])   # settings window toggle, no restart
        self.assertTrue(wait_for(lambda: SENT[-1] is None))
        pf.unlink()                              # post-exit command
        t.join(5)
        self.assertFalse(t.is_alive())


class InstanceResolutionTests(unittest.TestCase):
    """`Path("")` is ".", and `is_dir()` accepts it: a run without INST_DIR used
    to treat the working directory as an instance and write its PID file there."""

    def setUp(self):
        self.addCleanup(setattr, sys, "argv", sys.argv)
        for key in ("INST_DIR", "INST_NAME"):
            previous = os.environ.pop(key, None)
            if previous is None:
                self.addCleanup(os.environ.pop, key, None)
            else:
                self.addCleanup(os.environ.__setitem__, key, previous)

    def test_no_directory_means_no_instance(self):
        sys.argv = ["main.py", "--child"]
        self.assertEqual(presence.resolve_instance(), ("Modded Minecraft", None))

    def test_an_empty_directory_is_not_the_current_one(self):
        os.environ["INST_DIR"] = ""
        sys.argv = ["main.py", "--child", ""]
        self.assertIsNone(presence.resolve_instance()[1])

    def test_environment_and_arguments_give_the_directory(self):
        inst = Path(tempfile.mkdtemp())
        os.environ["INST_DIR"], os.environ["INST_NAME"] = str(inst), "FromPrism"
        sys.argv = ["main.py"]
        self.assertEqual(presence.resolve_instance(), ("FromPrism", inst))
        sys.argv = ["main.py", "--child", "Named", str(inst)]
        self.assertEqual(presence.resolve_instance(), ("Named", inst))

    def test_option_arguments_are_not_taken_for_a_name(self):
        sys.argv = ["main.py", "--child", "-q", "--dry-run"]
        self.assertEqual(presence.resolve_instance(), ("Modded Minecraft", None))

    def test_nothing_is_written_to_the_working_directory(self):
        sys.argv = ["main.py", "--child"]
        before = set(os.listdir(Path.cwd()))
        presence.run(True)             # no instance: returns without starting
        self.assertEqual(set(os.listdir(Path.cwd())), before)
        presence.stop_instance(None)   # post-exit hook without a directory


class LogTailTests(unittest.TestCase):
    def write(self, text):
        log = Path(tempfile.mkdtemp()) / "latest.log"
        log.write_text(text, encoding="utf-8")
        return log

    def test_replays_the_status_out_of_a_log(self):
        log = self.write("[1] [Render thread/INFO]: Starting integrated minecraft "
                         "server version 1.21\n")
        self.assertEqual(presence.tail_log(log).details(), "Playing Singleplayer")
        log = self.write("[1] [Render thread/INFO]: Connecting to mc.example.com, 25565\n")
        watcher = presence.tail_log(log)
        self.assertEqual(watcher.details(), "Playing on mc.example.com")
        self.assertEqual(watcher.details(show_server=False), "Playing Multiplayer")
        log = self.write("[1] Connecting to mc.example.com, 25565\n[2] Stopping server\n")
        self.assertEqual(presence.tail_log(log).details(), "Main Menu")

    def test_a_missing_log_reads_as_the_main_menu(self):
        watcher = presence.tail_log(Path(tempfile.mkdtemp()) / "nope.log")
        self.assertEqual(watcher.details(), "Main Menu")

    def test_only_the_tail_is_read(self):
        log = self.write("[1] Connecting to old.example.com, 25565\n" + "[2] quiet\n" * 40)
        self.assertEqual(presence.tail_log(log).details(), "Playing on old.example.com")
        self.assertEqual(presence.tail_log(log, max_bytes=40).details(), "Main Menu")

    def test_a_line_cut_in_half_is_dropped(self):
        first = "[1] [Render thread/INFO]: Stopping server\n"
        second = "[2] [Render thread/INFO]: Connecting to mc.example.com, 25565\n"
        log = self.write(first + second)
        # Reading starts 10 bytes into the second line: "Connecting to ..." is
        # still inside the window, but the line is incomplete, so it must not
        # become the current server.
        watcher = presence.tail_log(log, max_bytes=len(second.encode()) - 10)
        self.assertEqual(watcher.mode, "menu")
        self.assertIsNone(watcher.server)


class DiagnoseTests(unittest.TestCase):
    def make_instance(self, rpc_mod=False, with_log=True):
        inst = Path(tempfile.mkdtemp()) / "Cube Warper"
        mc = inst / "minecraft"
        (mc / "logs").mkdir(parents=True)
        (mc / "mods").mkdir()
        for name in ("a.jar", "b.jar", "c.jar"):
            (mc / "mods" / name).write_text("", encoding="utf-8")
        if rpc_mod:
            (mc / "mods" / "craftpresence-2.7.1.jar").write_text("", encoding="utf-8")
        (inst / "instance.cfg").write_text(
            "name=Cube Warper\nManagedPackType=modrinth\nManagedPackID=abc123\n",
            encoding="utf-8")
        (inst / "mmc-pack.json").write_text(json.dumps({"components": [
            {"uid": "net.minecraft", "version": "1.21.1"},
            {"uid": "net.neoforged", "version": "21.1.9"},
        ]}), encoding="utf-8")
        if with_log:
            (mc / "logs" / "latest.log").write_text(
                "[1] [Render thread/INFO]: Starting integrated minecraft server "
                "version 1.21.1\n", encoding="utf-8")
        return inst

    def test_instance_section(self):
        inst = self.make_instance()
        report = diagnose.collect(inst)
        section = report["instance"]
        self.assertEqual(section["name"], "Cube Warper")
        self.assertEqual(section["minecraft"], "1.21.1")
        self.assertEqual(section["loader"], "NeoForge")
        self.assertEqual(section["mods"], 3)
        self.assertFalse(section["rich_presence_mod"])
        self.assertEqual(section["pack"], {"source": "modrinth", "id": "abc123"})
        self.assertEqual(section["log"]["status"], "Playing Singleplayer")
        self.assertIsNone(section["pid_file"])
        text = "\n".join(diagnose.render(report))
        for expected in ("Cube Warper", "1.21.1 (NeoForge)", "3 jar(s)",
                         "Playing Singleplayer", "modrinth abc123"):
            self.assertIn(expected, text)

    def test_a_running_presence_shows_up_as_a_pid_file(self):
        inst = self.make_instance()
        presence.pid_file(inst).write_text("4321", encoding="utf-8")
        self.assertEqual(diagnose.collect(inst)["instance"]["pid_file"], "4321")

    def test_log_override_and_a_missing_log(self):
        inst = self.make_instance(with_log=False)
        other = inst.parent / "other.log"
        other.write_text("[1] Connecting to other.example.org, 25565\n", encoding="utf-8")
        report = diagnose.collect(inst, log_path=other)
        self.assertEqual(report["instance"]["log"]["status"], "Playing on other.example.org")
        self.assertFalse(diagnose.collect(inst)["instance"]["log"]["exists"])
        notes = diagnose.problems(diagnose.collect(inst))
        self.assertTrue(any("latest.log" in note for note in notes))

    def test_json_output_is_plain_data(self):
        inst = self.make_instance()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = diagnose.run(["--diagnose", str(inst), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["instance"]["minecraft"], "1.21.1")

    def test_no_instance_given(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = diagnose.run(["--diagnose"])
        self.assertEqual(code, 0)
        self.assertIn("Instance: none given", out.getvalue())

    def test_notes_name_the_blockers(self):
        report = diagnose.collect(self.make_instance(rpc_mod=True))
        notes = diagnose.problems(report)
        self.assertTrue(any("Rich Presence mod" in note for note in notes))

        clean = diagnose.collect(self.make_instance())     # nothing installed here
        clean["modules"]["pypresence"] = True
        clean["prism"]["config_exists"] = True
        clean["prism"]["commands"] = {
            "PreLaunchCommand": {"value": "PrismRPC.exe \"x\"", "ours": True},
            "PostExitCommand": {"value": "PrismRPC.exe --stop", "ours": True},
        }
        clean["settings"]["values"]["enabled"] = True
        clean["instance"]["enabled"] = True
        self.assertEqual(diagnose.problems(clean), [])


class PlaytimeTests(unittest.TestCase):
    def inst(self, cfg_text):
        d = Path(tempfile.mkdtemp())
        (d / "instance.cfg").write_text(cfg_text)
        return d

    def test_saved_playtime(self):
        self.assertEqual(presence.saved_playtime(self.inst("name=X\ntotalTimePlayed=187200\n")), 187200)
        self.assertEqual(presence.saved_playtime(self.inst("name=X\n")), 0)           # never played
        self.assertEqual(presence.saved_playtime(self.inst("totalTimePlayed=abc\n")), 0)
        self.assertEqual(presence.saved_playtime(Path(tempfile.mkdtemp())), 0)         # no instance.cfg

    def test_format_playtime(self):
        self.assertEqual(presence.format_playtime(5), "1 min")
        self.assertEqual(presence.format_playtime(25 * 60), "25 min")
        self.assertEqual(presence.format_playtime(3.4 * 3600), "3.4 h")
        self.assertEqual(presence.format_playtime(52 * 3600 + 600), "52 h")

    def test_total_in_hover_text_and_setting(self):
        inst = Path(tempfile.mkdtemp())
        sess = presence.Session("Pack", inst)
        sess.base_seconds = 52 * 3600
        w = presence.LogWatcher(inst / "latest.log", 0.0)
        s = dict(settings.DEFAULTS)
        a = presence.build_activity(sess, s, w, "1.21", "Fabric", 3, sess.launched)
        self.assertEqual(a["large_text"], "Pack • 52 h played in total")
        a = presence.build_activity(sess, dict(s, show_total_time=False), w, "1.21", "Fabric", 3, sess.launched)
        self.assertEqual(a["large_text"], "Pack")
        sess.launched -= 2 * 3600            # two hours into this session: the total grows
        self.assertAlmostEqual(sess.total_seconds(), 54 * 3600, delta=5)


if __name__ == "__main__":
    unittest.main(verbosity=1)
