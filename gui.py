"""Settings window (customtkinter). Every change is saved immediately."""
import queue
import threading
import webbrowser

import settings as st
from config import VERSION
from online import UPDATE_CHECK_ENABLED, check_for_update

TIMER_LABELS = {"game": "Game", "world": "World", "off": "Off"}
MUTED = ("gray45", "gray60")  # (light mode, dark mode)


def open_window():
    import customtkinter as ctk
    from PIL import Image

    ctk.set_appearance_mode("system")  # follows Windows light/dark
    ctk.set_default_color_theme("green")

    root = ctk.CTk()
    root.title("PrismRPC")
    root.geometry("480x600")
    root.resizable(False, False)
    icon = st.resource_path("icon.ico")

    def set_icon():
        try:
            root.iconbitmap(str(icon))
        except Exception:
            pass

    root.after(250, set_icon)  # customtkinter sets its own icon at ~200 ms
    small = ctk.CTkFont(size=12)
    current = st.load()

    # --- header ---------------------------------------------------------------
    header = ctk.CTkFrame(root, fg_color="transparent")
    header.pack(fill="x", padx=24, pady=(22, 8))
    try:  # NEAREST keeps the pixel art sharp
        logo = Image.open(icon).convert("RGBA").resize((96, 96), Image.NEAREST)
        ctk.CTkLabel(header, text="", image=ctk.CTkImage(light_image=logo, size=(48, 48))
                     ).pack(side="left", padx=(0, 14))
    except Exception:
        pass
    titles = ctk.CTkFrame(header, fg_color="transparent")
    titles.pack(side="left")
    ctk.CTkLabel(titles, text="PrismRPC", font=ctk.CTkFont(size=22, weight="bold")
                 ).pack(anchor="w")
    ctk.CTkLabel(titles, text="Discord Rich Presence for Prism Launcher",
                 text_color=MUTED, font=small).pack(anchor="w")

    tabs = ctk.CTkTabview(root, height=380)
    tabs.pack(fill="both", expand=True, padx=20, pady=(4, 0))
    general, display, inst = (tabs.add(n) for n in ("General", "Display", "Instances"))

    def switch_row(parent, key, text, hint=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=7)
        labels = ctk.CTkFrame(row, fg_color="transparent")
        labels.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(labels, text=text, anchor="w").pack(anchor="w")
        if hint:
            ctk.CTkLabel(labels, text=hint, anchor="w", justify="left", wraplength=300,
                         text_color=MUTED, font=small).pack(anchor="w")
        sw = ctk.CTkSwitch(row, text="", width=46,
                           command=lambda: st.update(**{key: bool(sw.get())}))
        sw.pack(side="right")
        if current[key]:
            sw.select()

    # --- General --------------------------------------------------------------
    switch_row(general, "enabled", "Enable PrismRPC", "Turns your status off everywhere.")
    switch_row(general, "show_tray", "Tray icon while playing", "Applies the next time you launch a game.")
    switch_row(general, "check_updates", "Check for new versions")

    # --- Display --------------------------------------------------------------
    switch_row(display, "show_server_address", "Server address", "Warning: shows the server's address on your profile. Keep it off for private servers.")
    switch_row(display, "show_mod_count", "Number of mods")
    switch_row(display, "show_total_time", "Total play time", "Shown when you hover the pack icon.")
    switch_row(display, "show_button", "\"View modpack\" button", "Others see it on your profile; Discord hides it from you.")
    ctk.CTkLabel(display, text="Timer", anchor="w").pack(anchor="w", pady=(14, 4))
    seg = ctk.CTkSegmentedButton(
        display, values=list(TIMER_LABELS.values()),
        command=lambda v: st.update(timer={l: k for k, l in TIMER_LABELS.items()}[v]))
    seg.set(TIMER_LABELS[current["timer"]])
    seg.pack(fill="x")
    ctk.CTkLabel(display, text="Game: since launch. World: since joining a world or server.",
                 text_color=MUTED, font=small, anchor="w").pack(anchor="w", pady=(4, 0))

    # --- Instances ------------------------------------------------------------
    instances = st.list_instances()
    if not instances:
        ctk.CTkLabel(inst, text="No Prism instances found.", text_color=MUTED).pack(anchor="w")
    else:
        ctk.CTkLabel(inst, text="Untick an instance to hide its status.",
                     text_color=MUTED, font=small).pack(anchor="w", pady=(0, 6))
        lst = ctk.CTkScrollableFrame(inst, height=250)
        lst.pack(fill="both", expand=True)
        disabled = set(current["disabled_instances"])

        def toggle(key, on):
            names = set(st.load()["disabled_instances"])
            (names.discard if on else names.add)(key)
            st.update(disabled_instances=sorted(names))

        def add_instance(key, name):
            cb = ctk.CTkCheckBox(lst, text=name, command=lambda: toggle(key, bool(cb.get())))
            cb.pack(anchor="w", pady=5, padx=4)
            if key not in disabled:
                cb.select()

        for key, name in instances:
            add_instance(key, name)

    # --- footer ---------------------------------------------------------------
    footer = ctk.CTkFrame(root, fg_color="transparent")
    footer.pack(fill="x", padx=24, pady=(12, 4))
    ctk.CTkLabel(footer, text=f"Version {VERSION}", text_color=MUTED, font=small
                 ).pack(side="left")
    ctk.CTkButton(footer, text="Close", width=90, command=root.destroy).pack(side="right")
    check = ctk.CTkButton(footer, text="Check for updates", width=140, fg_color="transparent",
                          border_width=1, text_color=("gray10", "gray90"))
    check.pack(side="right", padx=8)
    download = ctk.CTkButton(footer, text="Download", width=100)  # shown only for updates
    status = ctk.CTkLabel(root, text="", text_color=MUTED, font=small, anchor="w")
    status.pack(anchor="w", padx=26, pady=(0, 14))
    results = queue.Queue()  # filled by the update-check thread

    def check_now():
        # The request can take seconds: never run it on the window's thread.
        status.configure(text="Checking...")
        download.pack_forget()
        threading.Thread(target=lambda: results.put(check_for_update(force=True)),
                         daemon=True).start()
        wait_for_result()

    def wait_for_result():
        try:
            found = results.get_nowait()
        except queue.Empty:
            root.after(100, wait_for_result)
            return
        if found:
            status.configure(text=f"Version {found[0]} is available.")
            download.configure(command=lambda: webbrowser.open(found[1]))
            download.pack(side="right", padx=(0, 8))
        else:
            download.pack_forget()
            status.configure(text="You're up to date.")

    check.configure(command=check_now)
    if not UPDATE_CHECK_ENABLED:
        check.configure(state="disabled")
        status.configure(text="Update checking isn't set up in this build.")

    def center():
        try:
            root.eval("tk::PlaceWindow . center")
        except Exception:
            pass

    root.after(10, center)
    root.mainloop()
