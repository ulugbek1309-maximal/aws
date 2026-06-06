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

The whole UI, including the animated "Matrix" digital-rain banner, is rendered
on the graphics card every frame.

Run:
    pip install dearpygui paramiko
    python my_aws_server_gpu.py

The SSH/SFTP logic mirrors the Tkinter build; only the GUI layer changed.
"""

from __future__ import annotations

import os
import stat
import time
import shlex
import random
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

# Matrix green palette (0-255 RGBA tuples for Dear PyGui themes).
COL_BG = (0, 8, 0, 255)
COL_PANEL = (0, 20, 0, 255)
COL_PANEL_HOVER = (0, 40, 0, 255)
COL_GREEN = (0, 255, 65, 255)
COL_GREEN_DIM = (0, 140, 30, 255)
COL_GREEN_BRIGHT = (180, 255, 180, 255)
COL_TEXT = (0, 255, 65, 255)
COL_RED = (255, 60, 60, 255)
COL_BLACK = (0, 0, 0, 255)

MATRIX_CHARS = ("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "アイウエオカキクケコサシスセソタチツテト"
                "ナニヌネノハヒフヘホ$#%&@*+=<>")

CWD_SENTINEL = "__ATM_CWD__:"


def _human_size(num: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if num < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}P"


def _join(base: str, name: str) -> str:
    return base + name if base.endswith("/") else base + "/" + name


# --------------------------------------------------------------------------- #
#  SSH backend (GUI-agnostic, reused logic from the Tkinter build)
# --------------------------------------------------------------------------- #
class SSHBackend:
    """Thin, thread-safe wrapper around paramiko SSH + SFTP."""

    def __init__(self) -> None:
        self.ssh: paramiko.SSHClient | None = None
        self.sftp: paramiko.SFTPClient | None = None
        self.connected = False
        self.use_sudo = True

    # ----- connection ------------------------------------------------ #
    def connect(self, host: str, port: int, user: str, key_path: str,
                passphrase: str | None, use_agent: bool) -> str:
        """Connect and return the remote home directory. Raises on failure."""
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
        except Exception:
            home = f"/home/{user}"
        self.ssh = client
        self.sftp = sftp
        self.connected = True
        return home

    @staticmethod
    def _load_private_key(key_path: str, passphrase: str | None):
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
        self.sftp = None
        self.ssh = None
        self.connected = False

    def is_alive(self) -> bool:
        if not (self.connected and self.ssh):
            return False
        tr = self.ssh.get_transport()
        return bool(tr and tr.is_active())

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

    def _sftp_put_text(self, path: str, text: str) -> None:
        data = text.encode("utf-8")
        with self.sftp.open(path, "w") as fh:
            try:
                fh.set_pipelined(True)
            except Exception:  # noqa: BLE001
                pass
            fh.write(data)

    def write_file(self, path: str, content: str) -> None:
        if self.use_sudo:
            tmp = f"/tmp/atm_save_{int(time.time() * 1000)}_{os.getpid()}"
            try:
                self._sftp_put_text(tmp, content)
                rc, _o, err = self.run(
                    f"sudo -n cp -f {shlex.quote(tmp)} {shlex.quote(path)}")
            finally:
                self.run(f"rm -f {shlex.quote(tmp)}")
            if rc != 0:
                raise IOError(err.strip() or "sudo save failed (passwordless sudo required)")
        else:
            self._sftp_put_text(path, content)

    # ----- shell ----------------------------------------------------- #
    def run(self, cmd: str, timeout: int = 120):
        """Run a command; return (exit_code, stdout, stderr)."""
        _i, out_s, err_s = self.ssh.exec_command(cmd, timeout=timeout)
        out = out_s.read().decode("utf-8", errors="replace")
        err = err_s.read().decode("utf-8", errors="replace")
        rc = out_s.channel.recv_exit_status()
        return rc, out, err

    def terminal_exec(self, command: str, cwd: str, timeout: int = 300):
        """Run a command in cwd; return (output, new_cwd) so 'cd' persists."""
        full = (f"cd {shlex.quote(cwd)} 2>/dev/null; {command}; "
                f"printf '\\n{CWD_SENTINEL}%s\\n' \"$(pwd)\"")
        _i, out_s, err_s = self.ssh.exec_command(full, timeout=timeout)
        out = out_s.read().decode("utf-8", errors="replace")
        err = err_s.read().decode("utf-8", errors="replace")
        new_cwd = None
        kept = []
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
        """Upload code to a temp file and run it with the chosen interpreter."""
        ext = {"python3": "py", "python": "py", "bash": "sh", "sh": "sh",
               "node": "js"}.get(interp, "txt")
        tmp = f"/tmp/atm_run_{int(time.time() * 1000)}_{os.getpid()}.{ext}"
        self._sftp_put_text(tmp, code)
        try:
            cmd = f"cd {shlex.quote(cwd)} 2>/dev/null; {shlex.quote(interp)} {shlex.quote(tmp)} 2>&1"
            rc, out, err = self.run(cmd, timeout=timeout)
        finally:
            self.run(f"rm -f {shlex.quote(tmp)}")
        return rc, (out + err)


# --------------------------------------------------------------------------- #
#  GPU application (Dear PyGui)
# --------------------------------------------------------------------------- #
class GpuApp:
    def __init__(self) -> None:
        self.ssh = SSHBackend()
        self.current_path = "/home/" + HINT_USERNAME
        self.entries: list[dict] = []
        self.active_file: str | None = None

        # Matrix-rain animation state.
        self._rain_w = 1320
        self._rain_h = 90
        self._rain_col_w = 14
        self._rain_drops: list[int] = []
        self._rain_last = 0.0

    # ----- thread helper --------------------------------------------- #
    @staticmethod
    def _bg(fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _set_status(self, text: str, ok: bool = False) -> None:
        dpg.set_value("status_text", text)
        dpg.configure_item("status_text", color=COL_GREEN if ok else COL_RED)

    def _log_terminal(self, text: str) -> None:
        old = dpg.get_value("term_output") or ""
        dpg.set_value("term_output", (old + text).strip("\n") + "\n")

    # ================================================================= #
    #  Connection
    # ================================================================= #
    def on_connect(self) -> None:
        if self.ssh.connected:
            self.ssh.disconnect()
            self._set_status("offline", ok=False)
            dpg.configure_item("conn_btn", label="Connect")
            return

        host = dpg.get_value("in_host").strip()
        port = dpg.get_value("in_port").strip() or HINT_PORT
        user = dpg.get_value("in_user").strip() or HINT_USERNAME
        key = dpg.get_value("in_key").strip()
        passphrase = dpg.get_value("in_pass") or None
        use_agent = dpg.get_value("in_agent")

        if not host:
            self._set_status("Enter host / public IP", ok=False)
            return
        if not port.isdigit() or not (0 < int(port) < 65536):
            self._set_status("Invalid port (1-65535)", ok=False)
            return
        if not use_agent and (not key or not os.path.isfile(key)):
            self._set_status("Select a valid .pem key or enable SSH agent", ok=False)
            return

        self._set_status("connecting...", ok=False)
        self._bg(self._do_connect, host, int(port), user, key, passphrase, use_agent)

    def _do_connect(self, host, port, user, key, passphrase, use_agent) -> None:
        try:
            home = self.ssh.connect(host, port, user, key, passphrase, use_agent)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Connection failed: {exc}", ok=False)
            return
        self.current_path = home
        self._set_status(f"{user}@{host}:{port}", ok=True)
        dpg.configure_item("conn_btn", label="Disconnect")
        self.refresh_listing()

    # ================================================================= #
    #  Files
    # ================================================================= #
    def refresh_listing(self) -> None:
        if not self.ssh.connected:
            self._set_status("Not connected", ok=False)
            return
        self._bg(self._do_listing)

    def _do_listing(self) -> None:
        try:
            rows = self.ssh.listdir(self.current_path)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"List error: {exc}", ok=False)
            return
        self.entries = rows
        dpg.configure_item("file_list", items=[e["display"] for e in rows])
        dpg.set_value("path_text", self.current_path)

    def _selected_entry(self) -> dict | None:
        disp = dpg.get_value("file_list")
        if not disp:
            return None
        for e in self.entries:
            if e["display"] == disp:
                return e
        return None

    def on_open(self) -> None:
        if not self.ssh.connected:
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
            self._set_status(f"Open error: {exc}", ok=False)
            return
        self.active_file = path
        dpg.set_value("editor", text)
        dpg.set_value("editor_label", f"Editing: {path}")

    def on_save(self) -> None:
        if not self.ssh.connected:
            return
        path = self.active_file
        if not path:
            e = self._selected_entry()
            if e and not e["is_dir"] and e["name"] != "..":
                path = _join(self.current_path, e["name"])
                self.active_file = path
        if not path:
            self._set_status("Open or select a file before saving", ok=False)
            return
        self.ssh.use_sudo = dpg.get_value("in_sudo")
        content = dpg.get_value("editor")
        self._bg(self._do_save_file, path, content)

    def _do_save_file(self, path: str, content: str) -> None:
        try:
            self.ssh.write_file(path, content)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Save error: {exc}", ok=False)
            return
        self._set_status(f"Saved: {path}", ok=True)

    # ================================================================= #
    #  .env editor
    # ================================================================= #
    def on_env_load(self) -> None:
        if not self.ssh.connected:
            return
        path = dpg.get_value("env_path").strip()
        if path:
            self._bg(self._do_env_load, path)

    def _do_env_load(self, path: str) -> None:
        try:
            text = self.ssh.read_file(path)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f".env open error: {exc}", ok=False)
            return
        dpg.set_value("env_editor", text)

    def on_env_save(self) -> None:
        if not self.ssh.connected:
            return
        path = dpg.get_value("env_path").strip()
        if not path:
            return
        self.ssh.use_sudo = dpg.get_value("in_sudo")
        self._bg(self._do_env_save, path, dpg.get_value("env_editor"))

    def _do_env_save(self, path: str, content: str) -> None:
        try:
            self.ssh.write_file(path, content)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f".env save error: {exc}", ok=False)
            return
        self._set_status(f"Saved: {path}", ok=True)

    # ================================================================= #
    #  Terminal
    # ================================================================= #
    def on_term_run(self) -> None:
        if not self.ssh.connected:
            self._log_terminal("[not connected]")
            return
        cmd = dpg.get_value("term_input").strip()
        if not cmd:
            return
        dpg.set_value("term_input", "")
        self._log_terminal(f"{self.current_path}$ {cmd}")
        self._bg(self._do_term, cmd)

    def _do_term(self, cmd: str) -> None:
        try:
            out, new_cwd = self.ssh.terminal_exec(cmd, self.current_path)
        except Exception as exc:  # noqa: BLE001
            self._log_terminal(f"[error] {exc}")
            return
        if out:
            self._log_terminal(out)
        if new_cwd:
            self.current_path = new_cwd
            dpg.set_value("path_text", new_cwd)

    def on_term_clear(self) -> None:
        dpg.set_value("term_output", "")

    # ================================================================= #
    #  Code runner
    # ================================================================= #
    def on_run_code(self) -> None:
        if not self.ssh.connected:
            dpg.set_value("runner_output", "[not connected]")
            return
        interp = dpg.get_value("interp")
        code = dpg.get_value("code_editor")
        dpg.set_value("runner_output", "[running...]")
        self._bg(self._do_run_code, interp, code)

    def _do_run_code(self, interp: str, code: str) -> None:
        try:
            rc, out = self.ssh.run_code(interp, code, self.current_path)
        except Exception as exc:  # noqa: BLE001
            dpg.set_value("runner_output", f"[error] {exc}")
            return
        dpg.set_value("runner_output", f"(exit {rc})\n{out}")

    # ================================================================= #
    #  Tasks (cron)
    # ================================================================= #
    def on_cron_create(self) -> None:
        if not self.ssh.connected:
            return
        line = dpg.get_value("cron_line").strip()
        if not line:
            return
        self._bg(self._do_cron_create, line)

    def _do_cron_create(self, line: str) -> None:
        try:
            tmp = f"/tmp/atm_cron_{int(time.time() * 1000)}"
            self.ssh._sftp_put_text(tmp, line + "\n")
            rc, _o, err = self.ssh.run(
                f"(crontab -l 2>/dev/null; cat {shlex.quote(tmp)}) | crontab - ; "
                f"rm -f {shlex.quote(tmp)}")
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"cron error: {exc}", ok=False)
            return
        self._set_status("cron entry added" if rc == 0 else f"cron failed: {err}", ok=(rc == 0))
        self.on_cron_refresh()

    def on_cron_refresh(self) -> None:
        if not self.ssh.connected:
            return
        self._bg(self._do_cron_refresh)

    def _do_cron_refresh(self) -> None:
        try:
            _rc, out, _err = self.ssh.run("crontab -l 2>/dev/null")
        except Exception as exc:  # noqa: BLE001
            dpg.set_value("cron_output", f"[error] {exc}")
            return
        dpg.set_value("cron_output", out or "(no crontab entries)")

    # ================================================================= #
    #  Matrix digital-rain banner (GPU drawlist, updated every frame)
    # ================================================================= #
    def _rain_frame(self) -> None:
        now = time.time()
        if now - self._rain_last < 0.05:  # ~20 FPS for the rain
            return
        self._rain_last = now
        w = dpg.get_item_width("rain_draw") or self._rain_w
        h = self._rain_h
        ncols = max(1, int(w) // self._rain_col_w)
        if len(self._rain_drops) != ncols:
            self._rain_drops = [random.randint(-h, 0) for _ in range(ncols)]

        dpg.delete_item("rain_draw", children_only=True)
        for i, head in enumerate(self._rain_drops):
            x = i * self._rain_col_w + 4
            for t in range(6):
                y = head - t * self._rain_col_w
                if 0 <= y <= h:
                    ch = random.choice(MATRIX_CHARS)
                    if t == 0:
                        color = COL_GREEN_BRIGHT
                    elif t == 1:
                        color = COL_GREEN
                    elif t <= 3:
                        color = (0, 200, 50, 255)
                    else:
                        color = (0, 90, 20, 255)
                    dpg.draw_text((x, y), ch, color=color, size=15, parent="rain_draw")
            ny = head + self._rain_col_w
            if ny - 6 * self._rain_col_w > h and random.random() < 0.12:
                ny = 0
            self._rain_drops[i] = ny

    # ================================================================= #
    #  UI construction
    # ================================================================= #
    def _build_theme(self) -> None:
        with dpg.theme() as self.global_theme:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_WindowBg, COL_BG)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, COL_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_Text, COL_TEXT)
                dpg.add_theme_color(dpg.mvThemeCol_Border, COL_GREEN_DIM)
                dpg.add_theme_color(dpg.mvThemeCol_Button, COL_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, COL_PANEL_HOVER)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, COL_GREEN_DIM)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (0, 16, 0, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, COL_PANEL_HOVER)
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, COL_GREEN_DIM)
                dpg.add_theme_color(dpg.mvThemeCol_TitleBg, COL_BLACK)
                dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, COL_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_Header, COL_PANEL_HOVER)
                dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, COL_GREEN_DIM)
                dpg.add_theme_color(dpg.mvThemeCol_Tab, COL_PANEL)
                dpg.add_theme_color(dpg.mvThemeCol_TabHovered, COL_GREEN_DIM)
                dpg.add_theme_color(dpg.mvThemeCol_TabActive, COL_GREEN_DIM)
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, COL_BLACK)
                dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, COL_GREEN_DIM)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 2)
                dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 2)
        dpg.bind_theme(self.global_theme)

    def _build_connection_bar(self, parent) -> None:
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text("root@matrix:~#", color=COL_GREEN_DIM)
            dpg.add_input_text(tag="in_host", hint="Host / Public IP", width=170)
            dpg.add_input_text(tag="in_port", default_value=HINT_PORT, width=60)
            dpg.add_input_text(tag="in_user", default_value=HINT_USERNAME, width=110)
            dpg.add_button(label="Connect", tag="conn_btn", callback=self.on_connect)
            dpg.add_text("offline", tag="status_text", color=COL_RED)
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_input_text(tag="in_key", hint="SSH key (.pem) path", width=300)
            dpg.add_input_text(tag="in_pass", hint="passphrase", password=True, width=150)
            dpg.add_checkbox(label="SSH agent", tag="in_agent", default_value=False)
            dpg.add_checkbox(label="sudo", tag="in_sudo", default_value=True)

    def _build_files_tab(self) -> None:
        with dpg.tab(label="[~] Files"):
            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh", callback=lambda: self.refresh_listing())
                dpg.add_button(label="Open / Enter", callback=lambda: self.on_open())
                dpg.add_button(label="Save (server)", callback=lambda: self.on_save())
                dpg.add_text("", tag="path_text", color=COL_GREEN_DIM)
            with dpg.group(horizontal=True):
                dpg.add_listbox(tag="file_list", items=[], width=430, num_items=22,
                                callback=lambda: self.on_open())
                with dpg.child_window(width=-1, height=400):
                    dpg.add_text("No file open", tag="editor_label", color=COL_GREEN_DIM)
                    dpg.add_input_text(tag="editor", multiline=True, width=-1, height=360,
                                       default_value="")

    def _build_env_tab(self) -> None:
        with dpg.tab(label="[*] .env Editor"):
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="env_path", hint="/path/to/.env", width=400)
                dpg.add_button(label="Load", callback=lambda: self.on_env_load())
                dpg.add_button(label="Save", callback=lambda: self.on_env_save())
            dpg.add_input_text(tag="env_editor", multiline=True, width=-1, height=420)

    def _build_terminal_tab(self) -> None:
        with dpg.tab(label="[#] Terminal"):
            dpg.add_input_text(tag="term_output", multiline=True, width=-1, height=420,
                               readonly=True, default_value="")
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="term_input", hint="command", width=-160,
                                   on_enter=True, callback=lambda: self.on_term_run())
                dpg.add_button(label="Run", callback=lambda: self.on_term_run())
                dpg.add_button(label="Clear", callback=lambda: self.on_term_clear())

    def _build_runner_tab(self) -> None:
        with dpg.tab(label="[>] Code Runner"):
            with dpg.group(horizontal=True):
                dpg.add_text("Interpreter:")
                dpg.add_combo(("python3", "python", "bash", "sh", "node"),
                              tag="interp", default_value="python3", width=120)
                dpg.add_button(label="Run Code", callback=lambda: self.on_run_code())
            dpg.add_input_text(tag="code_editor", multiline=True, width=-1, height=240,
                               default_value="print('hello from GPU console')")
            dpg.add_text("Output:", color=COL_GREEN_DIM)
            dpg.add_input_text(tag="runner_output", multiline=True, width=-1, height=150,
                               readonly=True)

    def _build_tasks_tab(self) -> None:
        with dpg.tab(label="[+] Tasks"):
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="cron_line", width=-220,
                                   hint="30 3 * * * /usr/bin/command")
                dpg.add_button(label="Add cron", callback=lambda: self.on_cron_create())
                dpg.add_button(label="Refresh", callback=lambda: self.on_cron_refresh())
            dpg.add_text("Current crontab:", color=COL_GREEN_DIM)
            dpg.add_input_text(tag="cron_output", multiline=True, width=-1, height=400,
                               readonly=True)

    def build(self) -> None:
        dpg.create_context()
        self._build_theme()

        with dpg.window(tag="main_win"):
            # GPU-rendered Matrix rain banner.
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

        dpg.create_viewport(title=APP_TITLE, width=1320, height=860,
                            clear_color=(0, 0, 0, 255))
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("main_win", True)

    def run(self) -> None:
        self.build()
        # Manual render loop so we can drive the GPU rain animation per frame.
        while dpg.is_dearpygui_running():
            try:
                self._rain_frame()
            except Exception:  # noqa: BLE001
                pass
            dpg.render_dearpygui_frame()
        dpg.destroy_context()


def main() -> None:
    GpuApp().run()


if __name__ == "__main__":
    main()
