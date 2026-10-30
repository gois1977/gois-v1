"""gois.v1 | Office & Command Center - Aplicação Desktop (v2.3)

Corre sempre como Administrador do Windows (pede elevação UAC ao abrir).
Tema claro/escuro, totais gois.v1/Gemini/DeepSeek sempre no topo, comparação ao vivo com gráficos, chat com histórico
pesquisável e anexos, Sala de Aula com cópia local e 120 skills integradas.
"""
import os
import re
import sys
import csv
import json
import time
import queue
import threading
import subprocess
import zipfile
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

try:
    import windnd  # arrastar-e-largar ficheiros (Windows)
except Exception:  # pragma: no cover
    windnd = None

HERE = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent)
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
try:
    from gois_skills import SKILLS as BUILTIN_SKILLS, CATEGORIES as DEFAULT_CATEGORIES
    SKILLS_ERROR = ""
except Exception as _e:  # gois_skills.py em falta/corrompido: a app abre na mesma e avisa
    SKILLS_ERROR = f"{type(_e).__name__}: {_e}"
    BUILTIN_SKILLS = []
    DEFAULT_CATEGORIES = [
        "Arquitetura de Software & Sistemas Assíncronos", "Algoritmos, Estruturas de Dados & Programação",
        "Game Design, Unity C# & Motores 3D", "WebGL, Three.js, Frontend & Gráficos 2D/3D",
        "Matemática Avançada, Lógica & Raciocínio Formal", "Inteligência Artificial, LLMs, RAG & Agentes",
        "Ciência de Dados, SQL & Engenharia de Dados", "Cibersegurança, Criptografia & Auditoria",
        "DevOps, Cloud, Docker & Sistemas Linux", "Engenharia, AutoCAD, Robótica & IoT",
        "Física, Química, Biologia & Ciências Exatas", "Multimodalidade, Visão Computacional & Áudio",
        "Linguística, Tradução & Comunicação Multilingue", "Economia, Finanças Quantitativas & Estratégia",
        "Direito Tecnológico, Ética & Segurança de IA", "Otimização Extrema & Hardware Leve / Edge AI",
    ]

APP_NAME = "gois.v1 | Office & Command Center"
DEFAULT_SERVER = "https://gois-v1.onrender.com"

DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "gois_v1"
CONFIG_FILE = DATA_DIR / "config.json"
SESSION_FILE = DATA_DIR / "multichats.json"
LOCAL_SKILLS_FILE = DATA_DIR / "skills.json"          # skills criadas por ti
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"          # cópia local das lições da Sala de Aula

APP_VERSION = "2.3"
COMP_CACHE_FILE = DATA_DIR / "settings_cache.json"      # última leitura do servidor (mostra dados mesmo offline)
HISTORY_FILE = DATA_DIR / "comparison_history.json"     # histórico dos totais para o gráfico de evolução

THEMES = {
    "dark": {
        "name": "dark", "bg": "#0d1117", "panel": "#161b22", "panel_alt": "#1c2330", "border": "#30363d",
        "fg": "#e6edf3", "muted": "#8b949e", "user": "#58a6ff", "bot": "#3fb950", "btn": "#21262d",
        "accent": "#238636", "accent_blue": "#1f6feb", "code_bg": "#010409", "code_fg": "#79c0ff",
        "warn": "#d29922", "danger": "#f85149", "user_bg": "#16233a",
        "s_gois": "#3fb950", "s_gemini": "#4493f8", "s_deepseek": "#b083f0",
    },
    "light": {
        "name": "light", "bg": "#f6f8fa", "panel": "#ffffff", "panel_alt": "#eaeef2", "border": "#d0d7de",
        "fg": "#1f2328", "muted": "#59636e", "user": "#0969da", "bot": "#1a7f37", "btn": "#eaeef2",
        "accent": "#1a7f37", "accent_blue": "#0969da", "code_bg": "#f6f8fa", "code_fg": "#0550ae",
        "warn": "#9a6700", "danger": "#cf222e", "user_bg": "#ddf4ff",
        "s_gois": "#1a7f37", "s_gemini": "#0969da", "s_deepseek": "#8250df",
    },
}
THEME = dict(THEMES["dark"])


def set_theme(name: str) -> None:
    THEME.clear()
    THEME.update(THEMES.get(name, THEMES["dark"]))


def series_color(key: str) -> str:
    return THEME["s_" + key]

EXT = {
    "python": "py", "py": "py", "javascript": "js", "js": "js", "typescript": "ts", "ts": "ts", "tsx": "tsx",
    "jsx": "jsx", "html": "html", "css": "css", "json": "json", "bash": "sh", "sh": "sh", "shell": "sh",
    "powershell": "ps1", "ps1": "ps1", "bat": "bat", "batch": "bat", "cmd": "bat", "c": "c", "cpp": "cpp",
    "c++": "cpp", "csharp": "cs", "cs": "cs", "c#": "cs", "java": "java", "go": "go", "rust": "rs", "rs": "rs",
    "sql": "sql", "yaml": "yml", "yml": "yml", "xml": "xml", "markdown": "md", "md": "md", "iss": "iss",
    "toml": "toml", "ini": "ini", "lua": "lua", "glsl": "glsl", "txt": "txt",
}
FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.S)
MAX_FILE = 200_000
BLOCKED_EXT = {".exe", ".dll", ".zip", ".7z", ".rar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf",
               ".mp3", ".mp4", ".wav", ".iso", ".bin", ".xlsx", ".pptx"}


# --------------------------------------------------------------------------- #
# Administrador do Windows
# --------------------------------------------------------------------------- #
def is_windows_admin() -> bool:
    if sys.platform != "win32":
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Reabre a app com o prompt UAC. True se o utilizador aceitou (o processo atual deve terminar)."""
    try:
        import ctypes
        if getattr(sys, "frozen", False):
            exe, args = sys.executable, sys.argv[1:]
        else:
            exe = sys.executable
            pyw = Path(exe).with_name("pythonw.exe")  # sem janela de consola
            exe = str(pyw) if pyw.exists() else exe
            args = [os.path.abspath(sys.argv[0])] + sys.argv[1:]
        params = " ".join(f'"{a}"' for a in args)
        workdir = str(Path(args[0]).parent) if args and not getattr(sys, "frozen", False) else None
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, workdir, 1) > 32
    except Exception:
        return False


def migrate_legacy_data(target: Path) -> None:
    """Ao correr como outro utilizador admin, o %APPDATA% muda e o histórico/skills/token 'desaparecem'.
    Procura pastas gois_v1 de outros perfis e junta o que falta aqui (nunca apaga nada)."""
    try:
        cands = {Path.home() / "AppData" / "Roaming" / "gois_v1"}
        users = Path(os.environ.get("SystemDrive", "C:") + os.sep) / "Users"
        if users.exists():
            cands |= set(users.glob("*/AppData/Roaming/gois_v1"))
        for src in cands:
            if not src.exists() or src.resolve() == target.resolve():
                continue
            for name, key in (("multichats.json", "id"), ("skills.json", "title"), ("knowledge.json", "id")):
                other = read_json(src / name, [])
                if not isinstance(other, list) or not other:
                    continue
                mine = read_json(target / name, [])
                mine = mine if isinstance(mine, list) else []
                have = {x.get(key) for x in mine if isinstance(x, dict)}
                extra = [x for x in other if isinstance(x, dict) and x.get(key) and x.get(key) not in have]
                if not extra:
                    continue
                if name == "multichats.json":  # descarta a conversa vazia criada por omissão
                    mine = [m for m in mine if m.get("messages")]
                write_json(target / name, mine + extra)
            if not (target / "config.json").exists() and (src / "config.json").exists():
                write_json(target / "config.json", read_json(src / "config.json", {}))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Utilitários
# --------------------------------------------------------------------------- #
def read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def split_blocks(text: str):
    """Divide uma resposta em blocos de texto e de código."""
    blocks, last = [], 0
    for m in FENCE_RE.finditer(text):
        if m.start() > last:
            blocks.append({"kind": "text", "text": text[last:m.start()]})
        info = m.group(1).strip().split()
        lang = (info[0] if info else "").lower()
        fname = next((p for p in info[1:] if "." in p), "")
        if "." in lang:
            fname, lang = lang, lang.rsplit(".", 1)[-1]
        blocks.append({"kind": "code", "lang": lang, "fname": fname, "code": m.group(2).rstrip("\n")})
        last = m.end()
    if last < len(text):
        blocks.append({"kind": "text", "text": text[last:]})
    return blocks


def read_attachment(path: str):
    """-> (conteúdo | None, aviso)"""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".docx":
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            with zipfile.ZipFile(p) as z:
                root = ET.fromstring(z.read("word/document.xml"))
            text = "\n".join("".join(t.text or "" for t in para.iter(ns + "t")) for para in root.iter(ns + "p"))
        elif ext in BLOCKED_EXT:
            return None, f"Tipo {ext} não suportado (só texto, código e .docx)."
        else:
            raw = p.read_bytes()[: MAX_FILE * 2]
            if b"\x00" in raw[:4096]:
                return None, "Parece um ficheiro binário."
            text = raw.decode("utf-8", errors="ignore")
    except Exception as e:
        return None, f"Não foi possível ler: {e}"
    note = ""
    if len(text) > MAX_FILE:
        text, note = text[:MAX_FILE], f"cortado aos primeiros {MAX_FILE // 1000} KB"
    return text, note


class ApiError(Exception):
    def __init__(self, msg, code=0):
        super().__init__(msg)
        self.code = code


class Api:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def call(self, method: str, path: str, body=None, timeout: float = 30):
        url = self.cfg["server"].rstrip("/") + path
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        token = (self.cfg.get("token") or "").encode("latin-1", "ignore").decode("latin-1")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Admin-Token": token,
                   "User-Agent": "gois-desktop/2.2"}
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("detail", "")
            except Exception:
                pass
            if e.code == 503 and "ADMIN_TOKEN" in (detail or ""):
                detail = "o servidor não tem ADMIN_TOKEN (Render > Environment)"
            elif e.code == 401:
                detail = "token de administrador não coincide com o do servidor"
            raise ApiError(f"HTTP {e.code}: {detail or e.reason}", e.code) from None
        except Exception as e:
            raise ApiError(f"Sem ligação ao servidor ({e})") from None
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            raise ApiError("Resposta inválida do servidor.") from None


# --------------------------------------------------------------------------- #
# Janela principal
# --------------------------------------------------------------------------- #
class GoisWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.cfg = self.load_config()
        set_theme(self.cfg.get("theme", "dark"))
        self.geometry("1380x840")
        self.minsize(1200, 700)
        self.configure(bg=THEME["bg"])
        self.api = Api(self.cfg)
        self.sessions = self.load_sessions()
        self.custom_skills = read_json(LOCAL_SKILLS_FILE, [])
        self.kb_cache = read_json(KNOWLEDGE_FILE, [])
        self.current_session_id = None
        self.attachments = []
        self.busy = False
        self.pending_sid = None
        self.last_error = None
        self.connected = False
        self.is_admin = False
        self.admin_configured = None
        self._warned_token = False
        self.use_skills = tk.BooleanVar(value=True)
        self.hist_filter = tk.StringVar()
        self.hist_filter.trace_add("write", lambda *_: self.refresh_session_list() if hasattr(self, "hist_list") else None)
        self.skill_search = tk.StringVar()
        self.skill_search.trace_add("write", lambda *_: self.refresh_skills() if hasattr(self, "skill_tree") else None)
        cached = read_json(COMP_CACHE_FILE, {})
        self.server_settings = cached if isinstance(cached, dict) else {}
        self.health = {}
        self.latency_ms = None
        self.last_live = 0.0
        self._live_busy = False
        self._live_after = self._bench_after = None
        self.auto_bench = tk.BooleanVar(value=False)  # gasta tokens: começa sempre desligado
        self.live_s = tk.StringVar(value=str(self.cfg.get("live_s", 10)))
        self.log_lines = []
        self.sel_cat = None
        hist = read_json(HISTORY_FILE, [])
        self.history = hist if isinstance(hist, list) else []
        self.kb = []
        self.class_busy = False
        self._emb = []
        self._conn_after = None
        self._poll_after = None
        self.ui_q = queue.Queue()

        self.setup_styles()
        self.build_ui()
        self.init_first_session()
        self.kb = list(self.kb_cache)  # Sala de Aula mostra já as lições guardadas neste PC, mesmo offline
        self.update_knowledge_ui()
        if self.server_settings:
            self.apply_settings(self.server_settings, cached=True)
        self.after(300, self.setup_drop)
        self.after(1000, self.clock_tick)
        self._live_after = self.after(3000, self.live_tick)
        self._bench_after = self.after(90_000, self.bench_tick)
        self.after(100, self.pump)
        self.after(400, self.first_run_token)
        if SKILLS_ERROR:
            self.after(600, lambda: messagebox.showwarning(
                "Skills integradas", "Não consegui carregar gois_skills.py (tem de estar na mesma pasta que gois_window.py).\n\n"
                + SKILLS_ERROR, parent=self))

    # ---------------- infraestrutura ----------------
    def load_config(self):
        migrate_legacy_data(DATA_DIR)
        cfg = read_json(CONFIG_FILE, {})
        if not isinstance(cfg, dict):
            cfg = {}
        cfg.setdefault("server", DEFAULT_SERVER)
        cfg.setdefault("token", "")
        return cfg

    def save_config(self):
        write_json(CONFIG_FILE, self.cfg)

    def pump(self):
        """Executa no thread da UI o que as threads de fundo pedem (tkinter não é thread-safe)."""
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(100, self.pump)

    def bg(self, work, done=None):
        def runner():
            try:
                res, err = work(), None
            except Exception as e:
                res, err = None, e
            if done:
                self.ui_q.put(lambda: done(res, err))
        threading.Thread(target=runner, daemon=True).start()

    def flash(self, text, color=None, ms=5000):
        try:
            self.flash_lbl.config(text=text, fg=color or THEME["muted"])
        except tk.TclError:
            return

        def clear():
            try:
                if self.flash_lbl.cget("text") == text:
                    self.flash_lbl.config(text="")
            except tk.TclError:
                pass

        self.after(ms, clear)

    def first_run_token(self):
        if not self.cfg.get("token"):
            tok = simpledialog.askstring(
                "Token de administrador",
                "Introduz o ADMIN_TOKEN definido no servidor (Render > Environment).\n"
                "Sem ele o Command Center fica em modo público.",
                show="•", parent=self)
            if tok:
                self.cfg["token"] = tok.strip()
                self.save_config()
        self.check_connection()

    # ---------------- estilos ----------------
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=THEME["bg"], borderwidth=0, tabmargins=[16, 8, 16, 0])
        style.configure("TNotebook.Tab", background=THEME["panel"], foreground=THEME["muted"],
                        padding=[16, 9], font=("Segoe UI Semibold", 9), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", THEME["accent_blue"]), ("active", THEME["panel_alt"])],
                  foreground=[("selected", "#ffffff"), ("active", THEME["fg"])])
        style.configure("Treeview", background=THEME["panel"], fieldbackground=THEME["panel"],
                        foreground=THEME["fg"], rowheight=28, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=THEME["panel_alt"], foreground=THEME["fg"],
                        font=("Segoe UI Semibold", 9), borderwidth=0, relief="flat")
        style.map("Treeview", background=[("selected", THEME["accent_blue"])], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", THEME["border"])])
        style.configure("Vertical.TScrollbar", background=THEME["btn"], troughcolor=THEME["bg"], borderwidth=0,
                        arrowcolor=THEME["muted"])
        style.configure("TCombobox", fieldbackground=THEME["code_bg"], background=THEME["btn"], foreground=THEME["fg"],
                        arrowcolor=THEME["fg"], bordercolor=THEME["border"])
        style.map("TCombobox", fieldbackground=[("readonly", THEME["code_bg"])], foreground=[("readonly", THEME["fg"])])
        self.option_add("*TCombobox*Listbox.background", THEME["panel"])
        self.option_add("*TCombobox*Listbox.foreground", THEME["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", THEME["accent_blue"])

    def btn(self, parent, text, cmd, color=None, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=color or THEME["btn"], fg="#ffffff" if color else THEME["fg"],
                         relief=tk.FLAT, bd=0, highlightthickness=0, activebackground=THEME["accent_blue"],
                         activeforeground="#ffffff", font=kw.pop("font", ("Segoe UI Semibold", 9)), cursor="hand2",
                         padx=kw.pop("padx", 12), pady=kw.pop("pady", 5), **kw)

    def card(self, parent, **kw):
        return tk.Frame(parent, bg=THEME["panel"], highlightbackground=THEME["border"], highlightthickness=1, **kw)

    def kpi_card(self, parent, key, title, color=None, big=False):
        f = self.card(parent)
        f.pack(side=tk.LEFT, padx=(0, 8), fill=tk.Y)
        top = tk.Frame(f, bg=THEME["panel"])
        top.pack(fill=tk.X, padx=12, pady=(8, 0))
        if color:
            tk.Label(top, text="●", fg=color, bg=THEME["panel"], font=("Segoe UI", 10)).pack(side=tk.LEFT)
        tk.Label(top, text=title, fg=THEME["muted"], bg=THEME["panel"], font=("Segoe UI Semibold", 9)).pack(side=tk.LEFT, padx=(4 if color else 0, 0))
        val = tk.Label(f, text="—", fg=color or THEME["fg"], bg=THEME["panel"], font=("Segoe UI Semibold", 22 if big else 14))
        val.pack(anchor=tk.W, padx=12)
        sub = tk.Label(f, text="", fg=THEME["muted"], bg=THEME["panel"], font=("Segoe UI", 8))
        sub.pack(anchor=tk.W, padx=12, pady=(0, 8))
        self.kpi[key] = (val, sub)

    # ---------------- UI base ----------------
    def build_ui(self):
        self.kpi = {}
        header = tk.Frame(self, bg=THEME["panel"], height=54, highlightbackground=THEME["border"], highlightthickness=1)
        header.pack(side=tk.TOP, fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=" g ", bg=THEME["accent"], fg="#ffffff", font=("Segoe UI Semibold", 14)).pack(side=tk.LEFT, padx=(16, 8), pady=10)
        tk.Label(header, text="gois.v1", bg=THEME["panel"], fg=THEME["fg"], font=("Segoe UI Semibold", 13)).pack(side=tk.LEFT)
        tk.Label(header, text="Office & Command Center", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=8)
        self.btn(header, "⚙ Definições", self.open_settings).pack(side=tk.RIGHT, padx=(6, 14), pady=10)
        self.theme_btn = self.btn(header, "☀ Claro" if THEME["name"] == "dark" else "🌙 Escuro", self.toggle_theme)
        self.theme_btn.pack(side=tk.RIGHT, padx=6, pady=10)
        self.status_lbl = tk.Label(header, text="⏳ a ligar…", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI Semibold", 9))
        self.status_lbl.pack(side=tk.RIGHT, padx=10)
        self.live_lbl = tk.Label(header, text="● …", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI Semibold", 9))
        self.live_lbl.pack(side=tk.RIGHT, padx=10)
        self.flash_lbl = tk.Label(header, text="", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9))
        self.flash_lbl.pack(side=tk.RIGHT, padx=10)
        if sys.platform == "win32":
            ok = is_windows_admin()
            tk.Label(header, text="🛡 Administrador" if ok else "⚠ sem Administrador", bg=THEME["panel"],
                     fg=THEME["bot"] if ok else THEME["warn"], font=("Segoe UI Semibold", 9)).pack(side=tk.RIGHT, padx=10)

        # barra de estado (rodapé)
        bar = tk.Frame(self, bg=THEME["panel"], highlightbackground=THEME["border"], highlightthickness=1)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Label(bar, text=f"📁 {DATA_DIR}", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=12, pady=3)
        self.foot_r = tk.Label(bar, text="", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 8))
        self.foot_r.pack(side=tk.RIGHT, padx=12)

        # totais gerais sempre visíveis
        strip = tk.Frame(self, bg=THEME["bg"])
        strip.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(10, 2))
        self.kpi_card(strip, "gois", "gois.v1 · total geral", series_color("gois"), big=True)
        self.kpi_card(strip, "gemini", "Gemini · total geral", series_color("gemini"), big=True)
        self.kpi_card(strip, "deepseek", "DeepSeek · total geral", series_color("deepseek"), big=True)
        tk.Frame(strip, bg=THEME["border"], width=1).pack(side=tk.LEFT, fill=tk.Y, padx=(2, 10), pady=4)
        self.kpi_card(strip, "par_gem", "Paridade vs Gemini")
        self.kpi_card(strip, "par_ds", "Paridade vs DeepSeek")
        self.kpi_card(strip, "lessons", "Lições")
        self.kpi_card(strip, "skills", "Skills")
        self.kpi_card(strip, "tokens", "Tokens hoje")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tab_chat = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_comp = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_class = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_skills = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_sys = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_chat, text="💬 Chat & Histórico")
        self.notebook.add(self.tab_comp, text="📊 Comparação Live")
        self.notebook.add(self.tab_class, text="🏫 Sala de Aula")
        self.notebook.add(self.tab_skills, text="🧠 Skills")
        self.notebook.add(self.tab_sys, text="🛠 Sistema")

        self.build_chat_tab()
        self.build_comparison_tab()
        self.build_classroom_tab()
        self.build_skills_tab()
        self.build_system_tab()
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

    def toggle_theme(self):
        name = "light" if THEME["name"] == "dark" else "dark"
        self.cfg["theme"] = name
        self.save_config()
        set_theme(name)
        self.rebuild_ui()

    def rebuild_ui(self):
        """Recria a interface com o tema novo, mantendo o rascunho, o separador e todos os dados."""
        draft, tab = "", 0
        try:
            draft = self.msg_entry.get("1.0", "end-1c")
            tab = self.notebook.index(self.notebook.select())
        except Exception:
            pass
        for w in self.winfo_children():
            w.destroy()
        self._emb = []
        self.configure(bg=THEME["bg"])
        self.setup_styles()
        self.build_ui()
        if draft:
            self.msg_entry.insert("1.0", draft)
        self.render_chips()
        self.refresh_session_list()
        self.render_messages()
        self.update_knowledge_ui()
        if self.server_settings:
            self.apply_settings(self.server_settings, cached=True)
        self.class_log_restore()
        self.update_kpis()
        self.refresh_system_tab()
        self.refresh_live_ui()
        try:
            self.notebook.select(tab)
        except Exception:
            pass
        self.check_connection()

    # ======================================================================= #
    # LIGAÇÃO AO SERVIDOR
    # ======================================================================= #
    def set_status(self, text, color):
        try:
            self.status_lbl.config(text=text[:90], fg=color)
        except tk.TclError:
            pass

    def check_connection(self):
        if self._conn_after:
            self.after_cancel(self._conn_after)
            self._conn_after = None
        self.set_status("🟡 a ligar… (o Render pode demorar ~1 min a acordar)", THEME["warn"])

        def done(res, err):
            was_connected = self.connected
            if err:
                self.connected = self.is_admin = False
                self.set_status(f"🔴 offline — {err}"[:110], THEME["danger"])
                self.class_status.config(text=f"🔴 Sala de Aula sem ligação: {err}\nA mostrar as lições guardadas neste PC; "
                                              "volta a tentar a cada 20 s.", fg=THEME["danger"])
                delay = 20_000
            else:
                res, ms = res
                self.latency_ms, self.health = ms, res
                self.connected = True
                self.is_admin = bool(res.get("admin"))
                self.admin_configured = bool(res.get("admin_configured"))
                if not self.admin_configured:
                    self.set_status("🟠 online · ADMIN_TOKEN não definido no servidor", THEME["warn"])
                    self.class_status.config(text="🟠 O servidor responde, mas não tem ADMIN_TOKEN — por isso a Sala de Aula, as skills e o "
                                                  "benchmark ficam bloqueados.\nRender > gois-v1 > Environment > Add: ADMIN_TOKEN = "
                                                  "(a tua palavra-passe) > Save; depois ⚙ Definições com a mesma palavra-passe.", fg=THEME["warn"])
                    self.warn_no_token_once()
                elif self.is_admin:
                    self.set_status(f"🟢 online · admin · v{res.get('version', '?')}", THEME["bot"])
                else:
                    self.set_status("🟠 online · token inválido (⚙ Definições)", THEME["warn"])
                    self.class_status.config(text="🟠 O token guardado neste PC não coincide com o ADMIN_TOKEN do servidor (⚙ Definições).",
                                             fg=THEME["warn"])
                if self.is_admin:
                    # o plano gratuito do Render tem disco efémero: se o servidor perdeu skills/lições, repõe a partir deste PC
                    lost = res.get("skills", 0) < len(self.all_skills()) or res.get("lessons", 0) < len(self.kb_cache)
                    if lost or not was_connected:
                        self.sync_skills()
                        self.refresh_classroom()
                        self.fetch_live()
                delay = 240_000  # ping periódico: também impede o Render de adormecer enquanto a app está aberta
            self._conn_after = self.after(delay, self.check_connection)
            self.refresh_system_tab()
            self.refresh_live_ui()

        def probe():
            t0 = time.time()
            h = self.api.call("GET", "/api/health", timeout=80)
            return h, int((time.time() - t0) * 1000)

        self.bg(probe, done)

    def warn_no_token_once(self):
        if self._warned_token:
            return
        self._warned_token = True
        messagebox.showinfo(
            "ADMIN_TOKEN em falta no servidor",
            "O servidor gois-v1 está online mas não tem a variável ADMIN_TOKEN, por isso rejeita tudo o que é de administrador "
            "(Sala de Aula, skills, benchmark). O chat continua a funcionar porque é público.\n\n"
            "1) render.com > gois-v1 > Environment > Add Environment Variable\n"
            "2) ADMIN_TOKEN = uma palavra-passe tua  >  Save (o Render faz redeploy)\n"
            "3) Aqui: ⚙ Definições > Token de administrador = a mesma palavra-passe", parent=self)

    def on_tab_changed(self, _e=None):
        tab = self.notebook.index(self.notebook.select())
        if tab in (1, 2) and self.is_admin:
            self.refresh_classroom()
        if tab == 1:
            self.after(50, self.draw_all_charts)
        if tab == 4:
            self.refresh_system_tab()
        self.schedule_poll(tab == 2)

    def schedule_poll(self, on):
        if self._poll_after:
            self.after_cancel(self._poll_after)
            self._poll_after = None
        if on:
            self._poll_after = self.after(8000, self.poll_tick)

    def poll_tick(self):
        self._poll_after = None
        if self.notebook.index(self.notebook.select()) == 2:
            if self.is_admin and not self.class_busy:
                self.refresh_classroom()
            self.schedule_poll(True)

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("Definições")
        win.configure(bg=THEME["panel"])
        win.geometry("560x540")
        win.transient(self)
        win.grab_set()

        def row(label, value="", show=None):
            tk.Label(win, text=label, bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(anchor=tk.W, padx=18, pady=(12, 2))
            e = tk.Entry(win, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white", relief=tk.FLAT, show=show)
            e.insert(0, value)
            e.pack(fill=tk.X, padx=18, ipady=5)
            return e

        e_server = row("URL do servidor", self.cfg["server"])
        e_token = row("Token de administrador (igual ao ADMIN_TOKEN do servidor)", self.cfg.get("token", ""), show="•")

        def save():
            self.cfg["server"] = e_server.get().strip().rstrip("/") or DEFAULT_SERVER
            self.cfg["token"] = e_token.get().strip()
            self.save_config()
            self.api.cfg = self.cfg
            self.connected = False
            self.check_connection()
            win.destroy()

        self.btn(win, "Guardar e reconectar", save, THEME["accent"]).pack(anchor=tk.W, padx=18, pady=12)

        tk.Frame(win, bg=THEME["border"], height=1).pack(fill=tk.X, padx=18, pady=6)
        tk.Label(win, text="Chave API para o servidor", bg=THEME["panel"], fg=THEME["fg"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, padx=18)
        frm = tk.Frame(win, bg=THEME["panel"])
        frm.pack(fill=tk.X, padx=18, pady=6)
        prov = ttk.Combobox(frm, values=["Groq", "Gemini", "OpenRouter", "Mistral"], state="readonly", width=12)
        prov.set("Groq")
        prov.pack(side=tk.LEFT)
        e_key = tk.Entry(frm, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white", relief=tk.FLAT, show="•")
        e_key.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=5)
        info = tk.Label(win, text="", bg=THEME["panel"], fg=THEME["muted"], justify=tk.LEFT, wraplength=520, font=("Segoe UI", 9))
        info.pack(anchor=tk.W, padx=18)

        def send_key():
            k = e_key.get().strip()
            if not k:
                return

            def done(res, err):
                info.config(text=f"❌ {err}" if err else "✅ Enviada. Fica só em memória no servidor — para ser permanente define-a em Render > Environment.")
                if not err:
                    e_key.delete(0, tk.END)

            self.bg(lambda: self.api.call("POST", "/api/keys", {"provider": prov.get(), "key": k}), done)

        self.btn(frm, "Enviar", send_key, THEME["accent_blue"]).pack(side=tk.LEFT)

        def show_status():
            def done(res, err):
                if err:
                    info.config(text=f"❌ {err}")
                else:
                    info.config(text="Chaves: " + " · ".join(f"{n}: {'✅' if v.get('configured') else '—'}" for n, v in res.items()))

            self.bg(lambda: self.api.call("GET", "/api/keys/status"), done)

        def diagnose():
            info.config(text="⏳ a testar a ligação…", fg=THEME["muted"])

            def work():
                h = self.api.call("GET", "/api/health", timeout=80)
                return "\n".join([
                    f"✅ Servidor online (v{h.get('version', '?')}) · skills no servidor: {h.get('skills', 0)} · lições: {h.get('lessons', 0)}",
                    "ADMIN_TOKEN no servidor: " + ("sim" if h.get("admin_configured") else "NÃO — define-o no Render > Environment"),
                    "Token deste PC: " + ("válido ✅" if h.get("admin") else "inválido ❌"),
                    "Windows: " + ("a correr como Administrador 🛡" if is_windows_admin() else "sem Administrador ⚠"),
                ])

            self.bg(work, lambda r, e: info.config(text=f"❌ {e}" if e else r, fg=THEME["danger"] if e else THEME["fg"]))

        rowb = tk.Frame(win, bg=THEME["panel"])
        rowb.pack(anchor=tk.W, padx=18, pady=6)
        self.btn(rowb, "Ver estado das chaves", show_status).pack(side=tk.LEFT)
        self.btn(rowb, "🔌 Testar ligação", diagnose, THEME["accent_blue"]).pack(side=tk.LEFT, padx=6)

    # ======================================================================= #
    # CHAT
    # ======================================================================= #
    def load_sessions(self):
        data = read_json(SESSION_FILE, [])
        if isinstance(data, list) and data:
            return data
        return [{"id": "default", "title": "Nova conversa", "messages": []}]

    def save_sessions(self):
        write_json(SESSION_FILE, self.sessions)

    def cur_session(self):
        return next((s for s in self.sessions if s["id"] == self.current_session_id), None)

    def build_chat_tab(self):
        pane = tk.PanedWindow(self.tab_chat, orient=tk.HORIZONTAL, bg=THEME["border"], sashwidth=4)
        pane.pack(fill=tk.BOTH, expand=True)

        side = tk.Frame(pane, bg=THEME["panel"], width=260)
        pane.add(side, minsize=200)
        tk.Label(side, text="Histórico de Chats", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, padx=10, pady=8)
        self.btn(side, "+ Nova Conversa", self.new_chat_session, THEME["accent_blue"]).pack(fill=tk.X, padx=10, pady=3)
        row = tk.Frame(side, bg=THEME["panel"])
        row.pack(fill=tk.X, padx=10, pady=3)
        self.btn(row, "✏ Renomear", self.rename_session, padx=6).pack(side=tk.LEFT, expand=True, fill=tk.X)
        self.btn(row, "🗑 Apagar", self.delete_session, padx=6).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
        self.btn(side, "⬇ Exportar (.md)", self.export_session).pack(fill=tk.X, padx=10, pady=3)
        self.btn(side, "🧹 Apagar todas", self.delete_all_sessions).pack(fill=tk.X, padx=10, pady=3)
        srow = tk.Frame(side, bg=THEME["panel"])
        srow.pack(fill=tk.X, padx=10, pady=(6, 0))
        tk.Label(srow, text="🔎", bg=THEME["panel"], fg=THEME["muted"]).pack(side=tk.LEFT)
        tk.Entry(srow, textvariable=self.hist_filter, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white",
                 relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, ipady=3)
        lb_frame = tk.Frame(side, bg=THEME["panel"])
        lb_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        sb = ttk.Scrollbar(lb_frame, orient="vertical")
        self.hist_list = tk.Listbox(lb_frame, bg=THEME["panel"], fg=THEME["fg"], selectbackground=THEME["accent_blue"],
                                    selectforeground="#ffffff", relief=tk.FLAT, highlightthickness=0, activestyle="none",
                                    font=("Segoe UI", 10), yscrollcommand=sb.set, exportselection=False)
        sb.config(command=self.hist_list.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.hist_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.hist_list.bind("<<ListboxSelect>>", self.on_history_select)
        self.hist_list.bind("<Delete>", lambda e: self.delete_session())
        self.session_ids = []

        area = tk.Frame(pane, bg=THEME["bg"])
        pane.add(area, minsize=560)

        bottom = tk.Frame(area, bg=THEME["panel"])
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.chips = tk.Frame(bottom, bg=THEME["panel"])
        self.chips.pack(fill=tk.X, padx=10, pady=(6, 0))
        inrow = tk.Frame(bottom, bg=THEME["panel"])
        inrow.pack(fill=tk.X, padx=8, pady=6)
        ab = tk.Frame(inrow, bg=THEME["panel"])
        ab.pack(side=tk.LEFT, padx=(2, 6))
        self.btn(ab, "📎 Anexar", self.attach_dialog).pack(fill=tk.X)
        self.btn(ab, "📁 Pasta", self.attach_folder).pack(fill=tk.X, pady=(3, 0))
        self.msg_entry = tk.Text(inrow, height=3, bg=THEME["code_bg"], fg=THEME["fg"], font=("Segoe UI", 10),
                                 insertbackground="white", relief=tk.FLAT, padx=8, pady=6, wrap=tk.WORD)
        self.msg_entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.msg_entry.bind("<Return>", self.on_enter_pressed)
        self.send_btn = self.btn(inrow, "Enviar 🚀", self.send_message, THEME["accent"], font=("Segoe UI", 10, "bold"))
        self.send_btn.pack(side=tk.RIGHT, padx=(6, 2))
        hint = "Enter envia · Shift+Enter nova linha · arrasta ficheiros para a janela" if windnd else \
            "Enter envia · Shift+Enter nova linha · usa 📎 para anexar (arrastar indisponível)"
        hrow = tk.Frame(bottom, bg=THEME["panel"])
        hrow.pack(fill=tk.X, padx=12, pady=(0, 4))
        self.hint_lbl = tk.Label(hrow, text=hint, bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 8))
        self.hint_lbl.pack(side=tk.LEFT)
        tk.Checkbutton(hrow, text="🧠 usar skills relevantes no chat", variable=self.use_skills, bg=THEME["panel"], fg=THEME["muted"],
                       selectcolor=THEME["code_bg"], activebackground=THEME["panel"], activeforeground=THEME["fg"],
                       font=("Segoe UI", 8)).pack(side=tk.RIGHT)
        if not windnd and sys.platform == "win32":
            self.btn(hrow, "Ativar arrastar (pip install windnd)", self.install_windnd, padx=6, pady=0,
                     font=("Segoe UI", 8)).pack(side=tk.RIGHT, padx=8)

        sbc = ttk.Scrollbar(area, orient="vertical")
        self.chat_text = tk.Text(area, bg=THEME["bg"], fg=THEME["fg"], wrap=tk.WORD, state=tk.DISABLED, relief=tk.FLAT,
                                 padx=16, pady=10, font=("Segoe UI", 10), cursor="arrow", highlightthickness=0,
                                 yscrollcommand=sbc.set, spacing3=2)
        sbc.config(command=self.chat_text.yview)
        sbc.pack(side=tk.RIGHT, fill=tk.Y)
        self.chat_text.pack(fill=tk.BOTH, expand=True)
        t = self.chat_text
        t.tag_configure("who_user", foreground=THEME["user"], font=("Segoe UI", 9, "bold"), spacing1=12)
        t.tag_configure("who_bot", foreground=THEME["bot"], font=("Segoe UI", 9, "bold"), spacing1=12)
        t.tag_configure("meta", foreground=THEME["muted"], font=("Segoe UI", 8))
        t.tag_configure("user_body", background=THEME["user_bg"], lmargin1=10, lmargin2=10, rmargin=10)
        t.tag_configure("bot_body", lmargin1=10, lmargin2=10, rmargin=10)
        t.tag_configure("err", foreground=THEME["danger"], lmargin1=10, lmargin2=10)

    # ---- histórico ----
    def init_first_session(self):
        self.switch_session(self.sessions[0]["id"])

    def touch(self, sess):
        sess["updated"] = time.time()

    def refresh_session_list(self):
        self.sessions.sort(key=lambda x: x.get("updated", 0), reverse=True)  # mais recentes primeiro (ordenação estável)
        q = self.hist_filter.get().strip().lower()

        def match(x):
            return not q or q in x["title"].lower() or any(q in m.get("text", "").lower() for m in x["messages"])

        shown = [x for x in self.sessions if match(x)]
        self.hist_list.delete(0, tk.END)
        self.session_ids = [x["id"] for x in shown]
        for x in shown:
            when = time.strftime("%d/%m %H:%M", time.localtime(x["updated"])) if x.get("updated") else ""
            self.hist_list.insert(tk.END, f"  {x['title']}" + (f"   · {when}" if when else ""))
        if self.current_session_id in self.session_ids:
            i = self.session_ids.index(self.current_session_id)
            self.hist_list.selection_clear(0, tk.END)
            self.hist_list.selection_set(i)
            self.hist_list.see(i)

    def on_history_select(self, _e=None):
        sel = self.hist_list.curselection()
        if sel and sel[0] < len(self.session_ids):
            sid = self.session_ids[sel[0]]
            if sid != self.current_session_id:
                self.switch_session(sid)

    def new_chat_session(self):
        sid = f"s{int(time.time() * 1000)}"
        self.sessions.insert(0, {"id": sid, "title": "Nova conversa", "messages": [], "updated": time.time()})
        self.save_sessions()
        self.last_error = None
        self.switch_session(sid)

    def switch_session(self, sid):
        self.current_session_id = sid
        self.refresh_session_list()
        self.render_messages()

    def rename_session(self):
        s = self.cur_session()
        if not s:
            return
        name = simpledialog.askstring("Renomear", "Novo nome da conversa:", initialvalue=s["title"], parent=self)
        if name and name.strip():
            s["title"] = name.strip()[:60]
            self.touch(s)
            self.save_sessions()
            self.refresh_session_list()

    def delete_session(self):
        s = self.cur_session()
        if not s or not messagebox.askyesno("Apagar", f"Apagar a conversa «{s['title']}»?", parent=self):
            return
        self.sessions = [x for x in self.sessions if x["id"] != s["id"]]
        if not self.sessions:
            self.sessions = [{"id": f"s{int(time.time() * 1000)}", "title": "Nova conversa", "messages": []}]
        self.save_sessions()
        self.switch_session(self.sessions[0]["id"])

    def delete_all_sessions(self):
        if not messagebox.askyesno("Apagar tudo", "Apagar TODAS as conversas? Isto não pode ser desfeito.", parent=self):
            return
        sid = f"s{int(time.time() * 1000)}"
        self.sessions = [{"id": sid, "title": "Nova conversa", "messages": [], "updated": time.time()}]
        self.save_sessions()
        self.switch_session(sid)

    def export_session(self):
        s = self.cur_session()
        if not s or not s["messages"]:
            return
        path = filedialog.asksaveasfilename(initialfile=re.sub(r"[^\w\-]+", "_", s["title"]) + ".md", defaultextension=".md",
                                            filetypes=[("Markdown", "*.md"), ("Todos", "*.*")])
        if path:
            md = "\n".join(f"## {'Tu' if m['role'] == 'user' else 'gois.v1'}\n\n{m['text']}\n" for m in s["messages"])
            Path(path).write_text(md, encoding="utf-8")
            self.flash(f"💾 Guardado: {path}", THEME["bot"])

    # ---- renderização ----
    def copy_text(self, text, btn=None):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        if btn is not None:
            old = btn.cget("text")
            btn.config(text="✅ Copiado!")
            self.after(1500, lambda: btn.winfo_exists() and btn.config(text=old))

    def save_code(self, code, lang, fname):
        ext = EXT.get(lang, "txt")
        path = filedialog.asksaveasfilename(initialfile=fname or f"codigo.{ext}", defaultextension="." + ext,
                                            filetypes=[(ext.upper() + " file", "*." + ext), ("Todos os ficheiros", "*.*")])
        if path:
            Path(path).write_text(code, encoding="utf-8")
            self.flash(f"💾 Guardado: {path}", THEME["bot"])

    def save_all_code(self, blocks):
        folder = filedialog.askdirectory(title="Pasta onde guardar os ficheiros")
        if not folder:
            return
        for i, b in enumerate(blocks, 1):
            name = b["fname"] or f"codigo_{i}.{EXT.get(b['lang'], 'txt')}"
            Path(folder, Path(name).name).write_text(b["code"], encoding="utf-8")
        self.flash(f"💾 {len(blocks)} ficheiros guardados em {folder}", THEME["bot"])

    def _forward_wheel(self, widget):
        widget.bind("<MouseWheel>", lambda e: (self.chat_text.yview_scroll(int(-e.delta / 120), "units"), "break")[1])

    def make_code_block(self, blk):
        t = self.chat_text
        fr = tk.Frame(t, bg=THEME["code_bg"], highlightbackground=THEME["border"], highlightthickness=1)
        head = tk.Frame(fr, bg=THEME["panel_alt"])
        head.pack(fill=tk.X)
        title = blk["fname"] or (blk["lang"] or "código")
        tk.Label(head, text=title.upper() if not blk["fname"] else title, bg=THEME["panel_alt"], fg=THEME["muted"],
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=8)
        cp = tk.Button(head, text="📋 Copiar", bg=THEME["btn"], fg=THEME["fg"], relief=tk.FLAT, font=("Segoe UI", 8, "bold"), cursor="hand2")
        cp.config(command=lambda: self.copy_text(blk["code"], cp))
        cp.pack(side=tk.RIGHT, padx=3, pady=3)
        tk.Button(head, text="💾 Download", bg=THEME["accent_blue"], fg="#ffffff", relief=tk.FLAT, font=("Segoe UI", 8, "bold"),
                  cursor="hand2", command=lambda: self.save_code(blk["code"], blk["lang"], blk["fname"])).pack(side=tk.RIGHT, padx=3, pady=3)
        lines = blk["code"].count("\n") + 1
        long_ = lines > 22
        body = tk.Text(fr, height=min(lines, 22), width=92, bg=THEME["code_bg"], fg=THEME["code_fg"], font=("Consolas", 10),
                       wrap=tk.WORD, relief=tk.FLAT, padx=8, pady=6, highlightthickness=0)
        body.insert("1.0", blk["code"])
        body.config(state=tk.DISABLED)
        if long_:
            vs = ttk.Scrollbar(fr, orient="vertical", command=body.yview)
            body.config(yscrollcommand=vs.set)
            vs.pack(side=tk.RIGHT, fill=tk.Y)
        else:
            self._forward_wheel(body)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._forward_wheel(head)
        self._emb.append(fr)
        return fr

    def render_messages(self):
        t = self.chat_text
        for w in self._emb:
            try:
                w.destroy()
            except Exception:
                pass
        self._emb = []
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        s = self.cur_session()
        if not s:
            t.config(state=tk.DISABLED)
            return
        if not s["messages"]:
            t.insert(tk.END, "🟢 gois.v1\n", "who_bot")
            t.insert(tk.END, "Olá! Escreve uma mensagem, anexa ficheiros (📎 ou arrasta) e eu respondo com o histórico desta conversa.\n", "bot_body")
        for m in s["messages"]:
            if m["role"] == "user":
                t.insert(tk.END, "👤 Tu\n", "who_user")
                t.insert(tk.END, m["text"] + "\n", "user_body")
                if m.get("attachments"):
                    t.insert(tk.END, "📎 " + "  ·  ".join(a["name"] for a in m["attachments"]) + "\n", "meta")
            else:
                t.insert(tk.END, "🟢 gois.v1", "who_bot")
                if m.get("model"):
                    t.insert(tk.END, "   " + m["model"], "meta")
                t.insert(tk.END, "\n")
                blocks = split_blocks(m["text"])
                for b in blocks:
                    if b["kind"] == "text":
                        if b["text"].strip():
                            t.insert(tk.END, b["text"].strip("\n") + "\n", "bot_body")
                    else:
                        t.window_create(tk.END, window=self.make_code_block(b), padx=10, pady=4)
                        t.insert(tk.END, "\n")
                self.insert_message_actions(m, [b for b in blocks if b["kind"] == "code"])
        if self.busy and self.pending_sid == self.current_session_id:
            t.insert(tk.END, "🟢 gois.v1\n", "who_bot")
            t.insert(tk.END, "⏳ A pensar…\n", "meta")
        if self.last_error and self.last_error[0] == self.current_session_id:
            t.insert(tk.END, "❌ " + self.last_error[1] + "\n", "err")
            rb = tk.Button(t, text="↻ Tentar novamente", bg=THEME["accent_blue"], fg="#ffffff", relief=tk.FLAT,
                           font=("Segoe UI", 9, "bold"), cursor="hand2", command=self.retry_last)
            self._emb.append(rb)
            t.window_create(tk.END, window=rb, padx=10, pady=4)
            t.insert(tk.END, "\n")
        t.config(state=tk.DISABLED)
        t.see(tk.END)

    def insert_message_actions(self, m, code_blocks):
        t = self.chat_text
        fr = tk.Frame(t, bg=THEME["bg"])
        c = tk.Button(fr, text="📋 Copiar resposta", bg=THEME["btn"], fg=THEME["fg"], relief=tk.FLAT, font=("Segoe UI", 8), cursor="hand2")
        c.config(command=lambda: self.copy_text(m["text"], c))
        c.pack(side=tk.LEFT, padx=2)
        tk.Button(fr, text="💾 Guardar resposta (.md)", bg=THEME["btn"], fg=THEME["fg"], relief=tk.FLAT, font=("Segoe UI", 8),
                  cursor="hand2", command=lambda: self.save_code(m["text"], "md", "resposta.md")).pack(side=tk.LEFT, padx=2)
        if len(code_blocks) > 1:
            tk.Button(fr, text=f"📦 Guardar os {len(code_blocks)} ficheiros", bg=THEME["btn"], fg=THEME["fg"], relief=tk.FLAT,
                      font=("Segoe UI", 8), cursor="hand2", command=lambda: self.save_all_code(code_blocks)).pack(side=tk.LEFT, padx=2)
        self._emb.append(fr)
        self._forward_wheel(fr)
        t.window_create(tk.END, window=fr, padx=10, pady=2)
        t.insert(tk.END, "\n")

    # ---- anexos ----
    def setup_drop(self):
        if sys.platform != "win32":
            return
        try:  # com a app elevada (admin) o Windows bloqueia por omissão o arrastar vindo do Explorer (UIPI)
            import ctypes
            self.update_idletasks()
            hwnds = {self.winfo_id(), ctypes.windll.user32.GetParent(self.winfo_id())}
            for h in hwnds:
                for msg in (0x233, 0x4A, 0x49):  # WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA
                    ctypes.windll.user32.ChangeWindowMessageFilterEx(h, msg, 1, None)
        except Exception:
            pass
        if windnd:
            try:
                windnd.hook_dropfiles(self, func=self.on_drop, force_unicode=True)
            except TypeError:  # versões antigas do windnd não têm force_unicode
                try:
                    windnd.hook_dropfiles(self, func=self.on_drop)
                except Exception:
                    pass
            except Exception:
                pass

    def install_windnd(self):
        self.flash("⏳ a instalar windnd…", THEME["warn"], 20000)

        def work():
            exe = sys.executable
            if getattr(sys, "frozen", False):
                raise RuntimeError("Na versão .exe inclui o windnd no build (pip install windnd antes do PyInstaller).")
            r = subprocess.run([exe, "-m", "pip", "install", "windnd"], capture_output=True, text=True, timeout=180)
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout)[-200:])

        def done(_r, err):
            if err:
                self.flash(f"❌ {err}", THEME["danger"], 12000)
            else:
                messagebox.showinfo("windnd", "Instalado. Fecha e abre a app para ativar o arrastar ficheiros.", parent=self)

        self.bg(work, done)

    def on_drop(self, files):
        paths = []
        for f in files:
            if isinstance(f, bytes):
                try:
                    f = f.decode("utf-8")
                except UnicodeDecodeError:
                    f = f.decode("mbcs" if sys.platform == "win32" else "latin-1", "ignore")
            paths.append(f)
        self.ui_q.put(lambda: self.add_files(paths))

    def expand_paths(self, paths, limit=40):
        """Pastas -> os ficheiros de texto lá dentro (sem node_modules/.git, no máx. `limit`)."""
        skip = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build"}
        out = []
        for p in paths:
            pp = Path(p)
            if pp.is_dir():
                for root, dirs, fns in os.walk(pp):
                    dirs[:] = [d for d in dirs if d not in skip]
                    for fn in sorted(fns):
                        if Path(fn).suffix.lower() not in BLOCKED_EXT:
                            out.append(str(Path(root, fn)))
                        if len(out) >= limit:
                            return out
            else:
                out.append(str(pp))
        return out

    def attach_folder(self):
        folder = filedialog.askdirectory(title="Anexar todos os ficheiros de texto de uma pasta")
        if folder:
            self.add_files([folder])

    def attach_dialog(self):
        paths = filedialog.askopenfilenames(title="Anexar ficheiros")
        if paths:
            self.add_files(list(paths))

    def add_files(self, paths):
        self.notebook.select(self.tab_chat)
        paths = self.expand_paths(paths)
        if len(paths) >= 40:
            self.flash("Pasta grande: só os primeiros 40 ficheiros foram anexados.", THEME["warn"])
        for p in paths:
            if any(a["path"] == p for a in self.attachments):
                continue
            content, note = read_attachment(p)
            if content is None:
                messagebox.showwarning("Anexo ignorado", f"{Path(p).name}: {note}", parent=self)
                continue
            self.attachments.append({"path": p, "name": Path(p).name, "content": content})
            if note:
                self.flash(f"{Path(p).name}: {note}", THEME["warn"])
        self.render_chips()

    def render_chips(self):
        for w in self.chips.winfo_children():
            w.destroy()
        for i, a in enumerate(self.attachments):
            chip = tk.Frame(self.chips, bg=THEME["accent_blue"])
            chip.pack(side=tk.LEFT, padx=(0, 6), pady=2)
            tk.Label(chip, text=f"📄 {a['name']} ({len(a['content']) // 1000 or 1} KB)", bg=THEME["accent_blue"], fg="#ffffff",
                     font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(6, 2))
            tk.Button(chip, text="✕", bg=THEME["accent_blue"], fg="#ffffff", relief=tk.FLAT, font=("Segoe UI", 8, "bold"),
                      cursor="hand2", command=lambda i=i: self.remove_attachment(i)).pack(side=tk.LEFT)

    def remove_attachment(self, i):
        if 0 <= i < len(self.attachments):
            self.attachments.pop(i)
            self.render_chips()

    # ---- envio ----
    def on_enter_pressed(self, event):
        if not event.state & 0x1:  # Shift = nova linha
            self.send_message()
            return "break"

    def send_message(self):
        if self.busy:
            return
        text = self.msg_entry.get("1.0", tk.END).strip()
        if not text and not self.attachments:
            return
        s = self.cur_session()
        msg = {"role": "user", "text": text or "(ficheiros anexados)"}
        if self.attachments:
            msg["attachments"] = [{"name": a["name"], "content": a["content"]} for a in self.attachments]
        s["messages"].append(msg)
        self.touch(s)
        if len(s["messages"]) == 1:
            s["title"] = (text or self.attachments[0]["name"])[:40]
        self.attachments = []
        self.render_chips()
        self.msg_entry.delete("1.0", tk.END)
        self.last_error = None
        self.save_sessions()
        self.refresh_session_list()
        self.request_reply()

    def retry_last(self):
        if not self.busy:
            self.last_error = None
            self.request_reply()

    _STOP = {"para", "como", "isto", "isso", "esta", "este", "essa", "esse", "quero", "preciso", "fazer", "pode", "podes",
             "sobre", "mais", "muito", "onde", "quando", "qual", "quais", "tenho", "temos", "ficheiro", "código", "codigo",
             "ajuda", "favor", "agora", "ainda", "também", "porque", "tudo", "todos", "todas", "uma", "umas", "uns"}

    def relevant_skills(self, text, k=4):
        words = {w for w in re.findall(r"\w{5,}", text.lower()) if w not in self._STOP}
        if not words:
            return []
        scored = []
        for sk in self.all_skills():
            hay = (sk["title"] + " " + sk.get("content", "")).lower()
            score = sum(1 for w in words if w in hay)
            if score >= 2:
                scored.append((score, sk))
        scored.sort(key=lambda x: -x[0])
        return [sk for _, sk in scored[:k]]

    def build_api_messages(self, s):
        msgs = s["messages"][-12:]
        out, n = [], len(msgs)
        for i, m in enumerate(msgs):
            content = m["text"]
            for a in m.get("attachments", []):
                c = a["content"] if i >= n - 4 else a["content"][:3000]
                content += f"\n\n[Ficheiro anexado: {a['name']}]\n```\n{c}\n```"
            out.append({"role": "user" if m["role"] == "user" else "assistant", "content": content})
        while out and out[0]["role"] != "user":
            out.pop(0)
        if self.use_skills.get() and msgs:
            picked = self.relevant_skills(msgs[-1]["text"] if msgs[-1]["role"] == "user" else "")
            if picked:
                guide = "Especialidades relevantes (usa-as só se ajudarem a pergunta):\n" + "\n".join(
                    f"- {sk['title']}: {sk.get('content', '')[:400]}" for sk in picked)
                # o servidor só aceita mensagens user/assistant: o contexto vai dentro da última pergunta
                for m in reversed(out):
                    if m["role"] == "user":
                        m["content"] = guide + "\n\nPergunta:\n" + m["content"]
                        break
        return out

    def request_reply(self):
        s = self.cur_session()
        sid, msgs = s["id"], self.build_api_messages(s)
        self.busy, self.pending_sid = True, sid
        self.send_btn.config(state=tk.DISABLED)
        self.render_messages()

        def done(res, err):
            self.busy = False
            self.send_btn.config(state=tk.NORMAL)
            sess = next((x for x in self.sessions if x["id"] == sid), None)
            if err or not sess:
                self.last_error = (sid, str(err or "Conversa apagada."))
            else:
                try:
                    reply = (res["choices"][0]["message"].get("content") or "").strip() or "Sem resposta."
                except Exception:
                    reply = "Sem resposta."
                sess["messages"].append({"role": "bot", "text": reply, "model": "gois.v1"})
                self.touch(sess)
                self.save_sessions()
                self.refresh_session_list()
            if self.current_session_id == sid:
                self.render_messages()

        self.bg(lambda: self.api.call("POST", "/v1/chat/completions", {"messages": msgs, "stream": False}, timeout=150), done)

    # ======================================================================= #
    # SKILLS
    # ======================================================================= #
    def all_skills(self):
        out = [dict(s, origin="integrada") for s in BUILTIN_SKILLS]
        out += [dict(s, origin="personalizada") for s in self.custom_skills]
        return out

    def build_skills_tab(self):
        top = tk.Frame(self.tab_skills, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=20, pady=(14, 6))
        self.skills_title = tk.Label(top, text="", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI", 12, "bold"))
        self.skills_title.pack(side=tk.LEFT)
        self.btn(top, "⬆ Sincronizar com servidor", self.sync_skills).pack(side=tk.RIGHT, padx=3)
        self.btn(top, "⬇ Exportar .json", self.export_skills).pack(side=tk.RIGHT, padx=3)
        self.btn(top, "🗑 Apagar personalizada", self.delete_skill).pack(side=tk.RIGHT, padx=3)
        self.btn(top, "+ Nova skill", self.add_skill_dialog, THEME["accent"]).pack(side=tk.RIGHT, padx=3)

        sf = tk.Frame(self.tab_skills, bg=THEME["bg"])
        sf.pack(fill=tk.X, padx=20)
        tk.Label(sf, text="🔎", bg=THEME["bg"], fg=THEME["muted"]).pack(side=tk.LEFT)
        tk.Entry(sf, textvariable=self.skill_search, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white",
                 relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=4)

        pw = tk.PanedWindow(self.tab_skills, orient=tk.VERTICAL, bg=THEME["border"], sashwidth=4)
        pw.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)
        tf = tk.Frame(pw, bg=THEME["panel"])
        pw.add(tf, minsize=200)
        self.skill_tree = ttk.Treeview(tf, columns=("tipo", "titulo", "origem"), show="headings", selectmode="browse")
        for c, w, txt in (("tipo", 70, "Tipo"), ("titulo", 700, "Título"), ("origem", 120, "Origem")):
            self.skill_tree.heading(c, text=txt)
            self.skill_tree.column(c, width=w, anchor=tk.W)
        vs = ttk.Scrollbar(tf, orient="vertical", command=self.skill_tree.yview)
        self.skill_tree.configure(yscrollcommand=vs.set)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.skill_tree.pack(fill=tk.BOTH, expand=True)
        self.skill_tree.bind("<<TreeviewSelect>>", self.show_skill)
        self.skill_detail = tk.Text(pw, height=6, bg=THEME["code_bg"], fg=THEME["fg"], font=("Segoe UI", 10), wrap=tk.WORD,
                                    relief=tk.FLAT, padx=10, pady=8, state=tk.DISABLED)
        pw.add(self.skill_detail, minsize=90)
        self.skill_rows = []
        self.refresh_skills()

    def refresh_skills(self):
        q = self.skill_search.get().strip().lower()
        rows = [s for s in self.all_skills() if not q or q in s["title"].lower() or q in s.get("content", "").lower()]
        self.skill_rows = rows
        self.skill_tree.delete(*self.skill_tree.get_children())
        for i, s in enumerate(rows):
            self.skill_tree.insert("", tk.END, iid=str(i), values=(s.get("type", "SKILL"), s["title"], s["origin"]))
        total = len(BUILTIN_SKILLS) + len(self.custom_skills)
        self.skills_title.config(text=f"🧠 Skills & Diretrizes — {total} ({len(BUILTIN_SKILLS)} integradas + {len(self.custom_skills)} tuas)")

    def show_skill(self, _e=None):
        sel = self.skill_tree.selection()
        self.skill_detail.config(state=tk.NORMAL)
        self.skill_detail.delete("1.0", tk.END)
        if sel:
            s = self.skill_rows[int(sel[0])]
            self.skill_detail.insert(tk.END, s["title"] + "\n\n" + s.get("content", ""))
        self.skill_detail.config(state=tk.DISABLED)

    def add_skill_dialog(self):
        win = tk.Toplevel(self)
        win.title("Nova skill")
        win.configure(bg=THEME["panel"])
        win.geometry("520x340")
        win.transient(self)
        win.grab_set()
        tk.Label(win, text="Título", bg=THEME["panel"], fg=THEME["muted"]).pack(anchor=tk.W, padx=16, pady=(14, 2))
        e_title = tk.Entry(win, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white", relief=tk.FLAT)
        e_title.pack(fill=tk.X, padx=16, ipady=5)
        tk.Label(win, text="Descrição / diretriz", bg=THEME["panel"], fg=THEME["muted"]).pack(anchor=tk.W, padx=16, pady=(10, 2))
        e_text = tk.Text(win, height=8, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white", relief=tk.FLAT, wrap=tk.WORD, padx=6, pady=6)
        e_text.pack(fill=tk.BOTH, expand=True, padx=16)

        def save():
            title, content = e_title.get().strip(), e_text.get("1.0", tk.END).strip()
            if not title:
                return
            if any(s["title"].lower() == title.lower() for s in self.all_skills()):
                messagebox.showwarning("Skill", "Já existe uma skill com esse título.", parent=win)
                return
            sk = {"type": "SKILL", "title": title, "content": content}
            self.custom_skills.append(sk)
            write_json(LOCAL_SKILLS_FILE, self.custom_skills)
            self.refresh_skills()
            win.destroy()
            if self.is_admin:
                self.bg(lambda: self.api.call("POST", "/api/skills", sk), None)

        self.btn(win, "Guardar", save, THEME["accent"]).pack(pady=10)

    def delete_skill(self):
        sel = self.skill_tree.selection()
        if not sel:
            return
        s = self.skill_rows[int(sel[0])]
        if s["origin"] != "personalizada":
            messagebox.showinfo("Skill", "As skills integradas fazem parte da app e não se apagam.", parent=self)
            return
        self.custom_skills = [x for x in self.custom_skills if x["title"] != s["title"]]
        write_json(LOCAL_SKILLS_FILE, self.custom_skills)
        self.refresh_skills()
        self.show_skill()

    def export_skills(self):
        path = filedialog.asksaveasfilename(initialfile="gois_skills.json", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            write_json(Path(path), [{k: v for k, v in s.items() if k != "origin"} for s in self.all_skills()])
            self.flash(f"💾 {path}", THEME["bot"])

    def sync_skills(self):
        """Repõe no servidor o que ele perdeu e traz para aqui o que só lá existe."""
        if not self.is_admin:
            self.flash("Sincronização precisa do token de admin (⚙).", THEME["warn"])
            return
        local = self.all_skills()

        def work():
            server = self.api.call("GET", "/api/skills")
            have = {s.get("title") for s in server}
            missing = [{"type": s.get("type", "SKILL"), "title": s["title"], "content": s.get("content", "")}
                       for s in local if s["title"] not in have]
            for i in range(0, len(missing), 40):
                self.api.call("POST", "/api/skills/bulk", {"skills": missing[i:i + 40]}, timeout=60)
            mine = {s["title"] for s in local}
            return len(missing), [s for s in server if s.get("title") and s["title"] not in mine]

        def done(res, err):
            if err:
                self.flash(f"Skills: {err}", THEME["danger"])
                return
            up, new = res
            for s in new:
                self.custom_skills.append({"type": s.get("type", "SKILL"), "title": s["title"], "content": s.get("content", "")})
            if new:
                write_json(LOCAL_SKILLS_FILE, self.custom_skills)
                self.refresh_skills()
            self.flash(f"✅ Skills sincronizadas (↑{up} ↓{len(new)})", THEME["bot"])

        self.bg(work, done)

    # ======================================================================= #
    # COMPARAÇÃO LIVE
    # ======================================================================= #
    MODELS = (("gois", "gois.v1", "gois_score"), ("gemini", "Gemini", "gemini_ref"), ("deepseek", "DeepSeek", "deepseek_ref"))

    def cat_data(self):
        return (self.server_settings.get("comparison") or {}).get("categories") or {}

    def totals(self):
        cats = self.cat_data()

        def avg(key):
            v = [e[key] for e in cats.values() if isinstance(e, dict) and e.get(key) is not None]
            return (round(sum(v) / len(v), 1), len(v)) if v else (None, 0)

        return {k: avg(f) for k, _n, f in self.MODELS}

    def leadership(self):
        """(categorias em que o gois.v1 lidera, categorias comparáveis)"""
        wins = total = 0
        for e in self.cat_data().values():
            g = e.get("gois_score")
            refs = [e.get("gemini_ref"), e.get("deepseek_ref")]
            refs = [r for r in refs if r is not None]
            if g is not None and refs:
                total += 1
                wins += g >= max(refs)
        return wins, total

    def live_interval(self):
        try:
            return max(3, int(self.live_s.get()))
        except Exception:
            return 10

    def build_comparison_tab(self):
        t = self.tab_comp
        top = tk.Frame(t, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=18, pady=(12, 2))
        tk.Label(top, text="Comparação ao vivo", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI Semibold", 13)).pack(side=tk.LEFT)
        self.cmp_stamp = tk.Label(top, text="", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9))
        self.cmp_stamp.pack(side=tk.LEFT, padx=12)
        self.btn(top, "⬇ CSV", self.export_comparison_csv).pack(side=tk.RIGHT, padx=3)
        self.bench_all_btn = self.btn(top, "⚔ Benchmark às 16", self.bench_all)
        self.bench_all_btn.pack(side=tk.RIGHT, padx=3)
        self.bench_btn = self.btn(top, "⚔ Benchmark da categoria", self.run_benchmark_ui, THEME["accent_blue"])
        self.bench_btn.pack(side=tk.RIGHT, padx=3)

        ctl = tk.Frame(t, bg=THEME["bg"])
        ctl.pack(fill=tk.X, padx=18, pady=(2, 0))
        for key, name, _f in self.MODELS:
            tk.Label(ctl, text="■", fg=series_color(key), bg=THEME["bg"]).pack(side=tk.LEFT)
            tk.Label(ctl, text=name + "   ", fg=THEME["muted"], bg=THEME["bg"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
        cb = ttk.Combobox(ctl, textvariable=self.live_s, values=["5", "10", "30", "60"], width=4, state="readonly")
        cb.pack(side=tk.RIGHT)
        cb.bind("<<ComboboxSelected>>", lambda _e: (self.cfg.__setitem__("live_s", self.live_interval()), self.save_config()))
        tk.Label(ctl, text="Atualizar a cada (s):", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=(12, 4))
        tk.Checkbutton(ctl, text="🔁 benchmark contínuo (gasta tokens)", variable=self.auto_bench, bg=THEME["bg"], fg=THEME["muted"],
                       selectcolor=THEME["code_bg"], activebackground=THEME["bg"], activeforeground=THEME["fg"],
                       font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=8)
        tk.Label(t, text="Notas 0–100 dadas por um modelo-juiz (LLM) a respostas anónimas à mesma pergunta; Gemini e DeepSeek são chamados "
                         "de verdade pelo servidor. São indicativas, não um teste oficial.", bg=THEME["bg"], fg=THEME["muted"],
                 font=("Segoe UI", 8), anchor=tk.W, justify=tk.LEFT, wraplength=1200).pack(fill=tk.X, padx=18, pady=(2, 0))

        pw = tk.PanedWindow(t, orient=tk.VERTICAL, bg=THEME["border"], sashwidth=4)
        pw.pack(fill=tk.BOTH, expand=True, padx=18, pady=8)
        upper = tk.Frame(pw, bg=THEME["bg"])
        pw.add(upper, minsize=300, height=400)
        left = tk.Frame(upper, bg=THEME["bg"])
        left.pack(side=tk.LEFT, fill=tk.Y)
        c1 = self.card(left)
        c1.pack(fill=tk.X, pady=(0, 8))
        self.cv_overall = tk.Canvas(c1, width=400, height=200, bg=THEME["panel"], highlightthickness=0)
        self.cv_overall.pack(fill=tk.X)
        c2 = self.card(left)
        c2.pack(fill=tk.BOTH, expand=True)
        self.cv_trend = tk.Canvas(c2, width=400, height=190, bg=THEME["panel"], highlightthickness=0)
        self.cv_trend.pack(fill=tk.BOTH, expand=True)
        c3 = self.card(upper)
        c3.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        vs = ttk.Scrollbar(c3, orient="vertical")
        self.cv_cats = tk.Canvas(c3, bg=THEME["panel"], highlightthickness=0, yscrollcommand=vs.set)
        vs.config(command=self.cv_cats.yview)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.cv_cats.pack(fill=tk.BOTH, expand=True)
        self.cv_cats.bind("<MouseWheel>", lambda e: self.cv_cats.yview_scroll(int(-e.delta / 120), "units"))
        self.cv_overall.bind("<Configure>", lambda _e: self.draw_overall())
        self.cv_trend.bind("<Configure>", lambda _e: self.draw_trend())
        self.cv_cats.bind("<Configure>", lambda _e: self.draw_cats())

        tf = tk.Frame(pw, bg=THEME["panel"])
        pw.add(tf, minsize=140)
        cols = ("n", "cat", "gois", "gem", "ds", "dg", "dd", "les", "bm", "last")
        heads = (("n", 36, "#"), ("cat", 330, "Categoria"), ("gois", 75, "gois.v1"), ("gem", 75, "Gemini"), ("ds", 80, "DeepSeek"),
                 ("dg", 90, "Δ vs Gemini"), ("dd", 100, "Δ vs DeepSeek"), ("les", 60, "Lições"), ("bm", 85, "Benchmarks"), ("last", 130, "Último"))
        self.comp_tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for c, w, txt in heads:
            self.comp_tree.heading(c, text=txt)
            self.comp_tree.column(c, width=w, anchor=tk.W if c in ("cat", "last") else tk.CENTER)
        self.comp_tree.tag_configure("lead", foreground=THEME["bot"])
        tv = ttk.Scrollbar(tf, orient="vertical", command=self.comp_tree.yview)
        self.comp_tree.configure(yscrollcommand=tv.set)
        tv.pack(side=tk.RIGHT, fill=tk.Y)
        self.comp_tree.pack(fill=tk.BOTH, expand=True)
        for i, c in enumerate(DEFAULT_CATEGORIES):
            self.comp_tree.insert("", tk.END, iid=str(i), values=(i + 1, c, "—", "—", "—", "—", "—", 0, 0, ""))
        self.comp_tree.bind("<<TreeviewSelect>>", self.on_comp_select)
        self.update_comparison_ui()

    def on_comp_select(self, _e=None):
        c = self.selected_comp_category()
        if c != self.sel_cat:
            self.sel_cat = c
            self.draw_cats()

    @staticmethod
    def fnum(v, nd=1):
        return "—" if v is None else f"{v:.{nd}f}"

    @staticmethod
    def fdelta(a, b):
        return "—" if a is None or b is None else f"{a - b:+.1f}"

    def update_comparison_ui(self):
        if not hasattr(self, "comp_tree"):
            return
        try:
            cats = self.cat_data()
            for i, c in enumerate(DEFAULT_CATEGORIES):
                e = cats.get(c) or {}
                g, gm, ds = e.get("gois_score"), e.get("gemini_ref"), e.get("deepseek_ref")
                refs = [x for x in (gm, ds) if x is not None]
                lead = g is not None and refs and g >= max(refs)
                last = str(e.get("last_benchmark") or "")[:16].replace("T", " ")
                self.comp_tree.item(str(i), tags=("lead",) if lead else (), values=(
                    i + 1, c, self.fnum(g), self.fnum(gm), self.fnum(ds), self.fdelta(g, gm), self.fdelta(g, ds),
                    e.get("lessons", 0), e.get("benchmarks", 0), last))
            self.draw_all_charts()
        except tk.TclError:
            pass

    # ---- gráficos (Canvas) ----
    def _cv_ready(self, cv):
        try:
            cv.delete("all")
            return cv.winfo_width() > 80 and cv.winfo_height() > 80
        except tk.TclError:
            return False

    def draw_all_charts(self):
        self.draw_overall()
        self.draw_trend()
        self.draw_cats()

    def draw_overall(self):
        cv = getattr(self, "cv_overall", None)
        if cv is None or not self._cv_ready(cv):
            return
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.create_text(14, 14, text="Total geral por modelo", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 10))
        wins, total = self.leadership()
        sub = f"gois.v1 lidera em {wins} de {total} categorias comparáveis" if total else "ainda sem benchmarks — corre um benchmark"
        cv.create_text(14, 30, text=sub, anchor="w", fill=THEME["muted"], font=("Segoe UI", 8))
        left, right, top, bottom = 38, w - 16, 48, h - 26
        for v in (0, 25, 50, 75, 100):
            y = bottom - (bottom - top) * v / 100
            cv.create_line(left, y, right, y, fill=THEME["border"])
            cv.create_text(left - 6, y, text=str(v), anchor="e", fill=THEME["muted"], font=("Segoe UI", 8))
        tot = self.totals()
        slot = (right - left) / 3
        bw = min(56, slot * 0.5)
        for i, (key, name, _f) in enumerate(self.MODELS):
            val, _n = tot[key]
            cx = left + slot * (i + 0.5)
            if val is not None:
                y = bottom - (bottom - top) * val / 100
                cv.create_rectangle(cx - bw / 2, y, cx + bw / 2, bottom, fill=series_color(key), outline="")
                cv.create_text(cx, y - 9, text=f"{val:.1f}%", fill=THEME["fg"], font=("Segoe UI Semibold", 9))
            else:
                cv.create_rectangle(cx - bw / 2, bottom - 2, cx + bw / 2, bottom, fill=THEME["border"], outline="")
                cv.create_text(cx, bottom - 12, text="—", fill=THEME["muted"])
            cv.create_text(cx, bottom + 13, text=name, fill=THEME["muted"], font=("Segoe UI", 9))

    def draw_trend(self):
        cv = getattr(self, "cv_trend", None)
        if cv is None or not self._cv_ready(cv):
            return
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.create_text(14, 14, text="Evolução dos totais (histórico local)", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 10))
        pts = self.history[-150:]
        if len(pts) < 2:
            cv.create_text(w / 2, h / 2, text="A recolher histórico…\ncada alteração dos totais fica guardada aqui.",
                           fill=THEME["muted"], font=("Segoe UI", 9), justify=tk.CENTER)
            return
        keys = [k for k, _n, _f in self.MODELS]
        vals = [p[k] for p in pts for k in keys if p.get(k) is not None]
        if not vals:
            return
        lo = max(0, int(min(vals)) - 5)
        hi = min(100, int(max(vals)) + 5)
        if hi - lo < 10:
            hi = min(100, lo + 10)
            lo = max(0, hi - 10)
        left, right, top, bottom = 38, w - 16, 34, h - 26
        t0, t1 = pts[0]["t"], pts[-1]["t"]
        span = max(t1 - t0, 1)

        def X(t):
            return left + (right - left) * (t - t0) / span

        def Y(v):
            return bottom - (bottom - top) * (v - lo) / (hi - lo)

        for v in (lo, (lo + hi) / 2, hi):
            cv.create_line(left, Y(v), right, Y(v), fill=THEME["border"])
            cv.create_text(left - 6, Y(v), text=f"{v:.0f}", anchor="e", fill=THEME["muted"], font=("Segoe UI", 8))
        fmt = "%H:%M" if span < 86400 else "%d/%m"
        cv.create_text(left, bottom + 13, text=time.strftime(fmt, time.localtime(t0)), anchor="w", fill=THEME["muted"], font=("Segoe UI", 8))
        cv.create_text(right, bottom + 13, text=time.strftime(fmt, time.localtime(t1)), anchor="e", fill=THEME["muted"], font=("Segoe UI", 8))
        for key in keys:
            col, seg = series_color(key), []

            def flush():
                if len(seg) >= 4:
                    cv.create_line(*seg, fill=col, width=2)

            for p in pts:
                v = p.get(key)
                if v is None:
                    flush()
                    seg = []
                else:
                    seg += [X(p["t"]), Y(v)]
            flush()
            last = next((p for p in reversed(pts) if p.get(key) is not None), None)
            if last:
                cv.create_oval(X(last["t"]) - 3, Y(last[key]) - 3, X(last["t"]) + 3, Y(last[key]) + 3, fill=col, outline="")

    def draw_cats(self):
        cv = getattr(self, "cv_cats", None)
        if cv is None:
            return
        try:
            cv.delete("all")
            w = cv.winfo_width()
        except tk.TclError:
            return
        if w < 120:
            return
        cats = self.cat_data()
        row_h, top = 46, 40
        label_w = min(300, int(w * 0.36))
        left, right = label_w + 14, w - 52
        total_h = top + row_h * len(DEFAULT_CATEGORIES) + 8
        cv.config(scrollregion=(0, 0, w, total_h))
        cv.create_text(14, 16, text="Por categoria", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 10))
        for v in (0, 25, 50, 75, 100):
            x = left + (right - left) * v / 100
            cv.create_line(x, top - 8, x, total_h - 6, fill=THEME["border"])
            cv.create_text(x, top - 16, text=str(v), fill=THEME["muted"], font=("Segoe UI", 8))
        for i, c in enumerate(DEFAULT_CATEGORIES):
            y0 = top + i * row_h
            if c == self.sel_cat:
                cv.create_rectangle(0, y0 - 3, w, y0 + row_h - 5, fill=THEME["panel_alt"], outline="")
            name = c if len(c) <= 46 else c[:45] + "…"
            cv.create_text(12, y0 + row_h / 2 - 3, text=f"{i + 1}. {name}", anchor="w", width=label_w, fill=THEME["fg"], font=("Segoe UI", 9))
            e = cats.get(c) or {}
            for j, (key, _n, field) in enumerate(self.MODELS):
                v = e.get(field)
                by = y0 + 3 + j * 12
                if v is None:
                    cv.create_line(left, by + 4, left + 6, by + 4, fill=THEME["border"], width=2)
                    continue
                x = left + (right - left) * v / 100
                cv.create_rectangle(left, by, x, by + 9, fill=series_color(key), outline="")
                cv.create_text(x + 5, by + 4, text=f"{v:.0f}", anchor="w", fill=THEME["muted"], font=("Segoe UI", 7))

    # ---- benchmarks ----
    def selected_comp_category(self):
        sel = self.comp_tree.selection()
        return DEFAULT_CATEGORIES[int(sel[0])] if sel else None

    def run_benchmark(self, cat, then=None):
        if self.class_busy:
            return False
        self.set_class_busy(True, f"⚔️ Benchmark em [{cat}] (gois.v1 vs Gemini vs DeepSeek, ~30–90 s)…")

        def done(res, err):
            self.set_class_busy(False)
            if err:
                self.class_log(f"❌ Erro no benchmark [{cat}]: {err}")
                return
            b = res.get("benchmark", {})
            self.class_log(f"🏆 [{cat}] em {b.get('elapsed_s', '?')}s: gois.v1 {self.fnum(b.get('gois_score'))}% · "
                           f"Gemini {self.fnum(b.get('gemini_ref'))}% · DeepSeek {self.fnum(b.get('deepseek_ref'))}%")
            if isinstance(res.get("settings"), dict):
                self.apply_settings(res["settings"])
            if then:
                then()

        self.bg(lambda: self.api.call("POST", "/api/comparison/benchmark", {"category": cat}, timeout=240), done)
        return True

    def run_benchmark_ui(self):
        if not self.require_admin_ui():
            return
        cat = self.selected_comp_category()
        if not cat:
            messagebox.showinfo("Benchmark", "Seleciona primeiro uma categoria na tabela.", parent=self)
            return
        self.run_benchmark(cat)

    def bench_all(self):
        if not self.require_admin_ui() or self.class_busy:
            return
        if not messagebox.askyesno("Benchmark às 16 categorias",
                                   "Corre 16 benchmarks seguidos (cada um usa vários pedidos aos modelos e demora ~1 min). Continuar?", parent=self):
            return
        pending = list(DEFAULT_CATEGORIES)

        def step():
            tok = self.server_settings.get("tokens") or {}
            if tok.get("limit_reached"):
                self.class_log("⛔ Limite diário de tokens atingido — benchmark em série parado.")
                return
            if pending:
                self.run_benchmark(pending.pop(0), then=step)
            else:
                self.class_log("🏁 Benchmark às 16 categorias concluído.")

        step()

    def on_auto_bench(self):
        pass

    def bench_tick(self):
        self._bench_after = self.after(90_000, self.bench_tick)
        if not (self.auto_bench.get() and self.is_admin and not self.class_busy):
            return
        tok = self.server_settings.get("tokens") or {}
        if tok.get("limit_reached") or tok.get("auto_paused_by_limit"):
            return
        cats = self.cat_data()

        def key(c):
            e = cats.get(c) or {}
            return (e.get("benchmarks") or 0, str(e.get("last_benchmark") or ""))

        self.run_benchmark(min(DEFAULT_CATEGORIES, key=key))

    def export_comparison_csv(self):
        path = filedialog.asksaveasfilename(initialfile="comparacao_gois_gemini_deepseek.csv", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        cats = self.cat_data()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(["Categoria", "gois.v1", "Gemini", "DeepSeek", "Δ vs Gemini", "Δ vs DeepSeek", "Lições", "Benchmarks", "Último benchmark"])
            for c in DEFAULT_CATEGORIES:
                e = cats.get(c) or {}
                g, gm, ds = e.get("gois_score"), e.get("gemini_ref"), e.get("deepseek_ref")
                wr.writerow([c, g, gm, ds, self.fdelta(g, gm), self.fdelta(g, ds), e.get("lessons", 0), e.get("benchmarks", 0), e.get("last_benchmark") or ""])
            tot = self.totals()
            wr.writerow(["TOTAL GERAL", tot["gois"][0], tot["gemini"][0], tot["deepseek"][0]])
        self.flash(f"💾 {path}", THEME["bot"])

    # ---- live ----
    def fetch_live(self):
        if self._live_busy or not self.is_admin:
            return
        self._live_busy = True

        def work():
            t0 = time.time()
            s = self.api.call("GET", "/api/settings", timeout=30)
            return s, int((time.time() - t0) * 1000)

        def done(res, err):
            self._live_busy = False
            if err:
                self.refresh_live_ui()
                return
            s, ms = res
            self.latency_ms = ms
            self.apply_settings(s)
            if (s.get("comparison") or {}).get("lessons_total", 0) > len(self.kb):
                self.refresh_classroom()

        self.bg(work, done)

    def live_tick(self):
        self._live_after = self.after(self.live_interval() * 1000, self.live_tick)
        if self.is_admin and not self.class_busy:
            self.fetch_live()

    def clock_tick(self):
        self.refresh_live_ui()
        self.after(1000, self.clock_tick)

    def record_history(self):
        tot = self.totals()
        snap = {"t": time.time(), **{k: tot[k][0] for k in tot}}
        if all(snap[k] is None for k in tot):
            return
        last = self.history[-1] if self.history else None
        same = last and all(last.get(k) == snap[k] for k in tot)
        if same and snap["t"] - last["t"] < 900:
            return
        self.history.append(snap)
        self.history = self.history[-500:]
        write_json(HISTORY_FILE, self.history)

    def refresh_live_ui(self):
        try:
            if not self.connected:
                txt, col = "● offline", THEME["danger"]
            elif not self.is_admin:
                txt, col = "● sem admin · dados em cache", THEME["warn"]
            else:
                age = time.time() - self.last_live if self.last_live else None
                if age is None:
                    txt, col = "● a sincronizar…", THEME["warn"]
                elif age <= self.live_interval() * 2 + 5:
                    txt, col = f"● LIVE · há {int(age)} s", THEME["bot"]
                else:
                    txt, col = f"● atrasado · há {int(age)} s", THEME["warn"]
            self.live_lbl.config(text=txt, fg=col)
            self.cmp_stamp.config(text=txt, fg=col)
            lat = f"{self.latency_ms} ms" if self.latency_ms is not None else "—"
            adm = ("🛡 Administrador" if is_windows_admin() else "sem Administrador") if sys.platform == "win32" else ""
            self.foot_r.config(text=f"Latência {lat}  ·  {adm}  ·  v{APP_VERSION}")
        except tk.TclError:
            pass

    def update_kpis(self):
        try:
            tot = self.totals()
            comp = self.server_settings.get("comparison") or {}
            tok = self.server_settings.get("tokens") or {}
            nc = len(DEFAULT_CATEGORIES)
            for k, _n, _f in self.MODELS:
                v, n = tot[k]
                self.kpi[k][0].config(text="—" if v is None else f"{v:.1f} %")
                self.kpi[k][1].config(text=f"{n}/{nc} categorias medidas")
            pg, pd = comp.get("parity_vs_gemini"), comp.get("parity_vs_deepseek")
            self.kpi["par_gem"][0].config(text=f"{pg} %" if pg else "—")
            self.kpi["par_gem"][1].config(text="gois.v1 ÷ Gemini")
            self.kpi["par_ds"][0].config(text=f"{pd} %" if pd else "—")
            self.kpi["par_ds"][1].config(text="gois.v1 ÷ DeepSeek")
            self.kpi["lessons"][0].config(text=str(comp.get("lessons_total", len(self.kb))))
            self.kpi["lessons"][1].config(text="na Sala de Aula")
            self.kpi["skills"][0].config(text=str(len(self.all_skills())))
            self.kpi["skills"][1].config(text=f"{len(BUILTIN_SKILLS)} integradas")
            used = int(tok.get("used_today", 0) or 0)
            self.kpi["tokens"][0].config(text=f"{used:,}".replace(",", "."))
            self.kpi["tokens"][1].config(text="limite " + (f"{int(tok.get('daily_limit', 0)):,}".replace(",", ".") if tok.get("daily_limit") else "∞"))
        except (tk.TclError, KeyError):
            pass

    # ---- aba Sistema ----
    def build_system_tab(self):
        t = self.tab_sys
        top = tk.Frame(t, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=18, pady=(12, 6))
        tk.Label(top, text="Sistema & Diagnóstico", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI Semibold", 13)).pack(side=tk.LEFT)
        self.btn(top, "📁 Abrir pasta de dados", self.open_data_dir).pack(side=tk.RIGHT, padx=3)
        self.btn(top, "⬆ Sincronizar skills", self.sync_skills).pack(side=tk.RIGHT, padx=3)
        self.btn(top, "🔄 Reconectar", self.check_connection, THEME["accent_blue"]).pack(side=tk.RIGHT, padx=3)
        f = self.card(t)
        f.pack(fill=tk.BOTH, expand=True, padx=18, pady=8)
        self.sys_tree = ttk.Treeview(f, columns=("k", "v"), show="headings", selectmode="none")
        self.sys_tree.heading("k", text="Item")
        self.sys_tree.heading("v", text="Valor")
        self.sys_tree.column("k", width=260, anchor=tk.W)
        self.sys_tree.column("v", width=800, anchor=tk.W)
        self.sys_tree.pack(fill=tk.BOTH, expand=True)
        self.refresh_system_tab()

    def open_data_dir(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(DATA_DIR))  # noqa
            else:
                subprocess.Popen(["xdg-open", str(DATA_DIR)])
        except Exception as e:
            self.flash(f"❌ {e}", THEME["danger"])

    def refresh_system_tab(self):
        tree = getattr(self, "sys_tree", None)
        if tree is None:
            return
        try:
            s, h = self.server_settings or {}, self.health or {}
            tok = s.get("tokens") or {}
            if not self.connected:
                mode = "Offline"
            elif self.admin_configured is False:
                mode = "Público — o servidor não tem ADMIN_TOKEN"
            elif self.is_admin:
                mode = "Administrador (token válido)"
            else:
                mode = "Token inválido"
            rows = [
                ("Servidor", self.cfg.get("server", "")),
                ("Estado da ligação", "Online" if self.connected else "Offline"),
                ("Latência do último pedido", f"{self.latency_ms} ms" if self.latency_ms is not None else "—"),
                ("Versão do servidor", h.get("version", "—")),
                ("Modo de acesso", mode),
                ("Skills (neste PC / no servidor)", f"{len(self.all_skills())} / {h.get('skills', '—')}"),
                ("Lições (neste PC / no servidor)", f"{len(self.kb_cache)} / {h.get('lessons', '—')}"),
                ("Fornecedores com chave", ", ".join(s.get("providers_with_key", [])) or "—"),
                ("Professor atual", s.get("current_teacher") or "—"),
                ("Modo autónomo", "ligado" if s.get("learning_now") else "desligado"),
                ("Tokens hoje / limite / total", f"{tok.get('used_today', 0)} / {tok.get('daily_limit', 0) or '∞'} / {tok.get('used_total', 0)}"),
                ("Último erro do servidor", s.get("last_error") or "—"),
                ("Windows como Administrador", ("sim" if is_windows_admin() else "não") if sys.platform == "win32" else "n/a"),
                ("Arrastar ficheiros (windnd)", "ativo" if windnd else "não instalado"),
                ("Pasta de dados", str(DATA_DIR)),
                ("Aplicação / Python", f"gois_window {APP_VERSION} · Python {sys.version.split()[0]} · Tk {tk.TkVersion}"),
            ]
            tree.delete(*tree.get_children())
            for i, (k, v) in enumerate(rows):
                tree.insert("", tk.END, iid=str(i), values=(k, v))
        except tk.TclError:
            pass

    # ======================================================================= #
    # SALA DE AULA
    # ======================================================================= #
    def build_classroom_tab(self):
        top = tk.Frame(self.tab_class, bg=THEME["bg"])
        top.pack(fill=tk.X, padx=20, pady=(14, 4))
        tk.Label(top, text="🏫 Sala de Aula Autónoma", bg=THEME["bg"], fg=THEME["fg"], font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        self.class_status = tk.Label(self.tab_class, text="Sala de Aula: a verificar…", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9), anchor=tk.W, justify=tk.LEFT)
        self.class_status.pack(fill=tk.X, padx=20)
        self.tokens_lbl = tk.Label(self.tab_class, text="🔋 Tokens hoje: —", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9), anchor=tk.W)
        self.tokens_lbl.pack(fill=tk.X, padx=20)

        ctrl = tk.Frame(self.tab_class, bg=THEME["bg"])
        ctrl.pack(fill=tk.X, padx=20, pady=6)
        self.study_btn = self.btn(ctrl, "📚 Estudar categoria selecionada", self.study_selected, THEME["accent"])
        self.study_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.study_all_btn = self.btn(ctrl, "🎓 Estudar todas (1 lição cada)", self.study_all)
        self.study_all_btn.pack(side=tk.LEFT, padx=6)
        self.auto_btn = self.btn(ctrl, "▶ Modo autónomo: OFF", self.toggle_auto)
        self.auto_btn.pack(side=tk.LEFT, padx=6)
        tk.Label(ctrl, text="Limite/dia (0 = sem):", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(18, 4))
        self.limit_var = tk.StringVar(value="0")
        tk.Entry(ctrl, textvariable=self.limit_var, width=10, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white", relief=tk.FLAT).pack(side=tk.LEFT, ipady=3)
        self.btn(ctrl, "Guardar", self.save_daily_limit).pack(side=tk.LEFT, padx=4)

        pw = tk.PanedWindow(self.tab_class, orient=tk.HORIZONTAL, bg=THEME["border"], sashwidth=4)
        pw.pack(fill=tk.BOTH, expand=True, padx=20, pady=6)
        left = tk.Frame(pw, bg=THEME["panel"])
        pw.add(left, minsize=300)
        self.cat_list = tk.Listbox(left, bg=THEME["panel"], fg=THEME["fg"], selectbackground=THEME["accent_blue"], selectforeground="#ffffff",
                                   relief=tk.FLAT, highlightthickness=0, activestyle="none", font=("Segoe UI", 9), exportselection=False)
        self.cat_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        for c in DEFAULT_CATEGORIES:
            self.cat_list.insert(tk.END, f"  {c}  (0)")
        self.cat_list.selection_set(0)

        right = tk.PanedWindow(pw, orient=tk.VERTICAL, bg=THEME["border"], sashwidth=4)
        pw.add(right, minsize=420)
        kf = tk.Frame(right, bg=THEME["panel"])
        right.add(kf, minsize=120)
        self.kb_tree = ttk.Treeview(kf, columns=("cat", "titulo", "prof"), show="headings", selectmode="browse")
        for c, w, txt in (("cat", 170, "Categoria"), ("titulo", 260, "Lição"), ("prof", 130, "Professor")):
            self.kb_tree.heading(c, text=txt)
            self.kb_tree.column(c, width=w, anchor=tk.W)
        vs = ttk.Scrollbar(kf, orient="vertical", command=self.kb_tree.yview)
        self.kb_tree.configure(yscrollcommand=vs.set)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.kb_tree.pack(fill=tk.BOTH, expand=True)
        self.kb_tree.bind("<<TreeviewSelect>>", self.show_lesson)
        self.lesson_text = tk.Text(right, height=8, bg=THEME["code_bg"], fg=THEME["fg"], font=("Segoe UI", 10), wrap=tk.WORD,
                                   relief=tk.FLAT, padx=10, pady=8, state=tk.DISABLED)
        right.add(self.lesson_text, minsize=100)
        self.kb_rows = []

        self.class_log_text = tk.Text(self.tab_class, height=5, bg=THEME["panel"], fg=THEME["muted"], font=("Consolas", 9), relief=tk.FLAT,
                                      padx=8, pady=4, state=tk.DISABLED, wrap=tk.WORD)
        self.class_log_text.pack(fill=tk.X, padx=20, pady=(0, 12))

    def _append_log(self, entry):
        try:
            self.class_log_text.config(state=tk.NORMAL)
            self.class_log_text.insert(tk.END, entry + "\n")
            self.class_log_text.see(tk.END)
            self.class_log_text.config(state=tk.DISABLED)
        except tk.TclError:
            pass

    def class_log(self, line):
        entry = time.strftime("%H:%M:%S ") + line
        self.log_lines = (self.log_lines + [entry])[-300:]
        self._append_log(entry)

    def class_log_restore(self):
        for e in self.log_lines[-80:]:
            self._append_log(e)

    def require_admin_ui(self):
        if not self.connected:
            messagebox.showwarning("Offline", "Sem ligação ao servidor. Espera que o indicador no topo fique verde.", parent=self)
            return False
        if not self.is_admin:
            if self.admin_configured is False:
                self.warn_no_token_once()
                messagebox.showwarning("Admin", "O servidor não tem ADMIN_TOKEN definido (Render > Environment).", parent=self)
            else:
                messagebox.showwarning("Admin", "O token de administrador não é válido. Abre ⚙ Definições > 🔌 Testar ligação.", parent=self)
            return False
        return True

    def set_class_busy(self, busy, msg=None):
        self.class_busy = busy
        st = tk.DISABLED if busy else tk.NORMAL
        for b in (self.study_btn, self.study_all_btn, self.bench_btn, self.bench_all_btn):
            b.config(state=st)
        if msg:
            self.class_log(msg)

    def selected_class_category(self):
        sel = self.cat_list.curselection()
        return DEFAULT_CATEGORIES[sel[0]] if sel else DEFAULT_CATEGORIES[0]

    def study_category(self, cat, then=None):
        self.set_class_busy(True, f"⚡ A estudar [{cat}] com modelo real…")

        def done(res, err):
            self.set_class_busy(False)
            if err:
                self.class_log(f"❌ Erro: {err}")
                return
            les = res.get("lesson", {})
            self.class_log(f"✅ Lição absorvida em [{cat}]: '{les.get('title', '?')}' (Professor: {les.get('teacher', '?')}) — Comparação % atualizada!")
            self.refresh_classroom()
            if then:
                then()

        self.bg(lambda: self.api.call("POST", "/api/classroom/learn", {"category": cat, "provider": "auto"}, timeout=150), done)

    def study_selected(self):
        if self.require_admin_ui() and not self.class_busy:
            self.study_category(self.selected_class_category())

    def study_all(self):
        if not self.require_admin_ui() or self.class_busy:
            return
        queue_ = list(DEFAULT_CATEGORIES)

        def step():
            if queue_:
                self.study_category(queue_.pop(0), then=step)
            else:
                self.class_log("🎓 Ronda completa: 1 lição por categoria.")

        step()

    def toggle_auto(self):
        if not self.require_admin_ui():
            return
        new = not bool(self.server_settings.get("learning_now"))

        def done(res, err):
            if err:
                self.class_log(f"❌ {err}")
                return
            self.class_log("▶ Modo autónomo LIGADO (1 lição por minuto, pelo servidor)." if new else "⏸ Modo autónomo desligado.")
            self.apply_settings(res)

        self.bg(lambda: self.api.call("POST", "/api/settings", {"learning_now": new}), done)

    def save_daily_limit(self):
        if not self.require_admin_ui():
            return
        raw = self.limit_var.get().strip().lower().replace(".", "").replace(",", "")
        mult = 1
        if raw.endswith("k"):
            raw, mult = raw[:-1], 1_000
        elif raw.endswith("m"):
            raw, mult = raw[:-1], 1_000_000
        try:
            val = int(raw or 0) * mult
        except ValueError:
            messagebox.showwarning("Limite", "Número inválido (ex.: 200000 ou 200k).", parent=self)
            return

        def done(res, err):
            self.class_log(f"❌ {err}" if err else f"🔋 Limite diário: {val or 'sem limite'} tokens.")
            if not err:
                self.apply_settings(res)

        self.bg(lambda: self.api.call("POST", "/api/settings", {"daily_token_limit": val}), done)

    def refresh_classroom(self):
        if not self.is_admin:
            return

        def work():
            settings = self.api.call("GET", "/api/settings")
            server_kb = self.api.call("GET", "/api/knowledge")
            ids = {k.get("id") for k in server_kb}
            missing = [k for k in self.kb_cache if k.get("id") not in ids]
            if missing:  # o servidor perdeu lições (restart no Render): repõe a partir da cópia local
                self.api.call("POST", "/api/sync/import", {"knowledge": missing}, timeout=60)
            merged = {k["id"]: k for k in self.kb_cache if k.get("id")}
            merged.update({k["id"]: k for k in server_kb if k.get("id")})
            return settings, sorted(merged.values(), key=lambda k: k.get("created", ""))

        def done(res, err):
            if err:
                self.class_status.config(text=f"Servidor indisponível: {err}", fg=THEME["danger"])
                return
            settings, kb = res
            self.kb = kb
            self.kb_cache = kb
            write_json(KNOWLEDGE_FILE, kb)
            self.apply_settings(settings)
            self.update_knowledge_ui()

        self.bg(work, done)

    def apply_settings(self, s, cached=False):
        self.server_settings = s
        if not cached:
            write_json(COMP_CACHE_FILE, s)
            self.last_live = time.time()
            self.record_history()
        comp = s.get("comparison", {})
        tok = s.get("tokens", {})
        auto = bool(s.get("learning_now"))
        self.auto_btn.config(text="⏸ Modo autónomo: ON" if auto else "▶ Modo autónomo: OFF", bg=THEME["accent"] if auto else THEME["btn"],
                             fg="#ffffff" if auto else THEME["fg"])
        if not cached:
            provs = ", ".join(s.get("providers_with_key", [])) or "NENHUM (configura chaves no Render)"
            self.class_status.config(
                text=f"Online · gois.v1 {comp.get('overall', 0)}% (Paridade Gemini {comp.get('parity_vs_gemini', 0)}% · DeepSeek {comp.get('parity_vs_deepseek', 0)}%) · "
                     f"{comp.get('lessons_total', 0)} lições · Professor atual: {s.get('current_teacher') or '—'}\n"
                     f"Fornecedores com chave: {provs}" + (f"\n⚠ Último erro: {s['last_error']}" if s.get("last_error") else ""),
                fg=THEME["bot"])
        used, lim = tok.get("used_today", 0), tok.get("daily_limit", 0)
        extra = "  ⛔ limite atingido — modo autónomo pausado" if tok.get("auto_paused_by_limit") or tok.get("limit_reached") else ""
        self.tokens_lbl.config(text=f"🔋 Tokens hoje: {used:,} / {lim:,}".replace(",", ".") + f"   ·   Total acumulado: {tok.get('used_total', 0):,}".replace(",", ".") + extra,
                               fg=THEME["warn"] if extra else THEME["muted"])
        if not self.limit_var.get().strip() or self.limit_var.get() == "0":
            self.limit_var.set(str(lim))
        cats = comp.get("categories", {})
        sel = self.cat_list.curselection()
        for i, c in enumerate(DEFAULT_CATEGORIES):
            self.cat_list.delete(i)
            self.cat_list.insert(i, f"  {c}  ({(cats.get(c) or {}).get('lessons', 0)})")
        self.cat_list.selection_set(sel[0] if sel else 0)
        self.update_comparison_ui()
        self.update_kpis()
        self.refresh_system_tab()
        self.refresh_live_ui()

    def update_knowledge_ui(self):
        sel = self.kb_tree.selection()
        self.kb_tree.delete(*self.kb_tree.get_children())
        self.kb_rows = list(reversed(self.kb))
        for i, k in enumerate(self.kb_rows):
            self.kb_tree.insert("", tk.END, iid=str(i), values=(k.get("category", "")[:28], k.get("title", ""), k.get("teacher", "")))
        if sel and sel[0] in self.kb_tree.get_children():
            self.kb_tree.selection_set(sel[0])

    def show_lesson(self, _e=None):
        sel = self.kb_tree.selection()
        self.lesson_text.config(state=tk.NORMAL)
        self.lesson_text.delete("1.0", tk.END)
        if sel and int(sel[0]) < len(self.kb_rows):
            k = self.kb_rows[int(sel[0])]
            self.lesson_text.insert(tk.END, f"{k.get('title', '')}\n[{k.get('category', '')} · {k.get('teacher', '')} · {k.get('created', '')}]\n\n{k.get('content', '')}")
        self.lesson_text.config(state=tk.DISABLED)


if __name__ == "__main__":
    if sys.platform == "win32" and "--no-elevate" not in sys.argv and not is_windows_admin():
        if relaunch_as_admin():  # o utilizador aceitou o UAC: a nova instância (admin) continua daqui
            sys.exit(0)
    try:  # nitidez em ecrãs com escala > 100 %
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    GoisWindow().mainloop()
