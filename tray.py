"""Tray icon shown while a game is running (needs pystray + Pillow)."""
import threading
import webbrowser

from presence import spawn
from settings import resource_path


def run(session, worker):
    """Show a tray icon on this (main) thread and run `worker` on another.

    Returns True once the worker has finished, or False if no tray icon could
    be created (missing library, no tray) - then the caller runs the worker itself.
    """
    try:
        import pystray
        from PIL import Image
        image = Image.open(resource_path("icon.ico"))
        image.load()
    except Exception:
        return False

    icon = None
    thread = None

    def toggle_pause(_icon, _item):
        session.paused = not session.paused
        _icon.update_menu()

    def open_settings(_icon, _item):
        spawn(["--settings"])

    def open_update(_icon, _item):
        if session.update:
            webbrowser.open(session.update[1])

    def quit_(_icon, _item):
        session.stop.set()
        _icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(session.name, lambda *_: None, enabled=False),
        pystray.MenuItem("Pause status", toggle_pause,
                         checked=lambda _item: session.paused),
        pystray.MenuItem(lambda _item: f"Update available: {session.update[0]}",
                         open_update, visible=lambda _item: bool(session.update)),
        pystray.MenuItem("Settings...", open_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit PrismRPC", quit_),
    )

    try:
        icon = pystray.Icon("PrismRPC", image, f"PrismRPC - {session.name}", menu)
    except Exception:
        return False

    def refresh():
        try:
            icon.update_menu()
        except Exception:
            pass

    def notify(message):
        try:
            icon.notify(message, "PrismRPC")
        except Exception:
            pass

    session.on_change = refresh
    session.notify = notify

    def setup(_icon):
        nonlocal thread
        _icon.visible = True

        def target():
            try:
                worker()
            finally:
                _icon.stop()  # game closed (or Quit): remove the icon

        thread = threading.Thread(target=target, daemon=True)
        thread.start()

    try:
        icon.run(setup)
    except Exception:
        if thread is None:
            return False  # tray never came up; caller runs the worker itself
    if thread is not None:
        session.stop.set()
        thread.join(timeout=10)  # let it clear the Discord status
    return True
