"""Self-check: python test_prismrpc.py  (no Discord, network, tray or GUI needed)."""
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


if __name__ == "__main__":
    unittest.main(verbosity=1)
