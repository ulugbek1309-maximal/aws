#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Telegram Bot & Web App Manager
==================================

Desktop GUI (Tkinter + Paramiko) to manage a Telegram bot and its Mini App
(Web App) on an AWS EC2 instance over SSH/SFTP. All configurable values are
entered by the user. Settings and server profiles are persisted locally.

Optional dependency: ``pygments`` enables editor syntax highlighting.
"""

import os
import json
import stat
import time
import re
import threading
import traceback
import queue
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import paramiko
except ImportError:  # pragma: no cover
    raise SystemExit("Missing dependency 'paramiko'. Run: pip install paramiko")

try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename, TextLexer
    from pygments.token import Token

    HAS_PYGMENTS = True
except ImportError:  # pragma: no cover
    HAS_PYGMENTS = False


# --------------------------------------------------------------------------- #
#  Constants & persistence
# --------------------------------------------------------------------------- #
APP_TITLE = "AWS Telegram Bot & Web App Manager"
APP_GEOMETRY = "1300x840"
MONO_FONT = ("Consolas", 11)
UI_FONT = ("Segoe UI", 10)

HINT_PORT = "22"
HINT_USERNAME = "ubuntu"
HINT_SERVICE = "my_bot"
HINT_LOG_PATH = "/home/ubuntu/your_project/bot.log"
HINT_UPLOAD_TARGET = "/var/www/html"

_HOME = os.path.expanduser("~")
PROFILE_FILE = os.path.join(_HOME, ".aws_telegram_manager_profiles.json")
SETTINGS_FILE = os.path.join(_HOME, ".aws_telegram_manager_settings.json")

CONSOLE_BG = "#000000"
CONSOLE_FG = "#00FF00"

THEMES = {
    "dark": {"bg": "#1e1e2e", "panel": "#2a2a3c", "accent": "#5865f2",
             "text": "#e6e6e6", "entry": "#3a3a4f", "editor_bg": "#1b1b27",
             "editor_fg": "#f8f8f2", "ok": "#43b581", "err": "#f04747"},
    "light": {"bg": "#f2f2f5", "panel": "#e3e3ea", "accent": "#5865f2",
              "text": "#1e1e2e", "entry": "#ffffff", "editor_bg": "#ffffff",
              "editor_fg": "#1e1e2e", "ok": "#2e9e62", "err": "#cc3333"},
}

TOKEN_COLORS = {
    "dark": {"keyword": "#c586c0", "string": "#ce9178", "comment": "#6a9955",
             "number": "#b5cea8", "name_function": "#dcdcaa",
             "name_class": "#4ec9b0", "operator": "#d4d4d4"},
    "light": {"keyword": "#af00db", "string": "#a31515", "comment": "#008000",
              "number": "#098658", "name_function": "#795e26",
              "name_class": "#267f99", "operator": "#000000"},
}

# ANSI SGR colour codes -> hex (for the terminal tab).
ANSI_COLORS = {
    "30": "#000000", "31": "#cd3131", "32": "#0dbc79", "33": "#e5e510",
    "34": "#2472c8", "35": "#bc3fbc", "36": "#11a8cd", "37": "#e5e5e5",
    "90": "#666666", "91": "#f14c4c", "92": "#23d18b", "93": "#f5f543",
    "94": "#3b8eea", "95": "#d670d6", "96": "#29b8db", "97": "#ffffff",
}

# --------------------------------------------------------------------------- #
#  i18n
# --------------------------------------------------------------------------- #
LANG = {
    "en": {
        "connect": "Connect", "disconnect": "Disconnect",
        "connected": "Connected", "disconnected": "Disconnected",
        "connecting": "Connecting...", "profile": "Profile:",
        "save_profile": "Save Profile", "delete_profile": "Delete Profile",
        "theme": "Toggle Theme", "language": "Language:",
        "host": "Host (Public IP):", "port": "Port:", "username": "Username:",
        "ssh_key": "SSH Key (.pem):", "passphrase": "Passphrase:",
        "browse": "Browse...", "use_agent": "Use SSH agent",
        "auto_reconnect": "Auto-reconnect",
        "tab_files": "  File Manager  ", "tab_ctrl": "  Bot Controller  ",
        "tab_env": "  .env Editor  ", "tab_term": "  Terminal  ",
        "refresh": "Refresh", "new_file": "New File", "new_folder": "New Folder",
        "delete": "Delete", "filter": "Filter:", "upload": "Upload Folder...",
        "download": "Download Selected...", "upload_target": "Upload target dir:",
        "save_server": "Save to Server (Ctrl+S)", "no_file": "No file open",
        "manager": "Manager:", "service": "Service name:", "bot": "Bot:",
        "log_path": "Log path:", "log_lines": "Log lines:",
        "start": "Start Bot", "stop": "Stop Bot", "restart": "Restart Bot",
        "status": "Bot Status", "check_status": "Check Status",
        "auto_refresh": "Auto status", "renew_ssl": "Renew SSL",
        "health": "Server Health", "nginx_test": "Test Nginx",
        "nginx_reload": "Reload Nginx", "stream": "Stream Logs (tail -f)",
        "stop_stream": "Stop Streaming", "tail_once": "Tail Once",
        "clear": "Clear", "log_filter": "Log filter:",
        "env_path": ".env path:", "load_env": "Load .env", "save_env": "Save .env",
        "quick": "Quick:", "run": "Run", "rename": "Rename",
        "copy_path": "Copy path", "chmod": "Permissions (chmod)",
    },
    "uz": {
        "connect": "Ulanish", "disconnect": "Uzish",
        "connected": "Ulangan", "disconnected": "Uzilgan",
        "connecting": "Ulanmoqda...", "profile": "Profil:",
        "save_profile": "Profilni saqlash", "delete_profile": "Profilni o'chirish",
        "theme": "Mavzu", "language": "Til:",
        "host": "Host (IP):", "port": "Port:", "username": "Foydalanuvchi:",
        "ssh_key": "SSH kalit (.pem):", "passphrase": "Parol (kalit):",
        "browse": "Tanlash...", "use_agent": "SSH agent",
        "auto_reconnect": "Avto qayta ulanish",
        "tab_files": "  Fayllar  ", "tab_ctrl": "  Bot boshqaruvi  ",
        "tab_env": "  .env muharrir  ", "tab_term": "  Terminal  ",
        "refresh": "Yangilash", "new_file": "Yangi fayl", "new_folder": "Yangi papka",
        "delete": "O'chirish", "filter": "Filtr:", "upload": "Papka yuklash...",
        "download": "Yuklab olish...", "upload_target": "Yuklash papkasi:",
        "save_server": "Serverga saqlash (Ctrl+S)", "no_file": "Fayl ochilmagan",
        "manager": "Menejer:", "service": "Servis nomi:", "bot": "Bot:",
        "log_path": "Log yo'li:", "log_lines": "Log qatorlar:",
        "start": "Botni ishga tushir", "stop": "Botni to'xtat",
        "restart": "Qayta ishga tushir", "status": "Bot holati",
        "check_status": "Holatni tekshir", "auto_refresh": "Avto holat",
        "renew_ssl": "SSL yangilash", "health": "Server holati",
        "nginx_test": "Nginx test", "nginx_reload": "Nginx reload",
        "stream": "Loglarni oqim (tail -f)", "stop_stream": "Oqimni to'xtat",
        "tail_once": "Bir marta", "clear": "Tozalash", "log_filter": "Log filtr:",
        "env_path": ".env yo'li:", "load_env": ".env yuklash", "save_env": ".env saqlash",
        "quick": "Tez:", "run": "Ishga tushir", "rename": "Nomini o'zgartir",
        "copy_path": "Yo'lni nusxalash", "chmod": "Ruxsatlar (chmod)",
    },
    "ru": {
        "connect": "Подключить", "disconnect": "Отключить",
        "connected": "Подключено", "disconnected": "Отключено",
        "connecting": "Подключение...", "profile": "Профиль:",
        "save_profile": "Сохранить профиль", "delete_profile": "Удалить профиль",
        "theme": "Тема", "language": "Язык:",
        "host": "Хост (IP):", "port": "Порт:", "username": "Пользователь:",
        "ssh_key": "SSH ключ (.pem):", "passphrase": "Пароль ключа:",
        "browse": "Обзор...", "use_agent": "SSH агент",
        "auto_reconnect": "Авто-переподключение",
        "tab_files": "  Файлы  ", "tab_ctrl": "  Управление ботом  ",
        "tab_env": "  Редактор .env  ", "tab_term": "  Терминал  ",
        "refresh": "Обновить", "new_file": "Новый файл", "new_folder": "Новая папка",
        "delete": "Удалить", "filter": "Фильтр:", "upload": "Загрузить папку...",
        "download": "Скачать...", "upload_target": "Папка загрузки:",
        "save_server": "Сохранить на сервер (Ctrl+S)", "no_file": "Файл не открыт",
        "manager": "Менеджер:", "service": "Имя сервиса:", "bot": "Бот:",
        "log_path": "Путь лога:", "log_lines": "Строк лога:",
        "start": "Запустить бот", "stop": "Остановить", "restart": "Перезапустить",
        "status": "Статус бота", "check_status": "Проверить статус",
        "auto_refresh": "Авто статус", "renew_ssl": "Обновить SSL",
        "health": "Состояние сервера", "nginx_test": "Тест Nginx",
        "nginx_reload": "Reload Nginx", "stream": "Поток логов (tail -f)",
        "stop_stream": "Остановить поток", "tail_once": "Один раз",
        "clear": "Очистить", "log_filter": "Фильтр лога:",
        "env_path": "Путь .env:", "load_env": "Загрузить .env",
        "save_env": "Сохранить .env", "quick": "Быстро:", "run": "Выполнить",
        "rename": "Переименовать", "copy_path": "Копировать путь",
        "chmod": "Права (chmod)",
    },
}


def _human_size(num: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if num < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}P"


# --------------------------------------------------------------------------- #
#  Main Application
# --------------------------------------------------------------------------- #
class AwsTelegramManager:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)

        self.settings = self._read_json(SETTINGS_FILE)
        self.theme_name = self.settings.get("theme", "dark")
        self.lang = self.settings.get("language", "en")
        self.root.geometry(self.settings.get("geometry", APP_GEOMETRY))
        self.root.minsize(1100, 740)

        # SSH state
        self.ssh_client: paramiko.SSHClient | None = None
        self.sftp_client: paramiko.SFTPClient | None = None
        self.connected = False
        self._last_conn: dict | None = None
        self._reconnecting = False

        self.current_path = "/home/ubuntu"
        self.active_file: str | None = None
        self._loaded_content = ""
        self.active_env_file: str | None = None

        self._all_entries: list[dict] = []
        self._visible_entries: list[dict] = []

        self._log_stop = threading.Event()
        self._log_following = False
        self._log_channel = None
        self._console_lines: list[str] = []

        self._cmd_history: list[str] = []
        self._cmd_index = 0

        self.palette = THEMES[self.theme_name]
        self._themed_texts: list[tk.Text] = []
        self._themed_listboxes: list[tk.Listbox] = []
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()
        self._highlight_job = None

        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self._build_connection_panel()
        self._build_status_bar()
        self._build_notebook()
        self._apply_theme(self.theme_name)
        self._load_profiles_into_combo()
        self._restore_last_profile()

        # Restore last tab.
        try:
            self.notebook.select(self.settings.get("last_tab", 0))
        except tk.TclError:
            pass

        self.root.after(80, self._pump_ui_queue)
        self.root.after(8000, self._watchdog)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _t(self, key: str) -> str:
        return LANG.get(self.lang, LANG["en"]).get(key, LANG["en"].get(key, key))

    # ================================================================== #
    #  Theme
    # ================================================================== #
    def _apply_theme(self, name: str) -> None:
        self.theme_name = name
        p = THEMES[name]
        self.palette = p
        self.root.configure(bg=p["bg"])
        s = self.style
        s.configure("TNotebook", background=p["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=p["panel"], foreground=p["text"],
                    padding=(16, 8), font=UI_FONT)
        s.map("TNotebook.Tab", background=[("selected", p["accent"])],
              foreground=[("selected", "#ffffff")])
        s.configure("TFrame", background=p["bg"])
        s.configure("Panel.TFrame", background=p["panel"])
        s.configure("TLabel", background=p["bg"], foreground=p["text"], font=UI_FONT)
        s.configure("Panel.TLabel", background=p["panel"], foreground=p["text"], font=UI_FONT)
        s.configure("TCheckbutton", background=p["panel"], foreground=p["text"])
        s.configure("TButton", font=UI_FONT, padding=6)
        s.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        s.configure("TEntry", fieldbackground=p["entry"], foreground=p["text"],
                    insertcolor=p["text"])
        s.configure("TCombobox", fieldbackground=p["entry"], foreground=p["text"])
        for txt in self._themed_texts:
            txt.configure(bg=p["editor_bg"], fg=p["editor_fg"], insertbackground=p["editor_fg"])
        for lb in self._themed_listboxes:
            lb.configure(bg=p["editor_bg"], fg=p["editor_fg"], selectbackground=p["accent"])
        self._configure_highlight_tags()
        if self.active_file:
            self._highlight_editor()

    def _toggle_theme(self) -> None:
        self._apply_theme("light" if self.theme_name == "dark" else "dark")

    def _on_language_change(self, _e=None) -> None:
        self.lang = self.language_var.get()
        self.settings["language"] = self.lang
        self._write_json(SETTINGS_FILE, self.settings)
        messagebox.showinfo("Language", "Restart the app to fully apply the language.")

    # ================================================================== #
    #  Connection panel
    # ================================================================== #
    def _build_connection_panel(self) -> None:
        f = ttk.Frame(self.root, style="Panel.TFrame", padding=10)
        f.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 4))

        # Row 0: profile + language + theme
        ttk.Label(f, text=self._t("profile"), style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(f, textvariable=self.profile_var, width=22, state="readonly")
        self.profile_combo.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        ttk.Button(f, text=self._t("save_profile"), command=self._save_profile).grid(row=0, column=2, padx=4, pady=4)
        ttk.Button(f, text=self._t("delete_profile"), command=self._delete_profile).grid(row=0, column=3, padx=4, pady=4)
        ttk.Label(f, text=self._t("language"), style="Panel.TLabel").grid(row=0, column=4, sticky="e", padx=4)
        self.language_var = tk.StringVar(value=self.lang)
        lc = ttk.Combobox(f, textvariable=self.language_var, width=5, state="readonly", values=["en", "uz", "ru"])
        lc.grid(row=0, column=5, padx=2, pady=4, sticky="w")
        lc.bind("<<ComboboxSelected>>", self._on_language_change)
        ttk.Button(f, text=self._t("theme"), command=self._toggle_theme).grid(row=0, column=6, padx=4, pady=4)

        # Row 1: host / port / user
        ttk.Label(f, text=self._t("host"), style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.host_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.host_var, width=24).grid(row=1, column=1, padx=4, pady=4, sticky="w")
        ttk.Label(f, text=self._t("port"), style="Panel.TLabel").grid(row=1, column=2, sticky="e", padx=4)
        self.port_var = tk.StringVar(value=HINT_PORT)
        ttk.Entry(f, textvariable=self.port_var, width=7).grid(row=1, column=3, padx=4, pady=4, sticky="w")
        ttk.Label(f, text=self._t("username"), style="Panel.TLabel").grid(row=1, column=4, sticky="e", padx=4)
        self.user_var = tk.StringVar(value=HINT_USERNAME)
        ttk.Entry(f, textvariable=self.user_var, width=14).grid(row=1, column=5, columnspan=2, padx=4, pady=4, sticky="w")

        # Row 2: key / passphrase / options / connect
        ttk.Label(f, text=self._t("ssh_key"), style="Panel.TLabel").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        self.key_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.key_var, width=24).grid(row=2, column=1, padx=4, pady=4, sticky="w")
        ttk.Button(f, text=self._t("browse"), command=self._pick_key).grid(row=2, column=2, padx=4, pady=4)
        ttk.Label(f, text=self._t("passphrase"), style="Panel.TLabel").grid(row=2, column=3, sticky="e", padx=4)
        self.passphrase_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.passphrase_var, width=14, show="*").grid(row=2, column=4, padx=4, pady=4, sticky="w")

        opts = ttk.Frame(f, style="Panel.TFrame")
        opts.grid(row=2, column=5, padx=2, pady=4, sticky="w")
        self.use_agent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text=self._t("use_agent"), variable=self.use_agent_var).pack(anchor="w")
        self.auto_reconnect_var = tk.BooleanVar(value=self.settings.get("auto_reconnect", False))
        ttk.Checkbutton(opts, text=self._t("auto_reconnect"), variable=self.auto_reconnect_var).pack(anchor="w")

        self.connect_btn = ttk.Button(f, text=self._t("connect"), style="Accent.TButton", command=self._toggle_connection)
        self.connect_btn.grid(row=2, column=6, padx=4, pady=4, sticky="we")

    def _pick_key(self) -> None:
        path = filedialog.askopenfilename(title="Select private SSH key",
                                          filetypes=[("PEM key", "*.pem"), ("All files", "*.*")])
        if path:
            self.key_var.set(path)

    # ----- JSON helpers ---------------------------------------------- #
    def _read_json(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _write_json(self, path: str, data: dict) -> None:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Save error", str(exc))

    # ----- Profiles --------------------------------------------------- #
    def _load_profiles_into_combo(self) -> None:
        self.profile_combo["values"] = sorted(self._read_json(PROFILE_FILE).keys())

    def _restore_last_profile(self) -> None:
        last = self.settings.get("last_profile", "")
        if last and last in self._read_json(PROFILE_FILE):
            self.profile_var.set(last)
            self._on_profile_selected()

    def _on_profile_selected(self, _e=None) -> None:
        data = self._read_json(PROFILE_FILE).get(self.profile_var.get())
        if not data:
            return
        self.host_var.set(data.get("host", ""))
        self.port_var.set(data.get("port", HINT_PORT))
        self.user_var.set(data.get("user", HINT_USERNAME))
        self.key_var.set(data.get("key", ""))
        self.service_var.set(data.get("service", HINT_SERVICE))
        self.logpath_var.set(data.get("log_path", HINT_LOG_PATH))
        self.upload_target_var.set(data.get("upload_target", HINT_UPLOAD_TARGET))
        self.manager_var.set(data.get("manager", "systemd"))

    def _save_profile(self) -> None:
        name = simpledialog.askstring("Save Profile", "Profile name:",
                                      initialvalue=self.profile_var.get() or self.host_var.get(),
                                      parent=self.root)
        if not name:
            return
        profiles = self._read_json(PROFILE_FILE)
        profiles[name] = {  # passphrase intentionally not stored
            "host": self.host_var.get().strip(), "port": self.port_var.get().strip() or HINT_PORT,
            "user": self.user_var.get().strip() or HINT_USERNAME, "key": self.key_var.get().strip(),
            "service": self.service_var.get().strip(), "log_path": self.logpath_var.get().strip(),
            "upload_target": self.upload_target_var.get().strip(), "manager": self.manager_var.get(),
        }
        self._write_json(PROFILE_FILE, profiles)
        self._load_profiles_into_combo()
        self.profile_var.set(name)
        self.settings["last_profile"] = name
        self._write_json(SETTINGS_FILE, self.settings)
        messagebox.showinfo("Saved", f"Profile '{name}' saved.")

    def _delete_profile(self) -> None:
        name = self.profile_var.get()
        if not name:
            return
        if not messagebox.askyesno("Confirm", f"Delete profile '{name}'?"):
            return
        profiles = self._read_json(PROFILE_FILE)
        profiles.pop(name, None)
        self._write_json(PROFILE_FILE, profiles)
        self._load_profiles_into_combo()
        self.profile_var.set("")

    # ================================================================== #
    #  Status bar
    # ================================================================== #
    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self.root, style="Panel.TFrame", padding=(10, 4))
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(4, 8))
        self.status_dot = tk.Canvas(bar, width=14, height=14, highlightthickness=0, bg=THEMES[self.theme_name]["panel"])
        self._dot = self.status_dot.create_oval(2, 2, 12, 12, fill=THEMES[self.theme_name]["err"])
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.status_var = tk.StringVar(value=self._t("disconnected"))
        ttk.Label(bar, textvariable=self.status_var, style="Panel.TLabel").pack(side=tk.LEFT)
        self.path_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.path_var, style="Panel.TLabel").pack(side=tk.RIGHT)

    def _set_status(self, connected: bool, message: str | None = None) -> None:
        self.connected = connected
        color = self.palette["ok"] if connected else self.palette["err"]
        self.status_dot.configure(bg=self.palette["panel"])
        self.status_dot.itemconfig(self._dot, fill=color)
        self.status_var.set(message or (self._t("connected") if connected else self._t("disconnected")))
        self.connect_btn.config(text=self._t("disconnect") if connected else self._t("connect"))
        self.path_var.set(self.current_path if connected else "")

    # ================================================================== #
    #  Notebook
    # ================================================================== #
    def _build_notebook(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self._build_files_tab()
        self._build_controller_tab()
        self._build_env_tab()
        self._build_terminal_tab()

    # ================================================================== #
    #  TAB 1 - File manager
    # ================================================================== #
    def _build_files_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text=self._t("tab_files"))

        left = ttk.Frame(tab, style="Panel.TFrame", padding=6)
        left.pack(side=tk.LEFT, fill=tk.Y)

        tb = ttk.Frame(left, style="Panel.TFrame")
        tb.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(tb, text=self._t("refresh"), command=self.refresh_listing).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text=self._t("new_file"), command=self._new_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text=self._t("new_folder"), command=self._new_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text=self._t("delete"), command=self._delete_selected).pack(side=tk.LEFT, padx=2)

        # Breadcrumb
        self.breadcrumb = ttk.Frame(left, style="Panel.TFrame")
        self.breadcrumb.pack(fill=tk.X, pady=(0, 4))

        filt = ttk.Frame(left, style="Panel.TFrame")
        filt.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filt, text=self._t("filter"), style="Panel.TLabel").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(filt, textvariable=self.filter_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        ttk.Button(left, text=self._t("upload"), command=self._upload_folder).pack(fill=tk.X, pady=(0, 2))
        ttk.Button(left, text=self._t("download"), command=self._download_selected).pack(fill=tk.X, pady=(0, 2))
        ttk.Label(left, text=self._t("upload_target"), style="Panel.TLabel").pack(anchor="w")
        self.upload_target_var = tk.StringVar(value=HINT_UPLOAD_TARGET)
        ttk.Entry(left, textvariable=self.upload_target_var).pack(fill=tk.X, pady=(0, 4))
        self.progress = ttk.Progressbar(left, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 6))

        lf = ttk.Frame(left, style="Panel.TFrame")
        lf.pack(fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(lf, orient=tk.VERTICAL)
        self.file_list = tk.Listbox(lf, width=46, height=24, font=MONO_FONT, activestyle="none", yscrollcommand=sb.set)
        sb.config(command=self.file_list.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_list.bind("<Double-Button-1>", self._on_list_double_click)
        self.file_list.bind("<Button-3>", self._show_context_menu)
        self._themed_listboxes.append(self.file_list)

        self.ctx_menu = tk.Menu(self.root, tearoff=0)
        self.ctx_menu.add_command(label=self._t("download"), command=self._download_selected)
        self.ctx_menu.add_command(label=self._t("rename"), command=self._rename_selected)
        self.ctx_menu.add_command(label=self._t("copy_path"), command=self._copy_path_selected)
        self.ctx_menu.add_command(label=self._t("chmod"), command=self._chmod_selected)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label=self._t("delete"), command=self._delete_selected)

        right = ttk.Frame(tab, padding=(6, 0))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        et = ttk.Frame(right)
        et.pack(fill=tk.X, pady=(0, 6))
        self.editor_label_var = tk.StringVar(value=self._t("no_file"))
        ttk.Label(et, textvariable=self.editor_label_var).pack(side=tk.LEFT)
        ttk.Label(et, text=f"  | highlight: {'on' if HAS_PYGMENTS else 'off (pip install pygments)'}").pack(side=tk.LEFT)
        ttk.Button(et, text=self._t("save_server"), style="Accent.TButton", command=self._save_active_file).pack(side=tk.RIGHT, padx=2)

        ef = ttk.Frame(right)
        ef.pack(fill=tk.BOTH, expand=True)
        ys = ttk.Scrollbar(ef, orient=tk.VERTICAL)
        xs = ttk.Scrollbar(ef, orient=tk.HORIZONTAL)
        self.editor = tk.Text(ef, wrap=tk.NONE, font=MONO_FONT, undo=True, yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.config(command=self.editor.yview)
        xs.config(command=self.editor.xview)
        ys.pack(side=tk.RIGHT, fill=tk.Y)
        xs.pack(side=tk.BOTTOM, fill=tk.X)
        self.editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._themed_texts.append(self.editor)
        self.editor.bind("<Control-s>", self._on_ctrl_s)
        self.editor.bind("<KeyRelease>", self._on_editor_keyrelease)

    def _rebuild_breadcrumb(self) -> None:
        for w in self.breadcrumb.winfo_children():
            w.destroy()
        parts = [p for p in self.current_path.split("/") if p]
        ttk.Button(self.breadcrumb, text="/", width=2,
                   command=lambda: self._go_path("/")).pack(side=tk.LEFT)
        acc = ""
        for part in parts:
            acc += "/" + part
            ttk.Button(self.breadcrumb, text=part, command=lambda p=acc: self._go_path(p)).pack(side=tk.LEFT)

    def _go_path(self, path: str) -> None:
        self.current_path = path
        self.filter_var.set("")
        self.refresh_listing()

    # ----- Listing ---------------------------------------------------- #
    def refresh_listing(self) -> None:
        if not self._require_connection():
            return
        self._run_bg(self._task_list_dir, self.current_path)

    def _task_list_dir(self, path: str) -> None:
        try:
            entries = self.sftp_client.listdir_attr(path)
        except Exception as exc:  # noqa: BLE001
            self._error("List error", exc)
            return
        rows = [{"name": "..", "is_dir": True, "display": ".. (Parent Directory)"}]
        dirs, files = [], []
        for a in entries:
            is_dir = stat.S_ISDIR(a.st_mode)
            mtime = datetime.datetime.fromtimestamp(a.st_mtime or 0).strftime("%Y-%m-%d %H:%M")
            if is_dir:
                disp = f"[D] {a.filename:<26} {'<dir>':>8}  {mtime}"
                dirs.append({"name": a.filename, "is_dir": True, "display": disp})
            else:
                disp = f"[F] {a.filename:<26} {_human_size(a.st_size or 0):>8}  {mtime}"
                files.append({"name": a.filename, "is_dir": False, "display": disp})
        dirs.sort(key=lambda e: e["name"].lower())
        files.sort(key=lambda e: e["name"].lower())
        rows += dirs + files

        def update():
            self._all_entries = rows
            self._apply_filter()
            self.path_var.set(self.current_path)
            self._rebuild_breadcrumb()

        self._ui(update)

    def _apply_filter(self) -> None:
        needle = self.filter_var.get().strip().lower()
        self.file_list.delete(0, tk.END)
        self._visible_entries = []
        for e in self._all_entries:
            if e["name"] == ".." or not needle or needle in e["name"].lower():
                self.file_list.insert(tk.END, e["display"])
                self._visible_entries.append(e)

    def _selected_entry(self) -> dict | None:
        sel = self.file_list.curselection()
        if not sel:
            return None
        idx = sel[0]
        if 0 <= idx < len(self._visible_entries):
            return self._visible_entries[idx]
        return None

    def _on_list_double_click(self, _e=None) -> None:
        if not self._require_connection():
            return
        e = self._selected_entry()
        if not e:
            return
        if e["name"] == "..":
            self.current_path = os.path.dirname(self.current_path.rstrip("/")) or "/"
            self.filter_var.set("")
            self.refresh_listing()
        elif e["is_dir"]:
            self.current_path = self._join(self.current_path, e["name"])
            self.filter_var.set("")
            self.refresh_listing()
        else:
            if not self._confirm_discard():
                return
            self._run_bg(self._task_open_file, self._join(self.current_path, e["name"]))

    def _task_open_file(self, remote_path: str) -> None:
        try:
            with self.sftp_client.open(remote_path, "r") as fh:
                text = fh.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._error("Open error", exc)
            return

        def update():
            self.editor.delete("1.0", tk.END)
            self.editor.insert("1.0", text)
            self.active_file = remote_path
            self._loaded_content = text
            self.editor_label_var.set(f"Editing: {remote_path}")
            self._highlight_editor()

        self._ui(update)

    def _is_editor_dirty(self) -> bool:
        return bool(self.active_file) and self.editor.get("1.0", "end-1c") != self._loaded_content

    def _confirm_discard(self) -> bool:
        if not self._is_editor_dirty():
            return True
        ans = messagebox.askyesnocancel("Unsaved changes", "Save changes before continuing?")
        if ans is None:
            return False
        if ans:
            self._save_active_file()
        return True

    def _on_ctrl_s(self, _e=None) -> str:
        self._save_active_file()
        return "break"

    def _save_active_file(self) -> None:
        if not self._require_connection() or not self.active_file:
            if not self.active_file:
                messagebox.showwarning("No file", "Open a file before saving.")
            return
        content = self.editor.get("1.0", "end-1c")
        self._run_bg(self._task_save_file, self.active_file, content)

    def _task_save_file(self, remote_path: str, content: str) -> None:
        try:
            with self.sftp_client.open(remote_path, "w") as fh:
                fh.write(content.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._error("Save error", exc)
            return

        def done():
            self._loaded_content = content
            messagebox.showinfo("Saved", f"Saved to:\n{remote_path}")

        self._ui(done)

    # ----- Context menu actions -------------------------------------- #
    def _show_context_menu(self, event) -> None:
        idx = self.file_list.nearest(event.y)
        if idx >= 0:
            self.file_list.selection_clear(0, tk.END)
            self.file_list.selection_set(idx)
        e = self._selected_entry()
        if e and e["name"] != "..":
            self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _copy_path_selected(self) -> None:
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        path = self._join(self.current_path, e["name"])
        self.root.clipboard_clear()
        self.root.clipboard_append(path)
        messagebox.showinfo("Copied", path)

    def _rename_selected(self) -> None:
        if not self._require_connection():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        new = simpledialog.askstring("Rename", "New name:", initialvalue=e["name"], parent=self.root)
        if not new or new == e["name"]:
            return
        old_p = self._join(self.current_path, e["name"])
        new_p = self._join(self.current_path, new)
        self._run_bg(self._task_rename, old_p, new_p)

    def _task_rename(self, old_p: str, new_p: str) -> None:
        try:
            self.sftp_client.rename(old_p, new_p)
        except Exception as exc:  # noqa: BLE001
            self._error("Rename error", exc)
            return
        self._ui(self.refresh_listing)

    def _chmod_selected(self) -> None:
        if not self._require_connection():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        mode = simpledialog.askstring("chmod", "Octal mode (e.g. 644):", initialvalue="644", parent=self.root)
        if not mode:
            return
        try:
            mode_int = int(mode, 8)
        except ValueError:
            messagebox.showerror("chmod", "Invalid octal mode.")
            return
        self._run_bg(self._task_chmod, self._join(self.current_path, e["name"]), mode_int)

    def _task_chmod(self, path: str, mode_int: int) -> None:
        try:
            self.sftp_client.chmod(path, mode_int)
        except Exception as exc:  # noqa: BLE001
            self._error("chmod error", exc)
            return
        self._ui(self.refresh_listing)

    # ----- CRUD ------------------------------------------------------- #
    def _new_file(self) -> None:
        if not self._require_connection():
            return
        name = simpledialog.askstring("New File", "File name:", parent=self.root)
        if name:
            self._run_bg(self._task_new_file, self._join(self.current_path, name))

    def _task_new_file(self, remote_path: str) -> None:
        try:
            with self.sftp_client.open(remote_path, "x") as fh:
                fh.write(b"")
        except Exception as exc:  # noqa: BLE001
            self._error("Create error", exc)
            return
        self._ui(self.refresh_listing)

    def _new_folder(self) -> None:
        if not self._require_connection():
            return
        name = simpledialog.askstring("New Folder", "Folder name:", parent=self.root)
        if name:
            self._run_bg(self._task_new_folder, self._join(self.current_path, name))

    def _task_new_folder(self, remote_path: str) -> None:
        try:
            self.sftp_client.mkdir(remote_path)
        except Exception as exc:  # noqa: BLE001
            self._error("Mkdir error", exc)
            return
        self._ui(self.refresh_listing)

    def _delete_selected(self) -> None:
        if not self._require_connection():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        remote_path = self._join(self.current_path, e["name"])
        if not messagebox.askyesno("Confirm delete", f"Delete {remote_path}?"):
            return
        self._run_bg(self._task_delete, remote_path, e["is_dir"])

    def _task_delete(self, remote_path: str, is_dir: bool) -> None:
        try:
            if is_dir:
                self._rmtree(remote_path)
            else:
                self.sftp_client.remove(remote_path)
        except Exception as exc:  # noqa: BLE001
            self._error("Delete error", exc)
            return
        self._ui(self.refresh_listing)

    def _rmtree(self, remote_path: str) -> None:
        for a in self.sftp_client.listdir_attr(remote_path):
            child = self._join(remote_path, a.filename)
            if stat.S_ISDIR(a.st_mode):
                self._rmtree(child)
            else:
                self.sftp_client.remove(child)
        self.sftp_client.rmdir(remote_path)

    # ----- Upload / Download ----------------------------------------- #
    def _upload_folder(self) -> None:
        if not self._require_connection():
            return
        local_dir = filedialog.askdirectory(title="Select folder")
        if not local_dir:
            return
        target = self.upload_target_var.get().strip()
        if not target:
            messagebox.showwarning("Missing target", "Enter the remote target dir.")
            return
        self._run_bg(self._task_upload_folder, local_dir, target)

    def _task_upload_folder(self, local_dir: str, target: str) -> None:
        base = os.path.basename(local_dir.rstrip("/\\"))
        remote_root = self._join(target, base)
        all_files = [os.path.join(r, f) for r, _d, fs in os.walk(local_dir) for f in fs]
        total = len(all_files) or 1
        self._ui(lambda: self.progress.config(maximum=total, value=0))
        try:
            self._sftp_makedirs(remote_root)
            done = 0
            for r, _d, files in os.walk(local_dir):
                rel = os.path.relpath(r, local_dir)
                rdir = remote_root if rel == "." else self._join(remote_root, rel.replace(os.sep, "/"))
                self._sftp_makedirs(rdir)
                for fn in files:
                    self.sftp_client.put(os.path.join(r, fn), self._join(rdir, fn))
                    done += 1
                    self._ui(lambda d=done: self.progress.config(value=d))
        except Exception as exc:  # noqa: BLE001
            self._ui(lambda: self.progress.config(value=0))
            self._error("Upload error", exc)
            return
        self._ui(lambda: self.progress.config(value=0))
        self._ui(lambda: messagebox.showinfo("Upload complete", f"Uploaded {len(all_files)} files to:\n{remote_root}"))

    def _sftp_makedirs(self, remote_dir: str) -> None:
        path = ""
        for part in remote_dir.strip("/").split("/"):
            path += "/" + part
            try:
                self.sftp_client.stat(path)
            except IOError:
                self.sftp_client.mkdir(path)

    def _download_selected(self) -> None:
        if not self._require_connection():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        local_dir = filedialog.askdirectory(title="Select local destination")
        if not local_dir:
            return
        self._run_bg(self._task_download, self._join(self.current_path, e["name"]), e["name"], local_dir, e["is_dir"])

    def _task_download(self, remote_path, name, local_dir, is_dir) -> None:
        try:
            if is_dir:
                count = self._download_tree(remote_path, os.path.join(local_dir, name))
            else:
                self.sftp_client.get(remote_path, os.path.join(local_dir, name))
                count = 1
        except Exception as exc:  # noqa: BLE001
            self._error("Download error", exc)
            return
        self._ui(lambda: messagebox.showinfo("Download complete", f"Downloaded {count} file(s) to:\n{local_dir}"))

    def _download_tree(self, remote_dir: str, local_dir: str) -> int:
        os.makedirs(local_dir, exist_ok=True)
        count = 0
        for a in self.sftp_client.listdir_attr(remote_dir):
            cr = self._join(remote_dir, a.filename)
            cl = os.path.join(local_dir, a.filename)
            if stat.S_ISDIR(a.st_mode):
                count += self._download_tree(cr, cl)
            else:
                self.sftp_client.get(cr, cl)
                count += 1
        return count

    # ================================================================== #
    #  TAB 2 - Controller
    # ================================================================== #
    def _build_controller_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text=self._t("tab_ctrl"))

        cfg = ttk.Frame(tab, style="Panel.TFrame", padding=8)
        cfg.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(cfg, text=self._t("manager"), style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self.manager_var = tk.StringVar(value="systemd")
        ttk.Combobox(cfg, textvariable=self.manager_var, values=["systemd", "pm2"], width=10, state="readonly").grid(row=0, column=1, padx=4, pady=4, sticky="w")
        ttk.Label(cfg, text=self._t("service"), style="Panel.TLabel").grid(row=0, column=2, sticky="e", padx=4)
        self.service_var = tk.StringVar(value=HINT_SERVICE)
        ttk.Entry(cfg, textvariable=self.service_var, width=18).grid(row=0, column=3, padx=4, pady=4)
        ttk.Label(cfg, text=self._t("bot"), style="Panel.TLabel").grid(row=0, column=4, sticky="e", padx=4)
        self.bot_dot = tk.Canvas(cfg, width=14, height=14, highlightthickness=0)
        self._bot_dot = self.bot_dot.create_oval(2, 2, 12, 12, fill="#888888")
        self.bot_dot.grid(row=0, column=5, padx=2)
        self.bot_status_var = tk.StringVar(value="unknown")
        ttk.Label(cfg, textvariable=self.bot_status_var, style="Panel.TLabel").grid(row=0, column=6, sticky="w", padx=2)

        ttk.Label(cfg, text=self._t("log_path"), style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.logpath_var = tk.StringVar(value=HINT_LOG_PATH)
        ttk.Entry(cfg, textvariable=self.logpath_var, width=46).grid(row=1, column=1, columnspan=3, padx=4, pady=4, sticky="we")
        ttk.Label(cfg, text=self._t("log_lines"), style="Panel.TLabel").grid(row=1, column=4, sticky="e", padx=4)
        self.loglines_var = tk.StringVar(value="50")
        ttk.Entry(cfg, textvariable=self.loglines_var, width=6).grid(row=1, column=5, columnspan=2, padx=4, pady=4, sticky="w")

        b1 = ttk.Frame(tab)
        b1.pack(fill=tk.X, pady=(0, 4))
        for txt, act in (("start", "start"), ("stop", "stop"), ("restart", "restart"), ("status", "status")):
            ttk.Button(b1, text=self._t(txt), command=lambda a=act: self._service_action(a)).pack(side=tk.LEFT, padx=3)
        ttk.Button(b1, text=self._t("check_status"), command=self._check_status).pack(side=tk.LEFT, padx=3)
        self.auto_status_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(b1, text=self._t("auto_refresh"), variable=self.auto_status_var, command=self._toggle_auto_status).pack(side=tk.LEFT, padx=6)
        ttk.Button(b1, text=self._t("renew_ssl"), command=lambda: self._exec_simple("sudo certbot renew")).pack(side=tk.LEFT, padx=3)

        b2 = ttk.Frame(tab)
        b2.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(b2, text=self._t("health"), command=self._server_health).pack(side=tk.LEFT, padx=3)
        ttk.Button(b2, text=self._t("nginx_test"), command=lambda: self._exec_simple("sudo nginx -t")).pack(side=tk.LEFT, padx=3)
        ttk.Button(b2, text=self._t("nginx_reload"), command=lambda: self._exec_simple("sudo systemctl reload nginx")).pack(side=tk.LEFT, padx=3)
        ttk.Label(b2, text=self._t("log_filter"), style="TLabel").pack(side=tk.LEFT, padx=(10, 2))
        self.logfilter_var = tk.StringVar()
        self.logfilter_var.trace_add("write", lambda *_: self._rerender_console())
        ttk.Entry(b2, textvariable=self.logfilter_var, width=14).pack(side=tk.LEFT)
        self.follow_btn = ttk.Button(b2, text=self._t("stream"), style="Accent.TButton", command=self._toggle_follow_logs)
        self.follow_btn.pack(side=tk.RIGHT, padx=3)
        ttk.Button(b2, text=self._t("tail_once"), command=self._stream_logs_once).pack(side=tk.RIGHT, padx=3)
        ttk.Button(b2, text=self._t("clear"), command=self._clear_console).pack(side=tk.RIGHT, padx=3)

        self.console = tk.Text(tab, bg=CONSOLE_BG, fg=CONSOLE_FG, font=MONO_FONT, wrap=tk.WORD, insertbackground=CONSOLE_FG)
        cs = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.console.yview)
        self.console.config(yscrollcommand=cs.set)
        cs.pack(side=tk.RIGHT, fill=tk.Y)
        self.console.pack(fill=tk.BOTH, expand=True)

    def _console_append(self, text: str) -> None:
        for line in text.splitlines(keepends=True):
            self._console_lines.append(line)
        flt = self.logfilter_var.get().strip().lower()
        if not flt or flt in text.lower():
            self.console.insert(tk.END, text)
            self.console.see(tk.END)

    def _rerender_console(self) -> None:
        flt = self.logfilter_var.get().strip().lower()
        self.console.delete("1.0", tk.END)
        for line in self._console_lines:
            if not flt or flt in line.lower():
                self.console.insert(tk.END, line)
        self.console.see(tk.END)

    def _clear_console(self) -> None:
        self._console_lines = []
        self.console.delete("1.0", tk.END)

    def _build_service_cmd(self, action: str) -> str:
        svc = self.service_var.get().strip() or HINT_SERVICE
        if self.manager_var.get() == "pm2":
            return f"pm2 describe {svc}" if action == "status" else f"pm2 {action} {svc}"
        return f"sudo systemctl {action} {svc}"

    def _service_action(self, action: str) -> None:
        if not self._require_connection():
            return
        cmd = self._build_service_cmd(action)
        self._ui(lambda: self._console_append(f"\n$ {cmd}\n"))
        self._run_bg(self._task_exec_then_status, cmd, action != "status")

    def _task_exec_then_status(self, cmd: str, then_check: bool) -> None:
        self._task_exec_to_console(cmd)
        if then_check:
            time.sleep(0.6)
            self._task_check_status()

    def _server_health(self) -> None:
        if not self._require_connection():
            return
        self._exec_simple("echo '== DISK =='; df -h; echo '== MEM =='; free -m; "
                          "echo '== LOAD =='; uptime; echo '== TOP =='; "
                          "ps -eo pid,pcpu,pmem,comm --sort=-pcpu | head -n 12")

    def _exec_simple(self, cmd: str) -> None:
        if not self._require_connection():
            return
        self._ui(lambda: self._console_append(f"\n$ {cmd}\n"))
        self._run_bg(self._task_exec_to_console, cmd)

    def _task_exec_to_console(self, cmd: str) -> None:
        try:
            _i, out_s, err_s = self.ssh_client.exec_command(cmd, timeout=90)
            out = out_s.read().decode("utf-8", errors="replace")
            err = err_s.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._error("Command error", exc)
            return
        result = (out or "") + (("\n[stderr]\n" + err) if err.strip() else "")
        self._ui(lambda: self._console_append(result + "\n"))

    def _check_status(self) -> None:
        if not self._require_connection():
            return
        self._run_bg(self._task_check_status)

    def _task_check_status(self) -> None:
        svc = self.service_var.get().strip() or HINT_SERVICE
        if self.manager_var.get() == "pm2":
            cmd = f"pm2 describe {svc} | grep -i status | head -n1"
            kw = "online"
        else:
            cmd = f"systemctl is-active {svc}"
            kw = "active"
        try:
            _i, out_s, _e = self.ssh_client.exec_command(cmd, timeout=30)
            out = out_s.read().decode("utf-8", errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            self._error("Status error", exc)
            return
        online = kw in out.lower()
        if kw == "active" and "inactive" in out.lower():
            online = False
        label = out.splitlines()[0] if out else ("online" if online else "offline")

        def update():
            self.bot_dot.itemconfig(self._bot_dot, fill=self.palette["ok"] if online else self.palette["err"])
            self.bot_status_var.set(label)

        self._ui(update)

    def _toggle_auto_status(self) -> None:
        if self.auto_status_var.get():
            self._schedule_auto_status()

    def _schedule_auto_status(self) -> None:
        if self.auto_status_var.get() and self.connected:
            self._check_status()
            self.root.after(8000, self._schedule_auto_status)

    def _resolve_log_lines(self) -> str:
        v = self.loglines_var.get().strip()
        return v if v.isdigit() and int(v) > 0 else "50"

    def _stream_logs_once(self) -> None:
        if not self._require_connection():
            return
        self._exec_simple(f"tail -n {self._resolve_log_lines()} {self.logpath_var.get().strip() or HINT_LOG_PATH}")

    def _toggle_follow_logs(self) -> None:
        self._stop_follow_logs() if self._log_following else self._start_follow_logs()

    def _start_follow_logs(self) -> None:
        if not self._require_connection():
            return
        log_path = self.logpath_var.get().strip() or HINT_LOG_PATH
        self._log_stop.clear()
        self._log_following = True
        self.follow_btn.config(text=self._t("stop_stream"))
        self._console_append(f"\n$ tail -f -n {self._resolve_log_lines()} {log_path}\n")
        self._run_bg(self._task_follow_logs, log_path)

    def _stop_follow_logs(self) -> None:
        self._log_stop.set()
        if self._log_channel is not None:
            try:
                self._log_channel.close()
            except Exception:  # noqa: BLE001
                pass
        self._log_following = False
        try:
            self.follow_btn.config(text=self._t("stream"))
        except tk.TclError:
            pass

    def _task_follow_logs(self, log_path: str) -> None:
        try:
            channel = self.ssh_client.get_transport().open_session()
            self._log_channel = channel
            channel.exec_command(f"tail -f -n {self._resolve_log_lines()} {log_path}")
            while not self._log_stop.is_set():
                if channel.recv_ready():
                    data = channel.recv(4096).decode("utf-8", errors="replace")
                    if data:
                        self._ui(lambda d=data: self._console_append(d))
                elif channel.exit_status_ready() and not channel.recv_ready():
                    break
                else:
                    time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            self._error("Log stream error", exc)
        finally:
            self._log_channel = None
            self._log_following = False
            self._ui(lambda: self.follow_btn.config(text=self._t("stream")))

    # ================================================================== #
    #  TAB 3 - .env
    # ================================================================== #
    def _build_env_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text=self._t("tab_env"))
        tb = ttk.Frame(tab)
        tb.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(tb, text=self._t("env_path")).pack(side=tk.LEFT, padx=(0, 4))
        self.env_path_var = tk.StringVar(value="")
        ttk.Entry(tb, textvariable=self.env_path_var, width=50).pack(side=tk.LEFT, padx=4)
        ttk.Button(tb, text=self._t("load_env"), command=self._load_env).pack(side=tk.LEFT, padx=4)
        ttk.Button(tb, text=self._t("save_env"), style="Accent.TButton", command=self._save_env).pack(side=tk.LEFT, padx=4)
        ef = ttk.Frame(tab)
        ef.pack(fill=tk.BOTH, expand=True)
        es = ttk.Scrollbar(ef, orient=tk.VERTICAL)
        self.env_editor = tk.Text(ef, font=MONO_FONT, wrap=tk.NONE, undo=True, yscrollcommand=es.set)
        es.config(command=self.env_editor.yview)
        es.pack(side=tk.RIGHT, fill=tk.Y)
        self.env_editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._themed_texts.append(self.env_editor)

    def _load_env(self) -> None:
        if not self._require_connection():
            return
        path = self.env_path_var.get().strip() or self._join(self.current_path, ".env")
        self.env_path_var.set(path)
        self._run_bg(self._task_load_env, path)

    def _task_load_env(self, path: str) -> None:
        try:
            with self.sftp_client.open(path, "r") as fh:
                text = fh.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._error("Load .env error", exc)
            return

        def update():
            self.env_editor.delete("1.0", tk.END)
            self.env_editor.insert("1.0", text)
            self.active_env_file = path

        self._ui(update)

    def _save_env(self) -> None:
        if not self._require_connection():
            return
        path = self.env_path_var.get().strip()
        if not path:
            messagebox.showwarning("No path", "Provide the .env path first.")
            return
        content = self.env_editor.get("1.0", "end-1c")
        self._run_bg(self._task_save_env, path, content)

    def _task_save_env(self, path: str, content: str) -> None:
        try:
            with self.sftp_client.open(path, "w") as fh:
                fh.write(content.encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            self._error("Save .env error", exc)
            return
        self._ui(lambda: messagebox.showinfo("Saved", f"Saved {path}"))

    # ================================================================== #
    #  TAB 4 - Terminal (ANSI colours + history)
    # ================================================================== #
    def _build_terminal_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text=self._t("tab_term"))
        q = ttk.Frame(tab)
        q.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(q, text=self._t("quick")).pack(side=tk.LEFT, padx=(0, 4))
        for cmd in ("pip install -r requirements.txt", "ls -la", "df -h"):
            ttk.Button(q, text=cmd, command=lambda c=cmd: self._quick_command(c)).pack(side=tk.LEFT, padx=2)
        ttk.Button(q, text=self._t("clear"), command=lambda: self.term_output.delete("1.0", tk.END)).pack(side=tk.RIGHT, padx=2)

        self.term_output = tk.Text(tab, bg=CONSOLE_BG, fg=CONSOLE_FG, font=MONO_FONT, wrap=tk.WORD, insertbackground=CONSOLE_FG)
        ts = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.term_output.yview)
        self.term_output.config(yscrollcommand=ts.set)
        ts.pack(side=tk.RIGHT, fill=tk.Y)
        self.term_output.pack(fill=tk.BOTH, expand=True)
        for code, color in ANSI_COLORS.items():
            self.term_output.tag_configure(f"ansi{code}", foreground=color)

        ir = ttk.Frame(tab)
        ir.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(ir, text="$").pack(side=tk.LEFT, padx=(0, 4))
        self.term_input = ttk.Entry(ir)
        self.term_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.term_input.bind("<Return>", self._run_terminal_command)
        self.term_input.bind("<Up>", self._history_up)
        self.term_input.bind("<Down>", self._history_down)
        ttk.Button(ir, text=self._t("run"), command=self._run_terminal_command).pack(side=tk.LEFT, padx=4)

    def _history_up(self, _e=None) -> str:
        if self._cmd_history:
            self._cmd_index = max(0, self._cmd_index - 1)
            self.term_input.delete(0, tk.END)
            self.term_input.insert(0, self._cmd_history[self._cmd_index])
        return "break"

    def _history_down(self, _e=None) -> str:
        if self._cmd_history:
            self._cmd_index = min(len(self._cmd_history), self._cmd_index + 1)
            self.term_input.delete(0, tk.END)
            if self._cmd_index < len(self._cmd_history):
                self.term_input.insert(0, self._cmd_history[self._cmd_index])
        return "break"

    def _term_write_ansi(self, data: str) -> None:
        parts = re.split(r"(\x1b\[[0-9;]*m)", data)
        cur = None
        for part in parts:
            if not part:
                continue
            m = re.match(r"\x1b\[([0-9;]*)m", part)
            if m:
                codes = m.group(1).split(";")
                cur = None
                for c in codes:
                    if c in ANSI_COLORS:
                        cur = f"ansi{c}"
                    elif c in ("0", ""):
                        cur = None
                continue
            if cur:
                self.term_output.insert(tk.END, part, cur)
            else:
                self.term_output.insert(tk.END, part)
        self.term_output.see(tk.END)

    def _quick_command(self, command: str) -> None:
        if self._require_connection():
            self._exec_terminal(command)

    def _run_terminal_command(self, _e=None) -> None:
        if not self._require_connection():
            return
        command = self.term_input.get().strip()
        if not command:
            return
        self.term_input.delete(0, tk.END)
        self._cmd_history.append(command)
        self._cmd_index = len(self._cmd_history)
        self._exec_terminal(command)

    def _exec_terminal(self, command: str) -> None:
        self._term_write_ansi(f"\n{self.current_path}$ {command}\n")
        self._run_bg(self._task_terminal_exec, f"cd {self.current_path} && {command}")

    def _task_terminal_exec(self, full_cmd: str) -> None:
        try:
            _i, out_s, err_s = self.ssh_client.exec_command(full_cmd, timeout=180)
            out = out_s.read().decode("utf-8", errors="replace")
            err = err_s.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._error("Terminal error", exc)
            return

        def update():
            if out:
                self._term_write_ansi(out)
            if err.strip():
                self._term_write_ansi(err)

        self._ui(update)

    # ================================================================== #
    #  Syntax highlighting
    # ================================================================== #
    def _configure_highlight_tags(self) -> None:
        if not hasattr(self, "editor"):
            return
        for tag, color in TOKEN_COLORS[self.theme_name].items():
            self.editor.tag_configure(tag, foreground=color)

    def _on_editor_keyrelease(self, _e=None) -> None:
        if not HAS_PYGMENTS:
            return
        if self._highlight_job:
            self.root.after_cancel(self._highlight_job)
        self._highlight_job = self.root.after(400, self._highlight_editor)

    def _highlight_editor(self) -> None:
        if not HAS_PYGMENTS or not self.active_file:
            return
        for tag in TOKEN_COLORS[self.theme_name]:
            self.editor.tag_remove(tag, "1.0", tk.END)
        try:
            lexer = get_lexer_for_filename(self.active_file)
        except Exception:  # noqa: BLE001
            lexer = TextLexer()
        content = self.editor.get("1.0", "end-1c")
        self.editor.mark_set("rs", "1.0")
        for tok, value in lex(content, lexer):
            tag = self._token_to_tag(tok)
            self.editor.mark_set("re", f"rs+{len(value)}c")
            if tag:
                self.editor.tag_add(tag, "rs", "re")
            self.editor.mark_set("rs", "re")

    @staticmethod
    def _token_to_tag(tok) -> str | None:
        if tok in Token.Comment:
            return "comment"
        if tok in Token.Keyword:
            return "keyword"
        if tok in Token.String:
            return "string"
        if tok in Token.Number:
            return "number"
        if tok in Token.Name.Function:
            return "name_function"
        if tok in Token.Name.Class:
            return "name_class"
        if tok in Token.Operator:
            return "operator"
        return None

    # ================================================================== #
    #  Connection
    # ================================================================== #
    def _toggle_connection(self) -> None:
        self._disconnect() if self.connected else self._connect()

    def _connect(self) -> None:
        host = self.host_var.get().strip()
        port = self.port_var.get().strip() or HINT_PORT
        user = self.user_var.get().strip() or HINT_USERNAME
        key = self.key_var.get().strip()
        passphrase = self.passphrase_var.get() or None
        use_agent = self.use_agent_var.get()

        if not host:
            messagebox.showwarning("Missing host", "Enter the public IP / host.")
            return
        if not port.isdigit() or not (0 < int(port) < 65536):
            messagebox.showwarning("Invalid port", "Port must be 1-65535.")
            return
        if not use_agent and (not key or not os.path.isfile(key)):
            messagebox.showwarning("Missing key", "Select a valid .pem key, or enable SSH agent.")
            return

        self._last_conn = {"host": host, "port": int(port), "user": user,
                           "key": key, "passphrase": passphrase, "use_agent": use_agent}
        self._set_status(False, self._t("connecting"))
        self.connect_btn.config(state=tk.DISABLED)
        self._run_bg(self._task_connect, self._last_conn)

    def _task_connect(self, c: dict) -> None:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs = dict(hostname=c["host"], port=c["port"], username=c["user"],
                          timeout=20, banner_timeout=20, auth_timeout=20)
            if c["use_agent"]:
                kwargs.update(allow_agent=True, look_for_keys=True)
            else:
                kwargs["pkey"] = self._load_private_key(c["key"], c["passphrase"])
            client.connect(**kwargs)
            sftp = client.open_sftp()
            try:
                home = sftp.normalize(".")
            except Exception:  # noqa: BLE001
                home = f"/home/{c['user']}"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or exc.__class__.__name__
            self._ui(lambda: self._connect_failed(msg))
            return

        def finish():
            self.ssh_client = client
            self.sftp_client = sftp
            self.current_path = home
            self.connect_btn.config(state=tk.NORMAL)
            self._set_status(True, f"{self._t('connected')}: {c['user']}@{c['host']}:{c['port']}")
            self.refresh_listing()
            if self.auto_status_var.get():
                self._schedule_auto_status()

        self._ui(finish)

    def _connect_failed(self, message: str) -> None:
        self.connect_btn.config(state=tk.NORMAL)
        self._set_status(False, self._t("disconnected"))
        messagebox.showerror("Connection failed", message)

    def _load_private_key(self, key_path: str, passphrase: str | None = None):
        # Build the candidate list dynamically: some key classes (e.g. DSSKey
        # for deprecated DSA keys) were removed in newer paramiko releases, so
        # referencing them directly would raise AttributeError.
        candidates = []
        for name in ("RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey"):
            cls = getattr(paramiko, name, None)
            if cls is not None:
                candidates.append(cls)
        last = None
        for cls in candidates:
            try:
                return cls.from_private_key_file(key_path, password=passphrase)
            except paramiko.PasswordRequiredException as exc:
                # Wrong/missing passphrase: stop early with a clear message.
                raise exc
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise last if last else RuntimeError("Unsupported or unreadable key file")

    def _disconnect(self) -> None:
        self._stop_follow_logs()
        try:
            if self.sftp_client:
                self.sftp_client.close()
            if self.ssh_client:
                self.ssh_client.close()
        except Exception:  # noqa: BLE001
            pass
        self.sftp_client = None
        self.ssh_client = None
        self.file_list.delete(0, tk.END)
        self._all_entries = []
        self._visible_entries = []
        self._set_status(False, self._t("disconnected"))

    def _watchdog(self) -> None:
        # Detect dropped connections and optionally auto-reconnect.
        if self.connected and self.ssh_client is not None:
            tr = self.ssh_client.get_transport()
            if tr is None or not tr.is_active():
                self.connected = False
                self._set_status(False, "Connection lost")
                if self.auto_reconnect_var.get() and self._last_conn and not self._reconnecting:
                    self._reconnecting = True
                    self._set_status(False, "Reconnecting...")
                    self._run_bg(self._reconnect)
        self.root.after(8000, self._watchdog)

    def _reconnect(self) -> None:
        try:
            self._task_connect(self._last_conn)
        finally:
            self._reconnecting = False

    # ================================================================== #
    #  Helpers
    # ================================================================== #
    def _run_bg(self, func, *args) -> None:
        def wrapper():
            try:
                func(*args)
            except Exception:  # noqa: BLE001
                tb = traceback.format_exc()
                self._ui(lambda: messagebox.showerror("Unexpected error", tb))

        threading.Thread(target=wrapper, daemon=True).start()

    def _ui(self, callback) -> None:
        self._ui_queue.put(callback)

    def _error(self, title: str, exc: Exception) -> None:
        message = str(exc) or exc.__class__.__name__
        self._ui(lambda: messagebox.showerror(title, message))

    def _pump_ui_queue(self) -> None:
        try:
            while True:
                cb = self._ui_queue.get_nowait()
                try:
                    cb()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
        except queue.Empty:
            pass
        finally:
            self.root.after(80, self._pump_ui_queue)

    def _require_connection(self) -> bool:
        if not self.connected or not self.ssh_client or not self.sftp_client:
            messagebox.showwarning("Not connected", "Connect to the server first.")
            return False
        return True

    @staticmethod
    def _join(base: str, name: str) -> str:
        return base + name if base.endswith("/") else base + "/" + name

    def _on_close(self) -> None:
        if not self._confirm_discard():
            return
        # Persist settings.
        self.settings.update({
            "theme": self.theme_name, "language": self.lang,
            "geometry": self.root.winfo_geometry(),
            "last_profile": self.profile_var.get(),
            "auto_reconnect": self.auto_reconnect_var.get(),
        })
        try:
            self.settings["last_tab"] = self.notebook.index("current")
        except tk.TclError:
            pass
        self._write_json(SETTINGS_FILE, self.settings)
        self._disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AwsTelegramManager(root)
    root.mainloop()


if __name__ == "__main__":
    main()
