"""gois.v1 Command Center - Aplicação Desktop Completa (Tkinter + stdlib).

Secções incluídas:
  1. 💬 Chat: Streaming SSE em tempo real, Markdown com cópia de código, Parar, Regenerar, Exportar.
  2. 🏫 Sala de Aula (Modelos Reais): Liga-se a OpenRouter (DeepSeek), Groq (Llama 3.3 70B) e Mistral
     para aprender tópicos técnicos autonomamente ou a pedido ("Aprender Agora"), mostrando em tempo
     real o que está a estudar, com que modelo real está a aprender e tudo o que já aprendeu.
  3. 🧠 Skills & Chaves API: Gestão de Skills/Diretrizes do núcleo e configuração de chaves API.

Guarda automaticamente histórico, lições aprendidas e definições em %APPDATA%\\gois_v1\\ e re-sincroniza
com o servidor se a instância gratuita do Render reiniciar.
"""
import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_NAME = "gois.v1 | Office & Command Center"
DEFAULT_SERVER = "https://gois-v1.onrender.com"

APP_DIR = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "gois_v1"
CONFIG_FILE = DATA_DIR / "config.json"
LEGACY_CONFIG_FILE = APP_DIR / "gois_window_config.json"
SESSION_FILE = DATA_DIR / "session.json"
LOCAL_KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
LOCAL_SKILLS_FILE = DATA_DIR / "skills.json"

DEFAULT_CATEGORIES = [
    "Tecnologia & Software",
    "Game Design & Unity",
    "Engenharia & AutoCAD",
    "Inteligência Artificial",
    "Ciências & Exatas",
    "Humanidades & Filosofia",
    "DevOps & Ferramentas",
]

THEME = {
    "bg": "#0d1117",
    "panel": "#161b22",
    "panel_alt": "#1c2330",
    "border": "#30363d",
    "fg": "#e6edf3",
    "muted": "#8b949e",
    "user": "#58a6ff",
    "bot": "#3fb950",
    "warn": "#d29922",
    "err": "#f85149",
    "code_bg": "#1c2330",
    "btn": "#21262d",
    "btn_hover": "#30363d",
    "accent": "#238636",
    "accent_blue": "#1f6feb",
}
FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_CODE = ("Consolas", 10)


# =====================================================================
# LÓGICA PURA E REDE
# =====================================================================

def load_json_file(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def save_json_file(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def load_config() -> dict:
    cfg = {"server": DEFAULT_SERVER, "sync_token": "", "temperature": 0.5, "history": 30}
    legacy = load_json_file(LEGACY_CONFIG_FILE, {})
    if isinstance(legacy, dict):
        cfg.update({k: legacy[k] for k in cfg if k in legacy})
    saved = load_json_file(CONFIG_FILE, {})
    if isinstance(saved, dict):
        cfg.update({k: saved[k] for k in cfg if k in saved})
    cfg["server"] = str(cfg["server"]).strip().rstrip("/") or DEFAULT_SERVER
    return cfg


def parse_sse_line(raw: str) -> list:
    line = raw.strip()
    if not line.startswith("data:"):
        return []
    data = line[5:].strip()
    if not data:
        return []
    if data == "[DONE]":
        return [("done", None)]
    try:
        obj = json.loads(data)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []
    events = []
    if obj.get("error"):
        err = obj["error"]
        events.append(("error", err.get("message", str(err)) if isinstance(err, dict) else str(err)))
        return events
    if obj.get("model"):
        events.append(("model", str(obj["model"])))
    choices = obj.get("choices") or []
    if choices and isinstance(choices[0], dict):
        text = (choices[0].get("delta") or {}).get("content")
        if text:
            events.append(("delta", text))
    return events


_INLINE_RE = re.compile(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)")


def parse_inline(text: str) -> list:
    runs = []
    for part in _INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append((part[2:-2], "bold"))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            runs.append((part[1:-1], "icode"))
        else:
            runs.append((part, None))
    return runs


def parse_markdown(text: str) -> list:
    blocks = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", lang, "\n".join(code)))
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            blocks.append(("line", f"h{len(m.group(1))}", parse_inline(m.group(2))))
        else:
            b = re.match(r"^(\s*)[-*]\s+(.*)$", line)
            if b:
                blocks.append(("line", "li", parse_inline(b.group(2))))
            else:
                blocks.append(("line", "p", parse_inline(line)))
        i += 1
    return blocks


def http_json(url: str, payload=None, token: str = "", timeout: int = 120, method: str = ""):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Sync-Token"] = token
    verb = method or ("POST" if data is not None else "GET")
    req = urllib.request.Request(url, data=data, headers=headers, method=verb)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(body).get("detail", body)
        except Exception:
            detail = body
        raise RuntimeError(f"HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Sem ligação ao servidor: {e.reason}")


def stream_chat(cfg: dict, messages: list, emit, cancel: threading.Event) -> None:
    url = cfg["server"] + "/v1/chat/completions"
    base = {"messages": messages, "temperature": cfg["temperature"]}
    got_text = False
    server_error = None
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({**base, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                if cancel.is_set():
                    emit("done", None)
                    return
                for kind, value in parse_sse_line(raw.decode("utf-8", "replace")):
                    if kind == "delta":
                        got_text = True
                        emit("delta", value)
                    elif kind == "model":
                        emit("model", value)
                    elif kind == "error":
                        server_error = value
                    elif kind == "done":
                        if server_error and not got_text:
                            emit("error", server_error)
                        else:
                            emit("done", None)
                        return
        if got_text:
            emit("done", None)
            return
        if server_error:
            emit("error", server_error)
            return
    except Exception as e:
        if got_text:
            emit("error", f"Ligação interrompida: {e}")
            return
    if cancel.is_set():
        emit("done", None)
        return
    try:
        res = http_json(url, base, timeout=120)
        text = res["choices"][0]["message"]["content"] or ""
        if res.get("model"):
            emit("model", str(res["model"]))
        emit("delta", text)
        emit("done", None)
    except Exception as e:
        emit("error", str(e))


# =====================================================================
# INTERFACE GRÁFICA COMPLETA
# =====================================================================

class GoisWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.history = []
        self.knowledge_items = load_json_file(LOCAL_KNOWLEDGE_FILE, [])
        if not isinstance(self.knowledge_items, list):
            self.knowledge_items = []
        self.skills_items = load_json_file(LOCAL_SKILLS_FILE, [])
        if not isinstance(self.skills_items, list):
            self.skills_items = []

        self.server_state = {
            "classroom_mode": True,
            "learning_now": False,
            "current_topic": "A sincronizar...",
            "current_teacher": "Rotação Automática",
            "last_learned_title": "",
            "last_teacher": "",
            "active_categories": list(DEFAULT_CATEGORIES),
            "providers_with_key": [],
        }

        self.ui_q = queue.Queue()
        self.cancel = threading.Event()
        self.streaming = False
        self.learning_busy = False
        self.reply_buf = []
        self.reply_model = ""
        self.got_token = False
        self.cp_counter = 0
        self.last_user_text = ""
        self.current_tab = "chat"

        root.title(APP_NAME)
        root.geometry("1060x740")
        root.minsize(780, 520)
        root.configure(bg=THEME["bg"])

        self.build_ui()
        self.bind_keys()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        saved = load_json_file(SESSION_FILE, [])
        if isinstance(saved, list):
            self.history = [
                m for m in saved
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
            ]
        self.render_history()
        self.refresh_knowledge_list()
        self.poll()
        self.initial_connect()
        self.schedule_classroom_refresh()

    # ---------- componentes visuais ----------
    def make_button(self, parent, text, command, kind="default", padx=12, pady=5):
        T = THEME
        if kind == "primary":
            bg, hover = T["accent"], "#2ea043"
        elif kind == "blue":
            bg, hover = T["accent_blue"], "#388bfd"
        elif kind == "danger":
            bg, hover = "#da3633", "#f85149"
        else:
            bg, hover = T["btn"], T["btn_hover"]
        b = tk.Button(
            parent, text=text, command=command, bg=bg, fg=T["fg"],
            activebackground=hover, activeforeground=T["fg"],
            relief="flat", bd=0, padx=padx, pady=pady, font=FONT_UI,
            cursor="hand2", highlightthickness=0,
        )
        b._base_bg = bg
        b._hover_bg = hover
        b.bind("<Enter>", lambda e: b.configure(bg=b._hover_bg))
        b.bind("<Leave>", lambda e: b.configure(bg=b._base_bg))
        return b

    def configure_markdown_tags(self, widget: tk.Text):
        T = THEME
        widget.tag_config("who_user", foreground=T["user"], font=FONT_UI_BOLD, spacing1=10)
        widget.tag_config("who_bot", foreground=T["bot"], font=FONT_UI_BOLD, spacing1=10)
        widget.tag_config("sys", foreground=T["muted"], font=("Segoe UI", 9, "italic"))
        widget.tag_config("err", foreground=T["err"], font=FONT_UI)
        widget.tag_config("bold", font=FONT_UI_BOLD)
        widget.tag_config("icode", font=FONT_CODE, background=T["code_bg"], foreground="#ffa657")
        widget.tag_config("h1", font=("Segoe UI", 14, "bold"), foreground=T["bot"], spacing1=8, spacing3=4)
        widget.tag_config("h2", font=("Segoe UI", 12, "bold"), foreground=T["user"], spacing1=6, spacing3=2)
        widget.tag_config("h3", font=("Segoe UI", 11, "bold"), spacing1=4)
        widget.tag_config("li", lmargin1=18, lmargin2=32)
        widget.tag_config("code", font=FONT_CODE, background=T["code_bg"], lmargin1=14, lmargin2=14, rmargin=10)
        widget.tag_config("code_head", font=("Segoe UI", 8), foreground=T["muted"], background=T["code_bg"], lmargin1=14, spacing1=6)
        widget.tag_config("copy", font=("Segoe UI", 8, "underline"), foreground=T["user"], background=T["code_bg"])

    def build_ui(self):
        T = THEME
        # --- Barra de topo com Abas de Navegação ---
        top = tk.Frame(self.root, bg=T["panel"])
        top.pack(fill="x")

        brand = tk.Frame(top, bg=T["panel"])
        brand.pack(side="left", padx=(12, 10), pady=8)
        tk.Label(brand, text="●", bg=T["panel"], fg=T["bot"], font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 5))
        tk.Label(brand, text="gois.v1", bg=T["panel"], fg=T["fg"], font=("Segoe UI", 12, "bold")).pack(side="left")

        nav = tk.Frame(top, bg=T["panel"])
        nav.pack(side="left", padx=6, pady=6)
        self.tab_btns = {}
        tabs = [
            ("chat", "💬 Chat"),
            ("classroom", "🏫 Sala de Aula (Modelos Reais)"),
            ("skills", "🧠 Skills & Chaves API"),
        ]
        for key, label in tabs:
            btn = tk.Button(
                nav, text=label, command=lambda k=key: self.switch_tab(k),
                bg=T["accent_blue"] if key == "chat" else T["btn"],
                fg=T["fg"], activebackground=T["btn_hover"], activeforeground=T["fg"],
                relief="flat", bd=0, padx=12, pady=5, font=FONT_UI_BOLD if key == "chat" else FONT_UI,
                cursor="hand2", highlightthickness=0,
            )
            btn.pack(side="left", padx=3)
            self.tab_btns[key] = btn

        self.status = tk.Label(top, text="A ligar ao servidor...", bg=T["panel"], fg=T["muted"], font=FONT_SMALL, anchor="w")
        self.status.pack(side="left", fill="x", expand=True, padx=10)

        for text, cmd in (("Definições", self.open_settings), ("☁️ Sincronizar", self.sync)):
            self.make_button(top, text, cmd, padx=10, pady=4).pack(side="right", padx=4, pady=6)

        tk.Frame(self.root, bg=T["border"], height=1).pack(fill="x")

        # --- Contentor das Vistas ---
        self.views_host = tk.Frame(self.root, bg=T["bg"])
        self.views_host.pack(fill="both", expand=True)

        self.view_chat = tk.Frame(self.views_host, bg=T["bg"])
        self.view_classroom = tk.Frame(self.views_host, bg=T["bg"])
        self.view_skills = tk.Frame(self.views_host, bg=T["bg"])

        self.build_chat_view(self.view_chat)
        self.build_classroom_view(self.view_classroom)
        self.build_skills_view(self.view_skills)

        self.view_chat.pack(fill="both", expand=True)

    # ---------- VISTA 1: CHAT ----------
    def build_chat_view(self, parent: tk.Frame):
        T = THEME
        # Barra rápida do chat
        sub = tk.Frame(parent, bg=T["panel_alt"])
        sub.pack(fill="x")
        self.chat_classroom_banner = tk.Label(
            sub,
            text="🏫 Sala de Aula Autónoma: a verificar estado...",
            bg=T["panel_alt"], fg=T["muted"], font=FONT_SMALL, anchor="w", cursor="hand2"
        )
        self.chat_classroom_banner.pack(side="left", padx=14, pady=5, fill="x", expand=True)
        self.chat_classroom_banner.bind("<Button-1>", lambda e: self.switch_tab("classroom"))

        self.make_button(sub, "Nova conversa", self.new_chat, padx=10, pady=3).pack(side="right", padx=6, pady=4)
        self.make_button(sub, "Exportar Chat", self.export, padx=10, pady=3).pack(side="right", padx=2, pady=4)
        tk.Frame(parent, bg=T["border"], height=1).pack(fill="x")

        body = tk.Frame(parent, bg=T["bg"])
        body.pack(fill="both", expand=True)
        self.scroll = tk.Scrollbar(body, orient="vertical")
        self.scroll.pack(side="right", fill="y")
        self.log = tk.Text(
            body, wrap="word", state="disabled", bg=T["bg"], fg=T["fg"], bd=0, relief="flat",
            font=FONT_UI, padx=18, pady=12, insertbackground=T["fg"], spacing1=2, spacing3=2,
            selectbackground="#264f78", highlightthickness=0, yscrollcommand=self.scroll.set,
        )
        self.log.pack(side="left", fill="both", expand=True)
        self.scroll.config(command=self.log.yview)
        self.configure_markdown_tags(self.log)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Copiar seleção", command=self.copy_selection)
        self.menu.add_command(label="Copiar última resposta", command=self.copy_last_reply)
        self.log.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

        tk.Frame(parent, bg=T["border"], height=1).pack(fill="x")
        bottom = tk.Frame(parent, bg=T["panel"])
        bottom.pack(fill="x")
        inner = tk.Frame(bottom, bg=T["panel"])
        inner.pack(fill="x", padx=12, pady=10)
        self.entry = tk.Text(
            inner, height=3, wrap="word", bg=T["bg"], fg=T["fg"], insertbackground=T["fg"],
            relief="flat", bd=0, font=FONT_UI, padx=10, pady=8, highlightthickness=1,
            highlightbackground=T["border"], highlightcolor=T["user"],
        )
        self.entry.pack(side="left", fill="x", expand=True)
        buttons = tk.Frame(inner, bg=T["panel"])
        buttons.pack(side="right", padx=(10, 0))
        self.send_btn = self.make_button(buttons, "Enviar", self.send, kind="primary")
        self.send_btn.pack(fill="x")
        self.stop_btn = self.make_button(buttons, "Parar", self.stop, kind="danger")
        self.regen_btn = self.make_button(buttons, "Regenerar", self.regenerate)
        self.regen_btn.pack(fill="x", pady=(6, 0))
        self.entry.focus_set()

    # ---------- VISTA 2: SALA DE AULA & APRENDIZAGEM ----------
    def build_classroom_view(self, parent: tk.Frame):
        T = THEME
        # Topo: Painel de Estado & Controlo de Aprendizagem com Modelos Reais
        ctrl_card = tk.Frame(parent, bg=T["panel"], highlightthickness=1, highlightbackground=T["border"])
        ctrl_card.pack(fill="x", padx=14, pady=(12, 8))

        row1 = tk.Frame(ctrl_card, bg=T["panel"])
        row1.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(
            row1, text="🏫 Sala de Aula Autónoma — Aprendizagem Contínua com Modelos Reais",
            bg=T["panel"], fg=T["bot"], font=("Segoe UI", 11, "bold"),
        ).pack(side="left")

        self.btn_toggle_classroom = self.make_button(
            row1, "Ligar / Desligar Sala de Aula", self.toggle_classroom, kind="primary", padx=12, pady=4
        )
        self.btn_toggle_classroom.pack(side="right", padx=(6, 0))
        self.make_button(row1, "🔄 Atualizar", self.refresh_classroom_from_server, padx=10, pady=4).pack(side="right", padx=4)
        self.make_button(row1, "⬇️ Exportar Tudo (.md)", self.export_all_knowledge, padx=10, pady=4).pack(side="right", padx=4)

        # Indicadores ao vivo
        info_box = tk.Frame(ctrl_card, bg=T["bg"], highlightthickness=1, highlightbackground=T["border"])
        info_box.pack(fill="x", padx=14, pady=6)

        self.lbl_cls_mode = tk.Label(info_box, text="Estado: A verificar...", bg=T["bg"], fg=T["bot"], font=FONT_UI_BOLD, anchor="w")
        self.lbl_cls_mode.pack(fill="x", padx=10, pady=(6, 2))

        self.lbl_cls_models = tk.Label(
            info_box,
            text="Modelos Reais Ligados: OpenRouter (deepseek/deepseek-chat) · Groq (llama-3.3-70b-versatile) · Mistral (mistral-small-latest)",
            bg=T["bg"], fg=T["user"], font=FONT_SMALL, anchor="w",
        )
        self.lbl_cls_models.pack(fill="x", padx=10, pady=2)

        self.lbl_cls_topic = tk.Label(info_box, text="Tópico Atual / Último Estudo: —", bg=T["bg"], fg=T["fg"], font=FONT_SMALL, anchor="w")
        self.lbl_cls_topic.pack(fill="x", padx=10, pady=(2, 6))

        # Barra para estudar agora um tema com um modelo real à escolha
        learn_bar = tk.Frame(ctrl_card, bg=T["panel"])
        learn_bar.pack(fill="x", padx=14, pady=(4, 10))

        tk.Label(learn_bar, text="Modelo Professor:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL).pack(side="left")
        self.cmb_teacher = ttk.Combobox(
            learn_bar, values=["auto (Rotação)", "OpenRouter", "Groq", "Mistral"], state="readonly", width=15
        )
        self.cmb_teacher.set("auto (Rotação)")
        self.cmb_teacher.pack(side="left", padx=(4, 10))

        tk.Label(learn_bar, text="Categoria:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL).pack(side="left")
        self.cmb_category = ttk.Combobox(learn_bar, values=DEFAULT_CATEGORIES, state="readonly", width=22)
        self.cmb_category.set(DEFAULT_CATEGORIES[1])
        self.cmb_category.pack(side="left", padx=(4, 10))

        tk.Label(learn_bar, text="Tópico (opcional):", bg=T["panel"], fg=T["muted"], font=FONT_SMALL).pack(side="left")
        self.ent_custom_topic = tk.Entry(
            learn_bar, bg=T["bg"], fg=T["fg"], insertbackground=T["fg"], relief="flat",
            font=FONT_UI, highlightthickness=1, highlightbackground=T["border"], highlightcolor=T["user"],
        )
        self.ent_custom_topic.pack(side="left", fill="x", expand=True, padx=6, ipady=3)

        self.btn_learn_now = self.make_button(
            learn_bar, "⚡ Aprender Agora com Modelo Real", self.learn_now, kind="blue", padx=12, pady=4
        )
        self.btn_learn_now.pack(side="right")

        # Categorias ON/OFF
        cats_frame = tk.Frame(ctrl_card, bg=T["panel"])
        cats_frame.pack(fill="x", padx=14, pady=(0, 8))
        tk.Label(cats_frame, text="Categorias Ativas:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL).pack(side="left", padx=(0, 6))
        self.cat_vars = {}
        for cat in DEFAULT_CATEGORIES:
            var = tk.BooleanVar(value=True)
            self.cat_vars[cat] = var
            cb = tk.Checkbutton(
                cats_frame, text=cat.split(" & ")[0], variable=var, command=self.save_categories_to_server,
                bg=T["panel"], fg=T["fg"], selectcolor=T["bg"], activebackground=T["panel"],
                activeforeground=T["fg"], font=("Segoe UI", 8),
            )
            cb.pack(side="left", padx=3)

        # Corpo inferior dividido: Lista do que já aprendeu (esquerda) + Conteúdo completo da lição (direita)
        split = tk.Frame(parent, bg=T["bg"])
        split.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        left = tk.Frame(split, bg=T["panel"], width=370, highlightthickness=1, highlightbackground=T["border"])
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)

        lh = tk.Frame(left, bg=T["panel"])
        lh.pack(fill="x", padx=10, pady=8)
        self.lbl_lessons_count = tk.Label(
            lh, text="📚 Tudo o que já aprendeu (0 lições)", bg=T["panel"], fg=T["fg"], font=FONT_UI_BOLD, anchor="w"
        )
        self.lbl_lessons_count.pack(side="left")

        list_wrap = tk.Frame(left, bg=T["bg"])
        list_wrap.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        lscroll = tk.Scrollbar(list_wrap, orient="vertical")
        lscroll.pack(side="right", fill="y")
        self.lessons_list = tk.Listbox(
            list_wrap, bg=T["bg"], fg=T["fg"], selectbackground=T["accent_blue"], selectforeground="#ffffff",
            bd=0, relief="flat", font=FONT_SMALL, highlightthickness=0, activestyle="none",
            yscrollcommand=lscroll.set,
        )
        self.lessons_list.pack(side="left", fill="both", expand=True)
        lscroll.config(command=self.lessons_list.yview)
        self.lessons_list.bind("<<ListboxSelect>>", self.on_select_lesson)

        right = tk.Frame(split, bg=T["panel"], highlightthickness=1, highlightbackground=T["border"])
        right.pack(side="left", fill="both", expand=True)

        rh = tk.Frame(right, bg=T["panel"])
        rh.pack(fill="x", padx=12, pady=8)
        self.lbl_lesson_header = tk.Label(
            rh, text="Seleciona uma lição à esquerda ou clica em '⚡ Aprender Agora com Modelo Real'",
            bg=T["panel"], fg=T["user"], font=FONT_UI_BOLD, anchor="w",
        )
        self.lbl_lesson_header.pack(side="left", fill="x", expand=True)

        rbody = tk.Frame(right, bg=T["bg"])
        rbody.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        rscroll = tk.Scrollbar(rbody, orient="vertical")
        rscroll.pack(side="right", fill="y")
        self.lesson_viewer = tk.Text(
            rbody, wrap="word", state="disabled", bg=T["bg"], fg=T["fg"], bd=0, relief="flat",
            font=FONT_UI, padx=14, pady=10, spacing1=2, spacing3=2, highlightthickness=0,
            yscrollcommand=rscroll.set,
        )
        self.lesson_viewer.pack(side="left", fill="both", expand=True)
        rscroll.config(command=self.lesson_viewer.yview)
        self.configure_markdown_tags(self.lesson_viewer)

    # ---------- VISTA 3: SKILLS & CHAVES API ----------
    def build_skills_view(self, parent: tk.Frame):
        T = THEME
        cols = tk.Frame(parent, bg=T["bg"])
        cols.pack(fill="both", expand=True, padx=14, pady=12)

        # Coluna Esquerda: Skills & Diretrizes do Núcleo
        left = tk.Frame(cols, bg=T["panel"], highlightthickness=1, highlightbackground=T["border"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        tk.Label(
            left, text="🧠 Ensinar Nova Skill ou Diretriz ao gois.v1",
            bg=T["panel"], fg=T["bot"], font=FONT_UI_BOLD, anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 6))

        form = tk.Frame(left, bg=T["panel"])
        form.pack(fill="x", padx=14, pady=4)
        tk.Label(form, text="Tipo:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL).grid(row=0, column=0, sticky="w", pady=3)
        self.cmb_skill_type = ttk.Combobox(form, values=["SKILL", "DIRECTRIZ"], state="readonly", width=16)
        self.cmb_skill_type.set("SKILL")
        self.cmb_skill_type.grid(row=0, column=1, sticky="w", padx=6, pady=3)

        tk.Label(form, text="Título:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL).grid(row=1, column=0, sticky="w", pady=3)
        self.ent_skill_title = tk.Entry(
            form, bg=T["bg"], fg=T["fg"], insertbackground=T["fg"], relief="flat", font=FONT_UI,
            highlightthickness=1, highlightbackground=T["border"],
        )
        self.ent_skill_title.grid(row=1, column=1, sticky="ew", padx=6, pady=3, ipady=3)
        form.columnconfigure(1, weight=1)

        tk.Label(left, text="Conteúdo / Regra Técnica:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL, anchor="w").pack(fill="x", padx=14, pady=(4, 2))
        self.txt_skill_content = tk.Text(
            left, height=4, wrap="word", bg=T["bg"], fg=T["fg"], insertbackground=T["fg"],
            relief="flat", font=FONT_UI, padx=8, pady=6, highlightthickness=1, highlightbackground=T["border"],
        )
        self.txt_skill_content.pack(fill="x", padx=14, pady=4)

        btn_row = tk.Frame(left, bg=T["panel"])
        btn_row.pack(fill="x", padx=14, pady=6)
        self.make_button(btn_row, "+ Guardar Skill & Enviar para Render", self.add_skill_ui, kind="primary").pack(side="left")
        self.make_button(btn_row, "Remover Selecionada", self.delete_selected_skill, kind="danger").pack(side="right")

        tk.Label(
            left, text="Skills & Diretrizes Ativas no Núcleo:",
            bg=T["panel"], fg=T["fg"], font=FONT_UI_BOLD, anchor="w",
        ).pack(fill="x", padx=14, pady=(8, 4))

        s_wrap = tk.Frame(left, bg=T["bg"])
        s_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        s_scroll = tk.Scrollbar(s_wrap, orient="vertical")
        s_scroll.pack(side="right", fill="y")
        self.skills_listbox = tk.Listbox(
            s_wrap, bg=T["bg"], fg=T["fg"], selectbackground=T["accent_blue"], bd=0, relief="flat",
            font=FONT_SMALL, highlightthickness=0, yscrollcommand=s_scroll.set,
        )
        self.skills_listbox.pack(side="left", fill="both", expand=True)
        s_scroll.config(command=self.skills_listbox.yview)

        # Coluna Direita: Chaves API dos Modelos Reais
        right = tk.Frame(cols, bg=T["panel"], width=380, highlightthickness=1, highlightbackground=T["border"])
        right.pack(side="left", fill="y")
        right.pack_propagate(False)

        tk.Label(
            right, text="🔑 Fornecedores e Chaves API (Modelos Reais)",
            bg=T["panel"], fg=T["user"], font=FONT_UI_BOLD, anchor="w",
        ).pack(fill="x", padx=14, pady=(12, 6))

        self.lbl_keys_status = tk.Label(
            right, text="A verificar estado das chaves no servidor...",
            bg=T["bg"], fg=T["fg"], font=FONT_SMALL, justify="left", anchor="w", padx=10, pady=10,
        )
        self.lbl_keys_status.pack(fill="x", padx=14, pady=6)

        tk.Label(right, text="Fornecedor:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL, anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        self.cmb_key_provider = ttk.Combobox(right, values=["OpenRouter", "Groq", "Mistral"], state="readonly")
        self.cmb_key_provider.set("OpenRouter")
        self.cmb_key_provider.pack(fill="x", padx=14, pady=2)

        tk.Label(right, text="Nova Chave API:", bg=T["panel"], fg=T["muted"], font=FONT_SMALL, anchor="w").pack(fill="x", padx=14, pady=(8, 2))
        self.ent_api_key = tk.Entry(
            right, bg=T["bg"], fg=T["fg"], insertbackground=T["fg"], show="•", relief="flat",
            font=FONT_UI, highlightthickness=1, highlightbackground=T["border"],
        )
        self.ent_api_key.pack(fill="x", padx=14, pady=2, ipady=4)

        self.make_button(
            right, "☁️ Enviar Chave para o Servidor", self.send_api_key_ui, kind="blue"
        ).pack(fill="x", padx=14, pady=12)

        tk.Label(
            right,
            text=(
                "Nota:\n"
                "• O gois.v1 usa estas chaves tanto para o Chat como para aprender autonomamente na Sala de Aula.\n"
                "• Se um modelo falhar, o sistema faz failover automático para o fornecedor seguinte."
            ),
            bg=T["panel"], fg=T["muted"], font=FONT_SMALL, wraplength=340, justify="left", anchor="w",
        ).pack(fill="x", padx=14, pady=8)

    # ---------- navegação entre abas ----------
    def switch_tab(self, tab_key: str):
        T = THEME
        self.current_tab = tab_key
        for k, btn in self.tab_btns.items():
            btn.configure(
                bg=T["accent_blue"] if k == tab_key else T["btn"],
                font=FONT_UI_BOLD if k == tab_key else FONT_UI,
            )
        for frame in (self.view_chat, self.view_classroom, self.view_skills):
            frame.pack_forget()
        if tab_key == "chat":
            self.view_chat.pack(fill="both", expand=True)
            self.entry.focus_set()
        elif tab_key == "classroom":
            self.view_classroom.pack(fill="both", expand=True)
            self.refresh_classroom_from_server()
        elif tab_key == "skills":
            self.view_skills.pack(fill="both", expand=True)
            self.refresh_skills_from_server()

    def bind_keys(self):
        self.entry.bind("<Return>", self.on_enter)
        self.root.bind("<Control-n>", lambda e: self.new_chat())
        self.root.bind("<Escape>", lambda e: self.stop())

    # ---------- utilitários de interface ----------
    @contextmanager
    def editable(self, widget: tk.Text = None):
        w = widget or self.log
        w.configure(state="normal")
        try:
            yield
        finally:
            w.configure(state="disabled")

    def at_bottom(self) -> bool:
        return self.log.yview()[1] > 0.97

    def set_status(self, text: str, error: bool = False):
        self.status.config(text=text, fg=THEME["err"] if error else THEME["muted"])

    def poll(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(40, self.poll)

    def background(self, fn, done):
        def worker():
            try:
                result, err = fn(), None
            except Exception as e:
                result, err = None, str(e)
            self.ui_q.put(lambda: done(result, err))
        threading.Thread(target=worker, daemon=True).start()

    def on_enter(self, event):
        if event.state & 0x0001:
            return None
        self.send()
        return "break"

    def copy_text(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("Copiado para a área de transferência.")

    def copy_selection(self):
        try:
            self.copy_text(self.log.get("sel.first", "sel.last"))
        except tk.TclError:
            pass

    def copy_last_reply(self):
        for m in reversed(self.history):
            if m["role"] == "assistant":
                self.copy_text(m["content"])
                return

    # ---------- renderização Markdown ----------
    def render_markdown_into(self, widget: tk.Text, text: str):
        for block in parse_markdown(text):
            if block[0] == "code":
                _, lang, code = block
                self.cp_counter += 1
                tag = f"cp{self.cp_counter}"
                widget.insert("end", f"  {lang or 'código'}   ", "code_head")
                widget.insert("end", "[copiar]", ("code_head", "copy", tag))
                widget.insert("end", "\n", "code_head")
                widget.tag_bind(tag, "<Button-1>", lambda e, c=code: self.copy_text(c))
                widget.tag_bind(tag, "<Enter>", lambda e, w=widget: w.config(cursor="hand2"))
                widget.tag_bind(tag, "<Leave>", lambda e, w=widget: w.config(cursor=""))
                widget.insert("end", (code or " ") + "\n", "code")
            else:
                _, kind, runs = block
                base = () if kind == "p" else (kind,)
                if kind == "li":
                    widget.insert("end", "•  ", base)
                for txt, tag in runs:
                    widget.insert("end", txt, base + ((tag,) if tag else ()))
                widget.insert("end", "\n", base)

    def render_user(self, text: str):
        with self.editable():
            self.log.insert("end", "Tu\n", "who_user")
            self.log.insert("end", text.strip() + "\n")

    def render_bot(self, text: str):
        with self.editable():
            self.log.insert("end", "gois.v1\n", "who_bot")
            self.render_markdown_into(self.log, text)

    def render_history(self):
        with self.editable():
            self.log.delete("1.0", "end")
        if not self.history:
            with self.editable():
                self.log.insert(
                    "end",
                    "Olá! Escreve uma mensagem para conversar com o gois.v1, ou abre a aba "
                    "'🏫 Sala de Aula (Modelos Reais)' para veres o que está a aprender.\n"
                    "Enter envia · Shift+Enter muda de linha · Esc pára · Ctrl+N nova conversa.\n",
                    "sys",
                )
        for m in self.history:
            if m["role"] == "user":
                self.render_user(m["content"])
            else:
                self.render_bot(m["content"])
        self.log.see("end")

    def write_sys(self, text: str, error: bool = False):
        with self.editable():
            self.log.insert("end", text + "\n", "err" if error else "sys")
        self.log.see("end")

    # ---------- Sala de Aula & Sincronização Inteligente ----------
    def initial_connect(self):
        self.set_status("A ligar ao servidor (se estava a dormir no Render pode demorar ~1 min)...")
        self.refresh_classroom_from_server(initial=True)

    def schedule_classroom_refresh(self):
        if not self.streaming and not self.learning_busy:
            self.refresh_classroom_from_server(silent=True)
        self.root.after(15000, self.schedule_classroom_refresh)

    def merge_knowledge(self, remote_items: list):
        if not isinstance(remote_items, list):
            return
        by_id = {x.get("id"): x for x in self.knowledge_items if isinstance(x, dict) and x.get("id")}
        for item in reversed(remote_items):
            if isinstance(item, dict) and item.get("id"):
                by_id[item["id"]] = item
        # Ordena mais recente primeiro
        merged = sorted(by_id.values(), key=lambda x: str(x.get("id", "")), reverse=True)[:200]
        if len(merged) != len(self.knowledge_items) or (merged and self.knowledge_items and merged[0].get("id") != self.knowledge_items[0].get("id")):
            self.knowledge_items = merged
            save_json_file(LOCAL_KNOWLEDGE_FILE, self.knowledge_items)
            self.refresh_knowledge_list()

    def refresh_classroom_from_server(self, initial: bool = False, silent: bool = False):
        if not silent:
            self.set_status("A atualizar estado da Sala de Aula...")

        def fetch_all():
            settings = http_json(self.cfg["server"] + "/api/settings", timeout=90)
            knowledge = http_json(self.cfg["server"] + "/api/knowledge", timeout=60)
            # Se o Render reiniciou e perdeu as lições em memória mas temos lições guardadas localmente, restaura!
            if isinstance(knowledge, list) and len(knowledge) == 0 and len(self.knowledge_items) > 0:
                http_json(
                    self.cfg["server"] + "/api/sync/import",
                    {"knowledge": self.knowledge_items},
                    self.cfg["sync_token"],
                    timeout=60,
                )
                knowledge = http_json(self.cfg["server"] + "/api/knowledge", timeout=60)
            return {"settings": settings, "knowledge": knowledge}

        def done(res, err):
            if err:
                if not silent:
                    self.set_status("Servidor indisponível: " + err, error=True)
                return
            st = res.get("settings") if isinstance(res.get("settings"), dict) else {}
            kn = res.get("knowledge") if isinstance(res.get("knowledge"), list) else []
            self.server_state.update(st)
            self.merge_knowledge(kn)
            self.update_classroom_ui()
            providers = ", ".join(st.get("providers_with_key", [])) or "NENHUM"
            mode_txt = "Sala de Aula ATIVA" if st.get("classroom_mode") else "Sala de Aula Pausada"
            self.set_status(
                f"Online · {st.get('total_skills', len(self.skills_items))} skills · "
                f"{len(self.knowledge_items)} lições · {mode_txt} · {providers}"
            )

        self.background(fetch_all, done)

    def update_classroom_ui(self):
        T = THEME
        st = self.server_state
        active = bool(st.get("classroom_mode", True))
        learning_now = bool(st.get("learning_now", False))
        providers = st.get("providers_with_key") or []

        if learning_now:
            mode_str = f"⚡ A APRENDER AGORA COM MODELO REAL ({st.get('current_teacher', 'IA')})..."
            fg_color = T["warn"]
        elif active:
            mode_str = "🟢 MODO SALA DE AULA AUTÓNOMA: ATIVO (a estudar tópicos ciclicamente com modelos reais)"
            fg_color = T["bot"]
        else:
            mode_str = "🔴 MODO SALA DE AULA AUTÓNOMA: PAUSADO"
            fg_color = T["err"]

        self.lbl_cls_mode.config(text=mode_str, fg=fg_color)
        self.lbl_cls_models.config(
            text="Modelos Reais Ativos no Servidor: "
            + (", ".join(providers) if providers else "Nenhum configurado (adiciona chave na aba Skills & Chaves)")
        )

        topic = st.get("current_topic") or "Em espera..."
        last_title = st.get("last_learned_title") or (self.knowledge_items[0].get("title") if self.knowledge_items else "Nenhuma ainda")
        last_teacher = st.get("last_teacher") or (self.knowledge_items[0].get("teacher", "") if self.knowledge_items else "")
        teacher_badge = f" (ensinado por {last_teacher})" if last_teacher else ""
        self.lbl_cls_topic.config(text=f"Tópico em Foco: {topic}   |   Última Lição: {last_title}{teacher_badge}")

        self.chat_classroom_banner.config(
            text=f"🏫 Sala de Aula: {'ATIVA' if active else 'PAUSADA'} · {len(self.knowledge_items)} lições aprendidas · Última: {last_title}{teacher_badge} (clica para abrir)",
            fg=T["bot"] if active else T["muted"],
        )

        active_cats = st.get("active_categories") or DEFAULT_CATEGORIES
        for cat, var in self.cat_vars.items():
            var.set(cat in active_cats)

    def refresh_knowledge_list(self):
        self.lbl_lessons_count.config(text=f"📚 Tudo o que já aprendeu ({len(self.knowledge_items)} lições)")
        sel = self.lessons_list.curselection()
        prev_idx = sel[0] if sel else 0
        self.lessons_list.delete(0, "end")
        for item in self.knowledge_items:
            cat = (item.get("category") or "Geral").split(" & ")[0]
            title = item.get("title") or "Lição"
            teacher = (item.get("teacher") or "IA").split(" · ")[0]
            self.lessons_list.insert("end", f"[{cat}] {title}  ({teacher})")
        if self.knowledge_items:
            idx = min(prev_idx, len(self.knowledge_items) - 1)
            self.lessons_list.selection_set(idx)
            self.show_lesson_at(idx)

    def on_select_lesson(self, _event=None):
        sel = self.lessons_list.curselection()
        if not sel:
            return
        self.show_lesson_at(sel[0])

    def show_lesson_at(self, idx: int):
        if idx < 0 or idx >= len(self.knowledge_items):
            return
        item = self.knowledge_items[idx]
        title = item.get("title") or "Lição"
        cat = item.get("category") or "Geral"
        teacher = item.get("teacher") or "Modelo Real"
        ts = item.get("timestamp") or ""
        self.lbl_lesson_header.config(text=f"{title}  ·  [{cat}]  ·  Modelo Professor: {teacher}  ({ts})")
        with self.editable(self.lesson_viewer):
            self.lesson_viewer.delete("1.0", "end")
            meta_md = f"**Categoria:** `{cat}` · **Modelo Professor:** `{teacher}` · **Data:** `{ts}`\n\n"
            self.render_markdown_into(self.lesson_viewer, meta_md + (item.get("content") or ""))

    def toggle_classroom(self):
        new_mode = not bool(self.server_state.get("classroom_mode", True))
        self.set_status("A alterar estado da Sala de Aula...")

        def done(res, err):
            if err:
                self.set_status("Erro ao alterar Sala de Aula: " + err, error=True)
                return
            if isinstance(res, dict):
                self.server_state.update(res)
            self.update_classroom_ui()

        self.background(
            lambda: http_json(
                self.cfg["server"] + "/api/settings",
                {"classroom_mode": new_mode},
                self.cfg["sync_token"],
            ),
            done,
        )

    def save_categories_to_server(self):
        selected = [cat for cat, var in self.cat_vars.items() if var.get()]
        if not selected:
            selected = list(DEFAULT_CATEGORIES)
            for var in self.cat_vars.values():
                var.set(True)
        self.background(
            lambda: http_json(
                self.cfg["server"] + "/api/settings",
                {"active_categories": selected},
                self.cfg["sync_token"],
            ),
            lambda res, err: self.server_state.update(res) if isinstance(res, dict) else None,
        )

    def learn_now(self):
        if self.learning_busy:
            return
        self.learning_busy = True
        provider_raw = self.cmb_teacher.get().strip()
        provider = "auto" if provider_raw.startswith("auto") else provider_raw
        category = self.cmb_category.get().strip() or DEFAULT_CATEGORIES[0]
        custom_topic = self.ent_custom_topic.get().strip()

        self.btn_learn_now.config(text="⏳ A aprender com modelo real...", state="disabled")
        self.lbl_cls_mode.config(
            text=f"⚡ A ligar ao modelo ({provider}) para estudar '{custom_topic or category}'...",
            fg=THEME["warn"],
        )
        self.set_status("A estudar tópico em tempo real com o modelo professor...")

        payload = {"provider": provider, "category": category, "topic": custom_topic}

        def done(res, err):
            self.learning_busy = False
            self.btn_learn_now.config(text="⚡ Aprender Agora com Modelo Real", state="normal")
            if err:
                self.set_status("Falha ao aprender tópico: " + err, error=True)
                messagebox.showerror("Sala de Aula", f"Não foi possível concluir a lição:\n{err}")
                self.update_classroom_ui()
                return
            lesson = (res or {}).get("lesson")
            settings = (res or {}).get("settings")
            if isinstance(settings, dict):
                self.server_state.update(settings)
            if isinstance(lesson, dict):
                self.merge_knowledge([lesson])
                self.lessons_list.selection_clear(0, "end")
                self.lessons_list.selection_set(0)
                self.show_lesson_at(0)
            self.ent_custom_topic.delete(0, "end")
            self.update_classroom_ui()
            self.set_status(f"Nova lição aprendida com {lesson.get('teacher', 'IA') if isinstance(lesson, dict) else 'sucesso'}!")

        self.background(
            lambda: http_json(self.cfg["server"] + "/api/classroom/learn", payload, self.cfg["sync_token"], timeout=95),
            done,
        )

    def export_all_knowledge(self):
        if not self.knowledge_items and not self.skills_items:
            messagebox.showinfo("Exportar", "Ainda não há lições aprendidas para exportar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            initialfile="gois_v1_conhecimento_" + time.strftime("%Y%m%d_%H%M") + ".md",
            filetypes=[("Markdown", "*.md"), ("Texto", "*.txt")],
        )
        if not path:
            return
        lines = ["# BASE DE CONHECIMENTO & SALA DE AULA — gois.v1\n"]
        lines.append(f"*Total: {len(self.knowledge_items)} lições aprendidas com modelos reais.*\n")
        for k in self.knowledge_items:
            lines.append(
                f"## {k.get('title', 'Lição')}\n"
                f"- **Categoria:** {k.get('category', 'Geral')}\n"
                f"- **Modelo Professor:** {k.get('teacher', 'IA')}\n"
                f"- **Data:** {k.get('timestamp', '')}\n\n"
                f"{k.get('content', '').strip()}\n\n---\n"
            )
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self.set_status("Base de conhecimento exportada com sucesso.")
        except Exception as e:
            messagebox.showerror("Exportar", f"Erro ao guardar ficheiro: {e}")

    # ---------- Skills & Chaves API ----------
    def refresh_skills_from_server(self):
        def fetch():
            skills = http_json(self.cfg["server"] + "/api/skills", timeout=60)
            keys = http_json(self.cfg["server"] + "/api/keys/status", timeout=60)
            return {"skills": skills, "keys": keys}

        def done(res, err):
            if err:
                self.set_status("Erro ao carregar skills/chaves: " + err, error=True)
                return
            skills = res.get("skills")
            if isinstance(skills, list):
                self.skills_items = skills
                save_json_file(LOCAL_SKILLS_FILE, skills)
                self.skills_listbox.delete(0, "end")
                for s in skills:
                    self.skills_listbox.insert("end", f"[{s.get('type', 'SKILL')}] {s.get('title', '')} — {s.get('content', '')[:90]}")
            keys = res.get("keys")
            if isinstance(keys, dict):
                lines = []
                for name, info in keys.items():
                    ok = "🟢 Ativa" if info.get("configured") else "🔴 Sem chave"
                    src = f" ({info.get('source')})" if info.get("source") else ""
                    mdl = info.get("model", "")
                    lines.append(f"• {name} [{mdl}]:  {ok}{src}")
                self.lbl_keys_status.config(text="\n".join(lines))

        self.background(fetch, done)

    def add_skill_ui(self):
        stype = self.cmb_skill_type.get().strip() or "SKILL"
        title = self.ent_skill_title.get().strip()
        content = self.txt_skill_content.get("1.0", "end").strip()
        if not title or not content:
            messagebox.showwarning("Skills", "Preenche o título e o conteúdo da Skill/Diretriz.")
            return
        new_item = {"id": f"s_{int(time.time() * 1000)}", "type": stype, "title": title, "content": content}

        def done(_res, err):
            if err:
                messagebox.showerror("Skills", f"Erro ao guardar skill: {err}")
                return
            self.ent_skill_title.delete(0, "end")
            self.txt_skill_content.delete("1.0", "end")
            self.refresh_skills_from_server()
            self.set_status("Nova skill adicionada e sincronizada com o Render.")

        self.background(
            lambda: http_json(self.cfg["server"] + "/api/skills", new_item, self.cfg["sync_token"]),
            done,
        )

    def delete_selected_skill(self):
        sel = self.skills_listbox.curselection()
        if not sel or sel[0] >= len(self.skills_items):
            return
        skill = self.skills_items[sel[0]]
        sid = skill.get("id")
        if not sid:
            return

        def done(_res, err):
            if err:
                messagebox.showerror("Skills", f"Não foi possível remover: {err}")
                return
            self.refresh_skills_from_server()

        self.background(
            lambda: http_json(f"{self.cfg['server']}/api/skills/{sid}", None, self.cfg["sync_token"], method="DELETE"),
            done,
        )

    def send_api_key_ui(self):
        provider = self.cmb_key_provider.get().strip()
        key = self.ent_api_key.get().strip()
        if not key:
            messagebox.showwarning("Chave API", "Cola uma chave API válida primeiro.")
            return

        def done(_res, err):
            if err:
                messagebox.showerror("Chave API", f"Erro ao enviar chave: {err}")
                return
            self.ent_api_key.delete(0, "end")
            self.refresh_skills_from_server()
            self.refresh_classroom_from_server(silent=True)
            messagebox.showinfo("Chave API", f"Chave para {provider} ativada no servidor!")

        self.background(
            lambda: http_json(
                self.cfg["server"] + "/api/keys",
                {"provider": provider, "key": key},
                self.cfg["sync_token"],
            ),
            done,
        )

    # ---------- Sincronização & Exportação Chat ----------
    def save_session(self):
        save_json_file(SESSION_FILE, self.history)

    def new_chat(self):
        if self.streaming:
            self.stop()
        self.history = []
        self.save_session()
        self.render_history()

    def sync(self):
        payload, notes = {}, []
        for name, field in (("gois_skills_db.json", "skills"), ("gois_keys.json", "keys")):
            for folder in (APP_DIR, Path.cwd()):
                f = folder / name
                if f.is_file():
                    data = load_json_file(f, None)
                    if data is None:
                        notes.append(f"{name} ilegível")
                    else:
                        payload[field] = data
                    break
        if self.knowledge_items:
            payload["knowledge"] = self.knowledge_items
        if not payload:
            messagebox.showwarning("Sincronizar", "Nenhum ficheiro encontrado para sincronizar.")
            return
        self.set_status("A sincronizar com o Render...")

        def done(res, err):
            if err:
                self.set_status("Falha na sincronização", error=True)
                self.write_sys("Sincronização falhou: " + err, error=True)
                return
            imp = res.get("imported", {})
            keys = ", ".join(imp.get("keys", [])) or "mantidas as do servidor"
            self.write_sys(
                f"Sincronizado com sucesso: {imp.get('skills', 0)} skills, "
                f"{imp.get('knowledge', 0)} lições, chaves: {keys}."
            )
            self.refresh_classroom_from_server(silent=True)

        self.background(
            lambda: http_json(self.cfg["server"] + "/api/sync/import", payload, self.cfg["sync_token"]),
            done,
        )

    def export(self):
        if not self.history:
            messagebox.showinfo("Exportar", "Ainda não há conversa para exportar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            initialfile="conversa_gois_" + time.strftime("%Y%m%d_%H%M") + ".md",
            filetypes=[("Markdown", "*.md"), ("Texto", "*.txt")],
        )
        if not path:
            return
        parts = []
        for m in self.history:
            parts.append(("## Tu\n\n" if m["role"] == "user" else "## gois.v1\n\n") + m["content"].strip())
        try:
            Path(path).write_text("\n\n".join(parts) + "\n", encoding="utf-8")
            self.set_status("Conversa exportada.")
        except Exception as e:
            messagebox.showerror("Exportar", f"Não foi possível guardar: {e}")

    # ---------- Chat com Streaming SSE ----------
    def send(self):
        if self.streaming:
            return
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return
        self.entry.delete("1.0", "end")
        self.last_user_text = text
        self.history.append({"role": "user", "content": text})
        self.save_session()
        if len(self.history) == 1:
            self.render_history()
        else:
            self.render_user(text)
        self.start_reply()

    def regenerate(self):
        if self.streaming:
            return
        if self.history and self.history[-1]["role"] == "assistant":
            self.history.pop()
        if not self.history or self.history[-1]["role"] != "user":
            return
        self.last_user_text = self.history[-1]["content"]
        self.save_session()
        self.render_history()
        self.start_reply()

    def start_reply(self):
        self.streaming = True
        self.cancel = threading.Event()
        self.reply_buf = []
        self.reply_model = ""
        self.got_token = False
        self.send_btn.pack_forget()
        self.stop_btn.pack(fill="x", before=self.regen_btn)
        self.set_status("A pensar...")

        with self.editable():
            self.log.insert("end", "gois.v1\n", "who_bot")
            self.log.mark_set("reply_start", "end-1c")
            self.log.mark_gravity("reply_start", "left")
        self.log.see("end")

        limit = max(2, int(self.cfg["history"]))
        messages = self.history[-limit:]
        cancel = self.cancel

        def emit(kind, value):
            self.ui_q.put(lambda: self.on_stream_event(kind, value, cancel))

        threading.Thread(target=stream_chat, args=(self.cfg, messages, emit, cancel), daemon=True).start()
        self.root.after(8000, lambda: self.cold_start_hint(cancel))

    def cold_start_hint(self, cancel):
        if self.streaming and cancel is self.cancel and not self.got_token:
            self.set_status("A acordar o servidor (plano gratuito do Render, pode demorar cerca de 1 minuto)...")

    def on_stream_event(self, kind, value, cancel):
        if cancel is not self.cancel or not self.streaming:
            return
        if kind == "model":
            self.reply_model = value
        elif kind == "delta":
            if not self.got_token:
                self.got_token = True
                self.set_status("A responder...")
            self.reply_buf.append(value)
            follow = self.at_bottom()
            with self.editable():
                self.log.insert("end", value)
            if follow:
                self.log.see("end")
        elif kind == "done":
            self.finish_reply()
        elif kind == "error":
            self.finish_reply(error=value)

    def stop(self):
        if self.streaming:
            self.cancel.set()
            self.finish_reply(stopped=True)

    def finish_reply(self, error: str = "", stopped: bool = False):
        if not self.streaming:
            return
        self.streaming = False
        self.cancel.set()
        text = "".join(self.reply_buf).strip()
        if not text and not stopped and not error:
            error = "O modelo devolveu uma resposta vazia."
        follow = self.at_bottom()
        with self.editable():
            self.log.delete("reply_start", "end-1c")
            self.log.mark_set("insert_at", "end-1c")
        self.stop_btn.pack_forget()
        self.send_btn.pack(fill="x", before=self.regen_btn)

        if text:
            with self.editable():
                self.render_markdown_into(self.log, text)
            suffix = "\n\n*(resposta interrompida)*" if (stopped or error) else ""
            self.history.append({"role": "assistant", "content": text + suffix})
            if suffix:
                self.write_sys("(resposta interrompida)", error=bool(error))
        elif stopped:
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()
                self.entry.insert("1.0", self.last_user_text)
            self.render_history()
        elif error:
            if self.history and self.history[-1]["role"] == "user":
                self.history.pop()
                self.entry.insert("1.0", self.last_user_text)
            self.render_history()
            self.write_sys("Erro: " + error, error=True)
        self.save_session()

        if error:
            self.set_status("Erro no pedido", error=True)
        else:
            model = f"  ·  {self.reply_model}" if self.reply_model else ""
            self.set_status("Pronto" + model)
        if follow:
            self.log.see("end")

    # ---------- Definições ----------
    def open_settings(self):
        T = THEME
        win = tk.Toplevel(self.root)
        win.title("Definições")
        win.configure(bg=T["panel"])
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()

        def row(label, r):
            tk.Label(win, text=label, bg=T["panel"], fg=T["fg"], font=FONT_UI, anchor="w").grid(
                row=r, column=0, sticky="w", padx=14, pady=(12, 2)
            )

        def entry(r, value, show=""):
            e = tk.Entry(
                win, width=46, bg=T["bg"], fg=T["fg"], insertbackground=T["fg"], relief="flat",
                font=FONT_UI, show=show, highlightthickness=1, highlightbackground=T["border"],
                highlightcolor=T["user"],
            )
            e.insert(0, value)
            e.grid(row=r, column=1, padx=14, pady=(12, 2), ipady=4)
            return e

        row("Servidor", 0)
        e_server = entry(0, self.cfg["server"])
        row("Token de sincronização", 1)
        e_token = entry(1, self.cfg["sync_token"], show="•")
        row("Temperatura (0 a 1.5)", 2)
        e_temp = entry(2, str(self.cfg["temperature"]))
        row("Mensagens enviadas ao modelo", 3)
        e_hist = entry(3, str(self.cfg["history"]))

        def save():
            try:
                temp = max(0.0, min(1.5, float(e_temp.get().replace(",", "."))))
                hist = max(2, min(100, int(e_hist.get())))
            except ValueError:
                messagebox.showerror("Definições", "Temperatura e mensagens têm de ser números.", parent=win)
                return
            server = e_server.get().strip().rstrip("/") or DEFAULT_SERVER
            if not server.startswith(("http://", "https://")):
                messagebox.showerror("Definições", "O servidor tem de começar por https://", parent=win)
                return
            self.cfg.update({"server": server, "sync_token": e_token.get().strip(), "temperature": temp, "history": hist})
            save_json_file(CONFIG_FILE, self.cfg)
            win.destroy()
            self.refresh_classroom_from_server()

        btns = tk.Frame(win, bg=T["panel"])
        btns.grid(row=4, column=0, columnspan=2, pady=16)
        self.make_button(btns, "Guardar", save, kind="primary").pack(side="left", padx=6)
        self.make_button(btns, "Cancelar", win.destroy).pack(side="left", padx=6)

    def on_close(self):
        self.cancel.set()
        self.save_session()
        self.root.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    GoisWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
