"""gois.v1 | Office & Command Center - Aplicação Desktop (v2.4)

Corre sempre como Administrador do Windows (pede elevação UAC ao abrir).
Tema claro/escuro, totais gois.v1/Mistral/Groq sempre no topo (do mais forte ao mais fraco), comparação ao vivo com gráficos, chat com histórico
pesquisável e anexos, Sala de Aula com cópia local e 120 skills integradas.
"""
import os
import re
import math
import sys
import csv
import json
import time
from datetime import datetime
import queue
import threading
import atexit
import shutil
import subprocess
import webbrowser
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
COLAB_HOST = "https://colab.research.google.com/"

DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "gois_v1"
CONFIG_FILE = DATA_DIR / "config.json"
SESSION_FILE = DATA_DIR / "multichats.json"
LOCAL_SKILLS_FILE = DATA_DIR / "skills.json"          # skills criadas por ti
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"          # cópia local das lições da Sala de Aula

APP_VERSION = "2.5"
STATE_BACKUP_FILE = DATA_DIR / "state_backup.json"      # cópia do estado do servidor (notas, tokens, limites) para repor após um reinício
COMP_CACHE_FILE = DATA_DIR / "settings_cache.json"      # última leitura do servidor (mostra dados mesmo offline)
HISTORY_FILE = DATA_DIR / "comparison_history.json"     # histórico dos totais para o gráfico de evolução

THEMES = {
    "dark": {
        "name": "dark", "bg": "#0d1117", "panel": "#161b22", "panel_alt": "#1c2330", "border": "#30363d",
        "fg": "#e6edf3", "muted": "#8b949e", "user": "#58a6ff", "bot": "#3fb950", "btn": "#21262d",
        "accent": "#238636", "accent_blue": "#1f6feb", "code_bg": "#010409", "code_fg": "#79c0ff",
        "warn": "#d29922", "danger": "#f85149", "user_bg": "#16233a",
        "s_gois": "#3fb950", "s_mistral": "#f0883e", "s_groq": "#f778ba",
    },
    "light": {
        "name": "light", "bg": "#f6f8fa", "panel": "#ffffff", "panel_alt": "#eaeef2", "border": "#d0d7de",
        "fg": "#1f2328", "muted": "#59636e", "user": "#0969da", "bot": "#1a7f37", "btn": "#eaeef2",
        "accent": "#1a7f37", "accent_blue": "#0969da", "code_bg": "#f6f8fa", "code_fg": "#0550ae",
        "warn": "#9a6700", "danger": "#cf222e", "user_bg": "#ddf4ff",
        "s_gois": "#1a7f37", "s_mistral": "#bc4c00", "s_groq": "#bf3989",
    },
}
THEME = dict(THEMES["dark"])


def set_theme(name: str) -> None:
    THEME.clear()
    THEME.update(THEMES.get(name, THEMES["dark"]))


def blend(c1, c2, a):
    """Mistura a cor c1 sobre c2 com opacidade a (0..1); cores '#rrggbb'."""
    try:
        r1, g1, b1 = (int(c1[i:i + 2], 16) for i in (1, 3, 5))
        r2, g2, b2 = (int(c2[i:i + 2], 16) for i in (1, 3, 5))
        return "#%02x%02x%02x" % (round(r2 + (r1 - r2) * a), round(g2 + (g1 - g2) * a), round(b2 + (b1 - b2) * a))
    except Exception:
        return c1


class StatusLamp(tk.Canvas):
    """Indicador animado: bolinha com anel a pulsar, 'respiração' lenta, spinner ou círculo com número, mais texto."""

    def __init__(self, master, bg, width=200, height=28):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self.bgc, self.h = bg, height
        self.mode, self.color, self.text, self.count = "off", THEME["muted"], "", 0

    def set(self, mode, color, text, count=0):
        self.mode, self.color, self.text, self.count = mode, color, text, count

    def draw(self, t):
        self.delete("all")
        cx, cy, c, bg = 18, self.h // 2, self.color, self.bgc
        if self.mode == "pulse":
            p = (t * 0.9) % 1.0
            r = 5 + 9 * p
            self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=blend(c, bg, 1 - p), width=2)
            self.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=c, outline="")
        elif self.mode == "slow":
            k = 0.5 + 0.5 * math.sin(t * 2.2)
            self.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, fill=blend(c, bg, 0.3 + 0.7 * k), outline="")
        elif self.mode == "spin":
            self.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, outline=blend(c, bg, 0.25), width=3)
            self.create_arc(cx - 9, cy - 9, cx + 9, cy + 9, start=-(t * 320) % 360, extent=100, style=tk.ARC, outline=c, width=3)
        elif self.mode == "badge":
            on = (self.count or 0) > 0
            if on:
                p = (t * 1.1) % 1.0
                r = 11 + 4 * p
                self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=blend(c, bg, 1 - p), width=2)
            self.create_oval(cx - 11, cy - 11, cx + 11, cy + 11, fill=c if on else blend(c, bg, 0.22), outline="")
            self.create_text(cx, cy, text=str(self.count or 0), fill="#ffffff" if on else blend(THEME["muted"], bg, 0.9),
                             font=("Segoe UI Semibold", 11))
        else:
            self.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill=c, outline="")
        self.create_text(cx + 22, cy, text=self.text, anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 9))


def series_color(key: str) -> str:
    return THEME["s_" + key]


# --- Modelos comparados ------------------------------------------------------------------------------------- #
# (chave, nome, campo guardado no servidor). O gois.v1 é o teu modelo; Mistral e Groq são as referências.
# Três "lugares" na comparação: o teu modelo e UM modelo escolhido de cada fornecedor (Mistral, Groq). O nome mostrado é o do
# modelo escolhido; as notas vêm do servidor (e["models"]["Fornecedor|modelo"]). COMPARE_MODELS e MODEL_NAME mudam NO SÍTIO
# quando escolhes outro modelo, por isso tudo o que os usa (tabela, gráficos, totais) acompanha a escolha.
COMPARE_MODELS = [("gois", "gois.v1", "gois_score"), ("mistral", "Mistral", "mistral_ref"), ("groq", "Groq", "groq_ref")]
MODEL_FIELD = {k: f for k, _n, f in COMPARE_MODELS}
MODEL_NAME = {k: n for k, n, _f in COMPARE_MODELS}
MODEL_PROVIDER = {"mistral": "Mistral", "groq": "Groq"}


def mkey(provider, model):
    return f"{provider}|{model}"


def fscore(v, nd=1, sign=False):
    """Nota formatada sem esconder valores extremos: 0,04 não vira «0,0» e 0 continua a ser 0."""
    if v is None:
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    if v != 0 and float(s) == 0:
        a = abs(v)
        s = (f"{v:+.2g}" if sign else f"{v:.2g}") if a >= 0.001 else ("<0.001" if v > 0 else "-<0.001")
    return s


def scale_top(values, floor=100):
    """Topo da escala dos gráficos: 100 por omissão; sobe em degraus de 25 se alguma nota passar de 100."""
    nums = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    m = max(nums) if nums else 0
    return floor if m <= floor else math.ceil(m / 25) * 25


def short(txt, n=20):
    txt = str(txt)
    return txt if len(txt) <= n else txt[:n - 1] + "…"


def set_model_labels(sel):
    for i, (slot, _n, field) in enumerate(list(COMPARE_MODELS)):
        prov = MODEL_PROVIDER.get(slot)
        name = "gois.v1" if not prov else (sel.get(prov) or prov)
        COMPARE_MODELS[i] = (slot, name, field)
        MODEL_NAME[slot] = name
# Notas antigas (até à v2.3): continuam guardadas no servidor e entram no CSV, mas já não são comparadas.
LEGACY_FIELDS = (("Gemini (arquivo)", "gemini_ref"), ("DeepSeek (arquivo)", "deepseek_ref"))


def _score(e, field):
    v = e.get(field) if isinstance(e, dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def common_categories(cats):
    """Categorias em que TODOS os modelos comparados têm nota (a base justa para totais e ranking)."""
    return [c for c, e in (cats or {}).items() if all(_score(e, f) is not None for _k, _n, f in COMPARE_MODELS)]


def model_totals(cats, fair=True):
    """{modelo: (média, nº de categorias usadas)}.
    fair=True (por omissão): só as categorias medidas nos TRÊS modelos, para ninguém ser favorecido/prejudicado por ter
    mais ou menos categorias medidas. Enquanto ainda não há nenhuma categoria comum, usa as notas próprias de cada um.
    fair=False: média sobre todas as categorias que esse modelo já tem medidas."""
    common = set(common_categories(cats)) if fair else set()
    out = {}
    for key, _n, field in COMPARE_MODELS:
        pool = [e for c, e in (cats or {}).items() if (c in common if common else True)]
        v = [x for x in (_score(e, field) for e in pool) if x is not None]
        out[key] = (round(sum(v) / len(v), 2) if v else None, len(v))
    return out


def measured_counts(cats):
    """{modelo: categorias com nota própria} (cobertura real de cada um, independente da base justa)."""
    return {key: sum(1 for e in (cats or {}).values() if _score(e, field) is not None) for key, _n, field in COMPARE_MODELS}


def rank_models(cats):
    """[(modelo, nome, média, categorias)] do mais forte para o mais fraco. Sem notas vai para o fim; empates mantêm a ordem fixa."""
    tot = model_totals(cats)
    rows = [(k, n, tot[k][0], tot[k][1]) for k, n, _f in COMPARE_MODELS]
    return sorted(rows, key=lambda r: (r[2] is None, -(r[2] if r[2] is not None else 0)))


def head_to_head(cats, a, b):
    """a contra b só nas categorias onde os dois têm nota: (à frente, atrás, empates, Δ médio ou None, categorias em comum)."""
    win = lose = tie = 0
    diffs = []
    for e in (cats or {}).values():
        x, y = _score(e, MODEL_FIELD[a]), _score(e, MODEL_FIELD[b])
        if x is None or y is None:
            continue
        diffs.append(x - y)
        if x > y:
            win += 1
        elif x < y:
            lose += 1
        else:
            tie += 1
    return win, lose, tie, (round(sum(diffs) / len(diffs), 2) if diffs else None), len(diffs)


def focus_leads(e, focus):
    """True/False se o modelo em foco lidera (ou empata na frente) esta categoria; None se ainda não é comparável."""
    s = _score(e, MODEL_FIELD[focus])
    refs = [r for r in (_score(e, f) for k, _n, f in COMPARE_MODELS if k != focus) if r is not None]
    if s is None or not refs:
        return None
    return s >= max(refs)

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


DATASET_SYSTEM = "Tu és o gois.v1, um assistente técnico. Responde em português europeu, de forma direta e completa."


def build_dataset_rows(lessons, min_chars=200):
    """Converte lições da Sala de Aula em exemplos de afinação (formato chat 'messages').

    Devolve (linhas, ignoradas). Ignora lições vazias/curtas e títulos repetidos.
    """
    rows, seen, skipped = [], set(), 0
    for k in lessons:
        title = str(k.get("title") or "").strip()
        body = str(k.get("content") or "").strip()
        key = title.lower()
        if not title or len(body) < min_chars or key in seen:
            skipped += 1
            continue
        seen.add(key)
        cat = str(k.get("category") or "").strip()
        question = f"Explica em detalhe e com exemplos práticos: {title}" + (f" (área: {cat})" if cat else "")
        rows.append({"messages": [
            {"role": "system", "content": DATASET_SYSTEM},
            {"role": "user", "content": question},
            {"role": "assistant", "content": body},
        ]})
    return rows, skipped


# --------------------------------------------------------------------------- #
# Modelo próprio (LM Studio + túnel) — funções auxiliares
# --------------------------------------------------------------------------- #
LM_PORT = 1234                      # porta por defeito do servidor do LM Studio
LOCAL_IDENTIFIER = "gois-v1"        # nome com que o modelo fica carregado (usado pelo servidor)
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_TUNNEL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com", re.I)


def find_exe(name, extras=()):
    found = shutil.which(name)
    if found:
        return found
    for c in extras:
        p = Path(c).expanduser()
        if p.is_file():
            return str(p)
    return ""


def find_lms():
    home = Path.home()
    return find_exe("lms", [home / ".lmstudio" / "bin" / "lms.exe", home / ".lmstudio" / "bin" / "lms"])


def find_cloudflared():
    return find_exe("cloudflared", [r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                                    r"C:\Program Files\cloudflared\cloudflared.exe", Path.home() / "cloudflared.exe"])


def extract_tunnel_url(text):
    """Primeiro https://xxxx.trycloudflare.com da linha (ignora api.trycloudflare.com, que é o serviço)."""
    for m in _TUNNEL_RE.finditer(text or ""):
        if m.group(1).lower() != "api":
            return m.group(0)
    return ""


def run_cmd(args, timeout=60):
    """Corre um comando sem abrir janela. Devolve (código, saída)."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return -1, f"Tempo esgotado ({timeout}s)."
    except Exception as e:
        return -1, str(e)


def default_models_dir():
    for c in (Path.home() / ".lmstudio" / "models", Path.home() / ".cache" / "lm-studio" / "models"):
        if c.is_dir():
            return str(c)
    return str(Path.home() / ".lmstudio" / "models")


def newest_gguf(folder, pattern="gois-v1-*.gguf", settle_s=20):
    """Ficheiro .gguf mais recente na pasta, ignorando os que ainda estão a ser descarregados/sincronizados."""
    try:
        base = Path(folder)
        if not folder or not base.is_dir():
            return None
        best, now = None, time.time()
        for p in base.glob(pattern):
            try:
                st = p.stat()
            except OSError:
                continue
            if now - st.st_mtime < settle_s:
                continue
            if best is None or st.st_mtime > best[0]:
                best = (st.st_mtime, p)
        return best[1] if best else None
    except Exception:
        return None


def gguf_signature(p):
    st = Path(p).stat()
    return f"{Path(p).name}|{st.st_size}|{int(st.st_mtime)}"


def install_gguf(src, models_dir):
    """Copia o .gguf para <modelos do LM Studio>/gois/gois-v1/ (estrutura editor/modelo/ficheiro)."""
    src = Path(src)
    dest_dir = Path(models_dir) / "gois" / "gois-v1"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        tmp = dest_dir / (src.name + ".part")
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    return dest


def pick_model_key(ls_output, hint):
    """Procura em `lms ls --json` a chave do modelo cujo caminho contém `hint` (devolve '' se não houver)."""
    try:
        text = ls_output[ls_output.index("["):]
        models = json.loads(text)
    except Exception:
        return ""
    hint = (hint or "").lower()
    best = ""
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict):
            continue
        key = str(m.get("modelKey") or m.get("path") or "")
        path = str(m.get("path") or key)
        if hint and hint in (key + " " + path).lower():
            best = key
    return best


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
        fm = self.cfg.get("focus_model", "gois")
        # Modelo em foco na Comparação: só muda o destaque e os Δ; os dados de todos os modelos ficam sempre guardados.
        self.focus_model = fm if fm in MODEL_FIELD else "gois"
        hist = read_json(HISTORY_FILE, [])
        self.history = hist if isinstance(hist, list) else []      # (antigo, já não usado no gráfico: vem do servidor)
        sm = self.cfg.get("sel_models")
        self.sel_models = {p: (sm.get(p) if isinstance(sm, dict) else None) for p in MODEL_PROVIDER.values()}
        self.provider_models = {}          # lista real de modelos por fornecedor (GET /api/provider-models)
        set_model_labels(self.sel_models)
        self.kb = []
        self.class_busy = False
        self.class_kind = None            # 'learn' | 'benchmark' enquanto este painel executa algo
        self.class_cat = ""
        self.server_job = {}               # o que o servidor diz que está a correr (/api/job)
        self.model_state = "nosession"    # online | offline | nolink | noserver | nosession | checking
        self.lamps = []
        self._lamp_t = 0.0
        self._health_busy = False
        self._job_busy = False
        self._health_defer = None
        self._emb = []
        self._conn_after = None
        self._poll_after = None
        self.ui_q = queue.Queue()
        self.local_proc = None
        self.local_url = ""
        self.local_busy = False
        self._installing = False
        self._local_after = None
        self.local_log_lines = []
        self.local_status_state = ("⚪ Parado", "muted")
        self.prompts_data = {}
        self.prompt_widgets = {}

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
        self.after(200, self.anim_tick)
        self.after(3_500, self.health_tick)
        self.models_health = None          # último resultado de /api/health/models (+ teste local do LM Studio)
        self._mh_busy = False
        self.after(6_000, self.models_health_tick)
        self.after(4_500, self.job_tick)
        self.after(400, self.first_run_token)
        atexit.register(self.stop_tunnel)              # não deixa o túnel aberto ao fechar o painel
        self.after(15_000, self.local_watch_tick)      # procura .gguf novo (instalação automática)
        if self.cfg.get("auto_start_local"):
            self.after(8_000, lambda: self.auto_start_tick(0))
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

    def kpi_card(self, parent, key, title, color=None, big=False, status=False):
        f = self.card(parent)
        f.pack(side=tk.LEFT, padx=(0, 8), fill=tk.Y)
        self.kpi_frames[key] = f
        top = tk.Frame(f, bg=THEME["panel"])
        top.pack(fill=tk.X, padx=12, pady=(8, 0))
        if color:
            tk.Label(top, text="●", fg=color, bg=THEME["panel"], font=("Segoe UI", 10)).pack(side=tk.LEFT)
        tl = tk.Label(top, text=title, fg=THEME["muted"], bg=THEME["panel"], font=("Segoe UI Semibold", 9))
        tl.pack(side=tk.LEFT, padx=(4 if color else 0, 0))
        self.kpi_title[key] = tl
        val = tk.Label(f, text="—", fg=color or THEME["fg"], bg=THEME["panel"], font=("Segoe UI Semibold", 22 if big else 14))
        val.pack(anchor=tk.W, padx=12)
        sub = tk.Label(f, text="", fg=THEME["muted"], bg=THEME["panel"], font=("Segoe UI", 8))
        sub.pack(anchor=tk.W, padx=12, pady=(0, 0 if status else 8))
        self.kpi[key] = (val, sub)
        if status:
            st = tk.Label(f, text="… a verificar chave e ligação", fg=THEME["muted"], bg=THEME["panel"],
                          font=("Segoe UI Semibold", 8), cursor="hand2", wraplength=170, justify=tk.LEFT, anchor="w")
            st.pack(anchor=tk.W, padx=12, pady=(0, 8))
            st.bind("<Button-1>", lambda _e, k=key: self.show_model_health(k))
            self.kpi_status[key] = st

    # ---------------- UI base ----------------
    def build_ui(self):
        self.kpi = {}
        self.kpi_status = {}
        self.kpi_title = {}
        self.kpi_frames = {}
        self._kpi_order = None
        self.lamps = []
        header = tk.Frame(self, bg=THEME["panel"], height=54, highlightbackground=THEME["border"], highlightthickness=1)
        header.pack(side=tk.TOP, fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=" g ", bg=THEME["accent"], fg="#ffffff", font=("Segoe UI Semibold", 14)).pack(side=tk.LEFT, padx=(16, 8), pady=10)
        tk.Label(header, text="gois.v1", bg=THEME["panel"], fg=THEME["fg"], font=("Segoe UI Semibold", 13)).pack(side=tk.LEFT)
        self.model_lamp = self.make_lamp(header, THEME["panel"], 210, 30)
        self.model_lamp.pack(side=tk.LEFT, padx=(10, 0))
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
        for key, name, _f in COMPARE_MODELS:
            self.kpi_card(strip, key, f"{name} · total geral", series_color(key), big=True, status=True)
        self.kpi_sep = tk.Frame(strip, bg=THEME["border"], width=1)
        self.kpi_sep.pack(side=tk.LEFT, fill=tk.Y, padx=(2, 10), pady=4)
        self.kpi_card(strip, "par_mis", "Paridade vs Mistral")
        self.kpi_card(strip, "par_grq", "Paridade vs Groq")
        self.kpi_card(strip, "lessons", "Lições")
        self.kpi_card(strip, "skills", "Skills")
        self.kpi_card(strip, "capacity", "Capacidade restante hoje (estim.)")
        self.kpi_card(strip, "tokens", "Tokens hoje")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tab_chat = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_comp = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_class = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_skills = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_sys = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_local = tk.Frame(self.notebook, bg=THEME["bg"])
        self.tab_prompts = tk.Frame(self.notebook, bg=THEME["bg"])
        self.notebook.add(self.tab_chat, text="💬 Chat & Histórico")
        self.notebook.add(self.tab_comp, text="📊 Comparação Live")
        self.notebook.add(self.tab_class, text="🏫 Sala de Aula")
        self.notebook.add(self.tab_skills, text="🧠 Skills")
        self.notebook.add(self.tab_sys, text="🛠 Sistema")
        self.notebook.add(self.tab_local, text="🤖 Modelo próprio")
        self.notebook.add(self.tab_prompts, text="📜 Diretrizes")

        self.build_chat_tab()
        self.build_comparison_tab()
        self.build_classroom_tab()
        self.build_skills_tab()
        self.build_system_tab()
        self.build_local_tab()
        self.build_prompts_tab()
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
        self.local_ui_restore()
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
                        self.sync_prompts()
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
        if tab == 6 and self.is_admin:
            self.load_prompts()
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
        win.geometry("560x620")
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
        e_colab = row("URL do notebook no Colab (https://colab.research.google.com/...)", self.cfg.get("colab_url", ""))

        def save():
            self.cfg["server"] = e_server.get().strip().rstrip("/") or DEFAULT_SERVER
            self.cfg["token"] = e_token.get().strip()
            self.cfg["colab_url"] = e_colab.get().strip()
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
    MODELS = COMPARE_MODELS

    def raw_cats(self):
        return (self.server_settings.get("comparison") or {}).get("categories") or {}

    def cat_data(self):
        """Categorias com as notas dos modelos ESCOLHIDOS (1 do Mistral, 1 do Groq) nos campos de cada lugar.
        Os dados dos outros modelos continuam no servidor a evoluir e aparecem assim que os escolheres."""
        out = {}
        for c, e in self.raw_cats().items():
            d = dict(e)
            mods = e.get("models") or {}
            for slot, prov in MODEL_PROVIDER.items():
                sel = self.sel_models.get(prov)
                m = mods.get(mkey(prov, sel)) if sel else None
                m = m if isinstance(m, dict) else {}
                d[MODEL_FIELD[slot]] = m.get("score")
                d[slot + "_model"], d[slot + "_n"], d[slot + "_at"] = sel, m.get("n"), m.get("at")
            out[c] = d
        return out

    def init_selection(self):
        """Primeira vez (ou modelo que já não existe): escolhe o modelo com mais medições, senão o primeiro em medição."""
        comp = self.server_settings.get("comparison") or {}
        summ, tracked = comp.get("models_summary") or {}, self.server_settings.get("tracked_models") or {}
        changed = False
        for prov in MODEL_PROVIDER.values():
            cur = self.sel_models.get(prov)
            if cur and (mkey(prov, cur) in summ or cur in (tracked.get(prov) or [])):
                continue
            mine = sorted(((v.get("n", 0), k.split("|", 1)[1]) for k, v in summ.items() if k.startswith(prov + "|")), reverse=True)
            pick = mine[0][1] if mine else ((tracked.get(prov) or [None])[0])
            if pick and pick != cur:
                self.sel_models[prov] = pick
                changed = True
        if changed:
            self.cfg["sel_models"] = dict(self.sel_models)
            self.save_config()
        set_model_labels(self.sel_models)

    def totals(self):
        return model_totals(self.cat_data())

    def ranking(self):
        """[(modelo, nome, média, categorias)] do mais forte para o mais fraco, recalculado a cada leitura do servidor."""
        return rank_models(self.cat_data())

    def focus_key(self):
        k = getattr(self, "focus_model", "gois")
        return k if k in MODEL_FIELD else "gois"

    def leadership(self, focus=None):
        """(categorias em que o modelo em foco lidera, categorias comparáveis)"""
        focus = focus or self.focus_key()
        wins = total = 0
        for e in self.cat_data().values():
            lead = focus_leads(e, focus)
            if lead is not None:
                total += 1
                wins += bool(lead)
        return wins, total

    def set_focus(self, key):
        """Escolhe o modelo em foco. Só muda o destaque, os Δ e o resumo: NENHUM dado é apagado e os outros modelos
        continuam todos visíveis (esbatidos), por isso podes trocar de modelo quantas vezes quiseres."""
        if key not in MODEL_FIELD:
            return
        self.focus_model = key
        self.cfg["focus_model"] = key
        self.save_config()
        self.update_comparison_ui()

    # ---- escolha do modelo de cada fornecedor ----
    def on_pill(self, key, ev):
        self.set_focus(key)
        if key in MODEL_PROVIDER:
            self.open_model_menu(MODEL_PROVIDER[key], ev.widget)

    def provider_rows(self, prov):
        """[(id, tracked, avg, n, at)] — da lista real do fornecedor (se já vier) + tudo o que está em medição / já medido."""
        comp = self.server_settings.get("comparison") or {}
        summ = comp.get("models_summary") or {}
        tracked = (self.server_settings.get("tracked_models") or {}).get(prov) or []
        info = self.provider_models.get(prov) or {}
        ids = {m["id"] for m in info.get("models", [])} | set(tracked) | {k.split("|", 1)[1] for k in summ if k.startswith(prov + "|")}
        rows = []
        for m in ids:
            v = summ.get(mkey(prov, m)) or {}
            rows.append((m, m in tracked, v.get("avg"), v.get("n", 0), v.get("at") or ""))
        return sorted(rows, key=lambda r: (not r[1], r[2] is None, -(r[2] or 0), r[0]))

    def open_model_menu(self, prov, widget):
        self.fetch_provider_models(quiet=True)               # aquece a lista real para a próxima vez
        menu = tk.Menu(self, tearoff=0, bg=THEME["panel"], fg=THEME["fg"], activebackground=THEME["accent"], activeforeground="#ffffff")
        info = self.provider_models.get(prov) or {}
        menu.add_command(label=f"Modelos do {prov} — escolhe 1 para comparar com o gois.v1"
                               + ("" if info.get("live") else "  (lista ainda não carregada: usa ⟳)"), state=tk.DISABLED)
        menu.add_separator()
        cur = self.sel_models.get(prov)
        rows = self.provider_rows(prov)
        for m, tr, avg, n, at in rows:
            if avg is not None:
                txt = f"{m}    {fscore(avg)} %  ·  {n} medições  ·  há {self.age_txt(at)}"
            elif tr:
                txt = f"{m}    a medir — ainda sem notas (corre um benchmark)"
            else:
                txt = f"{m}    não medido (passa a ser medido ao escolher)"
            menu.add_radiobutton(label=("✔ " if m == cur else "    ") + txt, command=lambda mm=m: self.select_model(prov, mm))
        stoppable = [r[0] for r in rows if r[1] and r[0] != cur]
        if stoppable and len((self.server_settings.get("tracked_models") or {}).get(prov) or []) > 1:
            menu.add_separator()
            sub = tk.Menu(menu, tearoff=0, bg=THEME["panel"], fg=THEME["fg"])
            for m in stoppable:
                sub.add_command(label=m, command=lambda mm=m: self.track_model(prov, mm, False))
            menu.add_cascade(label="Parar de medir (os dados ficam guardados) ▸", menu=sub)
        menu.add_separator()
        menu.add_command(label=f"⟳ Atualizar a lista real de modelos do {prov}", command=lambda: self.fetch_provider_models(refresh=True))
        try:
            menu.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            menu.grab_release()

    def fetch_provider_models(self, refresh=False, quiet=False):
        if not (self.connected and self.is_admin):
            return

        def done(res, err):
            if err or not isinstance(res, dict):
                if not quiet:
                    self.flash(f"Não consegui obter a lista de modelos: {err}", THEME["danger"], 9000)
                return
            self.provider_models = res
            if not quiet:
                self.flash("✔ Lista real de modelos atualizada.", THEME["bot"], 5000)

        self.bg(lambda: self.api.call("GET", "/api/provider-models" + ("?refresh=1" if refresh else ""), timeout=60), done)

    def select_model(self, prov, model):
        """Escolhe o modelo do fornecedor que é comparado com o gois.v1. Não apaga nada: os outros continuam a ser medidos."""
        self.sel_models[prov] = model
        self.cfg["sel_models"] = dict(self.sel_models)
        self.save_config()
        set_model_labels(self.sel_models)
        self.update_comparison_ui()
        self.update_kpis()
        tracked = (self.server_settings.get("tracked_models") or {}).get(prov) or []
        has_data = any(mkey(prov, model) == k for k in ((self.server_settings.get("comparison") or {}).get("models_summary") or {}))
        if model not in tracked:
            self.track_model(prov, model, True)
        elif not has_data:
            self.flash(f"{model} ainda não tem notas: corre ⚔ Benchmark para o medir.", THEME["warn"], 9000)

    def track_model(self, prov, model, on):
        def done(res, err):
            if err:
                self.flash(f"❌ {err}", THEME["danger"], 12000)
                if on and self.sel_models.get(prov) == model:       # não deixes escolhido um modelo que não é medido
                    self.sel_models[prov] = None
                    self.init_selection()
                    self.update_comparison_ui()
                    self.update_kpis()
                return
            self.apply_settings(res)
            self.flash((f"✔ {model} passa a ser medido em cada benchmark (corre ⚔ para ter as primeiras notas)." if on
                        else f"{model} deixou de ser medido (dados guardados)."), THEME["bot"], 9000)

        self.bg(lambda: self.api.call("POST", "/api/models/track", {"provider": prov, "model": model, "tracked": on}, timeout=40), done)

    def reorder_model_cards(self):
        """Os cartões dos totais (topo) ficam sempre do mais forte para o mais fraco; só se mexem quando a ordem muda."""
        order = [r[0] for r in self.ranking()]
        frames, sep = getattr(self, "kpi_frames", {}), getattr(self, "kpi_sep", None)
        if order == getattr(self, "_kpi_order", None) or sep is None or any(k not in frames for k in order):
            return
        self._kpi_order = order
        for k in order:
            frames[k].pack_forget()
        for k in order:
            frames[k].pack(side=tk.LEFT, padx=(0, 8), fill=tk.Y, before=sep)

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
        self.bench_lamp = self.make_lamp(top, THEME["bg"], 330, 34)
        self.bench_lamp.pack(side=tk.LEFT, padx=8)
        self.btn(top, "⬇ CSV", self.export_comparison_csv).pack(side=tk.RIGHT, padx=3)
        self.bench_all_btn = self.btn(top, "⚔ Benchmark às 16", self.bench_all)
        self.bench_all_btn.pack(side=tk.RIGHT, padx=3)
        self.bench_btn = self.btn(top, "⚔ Benchmark da categoria", self.run_benchmark_ui, THEME["accent_blue"])
        self.bench_btn.pack(side=tk.RIGHT, padx=3)

        ctl = tk.Frame(t, bg=THEME["bg"])
        ctl.pack(fill=tk.X, padx=18, pady=(2, 0))
        tk.Label(ctl, text="Modelo em foco:", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        self.pick_btns = {}
        for key, name, _f in self.MODELS:
            b = tk.Label(ctl, text=f"● {name}", fg=series_color(key), bg=THEME["panel"], font=("Segoe UI Semibold", 9),
                         padx=10, pady=3, cursor="hand2", highlightthickness=2, highlightbackground=THEME["border"])
            b.pack(side=tk.LEFT, padx=3)
            b.bind("<Button-1>", lambda e, k=key: self.on_pill(k, e))
            self.pick_btns[key] = b
        cb = ttk.Combobox(ctl, textvariable=self.live_s, values=["5", "10", "30", "60"], width=4, state="readonly")
        cb.pack(side=tk.RIGHT)
        cb.bind("<<ComboboxSelected>>", lambda _e: (self.cfg.__setitem__("live_s", self.live_interval()), self.save_config()))
        tk.Label(ctl, text="Atualizar a cada (s):", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=(12, 4))
        tk.Checkbutton(ctl, text="🔁 benchmark contínuo (gasta tokens)", variable=self.auto_bench, bg=THEME["bg"], fg=THEME["muted"],
                       selectcolor=THEME["code_bg"], activebackground=THEME["bg"], activeforeground=THEME["fg"],
                       font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=8)
        tk.Label(t, text="Notas 0–100 dadas por um modelo-juiz (LLM) a respostas anónimas à mesma pergunta; cada modelo "
                         "é chamado de verdade pelo servidor. Clica em Mistral ▾ ou Groq ▾ para escolher QUAL dos modelos do fornecedor "
                         "comparas com o gois.v1: todos os modelos em medição continuam a ser avaliados em cada benchmark, por isso "
                         "ao trocar vês a evolução real deles. São notas indicativas, não um teste oficial.", bg=THEME["bg"], fg=THEME["muted"],
                 font=("Segoe UI", 8), anchor=tk.W, justify=tk.LEFT, wraplength=1200).pack(fill=tk.X, padx=18, pady=(2, 0))

        pw = tk.PanedWindow(t, orient=tk.VERTICAL, bg=THEME["border"], sashwidth=4)
        pw.pack(fill=tk.BOTH, expand=True, padx=18, pady=8)
        upper = tk.Frame(pw, bg=THEME["bg"])
        pw.add(upper, minsize=340, height=490)
        left = tk.Frame(upper, bg=THEME["bg"])
        left.pack(side=tk.LEFT, fill=tk.Y)
        c0 = self.card(left)
        c0.pack(fill=tk.X, pady=(0, 8))
        self.cv_rank = tk.Canvas(c0, width=470, height=138, bg=THEME["panel"], highlightthickness=0, cursor="hand2")
        self.cv_rank.pack(fill=tk.X)
        c1 = self.card(left)
        c1.pack(fill=tk.X, pady=(0, 8))
        self.cv_overall = tk.Canvas(c1, width=470, height=160, bg=THEME["panel"], highlightthickness=0)
        self.cv_overall.pack(fill=tk.X)
        c2 = self.card(left)
        c2.pack(fill=tk.BOTH, expand=True)
        self.cv_trend = tk.Canvas(c2, width=470, height=150, bg=THEME["panel"], highlightthickness=0)
        self.cv_trend.pack(fill=tk.BOTH, expand=True)
        c3 = self.card(upper)
        c3.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.focus_lbl = tk.Label(c3, text="", bg=THEME["panel"], fg=THEME["fg"], font=("Segoe UI", 9), justify=tk.LEFT,
                                  anchor="w", wraplength=620)
        self.focus_lbl.pack(side=tk.TOP, fill=tk.X, padx=12, pady=(8, 4))
        c3.bind("<Configure>", lambda e: self.focus_lbl.config(wraplength=max(300, e.width - 28)))
        vs = ttk.Scrollbar(c3, orient="vertical")
        self.cv_cats = tk.Canvas(c3, bg=THEME["panel"], highlightthickness=0, yscrollcommand=vs.set)
        vs.config(command=self.cv_cats.yview)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.cv_cats.pack(fill=tk.BOTH, expand=True)
        self.cv_cats.bind("<MouseWheel>", lambda e: self.cv_cats.yview_scroll(int(-e.delta / 120), "units"))
        self.cv_rank.bind("<Configure>", lambda _e: self.draw_rank())
        self.cv_rank.bind("<Button-1>", self.on_rank_click)
        self.cv_overall.bind("<Configure>", lambda _e: self.draw_overall())
        self.cv_trend.bind("<Configure>", lambda _e: self.draw_trend())
        self.cv_cats.bind("<Configure>", lambda _e: self.draw_cats())

        tf = tk.Frame(pw, bg=THEME["panel"])
        pw.add(tf, minsize=140)
        cols = ("n", "cat", "gois", "mis", "grq", "dg", "dd", "les", "bm", "last")
        heads = (("n", 36, "#"), ("cat", 280, "Categoria"), ("gois", 90, "gois.v1"), ("mis", 150, "Mistral"), ("grq", 150, "Groq"),
                 ("dg", 150, "Δ vs …"), ("dd", 150, "Δ vs …"), ("les", 60, "Lições"), ("bm", 85, "Benchmarks"), ("last", 130, "Último"))
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
        return fscore(v, nd)

    @staticmethod
    def fdelta(a, b):
        return "—" if a is None or b is None else fscore(a - b, 1, True)

    def update_comparison_ui(self):
        if not hasattr(self, "comp_tree"):
            return
        try:
            cats = self.cat_data()
            focus = self.focus_key()
            others = [k for k, _n, _f in self.MODELS if k != focus]
            for col, (k, n, _f) in zip(("gois", "mis", "grq"), self.MODELS):
                self.comp_tree.heading(col, text=("★ " if k == focus else "") + n)
            for col, k in zip(("dg", "dd"), others):
                self.comp_tree.heading(col, text=f"Δ {MODEL_NAME[focus]} vs {MODEL_NAME[k]}")
            for i, c in enumerate(DEFAULT_CATEGORIES):
                e = cats.get(c) or {}
                sc = {k: _score(e, f) for k, _n, f in self.MODELS}
                last = str(e.get("last_benchmark") or "")[:16].replace("T", " ")
                self.comp_tree.item(str(i), tags=("lead",) if focus_leads(e, focus) else (), values=(
                    i + 1, c, *[self.fnum(sc[k]) for k, _n, _f in self.MODELS],
                    self.fdelta(sc[focus], sc[others[0]]), self.fdelta(sc[focus], sc[others[1]]),
                    e.get("lessons", 0), e.get("benchmarks", 0), last))
            self.refresh_picker()
            self.refresh_focus_panel()
            self.draw_all_charts()
        except tk.TclError:
            pass

    def refresh_picker(self):
        focus = self.focus_key()
        for key, b in getattr(self, "pick_btns", {}).items():
            try:
                on = key == focus
                prov = MODEL_PROVIDER.get(key)
                b.config(text=f"● {prov} ▾  {short(MODEL_NAME[key], 28)}" if prov else f"● {MODEL_NAME[key]}")
                b.config(bg=THEME["panel_alt"] if on else THEME["panel"],
                         highlightbackground=series_color(key) if on else THEME["border"])
            except tk.TclError:
                pass

    def refresh_focus_panel(self):
        """Resumo do modelo em foco contra cada um dos outros (só categorias em que ambos têm nota)."""
        lbl = getattr(self, "focus_lbl", None)
        if lbl is None:
            return
        cats, focus = self.cat_data(), self.focus_key()
        rows = self.ranking()
        pos = next(i for i, r in enumerate(rows, 1) if r[0] == focus)
        _k, name, avg, n = rows[pos - 1]
        if avg is None:
            lines = [f"▶ {name}: ainda sem notas, corre um benchmark"]
        else:
            lines = [f"▶ {name}:  total {fscore(avg)} %  ·  #{pos} de {len(rows)}  ·  {n}/{len(DEFAULT_CATEGORIES)} categorias medidas"]
        for k, other, _f in self.MODELS:
            if k == focus:
                continue
            win, lose, tie, delta, common = head_to_head(cats, focus, k)
            if common:
                lines.append(f"      vs {other}: à frente em {win} · atrás em {lose} · empate em {tie} · Δ médio {fscore(delta, 1, True)}  ({common} categorias em comum)")
            else:
                lines.append(f"      vs {other}: ainda sem categorias em comum")
        lines.append("")
        cc = len(common_categories(cats))
        lines.append(f"Comparação real entre o gois.v1 e os modelos escolhidos (totais só nas {cc} categorias medidas nos três):")
        mc = measured_counts(cats)
        for k, n, field in self.MODELS:
            ents = [e for e in cats.values() if _score(e, field) is not None]
            stamps = [str(e.get(f"{k}_at") or "") for e in ents if e.get(f"{k}_at")]
            age = self.age_txt(max(stamps)) if stamps else "—"
            samples = sum(int(e.get(f"{k}_n") or 0) for e in ents)
            lines.append(f"      {n}: {mc[k]}/{len(DEFAULT_CATEGORIES)} categorias · {samples} medições · última há {age}")
        summ = (self.server_settings.get("comparison") or {}).get("models_summary") or {}
        for prov in MODEL_PROVIDER.values():
            others = sorted(((k.split("|", 1)[1], v) for k, v in summ.items() if k.startswith(prov + "|")
                             and k.split("|", 1)[1] != self.sel_models.get(prov)), key=lambda x: -x[1].get("avg", 0))
            if others:
                lines.append(f"      {prov}, em medição por detrás: " + " · ".join(f"{m} {fscore(v['avg'])}% ({v['n']})" for m, v in others))
        try:
            lbl.config(text="\n".join(lines))
        except tk.TclError:
            pass

    @staticmethod
    def age_txt(iso):
        try:
            t = datetime.fromisoformat(iso).timestamp()
            d = max(0, int(time.time() - t))
        except Exception:
            return "—"
        return f"{d} s" if d < 90 else (f"{d // 60} min" if d < 5400 else (f"{d // 3600} h" if d < 172800 else f"{d // 86400} d"))

    def on_rank_click(self, ev):
        for y0, y1, key in getattr(self, "_rank_rows", []):
            if y0 <= ev.y <= y1:
                self.set_focus(key)
                return

    # ---- gráficos (Canvas) ----
    def _cv_ready(self, cv):
        try:
            cv.delete("all")
            return cv.winfo_width() > 80 and cv.winfo_height() > 80
        except tk.TclError:
            return False

    def draw_all_charts(self):
        self.draw_rank()
        self.draw_overall()
        self.draw_trend()
        self.draw_cats()

    def draw_rank(self):
        """Área nova dos totais: os modelos do mais forte para o mais fraco, sempre ordenados com os dados ao vivo."""
        cv = getattr(self, "cv_rank", None)
        if cv is None or not self._cv_ready(cv):
            return
        w = cv.winfo_width()
        focus, rows, nc = self.focus_key(), self.ranking(), len(DEFAULT_CATEGORIES)
        self._rank_rows = []
        cv.create_text(14, 14, text="★ Ranking ao vivo · do mais forte ao mais fraco", anchor="w", fill=THEME["fg"],
                       font=("Segoe UI Semibold", 10))
        top, row_h, bar_l = 30, 30, 214
        bar_r = max(bar_l + 40, w - 150)
        lead = rows[0][2]
        hi = scale_top([r[2] for r in rows])
        for i, (key, name, val, n) in enumerate(rows):
            y0 = top + i * row_h
            yc = y0 + row_h / 2
            col = series_color(key)
            self._rank_rows.append((y0, y0 + row_h, key))
            if key == focus:
                cv.create_rectangle(6, y0 + 1, w - 6, y0 + row_h - 2, fill=THEME["panel_alt"], outline=col, width=2)
            cv.create_text(16, yc, text=f"#{i + 1}" if val is not None else "·", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 10))
            cv.create_oval(46, yc - 5, 56, yc + 5, fill=col, outline="")
            cv.create_text(64, yc, text=short(name, 20), anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 9))
            cv.create_rectangle(bar_l, yc - 5, bar_r, yc + 5, fill=THEME["border"], outline="")
            if val is None:
                cv.create_text(bar_r + 8, yc, text="—", anchor="w", fill=THEME["muted"], font=("Segoe UI", 9))
                continue
            cv.create_rectangle(bar_l, yc - 5, bar_l + (bar_r - bar_l) * val / hi, yc + 5, fill=col, outline="")
            cv.create_text(bar_r + 8, yc, text=f"{fscore(val)} %", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 9))
            cv.create_text(w - 78, yc, text=f"{n}/{nc}", anchor="w", fill=THEME["muted"], font=("Segoe UI", 8))
            cv.create_text(w - 12, yc, text="líder" if i == 0 else fscore(val - lead, 1, True), anchor="e",
                           fill=THEME["bot"] if i == 0 else THEME["muted"], font=("Segoe UI", 8))
        mc = measured_counts(self.cat_data())
        cc = len(common_categories(self.cat_data()))
        note, ncol = None, THEME["muted"]
        if not any(v is not None for _k, _nm, v, _n in rows):
            note = "ainda sem benchmarks: corre um benchmark"
        elif len(set(mc.values())) > 1 or cc < max(mc.values()):
            note, ncol = (f"totais justos: só as {cc} categorias medidas nos três modelos "
                          f"(cobertura: " + " · ".join(f"{MODEL_NAME[k]} {n}" for k, n in mc.items()) + ")"), THEME["warn"]
        if note:
            cv.create_text(14, top + len(rows) * row_h + 8, text=note, anchor="w", fill=ncol, font=("Segoe UI", 8), width=w - 28)

    def draw_overall(self):
        cv = getattr(self, "cv_overall", None)
        if cv is None or not self._cv_ready(cv):
            return
        w, h = cv.winfo_width(), cv.winfo_height()
        focus = self.focus_key()
        cv.create_text(14, 14, text="Total geral por modelo (do mais forte ao mais fraco)", anchor="w", fill=THEME["fg"],
                       font=("Segoe UI Semibold", 10))
        wins, total = self.leadership(focus)
        sub = (f"{MODEL_NAME[focus]} lidera em {wins} de {total} categorias comparáveis" if total
               else "ainda sem benchmarks — corre um benchmark")
        cv.create_text(14, 30, text=sub, anchor="w", fill=THEME["muted"], font=("Segoe UI", 8))
        left, right, top, bottom = 38, w - 16, 48, h - 26
        rows = self.ranking()
        hi = scale_top([r[2] for r in rows])
        for v in (hi * i / 4 for i in range(5)):
            y = bottom - (bottom - top) * v / hi
            cv.create_line(left, y, right, y, fill=THEME["border"])
            cv.create_text(left - 6, y, text=f"{v:g}", anchor="e", fill=THEME["muted"], font=("Segoe UI", 8))
        slot = (right - left) / len(rows)
        bw = min(56, slot * 0.5)
        for i, (key, name, val, _n) in enumerate(rows):
            col = series_color(key) if key == focus else blend(series_color(key), THEME["panel"], 0.5)
            cx = left + slot * (i + 0.5)
            if val is not None:
                y = bottom - (bottom - top) * val / hi
                cv.create_rectangle(cx - bw / 2, y, cx + bw / 2, bottom, fill=col, outline="")
                cv.create_text(cx, y - 9, text=f"{fscore(val)}%", fill=THEME["fg"], font=("Segoe UI Semibold", 9))
            else:
                cv.create_rectangle(cx - bw / 2, bottom - 2, cx + bw / 2, bottom, fill=THEME["border"], outline="")
                cv.create_text(cx, bottom - 12, text="—", fill=THEME["muted"])
            cv.create_text(cx, bottom + 13, text=short(name, 16), fill=THEME["fg"] if key == focus else THEME["muted"],
                           font=("Segoe UI Semibold" if key == focus else "Segoe UI", 9))

    def draw_trend(self):
        cv = getattr(self, "cv_trend", None)
        if cv is None or not self._cv_ready(cv):
            return
        w, h = cv.winfo_width(), cv.winfo_height()
        cv.create_text(14, 14, text="Evolução real (média de cada modelo nas suas categorias medidas)", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 10))
        pts = self.trend_points()[-150:]
        if len(pts) < 2:
            cv.create_text(w / 2, h / 2, text="A recolher histórico…\ncada benchmark fica guardado no servidor.",
                           fill=THEME["muted"], font=("Segoe UI", 9), justify=tk.CENTER)
            return
        keys = [k for k, _n, _f in self.MODELS]
        vals = [p[k] for p in pts for k in keys if p.get(k) is not None]
        if not vals:
            return
        lo = max(0, int(min(vals)) - 5)
        hi = math.ceil(max(vals)) + 5
        if hi - lo < 10:
            hi = lo + 10
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
            cv.create_text(left - 6, Y(v), text=f"{v:g}", anchor="e", fill=THEME["muted"], font=("Segoe UI", 8))
        fmt = "%H:%M" if span < 86400 else "%d/%m"
        cv.create_text(left, bottom + 13, text=time.strftime(fmt, time.localtime(t0)), anchor="w", fill=THEME["muted"], font=("Segoe UI", 8))
        cv.create_text(right, bottom + 13, text=time.strftime(fmt, time.localtime(t1)), anchor="e", fill=THEME["muted"], font=("Segoe UI", 8))
        focus = self.focus_key()
        for key in sorted(keys, key=lambda k: k == focus):      # o modelo em foco desenha-se por cima
            col = series_color(key) if key == focus else blend(series_color(key), THEME["panel"], 0.55)
            seg = []

            def flush():
                if len(seg) >= 4:
                    cv.create_line(*seg, fill=col, width=3 if key == focus else 2)

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
        focus = self.focus_key()
        row_h, top = 46, 40
        label_w = min(300, int(w * 0.36))
        left, right = label_w + 14, w - 52
        total_h = top + row_h * len(DEFAULT_CATEGORIES) + 8
        cv.config(scrollregion=(0, 0, w, total_h))
        cv.create_text(14, 16, text="Por categoria", anchor="w", fill=THEME["fg"], font=("Segoe UI Semibold", 10))
        hi = scale_top([_score(e, f) for e in cats.values() for _k, _n, f in self.MODELS])
        for v in (hi * i / 4 for i in range(5)):
            x = left + (right - left) * v / hi
            cv.create_line(x, top - 8, x, total_h - 6, fill=THEME["border"])
            cv.create_text(x, top - 16, text=f"{v:g}", fill=THEME["muted"], font=("Segoe UI", 8))
        for i, c in enumerate(DEFAULT_CATEGORIES):
            y0 = top + i * row_h
            if c == self.sel_cat:
                cv.create_rectangle(0, y0 - 3, w, y0 + row_h - 5, fill=THEME["panel_alt"], outline="")
            name = c if len(c) <= 46 else c[:45] + "…"
            cv.create_text(12, y0 + row_h / 2 - 3, text=f"{i + 1}. {name}", anchor="w", width=label_w, fill=THEME["fg"], font=("Segoe UI", 9))
            e = cats.get(c) or {}
            for j, (key, _n, field) in enumerate(self.MODELS):
                v = _score(e, field)
                by = y0 + 3 + j * 12
                if v is None:
                    cv.create_line(left, by + 4, left + 6, by + 4, fill=THEME["border"], width=2)
                    continue
                x = left + (right - left) * v / hi
                on = key == focus
                cv.create_rectangle(left, by, x, by + 9, fill=series_color(key) if on else blend(series_color(key), THEME["panel"], 0.5), outline="")
                cv.create_text(x + 5, by + 4, text=fscore(v, 0), anchor="w", fill=THEME["fg"] if on else THEME["muted"],
                               font=("Segoe UI Semibold" if on else "Segoe UI", 7))

    # ---- benchmarks ----
    def selected_comp_category(self):
        sel = self.comp_tree.selection()
        return DEFAULT_CATEGORIES[int(sel[0])] if sel else None

    def run_benchmark(self, cat, then=None):
        if self.class_busy:
            return False
        self.set_class_busy(True, f"⚔️ Benchmark em [{cat}] (gois.v1 vs todos os modelos Mistral/Groq em medição, pode demorar alguns minutos)…", kind="benchmark", cat=cat)

        def done(res, err):
            self.set_class_busy(False)
            if err:
                self.class_log(f"❌ Erro no benchmark [{cat}]: {err}")
                self.models_health_now()
                self.flash(f"❌ Benchmark [{cat}] falhou: {self.short(err, 110)} (vê o registo da Sala de Aula)", THEME["danger"], 25000)
                if then:
                    self.class_log("⛔ Série de benchmarks parada no primeiro erro (para não gastar tokens).")
                return
            b = res.get("benchmark", {})
            sc = b.get("scores") or {}
            self.class_log(f"🏆 [{cat}] em {b.get('elapsed_s', '?')}s (juiz: {b.get('judge') or '?'}): gois.v1 {self.fnum(sc.get('gois'))}% · "
                           + " · ".join(f"{k.replace('|', '/')} {self.fnum(v)}%" for k, v in sc.items() if k != "gois"))
            if b.get("missing"):
                self.class_log("⚠ Sem resposta neste benchmark (nota antiga mantida, não renovada): "
                               + "; ".join(m.replace("|", "/") for m in b["missing"]))
            if isinstance(res.get("settings"), dict):
                self.apply_settings(res["settings"])
            if then:
                then()

        def work():
            # 1) o servidor gera a pergunta; 2) o gois.v1 responde DIRETAMENTE no LM Studio deste PC (sem túnel,
            # por isso sem o limite de 100 s da Cloudflare / HTTP 524); 3) o servidor junta Mistral/Groq e avalia.
            q = self.api.call("POST", "/api/comparison/question", {"category": cat}, timeout=90)
            question = (q.get("question") or "").strip()
            if not question:
                raise ApiError("O servidor não gerou a pergunta do benchmark.")
            answer = self.local_answer(question, q.get("context") or "")
            return self.api.call("POST", "/api/comparison/benchmark",
                                 {"category": cat, "question": question, "gois_answer": answer}, timeout=300)

        self.bg(work, done)
        return True

    def local_answer(self, question, context=""):
        """Pede a resposta ao LM Studio local (127.0.0.1) — pode demorar minutos num PC lento, não há túnel pelo meio."""
        base = f"http://127.0.0.1:{LM_PORT}/v1"
        model = "gois-v1"
        try:
            with urllib.request.urlopen(base + "/models", timeout=10) as r:
                ids = [m.get("id") for m in (json.loads(r.read().decode("utf-8", "replace")).get("data") or []) if m.get("id")]
            if ids and model not in ids:
                model = ids[0]
        except Exception as e:
            raise ApiError(f"LM Studio não responde em 127.0.0.1:{LM_PORT} ({e}). Abre o LM Studio e inicia o servidor local.")
        system = "Tu és o gois.v1, um assistente técnico. Responde em português europeu, de forma rigorosa e concisa, em no máximo 200 palavras."
        if context:
            system += "\n\nNotas úteis:\n" + context[:1200]
        body = json.dumps({"model": model, "temperature": 0.4, "max_tokens": 400, "stream": False,
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer lm-studio"})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        except Exception as e:
            raise ApiError(f"O LM Studio não conseguiu responder ({e}).")
        text = text.strip()
        if not text:
            raise ApiError("O LM Studio devolveu uma resposta vazia (confirma que o modelo está carregado).")
        return text

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
        path = filedialog.asksaveasfilename(initialfile="comparacao_gois_modelos.csv", defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        cats = self.cat_data()
        pairs = [(a, b) for i, (a, _n, _f) in enumerate(self.MODELS) for (b, _n2, _f2) in self.MODELS[i + 1:]]

        def legacy_avg(fld):
            v = [x for x in (_score(e, fld) for e in cats.values()) if x is not None]
            return round(sum(v) / len(v), 2) if v else None

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f, delimiter=";")
            wr.writerow(["Categoria"] + [n for _k, n, _f in self.MODELS]
                        + [f"Δ {MODEL_NAME[a]} vs {MODEL_NAME[b]}" for a, b in pairs]
                        + ["Lições", "Benchmarks", "Último benchmark"] + [lbl for lbl, _f in LEGACY_FIELDS])
            for c in DEFAULT_CATEGORIES:
                e = cats.get(c) or {}
                sc = {k: _score(e, fld) for k, _n, fld in self.MODELS}
                wr.writerow([c] + [sc[k] for k, _n, _f in self.MODELS] + [self.fdelta(sc[a], sc[b]) for a, b in pairs]
                            + [e.get("lessons", 0), e.get("benchmarks", 0), e.get("last_benchmark") or ""]
                            + [_score(e, fld) for _l, fld in LEGACY_FIELDS])
            tot = self.totals()
            wr.writerow(["TOTAL GERAL"] + [tot[k][0] for k, _n, _f in self.MODELS] + [""] * (len(pairs) + 3)
                        + [legacy_avg(fld) for _l, fld in LEGACY_FIELDS])
            wr.writerow([])
            wr.writerow(["TODOS OS MODELOS EM MEDIÇÃO (escolhidos ou não)", "Fornecedor", "Média", "Categorias", "Medições", "Última"])
            for key, v in sorted(((self.server_settings.get("comparison") or {}).get("models_summary") or {}).items()):
                prov, _, model = key.partition("|")
                wr.writerow([model, prov, v.get("avg"), v.get("cats"), v.get("n"), v.get("at")])
            wr.writerow([])
            wr.writerow(["RANKING (do mais forte ao mais fraco)", "Posição", "Total geral", "Categorias medidas"])
            for i, (_k, name, val, n) in enumerate(self.ranking(), 1):
                wr.writerow([name, i if val is not None else "", val, n])
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
        # Sempre ao vivo, para TODOS os modelos, esteja o foco onde estiver e mesmo durante um benchmark/estudo
        # (é um GET leve; o servidor devolve as notas já gravadas).
        if self.is_admin:
            self.fetch_live()

    def clock_tick(self):
        self.refresh_live_ui()
        self.after(1000, self.clock_tick)

    def trend_points(self):
        """Evolução REAL guardada no servidor (um ponto por benchmark) para o gois.v1 e para os modelos escolhidos."""
        out = []
        for h in (self.server_settings.get("comparison") or {}).get("history") or []:
            if not isinstance(h, dict) or not h.get("t"):
                continue
            ms = h.get("models") or {}
            out.append({"t": h["t"], "gois": h.get("gois"),
                        "mistral": ms.get(mkey("Mistral", self.sel_models.get("Mistral"))),
                        "groq": ms.get(mkey("Groq", self.sel_models.get("Groq")))})
        return out

    def record_history(self):
        return          # substituído pelo histórico do servidor (comparison.history): inclui todos os modelos e a app fechada
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
            pos = {r[0]: i for i, r in enumerate(self.ranking(), 1)}
            mc = measured_counts(self.cat_data())
            for k, _n, _f in self.MODELS:
                v, n = tot[k]
                self.kpi[k][0].config(text="—" if v is None else f"{fscore(v)} %")
                self.kpi[k][1].config(text=(f"#{pos[k]} · " if v is not None else "") + f"{mc[k]}/{nc} medidas · base {n}")
            self.reorder_model_cards()
            pbm = comp.get("parity_by_model") or {}
            for slot, card in (("mistral", "par_mis"), ("groq", "par_grq")):
                prov = MODEL_PROVIDER[slot]
                pv = pbm.get(mkey(prov, self.sel_models.get(prov)))
                self.kpi[card][0].config(text=f"{pv} %" if pv else "—")
                self.kpi[card][1].config(text=f"gois.v1 ÷ {short(MODEL_NAME[slot], 24)}")
                self.kpi_title[card].config(text=f"Paridade vs {short(MODEL_NAME[slot], 24)}")
            for k, n, _f in self.MODELS:
                self.kpi_title[k].config(text=f"{short(n, 24)} · total geral")
            self.kpi["lessons"][0].config(text=str(comp.get("lessons_total", len(self.kb))))
            self.kpi["lessons"][1].config(text="na Sala de Aula")
            self.kpi["skills"][0].config(text=str(len(self.all_skills())))
            self.kpi["skills"][1].config(text=f"{len(BUILTIN_SKILLS)} integradas")
            cap = self.server_settings.get("capacity") or {}
            if cap.get("total_capacity"):
                self.kpi["capacity"][0].config(text=self.fmt_tok(cap.get("total_remaining", 0)))
                names = [n for n, r in (cap.get("providers") or {}).items() if r.get("capacity")]
                self.kpi["capacity"][1].config(
                    text=f"de {self.fmt_tok(cap['total_capacity'])} · {', '.join(names)} (estimativa)")
            else:
                self.kpi["capacity"][0].config(text="—")
                self.kpi["capacity"][1].config(text="define-a na Sala de Aula")
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
                ("Roteamento do chat", "Admin → só o teu modelo · Público → só fornecedores"),
                ("Fornecedores com chave (público + Sala de Aula)", ", ".join(s.get("providers_with_key", [])) or "—"),
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
        self.study_lamp = self.make_lamp(top, THEME["bg"], 330, 30)
        self.study_lamp.pack(side=tk.RIGHT, padx=(10, 0))
        self.btn(top, "📦 Exportar dataset", self.export_dataset, THEME["accent_blue"]).pack(side=tk.RIGHT)
        self.btn(top, "🧪 Testar Colab", self.test_colab).pack(side=tk.RIGHT, padx=6)
        self.btn(top, "🚀 Atualizar modelo (Colab)", self.update_model_colab, THEME["accent"]).pack(side=tk.RIGHT, padx=6)
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

        capf = tk.Frame(self.tab_class, bg=THEME["bg"])
        capf.pack(fill=tk.X, padx=20, pady=(0, 4))
        tk.Label(capf, text="Capacidade/dia estimada (0 = desconhecida):", bg=THEME["bg"], fg=THEME["muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.cap_vars = {}
        self._cap_loaded = False
        for name in ("Groq", "Gemini", "OpenRouter", "Mistral"):
            tk.Label(capf, text=name, bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(10, 3))
            var = tk.StringVar(value="0")
            self.cap_vars[name] = var
            tk.Entry(capf, textvariable=var, width=9, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white",
                     relief=tk.FLAT).pack(side=tk.LEFT, ipady=3)
        self.btn(capf, "Guardar", self.save_capacity).pack(side=tk.LEFT, padx=6)
        self.btn(capf, "→ 75 % no limite/dia", self.suggest_lesson_limit).pack(side=tk.LEFT)

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

    def set_class_busy(self, busy, msg=None, kind=None, cat=""):
        self.class_busy = busy
        self.class_kind = kind if busy else None
        self.class_cat = cat if busy else ""
        self.refresh_lamps()
        st = tk.DISABLED if busy else tk.NORMAL
        for b in (self.study_btn, self.study_all_btn, self.bench_btn, self.bench_all_btn):
            b.config(state=st)
        if msg:
            self.class_log(msg)

    def selected_class_category(self):
        sel = self.cat_list.curselection()
        return DEFAULT_CATEGORIES[sel[0]] if sel else DEFAULT_CATEGORIES[0]

    def study_category(self, cat, then=None):
        self.set_class_busy(True, f"⚡ A estudar [{cat}] com modelo real…", kind="learn", cat=cat)

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

    @staticmethod
    def fmt_tok(n):
        return f"{int(n or 0):,}".replace(",", ".")

    @staticmethod
    def parse_tok(raw):
        """'300k' -> 300000 ; '1m' -> 1000000 ; vazio -> 0. Levanta ValueError se inválido."""
        raw = (raw or "").strip().lower().replace(".", "").replace(",", "").replace(" ", "")
        mult = 1
        if raw.endswith("k"):
            raw, mult = raw[:-1], 1_000
        elif raw.endswith("m"):
            raw, mult = raw[:-1], 1_000_000
        return int(raw or 0) * mult

    def save_capacity(self):
        if not self.require_admin_ui():
            return
        try:
            caps = {n: self.parse_tok(v.get()) for n, v in self.cap_vars.items()}
        except ValueError:
            messagebox.showwarning("Capacidade", "Número inválido (ex.: 300000 ou 300k).", parent=self)
            return

        def done(res, err):
            self.class_log(f"❌ {err}" if err else "🔋 Capacidade diária estimada guardada.")
            if not err:
                self._cap_loaded = False
                self.apply_settings(res)

        self.bg(lambda: self.api.call("POST", "/api/settings", {"provider_capacity": caps}), done)

    def suggest_lesson_limit(self):
        cap = (self.server_settings.get("capacity") or {}).get("total_capacity", 0)
        if not cap:
            messagebox.showinfo("Limite sugerido", "Define primeiro a capacidade de pelo menos um fornecedor.", parent=self)
            return
        self.limit_var.set(str(int(cap * 0.75)))

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

    # ---------- indicadores animados ----------
    def make_lamp(self, parent, bg, width, height=28):
        lp = StatusLamp(parent, bg, width, height)
        self.lamps.append(lp)
        return lp

    @staticmethod
    def short(txt, n=30):
        txt = str(txt or "")
        return txt if len(txt) <= n else txt[: n - 1] + "…"

    def refresh_lamps(self):
        try:
            job = self.server_job or {}
            kind = self.class_kind or job.get("kind")
            cat = self.class_cat if self.class_kind else (job.get("category") or "")
            auto = bool((self.server_settings or {}).get("learning_now"))
            studying, benching = kind == "learn", kind == "benchmark"
            if studying:
                self.study_lamp.set("spin", THEME["bot"], "A estudar · " + self.short(cat, 34))
            elif auto:
                self.study_lamp.set("slow", THEME["warn"], "Autónomo ON · à espera da próxima lição")
            else:
                self.study_lamp.set("off", THEME["muted"], "Em repouso · não está a estudar")
            self.bench_lamp.set("badge", THEME["accent_blue"],
                                ("A executar benchmark · " + self.short(cat, 26)) if benching else "Nenhum benchmark a correr",
                                1 if benching else 0)
            m = self.model_state
            if m == "online":
                self.model_lamp.set("pulse", THEME["bot"], "Modelo online e funcional")
            elif m == "checking":
                self.model_lamp.set("pulse", THEME["warn"], "A verificar o modelo…")
            elif m == "offline":
                self.model_lamp.set("off", THEME["danger"], "Modelo offline")
            elif m == "nolink":
                self.model_lamp.set("off", THEME["warn"], "Modelo não ligado ao servidor")
            elif m == "noserver":
                self.model_lamp.set("off", THEME["danger"], "Servidor sem resposta")
            else:
                self.model_lamp.set("off", THEME["muted"], "Modelo: sem sessão admin")
        except (AttributeError, tk.TclError):
            pass

    def anim_tick(self):
        self._lamp_t += 0.07
        for lp in list(self.lamps):
            try:
                if lp.winfo_ismapped():
                    lp.draw(self._lamp_t)
            except tk.TclError:
                if lp in self.lamps:
                    self.lamps.remove(lp)
        if int(self._lamp_t / 0.07) % 14 == 0:
            self.refresh_lamps()
        self.after(70, self.anim_tick)

    def model_health_now(self):
        self._health_defer = None
        if not (self.connected and self.is_admin):
            self.model_state = "nosession"
            self.refresh_lamps()
            return
        if self._health_busy:
            return
        self._health_busy = True

        def done(res, err):
            self._health_busy = False
            if err:
                self.model_state = "noserver"
            else:
                r = res or {}
                self.model_state = "online" if r.get("online") else ("offline" if r.get("url_set") else "nolink")
            self.refresh_lamps()

        self.bg(lambda: self.api.call("GET", "/api/local/status", timeout=25), done)

    # ---------- estado de chaves e ligações dos 3 modelos ----------
    def local_lm_ok(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{LM_PORT}/v1/models", timeout=4) as r:
                return r.status == 200
        except Exception:
            return False

    def models_health_tick(self):
        self.after(120_000, self.models_health_tick)
        self.models_health_now()

    def models_health_now(self):
        if self._mh_busy:
            return
        if not (self.connected and self.is_admin):
            self.models_health = {"nosession": True}
            self.refresh_model_status()
            return
        self._mh_busy = True

        def work():
            local = self.local_lm_ok()
            try:
                srv = self.api.call("GET", "/api/health/models", timeout=100)
            except Exception as e:
                srv = {"_error": str(e)}
            return {"local": local, "srv": srv}

        def done(res, err):
            self._mh_busy = False
            self.models_health = res if not err else {"srv": {"_error": str(err)}, "local": False}
            self.refresh_model_status()

        self.bg(work, done)

    def model_status_view(self, key):
        """(texto curto, cor, detalhe completo) para o cartão de cada modelo."""
        mh = self.models_health
        if mh is None:
            return "… a verificar chave e ligação", THEME["muted"], "Ainda não verificado."
        if mh.get("nosession"):
            return "— sem sessão de administrador", THEME["muted"], "Entra como admin para verificar as chaves."
        srv = mh.get("srv") or {}
        if key == "gois":
            local = bool(mh.get("local"))
            tun = srv.get("gois") or {}
            if not local:
                return ("✖ LM Studio local não responde", THEME["danger"],
                        f"Sem resposta em 127.0.0.1:{LM_PORT}. Abre o LM Studio, carrega o modelo e inicia o servidor local.")
            if srv.get("_error"):
                return ("⚠ LM Studio OK · servidor sem resposta", THEME["warn"],
                        "O LM Studio local responde (o benchmark funciona), mas o servidor Render não respondeu: " + srv["_error"])
            if tun.get("ok"):
                return "✔ LM Studio e túnel OK", THEME["bot"], "LM Studio local OK e o servidor alcança-o pelo túnel."
            return ("⚠ LM Studio OK · túnel em baixo", THEME["warn"],
                    "O LM Studio local responde (o benchmark funciona), mas o servidor não o alcança: "
                    + str(tun.get("detail") or "sem detalhe") + ". O chat de admin pode dar «offline»; carrega em ▶ Ligar modelo próprio.")
        if srv.get("_error"):
            return "✖ servidor sem resposta", THEME["danger"], str(srv["_error"])
        r = srv.get(key) or {}
        detail = str(r.get("detail") or "sem detalhe")
        if r.get("ok"):
            return "✔ chave e ligação OK", THEME["bot"], detail
        if r.get("state") == "nokey":
            return f"✖ sem chave ({r.get('env', '')})", THEME["danger"], detail
        if "429" in detail:
            return "⚠ limite de uso atingido (429)", THEME["warn"], detail + "\nA chave existe, mas o fornecedor está a limitar pedidos; tenta mais tarde."
        if "HTTP 401" in detail or "HTTP 403" in detail:
            return "✖ chave inválida / sem permissão", THEME["danger"], detail
        return "✖ erro: " + self.short(detail, 44), THEME["danger"], detail

    def refresh_model_status(self):
        for k, lbl in list(getattr(self, "kpi_status", {}).items()):
            try:
                txt, col, _d = self.model_status_view(k)
                lbl.config(text=txt, fg=col)
            except tk.TclError:
                pass

    def show_model_health(self, key):
        txt, _c, detail = self.model_status_view(key)
        name = {"gois": "gois.v1", "mistral": "Mistral", "groq": "Groq"}.get(key, key)
        when = ((self.models_health or {}).get("srv") or {}).get("checked") or ""
        messagebox.showinfo(f"Estado · {name}", f"{txt}\n\n{detail}\n\nVerificado: {when or '—'}\n"
                            "(clica noutra vez após corrigir a chave no Render e fazer deploy)", parent=self)
        self.models_health_now()

    def health_tick(self):
        self.after(20_000, self.health_tick)
        self.model_health_now()

    def job_tick(self):
        self.after(8_000, self.job_tick)
        if not (self.connected and self.is_admin) or self._job_busy:
            return
        self._job_busy = True

        def done(res, err):
            self._job_busy = False
            if not err and isinstance(res, dict):
                self.server_job = res.get("job") or {}
                self.refresh_lamps()

        self.bg(lambda: self.api.call("GET", "/api/job", timeout=15), done)

    # ---------- cópia de segurança do estado do servidor ----------
    @staticmethod
    def state_snapshot(s):
        cats = {}
        for c, e in ((s.get("comparison") or {}).get("categories") or {}).items():
            cats[c] = {k: e.get(k) for k in ("gois_score", "mistral_ref", "groq_ref", "gemini_ref", "deepseek_ref", "benchmarks", "last_benchmark", "models")}
        tok = s.get("tokens") or {}
        return {"saved": time.strftime("%Y-%m-%d %H:%M:%S"),
                "comparison": {"categories": cats, "history": (s.get("comparison") or {}).get("history") or []},
                "tracked_models": s.get("tracked_models") or {},
                "daily_token_limit": s.get("daily_token_limit", 0), "provider_capacity": s.get("provider_capacity") or {},
                "tokens": {k: tok.get(k) for k in ("used_total", "used_today", "date", "by_provider")}}

    @staticmethod
    def state_regressed(now, old):
        def bench(st):
            return sum(int(e.get("benchmarks") or 0) for e in ((st.get("comparison") or {}).get("categories") or {}).values())
        def used(st):
            return int((st.get("tokens") or {}).get("used_total") or 0)
        return bench(now) < bench(old) or used(now) < used(old)

    def state_guard(self, s):
        """Guarda uma cópia do estado do servidor; se o servidor tiver perdido dados, repõe-nos (sem apagar nada)."""
        if not self.is_admin or getattr(self, "_restoring", False):
            return
        snap = self.state_snapshot(s)
        old = read_json(STATE_BACKUP_FILE, None)
        if old and self.state_regressed(snap, old):
            if time.time() - getattr(self, "_restore_try", 0) < 120:
                return
            self._restore_try = time.time()
            self._restoring = True

            def done(res, err):
                self._restoring = False
                if err:
                    self.flash(f"Não consegui repor o estado do servidor: {err}", THEME["danger"], 9000)
                    return
                self.flash("♻ O servidor tinha perdido dados: notas, tokens e definições repostos a partir da cópia local.",
                           THEME["bot"], 10000)
                self.apply_settings(res)

            self.bg(lambda: self.api.call("POST", "/api/state/restore", old, timeout=60), done)
            return                       # não substitui a cópia boa por um estado em regressão
        write_json(STATE_BACKUP_FILE, snap)
        day = time.strftime("%Y%m%d")
        daily = STATE_BACKUP_FILE.parent / "backups" / f"state-{day}.json"
        if not daily.exists():
            write_json(daily, snap)
            for old_f in sorted(daily.parent.glob("state-*.json"))[:-7]:   # guarda os últimos 7 dias
                try:
                    old_f.unlink()
                except OSError:
                    pass

    def apply_settings(self, s, cached=False):
        self.server_settings = s
        if not cached:
            self.state_guard(s)
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
                text=f"Online · gois.v1 {comp.get('overall', 0)}% (paridade vs {MODEL_NAME['mistral']} {(comp.get('parity_by_model') or {}).get(mkey('Mistral', self.sel_models.get('Mistral'))) or 0}% · {MODEL_NAME['groq']} {(comp.get('parity_by_model') or {}).get(mkey('Groq', self.sel_models.get('Groq'))) or 0}%) · "
                     f"{comp.get('lessons_total', 0)} lições · Professor atual: {s.get('current_teacher') or '—'}\n"
                     f"Fornecedores com chave: {provs}" + (f"\n⚠ Último erro: {s['last_error']}" if s.get("last_error") else ""),
                fg=THEME["bot"])
        used, lim = tok.get("used_today", 0), tok.get("daily_limit", 0)
        extra = "  ⛔ limite atingido — modo autónomo pausado" if tok.get("auto_paused_by_limit") or tok.get("limit_reached") else ""
        self.tokens_lbl.config(text=f"🔋 Tokens hoje: {used:,} / {lim:,}".replace(",", ".") + f"   ·   Total acumulado: {tok.get('used_total', 0):,}".replace(",", ".") + extra,
                               fg=THEME["warn"] if extra else THEME["muted"])
        capd = s.get("capacity") or {}
        if capd.get("providers"):
            if not self._cap_loaded:
                for n, v in self.cap_vars.items():
                    v.set(str((capd["providers"].get(n) or {}).get("capacity", 0)))
                self._cap_loaded = True
            parts = [f"{n} {self.fmt_tok(r.get('used', 0))}/{self.fmt_tok(r['capacity'])}"
                     for n, r in capd["providers"].items() if r.get("capacity")]
            if parts:
                self.tokens_lbl.config(text=self.tokens_lbl.cget("text") + "   ·   Por fornecedor: " + "  |  ".join(parts))
        if not self.limit_var.get().strip() or self.limit_var.get() == "0":
            self.limit_var.set(str(lim))
        self.init_selection()
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
        n_new = len(self.kb) - int(self.cfg.get("last_train_lessons", 0) or 0)
        if n_new >= 50 and not getattr(self, "_reminded", False):
            self._reminded = True
            self.flash(f"📚 {n_new} lições novas desde a última afinação — carrega em 🚀 Atualizar modelo (Colab).",
                       THEME["warn"], ms=15000)
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


    # ----------------------------------------------------------------------- #
    # Google Colab (afinação do modelo)
    # ----------------------------------------------------------------------- #
    def colab_url(self):
        return str(self.cfg.get("colab_url") or "").strip()

    def colab_url_problem(self):
        url = self.colab_url()
        if not url:
            return "Falta o URL do notebook no Colab (⚙ Definições)."
        if not url.startswith(COLAB_HOST):
            return f"O URL tem de começar por {COLAB_HOST}"
        return ""

    def test_colab(self):
        if not self.require_admin_ui():
            return
        problem = self.colab_url_problem()
        self.flash("⏳ A testar o Colab e o dataset…", THEME["muted"])

        def work():
            return self.api.call("GET", "/api/dataset?count=1", timeout=80)

        def done(res, err):
            lines = ["✅ Link do Colab com formato válido" if not problem else f"❌ {problem}"]
            if err:
                lines.append(f"❌ Dataset no servidor: {err}")
            else:
                n = res.get("count", 0)
                lines.append(f"✅ Dataset no servidor: {n} exemplos prontos ({res.get('skipped', 0)} lições ignoradas)")
                if n < 50:
                    lines.append("⚠ Poucos exemplos: com menos de ~50 o afinamento quase não muda o modelo.")
            lines.append("\nNota: daqui não consigo ver a tua sessão do Colab. Confirma lá que o segredo "
                         "GOIS_ADMIN_TOKEN existe (ícone da chave, na barra lateral).")
            self.flash("", THEME["muted"])
            messagebox.showinfo("Testar Colab", "\n".join(lines), parent=self)

        self.bg(work, done)

    def update_model_colab(self):
        """Sincroniza as lições com o servidor e abre o notebook no Colab; só falta 'Executar tudo'."""
        if not self.require_admin_ui():
            return
        problem = self.colab_url_problem()
        if problem:
            messagebox.showwarning("Colab", problem, parent=self)
            self.open_settings()
            return
        self.flash("⏳ A sincronizar as lições com o servidor…", THEME["muted"])

        def work():
            server_kb = self.api.call("GET", "/api/knowledge")
            ids = {k.get("id") for k in server_kb}
            missing = [k for k in self.kb_cache if k.get("id") not in ids]
            if missing:  # o Render perdeu lições (restart): repõe a partir da cópia local
                self.api.call("POST", "/api/sync/import", {"knowledge": missing}, timeout=60)
            return self.api.call("GET", "/api/dataset?count=1", timeout=80)

        def done(res, err):
            n = None
            if err:
                if not messagebox.askyesno("Colab", f"Não consegui preparar o dataset no servidor:\n{err}\n\nAbrir o Colab mesmo assim?", parent=self):
                    self.flash("", THEME["muted"])
                    return
            else:
                n = res.get("count", 0)
            webbrowser.open(self.colab_url())
            self.cfg["last_train_lessons"] = len(self.kb)
            self.save_config()
            msg = "🚀 Colab aberto — carrega em Ambiente de execução → Executar tudo (Ctrl+F9)."
            self.flash((f"{n} exemplos prontos. " if n is not None else "") + msg, THEME["bot"], ms=12000)
            if not self.cfg.get("colab_hint_seen"):
                messagebox.showinfo(
                    "Colab",
                    "Primeira vez? No Colab:\n"
                    "1. Barra lateral → 🔑 Segredos → cria GOIS_ADMIN_TOKEN com o teu ADMIN_TOKEN e liga o acesso ao notebook.\n"
                    "2. Ambiente de execução → Executar tudo (Ctrl+F9).\n"
                    "3. Aceita a ligação ao Google Drive quando pedir.\n\n"
                    "No fim, o modelo (.gguf) fica na pasta gois_v1 do teu Google Drive.", parent=self)
                self.cfg["colab_hint_seen"] = True
                self.save_config()

        self.bg(work, done)


    # ======================================================================= #
    # DIRETRIZES DO MODELO (admin / público)
    # ======================================================================= #
    PROMPT_ADMIN_SOFT = 1200   # acima disto o prompt do admin come espaço à conversa (contexto ~2048 tokens)

    DIRECTIVE_HELP = (
        "Todas as diretrizes valem já: o servidor injeta-as em CADA pedido, sem fine-tune. O fine-tune só serve para as tornar mais fiáveis "
        "no teu 3B ou para as tirar do prompt (poupa contexto).\n"
        "Regra prática: afina quando (1) o teste da diretriz falha em mais de 1 de cada 5 pedidos mesmo com ela no prompt; "
        "(2) tens umas centenas de exemplos que mostram exatamente esse comportamento; (3) o problema é de formato, estilo ou identidade. "
        "Factos que mudam vão para as lições (já são injetadas), não para o fine-tune.\n"
        "Mede antes e depois com ⚔ Benchmark: o gois.v1 responde com a diretriz de admin, por isso mexer nela já mexe na nota.")

    def build_prompts_tab(self):
        t = self.tab_prompts
        tk.Label(t, text="📜 Diretrizes do gois.v1", bg=THEME["bg"], fg=THEME["fg"],
                 font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, padx=20, pady=(14, 2))
        tk.Label(t, bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9), justify=tk.LEFT, anchor=tk.W, wraplength=1150,
                 text="O servidor injeta a diretriz certa em CADA pedido de chat, por isso vale em qualquer chat novo. "
                      "As mensagens «system» enviadas por clientes são ignoradas, logo ninguém as consegue sobrepor. "
                      "Guardar aqui grava no servidor e vale logo no pedido seguinte; «Repor padrão» volta ao texto que está no código do app.py."
                 ).pack(fill=tk.X, padx=20)
        bar = tk.Frame(t, bg=THEME["bg"])
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 8))
        self.btn(bar, "⟳ Recarregar do servidor", self.load_prompts).pack(side=tk.LEFT)
        body = tk.Frame(t, bg=THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(body, bg=THEME["bg"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(body, bg=THEME["bg"])
        right.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 20))
        self.prompt_widgets = {}
        specs = (("admin", "🔐 Admin (privado)", "Aplicada quando o pedido traz o teu X-Admin-Token válido. O chat de admin usa só o teu modelo (LM Studio)."),
                 ("public", "🌐 Público (site)", "Aplicada a visitantes sem token. O chat público usa só os fornecedores (Groq, Gemini, OpenRouter, Mistral)."))
        for key, title, hint in specs:
            box = self.card(left)
            box.pack(fill=tk.X, padx=(20, 10), pady=8)
            bg = box.cget("bg")
            head = tk.Frame(box, bg=bg)
            head.pack(fill=tk.X, padx=12, pady=(10, 2))
            tk.Label(head, text=title, bg=bg, fg=THEME["fg"], font=("Segoe UI Semibold", 10)).pack(side=tk.LEFT)
            counter = tk.Label(head, text="", bg=bg, fg=THEME["muted"], font=("Segoe UI", 9))
            counter.pack(side=tk.RIGHT)
            tk.Label(box, text=hint, bg=bg, fg=THEME["muted"], font=("Segoe UI", 9), anchor=tk.W, justify=tk.LEFT,
                     wraplength=640).pack(fill=tk.X, padx=12)
            txt = tk.Text(box, height=8, wrap=tk.WORD, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground=THEME["fg"],
                          relief=tk.FLAT, padx=8, pady=6, font=("Segoe UI", 10), undo=True, width=60)
            txt.pack(fill=tk.X, padx=12, pady=6)
            txt.bind("<KeyRelease>", lambda _e, k=key: self.prompt_counter(k))
            row = tk.Frame(box, bg=bg)
            row.pack(fill=tk.X, padx=12, pady=(0, 10))
            self.btn(row, "💾 Guardar no servidor", lambda k=key: self.save_prompt(k), THEME["accent"]).pack(side=tk.LEFT)
            self.btn(row, "↺ Repor padrão", lambda k=key: self.reset_prompt(k)).pack(side=tk.LEFT, padx=8)
            self.prompt_widgets[key] = (txt, counter)
        self.build_directive_status(right)
        self.prompts_ui_fill()

    def build_directive_status(self, parent):
        """Quadro «Estado de cada diretriz»: em vigor, fiabilidade no teu modelo e se o fine-tune ajuda. Só informação:
        nada disto é enviado ao modelo."""
        self._dir_rows = {}
        card = self.card(parent)
        card.pack(fill=tk.BOTH, expand=True, pady=8)
        bg = card.cget("bg")
        tk.Label(card, text="📊 Estado de cada diretriz (padrão do app.py)", bg=bg, fg=THEME["fg"],
                 font=("Segoe UI Semibold", 10)).pack(anchor=tk.W, padx=12, pady=(10, 2))
        self.dir_cost = tk.Label(card, text="", bg=bg, fg=THEME["muted"], font=("Segoe UI", 9), justify=tk.LEFT, anchor=tk.W, wraplength=560)
        self.dir_cost.pack(fill=tk.X, padx=12)
        cols = ("id", "text", "force", "rel", "ft")
        tv = ttk.Treeview(card, columns=cols, show="headings", height=13, selectmode="browse")
        for c, w, h in (("id", 44, "#"), ("text", 230, "Diretriz"), ("force", 84, "Em vigor"), ("rel", 80, "Fiabilidade"), ("ft", 110, "Fine-tune")):
            tv.heading(c, text=h)
            tv.column(c, width=w, anchor=tk.W, stretch=(c == "text"))
        tv.pack(fill=tk.X, padx=12, pady=6)
        tv.bind("<<TreeviewSelect>>", lambda _e: self.directive_detail())
        self.dir_tree = tv
        self.dir_detail = tk.Label(card, text="", bg=bg, fg=THEME["fg"], font=("Segoe UI", 9), justify=tk.LEFT, anchor="nw",
                                   wraplength=560, height=7)
        self.dir_detail.pack(fill=tk.X, padx=12)
        tk.Label(card, text=self.DIRECTIVE_HELP, bg=bg, fg=THEME["muted"], font=("Segoe UI", 8), justify=tk.LEFT, anchor="nw",
                 wraplength=560).pack(fill=tk.X, padx=12, pady=(6, 10))

    def directives_ui_fill(self):
        tv = getattr(self, "dir_tree", None)
        if tv is None:
            return
        data = self.prompts_data or {}
        try:
            tv.delete(*tv.get_children())
            self._dir_rows = {}
            for key, icon in (("admin", "🔐"), ("public", "🌐")):
                for d in data.get(key + "_directives") or []:
                    iid = f"{key}{d.get('n')}"
                    self._dir_rows[iid] = d
                    text = str(d.get("text") or "")
                    tv.insert("", tk.END, iid=iid, values=(f"{icon}{d.get('n')}", text if len(text) <= 44 else text[:43] + "…",
                                                           d.get("in_force", ""), d.get("reliability", ""), d.get("finetune", "")))
            if not self._dir_rows:
                self.dir_cost.config(text="Liga-te ao servidor como admin e carrega em ⟳ Recarregar. Se continuar vazio, "
                                          "atualiza o servidor para a versão 6.3.1.")
            else:
                cost = data.get("cost") or {}
                msg = (f"🔐 = Admin (o teu modelo) · 🌐 = Público (fornecedores). O prompt de admin em vigor ocupa "
                       f"≈ {cost.get('admin_tokens_est', '?')} tokens ({cost.get('admin_pct_ctx', '?')} % do contexto de {cost.get('ctx', '?')}).")
                if data.get("admin_custom") or data.get("public_custom"):
                    msg += " ⚠ Tens texto editado: este quadro descreve as diretrizes PADRÃO, não o que escreveste."
                self.dir_cost.config(text=msg)
            self.directive_detail()
        except tk.TclError:
            pass

    def directive_detail(self):
        try:
            sel = self.dir_tree.selection()
            d = self._dir_rows.get(sel[0]) if sel else None
            self.dir_detail.config(text=(f"«{d['text']}»\n\nPorquê: {d['note']}\n\nComo testar: {d['test']}" if d
                                         else "Seleciona uma diretriz para ver o porquê e como testá-la."))
        except (tk.TclError, KeyError, AttributeError):
            pass

    def prompt_text(self, key):
        txt, _ = self.prompt_widgets[key]
        return txt.get("1.0", "end-1c").strip()

    def prompt_counter(self, key):
        try:
            _, lbl = self.prompt_widgets[key]
            n = len(self.prompt_text(key))
            mx = int(self.prompts_data.get("max_chars", 3000))
            if n > mx:
                lbl.config(text=f"{n} caracteres — acima do máximo ({mx})", fg=THEME["danger"])
            elif key == "admin" and n > self.PROMPT_ADMIN_SOFT:
                lbl.config(text=f"{n} caracteres — longo: tira espaço à conversa e às lições", fg=THEME["warn"])
            else:
                lbl.config(text=f"{n} caracteres", fg=THEME["muted"])
        except (tk.TclError, KeyError):
            pass

    def prompts_ui_fill(self):
        local = self.cfg.get("prompts_local") or {}
        for key, (txt, _lbl) in self.prompt_widgets.items():
            text = self.prompts_data.get(key) or local.get(key) or ""
            try:
                txt.delete("1.0", tk.END)
                txt.insert("1.0", text if text else "(liga-te ao servidor como admin para ver a diretriz em vigor)")
            except tk.TclError:
                continue
            self.prompt_counter(key)
        self.directives_ui_fill()

    def load_prompts(self):
        if not self.is_admin:
            return

        def done(res, err):
            if err or not isinstance(res, dict):
                return
            self.prompts_data = res
            self.prompts_ui_fill()

        self.bg(lambda: self.api.call("GET", "/api/prompts", timeout=40), done)

    def sync_prompts(self):
        """O Render tem disco efémero: se perdeu as diretrizes editadas, repõe-nas a partir da cópia deste PC."""
        if not self.is_admin:
            return
        local = dict(self.cfg.get("prompts_local") or {})

        def work():
            res = self.api.call("GET", "/api/prompts", timeout=40)
            push = {k: local[k] for k in ("admin", "public") if local.get(k) and not res.get(k + "_custom")}
            if push:
                res = self.api.call("POST", "/api/prompts", push, timeout=40)
            return res, bool(push)

        def done(r, err):
            if err or not r:
                return
            res, pushed = r
            self.prompts_data = res
            loc, changed = dict(self.cfg.get("prompts_local") or {}), False
            for k in ("admin", "public"):
                want = res.get(k) if res.get(k + "_custom") else ""
                if loc.get(k, "") != want:
                    loc[k], changed = want, True
            if changed:
                self.cfg["prompts_local"] = loc
                self.save_config()
            self.prompts_ui_fill()
            if pushed:
                self.flash("📜 Diretrizes repostas no servidor (o Render tinha-as perdido).", THEME["warn"], ms=8000)

        self.bg(work, done)

    def push_prompt(self, key, send, ok_msg):
        def done(res, err):
            if err:
                messagebox.showerror("Diretrizes", f"Não consegui guardar no servidor:\n{err}", parent=self)
                return
            self.prompts_data = res
            loc = dict(self.cfg.get("prompts_local") or {})
            loc[key] = send
            self.cfg["prompts_local"] = loc
            self.save_config()
            self.prompts_ui_fill()
            self.flash(ok_msg, THEME["bot"], ms=8000)

        self.bg(lambda: self.api.call("POST", "/api/prompts", {key: send}, timeout=40), done)

    def save_prompt(self, key):
        if not self.require_admin_ui():
            return
        text = self.prompt_text(key)
        if text.startswith("(liga-te ao servidor"):
            messagebox.showinfo("Diretrizes", "Ainda não carreguei a diretriz do servidor. Carrega em ⟳ Recarregar e tenta de novo.", parent=self)
            return
        mx = int(self.prompts_data.get("max_chars", 3000))
        if len(text) > mx:
            messagebox.showwarning("Diretrizes", f"Máximo {mx} caracteres (tem {len(text)}).", parent=self)
            return
        if key == "admin" and len(text) > self.PROMPT_ADMIN_SOFT and not messagebox.askyesno(
                "Diretriz longa",
                f"Esta diretriz tem {len(text)} caracteres. O teu modelo tem um contexto pequeno (cerca de 2048 tokens): "
                "quanto maior o prompt, menos sobra para a conversa e para as lições da Sala de Aula.\n\nGuardar mesmo assim?", parent=self):
            return
        default = str(self.prompts_data.get(key + "_default") or "").strip()
        send = "" if (not text or text == default) else text      # igual ao padrão = sem personalização
        nome = "admin" if key == "admin" else "pública"
        self.push_prompt(key, send, f"💾 Diretriz {nome} guardada — vale já no próximo pedido.")

    def reset_prompt(self, key):
        if not self.require_admin_ui():
            return
        nome = "admin" if key == "admin" else "pública"
        if messagebox.askyesno("Repor padrão", f"Repor a diretriz {nome} para o texto padrão do app.py?", parent=self):
            self.push_prompt(key, "", f"↺ Diretriz {nome} reposta para o padrão.")

    # ======================================================================= #
    # MODELO PRÓPRIO (LM Studio + túnel Cloudflare + servidor)
    # ======================================================================= #
    def build_local_tab(self):
        c = self.cfg
        self.v_model = tk.StringVar(value=c.get("lm_model", ""))
        self.v_ctx = tk.StringVar(value=str(c.get("lm_ctx", 4096)))
        self.v_models_dir = tk.StringVar(value=c.get("lm_models_dir") or default_models_dir())
        self.v_watch_dir = tk.StringVar(value=c.get("watch_dir") or str(Path.home() / "Downloads"))
        self.v_auto_start = tk.BooleanVar(value=bool(c.get("auto_start_local", False)))
        self.v_auto_install = tk.BooleanVar(value=bool(c.get("auto_install_gguf", True)))

        tk.Label(self.tab_local, text="🤖 Modelo próprio (LM Studio neste PC)", bg=THEME["bg"], fg=THEME["fg"],
                 font=("Segoe UI", 12, "bold")).pack(anchor=tk.W, padx=20, pady=(14, 2))
        tk.Label(self.tab_local, bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI", 9), justify=tk.LEFT, anchor=tk.W,
                 text="▶ Ligar faz tudo: arranca o servidor do LM Studio, carrega o modelo, abre o túnel e diz ao servidor do chat onde está.\n"
                      "Roteamento: quando entras como ADMIN, o chat usa SÓ o teu modelo (se este PC estiver desligado mostra «offline»).\n"
                      "Quem usa o chat sem token (público) continua nos fornecedores e nunca chega ao teu PC.").pack(fill=tk.X, padx=20)

        card = self.card(self.tab_local)
        card.pack(fill=tk.X, padx=20, pady=10)
        card.columnconfigure(1, weight=1)

        def entry_row(r, label, var, browse=None, detect=None):
            tk.Label(card, text=label, bg=card.cget("bg"), fg=THEME["muted"], font=("Segoe UI", 9)).grid(row=r, column=0, sticky="w", padx=12, pady=6)
            e = tk.Entry(card, textvariable=var, bg=THEME["code_bg"], fg=THEME["fg"], insertbackground="white", relief=tk.FLAT)
            e.grid(row=r, column=1, sticky="ew", padx=6, ipady=4)
            e.bind("<FocusOut>", self.save_local_cfg)
            if browse:
                self.btn(card, "…", browse, padx=8, pady=2).grid(row=r, column=2, padx=(0, 4))
            if detect:
                self.btn(card, "🔎 Detetar", detect, padx=8, pady=2).grid(row=r, column=3, padx=(0, 12))

        entry_row(0, "Modelo no LM Studio (chave)", self.v_model, detect=self.detect_model_key)
        entry_row(1, "Contexto (tokens)", self.v_ctx)
        entry_row(2, "Pasta de modelos do LM Studio", self.v_models_dir, browse=lambda: self.pick_dir(self.v_models_dir))
        entry_row(3, "Pasta onde aparece o .gguf novo", self.v_watch_dir, browse=lambda: self.pick_dir(self.v_watch_dir))

        def check(text, var):
            return tk.Checkbutton(card, text=text, variable=var, command=self.save_local_cfg, bg=card.cget("bg"), fg=THEME["fg"],
                                  selectcolor=THEME["code_bg"], activebackground=card.cget("bg"), activeforeground=THEME["fg"],
                                  font=("Segoe UI", 9), anchor="w")
        tk.Label(card, text="🔒 Admin → só o teu modelo   ·   🌐 Público → só fornecedores (Groq, Gemini, OpenRouter, Mistral)",
                 bg=card.cget("bg"), fg=THEME["muted"], font=("Segoe UI", 9), anchor="w").grid(row=4, column=0, columnspan=4, sticky="w", padx=12, pady=(2, 4))
        check("Ligar automaticamente ao abrir o painel", self.v_auto_start).grid(row=5, column=0, columnspan=4, sticky="w", padx=8)
        check("Instalar automaticamente o .gguf novo (e recarregar o modelo)", self.v_auto_install).grid(row=6, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 8))

        bar = tk.Frame(self.tab_local, bg=THEME["bg"])
        bar.pack(fill=tk.X, padx=20)
        self.btn(bar, "▶ Ligar modelo próprio", self.start_local, THEME["accent"]).pack(side=tk.LEFT, padx=(0, 6))
        self.btn(bar, "⏹ Parar", self.stop_local).pack(side=tk.LEFT, padx=6)
        self.btn(bar, "📥 Instalar .gguf agora", lambda: self.install_gguf_now(False)).pack(side=tk.LEFT, padx=6)
        self.btn(bar, "🧪 Testar", self.test_local, THEME["accent_blue"]).pack(side=tk.LEFT, padx=6)

        self.local_status_lbl = tk.Label(self.tab_local, text="", bg=THEME["bg"], fg=THEME["muted"], font=("Segoe UI Semibold", 10),
                                         anchor=tk.W, justify=tk.LEFT, wraplength=1100)
        self.local_status_lbl.pack(fill=tk.X, padx=20, pady=(10, 4))
        self.local_log_text = tk.Text(self.tab_local, height=14, bg=THEME["panel"], fg=THEME["muted"], font=("Consolas", 9), relief=tk.FLAT,
                                      padx=8, pady=6, state=tk.DISABLED, wrap=tk.WORD)
        self.local_log_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 14))
        self.local_ui_restore()

    def local_ui_restore(self):
        try:
            text, color = self.local_status_state
            self.local_status_lbl.config(text=text, fg=THEME.get(color, THEME["muted"]))
            self.local_log_text.config(state=tk.NORMAL)
            self.local_log_text.delete("1.0", tk.END)
            self.local_log_text.insert(tk.END, "\n".join(self.local_log_lines[-200:]))
            self.local_log_text.see(tk.END)
            self.local_log_text.config(state=tk.DISABLED)
        except Exception:
            pass

    def set_local_status(self, text, color="muted"):
        self.local_status_state = (text, color)
        try:
            if self._health_defer:
                self.after_cancel(self._health_defer)
            self._health_defer = self.after(2500, self.model_health_now)
        except Exception:
            pass
        try:
            self.local_status_lbl.config(text=text, fg=THEME.get(color, THEME["muted"]))
        except Exception:
            pass

    def local_log(self, line):
        line = str(line).strip()
        if not line:
            return
        self.local_log_lines.append(time.strftime("%H:%M:%S  ") + line)
        self.local_log_lines = self.local_log_lines[-300:]
        try:
            self.local_log_text.config(state=tk.NORMAL)
            self.local_log_text.insert(tk.END, self.local_log_lines[-1] + "\n")
            self.local_log_text.see(tk.END)
            self.local_log_text.config(state=tk.DISABLED)
        except Exception:
            pass

    def llog(self, line):
        """Registo seguro a partir de threads."""
        self.ui_q.put(lambda: self.local_log(line))

    def save_local_cfg(self, *_):
        try:
            ctx = int(self.v_ctx.get().strip() or 4096)
        except ValueError:
            ctx = 4096
        self.cfg.update(lm_model=self.v_model.get().strip(), lm_ctx=max(256, ctx), lm_models_dir=self.v_models_dir.get().strip(),
                        watch_dir=self.v_watch_dir.get().strip(),
                        auto_start_local=bool(self.v_auto_start.get()), auto_install_gguf=bool(self.v_auto_install.get()))
        self.save_config()

    def pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()), parent=self)
        if d:
            var.set(d)
            self.save_local_cfg()

    def detect_model_key(self):
        """Procura em `lms ls --json` o modelo 'gois' mais recente e preenche a chave."""
        def work():
            lms = find_lms()
            if not lms:
                raise RuntimeError("Não encontrei o 'lms'. Abre o LM Studio pelo menos uma vez e tenta de novo.")
            code, out = run_cmd([lms, "ls", "--json"], timeout=90)
            if code != 0:
                raise RuntimeError(out[-300:] or "O 'lms ls' falhou.")
            return pick_model_key(out, "gois")

        def done(key, err):
            if err:
                messagebox.showwarning("Modelo próprio", str(err), parent=self)
            elif not key:
                messagebox.showinfo("Modelo próprio", "Não encontrei nenhum modelo com «gois» no nome.\nConfirma a pasta de modelos do LM Studio.", parent=self)
            else:
                self.v_model.set(key)
                self.save_local_cfg()
                self.local_log(f"🔎 Modelo detetado: {key}")

        self.bg(work, done)

    # ---------------- túnel ----------------
    def open_tunnel(self, cf):
        if self.local_proc and self.local_proc.poll() is None and self.local_url:
            return self.local_url
        self.llog("▶ A abrir o túnel Cloudflare…")
        proc = subprocess.Popen([cf, "tunnel", "--url", f"http://127.0.0.1:{LM_PORT}"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        found = queue.Queue()

        def reader():
            try:
                for line in proc.stdout:  # continua a ler até ao fim para o pipe nunca encher
                    u = extract_tunnel_url(line)
                    if u and found.empty():
                        found.put(u)
            except Exception:
                pass

        threading.Thread(target=reader, daemon=True).start()
        try:
            url = found.get(timeout=60)
        except queue.Empty:
            proc.terminate()
            raise RuntimeError("O túnel não devolveu um endereço em 60 s. Verifica a ligação à internet.")
        self.local_proc, self.local_url = proc, url
        return url

    def stop_tunnel(self):
        try:
            if self.local_proc and self.local_proc.poll() is None:
                self.local_proc.terminate()
        except Exception:
            pass

    # ---------------- ligar / parar ----------------
    def start_local(self, auto=False, only_tunnel=False):
        if self.local_busy:
            return
        if not auto and not self.require_admin_ui():
            return
        self.save_local_cfg()
        model_key, ctx = self.cfg.get("lm_model", ""), self.cfg.get("lm_ctx", 4096)
        self.local_busy = True
        self.set_local_status("⏳ A ligar o modelo próprio…", "warn")

        def work():
            lms = find_lms()
            if not lms:
                raise RuntimeError("Não encontrei o 'lms' (CLI do LM Studio). Abre o LM Studio pelo menos uma vez e tenta de novo.")
            cf = find_cloudflared()
            if not cf:
                raise RuntimeError("Não encontrei o 'cloudflared'. Instala-o numa janela de comandos com:\n"
                                   "winget install --id Cloudflare.cloudflared\n(ou descarrega-o em developers.cloudflare.com) e tenta de novo.")
            key = model_key
            if not only_tunnel:
                self.llog("▶ A iniciar o servidor do LM Studio…")
                code, out = run_cmd([lms, "server", "start"], timeout=120)
                self.llog(out[-300:] or f"(código {code})")
                if not key:
                    code, out = run_cmd([lms, "ls", "--json"], timeout=90)
                    key = pick_model_key(out, "gois")
                    if key:
                        self.ui_q.put(lambda k=key: (self.v_model.set(k), self.save_local_cfg()))
                if not key:
                    raise RuntimeError("Falta a chave do modelo. Carrega em 🔎 Detetar (ou escreve a chave que aparece em 'lms ls').")
                self.llog(f"▶ A carregar «{key}» (contexto {ctx}, sem GPU). Pode demorar alguns minutos…")
                run_cmd([lms, "unload", "--all"], timeout=60)
                code, out = run_cmd([lms, "load", key, "--identifier", LOCAL_IDENTIFIER, "--context-length", str(ctx), "--gpu", "off"], timeout=900)
                self.llog(out[-300:] or f"(código {code})")
                if code != 0:
                    raise RuntimeError("O LM Studio não conseguiu carregar o modelo:\n" + out[-300:])
            url = self.open_tunnel(cf)
            self.llog(f"🌐 Túnel: {url}")
            self.api.call("POST", "/api/local/config", {"url": url + "/v1", "model": LOCAL_IDENTIFIER, "ctx": int(self.cfg.get("lm_ctx", 4096) or 4096)}, timeout=60)
            return url

        def done(url, err):
            self.local_busy = False
            if err:
                self.set_local_status(f"🔴 {err}", "danger")
                self.local_log(f"❌ {err}")
                if not auto:
                    messagebox.showerror("Modelo próprio", str(err), parent=self)
                return
            self.set_local_status(f"🟢 Online — o servidor do chat já está ligado ao teu modelo\n{url}", "bot")
            self.schedule_local_keepalive()

        self.bg(work, done)

    def stop_local(self):
        self.stop_tunnel()
        self.local_url = ""
        if self._local_after:
            try:
                self.after_cancel(self._local_after)
            except Exception:
                pass
            self._local_after = None

        def work():
            lms = find_lms()
            if lms:
                run_cmd([lms, "server", "stop"], timeout=60)
            try:
                self.api.call("POST", "/api/local/config", {"url": ""}, timeout=30)
            except Exception:
                pass

        self.set_local_status("⚪ Parado — o chat de admin mostra «offline»; o chat público continua nos fornecedores", "muted")
        self.local_log("⏹ Parado.")
        self.bg(work)

    def schedule_local_keepalive(self):
        if self._local_after:
            try:
                self.after_cancel(self._local_after)
            except Exception:
                pass
        self._local_after = self.after(60_000, self.local_keepalive)

    def local_keepalive(self):
        """De 60 em 60 s: reenvia o URL ao servidor (o Render reinicia e esquece) e religa o túnel se tiver caído."""
        self._local_after = None
        if not self.local_url:
            return
        if not (self.local_proc and self.local_proc.poll() is None):
            self.local_url = ""
            self.set_local_status("🟠 O túnel caiu — a religar…", "warn")
            self.local_log("🟠 O túnel caiu; a abrir outro.")
            self.start_local(auto=True, only_tunnel=True)
            return
        url = self.local_url
        self.bg(lambda: self.api.call("POST", "/api/local/config", {"url": url + "/v1", "model": LOCAL_IDENTIFIER, "ctx": int(self.cfg.get("lm_ctx", 4096) or 4096)}, timeout=30))
        self.schedule_local_keepalive()

    def auto_start_tick(self, tries):
        if self.connected and self.is_admin:
            self.start_local(auto=True)
        elif tries < 12:
            self.after(5000, lambda: self.auto_start_tick(tries + 1))

    def test_local(self):
        if not self.require_admin_ui():
            return

        def done(res, err):
            if err:
                messagebox.showerror("Testar modelo próprio", str(err), parent=self)
                return
            lines = [
                ("✅" if res.get("url_set") else "❌") + " O servidor conhece o endereço do teu modelo",
                ("✅" if res.get("online") else "❌") + " O servidor consegue falar com o LM Studio" + (f" ({res.get('error')})" if res.get("error") else ""),
                f"Modelo: {res.get('model')}",
                "Roteamento: admin → só o teu modelo · público → só fornecedores",
            ]
            if not res.get("url_set"):
                lines.append("\nCarrega em ▶ Ligar modelo próprio.")
            messagebox.showinfo("Testar modelo próprio", "\n".join(lines), parent=self)

        self.bg(lambda: self.api.call("GET", "/api/local/status", timeout=40), done)

    # ---------------- instalar .gguf novo ----------------
    def local_watch_tick(self):
        try:
            if getattr(self, "v_auto_install", None) is not None and self.v_auto_install.get() and not self.local_busy:
                self.install_gguf_now(True)
        finally:
            self.after(30_000, self.local_watch_tick)

    def install_gguf_now(self, auto=False):
        if self._installing:
            return
        self.save_local_cfg()
        watch, mdir = self.v_watch_dir.get().strip(), self.v_models_dir.get().strip()
        last_sig = self.cfg.get("last_gguf_sig", "")
        self._installing = True

        def work():
            src = newest_gguf(watch)
            if not src:
                return None
            sig = gguf_signature(src)
            if auto and sig == last_sig:
                return None
            self.llog(f"📥 .gguf novo encontrado: {src.name} — a copiar para o LM Studio…")
            dest = install_gguf(src, mdir)
            key = ""
            lms = find_lms()
            if lms:
                code, out = run_cmd([lms, "ls", "--json"], timeout=90)
                key = pick_model_key(out, src.stem)
            return sig, str(dest), key

        def done(res, err):
            self._installing = False
            if err:
                self.local_log(f"❌ Instalação do .gguf: {err}")
                if not auto:
                    messagebox.showerror("Instalar .gguf", str(err), parent=self)
                return
            if res is None:
                if not auto:
                    messagebox.showinfo("Instalar .gguf", f"Não encontrei nenhum ficheiro gois-v1-*.gguf novo em:\n{watch}\n\n"
                                        "(Ficheiros com menos de 20 s são ignorados porque ainda podem estar a descarregar.)", parent=self)
                return
            sig, dest, key = res
            self.cfg["last_gguf_sig"] = sig
            self.local_log(f"✅ Instalado: {dest}")
            if key:
                self.cfg["lm_model"] = key
                self.v_model.set(key)
                self.local_log(f"🔎 Chave do modelo novo: {key}")
            else:
                self.local_log("⚠ O LM Studio ainda não indexou o ficheiro. Espera uns segundos e carrega em 🔎 Detetar.")
            self.save_config()
            self.flash("📥 Modelo novo instalado no LM Studio.", THEME["bot"], ms=10000)
            if key and self.local_url and not self.local_busy:
                self.local_log("🔄 A recarregar o modelo novo…")
                self.start_local(auto=True)

        self.bg(work, done)

    def export_dataset(self):
        """Exporta as lições (atualizadas do servidor + cópia local) para um dataset JSONL de afinação."""
        self.flash("⏳ A juntar as lições mais recentes…", THEME["muted"])

        def work():
            merged = {k["id"]: k for k in self.kb_cache if k.get("id")}
            if self.is_admin:
                try:  # lições mais recentes do servidor; se estiver offline, usa a cópia local
                    for k in self.api.call("GET", "/api/knowledge"):
                        if k.get("id"):
                            merged[k["id"]] = k
                except Exception:
                    pass
            return sorted(merged.values(), key=lambda k: k.get("created", ""))

        def done(res, err):
            rows, skipped = build_dataset_rows(res or [])
            if err or not rows:
                messagebox.showinfo("Exportar dataset", "Ainda não há lições suficientes para exportar.\n"
                                    "(Cada lição precisa de título e de pelo menos 200 caracteres.)")
                return
            path = filedialog.asksaveasfilename(initialfile="gois_dataset.jsonl", defaultextension=".jsonl",
                                                filetypes=[("JSON Lines", "*.jsonl")])
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            except Exception as e:
                messagebox.showerror("Exportar dataset", f"Não foi possível gravar o ficheiro:\n{e}")
                return
            self.flash(f"📦 {len(rows)} exemplos exportados → {path}", THEME["bot"], ms=9000)
            messagebox.showinfo("Exportar dataset", f"{len(rows)} exemplos exportados.\n"
                                f"{skipped} lição(ões) ignorada(s) (curtas ou repetidas).\n\n{path}")

        self.bg(work, done)


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
