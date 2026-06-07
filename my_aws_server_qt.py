#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Server Manager - Qt edition (PySide6)
=========================================

A desktop GUI to manage an AWS server over SSH/SFTP, built on PySide6 (Qt).

Why Qt? Qt renders text through the operating system's native text engine
(DirectWrite on Windows, CoreText on macOS, FreeType on Linux). That means
**full-color emoji** and every Unicode script render natively and correctly,
which the immediate-mode GPU build (Dear PyGui / stb_truetype) cannot do.
Qt itself is GPU-composited by the OS window system.

This is a full-feature port: profiles, file manager (create / delete / rename /
chmod / copy-path / upload / download / filter), the .env editor, an
interactive terminal (history + quick commands), the code runner, and the
Tasks tab (cron scheduled tasks + systemd always-on tasks). The SSH/SFTP logic
(`SSHBackend`) is shared verbatim with the other builds.

Run:
    pip install PySide6 paramiko
    python my_aws_server_qt.py
"""

from __future__ import annotations

import os
import re
import sys
import json
import stat
import time
import shlex
import datetime

try:
    import paramiko
except ImportError:  # pragma: no cover
    raise SystemExit("Missing dependency 'paramiko'. Run: pip install paramiko")

try:
    from PySide6.QtCore import Qt, QObject, QThread, Signal, QTimer
    from PySide6.QtGui import QFont, QTextOption
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
        QListWidget, QPlainTextEdit, QProgressBar, QFileDialog, QInputDialog,
        QMessageBox, QSpinBox, QSplitter,
    )
except ImportError:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'PySide6'. This build renders the GUI through Qt\n"
        "(native OS text engine = real color emoji). Install it with:\n"
        "    pip install PySide6"
    )


# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
APP_TITLE = "AWS Server Manager - Qt (Matrix)"
HINT_PORT = "22"
HINT_USERNAME = "ubuntu"
HINT_UPLOAD_TARGET = "/var/www/html"
AOT_PREFIX = "atm-"  # systemd unit prefix for always-on tasks
CWD_SENTINEL = "__ATM_CWD__:"

_HOME = os.path.expanduser("~")
PROFILE_FILE = os.path.join(_HOME, ".aws_qt_manager_profiles.json")
SETTINGS_FILE = os.path.join(_HOME, ".aws_qt_manager_settings.json")

# Theme stylesheets (Qt Style Sheets). "matrix" is the default hacker look.
THEMES = {
    "matrix": {
        "bg": "#000800", "panel": "#001400", "field": "#001a00",
        "text": "#00ff41", "dim": "#00a01e", "accent": "#00ff41",
        "border": "#005a19", "sel": "#00ff41", "ok": "#39ff14", "err": "#ff3c3c",
        "editor_bg": "#000000", "editor_fg": "#00ff41",
    },
    "kali": {
        "bg": "#0c0c0c", "panel": "#1c1f24", "field": "#16191d",
        "text": "#c8c8c8", "dim": "#7aa0e6", "accent": "#367bf0",
        "border": "#2a2f37", "sel": "#367bf0", "ok": "#4caf50", "err": "#ff5252",
        "editor_bg": "#0f1115", "editor_fg": "#d6d6d6",
    },
    "dark": {
        "bg": "#1e1e2e", "panel": "#2a2a3c", "field": "#3a3a4f",
        "text": "#e6e6e6", "dim": "#aaaabb", "accent": "#5865f2",
        "border": "#44445a", "sel": "#5865f2", "ok": "#43b581", "err": "#f04747",
        "editor_bg": "#1b1b27", "editor_fg": "#f8f8f2",
    },
    "light": {
        "bg": "#f2f2f5", "panel": "#e3e3ea", "field": "#ffffff",
        "text": "#1e1e2e", "dim": "#5a5a6e", "accent": "#5865f2",
        "border": "#c8c8d2", "sel": "#5865f2", "ok": "#2e9e62", "err": "#cc3333",
        "editor_bg": "#ffffff", "editor_fg": "#1e1e2e",
    },
}
THEME_ORDER = ["matrix", "kali", "dark", "light"]


def _human_size(num: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if num < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}P"


def _join(base: str, name: str) -> str:
    return base + name if base.endswith("/") else base + "/" + name


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_json(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  SSH backend (GUI-agnostic; shared verbatim with the other builds)
# --------------------------------------------------------------------------- #
class SSHBackend:
    """Thread-safe wrapper around paramiko SSH + SFTP with sudo-aware ops."""

    def __init__(self) -> None:
        self.ssh: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None
        self.connected = False
        self.use_sudo = True

    # ----- connection ------------------------------------------------ #
    def connect(self, host, port, user, key_path, passphrase, use_agent) -> str:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = dict(hostname=host, port=port, username=user,
                      timeout=20, banner_timeout=20, auth_timeout=20)
        if use_agent:
            kwargs.update(allow_agent=True, look_for_keys=True)
        else:
            kwargs["pkey"] = self._load_private_key(key_path, passphrase)
        client.connect(**kwargs)
        sftp = client.open_sftp()
        try:
            home = sftp.normalize(".")
        except Exception:  # noqa: BLE001
            home = f"/home/{user}"
        self.ssh, self.sftp, self.connected = client, sftp, True
        return home

    @staticmethod
    def _load_private_key(key_path, passphrase):
        candidates = []
        for name in ("RSAKey", "Ed25519Key", "ECDSAKey", "DSSKey"):
            cls = getattr(paramiko, name, None)
            if cls is not None:
                candidates.append(cls)
        last = None
        for cls in candidates:
            try:
                return cls.from_private_key_file(key_path, password=passphrase)
            except paramiko.PasswordRequiredException:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise last if last else RuntimeError("Unsupported or unreadable key file")

    def disconnect(self) -> None:
        try:
            if self.sftp:
                self.sftp.close()
            if self.ssh:
                self.ssh.close()
        except Exception:  # noqa: BLE001
            pass
        self.sftp = self.ssh = None
        self.connected = False

    def is_alive(self) -> bool:
        if not (self.connected and self.ssh):
            return False
        tr = self.ssh.get_transport()
        return bool(tr and tr.is_active())

    # ----- shell helpers --------------------------------------------- #
    def run(self, cmd: str, timeout: int = 120):
        _i, out_s, err_s = self.ssh.exec_command(cmd, timeout=timeout)
        out = out_s.read().decode("utf-8", errors="replace")
        err = err_s.read().decode("utf-8", errors="replace")
        rc = out_s.channel.recv_exit_status()
        return rc, out, err

    def run_checked(self, cmd: str, timeout: int = 120) -> None:
        rc, _out, err = self.run(cmd, timeout)
        if rc != 0:
            raise IOError(err.strip() or f"Command failed (exit {rc}): {cmd}")

    @staticmethod
    def _mktemp(prefix: str = "atm") -> str:
        return f"/tmp/{prefix}_{int(time.time() * 1000)}_{os.getpid()}"

    def _sftp_put_text(self, path: str, text: str) -> None:
        with self.sftp.open(path, "w") as fh:
            try:
                fh.set_pipelined(True)
            except Exception:  # noqa: BLE001
                pass
            fh.write(text.encode("utf-8"))

    # ----- filesystem ------------------------------------------------ #
    def listdir(self, path: str) -> list[dict]:
        entries = self.sftp.listdir_attr(path)
        rows = [{"name": "..", "is_dir": True, "display": ".. (parent)"}]
        dirs, files = [], []
        for a in entries:
            is_dir = stat.S_ISDIR(a.st_mode)
            mtime = datetime.datetime.fromtimestamp(a.st_mtime or 0).strftime("%Y-%m-%d %H:%M")
            if is_dir:
                disp = f"[D] {a.filename:<26}    <dir>  {mtime}"
                dirs.append({"name": a.filename, "is_dir": True, "display": disp})
            else:
                disp = f"[F] {a.filename:<26} {_human_size(a.st_size or 0):>8}  {mtime}"
                files.append({"name": a.filename, "is_dir": False, "display": disp})
        dirs.sort(key=lambda e: e["name"].lower())
        files.sort(key=lambda e: e["name"].lower())
        return rows + dirs + files

    def read_file(self, path: str) -> str:
        with self.sftp.open(path, "r") as fh:
            return fh.read().decode("utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> None:
        if self.use_sudo:
            tmp = self._mktemp("atm_save")
            try:
                self._sftp_put_text(tmp, content)
                rc, _o, err = self.run(f"sudo -n cp -f {shlex.quote(tmp)} {shlex.quote(path)}")
            finally:
                self.run(f"rm -f {shlex.quote(tmp)}")
            if rc != 0:
                raise IOError(err.strip() or "sudo save failed (passwordless sudo required)")
        else:
            self._sftp_put_text(path, content)

    def new_file(self, path: str) -> None:
        if self.use_sudo:
            self.run_checked(f"sudo -n touch {shlex.quote(path)}")
        else:
            with self.sftp.open(path, "x") as fh:
                fh.write(b"")

    def new_folder(self, path: str) -> None:
        if self.use_sudo:
            self.run_checked(f"sudo -n mkdir -p {shlex.quote(path)}")
        else:
            self.sftp.mkdir(path)

    def delete(self, path: str, is_dir: bool) -> None:
        if self.use_sudo:
            self.run_checked(f"sudo -n rm -rf {shlex.quote(path)}")
        elif is_dir:
            self._rmtree(path)
        else:
            self.sftp.remove(path)

    def _rmtree(self, path: str) -> None:
        for a in self.sftp.listdir_attr(path):
            child = _join(path, a.filename)
            if stat.S_ISDIR(a.st_mode):
                self._rmtree(child)
            else:
                self.sftp.remove(child)
        self.sftp.rmdir(path)

    def rename(self, old_p: str, new_p: str) -> None:
        if self.use_sudo:
            self.run_checked(f"sudo -n mv {shlex.quote(old_p)} {shlex.quote(new_p)}")
        else:
            self.sftp.rename(old_p, new_p)

    def chmod(self, path: str, mode_int: int) -> None:
        if self.use_sudo:
            self.run_checked(f"sudo -n chmod {oct(mode_int)[2:]} {shlex.quote(path)}")
        else:
            self.sftp.chmod(path, mode_int)

    def _sftp_makedirs(self, remote_dir: str) -> None:
        path = ""
        for part in remote_dir.strip("/").split("/"):
            path += "/" + part
            try:
                self.sftp.stat(path)
            except IOError:
                self.sftp.mkdir(path)

    def upload_folder(self, local_dir: str, target: str, progress_cb=None) -> str:
        base = os.path.basename(local_dir.rstrip("/\\"))
        remote_root = _join(target, base)
        all_files = [os.path.join(r, f) for r, _d, fs in os.walk(local_dir) for f in fs]
        total = len(all_files) or 1
        use_sudo = self.use_sudo
        staging = self._mktemp("atm_upload") if use_sudo else None
        dest_root = _join(staging, base) if use_sudo else remote_root
        try:
            if use_sudo:
                self.sftp.mkdir(staging)
            self._sftp_makedirs(dest_root)
            done = 0
            for r, _d, files in os.walk(local_dir):
                rel = os.path.relpath(r, local_dir)
                rdir = dest_root if rel == "." else _join(dest_root, rel.replace(os.sep, "/"))
                self._sftp_makedirs(rdir)
                for fn in files:
                    self.sftp.put(os.path.join(r, fn), _join(rdir, fn))
                    done += 1
                    if progress_cb:
                        progress_cb(done / total)
            if use_sudo:
                self.run_checked(
                    f"sudo -n mkdir -p {shlex.quote(target)} && "
                    f"sudo -n cp -r {shlex.quote(dest_root)} {shlex.quote(target)}/")
                self.run(f"rm -rf {shlex.quote(staging)}")
        except Exception:
            if use_sudo:
                self.run(f"rm -rf {shlex.quote(staging)}")
            raise
        return remote_root

    def download(self, remote_path, name, local_dir, is_dir, progress_cb=None) -> int:
        if is_dir:
            return self._download_tree(remote_path, os.path.join(local_dir, name), progress_cb)
        self.sftp.get(remote_path, os.path.join(local_dir, name))
        if progress_cb:
            progress_cb(1.0)
        return 1

    def _download_tree(self, remote_dir, local_dir, progress_cb=None) -> int:
        os.makedirs(local_dir, exist_ok=True)
        count = 0
        for a in self.sftp.listdir_attr(remote_dir):
            cr = _join(remote_dir, a.filename)
            cl = os.path.join(local_dir, a.filename)
            if stat.S_ISDIR(a.st_mode):
                count += self._download_tree(cr, cl, progress_cb)
            else:
                self.sftp.get(cr, cl)
                count += 1
        return count

    # ----- terminal / runner ----------------------------------------- #
    def terminal_exec(self, command: str, cwd: str, timeout: int = 300):
        full = (f"cd {shlex.quote(cwd)} 2>/dev/null; {command}; "
                f"printf '\\n{CWD_SENTINEL}%s\\n' \"$(pwd)\"")
        _i, out_s, err_s = self.ssh.exec_command(full, timeout=timeout)
        out = out_s.read().decode("utf-8", errors="replace")
        err = err_s.read().decode("utf-8", errors="replace")
        new_cwd, kept = None, []
        for line in out.split("\n"):
            if line.startswith(CWD_SENTINEL):
                new_cwd = line[len(CWD_SENTINEL):].strip()
            else:
                kept.append(line)
        text = "\n".join(kept).rstrip("\n")
        if err.strip():
            text = (text + "\n" + err.rstrip("\n")) if text else err.rstrip("\n")
        return text, new_cwd

    def run_code(self, interp: str, code: str, cwd: str, timeout: int = 600):
        tmp = self._mktemp("atm_run")
        self._sftp_put_text(tmp, code)
        try:
            cmd = f"cd {shlex.quote(cwd)} && {interp} {shlex.quote(tmp)}"
            rc, out, err = self.run(cmd, timeout=timeout)
        finally:
            self.run(f"rm -f {shlex.quote(tmp)}")
        return rc, out, err

    # ----- cron ------------------------------------------------------- #
    def cron_list(self) -> list[str]:
        rc, out, _err = self.run("crontab -l 2>/dev/null")
        return [ln for ln in out.split("\n") if ln.strip()] if rc == 0 else []

    def cron_write(self, lines: list[str]) -> None:
        if not lines:
            self.run("crontab -r")
            return
        tmp = self._mktemp("atm_cron")
        try:
            self._sftp_put_text(tmp, "\n".join(lines) + "\n")
            self.run_checked(f"crontab {shlex.quote(tmp)}")
        finally:
            self.run(f"rm -f {shlex.quote(tmp)}")

    # ----- always-on (systemd) --------------------------------------- #
    def aot_list(self) -> list[tuple[str, str]]:
        rc, out, _err = self.run(
            f"systemctl list-unit-files --type=service --no-legend '{AOT_PREFIX}*.service' 2>/dev/null")
        result = []
        if rc == 0:
            for ln in out.split("\n"):
                parts = ln.split()
                if not parts:
                    continue
                unit = parts[0]
                state = parts[1] if len(parts) > 1 else ""
                if unit.startswith(AOT_PREFIX) and unit.endswith(".service"):
                    nm = unit[len(AOT_PREFIX):-len(".service")]
                    result.append((nm, state))
        return result

    def aot_create(self, name: str, cmd: str, user: str, cwd: str) -> str:
        if any(("\n" in v) or ("\r" in v) for v in (cmd, user, cwd, name)):
            raise ValueError("Name/command/path must be a single line.")
        unit = f"{AOT_PREFIX}{name}"
        path = f"/etc/systemd/system/{unit}.service"
        content = (
            "[Unit]\n"
            f"Description=ATM always-on task: {name}\n"
            "After=network.target\n\n"
            "[Service]\n"
            f"User={user}\n"
            f"WorkingDirectory={cwd}\n"
            f"ExecStart={cmd}\n"
            "Restart=always\n"
            "RestartSec=3\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
        tmp = self._mktemp("atm_unit")
        try:
            self._sftp_put_text(tmp, content)
            self.run_checked(f"sudo -n cp -f {shlex.quote(tmp)} {shlex.quote(path)}")
            self.run_checked("sudo -n systemctl daemon-reload")
            _rc, out, err = self.run(f"sudo -n systemctl enable --now {shlex.quote(unit)}")
        finally:
            self.run(f"rm -f {shlex.quote(tmp)}")
        return (out or "") + (err or "")

    def aot_action(self, name: str, action: str):
        unit = f"{AOT_PREFIX}{name}"
        return self.run(f"sudo -n systemctl {action} {shlex.quote(unit)}")

    def aot_logs(self, name: str):
        unit = f"{AOT_PREFIX}{name}"
        return self.run(f"sudo -n journalctl -u {shlex.quote(unit)} -n 50 --no-pager")

    def aot_delete(self, name: str):
        unit = f"{AOT_PREFIX}{name}"
        cmd = (f"sudo -n systemctl disable --now {shlex.quote(unit)}; "
               f"sudo -n rm -f /etc/systemd/system/{shlex.quote(unit)}.service; "
               "sudo -n systemctl daemon-reload")
        return self.run(cmd)


# --------------------------------------------------------------------------- #
#  Background worker (runs any callable off the UI thread)
# --------------------------------------------------------------------------- #
class Worker(QObject):
    """Runs a function on a QThread and emits the result back to the UI thread.

    Every blocking SSH/SFTP call goes through here, so the Qt UI never freezes.
    Qt signals are delivered on the receiver's (UI) thread, which keeps all
    widget updates safely on the main thread.
    """
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float)

    def __init__(self, fn, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._wants_progress = False

    def run(self) -> None:
        try:
            if self._wants_progress:
                self._kwargs["progress_cb"] = lambda f: self.progress.emit(float(f))
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit(result)


# --------------------------------------------------------------------------- #
#  Main window (Qt)
# --------------------------------------------------------------------------- #
class QtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.ssh = SSHBackend()
        self.current_path = "/home/" + HINT_USERNAME
        self.entries: list[dict] = []
        self.active_file: str | None = None
        self.cron_lines: list[str] = []
        self.aot_names: list[str] = []
        self._cmd_history: list[str] = []
        self._cmd_index = 0
        self._threads: list[tuple[QThread, Worker]] = []

        # Chunked loading: very large files are inserted into the editor a
        # block at a time via a timer, so the UI never freezes on open.
        self.CHUNK_CHARS = 200_000   # characters appended per timer tick
        self._load_chunks: list[str] = []
        self._load_timer = QTimer(self)
        self._load_timer.setInterval(0)  # fire as fast as the event loop allows
        self._load_timer.timeout.connect(self._load_next_chunk)

        self.settings = _read_json(SETTINGS_FILE)
        self.theme_name = self.settings.get("theme", "matrix")

        self.setWindowTitle(APP_TITLE)
        self.resize(1360, 900)
        self._build_ui()
        self._apply_theme(self.theme_name)
        self._refresh_profiles()

        # Connection watchdog.
        self._wd = QTimer(self)
        self._wd.timeout.connect(self._watchdog)
        self._wd.start(8000)

    # ================================================================= #
    #  Threading helper
    # ================================================================= #
    def _bg(self, fn, *args, on_done=None, on_fail=None, on_progress=None):
        thread = QThread()
        worker = Worker(fn, *args)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def cleanup():
            thread.quit()
            thread.wait()
            if (thread, worker) in self._threads:
                self._threads.remove((thread, worker))

        worker.done.connect(lambda res: (on_done(res) if on_done else None, cleanup()))
        worker.failed.connect(lambda msg: (
            (on_fail(msg) if on_fail else self._status(msg, ok=False)), cleanup()))
        if on_progress:
            worker._wants_progress = True
            worker.progress.connect(on_progress)
        self._threads.append((thread, worker))
        thread.start()
        return worker

    def _status(self, text: str, ok: bool = False) -> None:
        pal = THEMES[self.theme_name]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {pal['ok'] if ok else pal['err']};")

    def _require(self) -> bool:
        if not self.ssh.connected:
            self._status("Not connected", ok=False)
            return False
        return True

    # ================================================================= #
    #  UI construction
    # ================================================================= #
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        root.addWidget(self._build_connection_bar())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_files_tab(), "🗂  Files")
        self.tabs.addTab(self._build_env_tab(), "🔑  .env Editor")
        self.tabs.addTab(self._build_terminal_tab(), "⌨  Terminal")
        self.tabs.addTab(self._build_runner_tab(), "▶  Code Runner")
        self.tabs.addTab(self._build_tasks_tab(), "🗓  Tasks")
        root.addWidget(self.tabs, 1)

        self.status_label = QLabel("offline")
        root.addWidget(self.status_label)

    def _build_connection_bar(self) -> QWidget:
        box = QWidget()
        g = QGridLayout(box)
        g.setContentsMargins(6, 6, 6, 6)

        # Row 0: profile + theme
        g.addWidget(QLabel("Profile:"), 0, 0)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self.profile_combo.currentTextChanged.connect(self.on_profile_selected)
        g.addWidget(self.profile_combo, 0, 1)
        b_save = QPushButton("Save")
        b_save.clicked.connect(self.on_profile_save)
        g.addWidget(b_save, 0, 2)
        b_del = QPushButton("Delete")
        b_del.clicked.connect(self.on_profile_delete)
        g.addWidget(b_del, 0, 3)
        b_theme = QPushButton("🎨 Theme")
        b_theme.clicked.connect(self.on_toggle_theme)
        g.addWidget(b_theme, 0, 4)

        # Row 1: host / port / user
        g.addWidget(QLabel("Host:"), 1, 0)
        self.in_host = QLineEdit()
        self.in_host.setPlaceholderText("Public IP / host")
        g.addWidget(self.in_host, 1, 1)
        g.addWidget(QLabel("Port:"), 1, 2)
        self.in_port = QLineEdit(HINT_PORT)
        g.addWidget(self.in_port, 1, 3)
        g.addWidget(QLabel("User:"), 1, 4)
        self.in_user = QLineEdit(HINT_USERNAME)
        g.addWidget(self.in_user, 1, 5)

        # Row 2: key / passphrase / options / connect
        g.addWidget(QLabel("SSH key:"), 2, 0)
        self.in_key = QLineEdit()
        self.in_key.setPlaceholderText(".pem key path")
        g.addWidget(self.in_key, 2, 1)
        b_browse = QPushButton("Browse")
        b_browse.clicked.connect(self.on_pick_key)
        g.addWidget(b_browse, 2, 2)
        self.in_pass = QLineEdit()
        self.in_pass.setPlaceholderText("passphrase")
        self.in_pass.setEchoMode(QLineEdit.Password)
        g.addWidget(self.in_pass, 2, 3)
        self.chk_agent = QCheckBox("SSH agent")
        g.addWidget(self.chk_agent, 2, 4)
        self.chk_sudo = QCheckBox("sudo")
        self.chk_sudo.setChecked(True)
        g.addWidget(self.chk_sudo, 2, 5)
        self.conn_btn = QPushButton("Connect")
        self.conn_btn.clicked.connect(self.on_connect)
        g.addWidget(self.conn_btn, 2, 6)

        self.path_label = QLabel("")
        g.addWidget(self.path_label, 3, 0, 1, 7)
        return box

    def _mono(self, widget):
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        widget.setFont(f)
        return widget

    def _build_files_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        bar = QHBoxLayout()
        for label, slot in (("Refresh", self.refresh_listing), ("New File", self.on_new_file),
                            ("New Folder", self.on_new_folder), ("Delete", self.on_delete),
                            ("Rename", self.on_rename), ("chmod", self.on_chmod),
                            ("Copy path", self.on_copy_path)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)

        bar2 = QHBoxLayout()
        bar2.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.textChanged.connect(self._render_file_list)
        bar2.addWidget(self.filter_edit)
        b_up = QPushButton("Upload folder")
        b_up.clicked.connect(self.on_upload)
        bar2.addWidget(b_up)
        b_dl = QPushButton("Download")
        b_dl.clicked.connect(self.on_download)
        bar2.addWidget(b_dl)
        bar2.addWidget(QLabel("Target:"))
        self.upload_target = QLineEdit(HINT_UPLOAD_TARGET)
        bar2.addWidget(self.upload_target)
        v.addLayout(bar2)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        v.addWidget(self.progress)

        split = QSplitter(Qt.Horizontal)
        self.file_list = QListWidget()
        self._mono(self.file_list)
        self.file_list.itemDoubleClicked.connect(lambda _i: self.on_open())
        split.addWidget(self.file_list)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        topr = QHBoxLayout()
        self.editor_label = QLabel("No file open")
        topr.addWidget(self.editor_label, 1)
        b_open = QPushButton("Open")
        b_open.clicked.connect(self.on_open)
        topr.addWidget(b_open)
        b_savef = QPushButton("💾 Save")
        b_savef.clicked.connect(self.on_save)
        topr.addWidget(b_savef)
        rv.addLayout(topr)
        self.editor = QPlainTextEdit()
        self._mono(self.editor)
        self.editor.setWordWrapMode(QTextOption.NoWrap)
        rv.addWidget(self.editor, 1)
        split.addWidget(right)
        split.setSizes([430, 900])
        v.addWidget(split, 1)
        return w

    def _build_env_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(QLabel(".env path:"))
        self.env_path = QLineEdit()
        self.env_path.setPlaceholderText("/path/to/.env")
        bar.addWidget(self.env_path, 1)
        b_load = QPushButton("Load")
        b_load.clicked.connect(self.on_env_load)
        bar.addWidget(b_load)
        b_save = QPushButton("💾 Save")
        b_save.clicked.connect(self.on_env_save)
        bar.addWidget(b_save)
        v.addLayout(bar)
        self.env_editor = QPlainTextEdit()
        self._mono(self.env_editor)
        v.addWidget(self.env_editor, 1)
        return w

    def _build_terminal_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Quick:"))
        for cmd in ("pip install -r requirements.txt", "ls -la", "df -h"):
            b = QPushButton(cmd)
            b.clicked.connect(lambda _c=False, c=cmd: self.on_quick(c))
            bar.addWidget(b)
        bar.addStretch(1)
        b_clear = QPushButton("Clear")
        b_clear.clicked.connect(lambda: self.term_output.clear())
        bar.addWidget(b_clear)
        v.addLayout(bar)

        self.term_output = QPlainTextEdit()
        self.term_output.setReadOnly(True)
        self._mono(self.term_output)
        v.addWidget(self.term_output, 1)

        row = QHBoxLayout()
        self.term_input = QLineEdit()
        self.term_input.setPlaceholderText("command  (Enter to run, Up/Down for history)")
        self.term_input.returnPressed.connect(self.on_term_run)
        self.term_input.installEventFilter(self)
        self._mono(self.term_input)
        row.addWidget(self.term_input, 1)
        b_run = QPushButton("Run")
        b_run.clicked.connect(self.on_term_run)
        row.addWidget(b_run)
        v.addLayout(row)
        return w

    def _build_runner_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Interpreter:"))
        self.interp = QComboBox()
        self.interp.addItems(["python3", "python", "node", "bash"])
        bar.addWidget(self.interp)
        b_run = QPushButton("▶ Run Code")
        b_run.clicked.connect(self.on_run_code)
        bar.addWidget(b_run)
        b_load = QPushButton("Load open file")
        b_load.clicked.connect(self.on_load_open)
        bar.addWidget(b_load)
        bar.addStretch(1)
        b_clear = QPushButton("Clear")
        b_clear.clicked.connect(lambda: self.runner_output.clear())
        bar.addWidget(b_clear)
        v.addLayout(bar)

        split = QSplitter(Qt.Vertical)
        self.code_editor = QPlainTextEdit()
        self._mono(self.code_editor)
        self.code_editor.setPlainText("# Write code here and click Run Code\nprint('Hello from the server!')")
        split.addWidget(self.code_editor)
        self.runner_output = QPlainTextEdit()
        self.runner_output.setReadOnly(True)
        self._mono(self.runner_output)
        split.addWidget(self.runner_output)
        split.setSizes([400, 250])
        v.addWidget(split, 1)
        return w

    def _build_tasks_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        sub = QTabWidget()
        sub.addTab(self._build_cron_tab(), "Scheduled (cron)")
        sub.addTab(self._build_aot_tab(), "Always-on (systemd)")
        v.addWidget(sub)
        return w

    def _build_cron_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Run a command at a set time (server crontab)."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Command:"))
        self.cron_cmd = QLineEdit()
        row.addWidget(self.cron_cmd, 1)
        v.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Frequency:"))
        self.cron_freq = QComboBox()
        self.cron_freq.addItems(["Daily", "Hourly"])
        row2.addWidget(self.cron_freq)
        row2.addWidget(QLabel("Hour (UTC):"))
        self.cron_hour = QSpinBox()
        self.cron_hour.setRange(0, 23)
        row2.addWidget(self.cron_hour)
        row2.addWidget(QLabel("Minute:"))
        self.cron_min = QSpinBox()
        self.cron_min.setRange(0, 59)
        row2.addWidget(self.cron_min)
        b_create = QPushButton("Create")
        b_create.clicked.connect(self.on_cron_create)
        row2.addWidget(b_create)
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self.cron_refresh)
        row2.addWidget(b_refresh)
        b_delete = QPushButton("Delete selected")
        b_delete.clicked.connect(self.on_cron_delete)
        row2.addWidget(b_delete)
        row2.addStretch(1)
        v.addLayout(row2)
        self.cron_list = QListWidget()
        self._mono(self.cron_list)
        v.addWidget(self.cron_list, 1)
        return w

    def _build_aot_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("Keep a command running 24/7; auto-restarts (needs sudo)."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Name:"))
        self.aot_name = QLineEdit()
        row.addWidget(self.aot_name)
        row.addWidget(QLabel("Command:"))
        self.aot_cmd = QLineEdit()
        row.addWidget(self.aot_cmd, 1)
        b_create = QPushButton("Create & Start")
        b_create.clicked.connect(self.on_aot_create)
        row.addWidget(b_create)
        v.addLayout(row)
        row2 = QHBoxLayout()
        b_refresh = QPushButton("Refresh")
        b_refresh.clicked.connect(self.aot_refresh)
        row2.addWidget(b_refresh)
        for label, act in (("Start", "start"), ("Stop", "stop"),
                           ("Restart", "restart"), ("Status", "status")):
            b = QPushButton(label)
            b.clicked.connect(lambda _c=False, a=act: self.on_aot_action(a))
            row2.addWidget(b)
        b_logs = QPushButton("Logs")
        b_logs.clicked.connect(self.on_aot_logs)
        row2.addWidget(b_logs)
        b_delete = QPushButton("Delete")
        b_delete.clicked.connect(self.on_aot_delete)
        row2.addWidget(b_delete)
        row2.addStretch(1)
        v.addLayout(row2)
        self.aot_list = QListWidget()
        self._mono(self.aot_list)
        v.addWidget(self.aot_list)
        self.tasks_console = QPlainTextEdit()
        self.tasks_console.setReadOnly(True)
        self._mono(self.tasks_console)
        v.addWidget(self.tasks_console, 1)
        return w

    # Up/Down history in the terminal input.
    def eventFilter(self, obj, event):
        if obj is getattr(self, "term_input", None) and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key_Up:
                self._history(-1)
                return True
            if event.key() == Qt.Key_Down:
                self._history(1)
                return True
        return super().eventFilter(obj, event)

    # ================================================================= #
    #  Theme
    # ================================================================= #
    def _apply_theme(self, name: str) -> None:
        self.theme_name = name
        p = THEMES[name]
        self.setStyleSheet(f"""
            QWidget {{ background-color: {p['bg']}; color: {p['text']};
                       font-family: 'Segoe UI', sans-serif; font-size: 13px; }}
            QLineEdit, QComboBox, QSpinBox, QListWidget {{
                background-color: {p['field']}; color: {p['text']};
                border: 1px solid {p['border']}; border-radius: 3px; padding: 3px; }}
            QPlainTextEdit {{
                background-color: {p['editor_bg']}; color: {p['editor_fg']};
                border: 1px solid {p['border']}; border-radius: 3px; }}
            QPushButton {{ background-color: {p['panel']}; color: {p['text']};
                border: 1px solid {p['border']}; border-radius: 3px; padding: 5px 10px; }}
            QPushButton:hover {{ background-color: {p['accent']}; color: #ffffff; }}
            QTabBar::tab {{ background: {p['panel']}; color: {p['text']};
                padding: 7px 14px; border: 1px solid {p['border']}; }}
            QTabBar::tab:selected {{ background: {p['accent']}; color: #ffffff; }}
            QListWidget::item:selected {{ background: {p['accent']}; color: #ffffff; }}
            QProgressBar {{ border: 1px solid {p['border']}; border-radius: 3px;
                text-align: center; background: {p['field']}; }}
            QProgressBar::chunk {{ background-color: {p['accent']}; }}
            QLabel {{ background: transparent; }}
        """)
        self.settings["theme"] = name
        _write_json(SETTINGS_FILE, self.settings)

    def on_toggle_theme(self) -> None:
        idx = THEME_ORDER.index(self.theme_name) if self.theme_name in THEME_ORDER else 0
        self._apply_theme(THEME_ORDER[(idx + 1) % len(THEME_ORDER)])

    # ================================================================= #
    #  Profiles
    # ================================================================= #
    def _refresh_profiles(self) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItems(sorted(_read_json(PROFILE_FILE).keys()))
        self.profile_combo.blockSignals(False)

    def on_profile_selected(self, name: str) -> None:
        data = _read_json(PROFILE_FILE).get(name)
        if not data:
            return
        self.in_host.setText(data.get("host", ""))
        self.in_port.setText(data.get("port", HINT_PORT))
        self.in_user.setText(data.get("user", HINT_USERNAME))
        self.in_key.setText(data.get("key", ""))
        self.upload_target.setText(data.get("upload_target", HINT_UPLOAD_TARGET))

    def on_profile_save(self) -> None:
        default = self.profile_combo.currentText() or self.in_host.text()
        name, ok = QInputDialog.getText(self, "Save Profile", "Profile name:", text=default)
        if not ok or not name.strip():
            return
        name = name.strip()
        profiles = _read_json(PROFILE_FILE)
        profiles[name] = {
            "host": self.in_host.text().strip(),
            "port": self.in_port.text().strip() or HINT_PORT,
            "user": self.in_user.text().strip() or HINT_USERNAME,
            "key": self.in_key.text().strip(),
            "upload_target": self.upload_target.text().strip(),
        }
        _write_json(PROFILE_FILE, profiles)
        self._refresh_profiles()
        self.profile_combo.setCurrentText(name)
        self._status(f"Profile '{name}' saved", ok=True)

    def on_profile_delete(self) -> None:
        name = self.profile_combo.currentText()
        if not name:
            return
        if QMessageBox.question(self, "Confirm", f"Delete profile '{name}'?") != QMessageBox.Yes:
            return
        profiles = _read_json(PROFILE_FILE)
        profiles.pop(name, None)
        _write_json(PROFILE_FILE, profiles)
        self._refresh_profiles()

    def on_pick_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select private SSH key", "",
                                              "PEM key (*.pem);;All files (*)")
        if path:
            self.in_key.setText(path)

    # ================================================================= #
    #  Connection
    # ================================================================= #
    def on_connect(self) -> None:
        if self.ssh.connected:
            self.ssh.disconnect()
            self._status("offline", ok=False)
            self.conn_btn.setText("Connect")
            self.file_list.clear()
            return
        host = self.in_host.text().strip()
        port = self.in_port.text().strip() or HINT_PORT
        user = self.in_user.text().strip() or HINT_USERNAME
        key = self.in_key.text().strip()
        passphrase = self.in_pass.text() or None
        use_agent = self.chk_agent.isChecked()
        if not host:
            self._status("Enter host / public IP", ok=False)
            return
        if not port.isdigit() or not (0 < int(port) < 65536):
            self._status("Invalid port (1-65535)", ok=False)
            return
        if not use_agent and (not key or not os.path.isfile(key)):
            self._status("Select a valid .pem key or enable SSH agent", ok=False)
            return
        self._status("connecting...", ok=False)
        self._bg(self.ssh.connect, host, int(port), user, key, passphrase, use_agent,
                 on_done=self._connected, on_fail=lambda m: self._status(f"Connection failed: {m}", ok=False))

    def _connected(self, home: str) -> None:
        self.current_path = home
        self._status(f"{self.in_user.text().strip()}@{self.in_host.text().strip()}", ok=True)
        self.conn_btn.setText("Disconnect")
        self.in_pass.clear()
        self.refresh_listing()
        self.cron_refresh()
        self.aot_refresh()

    def _watchdog(self) -> None:
        if self.ssh.connected and not self.ssh.is_alive():
            self.ssh.connected = False
            self._status("Connection lost", ok=False)
            self.conn_btn.setText("Connect")

    def _sync_sudo(self) -> None:
        self.ssh.use_sudo = self.chk_sudo.isChecked()

    # ================================================================= #
    #  Files
    # ================================================================= #
    def refresh_listing(self) -> None:
        if not self._require():
            return
        self._bg(self.ssh.listdir, self.current_path, on_done=self._got_listing)

    def _got_listing(self, rows: list[dict]) -> None:
        self.entries = rows
        self._render_file_list()
        self.path_label.setText("Path: " + self.current_path)

    def _render_file_list(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        self.file_list.clear()
        for e in self.entries:
            if e["name"] == ".." or not needle or needle in e["name"].lower():
                self.file_list.addItem(e["display"])

    def _selected_entry(self) -> dict | None:
        item = self.file_list.currentItem()
        if not item:
            return None
        for e in self.entries:
            if e["display"] == item.text():
                return e
        return None

    def on_open(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e:
            return
        if e["name"] == "..":
            self.current_path = os.path.dirname(self.current_path.rstrip("/")) or "/"
            self.refresh_listing()
        elif e["is_dir"]:
            self.current_path = _join(self.current_path, e["name"])
            self.refresh_listing()
        else:
            path = _join(self.current_path, e["name"])
            self._bg(self.ssh.read_file, path, on_done=lambda text: self._opened(path, text))

    def _opened(self, path: str, text: str) -> None:
        self._load_timer.stop()          # cancel any in-progress chunked load
        self._load_chunks = []
        self.active_file = path
        self.editor_label.setText(f"Editing: {path}")
        # Small files: set in one go. Large files: stream into the editor in
        # chunks via a timer so the UI thread never blocks on a huge insert.
        if len(text) <= self.CHUNK_CHARS:
            self.editor.setPlainText(text)
            self._status(f"Opened: {path}", ok=True)
            return
        self._load_timer.stop()
        self.editor.setReadOnly(True)
        self.editor.clear()
        # Split into chunks on line boundaries where possible.
        self._load_chunks = self._chunk_text(text, self.CHUNK_CHARS)
        self._load_total = len(self._load_chunks)
        self.editor_label.setText(f"Loading {path} ...")
        self._status(f"Loading large file ({len(text):,} chars)...", ok=True)
        self._load_timer.start()

    @staticmethod
    def _chunk_text(text: str, size: int) -> list[str]:
        chunks, n, i = [], len(text), 0
        while i < n:
            end = min(n, i + size)
            # Prefer to break at a newline so lines aren't split mid-way.
            nl = text.rfind("\n", i, end)
            if nl > i and end < n:
                end = nl + 1
            chunks.append(text[i:end])
            i = end
        return chunks

    def _load_next_chunk(self) -> None:
        if not self._load_chunks:
            self._load_timer.stop()
            self.editor.setReadOnly(False)
            self.editor.moveCursor(self.editor.textCursor().Start)
            self.editor.ensureCursorVisible()
            self.editor_label.setText(f"Editing: {self.active_file}")
            self._status(f"Opened: {self.active_file}", ok=True)
            return
        chunk = self._load_chunks.pop(0)
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(chunk)
        done = self._load_total - len(self._load_chunks)
        self.editor_label.setText(
            f"Loading {self.active_file} ...  ({done}/{self._load_total})")

    def on_save(self) -> None:
        if not self._require():
            return
        path = self.active_file
        if not path:
            e = self._selected_entry()
            if e and not e["is_dir"] and e["name"] != "..":
                path = _join(self.current_path, e["name"])
                self.active_file = path
        if not path:
            self._status("Open or select a file before saving", ok=False)
            return
        self._sync_sudo()
        content = self.editor.toPlainText()
        self._bg(self.ssh.write_file, path, content,
                 on_done=lambda _r: self._status(f"Saved: {path}", ok=True))

    def on_new_file(self) -> None:
        if not self._require():
            return
        name, ok = QInputDialog.getText(self, "New File", "File name:")
        if ok and name.strip():
            self._sync_sudo()
            self._bg(self.ssh.new_file, _join(self.current_path, name.strip()),
                     on_done=lambda _r: self.refresh_listing())

    def on_new_folder(self) -> None:
        if not self._require():
            return
        name, ok = QInputDialog.getText(self, "New Folder", "Folder name:")
        if ok and name.strip():
            self._sync_sudo()
            self._bg(self.ssh.new_folder, _join(self.current_path, name.strip()),
                     on_done=lambda _r: self.refresh_listing())

    def on_delete(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        path = _join(self.current_path, e["name"])
        if QMessageBox.question(self, "Confirm delete", f"Delete {path}?") != QMessageBox.Yes:
            return
        self._sync_sudo()
        self._bg(self.ssh.delete, path, e["is_dir"], on_done=lambda _r: self.refresh_listing())

    def on_rename(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=e["name"])
        if not ok or not new.strip() or new == e["name"]:
            return
        self._sync_sudo()
        self._bg(self.ssh.rename, _join(self.current_path, e["name"]),
                 _join(self.current_path, new.strip()), on_done=lambda _r: self.refresh_listing())

    def on_chmod(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        mode, ok = QInputDialog.getText(self, "chmod", "Octal mode (e.g. 644):", text="644")
        if not ok or not mode.strip():
            return
        try:
            mode_int = int(mode.strip(), 8)
        except ValueError:
            self._status("Invalid octal mode", ok=False)
            return
        self._sync_sudo()
        self._bg(self.ssh.chmod, _join(self.current_path, e["name"]), mode_int,
                 on_done=lambda _r: self.refresh_listing())

    def on_copy_path(self) -> None:
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        path = _join(self.current_path, e["name"])
        QApplication.clipboard().setText(path)
        self._status(f"Copied: {path}", ok=True)

    def on_upload(self) -> None:
        if not self._require():
            return
        local = QFileDialog.getExistingDirectory(self, "Select folder to upload")
        if not local:
            return
        target = self.upload_target.text().strip()
        if not target:
            self._status("Enter the remote target dir", ok=False)
            return
        self._sync_sudo()
        self.progress.setValue(0)
        self._bg(self.ssh.upload_folder, local, target,
                 on_done=self._upload_done,
                 on_progress=lambda f: self.progress.setValue(int(f * 100)))

    def _upload_done(self, remote_root: str) -> None:
        self.progress.setValue(0)
        self._status(f"Uploaded to: {remote_root}", ok=True)
        self.refresh_listing()

    def on_download(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            self._status("Select a file/folder to download", ok=False)
            return
        local = QFileDialog.getExistingDirectory(self, "Select local destination")
        if not local:
            return
        self.progress.setValue(0)
        self._bg(self.ssh.download, _join(self.current_path, e["name"]), e["name"], local, e["is_dir"],
                 on_done=lambda count: (self.progress.setValue(0),
                                        self._status(f"Downloaded {count} file(s) to: {local}", ok=True)),
                 on_progress=lambda f: self.progress.setValue(int(f * 100)))

    # ================================================================= #
    #  .env editor
    # ================================================================= #
    def on_env_load(self) -> None:
        if not self._require():
            return
        path = self.env_path.text().strip() or _join(self.current_path, ".env")
        self.env_path.setText(path)
        self._bg(self.ssh.read_file, path, on_done=lambda text: self.env_editor.setPlainText(text))

    def on_env_save(self) -> None:
        if not self._require():
            return
        path = self.env_path.text().strip()
        if not path:
            self._status("Provide the .env path first", ok=False)
            return
        self._sync_sudo()
        self._bg(self.ssh.write_file, path, self.env_editor.toPlainText(),
                 on_done=lambda _r: self._status(f"Saved: {path}", ok=True))

    # ================================================================= #
    #  Terminal
    # ================================================================= #
    def _term_log(self, text: str) -> None:
        self.term_output.appendPlainText(text.rstrip("\n"))

    def on_term_run(self) -> None:
        if not self.ssh.connected:
            self._term_log("[not connected]")
            return
        cmd = self.term_input.text().strip()
        if not cmd:
            return
        self.term_input.clear()
        if cmd in ("clear", "cls"):
            self.term_output.clear()
            return
        self._cmd_history.append(cmd)
        self._cmd_index = len(self._cmd_history)
        self._term_log(f"{self.current_path}$ {cmd}")
        self._bg(self.ssh.terminal_exec, cmd, self.current_path, on_done=self._term_done)

    def _term_done(self, result) -> None:
        out, new_cwd = result
        if out:
            self._term_log(out)
        if new_cwd:
            self.current_path = new_cwd
            self.path_label.setText("Path: " + new_cwd)

    def _history(self, direction: int) -> None:
        if not self._cmd_history:
            return
        self._cmd_index = max(0, min(len(self._cmd_history), self._cmd_index + direction))
        val = self._cmd_history[self._cmd_index] if self._cmd_index < len(self._cmd_history) else ""
        self.term_input.setText(val)

    def on_quick(self, cmd: str) -> None:
        self.term_input.setText(cmd)
        self.on_term_run()

    # ================================================================= #
    #  Code runner
    # ================================================================= #
    def on_run_code(self) -> None:
        if not self._require():
            return
        interp = self.interp.currentText()
        code = self.code_editor.toPlainText()
        if not code.strip():
            return
        self.runner_output.setPlainText(f"$ {interp}  (cwd: {self.current_path})\n[running...]")
        self._bg(self.ssh.run_code, interp, code, self.current_path,
                 on_done=lambda res: self._run_done(interp, res))

    def _run_done(self, interp: str, res) -> None:
        rc, out, err = res
        text = f"$ {interp}  (cwd: {self.current_path})\n"
        if out:
            text += out
        if err.strip():
            text += "\n[stderr]\n" + err
        text += f"\n--- done (exit {rc}) ---"
        self.runner_output.setPlainText(text)

    def on_load_open(self) -> None:
        if not self.active_file:
            self._status("Open a file in Files first", ok=False)
            return
        self.code_editor.setPlainText(self.editor.toPlainText())

    # ================================================================= #
    #  Tasks - cron
    # ================================================================= #
    def cron_refresh(self) -> None:
        if not self.ssh.connected:
            return
        self._bg(self.ssh.cron_list, on_done=self._cron_loaded)

    def _cron_loaded(self, lines: list[str]) -> None:
        self.cron_lines = lines
        self.cron_list.clear()
        self.cron_list.addItems(lines or ["(no scheduled tasks)"])

    def on_cron_create(self) -> None:
        if not self._require():
            return
        cmd = self.cron_cmd.text().strip()
        if not cmd:
            self._status("Enter a command to schedule", ok=False)
            return
        minute = self.cron_min.value()
        hour = self.cron_hour.value()
        schedule = f"{minute} {hour} * * *" if self.cron_freq.currentText() == "Daily" else f"{minute} * * * *"
        line = f"{schedule} {cmd}"
        self._bg(self._cron_add, line, on_done=lambda _r: (self._status(f"Scheduled: {line}", ok=True),
                                                           self.cron_refresh()))

    def _cron_add(self, line: str):
        lines = self.ssh.cron_list()
        lines.append(line)
        self.ssh.cron_write(lines)

    def on_cron_delete(self) -> None:
        if not self._require():
            return
        item = self.cron_list.currentItem()
        if not item or item.text() not in self.cron_lines:
            return
        target = item.text()
        if QMessageBox.question(self, "Confirm", f"Delete scheduled task?\n{target}") != QMessageBox.Yes:
            return
        self._bg(self._cron_remove, target, on_done=lambda _r: self.cron_refresh())

    def _cron_remove(self, target: str):
        lines = [ln for ln in self.ssh.cron_list() if ln != target]
        self.ssh.cron_write(lines)

    # ================================================================= #
    #  Tasks - always-on (systemd)
    # ================================================================= #
    def _tasks_log(self, text: str) -> None:
        self.tasks_console.appendPlainText(text.rstrip("\n"))

    def aot_refresh(self) -> None:
        if not self.ssh.connected:
            return
        self._bg(self.ssh.aot_list, on_done=self._aot_loaded)

    def _aot_loaded(self, pairs) -> None:
        self.aot_names = [nm for nm, _s in pairs]
        self.aot_list.clear()
        self.aot_list.addItems([f"{nm}  [{st}]" for nm, st in pairs] or ["(no always-on tasks)"])

    def on_aot_create(self) -> None:
        if not self._require():
            return
        name = re.sub(r"[^a-zA-Z0-9_-]", "-", self.aot_name.text()).strip("-")
        cmd = self.aot_cmd.text().strip()
        if not name:
            self._status("Enter a task name", ok=False)
            return
        if not cmd:
            self._status("Enter a command to run", ok=False)
            return
        user = self.in_user.text().strip() or HINT_USERNAME
        self._tasks_log(f"$ create {AOT_PREFIX}{name}")
        self._bg(self.ssh.aot_create, name, cmd, user, self.current_path,
                 on_done=lambda out: (self._tasks_log(out + f"\nStarted {AOT_PREFIX}{name}"),
                                      self.aot_refresh()))

    def _selected_aot(self) -> str | None:
        item = self.aot_list.currentItem()
        if not item:
            return None
        nm = item.text().split("  [")[0]
        return nm if nm in self.aot_names else None

    def on_aot_action(self, action: str) -> None:
        if not self._require():
            return
        name = self._selected_aot()
        if not name:
            self._status("Select an always-on task first", ok=False)
            return
        self._tasks_log(f"$ systemctl {action} {AOT_PREFIX}{name}")
        self._bg(self.ssh.aot_action, name, action, on_done=lambda res: self._aot_result(res, action != "status"))

    def _aot_result(self, res, refresh: bool) -> None:
        rc, out, err = res
        self._tasks_log((out or "") + (err or "") + f"\n(exit {rc})")
        if refresh:
            self.aot_refresh()

    def on_aot_logs(self) -> None:
        if not self._require():
            return
        name = self._selected_aot()
        if not name:
            self._status("Select an always-on task first", ok=False)
            return
        self._tasks_log(f"$ journalctl -u {AOT_PREFIX}{name} -n 50")
        self._bg(self.ssh.aot_logs, name, on_done=lambda res: self._aot_result(res, False))

    def on_aot_delete(self) -> None:
        if not self._require():
            return
        name = self._selected_aot()
        if not name:
            return
        if QMessageBox.question(self, "Confirm", f"Delete always-on task '{name}'?") != QMessageBox.Yes:
            return
        self._tasks_log(f"$ delete {AOT_PREFIX}{name}")
        self._bg(self.ssh.aot_delete, name, on_done=lambda res: self._aot_result(res, True))

    def closeEvent(self, event) -> None:
        self.ssh.disconnect()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    win = QtApp()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
