#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless self-test: fakes tkinter + paramiko, drives the app logic.

Catches real bugs (file open path, save, terminal, runner, tasks, profiles)
without needing a display. Run:  python selftest.py
"""
import sys
import types
import stat
import tempfile
import os

# --------------------------------------------------------------------------- #
#  Fake tkinter
# --------------------------------------------------------------------------- #
END = "end"


class _Var:
    def __init__(self, value="", master=None, **k):
        self._v = value
        self._cbs = []

    def get(self):
        return self._v

    def set(self, v):
        self._v = v
        for cb in self._cbs:
            try:
                cb()
            except Exception:
                pass

    def trace_add(self, mode, cb):
        self._cbs.append(cb)


class FakeWidget:
    def __init__(self, *a, **k):
        self._d = {}

    def __getattr__(self, name):
        if name == "winfo_children":
            return lambda *a, **k: []
        def _noop(*a, **k):
            return None
        return _noop

    def __setitem__(self, k, v):
        self._d[k] = v

    def __getitem__(self, k):
        return self._d.get(k)


class FakeText(FakeWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._content = ""

    def insert(self, index, chars, *tags):
        if index in ("1.0",):
            self._content = chars + self._content
        else:
            self._content += chars

    def delete(self, a, b=None):
        self._content = ""

    def get(self, a="1.0", b=END):
        return self._content

    def bbox(self, *a):
        return (0, 0, 10, 16)

    def compare(self, *a):
        return False

    def index(self, *a):
        return "1.0"

    def edit_modified(self, *a):
        return False


class FakeListbox(FakeWidget):
    def __init__(self, *a, **k):
        super().__init__()
        self._items = []
        self._sel = set()

    def insert(self, index, text):
        if index == END:
            self._items.append(text)
        else:
            self._items.insert(int(index), text)

    def delete(self, a, b=None):
        self._items = []
        self._sel = set()

    def get(self, a, b=None):
        return self._items[int(a)]

    def size(self):
        return len(self._items)

    def curselection(self):
        return tuple(sorted(self._sel))

    def selection_clear(self, a, b=None):
        self._sel = set()

    def selection_set(self, i):
        self._sel = {int(i)}

    def activate(self, i):
        pass

    def nearest(self, y):
        idx = y // 16
        return max(0, min(idx, len(self._items) - 1)) if self._items else -1

    def bbox(self, idx):
        idx = int(idx)
        if idx < 0 or idx >= len(self._items):
            return None
        return (0, idx * 16, 50, 16)


class FakeNotebook(FakeWidget):
    def add(self, child, text=""):
        pass

    def select(self, *a):
        return 0

    def index(self, *a):
        return 0


class FakeStyle(FakeWidget):
    def theme_use(self, *a):
        pass


class FakeTk(FakeWidget):
    def after(self, ms, cb=None, *a):
        return "id"  # do NOT run callbacks (avoid loops/threads)

    def after_cancel(self, *a):
        pass

    def winfo_geometry(self):
        return "1300x840+0+0"


def build_fake_tkinter():
    tk = types.ModuleType("tkinter")
    for name in ("END", "LEFT", "RIGHT", "TOP", "BOTTOM", "BOTH", "X", "Y",
                 "NONE", "WORD", "CHAR", "INSERT", "VERTICAL", "HORIZONTAL",
                 "DISABLED", "NORMAL"):
        setattr(tk, name, name.lower())
    tk.END = "end"
    tk.INSERT = "insert"
    tk.Tk = FakeTk
    tk.Text = FakeText
    tk.Listbox = FakeListbox
    tk.Canvas = FakeWidget
    tk.Menu = FakeWidget
    tk.StringVar = _Var
    tk.BooleanVar = _Var
    tk.TclError = Exception
    tk.Frame = FakeWidget

    ttk = types.ModuleType("tkinter.ttk")
    for n in ("Frame", "Label", "Button", "Entry", "Scrollbar", "Progressbar",
              "Checkbutton", "Spinbox", "Panedwindow", "Labelframe"):
        setattr(ttk, n, FakeWidget)
    ttk.Combobox = FakeWidget
    ttk.Notebook = FakeNotebook
    ttk.Style = FakeStyle
    tk.ttk = ttk

    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = lambda **k: ""
    filedialog.askdirectory = lambda **k: ""
    messagebox = types.ModuleType("tkinter.messagebox")
    for fn in ("showerror", "showinfo", "showwarning"):
        setattr(messagebox, fn, lambda *a, **k: None)
    messagebox.askyesno = lambda *a, **k: True
    messagebox.askyesnocancel = lambda *a, **k: True
    simpledialog = types.ModuleType("tkinter.simpledialog")
    simpledialog.askstring = lambda *a, **k: k.get("initialvalue", "x")

    tk.filedialog = filedialog
    tk.messagebox = messagebox
    tk.simpledialog = simpledialog
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.filedialog"] = filedialog
    sys.modules["tkinter.messagebox"] = messagebox
    sys.modules["tkinter.simpledialog"] = simpledialog


# --------------------------------------------------------------------------- #
#  Fake paramiko
# --------------------------------------------------------------------------- #
class FakeAttr:
    def __init__(self, name, is_dir, size=10, mtime=1700000000):
        self.filename = name
        self.st_mode = (stat.S_IFDIR | 0o755) if is_dir else (stat.S_IFREG | 0o644)
        self.st_size = size
        self.st_mtime = mtime


class FakeFile:
    def __init__(self, data=b"", store=None, path=None):
        self._data = data
        self._store = store
        self._path = path
        self._written = b""

    def read(self):
        return self._data

    def write(self, b):
        self._written += b if isinstance(b, bytes) else b.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if self._store is not None and self._path is not None and self._written:
            self._store[self._path] = self._written
        return False


class FakeSFTP:
    def __init__(self):
        self.files = {"/home/ec2-user/bot.py": b"print('hello')\n"}
        self.dirs = {"/home/ec2-user": [FakeAttr("sub", True), FakeAttr("bot.py", False, 14)]}
        self.written = {}

    def normalize(self, p):
        return "/home/ec2-user"

    def listdir_attr(self, path):
        return self.dirs.get(path, [])

    def open(self, path, mode):
        if "r" in mode:
            return FakeFile(self.files.get(path, b""))
        return FakeFile(store=self.written, path=path)

    def stat(self, p):
        raise IOError("no")

    def mkdir(self, p):
        pass

    def remove(self, p):
        self.files.pop(p, None)
        self.written.pop(p, None)


class FakeChannel:
    def recv_exit_status(self):
        return 0

    def shutdown_write(self):
        pass


class FakeStd:
    def __init__(self, data=b""):
        self._data = data
        self.channel = FakeChannel()

    def read(self):
        return self._data

    def write(self, b):
        pass

    def shutdown_write(self):
        pass


class FakeTransport:
    def is_active(self):
        return True

    def open_session(self):
        return FakeWidget()


class FakeSSH:
    def __init__(self):
        self.last_cmd = None
        self.cmds = []
        self.stdout_data = b"output line\n"

    def get_transport(self):
        return FakeTransport()

    def exec_command(self, cmd, timeout=None):
        self.last_cmd = cmd
        self.cmds.append(cmd)
        out = self.stdout_data
        if "__ATM_CWD__" in cmd:
            out = b"file_output\n__ATM_CWD__:/home/ec2-user/sub\n"
        return FakeStd(b""), FakeStd(out), FakeStd(b"")


def build_fake_paramiko():
    pm = types.ModuleType("paramiko")
    pm.SSHClient = lambda: FakeWidget()
    pm.AutoAddPolicy = lambda: None
    pm.RSAKey = type("RSAKey", (), {})
    pm.Ed25519Key = type("Ed25519Key", (), {})
    pm.ECDSAKey = type("ECDSAKey", (), {})
    pm.PasswordRequiredException = type("PasswordRequiredException", (Exception,), {})
    sys.modules["paramiko"] = pm


# --------------------------------------------------------------------------- #
#  Run tests
# --------------------------------------------------------------------------- #
def main():
    build_fake_tkinter()
    build_fake_paramiko()

    import tkinter as tk
    # Redirect persistence to temp files.
    tmp = tempfile.mkdtemp()
    import importlib
    mod = importlib.import_module("aws_telegram_manager")
    mod.PROFILE_FILE = os.path.join(tmp, "profiles.json")
    mod.SETTINGS_FILE = os.path.join(tmp, "settings.json")

    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(("PASS" if cond else "FAIL"), name)

    # T1: instantiate (covers all _build_* methods)
    app = mod.AwsTelegramManager(tk.Tk())
    check("instantiate app", True)

    # Make async synchronous.
    app._run_bg = lambda f, *a: f(*a)
    app._ui = lambda cb: cb()

    # Wire fakes.
    app.connected = True
    app.ssh_client = FakeSSH()
    app.sftp_client = FakeSFTP()
    app.current_path = "/home/ec2-user"

    # T2: list dir
    app._task_list_dir("/home/ec2-user")
    check("listing has 3 rows (.. + dir + file)", len(app._visible_entries) == 3)
    names = [e["name"] for e in app._visible_entries]
    check("listing names correct", names == ["..", "sub", "bot.py"])

    # T3: select the file (index 2) -> opens in editor (ListboxSelect)
    app.file_list.selection_set(2)
    app._on_list_select()
    check("file opened: active_file set", app.active_file == "/home/ec2-user/bot.py")
    check("editor has file content", "print('hello')" in app.editor.get())

    # T4: double-click folder (index 1) -> navigates
    app.file_list.selection_set(1)
    app._on_list_double_click()
    check("folder navigation changed path", app.current_path == "/home/ec2-user/sub")

    # reset path
    app.current_path = "/home/ec2-user"
    app._task_list_dir("/home/ec2-user")
    # T5: selecting '..' should not open
    app.file_list.selection_set(0)
    before = app.active_file
    app._on_list_select()
    check("select on '..' does not open", app.active_file == before)

    # T6: save file (sudo off)
    app.sudo_var = _Var(value=False)
    app.active_file = "/home/ec2-user/bot.py"
    app.editor.delete("1.0")
    app.editor.insert("1.0", "new content")
    app._task_save_file("/home/ec2-user/bot.py", "new content")
    check("save wrote via sftp", app.sftp_client.written.get("/home/ec2-user/bot.py") == b"new content")

    # T7: terminal exec updates cwd via sentinel
    app._term_busy = True
    app._task_terminal_exec("ls")
    check("terminal parsed new cwd", app.current_path == "/home/ec2-user/sub")
    check("terminal output shown", "file_output" in app.term.get())

    # T8: code runner
    app.current_path = "/home/ec2-user"
    app.code_editor.delete("1.0")
    app.code_editor.insert("1.0", "print(1)")
    app.interp_var = _Var(value="python3")
    app._task_run_code("python3", "print(1)")
    check("runner output shown", "output line" in app.runner_output.get())

    # T11b: large multi-line save (1000 lines) via SFTP
    big = "".join("line %d\n" % i for i in range(1000))
    app.active_file = "/home/ec2-user/big.py"
    app._task_save_file("/home/ec2-user/big.py", big)
    check("large file saved fully", app.sftp_client.written.get("/home/ec2-user/big.py") == big.encode())

    # T9: cron create
    app._task_cron_create("30 3 * * * echo hi")
    check("cron command sent", any(c.startswith("crontab /tmp/") for c in app.ssh_client.cmds))

    # T10: always-on create builds a unit and enables it
    app._task_aot_create("mybot", "/usr/bin/python3 bot.py", "ec2-user", "/home/ec2-user")
    check("aot enabled service", any("enable --now atm-mybot" in c for c in app.ssh_client.cmds))

    # T11: profiles save/load roundtrip
    app.host_var.set("1.2.3.4")
    app.port_var.set("2222")
    app.user_var.set("ubuntu")
    app.key_var.set("/k.pem")
    app.upload_target_var.set("/var/www")
    app.profile_var = _Var(value="p1")
    # call internal save logic directly
    profiles = {}
    profiles["p1"] = {
        "host": app.host_var.get(), "port": app.port_var.get(),
        "user": app.user_var.get(), "key": app.key_var.get(),
        "upload_target": app.upload_target_var.get(),
    }
    app._write_json(mod.PROFILE_FILE, profiles)
    loaded = app._read_json(mod.PROFILE_FILE)
    check("profile roundtrip", loaded["p1"]["host"] == "1.2.3.4")

    # Summary
    failed = [n for n, ok in results if not ok]
    print("\n==== %d passed, %d failed ====" % (len(results) - len(failed), len(failed)))
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
