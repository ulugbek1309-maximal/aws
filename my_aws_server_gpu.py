#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AWS Server Manager - GPU edition (Dear PyGui)
=============================================

A GPU-rendered desktop GUI to manage an AWS server over SSH/SFTP. Unlike the
Tkinter build (``my_aws_server.py``, which renders on the CPU), this version is
drawn entirely on the GPU by Dear PyGui's backend:

    * Windows -> DirectX 11
    * macOS   -> Metal
    * Linux   -> OpenGL 3 / Vulkan

This is the full-feature port of the Tkinter app: profiles, file manager with
create / delete / rename / chmod / copy-path / upload / download / filter,
the .env editor, an interactive terminal (with history + quick commands), the
code runner, and the Tasks tab (cron scheduled tasks + systemd always-on
tasks). The whole UI, including the animated "Matrix" digital-rain banner, is
rendered on the graphics card every frame.

Run:
    pip install dearpygui paramiko
    python my_aws_server_gpu.py

The SSH/SFTP logic mirrors the Tkinter build; only the GUI layer changed.
"""

from __future__ import annotations

import os
import json
import stat
import time
import shlex
import queue
import random
import warnings
import threading
import datetime

try:
    import paramiko
except ImportError:  # pragma: no cover
    raise SystemExit("Missing dependency 'paramiko'. Run: pip install paramiko")

try:
    import dearpygui.dearpygui as dpg
except ImportError:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'dearpygui'. This is the GPU rendering engine.\n"
        "Install it with:  pip install dearpygui"
    )


# --------------------------------------------------------------------------- #
#  Constants
# --------------------------------------------------------------------------- #
APP_TITLE = "AWS Server Manager - GPU (Matrix)"
HINT_PORT = "22"
HINT_USERNAME = "ubuntu"
HINT_UPLOAD_TARGET = "/var/www/html"
AOT_PREFIX = "atm-"  # systemd unit prefix for always-on tasks
CWD_SENTINEL = "__ATM_CWD__:"

_HOME = os.path.expanduser("~")
PROFILE_FILE = os.path.join(_HOME, ".aws_gpu_manager_profiles.json")
SETTINGS_FILE = os.path.join(_HOME, ".aws_gpu_manager_settings.json")

# ASCII-only on purpose: katakana looks great but renders as empty boxes
# ("tofu") on systems whose UI font lacks Japanese glyphs (e.g. Segoe UI on
# Windows). These characters render in every font, so the rain always shows.
MATRIX_CHARS = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "abcdefghijklmnopqrstuvwxyz"
                "!<>[]{}()/\\|=+*#$%&@?;:.~^")

# Theme palettes (RGBA 0-255). "matrix" is the default hacker look.
PALETTES = {
    "matrix": {
        "win_bg": (0, 8, 0, 255), "child_bg": (0, 20, 0, 255),
        "frame": (0, 16, 0, 255), "frame_hover": (0, 40, 0, 255),
        "frame_active": (0, 70, 20, 255), "text": (0, 255, 65, 255),
        "border": (0, 90, 25, 255), "button": (0, 24, 0, 255),
        "button_hover": (0, 60, 18, 255), "button_active": (0, 120, 35, 255),
        "title": (0, 0, 0, 255), "tab": (0, 24, 0, 255),
        "tab_active": (0, 70, 20, 255), "header": (0, 50, 15, 255),
        "dim": (0, 140, 30, 255), "ok": (0, 255, 65, 255), "err": (255, 60, 60, 255),
    },
    "dark": {
        "win_bg": (30, 30, 46, 255), "child_bg": (42, 42, 60, 255),
        "frame": (58, 58, 79, 255), "frame_hover": (70, 70, 95, 255),
        "frame_active": (88, 101, 242, 255), "text": (230, 230, 230, 255),
        "border": (80, 80, 100, 255), "button": (58, 58, 79, 255),
        "button_hover": (88, 101, 242, 255), "button_active": (70, 80, 200, 255),
        "title": (20, 20, 30, 255), "tab": (42, 42, 60, 255),
        "tab_active": (88, 101, 242, 255), "header": (70, 70, 95, 255),
        "dim": (150, 150, 170, 255), "ok": (67, 181, 129, 255), "err": (240, 71, 71, 255),
    },
    "light": {
        "win_bg": (242, 242, 245, 255), "child_bg": (227, 227, 234, 255),
        "frame": (255, 255, 255, 255), "frame_hover": (220, 220, 230, 255),
        "frame_active": (88, 101, 242, 255), "text": (30, 30, 46, 255),
        "border": (180, 180, 190, 255), "button": (225, 225, 235, 255),
        "button_hover": (200, 205, 245, 255), "button_active": (88, 101, 242, 255),
        "title": (210, 210, 220, 255), "tab": (225, 225, 235, 255),
        "tab_active": (88, 101, 242, 255), "header": (200, 205, 245, 255),
        "dim": (90, 90, 110, 255), "ok": (46, 158, 98, 255), "err": (204, 51, 51, 255),
    },
}
THEME_ORDER = ["matrix", "kali", "dark", "light"]
PALETTES["kali"] = {
    **PALETTES["matrix"],
    "text": (54, 123, 240, 255), "dim": (120, 160, 230, 255),
    "frame_active": (54, 123, 240, 255), "button_active": (54, 123, 240, 255),
    "tab_active": (40, 70, 150, 255), "ok": (76, 175, 80, 255),
}


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
#  SSH backend (GUI-agnostic; reuses the Tkinter build's logic)
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
#  GPU application (Dear PyGui)
# --------------------------------------------------------------------------- #
class GpuApp:
    def __init__(self) -> None:
        self.ssh = SSHBackend()
        self.current_path = "/home/" + HINT_USERNAME
        self.entries: list[dict] = []
        self.active_file: str | None = None

        self.cron_lines: list[str] = []
        self.aot_names: list[str] = []

        self._cmd_history: list[str] = []
        self._cmd_index = 0

        self._prompt_cb = None
        self._confirm_cb = None
        self._dl_entry: dict | None = None

        # UI updates from worker threads are marshalled to the render (main)
        # thread through this queue to avoid races / freezes (e.g. when a large
        # file is loaded into the editor while the GPU thread reads the buffer).
        self._ui_queue: "queue.Queue" = queue.Queue()
        # Files larger than this are not loaded into the editor widget, because
        # the immediate-mode text box re-measures the whole buffer every frame
        # and would stutter/lock up on huge or very-long-line files.
        # Normal source files (even thousands of lines) stay fully editable.
        # Only genuinely huge files (logs, dumps) open in the read-only paged
        # viewer to avoid the per-frame cost of a giant editable text buffer.
        self.MAX_EDIT_CHARS = 500_000
        self._editor_readonly = False
        # Paged read-only viewer for large files: only ONE small page is ever
        # placed in a lightweight add_text item (NOT input_text, which ImGui
        # re-processes every frame and was the real cause of the freeze).
        self.PAGE_LINES = 200        # lines shown per page
        self.VIEW_LINE_CAP = 300     # truncate very long lines for display
        self._view_lines: list[str] = []
        self._view_text = ""         # full text, kept so "Edit anyway" can load it
        self._view_path = ""
        self._page = 0
        self._viewer_on = False
        # Captured here (on the main thread, where GpuApp is constructed) so
        # worker threads can detect when they must marshal UI calls through the
        # queue instead of touching dpg directly.
        self._main_thread_id = threading.get_ident()

        self.settings = _read_json(SETTINGS_FILE)
        self.theme_name = self.settings.get("theme", "matrix")
        self._themes: dict[str, int] = {}

        # Matrix-rain animation state.
        self._rain_w = 1340
        self._rain_h = 84
        self._rain_col_w = 14
        self._rain_drops: list[int] = []
        self._rain_last = 0.0

    # ----- thread + status helpers ----------------------------------- #
    @staticmethod
    def _bg(fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _post(self, fn) -> None:
        """Schedule a UI update to run on the main (render) thread."""
        self._ui_queue.put(fn)

    def _ui(self, fn) -> None:
        """Run a UI update safely from any thread.

        If we are already on the main (render) thread, run it now; otherwise
        queue it so it executes during the next frame. This guarantees no dpg.*
        call ever runs from a worker thread (which would corrupt the C++ backend
        and freeze/crash the app).
        """
        if threading.get_ident() == self._main_thread_id:
            fn()
        else:
            self._ui_queue.put(fn)

    def _set_value(self, tag, value) -> None:
        self._ui(lambda: dpg.set_value(tag, value))

    def _configure(self, tag, **kwargs) -> None:
        self._ui(lambda: dpg.configure_item(tag, **kwargs))

    def _drain_ui(self) -> None:
        for _ in range(128):  # cap work per frame so the UI stays responsive
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    def _status(self, text: str, ok: bool = False) -> None:
        pal = PALETTES[self.theme_name]
        color = pal["ok"] if ok else pal["err"]

        def do():
            try:
                dpg.set_value("status_text", text)
                dpg.configure_item("status_text", color=color)
            except Exception:  # noqa: BLE001
                pass

        self._ui(do)

    def _require(self) -> bool:
        if not self.ssh.connected:
            self._status("Not connected", ok=False)
            return False
        return True

    def _sync_sudo(self) -> None:
        self.ssh.use_sudo = bool(dpg.get_value("in_sudo"))

    # ----- reusable modal dialogs ------------------------------------ #
    def _prompt(self, title: str, default: str, on_ok) -> None:
        self._prompt_cb = on_ok
        dpg.set_value("prompt_title", title)
        dpg.set_value("prompt_input", default)
        dpg.configure_item("prompt_win", show=True)

    def _prompt_ok(self) -> None:
        val = dpg.get_value("prompt_input")
        dpg.configure_item("prompt_win", show=False)
        cb, self._prompt_cb = self._prompt_cb, None
        if cb:
            cb(val)

    def _confirm(self, text: str, on_yes) -> None:
        self._confirm_cb = on_yes
        dpg.set_value("confirm_text", text)
        dpg.configure_item("confirm_win", show=True)

    def _confirm_yes(self) -> None:
        dpg.configure_item("confirm_win", show=False)
        cb, self._confirm_cb = self._confirm_cb, None
        if cb:
            cb()

    # ================================================================= #
    #  Connection
    # ================================================================= #
    def on_connect(self) -> None:
        if self.ssh.connected:
            self.ssh.disconnect()
            self._status("offline", ok=False)
            dpg.configure_item("conn_btn", label="Connect")
            dpg.configure_item("file_list", items=[])
            return
        host = dpg.get_value("in_host").strip()
        port = dpg.get_value("in_port").strip() or HINT_PORT
        user = dpg.get_value("in_user").strip() or HINT_USERNAME
        key = dpg.get_value("in_key").strip()
        passphrase = dpg.get_value("in_pass") or None
        use_agent = dpg.get_value("in_agent")
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
        self._bg(self._do_connect, host, int(port), user, key, passphrase, use_agent)

    def _do_connect(self, host, port, user, key, passphrase, use_agent) -> None:
        try:
            home = self.ssh.connect(host, port, user, key, passphrase, use_agent)
        except Exception as exc:  # noqa: BLE001
            self._status(f"Connection failed: {exc}", ok=False)
            return
        self.current_path = home
        self._status(f"{user}@{host}:{port}", ok=True)
        self._configure("conn_btn", label="Disconnect")
        self._set_value("in_pass", "")
        self.refresh_listing()
        self.cron_refresh()
        self.aot_refresh()

    def _watchdog(self) -> None:
        if self.ssh.connected and not self.ssh.is_alive():
            self.ssh.connected = False
            self._status("Connection lost", ok=False)
            dpg.configure_item("conn_btn", label="Connect")

    # ----- profiles -------------------------------------------------- #
    def _refresh_profiles(self) -> None:
        names = sorted(_read_json(PROFILE_FILE).keys())
        dpg.configure_item("profile_combo", items=names)

    def on_profile_selected(self) -> None:
        name = dpg.get_value("profile_combo")
        data = _read_json(PROFILE_FILE).get(name)
        if not data:
            return
        dpg.set_value("in_host", data.get("host", ""))
        dpg.set_value("in_port", data.get("port", HINT_PORT))
        dpg.set_value("in_user", data.get("user", HINT_USERNAME))
        dpg.set_value("in_key", data.get("key", ""))
        dpg.set_value("upload_target", data.get("upload_target", HINT_UPLOAD_TARGET))

    def on_profile_save(self) -> None:
        default = dpg.get_value("profile_combo") or dpg.get_value("in_host")
        self._prompt("Profile name:", default, self._do_profile_save)

    def _do_profile_save(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        profiles = _read_json(PROFILE_FILE)
        profiles[name] = {
            "host": dpg.get_value("in_host").strip(),
            "port": dpg.get_value("in_port").strip() or HINT_PORT,
            "user": dpg.get_value("in_user").strip() or HINT_USERNAME,
            "key": dpg.get_value("in_key").strip(),
            "upload_target": dpg.get_value("upload_target").strip(),
        }
        _write_json(PROFILE_FILE, profiles)
        self._refresh_profiles()
        dpg.set_value("profile_combo", name)
        self._status(f"Profile '{name}' saved", ok=True)

    def on_profile_delete(self) -> None:
        name = dpg.get_value("profile_combo")
        if not name:
            return
        self._confirm(f"Delete profile '{name}'?", lambda: self._do_profile_delete(name))

    def _do_profile_delete(self, name: str) -> None:
        profiles = _read_json(PROFILE_FILE)
        profiles.pop(name, None)
        _write_json(PROFILE_FILE, profiles)
        self._refresh_profiles()
        dpg.set_value("profile_combo", "")

    # ----- theme ----------------------------------------------------- #
    def _build_theme(self, palette: dict) -> int:
        with dpg.theme() as theme_id:
            with dpg.theme_component(dpg.mvAll):
                c = dpg.add_theme_color
                c(dpg.mvThemeCol_WindowBg, palette["win_bg"])
                c(dpg.mvThemeCol_ChildBg, palette["child_bg"])
                c(dpg.mvThemeCol_Text, palette["text"])
                c(dpg.mvThemeCol_Border, palette["border"])
                c(dpg.mvThemeCol_Button, palette["button"])
                c(dpg.mvThemeCol_ButtonHovered, palette["button_hover"])
                c(dpg.mvThemeCol_ButtonActive, palette["button_active"])
                c(dpg.mvThemeCol_FrameBg, palette["frame"])
                c(dpg.mvThemeCol_FrameBgHovered, palette["frame_hover"])
                c(dpg.mvThemeCol_FrameBgActive, palette["frame_active"])
                c(dpg.mvThemeCol_TitleBg, palette["title"])
                c(dpg.mvThemeCol_TitleBgActive, palette["child_bg"])
                c(dpg.mvThemeCol_Header, palette["header"])
                c(dpg.mvThemeCol_HeaderHovered, palette["frame_hover"])
                c(dpg.mvThemeCol_HeaderActive, palette["frame_active"])
                c(dpg.mvThemeCol_Tab, palette["tab"])
                c(dpg.mvThemeCol_TabHovered, palette["header"])
                c(dpg.mvThemeCol_TabActive, palette["tab_active"])
                c(dpg.mvThemeCol_ScrollbarBg, palette["title"])
                c(dpg.mvThemeCol_ScrollbarGrab, palette["dim"])
                c(dpg.mvThemeCol_CheckMark, palette["text"])
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 2)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 2)
        return theme_id

    def _apply_theme(self, name: str) -> None:
        self.theme_name = name
        dpg.bind_theme(self._themes[name])
        self.settings["theme"] = name
        _write_json(SETTINGS_FILE, self.settings)

    def on_toggle_theme(self) -> None:
        idx = THEME_ORDER.index(self.theme_name) if self.theme_name in THEME_ORDER else 0
        self._apply_theme(THEME_ORDER[(idx + 1) % len(THEME_ORDER)])

    # ================================================================= #
    #  Files
    # ================================================================= #
    def refresh_listing(self) -> None:
        if not self._require():
            return
        self._bg(self._do_listing)

    def _do_listing(self) -> None:
        try:
            rows = self.ssh.listdir(self.current_path)
        except Exception as exc:  # noqa: BLE001
            self._status(f"List error: {exc}", ok=False)
            return
        self.entries = rows
        self._render_file_list()
        self._set_value("path_text", self.current_path)

    def _render_file_list(self) -> None:
        def do():
            needle = (dpg.get_value("file_filter") or "").strip().lower()
            items = [e["display"] for e in self.entries
                     if e["name"] == ".." or not needle or needle in e["name"].lower()]
            dpg.configure_item("file_list", items=items)

        self._ui(do)

    def on_filter(self) -> None:
        self._render_file_list()

    def _selected_entry(self) -> dict | None:
        disp = dpg.get_value("file_list")
        if not disp:
            return None
        for e in self.entries:
            if e["display"] == disp:
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
            self._bg(self._do_open_file, _join(self.current_path, e["name"]))

    def _do_open_file(self, path: str) -> None:
        try:
            text = self.ssh.read_file(path)
        except Exception as exc:  # noqa: BLE001
            self._status(f"Open error: {exc}", ok=False)
            return
        n = len(text)
        if n > self.MAX_EDIT_CHARS:
            # Too big to edit on the GPU: open it in the virtualized viewer
            # (only the visible lines are drawn) instead of freezing.
            self._open_in_viewer(text, path)
            return

        def apply():
            self.active_file = path
            self._editor_readonly = False
            self._viewer_on = False
            dpg.configure_item("viewer_group", show=False)
            dpg.configure_item("editor", show=True, readonly=False)
            dpg.set_value("editor", text)
            dpg.set_value("editor_label", f"Editing: {path}")
            self._status(f"Opened: {path}", ok=True)

        self._ui(apply)

    # ----- paged large-file viewer ----------------------------------- #
    def _open_in_viewer(self, text: str, path: str) -> None:
        lines = text.split("\n")  # done on the worker thread (cheap, no UI)

        def apply():
            self.active_file = None          # read-only: Save stays blocked
            self._editor_readonly = True
            self._view_lines = lines
            self._view_text = text           # kept so "Edit anyway" can load it
            self._view_path = path
            self._page = 0
            self._viewer_on = True
            dpg.configure_item("editor", show=False)
            dpg.configure_item("viewer_group", show=True)
            dpg.set_value("editor_label", f"VIEW (read-only, paged) - {path}")
            self._show_page()
            self._status(
                f"Large file ({len(text):,} chars) opened read-only. "
                f"Click 'Edit anyway' to load it into the editor.", ok=True)

        self._ui(apply)

    def on_edit_anyway(self) -> None:
        """Force-load the currently viewed large file into the editable box."""
        if not self._viewer_on or not self._view_text:
            return
        text, path = self._view_text, self._view_path

        def apply():
            self.active_file = path
            self._editor_readonly = False
            self._viewer_on = False
            dpg.configure_item("viewer_group", show=False)
            dpg.configure_item("editor", show=True, readonly=False)
            dpg.set_value("editor", text)
            dpg.set_value("editor_label", f"Editing (large): {path}")
            self._status("Editing large file - may feel heavy while typing.", ok=True)

        self._ui(apply)

    def _npages(self) -> int:
        total = len(self._view_lines)
        return max(1, (total + self.PAGE_LINES - 1) // self.PAGE_LINES)

    def _show_page(self) -> None:
        total = len(self._view_lines)
        npages = self._npages()
        self._page = max(0, min(self._page, npages - 1))
        start = self._page * self.PAGE_LINES
        end = min(total, start + self.PAGE_LINES)
        cap = self.VIEW_LINE_CAP
        rows = []
        for i in range(start, end):
            ln = self._view_lines[i]
            if len(ln) > cap:
                ln = ln[:cap] + " >> (truncated)"
            rows.append(f"{i + 1:>8}  {ln}")
        dpg.set_value("viewer_text", "\n".join(rows))
        dpg.set_value("viewer_info",
                      f"lines {start + 1:,}-{end:,} of {total:,}  (page {self._page + 1}/{npages})")

    def on_page(self, delta: int, absolute: bool = False) -> None:
        if not self._viewer_on:
            return
        if absolute:
            self._page = 0 if delta <= 0 else self._npages() - 1
        else:
            self._page += delta
        self._show_page()

    def on_goto_line(self) -> None:
        if not self._viewer_on or not self._view_lines:
            return
        ln = max(1, min(len(self._view_lines), int(dpg.get_value("viewer_goto"))))
        self._page = (ln - 1) // self.PAGE_LINES
        self._show_page()

    def on_save(self) -> None:
        if not self._require():
            return
        if getattr(self, "_editor_readonly", False):
            self._status("Read-only preview - large files cannot be saved here", ok=False)
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
        self._bg(self._do_save_file, path, dpg.get_value("editor"))

    def _do_save_file(self, path: str, content: str) -> None:
        try:
            self.ssh.write_file(path, content)
        except Exception as exc:  # noqa: BLE001
            self._status(f"Save error: {exc}", ok=False)
            return
        self._status(f"Saved: {path}", ok=True)

    def on_new_file(self) -> None:
        if not self._require():
            return
        self._prompt("New file name:", "", lambda n: self._do_fs(
            lambda: self.ssh.new_file(_join(self.current_path, n)), n))

    def on_new_folder(self) -> None:
        if not self._require():
            return
        self._prompt("New folder name:", "", lambda n: self._do_fs(
            lambda: self.ssh.new_folder(_join(self.current_path, n)), n))

    def on_delete(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        path = _join(self.current_path, e["name"])
        self._confirm(f"Delete {path}?",
                      lambda: self._do_fs(lambda: self.ssh.delete(path, e["is_dir"]), e["name"]))

    def on_rename(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        self._prompt("New name:", e["name"], lambda new: self._do_fs(
            lambda: self.ssh.rename(_join(self.current_path, e["name"]),
                                    _join(self.current_path, new)), new) if new and new != e["name"] else None)

    def on_chmod(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        self._prompt("Octal mode (e.g. 644):", "644", lambda m: self._do_chmod(e, m))

    def _do_chmod(self, e: dict, mode: str) -> None:
        try:
            mode_int = int(mode, 8)
        except ValueError:
            self._status("Invalid octal mode", ok=False)
            return
        self._do_fs(lambda: self.ssh.chmod(_join(self.current_path, e["name"]), mode_int), e["name"])

    def on_copy_path(self) -> None:
        e = self._selected_entry()
        if not e or e["name"] == "..":
            return
        path = _join(self.current_path, e["name"])
        try:
            dpg.set_clipboard_text(path)
        except Exception:  # noqa: BLE001
            pass
        self._status(f"Copied: {path}", ok=True)

    def _do_fs(self, action, label: str) -> None:
        """Run a sudo-aware filesystem mutation in the background, then refresh."""
        self._sync_sudo()

        def worker():
            try:
                action()
            except Exception as exc:  # noqa: BLE001
                self._status(f"Error ({label}): {exc}", ok=False)
                return
            self._status(f"OK: {label}", ok=True)
            self._do_listing()

        self._bg(worker)

    # ----- upload / download (local file dialogs) -------------------- #
    def on_upload(self) -> None:
        if not self._require():
            return
        dpg.show_item("upload_dialog")

    def _upload_chosen(self, _sender, app_data) -> None:
        local = app_data.get("file_path_name") or app_data.get("current_path")
        if not local or not os.path.isdir(local):
            self._status("Choose a valid folder to upload", ok=False)
            return
        target = dpg.get_value("upload_target").strip()
        if not target:
            self._status("Enter the remote target dir", ok=False)
            return
        self._sync_sudo()
        dpg.set_value("upload_progress", 0.0)
        self._bg(self._do_upload, local, target)

    def _do_upload(self, local: str, target: str) -> None:
        try:
            remote_root = self.ssh.upload_folder(
                local, target, progress_cb=lambda f: self._set_value("upload_progress", f))
        except Exception as exc:  # noqa: BLE001
            self._set_value("upload_progress", 0.0)
            self._status(f"Upload error: {exc}", ok=False)
            return
        self._set_value("upload_progress", 0.0)
        self._status(f"Uploaded to: {remote_root}", ok=True)
        self._do_listing()

    def on_download(self) -> None:
        if not self._require():
            return
        e = self._selected_entry()
        if not e or e["name"] == "..":
            self._status("Select a file/folder to download", ok=False)
            return
        self._dl_entry = e
        dpg.show_item("download_dialog")

    def _download_chosen(self, _sender, app_data) -> None:
        local = app_data.get("file_path_name") or app_data.get("current_path")
        e = self._dl_entry
        if not local or not os.path.isdir(local) or not e:
            self._status("Choose a valid destination folder", ok=False)
            return
        dpg.set_value("upload_progress", 0.0)
        self._bg(self._do_download, _join(self.current_path, e["name"]), e["name"], local, e["is_dir"])

    def _do_download(self, remote_path, name, local, is_dir) -> None:
        try:
            count = self.ssh.download(remote_path, name, local, is_dir,
                                      progress_cb=lambda f: self._set_value("upload_progress", f))
        except Exception as exc:  # noqa: BLE001
            self._set_value("upload_progress", 0.0)
            self._status(f"Download error: {exc}", ok=False)
            return
        self._set_value("upload_progress", 0.0)
        self._status(f"Downloaded {count} file(s) to: {local}", ok=True)

    def on_pick_key(self) -> None:
        dpg.show_item("key_dialog")

    def _key_chosen(self, _sender, app_data) -> None:
        path = app_data.get("file_path_name")
        if path:
            dpg.set_value("in_key", path)

    # ================================================================= #
    #  .env editor
    # ================================================================= #
    def on_env_load(self) -> None:
        if not self._require():
            return
        path = dpg.get_value("env_path").strip() or _join(self.current_path, ".env")
        dpg.set_value("env_path", path)
        self._bg(self._do_env_load, path)

    def _do_env_load(self, path: str) -> None:
        try:
            text = self.ssh.read_file(path)
        except Exception as exc:  # noqa: BLE001
            self._status(f".env open error: {exc}", ok=False)
            return
        if len(text) > self.MAX_EDIT_CHARS:
            self._set_value("env_editor", text[:self.MAX_EDIT_CHARS])
            self._status(f".env preview truncated ({len(text):,} chars)", ok=True)
            return
        self._set_value("env_editor", text)

    def on_env_save(self) -> None:
        if not self._require():
            return
        path = dpg.get_value("env_path").strip()
        if not path:
            self._status("Provide the .env path first", ok=False)
            return
        self._sync_sudo()
        self._bg(self._do_env_save, path, dpg.get_value("env_editor"))

    def _do_env_save(self, path: str, content: str) -> None:
        try:
            self.ssh.write_file(path, content)
        except Exception as exc:  # noqa: BLE001
            self._status(f".env save error: {exc}", ok=False)
            return
        self._status(f"Saved: {path}", ok=True)

    # ================================================================= #
    #  Terminal
    # ================================================================= #
    def _term_log(self, text: str) -> None:
        def do():
            old = dpg.get_value("term_output") or ""
            dpg.set_value("term_output", (old + text).rstrip("\n") + "\n")

        self._ui(do)

    def on_term_run(self) -> None:
        if not self.ssh.connected:
            self._term_log("[not connected]")
            return
        cmd = dpg.get_value("term_input").strip()
        if not cmd:
            return
        dpg.set_value("term_input", "")
        if cmd in ("clear", "cls"):
            dpg.set_value("term_output", "")
            return
        self._cmd_history.append(cmd)
        self._cmd_index = len(self._cmd_history)
        self._term_log(f"{self.current_path}$ {cmd}")
        self._bg(self._do_term, cmd)

    def _do_term(self, cmd: str) -> None:
        try:
            out, new_cwd = self.ssh.terminal_exec(cmd, self.current_path)
        except Exception as exc:  # noqa: BLE001
            self._term_log(f"[error] {exc}")
            return
        if out:
            self._term_log(out)
        if new_cwd:
            self.current_path = new_cwd
            self._set_value("path_text", new_cwd)

    def on_term_history(self, direction: int) -> None:
        if not self._cmd_history:
            return
        self._cmd_index = max(0, min(len(self._cmd_history), self._cmd_index + direction))
        val = self._cmd_history[self._cmd_index] if self._cmd_index < len(self._cmd_history) else ""
        dpg.set_value("term_input", val)

    def on_quick(self, cmd: str) -> None:
        dpg.set_value("term_input", cmd)
        self.on_term_run()

    def on_term_clear(self) -> None:
        dpg.set_value("term_output", "")

    # ================================================================= #
    #  Code runner
    # ================================================================= #
    def on_run_code(self) -> None:
        if not self._require():
            return
        interp = dpg.get_value("interp")
        code = dpg.get_value("code_editor")
        if not code.strip():
            return
        dpg.set_value("runner_output", f"$ {interp}  (cwd: {self.current_path})\n[running...]")
        self._bg(self._do_run_code, interp, code)

    def _do_run_code(self, interp: str, code: str) -> None:
        try:
            rc, out, err = self.ssh.run_code(interp, code, self.current_path)
        except Exception as exc:  # noqa: BLE001
            self._set_value("runner_output", f"[error] {exc}")
            return
        text = f"$ {interp}  (cwd: {self.current_path})\n"
        if out:
            text += out
        if err.strip():
            text += "\n[stderr]\n" + err
        text += f"\n--- done (exit {rc}) ---"
        if len(text) > self.MAX_EDIT_CHARS:
            text = text[:self.MAX_EDIT_CHARS] + "\n--- output truncated ---"
        self._set_value("runner_output", text)

    def on_load_open(self) -> None:
        if not self.active_file:
            self._status("Open a file in Files first", ok=False)
            return
        dpg.set_value("code_editor", dpg.get_value("editor"))

    def on_clear_runner(self) -> None:
        dpg.set_value("runner_output", "")

    # ================================================================= #
    #  Tasks - cron
    # ================================================================= #
    def cron_refresh(self) -> None:
        if not self.ssh.connected:
            return
        self._bg(self._do_cron_refresh)

    def _do_cron_refresh(self) -> None:
        try:
            lines = self.ssh.cron_list()
        except Exception as exc:  # noqa: BLE001
            self._status(f"cron error: {exc}", ok=False)
            return
        self.cron_lines = lines
        self._configure("cron_list", items=lines or ["(no scheduled tasks)"])

    def on_cron_create(self) -> None:
        if not self._require():
            return
        cmd = dpg.get_value("cron_cmd").strip()
        if not cmd:
            self._status("Enter a command to schedule", ok=False)
            return
        minute = str(dpg.get_value("cron_min")).strip()
        hour = str(dpg.get_value("cron_hour")).strip()
        if not minute.isdigit() or not (0 <= int(minute) <= 59):
            self._status("Minute must be 0-59", ok=False)
            return
        if dpg.get_value("cron_freq") == "Daily":
            if not hour.isdigit() or not (0 <= int(hour) <= 23):
                self._status("Hour must be 0-23", ok=False)
                return
            schedule = f"{int(minute)} {int(hour)} * * *"
        else:
            schedule = f"{int(minute)} * * * *"
        self._bg(self._do_cron_create, f"{schedule} {cmd}")

    def _do_cron_create(self, line: str) -> None:
        try:
            lines = self.ssh.cron_list()
            lines.append(line)
            self.ssh.cron_write(lines)
        except Exception as exc:  # noqa: BLE001
            self._status(f"Schedule error: {exc}", ok=False)
            return
        self._status(f"Scheduled: {line}", ok=True)
        self._do_cron_refresh()

    def on_cron_delete(self) -> None:
        if not self._require():
            return
        target = dpg.get_value("cron_list")
        if not target or target not in self.cron_lines:
            return
        self._confirm(f"Delete scheduled task?\n{target}",
                      lambda: self._bg(self._do_cron_delete, target))

    def _do_cron_delete(self, target: str) -> None:
        try:
            lines = [ln for ln in self.ssh.cron_list() if ln != target]
            self.ssh.cron_write(lines)
        except Exception as exc:  # noqa: BLE001
            self._status(f"Delete error: {exc}", ok=False)
            return
        self._do_cron_refresh()

    # ================================================================= #
    #  Tasks - always-on (systemd)
    # ================================================================= #
    def _tasks_log(self, text: str) -> None:
        def do():
            old = dpg.get_value("tasks_console") or ""
            dpg.set_value("tasks_console", (old + text).rstrip("\n") + "\n")

        self._ui(do)

    def aot_refresh(self) -> None:
        if not self.ssh.connected:
            return
        self._bg(self._do_aot_refresh)

    def _do_aot_refresh(self) -> None:
        try:
            pairs = self.ssh.aot_list()
        except Exception as exc:  # noqa: BLE001
            self._status(f"always-on error: {exc}", ok=False)
            return
        self.aot_names = [nm for nm, _s in pairs]
        display = [f"{nm}  [{st}]" for nm, st in pairs]
        self._configure("aot_list", items=display or ["(no always-on tasks)"])

    def on_aot_create(self) -> None:
        if not self._require():
            return
        import re
        name = re.sub(r"[^a-zA-Z0-9_-]", "-", dpg.get_value("aot_name")).strip("-")
        cmd = dpg.get_value("aot_cmd").strip()
        if not name:
            self._status("Enter a task name", ok=False)
            return
        if not cmd:
            self._status("Enter a command to run", ok=False)
            return
        user = dpg.get_value("in_user").strip() or HINT_USERNAME
        self._tasks_log(f"$ create {AOT_PREFIX}{name}")
        self._bg(self._do_aot_create, name, cmd, user, self.current_path)

    def _do_aot_create(self, name, cmd, user, cwd) -> None:
        try:
            out = self.ssh.aot_create(name, cmd, user, cwd)
        except Exception as exc:  # noqa: BLE001
            self._status(f"always-on error: {exc}", ok=False)
            self._tasks_log(f"[error] {exc}")
            return
        self._tasks_log(out + f"\nStarted {AOT_PREFIX}{name}")
        self._do_aot_refresh()

    def _selected_aot(self) -> str | None:
        disp = dpg.get_value("aot_list")
        if not disp:
            return None
        nm = disp.split("  [")[0]
        return nm if nm in self.aot_names else None

    def on_aot_action(self, action: str) -> None:
        if not self._require():
            return
        name = self._selected_aot()
        if not name:
            self._status("Select an always-on task first", ok=False)
            return
        self._tasks_log(f"$ systemctl {action} {AOT_PREFIX}{name}")
        self._bg(self._do_aot_action, name, action)

    def _do_aot_action(self, name: str, action: str) -> None:
        try:
            rc, out, err = self.ssh.aot_action(name, action)
        except Exception as exc:  # noqa: BLE001
            self._tasks_log(f"[error] {exc}")
            return
        self._tasks_log((out or "") + (err or "") + f"\n(exit {rc})")
        if action != "status":
            self._do_aot_refresh()

    def on_aot_logs(self) -> None:
        if not self._require():
            return
        name = self._selected_aot()
        if not name:
            self._status("Select an always-on task first", ok=False)
            return
        self._tasks_log(f"$ journalctl -u {AOT_PREFIX}{name} -n 50")
        self._bg(self._do_aot_logs, name)

    def _do_aot_logs(self, name: str) -> None:
        try:
            rc, out, err = self.ssh.aot_logs(name)
        except Exception as exc:  # noqa: BLE001
            self._tasks_log(f"[error] {exc}")
            return
        self._tasks_log((out or "") + (err or "") + f"\n(exit {rc})")

    def on_aot_delete(self) -> None:
        if not self._require():
            return
        name = self._selected_aot()
        if not name:
            return
        self._confirm(f"Delete always-on task '{name}'?",
                      lambda: self._bg(self._do_aot_delete, name))

    def _do_aot_delete(self, name: str) -> None:
        self._tasks_log(f"$ delete {AOT_PREFIX}{name}")
        try:
            rc, out, err = self.ssh.aot_delete(name)
        except Exception as exc:  # noqa: BLE001
            self._tasks_log(f"[error] {exc}")
            return
        self._tasks_log((out or "") + (err or "") + f"\n(exit {rc})")
        self._do_aot_refresh()

    # ================================================================= #
    #  Matrix digital-rain banner (GPU drawlist)
    # ================================================================= #
    def _rain_frame(self) -> None:
        now = time.time()
        if now - self._rain_last < 0.05:
            return
        self._rain_last = now
        if self.theme_name not in ("matrix", "kali"):
            dpg.delete_item("rain_draw", children_only=True)
            return
        w = dpg.get_item_width("rain_draw") or self._rain_w
        h = self._rain_h
        ncols = max(1, int(w) // self._rain_col_w)
        if len(self._rain_drops) != ncols:
            self._rain_drops = [random.randint(-h, 0) for _ in range(ncols)]
        head_col = (180, 255, 180, 255) if self.theme_name == "matrix" else (200, 220, 255, 255)
        main_col = PALETTES[self.theme_name]["text"]
        dpg.delete_item("rain_draw", children_only=True)
        for i, head in enumerate(self._rain_drops):
            x = i * self._rain_col_w + 4
            for t in range(6):
                y = head - t * self._rain_col_w
                if 0 <= y <= h:
                    ch = random.choice(MATRIX_CHARS)
                    if t == 0:
                        color = head_col
                    elif t == 1:
                        color = main_col
                    elif t <= 3:
                        color = (main_col[0] // 2, main_col[1] // 2, main_col[2] // 2, 255)
                    else:
                        color = (main_col[0] // 3, main_col[1] // 3, main_col[2] // 3, 255)
                    dpg.draw_text((x, y), ch, color=color, size=15, parent="rain_draw")
            ny = head + self._rain_col_w
            if ny - 6 * self._rain_col_w > h and random.random() < 0.12:
                ny = 0
            self._rain_drops[i] = ny

    # ================================================================= #
    #  UI construction
    # ================================================================= #
    def _build_dialogs(self) -> None:
        with dpg.window(label="Input", modal=True, show=False, tag="prompt_win",
                        no_resize=True, width=360, height=120, pos=(450, 300)):
            dpg.add_text("", tag="prompt_title")
            dpg.add_input_text(tag="prompt_input", width=330, on_enter=True,
                               callback=self._prompt_ok)
            with dpg.group(horizontal=True):
                dpg.add_button(label="OK", width=80, callback=self._prompt_ok)
                dpg.add_button(label="Cancel", width=80,
                               callback=lambda: dpg.configure_item("prompt_win", show=False))

        with dpg.window(label="Confirm", modal=True, show=False, tag="confirm_win",
                        no_resize=True, width=380, height=130, pos=(450, 300)):
            dpg.add_text("", tag="confirm_text", wrap=360)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Yes", width=80, callback=self._confirm_yes)
                dpg.add_button(label="No", width=80,
                               callback=lambda: dpg.configure_item("confirm_win", show=False))

        dpg.add_file_dialog(directory_selector=True, show=False, tag="upload_dialog",
                            width=620, height=420, callback=self._upload_chosen)
        dpg.add_file_dialog(directory_selector=True, show=False, tag="download_dialog",
                            width=620, height=420, callback=self._download_chosen)
        with dpg.file_dialog(directory_selector=False, show=False, tag="key_dialog",
                             width=620, height=420, callback=self._key_chosen):
            dpg.add_file_extension(".*")
            dpg.add_file_extension(".pem")

    def _build_connection_bar(self, parent) -> None:
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text("root@matrix:~#")
            dpg.add_text("Profile:")
            dpg.add_combo([], tag="profile_combo", width=160, callback=self.on_profile_selected)
            dpg.add_button(label="Save", callback=self.on_profile_save)
            dpg.add_button(label="Delete", callback=self.on_profile_delete)
            dpg.add_button(label="Theme", callback=self.on_toggle_theme)
            dpg.add_text("offline", tag="status_text")
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_input_text(tag="in_host", hint="Host / Public IP", width=170)
            dpg.add_input_text(tag="in_port", default_value=HINT_PORT, width=60)
            dpg.add_input_text(tag="in_user", default_value=HINT_USERNAME, width=110)
            dpg.add_input_text(tag="in_key", hint="SSH key (.pem) path", width=230)
            dpg.add_button(label="Browse", callback=self.on_pick_key)
            dpg.add_input_text(tag="in_pass", hint="passphrase", password=True, width=120)
            dpg.add_button(label="Connect", tag="conn_btn", callback=self.on_connect)
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_checkbox(label="SSH agent", tag="in_agent", default_value=False)
            dpg.add_checkbox(label="sudo", tag="in_sudo", default_value=True)
            dpg.add_text("Path:")
            dpg.add_text("", tag="path_text")

    def _build_files_tab(self) -> None:
        with dpg.tab(label="[~] Files"):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh", callback=lambda: self.refresh_listing())
                dpg.add_button(label="Open/Enter", callback=lambda: self.on_open())
                dpg.add_button(label="Save", callback=lambda: self.on_save())
                dpg.add_button(label="New File", callback=lambda: self.on_new_file())
                dpg.add_button(label="New Folder", callback=lambda: self.on_new_folder())
                dpg.add_button(label="Delete", callback=lambda: self.on_delete())
                dpg.add_button(label="Rename", callback=lambda: self.on_rename())
                dpg.add_button(label="chmod", callback=lambda: self.on_chmod())
                dpg.add_button(label="Copy path", callback=lambda: self.on_copy_path())
            with dpg.group(horizontal=True):
                dpg.add_text("Filter:")
                dpg.add_input_text(tag="file_filter", width=180, callback=lambda: self.on_filter())
                dpg.add_button(label="Upload folder", callback=lambda: self.on_upload())
                dpg.add_button(label="Download", callback=lambda: self.on_download())
                dpg.add_text("Target:")
                dpg.add_input_text(tag="upload_target", default_value=HINT_UPLOAD_TARGET, width=180)
            dpg.add_progress_bar(tag="upload_progress", default_value=0.0, width=-1)
            with dpg.group(horizontal=True):
                dpg.add_listbox(tag="file_list", items=[], width=430, num_items=20,
                                callback=lambda: self.on_open())
                with dpg.child_window(width=-1, height=380):
                    dpg.add_text("No file open", tag="editor_label")
                    dpg.add_input_text(tag="editor", multiline=True, width=-1, height=345)
                    with dpg.group(tag="viewer_group", show=False):
                        with dpg.group(horizontal=True):
                            dpg.add_button(label="|<", callback=lambda: self.on_page(0, absolute=True))
                            dpg.add_button(label="< Prev", callback=lambda: self.on_page(-1))
                            dpg.add_button(label="Next >", callback=lambda: self.on_page(1))
                            dpg.add_button(label=">|", callback=lambda: self.on_page(1, absolute=True))
                            dpg.add_text("", tag="viewer_info")
                            dpg.add_text("  line:")
                            dpg.add_input_int(tag="viewer_goto", width=110,
                                              default_value=1, min_value=1, min_clamped=True)
                            dpg.add_button(label="Go", callback=lambda: self.on_goto_line())
                            dpg.add_button(label="Edit anyway", callback=lambda: self.on_edit_anyway())
                        with dpg.child_window(tag="viewer_area", width=-1, height=320,
                                              horizontal_scrollbar=True):
                            dpg.add_text("", tag="viewer_text")

    def _build_env_tab(self) -> None:
        with dpg.tab(label="[*] .env Editor"):
            with dpg.group(horizontal=True):
                dpg.add_text(".env path:")
                dpg.add_input_text(tag="env_path", hint="/path/to/.env", width=380)
                dpg.add_button(label="Load", callback=lambda: self.on_env_load())
                dpg.add_button(label="Save", callback=lambda: self.on_env_save())
            dpg.add_input_text(tag="env_editor", multiline=True, width=-1, height=420)

    def _build_terminal_tab(self) -> None:
        with dpg.tab(label="[#] Terminal"):
            with dpg.group(horizontal=True):
                dpg.add_text("Quick:")
                for cmd in ("pip install -r requirements.txt", "ls -la", "df -h"):
                    dpg.add_button(label=cmd, user_data=cmd,
                                   callback=lambda s, a, u: self.on_quick(u))
                dpg.add_button(label="Clear", callback=lambda: self.on_term_clear())
            dpg.add_input_text(tag="term_output", multiline=True, width=-1, height=400,
                               readonly=True)
            with dpg.group(horizontal=True):
                dpg.add_button(label="<", callback=lambda: self.on_term_history(-1))
                dpg.add_button(label=">", callback=lambda: self.on_term_history(1))
                dpg.add_input_text(tag="term_input", hint="command", width=-120,
                                   on_enter=True, callback=lambda: self.on_term_run())
                dpg.add_button(label="Run", callback=lambda: self.on_term_run())

    def _build_runner_tab(self) -> None:
        with dpg.tab(label="[>] Code Runner"):
            with dpg.group(horizontal=True):
                dpg.add_text("Interpreter:")
                dpg.add_combo(("python3", "python", "node", "bash"), tag="interp",
                              default_value="python3", width=120)
                dpg.add_button(label="Run Code", callback=lambda: self.on_run_code())
                dpg.add_button(label="Load open file", callback=lambda: self.on_load_open())
                dpg.add_button(label="Clear", callback=lambda: self.on_clear_runner())
            dpg.add_input_text(tag="code_editor", multiline=True, width=-1, height=250,
                               default_value="# Write code here and click Run Code\nprint('Hello from the server!')")
            dpg.add_text("Output:")
            dpg.add_input_text(tag="runner_output", multiline=True, width=-1, height=160,
                               readonly=True)

    def _build_tasks_tab(self) -> None:
        with dpg.tab(label="[+] Tasks"):
            with dpg.tab_bar():
                with dpg.tab(label="Scheduled (cron)"):
                    dpg.add_text("Run a command at a set time (server crontab).")
                    with dpg.group(horizontal=True):
                        dpg.add_text("Command:")
                        dpg.add_input_text(tag="cron_cmd", width=-1)
                    with dpg.group(horizontal=True):
                        dpg.add_text("Frequency:")
                        dpg.add_combo(("Daily", "Hourly"), tag="cron_freq",
                                      default_value="Daily", width=90)
                        dpg.add_text("Hour (UTC):")
                        dpg.add_input_int(tag="cron_hour", default_value=0, width=90,
                                          min_value=0, max_value=23, min_clamped=True,
                                          max_clamped=True)
                        dpg.add_text("Minute:")
                        dpg.add_input_int(tag="cron_min", default_value=0, width=90,
                                          min_value=0, max_value=59, min_clamped=True,
                                          max_clamped=True)
                        dpg.add_button(label="Create", callback=lambda: self.on_cron_create())
                        dpg.add_button(label="Refresh", callback=lambda: self.cron_refresh())
                        dpg.add_button(label="Delete selected", callback=lambda: self.on_cron_delete())
                    dpg.add_listbox(tag="cron_list", items=[], num_items=16, width=-1)
                with dpg.tab(label="Always-on (systemd)"):
                    dpg.add_text("Keep a command running 24/7; auto-restarts (needs sudo).")
                    with dpg.group(horizontal=True):
                        dpg.add_text("Name:")
                        dpg.add_input_text(tag="aot_name", width=150)
                        dpg.add_text("Command:")
                        dpg.add_input_text(tag="aot_cmd", width=-1)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Create & Start", callback=lambda: self.on_aot_create())
                        dpg.add_button(label="Refresh", callback=lambda: self.aot_refresh())
                        for label, act in (("Start", "start"), ("Stop", "stop"),
                                           ("Restart", "restart"), ("Status", "status")):
                            dpg.add_button(label=label, user_data=act,
                                           callback=lambda s, a, u: self.on_aot_action(u))
                        dpg.add_button(label="Logs", callback=lambda: self.on_aot_logs())
                        dpg.add_button(label="Delete", callback=lambda: self.on_aot_delete())
                    dpg.add_listbox(tag="aot_list", items=[], num_items=6, width=-1)
                    dpg.add_input_text(tag="tasks_console", multiline=True, width=-1,
                                       height=200, readonly=True)

    def _setup_fonts(self) -> None:
        """Load a wide-Unicode font so emojis / cyrillic / CJK don't show as '?'.

        Dear PyGui's built-in font only covers basic ASCII, so any other glyph
        renders as a missing-character box / '?'. We pick the first available
        broad-coverage .ttf/.ttc on this machine, enable the full glyph range,
        and (if present) merge a color-emoji font on top of it.
        """
        text_candidates = [
            # Windows
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arialuni.ttf",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/YuGothM.ttc",
            # macOS
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            # Linux (common Noto / DejaVu coverage)
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "/usr/share/fonts/google-noto/NotoSans-Regular.ttf",
        ]
        emoji_candidates = [
            "C:/Windows/Fonts/seguiemj.ttf",                       # Windows color emoji
            "/System/Library/Fonts/Apple Color Emoji.ttc",         # macOS color emoji
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",   # Linux
            "/usr/share/fonts/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",
        ]
        text_path = next((p for p in text_candidates if os.path.isfile(p)), None)
        if not text_path:
            # No broad font found: keep the default (ASCII still fine).
            return
        try:
            with dpg.font_registry():
                with dpg.font(text_path, 16) as main_font:
                    # Newer Dear PyGui versions build glyph ranges automatically
                    # and mark these calls as deprecated no-ops; older versions
                    # still need them for non-ASCII coverage. Call them only if
                    # available and silence the deprecation noise either way.
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", DeprecationWarning)
                        hint_fn = getattr(dpg, "add_font_range_hint", None)
                        range_fn = getattr(dpg, "add_font_range", None)
                        if callable(hint_fn):
                            default_hint = getattr(dpg, "mvFontRangeHint_Default", None)
                            if default_hint is not None:
                                hint_fn(default_hint)
                            for hint in ("Cyrillic", "Japanese", "Chinese_Full",
                                         "Korean", "Vietnamese", "Thai"):
                                h = getattr(dpg, f"mvFontRangeHint_{hint}", None)
                                if h is not None:
                                    hint_fn(h)
                        if callable(range_fn):
                            range_fn(0x2190, 0x2BFF)    # arrows, symbols, misc
                            range_fn(0x1F300, 0x1FAFF)  # emoji & pictographs
                    emoji_path = next((p for p in emoji_candidates if os.path.isfile(p)), None)
                    if emoji_path:
                        try:
                            dpg.add_font(emoji_path, 16)
                        except Exception:  # noqa: BLE001
                            pass
            dpg.bind_font(main_font)
        except Exception:  # noqa: BLE001
            # Any font loading issue must never crash the app.
            pass

    def build(self) -> None:
        dpg.create_context()
        self._setup_fonts()
        for nm, pal in PALETTES.items():
            self._themes[nm] = self._build_theme(pal)
        self._build_dialogs()

        with dpg.window(tag="main_win"):
            with dpg.drawlist(width=self._rain_w, height=self._rain_h, tag="rain_draw"):
                pass
            self._build_connection_bar("main_win")
            dpg.add_separator()
            with dpg.tab_bar():
                self._build_files_tab()
                self._build_env_tab()
                self._build_terminal_tab()
                self._build_runner_tab()
                self._build_tasks_tab()

        dpg.create_viewport(title=APP_TITLE, width=1360, height=880)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main_win", True)
        self._apply_theme(self.theme_name)
        self._refresh_profiles()

    def run(self) -> None:
        self.build()
        frame = 0
        while dpg.is_dearpygui_running():
            try:
                self._drain_ui()
                self._rain_frame()
                frame += 1
                if frame % 240 == 0:  # ~ every few seconds
                    self._watchdog()
            except Exception:  # noqa: BLE001
                pass
            dpg.render_dearpygui_frame()
        dpg.destroy_context()


def main() -> None:
    import traceback
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gpu_error.log")
    try:
        GpuApp().run()
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(tb)
        except OSError:
            pass
        print("\n=== AWS GPU Manager crashed ===")
        print(tb)
        print(f"(A copy was written to: {log_path})")
        raise


if __name__ == "__main__":
    main()
