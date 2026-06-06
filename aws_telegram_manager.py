#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Telegram Bot & Web App Manager
==================================

A production-ready desktop GUI application (Tkinter + Paramiko) to manage a
Telegram Bot and its associated Telegram Mini App (Web App) hosted on an AWS
EC2 instance over SSH/SFTP.

Every configurable value is entered by the user through editable input fields.

Features
--------
* Connection panel with savable server profiles (Host/Port/User/Key).
* Concurrent SSH + SFTP sessions; supports encrypted keys (passphrase).
* Tab 1 - File manager + IDE-like editor (optional syntax highlighting,
  Ctrl+S save), live filter/search, recursive folder upload with progress,
  and file/folder download.
* Tab 2 - Bot controller for systemd OR PM2, live status indicator,
  real-time log streaming (tail -f) with Stop, SSL renew, server health
  (disk/RAM/CPU), nginx test/reload.
* Tab 3 - Remote .env editor.
* Tab 4 - Interactive terminal + quick actions (pip install -r).
* Dark / Light theme toggle.

Optional dependency: ``pygments`` enables editor syntax highlighting. The app
runs fine without it.
"""

import os
import json
import stat
import time
import threading
import traceback
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import paramiko
except ImportError:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'paramiko'. Install it with:\n"
        "    pip install paramiko\n"
    )

# Optional: syntax highlighting. The app degrades gracefully without it.
try:
    from pygments import lex
    from pygments.lexers import get_lexer_for_filename, TextLexer
    from pygments.token import Token

    HAS_PYGMENTS = True
except ImportError:  # pragma: no cover
    HAS_PYGMENTS = False


# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
APP_TITLE = "AWS Telegram Bot & Web App Manager"
APP_GEOMETRY = "1280x820"

MONO_FONT = ("Consolas", 11)
UI_FONT = ("Segoe UI", 10)

# Editable starting hints (nothing is hard-coded into the logic).
HINT_PORT = "22"
HINT_USERNAME = "ubuntu"
HINT_SERVICE = "my_bot"
HINT_LOG_PATH = "/home/ubuntu/your_project/bot.log"
HINT_UPLOAD_TARGET = "/var/www/html"

# Persisted server profiles live here (no passwords/passphrases stored).
PROFILE_FILE = os.path.join(
    os.path.expanduser("~"), ".aws_telegram_manager_profiles.json"
)

# Console is always a terminal-style black/green regardless of theme.
CONSOLE_BG = "#000000"
CONSOLE_FG = "#00FF00"

# Two palettes for the Dark/Light theme toggle.
THEMES = {
    "dark": {
        "bg": "#1e1e2e",
        "panel": "#2a2a3c",
        "accent": "#5865f2",
        "text": "#e6e6e6",
        "entry": "#3a3a4f",
        "editor_bg": "#1b1b27",
        "editor_fg": "#f8f8f2",
        "ok": "#43b581",
        "err": "#f04747",
    },
    "light": {
        "bg": "#f2f2f5",
        "panel": "#e3e3ea",
        "accent": "#5865f2",
        "text": "#1e1e2e",
        "entry": "#ffffff",
        "editor_bg": "#ffffff",
        "editor_fg": "#1e1e2e",
        "ok": "#2e9e62",
        "err": "#cc3333",
    },
}

# Token -> colour mapping for syntax highlighting (used if pygments present).
TOKEN_COLORS_DARK = {
    "keyword": "#c586c0",
    "string": "#ce9178",
    "comment": "#6a9955",
    "number": "#b5cea8",
    "name_function": "#dcdcaa",
    "name_class": "#4ec9b0",
    "operator": "#d4d4d4",
}
TOKEN_COLORS_LIGHT = {
    "keyword": "#af00db",
    "string": "#a31515",
    "comment": "#008000",
    "number": "#098658",
    "name_function": "#795e26",
    "name_class": "#267f99",
    "operator": "#000000",
}


# --------------------------------------------------------------------------- #
#  Main Application
# --------------------------------------------------------------------------- #
class AwsTelegramManager:
    """Encapsulates the entire Tkinter application."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(APP_GEOMETRY)
        self.root.minsize(1080, 720)

        # --- SSH / SFTP state -------------------------------------------- #
        self.ssh_client: paramiko.SSHClient | None = None
        self.sftp_client: paramiko.SFTPClient | None = None
        self.connected = False

        self.current_path = "/home/ubuntu"
        self.active_file: str | None = None
        self.active_env_file: str | None = None

        # Cached directory items for live filtering.
        self._all_items: list[str] = []

        # Log streaming control.
        self._log_stop = threading.Event()
        self._log_following = False
        self._log_channel = None

        # Theme + widget registries for theme switching.
        self.theme_name = "dark"
        self.palette = THEMES[self.theme_name]
        self._themed_texts: list[tk.Text] = []
        self._themed_listboxes: list[tk.Listbox] = []

        # Thread-safe queue to marshal background results onto the UI thread.
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()

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

        self.root.after(80, self._pump_ui_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================== #
    #  Theme handling
    # ================================================================== #
    def _apply_theme(self, name: str) -> None:
        self.theme_name = name
        p = THEMES[name]
        self.palette = p
        self.root.configure(bg=p["bg"])

        s = self.style
        s.configure("TNotebook", background=p["bg"], borderwidth=0)
        s.configure(
            "TNotebook.Tab",
            background=p["panel"],
            foreground=p["text"],
            padding=(16, 8),
            font=UI_FONT,
        )
        s.map(
            "TNotebook.Tab",
            background=[("selected", p["accent"])],
            foreground=[("selected", "#ffffff")],
        )
        s.configure("TFrame", background=p["bg"])
        s.configure("Panel.TFrame", background=p["panel"])
        s.configure("TLabel", background=p["bg"], foreground=p["text"], font=UI_FONT)
        s.configure(
            "Panel.TLabel", background=p["panel"], foreground=p["text"], font=UI_FONT
        )
        s.configure("TButton", font=UI_FONT, padding=6)
        s.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        s.configure(
            "TEntry",
            fieldbackground=p["entry"],
            foreground=p["text"],
            insertcolor=p["text"],
        )
        s.configure(
            "TCombobox",
            fieldbackground=p["entry"],
            foreground=p["text"],
        )

        # Recolour raw tk widgets (ttk styles do not cover these).
        for txt in self._themed_texts:
            txt.configure(
                bg=p["editor_bg"],
                fg=p["editor_fg"],
                insertbackground=p["editor_fg"],
            )
        for lb in self._themed_listboxes:
            lb.configure(
                bg=p["editor_bg"],
                fg=p["editor_fg"],
                selectbackground=p["accent"],
            )
        self._configure_highlight_tags()
        # Re-highlight the open file with the new palette.
        if self.active_file:
            self._highlight_editor()

    def _toggle_theme(self) -> None:
        self._apply_theme("light" if self.theme_name == "dark" else "dark")

    # ================================================================== #
    #  Connection panel (with profiles)
    # ================================================================== #
    def _build_connection_panel(self) -> None:
        frame = ttk.Frame(self.root, style="Panel.TFrame", padding=10)
        frame.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 4))

        # Row 0: profiles -------------------------------------------------
        ttk.Label(frame, text="Profile:", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=4, pady=4
        )
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(
            frame, textvariable=self.profile_var, width=24, state="readonly"
        )
        self.profile_combo.grid(row=0, column=1, padx=4, pady=4, sticky="w")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)
        ttk.Button(frame, text="Save Profile", command=self._save_profile).grid(
            row=0, column=2, padx=4, pady=4
        )
        ttk.Button(frame, text="Delete Profile", command=self._delete_profile).grid(
            row=0, column=3, padx=4, pady=4
        )
        ttk.Button(frame, text="Toggle Theme", command=self._toggle_theme).grid(
            row=0, column=5, padx=4, pady=4, sticky="e"
        )

        # Row 1: host / port / username ----------------------------------
        ttk.Label(frame, text="Host (Public IP):", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=4, pady=4
        )
        self.host_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.host_var, width=26).grid(
            row=1, column=1, padx=4, pady=4, sticky="w"
        )
        ttk.Label(frame, text="Port:", style="Panel.TLabel").grid(
            row=1, column=2, sticky="e", padx=4, pady=4
        )
        self.port_var = tk.StringVar(value=HINT_PORT)
        ttk.Entry(frame, textvariable=self.port_var, width=7).grid(
            row=1, column=3, padx=4, pady=4, sticky="w"
        )
        ttk.Label(frame, text="Username:", style="Panel.TLabel").grid(
            row=1, column=4, sticky="e", padx=4, pady=4
        )
        self.user_var = tk.StringVar(value=HINT_USERNAME)
        ttk.Entry(frame, textvariable=self.user_var, width=16).grid(
            row=1, column=5, padx=4, pady=4, sticky="w"
        )

        # Row 2: key / passphrase / connect ------------------------------
        ttk.Label(frame, text="SSH Key (.pem):", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", padx=4, pady=4
        )
        self.key_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.key_var, width=26).grid(
            row=2, column=1, padx=4, pady=4, sticky="w"
        )
        ttk.Button(frame, text="Browse...", command=self._pick_key).grid(
            row=2, column=2, padx=4, pady=4
        )
        ttk.Label(frame, text="Passphrase:", style="Panel.TLabel").grid(
            row=2, column=3, sticky="e", padx=4, pady=4
        )
        self.passphrase_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.passphrase_var, width=16, show="*").grid(
            row=2, column=4, padx=4, pady=4, sticky="w"
        )
        self.connect_btn = ttk.Button(
            frame, text="Connect", style="Accent.TButton", command=self._toggle_connection
        )
        self.connect_btn.grid(row=2, column=5, padx=4, pady=4, sticky="we")

    def _pick_key(self) -> None:
        path = filedialog.askopenfilename(
            title="Select private SSH key",
            filetypes=[("PEM key", "*.pem"), ("All files", "*.*")],
        )
        if path:
            self.key_var.set(path)

    # ----- Profiles persistence -------------------------------------- #
    def _read_profiles(self) -> dict:
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _write_profiles(self, profiles: dict) -> None:
        try:
            with open(PROFILE_FILE, "w", encoding="utf-8") as fh:
                json.dump(profiles, fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Profile error", str(exc))

    def _load_profiles_into_combo(self) -> None:
        profiles = self._read_profiles()
        names = sorted(profiles.keys())
        self.profile_combo["values"] = names

    def _on_profile_selected(self, _event=None) -> None:
        name = self.profile_var.get()
        profiles = self._read_profiles()
        data = profiles.get(name)
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
        name = simpledialog.askstring(
            "Save Profile",
            "Profile name:",
            initialvalue=self.profile_var.get() or self.host_var.get(),
            parent=self.root,
        )
        if not name:
            return
        profiles = self._read_profiles()
        # Note: passphrase is intentionally NOT stored.
        profiles[name] = {
            "host": self.host_var.get().strip(),
            "port": self.port_var.get().strip() or HINT_PORT,
            "user": self.user_var.get().strip() or HINT_USERNAME,
            "key": self.key_var.get().strip(),
            "service": self.service_var.get().strip(),
            "log_path": self.logpath_var.get().strip(),
            "upload_target": self.upload_target_var.get().strip(),
            "manager": self.manager_var.get(),
        }
        self._write_profiles(profiles)
        self._load_profiles_into_combo()
        self.profile_var.set(name)
        messagebox.showinfo("Saved", f"Profile '{name}' saved.")

    def _delete_profile(self) -> None:
        name = self.profile_var.get()
        if not name:
            messagebox.showwarning("No profile", "Select a profile to delete.")
            return
        if not messagebox.askyesno("Confirm", f"Delete profile '{name}'?"):
            return
        profiles = self._read_profiles()
        profiles.pop(name, None)
        self._write_profiles(profiles)
        self._load_profiles_into_combo()
        self.profile_var.set("")

    # ================================================================== #
    #  Status bar
    # ================================================================== #
    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self.root, style="Panel.TFrame", padding=(10, 4))
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=(4, 8))

        self.status_dot = tk.Canvas(
            bar, width=14, height=14, highlightthickness=0, bg=THEMES["dark"]["panel"]
        )
        self._dot = self.status_dot.create_oval(2, 2, 12, 12, fill=THEMES["dark"]["err"])
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))

        self.status_var = tk.StringVar(value="Disconnected")
        ttk.Label(bar, textvariable=self.status_var, style="Panel.TLabel").pack(
            side=tk.LEFT
        )
        self.path_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.path_var, style="Panel.TLabel").pack(
            side=tk.RIGHT
        )

    def _set_status(self, connected: bool, message: str | None = None) -> None:
        self.connected = connected
        color = self.palette["ok"] if connected else self.palette["err"]
        self.status_dot.configure(bg=self.palette["panel"])
        self.status_dot.itemconfig(self._dot, fill=color)
        self.status_var.set(
            message or ("Connected" if connected else "Disconnected")
        )
        self.connect_btn.config(text="Disconnect" if connected else "Connect")
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
    #  TAB 1 - File manager & editor
    # ================================================================== #
    def _build_files_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(tab, text="  File Manager  ")

        left = ttk.Frame(tab, style="Panel.TFrame", padding=6)
        left.pack(side=tk.LEFT, fill=tk.Y)

        toolbar = ttk.Frame(left, style="Panel.TFrame")
        toolbar.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(toolbar, text="Refresh", command=self.refresh_listing).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="New File", command=self._new_file).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="New Folder", command=self._new_folder).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(toolbar, text="Delete", command=self._delete_selected).pack(
            side=tk.LEFT, padx=2
        )

        # Live filter / search.
        filt = ttk.Frame(left, style="Panel.TFrame")
        filt.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filt, text="Filter:", style="Panel.TLabel").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._apply_filter())
        ttk.Entry(filt, textvariable=self.filter_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=4
        )

        # Transfer buttons.
        ttk.Button(
            left, text="Upload Web App Folder...", command=self._upload_folder
        ).pack(fill=tk.X, pady=(0, 2))
        ttk.Button(
            left, text="Download Selected...", command=self._download_selected
        ).pack(fill=tk.X, pady=(0, 2))
        ttk.Label(left, text="Upload target dir:", style="Panel.TLabel").pack(
            anchor="w"
        )
        self.upload_target_var = tk.StringVar(value=HINT_UPLOAD_TARGET)
        ttk.Entry(left, textvariable=self.upload_target_var).pack(
            fill=tk.X, pady=(0, 4)
        )
        self.progress = ttk.Progressbar(left, mode="determinate")
        self.progress.pack(fill=tk.X, pady=(0, 6))

        list_frame = ttk.Frame(left, style="Panel.TFrame")
        list_frame.pack(fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self.file_list = tk.Listbox(
            list_frame,
            width=40,
            height=26,
            font=MONO_FONT,
            activestyle="none",
            yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.file_list.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_list.bind("<Double-Button-1>", self._on_list_double_click)
        self._themed_listboxes.append(self.file_list)

        # Right: editor.
        right = ttk.Frame(tab, padding=(6, 0))
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ed_toolbar = ttk.Frame(right)
        ed_toolbar.pack(fill=tk.X, pady=(0, 6))
        self.editor_label_var = tk.StringVar(value="No file open")
        ttk.Label(ed_toolbar, textvariable=self.editor_label_var).pack(side=tk.LEFT)
        hl = "on" if HAS_PYGMENTS else "off (pip install pygments)"
        ttk.Label(ed_toolbar, text=f"  | highlight: {hl}").pack(side=tk.LEFT)
        ttk.Button(
            ed_toolbar,
            text="Save to Server (Ctrl+S)",
            style="Accent.TButton",
            command=self._save_active_file,
        ).pack(side=tk.RIGHT, padx=2)

        editor_frame = ttk.Frame(right)
        editor_frame.pack(fill=tk.BOTH, expand=True)
        y_scroll = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL)
        x_scroll = ttk.Scrollbar(editor_frame, orient=tk.HORIZONTAL)
        self.editor = tk.Text(
            editor_frame,
            wrap=tk.NONE,
            font=MONO_FONT,
            undo=True,
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )
        y_scroll.config(command=self.editor.yview)
        x_scroll.config(command=self.editor.xview)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        x_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._themed_texts.append(self.editor)

        # Ctrl+S to save; debounced re-highlight on typing.
        self.editor.bind("<Control-s>", self._on_ctrl_s)
        self.editor.bind("<KeyRelease>", self._on_editor_keyrelease)
        self._highlight_job = None

    # ----- File listing / filter ------------------------------------- #
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
        dirs, files = [], []
        for attr in entries:
            name = attr.filename
            if stat.S_ISDIR(attr.st_mode):
                dirs.append(name + "/")
            else:
                files.append(name)
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        items = [".. (Parent Directory)"] + dirs + files

        def update():
            self._all_items = items
            self._apply_filter()
            self.path_var.set(self.current_path)

        self._ui(update)

    def _apply_filter(self) -> None:
        needle = self.filter_var.get().strip().lower()
        self.file_list.delete(0, tk.END)
        for item in self._all_items:
            if not needle or needle in item.lower() or item.startswith(".."):
                self.file_list.insert(tk.END, item)

    def _on_list_double_click(self, _event=None) -> None:
        if not self._require_connection():
            return
        sel = self.file_list.curselection()
        if not sel:
            return
        label = self.file_list.get(sel[0])
        if label.startswith(".."):
            self.current_path = os.path.dirname(self.current_path.rstrip("/")) or "/"
            self.refresh_listing()
            return
        if label.endswith("/"):
            self.current_path = self._join(self.current_path, label[:-1])
            self.filter_var.set("")
            self.refresh_listing()
        else:
            self._run_bg(self._task_open_file, self._join(self.current_path, label))

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
            self.editor_label_var.set(f"Editing: {remote_path}")
            self._highlight_editor()

        self._ui(update)

    def _on_ctrl_s(self, _event=None) -> str:
        self._save_active_file()
        return "break"

    def _save_active_file(self) -> None:
        if not self._require_connection():
            return
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
        self._ui(lambda: messagebox.showinfo("Saved", f"Saved to:\n{remote_path}"))

    # ----- Syntax highlighting --------------------------------------- #
    def _configure_highlight_tags(self) -> None:
        colors = TOKEN_COLORS_DARK if self.theme_name == "dark" else TOKEN_COLORS_LIGHT
        for tag, color in colors.items():
            self.editor.tag_configure(tag, foreground=color)

    def _on_editor_keyrelease(self, _event=None) -> None:
        if not HAS_PYGMENTS:
            return
        if self._highlight_job:
            self.root.after_cancel(self._highlight_job)
        self._highlight_job = self.root.after(400, self._highlight_editor)

    def _highlight_editor(self) -> None:
        if not HAS_PYGMENTS or not self.active_file:
            return
        for tag in (TOKEN_COLORS_DARK if self.theme_name == "dark" else TOKEN_COLORS_LIGHT):
            self.editor.tag_remove(tag, "1.0", tk.END)
        try:
            lexer = get_lexer_for_filename(self.active_file)
        except Exception:  # noqa: BLE001
            lexer = TextLexer()
        content = self.editor.get("1.0", "end-1c")
        self.editor.mark_set("range_start", "1.0")
        for tok_type, value in lex(content, lexer):
            tag = self._token_to_tag(tok_type)
            self.editor.mark_set(
                "range_end", f"range_start+{len(value)}c"
            )
            if tag:
                self.editor.tag_add(tag, "range_start", "range_end")
            self.editor.mark_set("range_start", "range_end")

    @staticmethod
    def _token_to_tag(tok_type) -> str | None:
        if tok_type in Token.Comment:
            return "comment"
        if tok_type in Token.Keyword:
            return "keyword"
        if tok_type in Token.String:
            return "string"
        if tok_type in Token.Number:
            return "number"
        if tok_type in Token.Name.Function:
            return "name_function"
        if tok_type in Token.Name.Class:
            return "name_class"
        if tok_type in Token.Operator:
            return "operator"
        return None

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
        sel = self.file_list.curselection()
        if not sel:
            messagebox.showwarning("Nothing selected", "Select an item first.")
            return
        label = self.file_list.get(sel[0])
        if label.startswith(".."):
            return
        is_dir = label.endswith("/")
        name = label[:-1] if is_dir else label
        remote_path = self._join(self.current_path, name)
        if not messagebox.askyesno("Confirm delete", f"Delete {remote_path}?"):
            return
        self._run_bg(self._task_delete, remote_path, is_dir)

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
        for attr in self.sftp_client.listdir_attr(remote_path):
            child = self._join(remote_path, attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                self._rmtree(child)
            else:
                self.sftp_client.remove(child)
        self.sftp_client.rmdir(remote_path)

    # ----- Upload (with progress) ------------------------------------ #
    def _upload_folder(self) -> None:
        if not self._require_connection():
            return
        local_dir = filedialog.askdirectory(title="Select Web App folder")
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
        all_files = []
        for root_dir, _dirs, files in os.walk(local_dir):
            for fname in files:
                all_files.append(os.path.join(root_dir, fname))
        total = len(all_files) or 1
        self._ui(lambda: self._progress_start(total))
        try:
            self._sftp_makedirs(remote_root)
            done = 0
            for root_dir, _dirs, files in os.walk(local_dir):
                rel = os.path.relpath(root_dir, local_dir)
                remote_dir = (
                    remote_root
                    if rel == "."
                    else self._join(remote_root, rel.replace(os.sep, "/"))
                )
                self._sftp_makedirs(remote_dir)
                for fname in files:
                    self.sftp_client.put(
                        os.path.join(root_dir, fname),
                        self._join(remote_dir, fname),
                    )
                    done += 1
                    self._ui(lambda d=done: self._progress_set(d))
        except Exception as exc:  # noqa: BLE001
            self._ui(self._progress_reset)
            self._error("Upload error", exc)
            return
        self._ui(self._progress_reset)
        self._ui(
            lambda: messagebox.showinfo(
                "Upload complete", f"Uploaded {len(all_files)} files to:\n{remote_root}"
            )
        )

    def _progress_start(self, total: int) -> None:
        self.progress.config(maximum=total, value=0)

    def _progress_set(self, value: int) -> None:
        self.progress.config(value=value)

    def _progress_reset(self) -> None:
        self.progress.config(value=0)

    def _sftp_makedirs(self, remote_dir: str) -> None:
        path = ""
        for part in remote_dir.strip("/").split("/"):
            path = path + "/" + part
            try:
                self.sftp_client.stat(path)
            except IOError:
                self.sftp_client.mkdir(path)

    # ----- Download -------------------------------------------------- #
    def _download_selected(self) -> None:
        if not self._require_connection():
            return
        sel = self.file_list.curselection()
        if not sel:
            messagebox.showwarning("Nothing selected", "Select a file or folder.")
            return
        label = self.file_list.get(sel[0])
        if label.startswith(".."):
            return
        is_dir = label.endswith("/")
        name = label[:-1] if is_dir else label
        remote_path = self._join(self.current_path, name)
        local_dir = filedialog.askdirectory(title="Select local destination folder")
        if not local_dir:
            return
        self._run_bg(self._task_download, remote_path, name, local_dir, is_dir)

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
        self._ui(
            lambda: messagebox.showinfo(
                "Download complete", f"Downloaded {count} file(s) to:\n{local_dir}"
            )
        )

    def _download_tree(self, remote_dir: str, local_dir: str) -> int:
        os.makedirs(local_dir, exist_ok=True)
        count = 0
        for attr in self.sftp_client.listdir_attr(remote_dir):
            child_remote = self._join(remote_dir, attr.filename)
            child_local = os.path.join(local_dir, attr.filename)
            if stat.S_ISDIR(attr.st_mode):
                count += self._download_tree(child_remote, child_local)
            else:
                self.sftp_client.get(child_remote, child_local)
                count += 1
        return count

    # ================================================================== #
    #  TAB 2 - Bot controller (systemd / pm2)
    # ================================================================== #
    def _build_controller_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Bot Controller  ")

        cfg = ttk.Frame(tab, style="Panel.TFrame", padding=8)
        cfg.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(cfg, text="Manager:", style="Panel.TLabel").grid(
            row=0, column=0, sticky="w", padx=4, pady=4
        )
        self.manager_var = tk.StringVar(value="systemd")
        ttk.Combobox(
            cfg,
            textvariable=self.manager_var,
            values=["systemd", "pm2"],
            width=10,
            state="readonly",
        ).grid(row=0, column=1, padx=4, pady=4, sticky="w")

        ttk.Label(cfg, text="Service name:", style="Panel.TLabel").grid(
            row=0, column=2, sticky="e", padx=4, pady=4
        )
        self.service_var = tk.StringVar(value=HINT_SERVICE)
        ttk.Entry(cfg, textvariable=self.service_var, width=18).grid(
            row=0, column=3, padx=4, pady=4
        )

        # Bot status indicator.
        ttk.Label(cfg, text="Bot:", style="Panel.TLabel").grid(
            row=0, column=4, sticky="e", padx=4, pady=4
        )
        self.bot_dot = tk.Canvas(cfg, width=14, height=14, highlightthickness=0)
        self._bot_dot = self.bot_dot.create_oval(2, 2, 12, 12, fill="#888888")
        self.bot_dot.grid(row=0, column=5, padx=2)
        self.bot_status_var = tk.StringVar(value="unknown")
        ttk.Label(cfg, textvariable=self.bot_status_var, style="Panel.TLabel").grid(
            row=0, column=6, sticky="w", padx=2
        )

        ttk.Label(cfg, text="Log path:", style="Panel.TLabel").grid(
            row=1, column=0, sticky="w", padx=4, pady=4
        )
        self.logpath_var = tk.StringVar(value=HINT_LOG_PATH)
        ttk.Entry(cfg, textvariable=self.logpath_var, width=46).grid(
            row=1, column=1, columnspan=3, padx=4, pady=4, sticky="we"
        )
        ttk.Label(cfg, text="Log lines:", style="Panel.TLabel").grid(
            row=1, column=4, sticky="e", padx=4, pady=4
        )
        self.loglines_var = tk.StringVar(value="50")
        ttk.Entry(cfg, textvariable=self.loglines_var, width=6).grid(
            row=1, column=5, columnspan=2, padx=4, pady=4, sticky="w"
        )

        # Action buttons.
        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=(0, 4))
        for text, action in (
            ("Start Bot", "start"),
            ("Stop Bot", "stop"),
            ("Restart Bot", "restart"),
            ("Bot Status", "status"),
        ):
            ttk.Button(
                btns, text=text, command=lambda a=action: self._service_action(a)
            ).pack(side=tk.LEFT, padx=3)
        ttk.Button(btns, text="Check Status", command=self._check_status).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(btns, text="Renew SSL", command=self._renew_ssl).pack(
            side=tk.LEFT, padx=3
        )

        btns2 = ttk.Frame(tab)
        btns2.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(btns2, text="Server Health", command=self._server_health).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(btns2, text="Test Nginx", command=self._nginx_test).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(btns2, text="Reload Nginx", command=self._nginx_reload).pack(
            side=tk.LEFT, padx=3
        )
        self.follow_btn = ttk.Button(
            btns2,
            text="Stream Logs (tail -f)",
            style="Accent.TButton",
            command=self._toggle_follow_logs,
        )
        self.follow_btn.pack(side=tk.RIGHT, padx=3)
        ttk.Button(btns2, text="Tail Once", command=self._stream_logs_once).pack(
            side=tk.RIGHT, padx=3
        )
        ttk.Button(btns2, text="Clear", command=self._clear_console).pack(
            side=tk.RIGHT, padx=3
        )

        self.console = tk.Text(tab, bg=CONSOLE_BG, fg=CONSOLE_FG, font=MONO_FONT, wrap=tk.WORD)
        console_scroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.console.yview)
        self.console.config(yscrollcommand=console_scroll.set, insertbackground=CONSOLE_FG)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.console.pack(fill=tk.BOTH, expand=True)

    def _console_write(self, text: str) -> None:
        self.console.insert(tk.END, text)
        self.console.see(tk.END)

    def _clear_console(self) -> None:
        self.console.delete("1.0", tk.END)

    # ----- Service command building ---------------------------------- #
    def _build_service_cmd(self, action: str) -> str:
        service = self.service_var.get().strip() or HINT_SERVICE
        if self.manager_var.get() == "pm2":
            if action == "status":
                return f"pm2 describe {service}"
            return f"pm2 {action} {service}"
        # systemd
        return f"sudo systemctl {action} {service}"

    def _service_action(self, action: str) -> None:
        if not self._require_connection():
            return
        cmd = self._build_service_cmd(action)
        self._ui(lambda: self._console_write(f"\n$ {cmd}\n"))
        self._run_bg(self._task_exec_then_status, cmd, action != "status")

    def _task_exec_then_status(self, cmd: str, then_check: bool) -> None:
        self._task_exec_to_console(cmd)
        if then_check:
            time.sleep(0.6)
            self._task_check_status()

    def _renew_ssl(self) -> None:
        if not self._require_connection():
            return
        self._exec_simple("sudo certbot renew")

    def _nginx_test(self) -> None:
        if not self._require_connection():
            return
        self._exec_simple("sudo nginx -t")

    def _nginx_reload(self) -> None:
        if not self._require_connection():
            return
        self._exec_simple("sudo systemctl reload nginx")

    def _server_health(self) -> None:
        if not self._require_connection():
            return
        cmd = (
            "echo '===== DISK ====='; df -h; "
            "echo '===== MEMORY ====='; free -m; "
            "echo '===== UPTIME / LOAD ====='; uptime; "
            "echo '===== TOP PROCESSES ====='; "
            "ps -eo pid,pcpu,pmem,comm --sort=-pcpu | head -n 12"
        )
        self._exec_simple(cmd)

    def _exec_simple(self, cmd: str) -> None:
        self._ui(lambda: self._console_write(f"\n$ {cmd}\n"))
        self._run_bg(self._task_exec_to_console, cmd)

    def _task_exec_to_console(self, cmd: str) -> None:
        try:
            _stdin, stdout, stderr = self.ssh_client.exec_command(cmd, timeout=90)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._error("Command error", exc)
            return
        result = (out or "") + (("\n[stderr]\n" + err) if err.strip() else "")
        self._ui(lambda: self._console_write(result + "\n"))

    # ----- Status check ---------------------------------------------- #
    def _check_status(self) -> None:
        if not self._require_connection():
            return
        self._run_bg(self._task_check_status)

    def _task_check_status(self) -> None:
        service = self.service_var.get().strip() or HINT_SERVICE
        if self.manager_var.get() == "pm2":
            cmd = (
                f"pm2 jlist | tr ',' '\\n' | grep -A1 '\"name\":\"{service}\"' "
                f"| grep status || pm2 describe {service} | grep -i status"
            )
            online_keyword = "online"
        else:
            cmd = f"systemctl is-active {service}"
            online_keyword = "active"
        try:
            _stdin, stdout, _stderr = self.ssh_client.exec_command(cmd, timeout=30)
            out = stdout.read().decode("utf-8", errors="replace").strip()
        except Exception as exc:  # noqa: BLE001
            self._error("Status error", exc)
            return
        is_online = online_keyword in out.lower()
        # 'inactive' contains 'active' as a substring; guard against it.
        if online_keyword == "active" and "inactive" in out.lower():
            is_online = False
        label = out.splitlines()[0] if out else "unknown"

        def update():
            color = self.palette["ok"] if is_online else self.palette["err"]
            self.bot_dot.itemconfig(self._bot_dot, fill=color)
            self.bot_status_var.set(label or ("online" if is_online else "offline"))

        self._ui(update)

    # ----- Log streaming --------------------------------------------- #
    def _resolve_log_lines(self) -> str:
        lines = self.loglines_var.get().strip()
        return lines if lines.isdigit() and int(lines) > 0 else "50"

    def _stream_logs_once(self) -> None:
        if not self._require_connection():
            return
        log_path = self.logpath_var.get().strip() or HINT_LOG_PATH
        self._exec_simple(f"tail -n {self._resolve_log_lines()} {log_path}")

    def _toggle_follow_logs(self) -> None:
        if self._log_following:
            self._stop_follow_logs()
        else:
            self._start_follow_logs()

    def _start_follow_logs(self) -> None:
        if not self._require_connection():
            return
        log_path = self.logpath_var.get().strip() or HINT_LOG_PATH
        self._log_stop.clear()
        self._log_following = True
        self.follow_btn.config(text="Stop Streaming")
        self._console_write(
            f"\n$ tail -f -n {self._resolve_log_lines()} {log_path}\n"
        )
        self._run_bg(self._task_follow_logs, log_path)

    def _stop_follow_logs(self) -> None:
        self._log_stop.set()
        if self._log_channel is not None:
            try:
                self._log_channel.close()
            except Exception:  # noqa: BLE001
                pass
        self._log_following = False
        self.follow_btn.config(text="Stream Logs (tail -f)")

    def _task_follow_logs(self, log_path: str) -> None:
        try:
            transport = self.ssh_client.get_transport()
            channel = transport.open_session()
            self._log_channel = channel
            channel.exec_command(f"tail -f -n {self._resolve_log_lines()} {log_path}")
            while not self._log_stop.is_set():
                if channel.recv_ready():
                    data = channel.recv(4096).decode("utf-8", errors="replace")
                    if data:
                        self._ui(lambda d=data: self._console_write(d))
                elif channel.exit_status_ready() and not channel.recv_ready():
                    break
                else:
                    time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001
            self._error("Log stream error", exc)
        finally:
            self._log_channel = None
            self._log_following = False
            self._ui(lambda: self.follow_btn.config(text="Stream Logs (tail -f)"))

    # ================================================================== #
    #  TAB 3 - .env editor
    # ================================================================== #
    def _build_env_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  .env Editor  ")

        toolbar = ttk.Frame(tab)
        toolbar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(toolbar, text=".env path:").pack(side=tk.LEFT, padx=(0, 4))
        self.env_path_var = tk.StringVar(value="")
        ttk.Entry(toolbar, textvariable=self.env_path_var, width=50).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(toolbar, text="Load .env", command=self._load_env).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            toolbar, text="Save .env", style="Accent.TButton", command=self._save_env
        ).pack(side=tk.LEFT, padx=4)

        ttk.Label(
            tab,
            text="Loads the .env from the current working directory by default. "
            "Edit secrets/tokens/URLs and save.",
        ).pack(fill=tk.X, pady=(0, 6))

        editor_frame = ttk.Frame(tab)
        editor_frame.pack(fill=tk.BOTH, expand=True)
        env_scroll = ttk.Scrollbar(editor_frame, orient=tk.VERTICAL)
        self.env_editor = tk.Text(
            editor_frame, font=MONO_FONT, wrap=tk.NONE, undo=True, yscrollcommand=env_scroll.set
        )
        env_scroll.config(command=self.env_editor.yview)
        env_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.env_editor.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._themed_texts.append(self.env_editor)

    def _load_env(self) -> None:
        if not self._require_connection():
            return
        path = self.env_path_var.get().strip()
        if not path:
            path = self._join(self.current_path, ".env")
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
    #  TAB 4 - Interactive terminal
    # ================================================================== #
    def _build_terminal_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="  Terminal  ")

        quick = ttk.Frame(tab)
        quick.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(quick, text="Quick:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(
            quick,
            text="pip install -r requirements.txt",
            command=lambda: self._quick_command("pip install -r requirements.txt"),
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            quick, text="ls -la", command=lambda: self._quick_command("ls -la")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            quick, text="df -h", command=lambda: self._quick_command("df -h")
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(quick, text="Clear", command=self._clear_terminal).pack(
            side=tk.RIGHT, padx=2
        )

        self.term_output = tk.Text(
            tab, bg=CONSOLE_BG, fg=CONSOLE_FG, font=MONO_FONT, wrap=tk.WORD
        )
        term_scroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=self.term_output.yview)
        self.term_output.config(yscrollcommand=term_scroll.set, insertbackground=CONSOLE_FG)
        term_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.term_output.pack(fill=tk.BOTH, expand=True)

        input_row = ttk.Frame(tab)
        input_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(input_row, text="$").pack(side=tk.LEFT, padx=(0, 4))
        self.term_input = ttk.Entry(input_row)
        self.term_input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.term_input.bind("<Return>", self._run_terminal_command)
        ttk.Button(input_row, text="Run", command=self._run_terminal_command).pack(
            side=tk.LEFT, padx=4
        )

    def _clear_terminal(self) -> None:
        self.term_output.delete("1.0", tk.END)

    def _term_write(self, text: str) -> None:
        self.term_output.insert(tk.END, text)
        self.term_output.see(tk.END)

    def _quick_command(self, command: str) -> None:
        if not self._require_connection():
            return
        self._exec_terminal(command)

    def _run_terminal_command(self, _event=None) -> None:
        if not self._require_connection():
            return
        command = self.term_input.get().strip()
        if not command:
            return
        self.term_input.delete(0, tk.END)
        self._exec_terminal(command)

    def _exec_terminal(self, command: str) -> None:
        self._term_write(f"\n{self.current_path}$ {command}\n")
        full_cmd = f"cd {self.current_path} && {command}"
        self._run_bg(self._task_terminal_exec, full_cmd)

    def _task_terminal_exec(self, full_cmd: str) -> None:
        try:
            _stdin, stdout, stderr = self.ssh_client.exec_command(full_cmd, timeout=180)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._error("Terminal error", exc)
            return

        def update():
            if out:
                self._term_write(out)
            if err.strip():
                self._term_write(err)

        self._ui(update)

    # ================================================================== #
    #  Connection management
    # ================================================================== #
    def _toggle_connection(self) -> None:
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        host = self.host_var.get().strip()
        port = self.port_var.get().strip() or HINT_PORT
        user = self.user_var.get().strip() or HINT_USERNAME
        key = self.key_var.get().strip()
        passphrase = self.passphrase_var.get() or None

        if not host:
            messagebox.showwarning("Missing host", "Enter the public IP / host.")
            return
        if not port.isdigit() or not (0 < int(port) < 65536):
            messagebox.showwarning("Invalid port", "Port must be 1-65535.")
            return
        if not key or not os.path.isfile(key):
            messagebox.showwarning("Missing key", "Select a valid .pem key file.")
            return

        self._set_status(False, "Connecting...")
        self.connect_btn.config(state=tk.DISABLED)
        self._run_bg(self._task_connect, host, int(port), user, key, passphrase)

    def _task_connect(self, host, port, user, key, passphrase) -> None:
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            pkey = self._load_private_key(key, passphrase)
            client.connect(
                hostname=host,
                port=port,
                username=user,
                pkey=pkey,
                timeout=20,
                banner_timeout=20,
                auth_timeout=20,
            )
            sftp = client.open_sftp()
            try:
                home = sftp.normalize(".")
            except Exception:  # noqa: BLE001
                home = f"/home/{user}"
        except Exception as exc:  # noqa: BLE001
            err_text = str(exc) or exc.__class__.__name__
            self._ui(lambda: self._connect_failed(err_text))
            return

        def finish():
            self.ssh_client = client
            self.sftp_client = sftp
            self.current_path = home
            self.connect_btn.config(state=tk.NORMAL)
            self._set_status(True, f"Connected to {user}@{host}:{port}")
            self.refresh_listing()

        self._ui(finish)

    def _connect_failed(self, message: str) -> None:
        self.connect_btn.config(state=tk.NORMAL)
        self._set_status(False, "Disconnected")
        messagebox.showerror("Connection failed", message)

    def _load_private_key(self, key_path: str, passphrase: str | None = None):
        last_exc = None
        for key_cls in (
            paramiko.RSAKey,
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.DSSKey,
        ):
            try:
                return key_cls.from_private_key_file(key_path, password=passphrase)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        raise last_exc if last_exc else RuntimeError("Unsupported key format")

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
        self._all_items = []
        self._set_status(False, "Disconnected")

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
        # Capture the message now; the except-name is gone by the time the
        # queued lambda runs on the UI thread.
        message = str(exc) or exc.__class__.__name__
        self._ui(lambda: messagebox.showerror(title, message))

    def _pump_ui_queue(self) -> None:
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
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
        self._disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AwsTelegramManager(root)
    root.mainloop()


if __name__ == "__main__":
    main()
