"""VRChat Group Bulk-Ban - Android app (Kivy), dark UI in the style of VRCX."""
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

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ColorProperty, NumericProperty, StringProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import escape_markup, get_color_from_hex, platform

APP_VERSION = "1.1"
API = "https://api.vrchat.cloud/api/1"
# VRChat asks third-party tools to identify themselves with a way to contact the developer.
USER_AGENT = f"VRCGroupBanTool/{APP_VERSION} (github.com/Kasunya/VRCGroupBanToolAndroid)"
DELAY = 1.5  # seconds between requests (rate limit)
TIMEOUT = 30

FOLDER_NAME = "VRChatGroupBan"  # reports are saved to Download/VRChatGroupBan
HISTORY_LIMIT = 500  # how many bans the in-app history keeps
HISTORY_SHOWN = 100  # how many bans the history window shows
LOG_LIMIT = 400  # log lines kept on screen (one widget per line)
GROUPS_MAX_AGE = 24 * 3600  # reload the group list after a day
GROUP_CHECK_DELAY = 0.3  # seconds between group permission checks
BAN_PERMISSIONS = ("*", "group-bans-manage")

# Only real VRChat IDs are accepted, so nothing else can end up in an API path.
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
GROUP_ID_RE = re.compile("grp_" + _UUID)
USER_ID_RE = re.compile("usr_" + _UUID)

# Element Plus dark palette, which VRCX is built on.
H = {
    "bg": "#141517",
    "bar": "#1b1c1f",
    "card": "#1e1f22",
    "card_hi": "#292a2e",
    "border": "#35373c",
    "input": "#131416",
    "text": "#e5eaf3",
    "text2": "#a3a6ad",
    "text3": "#6c6e72",
    "primary": "#409eff",
    "primary_down": "#337ecc",
    "success": "#67c23a",
    "warning": "#e6a23c",
    "danger": "#f56c6c",
    "danger_down": "#c45656",
    "white": "#ffffff",
}
C = {k: get_color_from_hex(v) for k, v in H.items()}


def alpha(color, a):
    return list(color[:3]) + [a]


# VRCX trust rank colors
TRUST = [
    ("system_trust_veteran", "Trusted User", "#8143e6"),
    ("system_trust_trusted", "Known User", "#ff7b42"),
    ("system_trust_known", "User", "#2bcf5c"),
    ("system_trust_basic", "New User", "#1778ff"),
]


def trust_of(tags):
    tags = tags or []
    for tag, label, color in TRUST:
        if tag in tags:
            return label, color
    return "Visitor", "#cccccc"


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


class SessionExpired(Exception):
    """VRChat answered 401 - the login is no longer valid."""


def parse_names(text):
    """Split at comma, semicolon and new line; drop empty entries, comments and duplicates."""
    seen, names = set(), []
    for part in re.split(r"[,;\r\n]+", text):
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        key = part.casefold()
        if key not in seen:
            seen.add(key)
            names.append(part)
    return names


def extract_group_id(text):
    m = GROUP_ID_RE.search(text or "")
    return m.group(0) if m else None


def extract_user_id(text):
    m = USER_ID_RE.search(text or "")
    return m.group(0) if m else None


def can_ban(group, user_id):
    """True / False if the group data tells whether the user may ban, None if unknown."""
    if user_id and group.get("ownerId") == user_id:
        return True
    member = group.get("myMember")
    if not isinstance(member, dict):
        return None
    perms = member.get("permissions")
    if not isinstance(perms, list):
        return None
    return any(p in perms for p in BAN_PERMISSIONS)


def safe_json(r, kind=dict):
    try:
        data = r.json()
    except ValueError:
        return kind()
    return data if isinstance(data, kind) else kind()


def api_error(r):
    err = safe_json(r).get("error")
    msg = err.get("message") if isinstance(err, dict) else None
    return f"{r.status_code}: {msg or r.text[:120]}"


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

    Android 10+: Download/VRChatGroupBan/ (via MediaStore, no permission needed).
    Fallback: the app's own folder on shared storage.
    Other systems: ~/Downloads/VRChatGroupBan/.
    Returns (ok, location_or_error).
    """
    if platform == "android":
        first_error = "MediaStore needs Android 10+"
        try:
            from jnius import autoclass

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            if autoclass("android.os.Build$VERSION").SDK_INT >= 29:
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
                writer = None
                try:
                    stream = resolver.openOutputStream(uri)
                    writer = autoclass("java.io.OutputStreamWriter")(stream, "UTF-8")
                    writer.write(text)
                    writer.flush()
                except Exception:
                    resolver.delete(uri, None, None)  # don't leave an empty file behind
                    raise
                finally:
                    if writer is not None:
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


def keep_screen_on(on):
    """Keep the display on during a run so Android does not suspend the app."""
    if platform != "android":
        return
    try:
        from android.runnable import run_on_ui_thread
        from jnius import autoclass

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        flag = autoclass("android.view.WindowManager$LayoutParams").FLAG_KEEP_SCREEN_ON

        @run_on_ui_thread
        def apply():
            window = activity.getWindow()
            if on:
                window.addFlags(flag)
            else:
                window.clearFlags(flag)

        apply()
    except Exception:
        pass


# ---------------- widgets ----------------
KV_COLORS = "\n".join(f"#:set c_{k} {tuple(v)}" for k, v in C.items())
KV = (
    KV_COLORS
    + f"""
#:set c_disabled_bg {tuple(alpha(C["card_hi"], 0.55))}
#:set c_selection {tuple(alpha(C["primary"], 0.35))}
#:set c_primary_soft {tuple(alpha(C["primary"], 0.12))}
"""
    + """
<BaseCard>:
    orientation: 'vertical'
    padding: dp(16), dp(14)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: c_card
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]
        Color:
            rgba: c_border
        Line:
            rounded_rectangle: (self.x + .5, self.y + .5, self.width - 1, self.height - 1, dp(12))
            width: 1

<Card>:
    size_hint_y: None
    height: self.minimum_height

<WrapLabel>:
    size_hint_y: None
    text_size: self.width, None
    height: self.texture_size[1]
    halign: 'left'
    valign: 'middle'
    color: c_text
    font_size: sp(14)

<Title>:
    bold: True
    font_size: sp(16)

<Muted>:
    font_size: sp(12.5)
    color: c_text2

<RowLabel>:
    text_size: self.size
    halign: 'left'
    valign: 'middle'
    shorten: True
    shorten_from: 'right'
    color: c_text
    font_size: sp(14)

<LogLine>:
    markup: True
    font_size: sp(12.5)
    color: c_text2

<FlatButton>:
    size_hint_y: None
    height: dp(46)
    bold: True
    font_size: sp(14.5)
    color: self.fg
    disabled_color: c_text3
    halign: 'center'
    valign: 'middle'
    text_size: self.size
    padding: dp(8), 0
    shorten: True
    canvas.before:
        Color:
            rgba: c_disabled_bg if self.disabled else (self.bg_down if self.state == 'down' else self.bg)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: c_border if self.disabled else self.line
        Line:
            rounded_rectangle: (self.x + .5, self.y + .5, self.width - 1, self.height - 1, dp(8))
            width: 1

<StyledInput>:
    background_normal: ''
    background_active: ''
    background_disabled_normal: ''
    background_color: 0, 0, 0, 0
    foreground_color: c_text
    disabled_foreground_color: c_text3
    hint_text_color: c_text3
    cursor_color: c_primary
    selection_color: c_selection
    font_size: sp(15)
    padding: dp(12), dp(13), dp(12), dp(13)
    size_hint_y: None
    height: dp(48)
    write_tab: False
    canvas.before:
        Color:
            rgba: c_input
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
        Color:
            rgba: c_primary if self.focus else c_border
        Line:
            rounded_rectangle: (self.x + .5, self.y + .5, self.width - 1, self.height - 1, dp(8))
            width: 1.3 if self.focus else 1
        # TextInput draws its text with the last color set in canvas.before - restore it
        Color:
            rgba: self.disabled_foreground_color if self.disabled else (self.hint_text_color if not self.text else self.foreground_color)

<GroupRow>:
    size_hint_y: None
    height: dp(60)
    padding: dp(42), dp(8), dp(8), dp(8)
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: c_primary_soft if self.selected else c_input
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: c_primary if self.selected else c_border
        Line:
            rounded_rectangle: (self.x + .5, self.y + .5, self.width - 1, self.height - 1, dp(10))
            width: 1.2 if self.selected else 1
        Color:
            rgba: c_primary if self.selected else c_text3
        Line:
            circle: (self.x + dp(21), self.center_y, dp(8))
            width: 1.3
        Color:
            rgba: c_primary if self.selected else (0, 0, 0, 0)
        Ellipse:
            pos: self.x + dp(21) - dp(4.5), self.center_y - dp(4.5)
            size: dp(9), dp(9)

<Toggle>:
    size_hint: None, None
    size: dp(46), dp(26)
    canvas:
        Color:
            rgba: c_disabled_bg if self.disabled else (c_primary if self.active else c_border)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.height / 2]
        Color:
            rgba: (1, 1, 1, .5) if self.disabled else (1, 1, 1, 1)
        Ellipse:
            pos: self.x + dp(3) + self.knob * (self.width - self.height), self.y + dp(3)
            size: self.height - dp(6), self.height - dp(6)

<Bar>:
    size_hint_y: None
    height: dp(8)
    canvas:
        Color:
            rgba: c_input
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.height / 2]
        Color:
            rgba: self.color
        RoundedRectangle:
            pos: self.pos
            size: (max(self.height, self.width * min(1, self.value / max(self.max, 1))) if self.value > 0 else 0), self.height
            radius: [self.height / 2]

<StatBox>:
    orientation: 'vertical'
    padding: dp(4), dp(8)
    canvas.before:
        Color:
            rgba: c_input
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
    Label:
        text: root.value
        bold: True
        font_size: sp(20)
        color: root.color
    Label:
        text: root.caption
        font_size: sp(11.5)
        color: c_text2

<Pill>:
    size_hint: None, None
    size: self.texture_size
    padding: dp(10), dp(4)
    font_size: sp(12)
    color: c_text2
    canvas.before:
        Color:
            rgba: c_input
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.height / 2]

<Avatar>:
    size_hint: None, None
    bold: True
    font_size: self.height * .45
    color: c_bg
    canvas.before:
        Color:
            rgba: self.ring
        Ellipse:
            pos: self.pos
            size: self.size

<Console>:
    do_scroll_x: False
    bar_width: dp(4)
    bar_color: c_text3
    bar_inactive_color: c_border
    canvas.before:
        Color:
            rgba: c_input
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]

<TopBar>:
    size_hint_y: None
    height: dp(58)
    padding: dp(16), dp(10)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: c_bar
        Rectangle:
            pos: self.pos
            size: self.size
        Color:
            rgba: c_border
        Rectangle:
            pos: self.x, self.y
            size: self.width, 1

<Toast>:
    size_hint: None, None
    size: self.texture_size
    padding: dp(16), dp(12)
    font_size: sp(13.5)
    color: c_text
    halign: 'center'
    valign: 'middle'
    canvas.before:
        Color:
            rgba: c_card_hi
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]
        Color:
            rgba: self.accent
        Line:
            rounded_rectangle: (self.x + .5, self.y + .5, self.width - 1, self.height - 1, dp(10))
            width: 1.2
"""
)


class BaseCard(BoxLayout):
    pass


class Card(BaseCard):
    pass


class WrapLabel(Label):
    pass


class Title(WrapLabel):
    pass


class Muted(WrapLabel):
    pass


class RowLabel(Label):
    pass


class LogLine(WrapLabel):
    pass


class Pill(Label):
    pass


class Toast(Label):
    accent = ColorProperty(C["border"])


class Avatar(Label):
    ring = ColorProperty(C["text2"])


class Console(ScrollView):
    pass


class TopBar(BoxLayout):
    pass


class StyledInput(TextInput):
    pass


class StatBox(BoxLayout):
    value = StringProperty("0")
    caption = StringProperty("")
    color = ColorProperty(C["text"])


class Bar(Widget):
    value = NumericProperty(0)
    max = NumericProperty(1)
    color = ColorProperty(C["primary"])


class GroupRow(ButtonBehavior, BoxLayout):
    selected = BooleanProperty(False)


class Toggle(ButtonBehavior, Widget):
    active = BooleanProperty(False)
    knob = NumericProperty(0)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.knob = 1 if self.active else 0

    def on_release(self):
        self.active = not self.active

    def on_active(self, *_):
        Animation.cancel_all(self, "knob")
        Animation(knob=1 if self.active else 0, d=0.15, t="out_quad").start(self)


BUTTON_KINDS = {
    # kind: (background, background pressed, border, text)
    "primary": (C["primary"], C["primary_down"], C["primary"], C["white"]),
    "default": (C["card_hi"], C["border"], C["border"], C["text"]),
    "danger": (alpha(C["danger"], 0.12), alpha(C["danger"], 0.3), C["danger"], C["danger"]),
    "solid_danger": (C["danger"], C["danger_down"], C["danger"], C["white"]),
}


class FlatButton(ButtonBehavior, Label):
    kind = StringProperty("default")
    bg = ColorProperty(C["card_hi"])
    bg_down = ColorProperty(C["border"])
    line = ColorProperty(C["border"])
    fg = ColorProperty(C["text"])

    def __init__(self, **kw):
        super().__init__(**kw)
        self.on_kind()

    def on_kind(self, *_):
        self.bg, self.bg_down, self.line, self.fg = BUTTON_KINDS[self.kind]


def small_button(text, width, kind="default"):
    return FlatButton(
        text=text, kind=kind, size_hint_x=None, width=width, height=dp(36), font_size="13sp"
    )


Builder.load_string(KV)


class BanApp(App):
    title = "VRChat Group Ban"

    # ---------------- UI ----------------
    def build(self):
        Window.clearcolor = C["bg"]
        Window.softinput_mode = "below_target"
        Window.bind(on_keyboard=self.on_key)

        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.logged_in = False
        self.me = {}
        self.running = False
        self.busy = False  # login / group check / preparing a run
        self.stop_event = threading.Event()
        self.history_lock = threading.Lock()
        self.log_lines = []
        self.modals = []
        self.toast_widget = None
        self.settings = self.load_settings()

        self.overlay = FloatLayout()
        main = BoxLayout(orientation="vertical")
        self.overlay.add_widget(main)

        # top bar
        self.topbar = TopBar()
        main.add_widget(self.topbar)

        scroll = ScrollView(do_scroll_x=False, bar_width=dp(3), bar_color=C["text3"])
        root = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            padding=[dp(12), dp(12), dp(12), dp(24)],
            spacing=dp(12),
        )
        root.bind(minimum_height=root.setter("height"))
        scroll.add_widget(root)
        main.add_widget(scroll)

        # account
        self.account_card = Card()
        root.add_widget(self.account_card)
        self.user_in = StyledInput(
            hint_text="Username or email", multiline=False, text=self.settings.get("username", "")
        )
        self.pw_in = StyledInput(hint_text="Password", password=True, multiline=False)
        self.pw_in.bind(on_text_validate=self.login)
        self.pw_show = small_button("Show", dp(72))
        self.pw_show.height = dp(48)
        self.pw_show.bind(on_release=self.toggle_password)
        self.pw_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.pw_row.add_widget(self.pw_in)
        self.pw_row.add_widget(self.pw_show)
        self.login_btn = FlatButton(text="Log in", kind="primary")
        self.login_btn.bind(on_release=self.login)
        self.login_hint = Muted(
            text="Your password is only sent to VRChat and is never stored on this device."
        )

        # group: the groups in which the logged-in user may ban
        card = Card()
        head = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        head.add_widget(RowLabel(text="Group", bold=True, font_size="16sp"))
        self.refresh_groups_btn = small_button("Refresh", dp(92))
        self.refresh_groups_btn.bind(on_release=lambda *_: self.load_groups())
        head.add_widget(self.refresh_groups_btn)
        card.add_widget(head)
        self.groups_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        self.groups_box.bind(minimum_height=self.groups_box.setter("height"))
        card.add_widget(self.groups_box)
        self.groups_msg = ""
        root.add_widget(card)

        # blacklist
        card = Card()
        head = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(8))
        head.add_widget(RowLabel(text="Blacklist", bold=True, font_size="16sp"))
        self.count_pill = Pill(text="0 entries")
        box = AnchorLayout(size_hint_x=None, width=dp(110), anchor_x="right")
        box.add_widget(self.count_pill)
        head.add_widget(box)
        card.add_widget(head)
        card.add_widget(
            Muted(
                text="One per line, or separated by comma / semicolon. Display names, "
                "usr_ IDs or profile links. Lines starting with # are ignored."
            )
        )
        self.names_in = StyledInput(
            hint_text="Name1\nName2\nusr_...",
            multiline=True,
            height=dp(170),
            font_size="14sp",
            text=self.settings.get("names", ""),
        )
        self.names_in.bind(text=self.update_count)
        card.add_widget(self.names_in)
        row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        paste = small_button("Paste", dp(84))
        paste.bind(on_release=self.paste_names)
        clear = small_button("Clear", dp(84))
        clear.bind(on_release=self.clear_names)
        row.add_widget(paste)
        row.add_widget(clear)
        row.add_widget(Widget())
        card.add_widget(row)
        root.add_widget(card)

        # run
        card = Card()
        card.add_widget(Title(text="Run"))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(12))
        sw_box = AnchorLayout(size_hint_x=None, width=dp(48))
        self.dry_sw = Toggle(active=True)  # test run is always on after start, for safety
        self.dry_sw.bind(active=self.update_start_button)
        sw_box.add_widget(self.dry_sw)
        row.add_widget(sw_box)
        col = BoxLayout(orientation="vertical")
        col.add_widget(RowLabel(text="Test run", bold=True))
        col.add_widget(RowLabel(text="Only searches the users, nobody gets banned", font_size="12.5sp", color=C["text2"]))
        row.add_widget(col)
        card.add_widget(row)
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.start_btn = FlatButton(text="Start", kind="primary", height=dp(48), disabled=True)
        self.start_btn.bind(on_release=self.start)
        self.stop_btn = FlatButton(
            text="Stop", kind="danger", size_hint_x=None, width=dp(96), height=dp(48), disabled=True
        )
        self.stop_btn.bind(on_release=self.stop_run)
        row.add_widget(self.start_btn)
        row.add_widget(self.stop_btn)
        card.add_widget(row)
        row = BoxLayout(size_hint_y=None, height=dp(20), spacing=dp(10))
        bar_box = AnchorLayout(anchor_y="center")
        self.progress = Bar()
        bar_box.add_widget(self.progress)
        row.add_widget(bar_box)
        self.progress_lb = RowLabel(
            text="0 / 0", size_hint_x=None, width=dp(70), halign="right", font_size="12.5sp", color=C["text2"]
        )
        row.add_widget(self.progress_lb)
        card.add_widget(row)
        row = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(8))
        self.stat_banned = StatBox(caption="Banned", color=C["success"])
        self.stat_skipped = StatBox(caption="Skipped", color=C["primary"])
        self.stat_missing = StatBox(caption="Not found", color=C["warning"])
        self.stat_failed = StatBox(caption="Failed", color=C["danger"])
        for w in (self.stat_banned, self.stat_skipped, self.stat_missing, self.stat_failed):
            row.add_widget(w)
        card.add_widget(row)
        root.add_widget(card)

        # log
        card = Card()
        head = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        head.add_widget(RowLabel(text="Log", bold=True, font_size="16sp"))
        self.copy_btn = small_button("Copy", dp(76))
        self.copy_btn.bind(on_release=self.copy_logs)
        self.history_btn = small_button("History", dp(90))
        self.history_btn.bind(on_release=self.show_history)
        head.add_widget(self.copy_btn)
        head.add_widget(self.history_btn)
        card.add_widget(head)
        self.console = Console(size_hint_y=None, height=dp(280))
        self.log_box = BoxLayout(
            orientation="vertical", size_hint_y=None, padding=dp(10), spacing=dp(3)
        )
        self.log_box.bind(minimum_height=self.log_box.setter("height"))
        self.console.add_widget(self.log_box)
        card.add_widget(self.console)
        root.add_widget(card)

        self.render_account()
        self.render_groups()
        self.update_count()
        self.update_start_button()
        self.refresh_buttons()
        self.log("Ready. Log in, enter a group and a blacklist, then start a test run.", level="muted")
        if platform not in ("android", "ios"):
            Window.size = (432, 900)
        return self.overlay

    def render_topbar(self):
        bar = self.topbar
        bar.clear_widgets()
        title = BoxLayout(orientation="vertical")
        title.add_widget(RowLabel(text="VRChat Group Ban", bold=True, font_size="17sp"))
        title.add_widget(RowLabel(text=f"v{APP_VERSION}  ·  unofficial", font_size="11.5sp", color=C["text3"]))
        bar.add_widget(title)
        if self.logged_in:
            name = self.me.get("displayName", "?")
            _, color = trust_of(self.me.get("tags"))
            chip = BoxLayout(size_hint_x=None, width=dp(150), spacing=dp(8))
            box = AnchorLayout(size_hint_x=None, width=dp(28))
            box.add_widget(Avatar(text=name[:1].upper(), ring=get_color_from_hex(color), size=(dp(28), dp(28))))
            chip.add_widget(box)
            chip.add_widget(RowLabel(text=name, color=get_color_from_hex(color), bold=True, font_size="13.5sp"))
            bar.add_widget(chip)
        else:
            bar.add_widget(
                RowLabel(text="Not logged in", size_hint_x=None, width=dp(110), halign="right", font_size="13sp", color=C["text3"])
            )
        info = small_button("?", dp(36))
        info.bind(on_release=lambda *_: self.show_disclaimer(info_only=True))
        box = AnchorLayout(size_hint_x=None, width=dp(36))
        box.add_widget(info)
        bar.add_widget(box)

    def render_account(self):
        c = self.account_card
        c.clear_widgets()
        c.add_widget(Title(text="Account"))
        if self.logged_in:
            name = self.me.get("displayName", "?")
            rank, color = trust_of(self.me.get("tags"))
            row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(12))
            box = AnchorLayout(size_hint_x=None, width=dp(42))
            box.add_widget(Avatar(text=name[:1].upper(), ring=get_color_from_hex(color), size=(dp(42), dp(42))))
            row.add_widget(box)
            col = BoxLayout(orientation="vertical")
            col.add_widget(RowLabel(text=name, bold=True, color=get_color_from_hex(color), font_size="15sp"))
            col.add_widget(RowLabel(text=f"{rank}  ·  logged in", font_size="12.5sp", color=C["text2"]))
            row.add_widget(col)
            self.logout_btn = small_button("Log out", dp(92), kind="danger")
            self.logout_btn.disabled = self.running
            self.logout_btn.bind(on_release=self.logout)
            box = AnchorLayout(size_hint_x=None, width=dp(92))
            box.add_widget(self.logout_btn)
            row.add_widget(box)
            c.add_widget(row)
        else:
            c.add_widget(self.login_hint)
            c.add_widget(self.user_in)
            c.add_widget(self.pw_row)
            c.add_widget(self.login_btn)
        self.render_topbar()

    def on_start(self):
        if self.settings.get("disclaimer") != APP_VERSION:
            Clock.schedule_once(lambda dt: self.show_disclaimer(), 0.3)

    def on_pause(self):
        self.save_settings()
        return True

    def on_stop(self):
        self.save_settings()

    # ---------------- dialogs & toasts ----------------
    def dialog(self, title, widgets, buttons, title_color=None, tall=False, on_back="dismiss"):
        """buttons: list of (text, kind, callback). on_back: 'dismiss', None (ignore) or a callable."""
        view = ModalView(
            size_hint=(0.92, 0.86 if tall else None),
            auto_dismiss=False,
            background="",
            background_color=(0, 0, 0, 0),
            overlay_color=(0, 0, 0, 0.7),
        )
        card = BaseCard(spacing=dp(12)) if tall else Card(spacing=dp(12))
        card.add_widget(Title(text=title, color=title_color or C["text"], font_size="17sp"))
        for w in widgets:
            card.add_widget(w)
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        for text, kind, cb in buttons:
            btn = FlatButton(text=text, kind=kind)

            def _press(_btn, cb=cb):
                view.dismiss()
                if cb:
                    cb()

            btn.bind(on_release=_press)
            row.add_widget(btn)
        card.add_widget(row)
        view.add_widget(card)
        if not tall:
            card.bind(height=lambda *_: setattr(view, "height", card.height))
        view.on_back = on_back
        self.modals.append(view)
        view.bind(on_dismiss=lambda v: self.modals.remove(v) if v in self.modals else None)
        view.open()
        return view

    def scroll_text(self, text, markup=False):
        sv = ScrollView(do_scroll_x=False, bar_width=dp(3), bar_color=C["text3"])
        lb = WrapLabel(text=text, markup=markup, valign="top", color=C["text2"])
        sv.add_widget(lb)
        return sv

    def toast(self, text, kind="info"):
        if self.toast_widget is not None:
            self.overlay.remove_widget(self.toast_widget)
        accent = {"info": C["border"], "ok": C["success"], "error": C["danger"], "warn": C["warning"]}[kind]
        t = Toast(text=text, accent=accent, opacity=0, pos_hint={"center_x": 0.5, "y": 0.03})
        t.text_size = (Window.width - dp(64), None)
        self.toast_widget = t
        self.overlay.add_widget(t)
        anim = Animation(opacity=1, d=0.15) + Animation(d=2.8) + Animation(opacity=0, d=0.3)

        def _done(*_):
            if self.toast_widget is t:
                self.overlay.remove_widget(t)
                self.toast_widget = None

        anim.bind(on_complete=_done)
        anim.start(t)

    def on_key(self, window, key, *args):
        """Android back button: close dialogs, never kill a running ban run by accident."""
        if key != 27:
            return False
        if self.modals:
            top = self.modals[-1]
            if top.on_back == "dismiss":
                top.dismiss()
            elif callable(top.on_back):
                top.on_back()
            return True
        if self.running:
            self.dialog(
                "Run in progress",
                [WrapLabel(text="A run is still in progress. Stop it and close the app?", color=C["text2"])],
                [("Keep running", "default", None), ("Stop & exit", "solid_danger", self.exit_app)],
            )
            return True
        return False

    def exit_app(self):
        self.stop_event.set()
        self.save_settings()
        self.stop()

    # ---------------- disclaimer ----------------
    def show_disclaimer(self, info_only=False):
        if info_only:
            buttons = [("Close", "default", None)]
            on_back = "dismiss"
        else:
            buttons = [("Exit", "default", self.exit_app), ("I accept", "primary", self.accept_disclaimer)]
            on_back = self.exit_app
        self.dialog(
            DISCLAIMER_TITLE,
            [self.scroll_text(DISCLAIMER_TEXT)],
            buttons,
            title_color=C["danger"],
            tall=True,
            on_back=on_back,
        )

    def accept_disclaimer(self):
        self.settings["disclaimer"] = APP_VERSION
        self.save_settings()

    # ---------------- settings ----------------
    def settings_path(self):
        return os.path.join(self.user_data_dir, "settings.json")

    def load_settings(self):
        try:
            with open(self.settings_path(), encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save_settings(self):
        """Remembers username, group list / selection and blacklist. Passwords and session cookies are never saved."""
        try:
            self.settings.update(
                username=self.user_in.text.strip(),
                names=self.names_in.text,
            )
            with open(self.settings_path(), "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False)
        except Exception:
            pass

    # ---------------- helpers (thread-safe) ----------------
    def ui(self, fn, *args):
        Clock.schedule_once(lambda dt: fn(*args), 0)

    def log(self, *segments, tag=None, level="info"):
        """segments: plain strings (shown in the level color) or (text, hex_color) tuples."""
        self.ui(self._log, segments, tag, level)

    def _log(self, segments, tag, level):
        colors = {"info": H["text2"], "ok": H["success"], "warn": H["warning"], "err": H["danger"], "head": H["primary"], "muted": H["text3"]}
        base = colors[level]
        ts = time.strftime("%H:%M:%S")
        markup = f"[color={H['text3']}]{ts}[/color]  "
        plain = f"{ts} "
        if tag:
            markup += f"[b][color={base}]{tag}[/color][/b]  "
            plain += f"{tag} "
        for seg in segments:
            text, color = seg if isinstance(seg, tuple) else (seg, base)
            markup += f"[color={color}]{escape_markup(text)}[/color]"
            plain += text
        self.log_lines.append(plain)
        self.log_box.add_widget(LogLine(text=markup))
        if len(self.log_lines) > LOG_LIMIT:
            self.log_lines = self.log_lines[-LOG_LIMIT:]
            for w in self.log_box.children[LOG_LIMIT:]:
                self.log_box.remove_widget(w)
        Clock.schedule_once(lambda dt: setattr(self.console, "scroll_y", 0), 0.05)

    def clear_log(self):
        self.log_lines = []
        self.log_box.clear_widgets()

    def set_progress(self, value, maximum=None):
        if maximum is not None:
            self.progress.max = max(maximum, 1)
            self._progress_total = maximum
        self.progress.value = value
        self.progress_lb.text = f"{value} / {getattr(self, '_progress_total', 0)}"

    def set_stats(self, banned, skipped, missing, failed):
        self.stat_banned.value = str(banned)
        self.stat_skipped.value = str(skipped)
        self.stat_missing.value = str(missing)
        self.stat_failed.value = str(failed)

    def set_running(self, running):
        self.running = running
        self.refresh_buttons()

    def set_busy(self, busy):
        self.busy = busy
        self.refresh_buttons()

    def refresh_buttons(self):
        idle = not self.running and not self.busy
        self.start_btn.disabled = not (idle and self.logged_in)
        self.stop_btn.disabled = not self.running
        self.refresh_groups_btn.disabled = not (idle and self.logged_in)
        for row in self.groups_box.children:
            row.disabled = self.running
        self.login_btn.disabled = not idle
        self.dry_sw.disabled = self.running
        if self.logged_in and hasattr(self, "logout_btn"):
            self.logout_btn.disabled = not idle

    def update_count(self, *_):
        n = len(parse_names(self.names_in.text))
        self.count_pill.text = f"{n} {'entry' if n == 1 else 'entries'}"
        self.update_start_button()

    def update_start_button(self, *_):
        n = len(parse_names(self.names_in.text))
        if self.dry_sw.active:
            self.start_btn.kind = "primary"
            self.start_btn.text = "Start test run"
        else:
            self.start_btn.kind = "solid_danger"
            self.start_btn.text = f"Ban {n} {'user' if n == 1 else 'users'}"

    def flash(self, button, message, base_text):
        """Show a short message on a button, then restore its label."""
        button.text = message
        Clock.schedule_once(lambda dt: setattr(button, "text", base_text), 1.5)

    def toggle_password(self, *_):
        self.pw_in.password = not self.pw_in.password
        self.pw_show.text = "Show" if self.pw_in.password else "Hide"

    def paste_names(self, *_):
        text = Clipboard.paste() or ""
        if not text.strip():
            self.toast("Clipboard is empty", "warn")
            return
        cur = self.names_in.text.rstrip()
        self.names_in.text = (cur + "\n" if cur else "") + text.strip()

    def clear_names(self, *_):
        if not self.names_in.text.strip():
            return
        self.dialog(
            "Clear blacklist?",
            [WrapLabel(text="This removes all entries from the blacklist field.", color=C["text2"])],
            [("Cancel", "default", None), ("Clear", "solid_danger", lambda: setattr(self.names_in, "text", ""))],
        )

    def request(self, method, path, **kw):
        """API call with rate-limit handling. Raises SessionExpired on 401."""
        r = None
        for attempt in range(5):
            r = self.session.request(method, API + path, timeout=TIMEOUT, **kw)
            if r.status_code == 401:
                raise SessionExpired()
            if r.status_code != 429:
                return r
            try:
                wait = int(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = 30 * (attempt + 1)
            wait = min(max(wait, 5), 300)
            self.log(f"Rate limited, waiting {wait}s ...", level="warn")
            if self.stop_event.wait(wait):
                return r
        return r

    # ---------------- logs & ban history ----------------
    def copy_logs(self, *_):
        text = "\n".join(self.log_lines)
        if not text:
            self.flash(self.copy_btn, "Empty", "Copy")
            return
        Clipboard.copy(text)
        self.flash(self.copy_btn, "Copied!", "Copy")

    def history_path(self):
        return os.path.join(self.user_data_dir, "ban_history.json")

    def load_history(self):
        try:
            with open(self.history_path(), encoding="utf-8") as f:
                data = json.load(f)
            return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
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
                self.log(f"Could not save ban history: {e}", level="err")

    def clear_history(self):
        with self.history_lock:
            try:
                os.remove(self.history_path())
            except OSError:
                pass
        self.toast("Ban history cleared", "ok")

    def show_history(self, *_):
        data = list(reversed(self.load_history()[-HISTORY_SHOWN:]))
        plain = "\n".join(
            f"{e.get('time', '')}  {e.get('name', '')}  ({e.get('id', '')})  [{e.get('group_name') or e.get('group', '')}]"
            for e in data
        )
        sv = ScrollView(do_scroll_x=False, bar_width=dp(3), bar_color=C["text3"])
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        box.bind(minimum_height=box.setter("height"))
        if not data:
            box.add_widget(WrapLabel(text="No bans recorded yet.", color=C["text2"]))
        for e in data:
            group = e.get("group_name") or e.get("group", "")
            box.add_widget(
                WrapLabel(
                    markup=True,
                    font_size="13.5sp",
                    text=f"[b]{escape_markup(str(e.get('name', '')))}[/b]\n"
                    f"[size=12sp][color={H['text3']}]{escape_markup(str(e.get('time', '')))}  ·  "
                    f"{escape_markup(str(group))}\n{escape_markup(str(e.get('id', '')))}[/color][/size]",
                )
            )
        sv.add_widget(box)
        info = Muted(text=f"Last {HISTORY_SHOWN} bans, newest first. Reports are saved in Download/{FOLDER_NAME}.")

        def _copy():
            if plain:
                Clipboard.copy(plain)
                self.toast("History copied", "ok")

        def _clear():
            self.dialog(
                "Clear ban history?",
                [WrapLabel(text="This only deletes the list in the app. Nobody gets unbanned.", color=C["text2"])],
                [("Cancel", "default", None), ("Clear", "solid_danger", self.clear_history)],
            )

        self.dialog(
            "Ban history",
            [info, sv],
            [("Copy", "default", _copy), ("Clear", "danger", _clear), ("Close", "primary", None)],
            tall=True,
        )

    def build_report(self, group, stamp, banned_list, skipped, missing, errors):
        lines = [
            "VRChat Group Ban - report",
            f"Date:  {stamp}",
            f"Group: {group.get('name', '')} ({group.get('id', '')})",
            "",
            f"Banned ({len(banned_list)}):",
        ]
        lines += [f"  {n} ({u})" for n, u in banned_list] or ["  -"]
        lines += ["", f"Skipped ({len(skipped)}):"]
        lines += [f"  {n}" for n in skipped] or ["  -"]
        lines += ["", f"Not found ({len(missing)}):"]
        lines += [f"  {n}" for n in missing] or ["  -"]
        lines += ["", f"Failed ({len(errors)}):"]
        lines += [f"  {n}" for n in errors] or ["  -"]
        return "\n".join(lines) + "\n"

    # ---------------- login ----------------
    def login(self, *_):
        if self.busy or self.logged_in:
            return
        user, pw = self.user_in.text.strip(), self.pw_in.text
        if not user or not pw:
            self.toast("Enter username and password", "error")
            return
        self.set_busy(True)
        self.login_btn.text = "Logging in ..."
        threading.Thread(target=self._login_thread, args=(user, pw), daemon=True).start()

    def _login_thread(self, user, pw):
        try:
            self.session.cookies.clear()
            # VRChat expects both parts URL-encoded before Base64
            cred = urllib.parse.quote(user, safe="") + ":" + urllib.parse.quote(pw, safe="")
            token = base64.b64encode(cred.encode()).decode()
            r = self.session.get(
                API + "/auth/user", headers={"Authorization": f"Basic {token}"}, timeout=TIMEOUT
            )
            data = safe_json(r)
            if r.status_code == 401:
                self.ui(self.login_failed, "Wrong username or password")
            elif r.status_code == 429:
                self.ui(self.login_failed, "Too many login attempts - wait a few minutes")
            elif r.status_code != 200:
                self.ui(self.login_failed, f"Login failed ({api_error(r)})")
            elif data.get("requiresTwoFactorAuth"):
                self.ui(self.ask_2fa, data["requiresTwoFactorAuth"])
            elif data.get("id"):
                self.ui(self.login_done, data)
            else:
                self.ui(self.login_failed, "Unexpected answer from VRChat")
        except requests.RequestException as e:
            self.ui(self.login_failed, f"Network error: {e}")

    def login_failed(self, message):
        self.login_btn.text = "Log in"
        self.set_busy(False)
        self.toast(message, "error")

    def ask_2fa(self, methods, error=None):
        has_totp = "totp" in methods
        has_email = "emailOtp" in methods
        has_recovery = "otp" in methods
        if has_totp:
            hint = "Enter the 6-digit code from your authenticator app."
        elif has_email:
            hint = "VRChat sent a 6-digit code to your email address."
        else:
            hint = "Enter one of your recovery codes."
        widgets = [WrapLabel(text=hint, color=C["text2"])]
        if error:
            widgets.append(WrapLabel(text=error, color=C["danger"], font_size="13sp"))
        code_in = StyledInput(hint_text="Code", multiline=False, input_type="number" if (has_totp or has_email) else "text")
        widgets.append(code_in)
        rec_sw = None
        if has_recovery and (has_totp or has_email):
            row = BoxLayout(size_hint_y=None, height=dp(32), spacing=dp(10))
            box = AnchorLayout(size_hint_x=None, width=dp(48))
            rec_sw = Toggle()
            rec_sw.bind(active=lambda _, on: setattr(code_in, "input_type", "text" if on else "number"))
            box.add_widget(rec_sw)
            row.add_widget(box)
            row.add_widget(RowLabel(text="Use a recovery code instead", font_size="13sp", color=C["text2"]))
            widgets.append(row)

        def submit():
            code = code_in.text.strip().replace(" ", "")
            if not code:
                self.ui(self.ask_2fa, methods, "Please enter a code.")
                return
            if (rec_sw is not None and rec_sw.active) or not (has_totp or has_email):
                ep = "/auth/twofactorauth/otp/verify"
            elif has_totp:
                ep = "/auth/twofactorauth/totp/verify"
            else:
                ep = "/auth/twofactorauth/emailotp/verify"
            self.login_btn.text = "Verifying ..."
            threading.Thread(target=self._2fa_thread, args=(code, ep, methods), daemon=True).start()

        def cancel():
            self.session.cookies.clear()
            self.login_failed("Login cancelled")

        view = self.dialog(
            "Two-factor authentication",
            widgets,
            [("Cancel", "default", cancel), ("Verify", "primary", submit)],
            on_back=None,
        )
        code_in.bind(on_text_validate=lambda *_: (view.dismiss(), submit()))
        Clock.schedule_once(lambda dt: setattr(code_in, "focus", True), 0.3)

    def _2fa_thread(self, code, ep, methods):
        try:
            v = self.session.post(API + ep, json={"code": code}, timeout=TIMEOUT)
            if v.status_code == 429:
                self.ui(self.ask_2fa, methods, "Too many attempts - wait a moment and try again.")
                return
            if v.status_code != 200 or not safe_json(v).get("verified"):
                self.ui(self.ask_2fa, methods, "Wrong code, please try again.")
                return
            r = self.session.get(API + "/auth/user", timeout=TIMEOUT)
            data = safe_json(r)
            if r.status_code == 200 and data.get("id"):
                self.ui(self.login_done, data)
            else:
                self.ui(self.login_failed, f"Login failed ({api_error(r)})")
        except requests.RequestException as e:
            self.ui(self.login_failed, f"Network error: {e}")

    def login_done(self, data):
        self.logged_in = True
        self.me = data
        self.pw_in.text = ""
        self.pw_in.password = True
        self.pw_show.text = "Show"
        self.login_btn.text = "Log in"
        self.busy = False
        self.save_settings()
        self.render_account()
        self.refresh_buttons()
        self.toast(f"Logged in as {data.get('displayName', '')}", "ok")
        self.log("Logged in as ", (data.get("displayName", ""), trust_of(data.get("tags"))[1]), level="ok")
        cached = self.settings.get("groups_user") == data.get("id") and self.settings.get("groups") is not None
        fresh = time.time() - self.settings.get("groups_time", 0) < GROUPS_MAX_AGE
        if cached and fresh:
            self.render_groups()
        else:
            self.load_groups()

    def logout(self, *_):
        if self.running:
            return

        def _bg():
            try:
                self.session.put(API + "/logout", timeout=10)
            except requests.RequestException:
                pass
            self.session.cookies.clear()

        threading.Thread(target=_bg, daemon=True).start()
        self.set_logged_out()
        self.toast("Logged out", "info")

    def set_logged_out(self):
        self.logged_in = False
        self.me = {}
        self.groups_msg = ""
        self.render_account()
        self.render_groups()
        self.refresh_buttons()

    def session_expired(self):
        self.session.cookies.clear()
        self.set_logged_out()
        self.toast("Session expired - please log in again", "error")

    # ---------------- group ----------------
    def fetch_group(self, gid):
        """Returns (group, None) or (None, error message). Runs in a thread."""
        try:
            r = self.request("GET", f"/groups/{gid}")
        except SessionExpired:
            self.ui(self.session_expired)
            return None, "Session expired - please log in again"
        except requests.RequestException as e:
            return None, f"Network error: {e}"
        if r.status_code == 404:
            return None, "Group not found"
        if r.status_code != 200:
            return None, f"Could not load group ({api_error(r)})"
        group = safe_json(r)
        if group.get("id") != gid:
            return None, "Unexpected answer from VRChat"
        return group, None

    def my_groups(self):
        """Cached groups with ban permission - only valid for the account that loaded them."""
        if not self.logged_in or self.settings.get("groups_user") != self.me.get("id"):
            return []
        return [
            g for g in self.settings.get("groups") or []
            if isinstance(g, dict) and extract_group_id(str(g.get("id", "")))
        ]

    def selected_group(self):
        gid = self.settings.get("group_id")
        for g in self.my_groups():
            if g.get("id") == gid:
                return g
        return None

    def group_entry(self, group, owner):
        return {
            "id": group.get("groupId") or group.get("id"),
            "name": str(group.get("name", "")),
            "code": f"{group.get('shortCode', '')}.{group.get('discriminator', '')}".strip("."),
            "members": group.get("memberCount"),
            "owner": owner,
        }

    def remember_group(self, group):
        """Refresh name / member count of a cached group."""
        groups = self.settings.get("groups") or []
        for i, g in enumerate(groups):
            if isinstance(g, dict) and g.get("id") == group.get("id"):
                groups[i] = self.group_entry(group, g.get("owner", False))

    def render_groups(self):
        box = self.groups_box
        box.clear_widgets()
        if not self.logged_in:
            box.add_widget(Muted(text="Log in to see the groups in which you are allowed to ban."))
            return
        if self.groups_msg:
            box.add_widget(WrapLabel(text=self.groups_msg, color=C["text2"], font_size="13sp"))
        groups = self.my_groups()
        if not groups:
            if not self.groups_msg:
                box.add_widget(
                    Muted(text='You have no ban permission in any of your groups. Tap "Refresh" after you got one.')
                )
            return
        selected = self.settings.get("group_id")
        for g in groups:
            row = GroupRow(selected=g["id"] == selected)
            col = BoxLayout(orientation="vertical")
            col.add_widget(RowLabel(text=str(g.get("name") or g["id"]), bold=True, font_size="14.5sp"))
            meta = str(g.get("code") or g["id"])
            if g.get("members") is not None:
                meta += f"  ·  {g['members']} members"
            col.add_widget(RowLabel(text=meta, font_size="12sp", color=C["text3"]))
            row.add_widget(col)
            badge = Pill(text="Owner" if g.get("owner") else "Can ban")
            badge.color = C["warning"] if g.get("owner") else C["primary"]
            badge_box = AnchorLayout(size_hint_x=None, width=dp(78), anchor_x="right")
            badge_box.add_widget(badge)
            row.add_widget(badge_box)
            row.bind(on_release=lambda _, gid=g["id"]: self.select_group(gid))
            row.disabled = self.running
            box.add_widget(row)

    def select_group(self, gid):
        if self.running:
            return
        self.settings["group_id"] = gid
        self.save_settings()
        self.render_groups()

    def load_groups(self):
        """Load all groups of the user and keep those where banning is allowed."""
        if not self.logged_in or self.busy or self.running:
            return
        self.set_busy(True)
        self.refresh_groups_btn.text = "..."
        self.set_groups_msg("Loading your groups ...")
        threading.Thread(target=self._load_groups_thread, args=(self.me.get("id"),), daemon=True).start()

    def set_groups_msg(self, text):
        self.groups_msg = text
        self.render_groups()

    def _load_groups_thread(self, uid):
        try:
            if not USER_ID_RE.fullmatch(uid or ""):
                raise RuntimeError("unknown user id")
            groups, offset = [], 0
            while offset < 1000:
                r = self.request("GET", f"/users/{uid}/groups", params={"n": 100, "offset": offset})
                if r.status_code != 200:
                    self.ui(self._groups_loaded, None, f"Could not load your groups ({api_error(r)})")
                    return
                page = [g for g in safe_json(r, list) if isinstance(g, dict)]
                groups += page
                if len(page) < 100:
                    break
                offset += 100
            result = []
            for i, g in enumerate(groups, 1):
                gid = g.get("groupId") or ""
                if not GROUP_ID_RE.fullmatch(gid):
                    continue
                if g.get("ownerId") == uid:
                    result.append(self.group_entry(g, True))
                    continue
                self.ui(self.set_groups_msg, f"Checking ban permissions ... {i} / {len(groups)}")
                r = self.request("GET", f"/groups/{gid}")
                time.sleep(GROUP_CHECK_DELAY)
                if r.status_code == 200 and can_ban(safe_json(r), uid) is True:
                    result.append(self.group_entry(g, False))
            result.sort(key=lambda e: (not e["owner"], e["name"].casefold()))
            self.ui(self._groups_loaded, result, None)
        except SessionExpired:
            self.ui(self._groups_loaded, None, "Session expired")
            self.ui(self.session_expired)
        except requests.RequestException as e:
            self.ui(self._groups_loaded, None, f"Network error: {e}")
        except Exception as e:
            self.ui(self._groups_loaded, None, f"Could not load your groups: {e!r}")

    def _groups_loaded(self, groups, err):
        self.refresh_groups_btn.text = "Refresh"
        self.groups_msg = ""
        self.set_busy(False)
        if groups is None:
            self.render_groups()
            if self.logged_in:
                self.toast(err, "error")
            return
        self.settings.update(groups=groups, groups_user=self.me.get("id"), groups_time=time.time())
        if not any(g["id"] == self.settings.get("group_id") for g in groups):
            self.settings["group_id"] = groups[0]["id"] if groups else None
        self.save_settings()
        self.render_groups()
        n = len(groups)
        self.toast(f"{n} {'group' if n == 1 else 'groups'} with ban permission", "ok" if n else "warn")

    # ---------------- banning ----------------
    def start(self, *_):
        if self.running or self.busy or not self.logged_in:
            return
        names = parse_names(self.names_in.text)
        selected = self.selected_group()
        gid = extract_group_id(str(selected.get("id", ""))) if selected else None
        dry = self.dry_sw.active
        if not names:
            self.toast("Your blacklist is empty", "error")
            return
        if not gid:
            self.toast("Select a group first", "error")
            return
        self.save_settings()
        self.set_busy(True)

        def _bg():
            group, err = self.fetch_group(gid)
            self.ui(self._prepared, names, dry, group, err)

        threading.Thread(target=_bg, daemon=True).start()

    def _prepared(self, names, dry, group, err):
        self.set_busy(False)
        if group is None:
            self.toast(err, "error")
            return
        if can_ban(group, self.me.get("id")) is False:
            self.toast("You no longer have ban permission in this group - tap Refresh", "error")
            return
        self.remember_group(group)  # refresh name / member count
        self.save_settings()
        self.render_groups()
        if dry:
            self.begin(names, dry, group)
            return
        n = len(names)
        self.dialog(
            "Confirm bans",
            [
                WrapLabel(
                    markup=True,
                    color=C["text2"],
                    text=f"Ban up to [b][color={H['text']}]{n} {'user' if n == 1 else 'users'}[/color][/b] from "
                    f"[b][color={H['text']}]{escape_markup(str(group.get('name', '')))}[/color][/b]?\n\n"
                    "Bans can only be removed again manually in VRChat.",
                )
            ],
            [("Cancel", "default", None), ("Ban", "solid_danger", lambda: self.begin(names, dry, group))],
            title_color=C["danger"],
        )

    def begin(self, names, dry, group):
        if self.running:
            return
        self.stop_event.clear()
        self.clear_log()
        self.set_progress(0, len(names))
        self.set_stats(0, 0, 0, 0)
        self.set_running(True)
        keep_screen_on(True)
        threading.Thread(target=self.worker, args=(names, group, dry), daemon=True).start()

    def stop_run(self, *_):
        if self.running and not self.stop_event.is_set():
            self.stop_event.set()
            self.log("Stop requested ...", level="warn")

    def resolve(self, name):
        """Returns (status, user_id, display_name, tags); status is 'ok', 'missing' or 'error'."""
        uid = extract_user_id(name)
        if uid:
            r = self.request("GET", f"/users/{uid}")
            if r.status_code == 404:
                return "missing", None, None, None
            if r.status_code != 200:
                self.log(f"Lookup failed ({api_error(r)})", level="err")
                return "error", None, None, None
            u = safe_json(r)
            return "ok", uid, u.get("displayName") or uid, u.get("tags")
        r = self.request("GET", "/users", params={"search": name, "n": 50})
        if r.status_code != 200:
            self.log(f"Search failed ({api_error(r)})", level="err")
            return "error", None, None, None
        try:
            results = r.json()
        except ValueError:
            results = None
        if not isinstance(results, list):
            self.log("Search failed (invalid answer from VRChat)", level="err")
            return "error", None, None, None
        wanted = name.casefold()
        for u in results:
            if not isinstance(u, dict):
                continue
            found = u.get("displayName", "")
            if found.casefold() == wanted and USER_ID_RE.fullmatch(u.get("id", "")):
                return "ok", u["id"], found, u.get("tags")
        return "missing", None, None, None

    def fetch_bans(self, gid):
        """IDs of users that are already banned, so they can be skipped. None if not readable."""
        ids, offset = set(), 0
        while not self.stop_event.is_set() and offset < 20000:
            r = self.request("GET", f"/groups/{gid}/bans", params={"n": 100, "offset": offset})
            if r.status_code != 200:
                self.log(f"Could not load existing bans ({api_error(r)}) - continuing without", level="warn")
                return None
            page = safe_json(r, list)
            ids.update(m.get("userId") for m in page if isinstance(m, dict) and m.get("userId"))
            if len(page) < 100:
                break
            offset += 100
            self.stop_event.wait(0.5)
        return ids

    def worker(self, names, group, dry):
        gid = group["id"]
        group_name = str(group.get("name", gid))
        banned_list, skipped, missing, errors = [], [], [], []
        started = now_str()
        total = len(names)

        def stats():
            self.ui(self.set_stats, len(banned_list), len(skipped), len(missing), len(errors))

        try:
            self.log("Test run" if dry else "Ban run", " in ", (group_name, H["text"]), f"  ({total} entries)", level="head")
            already = self.fetch_bans(gid)
            if already is not None:
                self.log(f"{len(already)} users are already banned in this group", level="muted")
            already = already or set()
            my_id = self.me.get("id")
            for i, name in enumerate(names, 1):
                if self.stop_event.is_set():
                    break
                try:
                    status, uid, shown, tags = self.resolve(name)
                    self.stop_event.wait(DELAY)
                    color = trust_of(tags)[1]
                    if status == "missing":
                        self.log(name, " - no exact match", tag="NOT FOUND", level="warn")
                        missing.append(name)
                    elif status == "error":
                        self.log(name, " - lookup failed", tag="FAILED", level="err")
                        errors.append(name)
                    elif uid == my_id:
                        self.log((shown, color), " - that is you, skipped", tag="SKIPPED", level="muted")
                        skipped.append(f"{name} (yourself)")
                    elif uid in already:
                        self.log((shown, color), " - already banned", tag="SKIPPED", level="muted")
                        skipped.append(f"{shown} ({uid})")
                    elif dry:
                        self.log((shown, color), (f"  {uid}", H["text3"]), tag="WOULD BAN", level="head")
                        already.add(uid)  # same user listed twice
                    elif self.stop_event.is_set():
                        break
                    else:
                        r = self.request("POST", f"/groups/{gid}/bans", json={"userId": uid})
                        self.stop_event.wait(DELAY)
                        if r.status_code == 200:
                            self.log((shown, color), (f"  {uid}", H["text3"]), tag="BANNED", level="ok")
                            banned_list.append((shown, uid))
                            already.add(uid)
                            self.add_history(
                                {"time": now_str(), "name": shown, "id": uid, "group": gid, "group_name": group_name}
                            )
                        else:
                            self.log((shown, color), f" - {api_error(r)}", tag="FAILED", level="err")
                            errors.append(f"{shown} ({uid})")
                except requests.RequestException as e:
                    self.log(name, f" - network error: {e}", tag="FAILED", level="err")
                    errors.append(name)
                self.ui(self.set_progress, i)
                stats()
        except SessionExpired:
            self.log("Session expired - please log in again", level="err")
            self.ui(self.session_expired)
        except Exception as e:  # never leave the UI stuck in "running"
            self.log(f"Unexpected error: {e!r}", level="err")
        finally:
            stats()
            if self.stop_event.is_set():
                self.log("Stopped by user", level="warn")
            self.log(
                f"Done - banned {len(banned_list)}, skipped {len(skipped)}, not found {len(missing)}, failed {len(errors)}",
                level="head",
            )
            if missing:
                self.log("Not found: " + ", ".join(missing), level="warn")
            if errors:
                self.log("Failed: " + ", ".join(errors), level="err")
            if not dry and (banned_list or skipped or missing or errors):
                stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
                report = self.build_report(group, started, banned_list, skipped, missing, errors)
                ok, where = save_report(f"ban_report_{stamp}.txt", report)
                self.log(f"Report saved: {where}" if ok else f"Could not save report: {where}", level="ok" if ok else "err")
            self.ui(self.finish_run)

    def finish_run(self):
        keep_screen_on(False)
        self.set_running(False)
        self.toast("Run finished", "ok")


if __name__ == "__main__":
    BanApp().run()
