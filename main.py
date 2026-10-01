"""VRChat Group Bulk-Ban - Android app (Kivy)."""
import base64
import json
import os
import re
import threading
import time
import urllib.parse

import requests

try:  # SSL certificates on Android
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

API = "https://api.vrchat.cloud/api/1"
USER_AGENT = "GroupBanTool/1.0 (your-discord-or-email)"  # add a contact here
DELAY = 1.5  # seconds between requests (rate limit)

FOLDER_NAME = "VRChatGroupBan"  # reports are saved to Download/VRChatGroupBan
HISTORY_LIMIT = 500  # how many bans the in-app history keeps
HISTORY_SHOWN = 100  # how many bans the history window shows

RED = (1, 0.4, 0.4, 1)
GREEN = (0.5, 0.9, 0.5, 1)
WHITE = (1, 1, 1, 1)

DISCLAIMER_TITLE = "WARNING - PLEASE READ"
DISCLAIMER_TEXT = (
    "This tool is unofficial and is NOT affiliated with, endorsed by or "
    "supported by VRChat Inc.\n\n"
    "It uses the VRChat API to perform actions (such as banning users from a "
    "group) automatically. Automating actions or logging in through "
    "third-party tools may violate the VRChat Terms of Service.\n\n"
    "By continuing, you acknowledge and agree that:\n"
    "- Your VRChat account may be warned, restricted, suspended or banned "
    "for using this tool.\n"
    "- The developer is NOT responsible or liable for any ban, suspension or "
    "other action taken against your VRChat account.\n"
    "- The developer is also not liable for users banned by mistake, wrong or "
    "outdated blacklist entries, or any other damage resulting from the use "
    "of this tool.\n"
    "- You use this tool entirely at your own risk.\n\n"
    "Tip: Always run a test run first and double-check your blacklist."
)


def parse_names(text):
    """Split at comma, semicolon and new line; drop empty entries and duplicates."""
    parts = re.split(r"[,;\n]+", text)
    names = [p.strip() for p in parts if p.strip() and not p.strip().startswith("#")]
    return list(dict.fromkeys(names))


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def write_text_file(folder, filename, text):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def save_report(filename, text):
    """Save a text file where the user can easily find it.

    Android: Download/VRChatGroupBan/ (via MediaStore, no permission needed).
    Fallback: the app's own folder on shared storage.
    Other systems: ~/Downloads/VRChatGroupBan/.
    Returns (ok, location_or_error).
    """
    if platform == "android":
        first_error = ""
        try:
            from jnius import autoclass

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            ContentValues = autoclass("android.content.ContentValues")
            Downloads = autoclass("android.provider.MediaStore$Downloads")
            resolver = activity.getContentResolver()
            values = ContentValues()
            values.put("_display_name", filename)
            values.put("mime_type", "text/plain")
            values.put("relative_path", f"Download/{FOLDER_NAME}")
            uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
            if uri is None:
                raise RuntimeError("could not create file in Downloads")
            stream = resolver.openOutputStream(uri)
            writer = autoclass("java.io.OutputStreamWriter")(stream, "UTF-8")
            writer.write(text)
            writer.flush()
            writer.close()
            return True, f"Download/{FOLDER_NAME}/{filename}"
        except Exception as e:
            first_error = str(e)
        try:
            from jnius import autoclass

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            base = activity.getExternalFilesDir(None).getAbsolutePath()
            return True, write_text_file(base, filename, text)
        except Exception as e:
            return False, f"{first_error} / {e}"
    try:
        folder = os.path.join(os.path.expanduser("~"), "Downloads", FOLDER_NAME)
        return True, write_text_file(folder, filename, text)
    except Exception as e:
        return False, str(e)


class BanApp(App):
    title = "VRChat Group Ban"

    # ---------------- UI ----------------
    def build(self):
        Window.softinput_mode = "below_target"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.logged_in = False
        self.running = False
        self.stop_event = threading.Event()
        self.history_lock = threading.Lock()
        self.log_lines = []

        scroll = ScrollView(do_scroll_x=False)
        root = BoxLayout(orientation="vertical", size_hint_y=None, padding=dp(12), spacing=dp(8))
        root.bind(minimum_height=root.setter("height"))
        scroll.add_widget(root)

        def header(text):
            lb = Label(
                text=text, size_hint_y=None, height=dp(32), bold=True, halign="left", valign="middle"
            )
            lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
            lb.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(8)))
            root.add_widget(lb)

        def field(hint, **kw):
            ti = TextInput(
                hint_text=hint,
                multiline=False,
                size_hint_y=None,
                height=dp(48),
                padding=[dp(10), dp(13), dp(10), dp(13)],
                **kw,
            )
            root.add_widget(ti)
            return ti

        root.add_widget(
            Label(text="VRChat Group Ban", bold=True, font_size=dp(20), size_hint_y=None, height=dp(44))
        )

        header("1. Login")
        self.user_in = field("VRChat username", write_tab=False)
        self.pw_in = field("Password", password=True, write_tab=False)
        self.login_btn = Button(text="Log in", size_hint_y=None, height=dp(48))
        self.login_btn.bind(on_release=self.login)
        root.add_widget(self.login_btn)
        self.status = Label(size_hint_y=None, height=dp(30))
        root.add_widget(self.status)
        self.set_status("Not logged in", False)

        header("2. Group")
        self.group_in = field("Group ID (grp_...)", write_tab=False)

        header("3. Blacklist (comma, semicolon or new line)")
        self.names_in = TextInput(
            hint_text="Name1, Name2, usr_...",
            multiline=True,
            size_hint_y=None,
            height=dp(150),
            padding=[dp(10)] * 4,
        )
        self.names_in.bind(text=self.update_count)
        root.add_widget(self.names_in)
        self.count_lb = Label(text="0 entries", size_hint_y=None, height=dp(28))
        root.add_widget(self.count_lb)

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.dry_cb = CheckBox(active=True, size_hint_x=None, width=dp(44))
        dry_lb = Label(
            text="Test run (search only, no bans)", halign="left", valign="middle"
        )
        dry_lb.bind(size=lambda w, s: setattr(w, "text_size", s))
        row.add_widget(self.dry_cb)
        row.add_widget(dry_lb)
        root.add_widget(row)

        btns = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.start_btn = Button(text="Start", disabled=True)
        self.start_btn.bind(on_release=self.start)
        self.stop_btn = Button(text="Stop", disabled=True, background_color=(0.8, 0.25, 0.25, 1))
        self.stop_btn.bind(on_release=self.stop_run)
        btns.add_widget(self.start_btn)
        btns.add_widget(self.stop_btn)
        root.add_widget(btns)

        self.progress = ProgressBar(max=1, value=0, size_hint_y=None, height=dp(20))
        root.add_widget(self.progress)

        self.log_scroll = ScrollView(size_hint_y=None, height=dp(280), do_scroll_x=False)
        self.log_lb = Label(text="", size_hint_y=None, halign="left", valign="top")
        self.log_lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        self.log_lb.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        self.log_scroll.add_widget(self.log_lb)
        root.add_widget(self.log_scroll)

        tools = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.copy_btn = Button(text="Copy logs")
        self.copy_btn.bind(on_release=self.copy_logs)
        self.history_btn = Button(text="Ban history")
        self.history_btn.bind(on_release=self.show_history)
        tools.add_widget(self.copy_btn)
        tools.add_widget(self.history_btn)
        root.add_widget(tools)
        return scroll

    def on_start(self):
        Clock.schedule_once(self.show_disclaimer, 0.3)

    # ---------------- disclaimer ----------------
    def show_disclaimer(self, *_):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        sv = ScrollView(do_scroll_x=False)
        lb = Label(text=DISCLAIMER_TEXT, size_hint_y=None, halign="left", valign="top")
        lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        lb.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        sv.add_widget(lb)
        box.add_widget(sv)
        accept = Button(text="I understand and accept", size_hint_y=None, height=dp(52))
        leave = Button(
            text="Exit", size_hint_y=None, height=dp(48), background_color=(0.8, 0.25, 0.25, 1)
        )
        box.add_widget(accept)
        box.add_widget(leave)
        pop = Popup(
            title=DISCLAIMER_TITLE,
            title_color=RED,
            content=box,
            size_hint=(0.94, 0.9),
            auto_dismiss=False,
        )
        accept.bind(on_release=lambda *_: pop.dismiss())
        leave.bind(on_release=lambda *_: self.stop())
        pop.open()

    # ---------------- helpers (thread-safe) ----------------
    def ui(self, fn, *args):
        Clock.schedule_once(lambda dt: fn(*args), 0)

    def log(self, msg):
        self.ui(self._log, msg)

    def _log(self, msg):
        self.log_lines.append(msg)
        self.log_lines = self.log_lines[-500:]
        self.log_lb.text = "\n".join(self.log_lines)
        Clock.schedule_once(lambda dt: setattr(self.log_scroll, "scroll_y", 0), 0.05)

    def set_status(self, text, ok=True):
        """ok=True green, ok=False red, ok=None neutral (white)."""
        self.status.text = text
        self.status.color = WHITE if ok is None else (GREEN if ok else RED)

    def set_progress(self, value, maximum=None):
        if maximum is not None:
            self.progress.max = max(maximum, 1)
        self.progress.value = value

    def set_running(self, running):
        self.running = running
        self.start_btn.disabled = running or not self.logged_in
        self.stop_btn.disabled = not running

    def update_count(self, *_):
        n = len(parse_names(self.names_in.text))
        self.count_lb.text = f"{n} {'entry' if n == 1 else 'entries'}"

    def flash(self, button, message, base_text):
        """Show a short message on a button, then restore its label."""
        button.text = message
        Clock.schedule_once(lambda dt: setattr(button, "text", base_text), 1.5)

    def popup(self, title, text, on_yes=None, with_input=False):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        box.add_widget(Label(text=text))
        ti = None
        if with_input:
            ti = TextInput(multiline=False, size_hint_y=None, height=dp(48), input_filter="int")
            box.add_widget(ti)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        yes = Button(text="OK")
        no = Button(text="Cancel")
        row.add_widget(yes)
        row.add_widget(no)
        box.add_widget(row)
        pop = Popup(title=title, content=box, size_hint=(0.9, 0.5), auto_dismiss=False)

        def _yes(*_):
            pop.dismiss()
            if on_yes:
                on_yes(ti.text if ti else None)

        yes.bind(on_release=_yes)
        no.bind(on_release=lambda *_: pop.dismiss())
        pop.open()

    def request(self, method, path, **kw):
        r = None
        for attempt in range(5):
            r = self.session.request(method, API + path, timeout=30, **kw)
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                self.log(f"  Rate limit, waiting {wait}s ...")
                for _ in range(wait):
                    if self.stop_event.is_set():
                        return r
                    time.sleep(1)
                continue
            return r
        return r

    # ---------------- logs & ban history ----------------
    def copy_logs(self, *_):
        text = "\n".join(self.log_lines)
        if not text:
            self.flash(self.copy_btn, "Nothing to copy", "Copy logs")
            return
        Clipboard.copy(text)
        self.flash(self.copy_btn, "Copied!", "Copy logs")

    def history_path(self):
        return os.path.join(self.user_data_dir, "ban_history.json")

    def load_history(self):
        try:
            with open(self.history_path(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def add_history(self, entry):
        with self.history_lock:
            data = self.load_history()
            data.append(entry)
            data = data[-HISTORY_LIMIT:]
            try:
                with open(self.history_path(), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            except Exception as e:
                self.log(f"  Could not save ban history: {e}")

    def history_text(self):
        data = self.load_history()
        if not data:
            return "No bans recorded yet."
        lines = [
            f"{e.get('time', '')}  {e.get('name', '')}  ({e.get('id', '')})  [{e.get('group', '')}]"
            for e in reversed(data[-HISTORY_SHOWN:])
        ]
        return "\n".join(lines)

    def show_history(self, *_):
        text = self.history_text()
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        info = Label(
            text=f"Last bans, newest first.\nReports are saved in: Download/{FOLDER_NAME}",
            size_hint_y=None,
            height=dp(56),
            halign="left",
            valign="middle",
        )
        info.bind(size=lambda w, s: setattr(w, "text_size", s))
        box.add_widget(info)
        sv = ScrollView(do_scroll_x=False)
        lb = Label(text=text, size_hint_y=None, halign="left", valign="top")
        lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        lb.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        sv.add_widget(lb)
        box.add_widget(sv)
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        copy = Button(text="Copy")
        close = Button(text="Close")
        row.add_widget(copy)
        row.add_widget(close)
        box.add_widget(row)
        pop = Popup(title="Ban history", content=box, size_hint=(0.94, 0.9), auto_dismiss=False)

        def _copy(*_):
            Clipboard.copy(text)
            self.flash(copy, "Copied!", "Copy")

        copy.bind(on_release=_copy)
        close.bind(on_release=lambda *_: pop.dismiss())
        pop.open()

    def build_report(self, group, stamp, banned_list, missing, errors):
        lines = [
            "VRChat Group Ban - report",
            f"Date:  {stamp}",
            f"Group: {group}",
            "",
            f"Banned ({len(banned_list)}):",
        ]
        lines += [f"  {n} ({u})" for n, u in banned_list] or ["  -"]
        lines += ["", f"Not found ({len(missing)}):"]
        lines += [f"  {n}" for n in missing] or ["  -"]
        lines += ["", f"Failed ({len(errors)}):"]
        lines += [f"  {n}" for n in errors] or ["  -"]
        return "\n".join(lines) + "\n"

    # ---------------- login ----------------
    def login(self, *_):
        user, pw = self.user_in.text.strip(), self.pw_in.text
        if not user or not pw:
            self.set_status("Enter username and password", False)
            return
        self.login_btn.disabled = True
        self.set_status("Logging in ...", None)
        threading.Thread(target=self._login_thread, args=(user, pw), daemon=True).start()

    def _login_thread(self, user, pw):
        try:
            cred = urllib.parse.quote(user) + ":" + urllib.parse.quote(pw)
            token = base64.b64encode(cred.encode()).decode()
            r = self.session.get(
                API + "/auth/user", headers={"Authorization": f"Basic {token}"}, timeout=30
            )
            if r.status_code != 200:
                self.ui(self.set_status, f"Login failed ({r.status_code})", False)
                self.ui(setattr, self.login_btn, "disabled", False)
                return
            data = r.json()
            methods = data.get("requiresTwoFactorAuth")
            if methods:
                self.ui(self.ask_2fa, methods)
            else:
                self.ui(self.login_done, data)
        except requests.RequestException as e:
            self.ui(self.set_status, f"Network error: {e}", False)
            self.ui(setattr, self.login_btn, "disabled", False)

    def ask_2fa(self, methods):
        self.set_status("Enter 2FA code", None)
        self.popup(
            "2FA",
            "Please enter your 2FA code:",
            on_yes=lambda code: threading.Thread(
                target=self._2fa_thread, args=(code or "", methods), daemon=True
            ).start(),
            with_input=True,
        )
        self.login_btn.disabled = False

    def _2fa_thread(self, code, methods):
        try:
            if "emailOtp" in methods:
                ep = "/auth/twofactorauth/emailotp/verify"
            elif "totp" in methods:
                ep = "/auth/twofactorauth/totp/verify"
            else:
                ep = "/auth/twofactorauth/otp/verify"
            v = self.session.post(API + ep, json={"code": code.strip()}, timeout=30)
            if v.status_code != 200 or not v.json().get("verified"):
                self.ui(self.set_status, f"2FA failed ({v.status_code})", False)
                return
            data = self.session.get(API + "/auth/user", timeout=30).json()
            self.ui(self.login_done, data)
        except requests.RequestException as e:
            self.ui(self.set_status, f"Network error: {e}", False)

    def login_done(self, data):
        self.logged_in = True
        self.pw_in.text = ""
        self.login_btn.disabled = True
        self.set_status(f"Logged in as {data.get('displayName', '')}", True)
        self.set_running(False)

    # ---------------- banning ----------------
    def start(self, *_):
        names = parse_names(self.names_in.text)
        group = self.group_in.text.strip()
        dry = self.dry_cb.active
        if not names:
            self.set_status("Blacklist is empty", False)
            return
        if not group.startswith("grp_"):
            self.set_status("Group ID must start with grp_", False)
            return

        def go(_=None):
            self.stop_event.clear()
            self.log_lines = []
            self._log("Starting ...")
            self.set_progress(0, len(names))
            self.set_running(True)
            threading.Thread(target=self.worker, args=(names, group, dry), daemon=True).start()

        if dry:
            go()
        else:
            self.popup("Confirm", f"Really ban {len(names)} entries in the group?", on_yes=go)

    def stop_run(self, *_):
        self.stop_event.set()
        self.log("Stop requested ...")

    def resolve(self, name):
        if name.startswith("usr_"):
            return name, name
        r = self.request("GET", "/users", params={"search": name, "n": 20})
        if r.status_code != 200:
            self.log(f"  Search failed: {r.status_code}")
            return None, None
        for u in r.json():
            if u.get("displayName", "").lower() == name.lower():
                return u["id"], u["displayName"]
        return None, None

    def worker(self, names, group, dry):
        banned_list, missing, errors = [], [], []
        started = now_str()
        try:
            for i, name in enumerate(names, 1):
                if self.stop_event.is_set():
                    break
                self.log(f"[{i}/{len(names)}] {name}")
                uid, shown = self.resolve(name)
                time.sleep(DELAY)
                if not uid:
                    self.log("  -> no exact match")
                    missing.append(name)
                elif dry:
                    self.log(f"  -> would ban: {shown} ({uid})")
                else:
                    r = self.request("POST", f"/groups/{group}/bans", json={"userId": uid})
                    time.sleep(DELAY)
                    if r.status_code == 200:
                        self.log(f"  -> banned: {shown}")
                        banned_list.append((shown, uid))
                        self.add_history(
                            {"time": now_str(), "name": shown, "id": uid, "group": group}
                        )
                    else:
                        self.log(f"  -> error {r.status_code}: {r.text[:120]}")
                        errors.append(name)
                self.ui(self.set_progress, i)
        except requests.RequestException as e:
            self.log(f"Network error: {e}")
        self.log("--- Summary ---")
        self.log(f"Banned: {len(banned_list)} | Not found: {len(missing)} | Failed: {len(errors)}")
        if missing:
            self.log("Not found: " + ", ".join(missing))
        if errors:
            self.log("Failed: " + ", ".join(errors))
        if not dry and (banned_list or missing or errors):
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            report = self.build_report(group, started, banned_list, missing, errors)
            ok, where = save_report(f"ban_report_{stamp}.txt", report)
            self.log(f"Report saved: {where}" if ok else f"Could not save report: {where}")
        self.ui(self.set_running, False)


if __name__ == "__main__":
    BanApp().run()
