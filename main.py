"""VRChat Group Bulk-Ban - Android app (Kivy). Languages: Deutsch / English."""
import base64
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

API = "https://api.vrchat.cloud/api/1"
USER_AGENT = "GroupBanTool/1.0 (dein-discord-oder-mail)"  # add a contact here
DELAY = 1.5  # seconds between requests (rate limit)

# The disclaimer is intentionally always shown in English.
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
DISCLAIMER_ACCEPT = "I understand and accept"
DISCLAIMER_EXIT = "Exit"

STRINGS = {
    "de": {
        "lang_btn": "English",
        "h_login": "1. Login",
        "user_hint": "VRChat Username",
        "pw_hint": "Passwort",
        "login_btn": "Einloggen",
        "st_not_logged": "Nicht eingeloggt",
        "st_enter_creds": "Username und Passwort eingeben",
        "st_logging_in": "Logge ein ...",
        "st_login_failed": "Login fehlgeschlagen ({code})",
        "st_net_error": "Netzwerkfehler: {err}",
        "st_enter_2fa": "2FA-Code eingeben",
        "st_2fa_failed": "2FA fehlgeschlagen ({code})",
        "st_logged_in": "Eingeloggt als {name}",
        "h_group": "2. Gruppe",
        "group_hint": "Gruppen-ID (grp_...)",
        "h_list": "3. Blacklist (Komma, Semikolon oder Zeilenumbruch)",
        "names_hint": "Name1, Name2, usr_...",
        "count": "{n} Einträge",
        "dry": "Testlauf (nur suchen, nicht bannen)",
        "start": "Start",
        "stop": "Stopp",
        "st_empty_list": "Blacklist ist leer",
        "st_bad_group": "Gruppen-ID muss mit grp_ beginnen",
        "confirm_title": "Bestätigen",
        "confirm_text": "{n} Einträge wirklich in der Gruppe bannen?",
        "ok": "OK",
        "cancel": "Abbrechen",
        "twofa_title": "2FA",
        "twofa_text": "Bitte 2FA-Code eingeben:",
        "log_start": "Starte ...",
        "log_stop_req": "Stopp angefordert ...",
        "log_rate": "  Rate Limit, warte {s}s ...",
        "log_search_failed": "  Suche fehlgeschlagen: {code}",
        "log_no_match": "  -> kein exakter Treffer",
        "log_would_ban": "  -> würde bannen: {name} ({uid})",
        "log_banned": "  -> gebannt: {name}",
        "log_error": "  -> Fehler {code}: {text}",
        "log_net_error": "Netzwerkfehler: {err}",
        "log_summary": "--- Zusammenfassung ---",
        "log_counts": "Gebannt: {b} | Nicht gefunden: {m} | Fehlgeschlagen: {f}",
        "log_missing": "Nicht gefunden: {names}",
        "log_failed": "Fehlgeschlagen: {names}",
    },
    "en": {
        "lang_btn": "Deutsch",
        "h_login": "1. Login",
        "user_hint": "VRChat username",
        "pw_hint": "Password",
        "login_btn": "Log in",
        "st_not_logged": "Not logged in",
        "st_enter_creds": "Enter username and password",
        "st_logging_in": "Logging in ...",
        "st_login_failed": "Login failed ({code})",
        "st_net_error": "Network error: {err}",
        "st_enter_2fa": "Enter 2FA code",
        "st_2fa_failed": "2FA failed ({code})",
        "st_logged_in": "Logged in as {name}",
        "h_group": "2. Group",
        "group_hint": "Group ID (grp_...)",
        "h_list": "3. Blacklist (comma, semicolon or new line)",
        "names_hint": "Name1, Name2, usr_...",
        "count": "{n} entries",
        "dry": "Test run (search only, no bans)",
        "start": "Start",
        "stop": "Stop",
        "st_empty_list": "Blacklist is empty",
        "st_bad_group": "Group ID must start with grp_",
        "confirm_title": "Confirm",
        "confirm_text": "Really ban {n} entries in the group?",
        "ok": "OK",
        "cancel": "Cancel",
        "twofa_title": "2FA",
        "twofa_text": "Please enter your 2FA code:",
        "log_start": "Starting ...",
        "log_stop_req": "Stop requested ...",
        "log_rate": "  Rate limit, waiting {s}s ...",
        "log_search_failed": "  Search failed: {code}",
        "log_no_match": "  -> no exact match",
        "log_would_ban": "  -> would ban: {name} ({uid})",
        "log_banned": "  -> banned: {name}",
        "log_error": "  -> error {code}: {text}",
        "log_net_error": "Network error: {err}",
        "log_summary": "--- Summary ---",
        "log_counts": "Banned: {b} | Not found: {m} | Failed: {f}",
        "log_missing": "Not found: {names}",
        "log_failed": "Failed: {names}",
    },
}


def parse_names(text):
    """Split at comma, semicolon and new line; drop empty entries and duplicates."""
    parts = re.split(r"[,;\n]+", text)
    names = [p.strip() for p in parts if p.strip() and not p.strip().startswith("#")]
    return list(dict.fromkeys(names))


class BanApp(App):
    title = "VRChat Group Ban"

    # ---------------- language ----------------
    def tr(self, key, **kw):
        return STRINGS[self.lang][key].format(**kw)

    def reg(self, widget, key, attr="text"):
        """Register a widget so its text follows the selected language."""
        self._tr_widgets.append((widget, attr, key))
        setattr(widget, attr, self.tr(key))
        return widget

    def _lang_file(self):
        return os.path.join(self.user_data_dir, "lang.txt")

    def load_language(self):
        try:
            with open(self._lang_file(), encoding="utf-8") as f:
                code = f.read().strip()
            if code in STRINGS:
                return code
        except Exception:
            pass
        return "de"

    def toggle_language(self, *_):
        self.lang = "en" if self.lang == "de" else "de"
        try:
            with open(self._lang_file(), "w", encoding="utf-8") as f:
                f.write(self.lang)
        except Exception:
            pass
        self.apply_language()

    def apply_language(self):
        for widget, attr, key in self._tr_widgets:
            setattr(widget, attr, self.tr(key))
        self.update_count()
        self.render_status()

    # ---------------- UI ----------------
    def build(self):
        Window.softinput_mode = "below_target"
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.logged_in = False
        self.running = False
        self.stop_event = threading.Event()
        self.log_lines = []
        self._tr_widgets = []
        self.lang = self.load_language()
        self.status_state = ("st_not_logged", True, {})

        scroll = ScrollView(do_scroll_x=False)
        root = BoxLayout(orientation="vertical", size_hint_y=None, padding=dp(12), spacing=dp(8))
        root.bind(minimum_height=root.setter("height"))
        scroll.add_widget(root)

        def header(key):
            lb = Label(size_hint_y=None, height=dp(32), bold=True, halign="left")
            lb.bind(size=lambda w, s: setattr(w, "text_size", s))
            self.reg(lb, key)
            root.add_widget(lb)

        def field(hint_key, **kw):
            ti = TextInput(multiline=False, size_hint_y=None, height=dp(46), **kw)
            self.reg(ti, hint_key, attr="hint_text")
            root.add_widget(ti)
            return ti

        top = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        top.add_widget(Label(text="VRChat Group Ban", bold=True, halign="left"))
        self.lang_btn = Button(size_hint_x=None, width=dp(110))
        self.reg(self.lang_btn, "lang_btn")
        self.lang_btn.bind(on_release=self.toggle_language)
        top.add_widget(self.lang_btn)
        root.add_widget(top)

        header("h_login")
        self.user_in = field("user_hint", write_tab=False)
        self.pw_in = field("pw_hint", password=True, write_tab=False)
        self.login_btn = Button(size_hint_y=None, height=dp(48))
        self.reg(self.login_btn, "login_btn")
        self.login_btn.bind(on_release=self.login)
        root.add_widget(self.login_btn)
        self.status = Label(size_hint_y=None, height=dp(30))
        root.add_widget(self.status)

        header("h_group")
        self.group_in = field("group_hint", write_tab=False)

        header("h_list")
        self.names_in = TextInput(multiline=True, size_hint_y=None, height=dp(150))
        self.reg(self.names_in, "names_hint", attr="hint_text")
        self.names_in.bind(text=self.update_count)
        root.add_widget(self.names_in)
        self.count_lb = Label(size_hint_y=None, height=dp(28))
        root.add_widget(self.count_lb)

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.dry_cb = CheckBox(active=True, size_hint_x=None, width=dp(44))
        dry_lb = Label(halign="left")
        dry_lb.bind(size=lambda w, s: setattr(w, "text_size", s))
        self.reg(dry_lb, "dry")
        row.add_widget(self.dry_cb)
        row.add_widget(dry_lb)
        root.add_widget(row)

        btns = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.start_btn = Button(disabled=True)
        self.reg(self.start_btn, "start")
        self.start_btn.bind(on_release=self.start)
        self.stop_btn = Button(disabled=True, background_color=(0.8, 0.25, 0.25, 1))
        self.reg(self.stop_btn, "stop")
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

        self.update_count()
        self.render_status()
        return scroll

    def on_start(self):
        Clock.schedule_once(self.show_disclaimer, 0.3)

    # ---------------- disclaimer (always English) ----------------
    def show_disclaimer(self, *_):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        sv = ScrollView(do_scroll_x=False)
        lb = Label(text=DISCLAIMER_TEXT, size_hint_y=None, halign="left", valign="top")
        lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        lb.bind(texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        sv.add_widget(lb)
        box.add_widget(sv)
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        accept = Button(text=DISCLAIMER_ACCEPT)
        leave = Button(text=DISCLAIMER_EXIT, background_color=(0.8, 0.25, 0.25, 1))
        row.add_widget(accept)
        row.add_widget(leave)
        box.add_widget(row)
        pop = Popup(
            title=DISCLAIMER_TITLE,
            title_color=(1, 0.45, 0.45, 1),
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

    def log(self, key, **kw):
        self.ui(self._log, self.tr(key, **kw))

    def _log(self, msg):
        self.log_lines.append(msg)
        self.log_lines = self.log_lines[-500:]
        self.log_lb.text = "\n".join(self.log_lines)
        Clock.schedule_once(lambda dt: setattr(self.log_scroll, "scroll_y", 0), 0.05)

    def set_status(self, key, ok=True, kw=None):
        self.status_state = (key, ok, kw or {})
        self.render_status()

    def render_status(self):
        key, ok, kw = self.status_state
        self.status.text = self.tr(key, **kw)
        self.status.color = (0.5, 0.9, 0.5, 1) if ok else (1, 0.5, 0.5, 1)

    def set_progress(self, value, maximum=None):
        if maximum is not None:
            self.progress.max = max(maximum, 1)
        self.progress.value = value

    def set_running(self, running):
        self.running = running
        self.start_btn.disabled = running or not self.logged_in
        self.stop_btn.disabled = not running

    def update_count(self, *_):
        self.count_lb.text = self.tr("count", n=len(parse_names(self.names_in.text)))

    def popup(self, title, text, on_yes=None, with_input=False):
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
        box.add_widget(Label(text=text))
        ti = None
        if with_input:
            ti = TextInput(multiline=False, size_hint_y=None, height=dp(46), input_filter="int")
            box.add_widget(ti)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        yes = Button(text=self.tr("ok"))
        no = Button(text=self.tr("cancel"))
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
                self.log("log_rate", s=wait)
                for _ in range(wait):
                    if self.stop_event.is_set():
                        return r
                    time.sleep(1)
                continue
            return r
        return r

    # ---------------- login ----------------
    def login(self, *_):
        user, pw = self.user_in.text.strip(), self.pw_in.text
        if not user or not pw:
            self.set_status("st_enter_creds", ok=False)
            return
        self.login_btn.disabled = True
        self.set_status("st_logging_in")
        threading.Thread(target=self._login_thread, args=(user, pw), daemon=True).start()

    def _login_thread(self, user, pw):
        try:
            cred = urllib.parse.quote(user) + ":" + urllib.parse.quote(pw)
            token = base64.b64encode(cred.encode()).decode()
            r = self.session.get(
                API + "/auth/user", headers={"Authorization": f"Basic {token}"}, timeout=30
            )
            if r.status_code != 200:
                self.ui(self.set_status, "st_login_failed", False, {"code": r.status_code})
                self.ui(setattr, self.login_btn, "disabled", False)
                return
            data = r.json()
            methods = data.get("requiresTwoFactorAuth")
            if methods:
                self.ui(self.ask_2fa, methods)
            else:
                self.ui(self.login_done, data)
        except requests.RequestException as e:
            self.ui(self.set_status, "st_net_error", False, {"err": str(e)})
            self.ui(setattr, self.login_btn, "disabled", False)

    def ask_2fa(self, methods):
        self.set_status("st_enter_2fa")
        self.popup(
            self.tr("twofa_title"),
            self.tr("twofa_text"),
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
                self.ui(self.set_status, "st_2fa_failed", False, {"code": v.status_code})
                return
            data = self.session.get(API + "/auth/user", timeout=30).json()
            self.ui(self.login_done, data)
        except requests.RequestException as e:
            self.ui(self.set_status, "st_net_error", False, {"err": str(e)})

    def login_done(self, data):
        self.logged_in = True
        self.pw_in.text = ""
        self.login_btn.disabled = True
        self.set_status("st_logged_in", True, {"name": data.get("displayName", "")})
        self.set_running(False)

    # ---------------- banning ----------------
    def start(self, *_):
        names = parse_names(self.names_in.text)
        group = self.group_in.text.strip()
        dry = self.dry_cb.active
        if not names:
            self.set_status("st_empty_list", ok=False)
            return
        if not group.startswith("grp_"):
            self.set_status("st_bad_group", ok=False)
            return

        def go(_=None):
            self.stop_event.clear()
            self.log_lines = []
            self.log("log_start")
            self.set_progress(0, len(names))
            self.set_running(True)
            threading.Thread(target=self.worker, args=(names, group, dry), daemon=True).start()

        if dry:
            go()
        else:
            self.popup(
                self.tr("confirm_title"), self.tr("confirm_text", n=len(names)), on_yes=go
            )

    def stop_run(self, *_):
        self.stop_event.set()
        self.log("log_stop_req")

    def resolve(self, name):
        if name.startswith("usr_"):
            return name, name
        r = self.request("GET", "/users", params={"search": name, "n": 20})
        if r.status_code != 200:
            self.log("log_search_failed", code=r.status_code)
            return None, None
        for u in r.json():
            if u.get("displayName", "").lower() == name.lower():
                return u["id"], u["displayName"]
        return None, None

    def worker(self, names, group, dry):
        banned = failed = 0
        missing, errors = [], []
        try:
            for i, name in enumerate(names, 1):
                if self.stop_event.is_set():
                    break
                self.ui(self._log, f"[{i}/{len(names)}] {name}")
                uid, shown = self.resolve(name)
                time.sleep(DELAY)
                if not uid:
                    self.log("log_no_match")
                    missing.append(name)
                elif dry:
                    self.log("log_would_ban", name=shown, uid=uid)
                else:
                    r = self.request("POST", f"/groups/{group}/bans", json={"userId": uid})
                    time.sleep(DELAY)
                    if r.status_code == 200:
                        self.log("log_banned", name=shown)
                        banned += 1
                    else:
                        self.log("log_error", code=r.status_code, text=r.text[:120])
                        failed += 1
                        errors.append(name)
                self.ui(self.set_progress, i)
        except requests.RequestException as e:
            self.log("log_net_error", err=str(e))
        self.log("log_summary")
        self.log("log_counts", b=banned, m=len(missing), f=failed)
        if missing:
            self.log("log_missing", names=", ".join(missing))
        if errors:
            self.log("log_failed", names=", ".join(errors))
        self.ui(self.set_running, False)


if __name__ == "__main__":
    BanApp().run()
