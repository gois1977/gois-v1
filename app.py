import threading
import time
import asyncio
import uvicorn
import os
import json
import re
from datetime import datetime
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
import httpx

# ---------------------------------------------------------------------------
# DETEÇÃO DE AMBIENTE
# ---------------------------------------------------------------------------
IS_PRODUCTION = (
    os.environ.get("RENDER", "").lower() == "true"
    or os.environ.get("RENDER_EXTERNAL_HOSTNAME") is not None
)

RENDER_CLOUD_URL = os.environ.get("GOIS_RENDER_URL", "https://gois-v1.onrender.com").rstrip("/")
SYNC_SECRET = os.environ.get("GOIS_SYNC_SECRET", "")

# ATENÇÃO: Substitui esta chave pela tua se necessário
DEFAULT_OPENROUTER_KEY = os.environ.get(
    "GOIS_DEFAULT_OPENROUTER_KEY",
    "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",
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GOIS_SYSTEM_PROMPT = (
    "Tu és o gois.v1, um núcleo de IA privado, autónomo e um parceiro de engenharia "
    "de topo. Atuas como um ecossistema de escritório completo para desenvolvimento "
    "de software, jogos 3D/2D, arquitetura e automação."
)

KEYS_FILE = "gois_keys.json"
KNOWLEDGE_FILE = "gois_knowledge_db.json"
SETTINGS_FILE = "gois_settings.json"
SKILLS_FILE = "gois_skills_db.json"

# ---------------------------------------------------------------------------
# CHAVES
# ---------------------------------------------------------------------------
def load_keys() -> list:
    if os.path.exists(KEYS_FILE):
        try:
            with open(KEYS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
        except Exception:
            pass
    return [{
        "id": "k_1",
        "provider": "OpenRouter",
        "name": "OpenRouter Principal",
        "key": DEFAULT_OPENROUTER_KEY,
        "status": "Ativo",
    }]

def save_keys(keys: list) -> None:
    try:
        with open(KEYS_FILE, "w", encoding="utf-8") as f:
            json.dump(keys, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERRO] gravar chaves: {e}")

API_KEYS = load_keys()

# ---------------------------------------------------------------------------
# SKILLS
# ---------------------------------------------------------------------------
def load_skills() -> list:
    default_skills = [
        {"id": "s_1", "title": "Profissionalismo & Código Completo", "type": "DIRECTRIZ", "content": "Sê sempre rigoroso, claro e direto ao assunto. Entrega sempre ficheiros de código completos, descomprimidos e funcionais."},
        {"id": "s_2", "title": "Arquitetura de Jogos de Estratégia & Map Editors", "type": "SKILL", "content": "Especialista em sistemas de grelha (tile grids), câmaras isométricas, editores de edifícios 3D."},
        {"id": "s_3", "title": "Desenvolvimento Unity C# & Shaders", "type": "SKILL", "content": "Criação de Character Controllers avançados, máquinas de estado (FSM), física customizada."},
        {"id": "s_4", "title": "WebGL, Three.js & Jogos Browser", "type": "SKILL", "content": "Renderização 3D/2D no browser com Three.js e Phaser."},
        {"id": "s_5", "title": "Arquitetura FastAPI & Python Assíncrono", "type": "SKILL", "content": "Construção de backends assíncronos de alta velocidade, WebSockets."},
        {"id": "s_20", "title": "Otimização para Hardware Leve / All-in-One", "type": "DIRECTRIZ", "content": "Prioriza código super leve, rápido e eficiente em recursos de CPU/RAM."},
    ]

    if os.path.exists(SKILLS_FILE):
        try:
            with open(SKILLS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, list):
                    data = []
                existing_ids = {s.get("id") for s in data}
                merged = list(data)
                for ds in default_skills:
                    if ds["id"] not in existing_ids:
                        merged.append(ds)
                if len(merged) != len(data):
                    save_skills(merged)
                return merged
        except Exception:
            pass

    save_skills(default_skills)
    return default_skills

def save_skills(skills: list) -> None:
    try:
        with open(SKILLS_FILE, "w", encoding="utf-8") as f:
            json.dump(skills, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERRO] gravar skills: {e}")

SKILLS_DB = load_skills()

# ---------------------------------------------------------------------------
# CONHECIMENTO & SETTINGS
# ---------------------------------------------------------------------------
def load_knowledge() -> list:
    if os.path.exists(KNOWLEDGE_FILE):
        try:
            with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []

def save_knowledge(data: list) -> None:
    try:
        with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERRO] gravar conhecimento: {e}")

DEFAULT_CATEGORIES = [
    "Tecnologia & Software", 
    "Game Design & Unity", 
    "Engenharia & AutoCAD", 
    "Inteligência Artificial", 
    "Ciências & Exatas", 
    "Humanidades & Filosofia", 
    "DevOps & Ferramentas"
]

def load_settings() -> dict:
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "active_categories" not in data or not data["active_categories"]:
                    data["active_categories"] = DEFAULT_CATEGORIES
                if "current_topic" not in data:
                    data["current_topic"] = "Em espera..."
                return data
        except Exception:
            pass
    return {
        "classroom_mode": False,
        "teacher_model": "deepseek/deepseek-r1:free",
        "active_categories": DEFAULT_CATEGORIES,
        "current_topic": "Em espera...",
    }

def save_settings(data: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERRO] gravar definições: {e}")

KNOWLEDGE_DB = load_knowledge()
SERVER_SETTINGS = load_settings()

# ---------------------------------------------------------------------------
# SYNC PC <-> RENDER
# ---------------------------------------------------------------------------
def _sync_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if SYNC_SECRET:
        h["X-Sync-Secret"] = SYNC_SECRET
    return h

async def push_state_to_render() -> bool:
    if IS_PRODUCTION:
        return True
    payload = {
        "keys": API_KEYS,
        "skills": SKILLS_DB,
        "settings": SERVER_SETTINGS,
        "knowledge": KNOWLEDGE_DB[:50],
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{RENDER_CLOUD_URL}/api/sync/import",
                json=payload,
                headers=_sync_headers(),
                timeout=20.0,
            )
            return r.status_code == 200
    except Exception as e:
        print(f"[SYNC] Push erro: {e}")
        return False

async def pull_state_from_render() -> bool:
    global API_KEYS, SKILLS_DB, KNOWLEDGE_DB, SERVER_SETTINGS
    if IS_PRODUCTION:
        return False

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{RENDER_CLOUD_URL}/api/sync/export",
                headers=_sync_headers(),
                timeout=20.0,
            )
            if r.status_code != 200:
                return False

            data = r.json()

            remote_keys = data.get("keys", []) or []
            local_kv = {k.get("key") for k in API_KEYS if k.get("key")}
            added = 0
            for rk in remote_keys:
                if rk.get("key") and rk["key"] not in local_kv:
                    API_KEYS.append(rk)
                    local_kv.add(rk["key"])
                    added += 1
            if added:
                save_keys(API_KEYS)

            remote_skills = data.get("skills", []) or []
            local_st = {s.get("title") for s in SKILLS_DB if s.get("title")}
            added = 0
            for rs in remote_skills:
                if rs.get("title") and rs["title"] not in local_st:
                    SKILLS_DB.insert(0, rs)
                    local_st.add(rs["title"])
                    added += 1
            if added:
                save_skills(SKILLS_DB)

            remote_know = data.get("knowledge", []) or []
            local_ki = {k.get("id") for k in KNOWLEDGE_DB if k.get("id")}
            added = 0
            for rk in remote_know:
                if rk.get("id") and rk["id"] not in local_ki:
                    KNOWLEDGE_DB.insert(0, rk)
                    local_ki.add(rk["id"])
                    added += 1
            if added:
                save_knowledge(KNOWLEDGE_DB)

            if isinstance(data.get("settings"), dict):
                SERVER_SETTINGS.update(data["settings"])
                save_settings(SERVER_SETTINGS)

            return True
    except Exception as e:
        print(f"[SYNC] Pull erro: {e}")
        return False

# ---------------------------------------------------------------------------
# CLIENTE LLM MULTI-PROVIDER COM FAILOVER (CORAÇÃO DO SISTEMA)
# ---------------------------------------------------------------------------
PROVIDER_ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai":     "https://api.openai.com/v1/chat/completions",
    "deepseek":   "https://api.deepseek.com/chat/completions",
    "groq":       "https://api.groq.com/openai/v1/chat/completions",
    "mistral":    "https://api.mistral.ai/v1/chat/completions",
    "google":     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
}

PROVIDER_MODELS = {
    "openrouter": [
        "deepseek/deepseek-r1:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemini-2.0-flash-exp:free",
        "mistralai/mistral-small-3.2-24b-instruct:free"
    ],
    "openai":   ["gpt-4o-mini", "gpt-4o"],
    "deepseek": ["deepseek-chat"],
    "groq":     ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
    "mistral":  ["mistral-large-latest", "mistral-small-latest"],
    "google":   ["gemini-2.0-flash", "gemini-1.5-flash"],
}

def _resolve_provider(provider: str) -> str:
    p = (provider or "").lower()
    if "openrouter" in p: return "openrouter"
    if "openai" in p: return "openai"
    if "deepseek" in p: return "deepseek"
    if "groq" in p: return "groq"
    if "mistral" in p: return "mistral"
    if "google" in p or "gemini" in p: return "google"
    return "openrouter"

async def make_llm_request(payload: dict, timeout: float = 45.0):
    if not API_KEYS:
        print("[LLM] ERRO: Sem chaves configuradas")
        return None

    async with httpx.AsyncClient() as client:
        for item in API_KEYS:
            key = (item.get("key") or "").strip()
            if not key:
                continue

            provider_id = _resolve_provider(item.get("provider", ""))
            url = PROVIDER_ENDPOINTS[provider_id]
            models = PROVIDER_MODELS[provider_id]

            for model_name in models:
                current_payload = {**payload, "model": model_name}
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                }
                if provider_id == "openrouter":
                    headers["HTTP-Referer"] = RENDER_CLOUD_URL
                    headers["X-Title"] = "gois.v1 Office"

                try:
                    resp = await client.post(url, json=current_payload, headers=headers, timeout=timeout)
                    if resp.status_code == 200:
                        print(f"[LLM] SUCESSO → {provider_id} a usar {model_name}")
                        return resp
                    print(f"[LLM] FALHOU {provider_id}/{model_name} → HTTP {resp.status_code}")
                except Exception as e:
                    print(f"[LLM] ERRO REDE {provider_id}/{model_name}: {e}")
        
        print("[LLM] ERRO FATAL: Todos os modelos e chaves falharam.")
        return None

# ---------------------------------------------------------------------------
# SALA DE AULA
# ---------------------------------------------------------------------------
UNIVERSAL_CLASSROOM_TOPICS = [
    ("Advanced Python Async architectures and Event Loops", "Tecnologia & Software"),
    ("Full-stack WebSockets and high-concurrency API design", "Tecnologia & Software"),
    ("Unity 3D tile grid layouts, isometric cameras and map editors", "Game Design & Unity"),
    ("Real-time strategy building placement and asset inspection systems", "Game Design & Unity"),
    ("AutoCAD automated drawing pipelines and parametric geometry", "Engenharia & AutoCAD"),
    ("Neural network distillation, RAG pipelines and autonomous agents", "Inteligência Artificial"),
    ("Advanced thermodynamics, physics engines and vector calculus", "Ciências & Exatas"),
    ("Strategic decision systems, game theory and economic models", "Humanidades & Filosofia"),
    ("Docker containerization, Render cloud deployment and Git workflows", "DevOps & Ferramentas"),
]

def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError("JSON inválido na resposta")

async def background_classroom_loop():
    if not IS_PRODUCTION:
        await asyncio.sleep(2)
        await pull_state_from_render()
        await push_state_to_render()

    while True:
        await asyncio.sleep(45)

        if not IS_PRODUCTION:
            await pull_state_from_render()

        settings = load_settings()
        if not settings.get("classroom_mode", False):
            continue

        active_cats = settings.get("active_categories", []) or DEFAULT_CATEGORIES
        valid_topics = [t for t in UNIVERSAL_CLASSROOM_TOPICS if t[1] in active_cats]
        if not valid_topics:
            valid_topics = UNIVERSAL_CLASSROOM_TOPICS

        idx = int(time.time() / 45) % len(valid_topics)
        topic, cat = valid_topics[idx]

        SERVER_SETTINGS["current_topic"] = f"[{cat}] {topic}"
        save_settings(SERVER_SETTINGS)

        payload = {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"Gera uma lição técnica prática e avançada sobre {cat}. "
                        "Responde APENAS em JSON válido com as chaves 'title' e 'content'."
                    ),
                },
                {"role": "user", "content": f"Tema: {topic}"},
            ]
        }

        resp = await make_llm_request(payload, timeout=45.0)
        if not (resp and resp.status_code == 200):
            print("[SALA] Sem resposta válida, a tentar novamente no próximo ciclo")
            continue

        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json(content)

            entry = {
                "id": f"k_{int(time.time()*1000)}",
                "title": parsed.get("title", topic),
                "content": parsed.get("content", ""),
                "category": cat,
                "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            }
            KNOWLEDGE_DB.insert(0, entry)
            save_knowledge(KNOWLEDGE_DB)

            if not IS_PRODUCTION:
                await push_state_to_render()
        except Exception as e:
            print(f"[SALA] erro a processar lição: {e}")

# ---------------------------------------------------------------------------
# HTML (A interface que já tinhas)
# ---------------------------------------------------------------------------
UNIFIED_OFFICE_HTML = """<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>gois.v1 | Office & Command Center</title>
    <meta name="theme-color" content="#090d16">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --bg: #090d16; --panel: #111827; --panel-alt: #161f33;
            --border: #1f2937; --accent: #3b82f6; --accent-green: #10b981;
            --text: #f3f4f6; --text-muted: #9ca3af; --user-msg: #1e293b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; background-color: var(--bg); color: var(--text); height: 100dvh; display: flex; flex-direction: column; overflow: hidden; }
        header { background: var(--panel); border-bottom: 1px solid var(--border); padding: 10px 16px; display: flex; align-items: center; justify-content: space-between; z-index: 10; flex-wrap: wrap; gap: 10px; }
        .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; color: var(--accent); font-size: 1.05rem; }
        .status-dot { width: 10px; height: 10px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 10px var(--accent-green); }
        .cloud-badge { font-size: 0.72rem; background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid var(--accent-green); padding: 3px 8px; border-radius: 99px; cursor: pointer; }
        .nav-tabs { display: flex; gap: 6px; background: var(--panel-alt); padding: 4px; border-radius: 8px; border: 1px solid var(--border); }
        .nav-btn { background: transparent; border: none; color: var(--text-muted); padding: 6px 14px; border-radius: 6px; font-size: 0.8rem; font-weight: 600; cursor: pointer; transition: 0.2s; }
        .nav-btn.active { background: var(--accent); color: #fff; }
        .app-view { flex: 1; display: none; flex-direction: column; overflow: hidden; }
        .app-view.active { display: flex; }
        #chat-container { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 14px; }
        .msg { max-width: 90%; padding: 14px 16px; border-radius: 12px; font-size: 0.94rem; line-height: 1.5; word-wrap: break-word; }
        .msg.user { align-self: flex-end; background: var(--user-msg); border: 1px solid #334155; }
        .msg.assistant { align-self: flex-start; background: var(--panel); border: 1px solid var(--border); width: 100%; }
        .msg.assistant pre { background: #05070c; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 10px 0; }
        .msg.assistant code { color: #a7f3d0; font-family: Consolas, monospace; }
        .welcome-box { max-width: 700px; margin: auto; text-align: center; padding: 20px; }
        .welcome-box h1 { font-size: 1.8rem; margin-bottom: 10px; color: #fff; }
        .welcome-box p { color: var(--text-muted); font-size: 0.92rem; margin-bottom: 20px; }
        .prompt-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; text-align: left; }
        .prompt-card { background: var(--panel); border: 1px solid var(--border); padding: 12px 14px; border-radius: 8px; font-size: 0.84rem; cursor: pointer; transition: 0.2s; }
        .prompt-card:hover { border-color: var(--accent); background: var(--panel-alt); }
        .input-bar { background: var(--panel); border-top: 1px solid var(--border); padding: 12px 16px; display: flex; gap: 10px; align-items: flex-end; }
        textarea { flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 10px; padding: 12px 14px; font-size: 0.95rem; resize: none; outline: none; font-family: inherit; }
        textarea:focus { border-color: var(--accent); }
        #send-btn { background: var(--accent); color: #fff; border: none; border-radius: 10px; padding: 0 20px; height: 44px; font-weight: 700; cursor: pointer; }
        .view-content { padding: 20px; overflow-y: auto; flex: 1; max-width: 950px; width: 100%; margin: 0 auto; }
        .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 16px; }
        .input-box { width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 10px 14px; border-radius: 8px; font-size: 0.88rem; margin-bottom: 12px; outline: none; }
        .input-box:focus { border-color: var(--accent); }
        .btn-action { background: var(--accent-green); color: #04130e; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 0.85rem; }
        .key-item { background: var(--bg); border: 1px solid var(--border); padding: 14px; border-radius: 10px; display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .skill-badge { font-size: 0.68rem; padding: 3px 8px; border-radius: 99px; font-weight: 700; background: rgba(59,130,246,0.2); color: #93c5fd; border: 1px solid var(--accent); }
        .directive-badge { font-size: 0.68rem; padding: 3px 8px; border-radius: 99px; font-weight: 700; background: rgba(245,158,11,0.2); color: #fcd34d; border: 1px solid #f59e0b; }
        .cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 10px; margin-top: 10px; margin-bottom: 14px; }
        .cat-checkbox { display: flex; align-items: center; gap: 8px; background: var(--bg); border: 1px solid var(--border); padding: 8px 10px; border-radius: 8px; font-size: 0.8rem; cursor: pointer; }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <div class="status-dot"></div>
            <span>gois.v1 | Office & Command Center</span>
            <span id="sync-badge" class="cloud-badge" onclick="manualCloudSync()" title="Clica para forçar sincronização">☁️ Sync Render</span>
        </div>
        <div class="nav-tabs">
            <button class="nav-btn active" onclick="switchView('view-chat', this)">💬 Chat</button>
            <button class="nav-btn" onclick="switchView('view-keys', this)">🔑 Chaves API</button>
            <button class="nav-btn" onclick="switchView('view-skills', this)">🧠 Skills & Sala de Aula</button>
            <button class="nav-btn" onclick="switchView('view-projects', this)">🚀 Projetos</button>
        </div>
    </header>

    <div id="view-chat" class="app-view active">
        <div id="chat-container">
            <div class="welcome-box">
                <h1>O QUE VAMOS CONSTRUIR HOJE?</h1>
                <p>Ligado ao teu ecossistema de Skills e sincronizado com <b>gois-v1.onrender.com</b>:</p>
                <div class="prompt-grid">
                    <div class="prompt-card" onclick="usePrompt('Cria um editor de mapas 3D isométrico em Three.js com grelha de construção estilo estratégia')">🏗️ Editor de Mapas 3D (Tile Grid)</div>
                    <div class="prompt-card" onclick="usePrompt('Gera um Character Controller em C# (.cs) para Unity com dash e double jump')">⚡ Script C# Unity: Character Controller</div>
                    <div class="prompt-card" onclick="usePrompt('Cria uma arquitetura FastAPI assíncrona completa com endpoints de gestão')">⚙️ Arquitetura FastAPI Assíncrona</div>
                    <div class="prompt-card" onclick="usePrompt('Desenvolve um script de automação para processamento geométrico')">📐 Automação e Processamento Geométrico</div>
                </div>
            </div>
        </div>
        <div class="input-bar">
            <textarea id="user-input" rows="1" placeholder="Descreve o projeto ou instrução técnica... (Enter para enviar)"></textarea>
            <button id="send-btn" onclick="sendMessage()">Enviar</button>
        </div>
    </div>

    <div id="view-keys" class="app-view">
        <div class="view-content">
            <div class="card">
                <h3 style="color: var(--accent); margin-bottom: 6px;">Adicionar Nova Chave API</h3>
                <p style="font-size: 0.78rem; color: var(--accent-green); margin-bottom: 12px;">☁️ A chave é enviada automaticamente para o teu Render.</p>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Fornecedor:</label>
                <select id="key-provider" class="input-box">
                    <option value="OpenRouter">OpenRouter</option>
                    <option value="OpenAI">OpenAI / ChatGPT</option>
                    <option value="DeepSeek">DeepSeek</option>
                    <option value="Groq">Groq (Ultra-rápido)</option>
                    <option value="Mistral">Mistral AI</option>
                    <option value="Google Gemini">Google Gemini</option>
                </select>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Nome (opcional):</label>
                <input id="key-name" class="input-box" type="text" placeholder="ex: Minha Chave Pessoal" />
                <label style="font-size: 0.78rem; color: var(--text-muted);">Chave API:</label>
                <input id="key-secret" class="input-box" type="password" placeholder="sk-... ou gsk_..." />
                <button class="btn-action" onclick="addApiKey()">+ Adicionar & Sincronizar com Render</button>
                <div id="key-sync-feedback" style="font-size:0.8rem; margin-top:8px;"></div>
            </div>
            <div class="card">
                <h3 style="margin-bottom: 12px;">Chaves Ativas no Sistema</h3>
                <div id="keys-list-container">A carregar chaves...</div>
            </div>
        </div>
    </div>

    <div id="view-skills" class="app-view">
        <div class="view-content">
            <div class="card" style="border-color: var(--accent-green);">
                <h3 style="color: var(--accent-green); margin-bottom: 6px;">🏫 Modo Sala de Aula Autónoma & Métricas</h3>
                <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 10px;">Monitorização em tempo real e seleção de tópicos ativos.</p>
                <div style="background: var(--bg); border: 1px solid var(--border); padding: 10px; border-radius: 8px; margin-bottom: 12px; font-size: 0.85rem;">
                    <div><b>Estado Atual:</b> <span id="classroom-status-text">A carregar...</span></div>
                    <div style="margin-top: 4px; color: var(--accent);"><b>Tópico Ativo:</b> <span id="active-topic-display">Nenhum no momento</span></div>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px;">
                    <span style="font-size: 0.82rem; font-weight: 700;">Categorias de Foco (ON/OFF):</span>
                    <div style="display: flex; gap: 8px;">
                        <button class="nav-btn" style="background: var(--accent); color: #fff;" onclick="manualCloudSync()">🔄 Sincronizar Agora</button>
                        <button class="btn-action" style="padding: 6px 12px; font-size: 0.78rem;" onclick="toggleClassroom()">Ligar / Desligar Sala de Aula</button>
                    </div>
                </div>
                <div id="categories-container" class="cat-grid"></div>
                <h4 style="font-size: 0.85rem; margin-top: 14px; margin-bottom: 8px; color: var(--accent);">📊 Percentagem de Dados por Categoria</h4>
                <div id="category-progress-container">A calcular métricas...</div>
            </div>
            <div class="card">
                <h3 style="color: var(--accent); margin-bottom: 10px;">Ensinar Nova Skill ou Directriz</h3>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Tipo:</label>
                <select id="skill-type" class="input-box">
                    <option value="SKILL">Skill (Capacidade técnica)</option>
                    <option value="DIRECTRIZ">Directriz (Comportamento/Regra)</option>
                </select>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Título:</label>
                <input id="skill-title" class="input-box" type="text" placeholder="ex: Shaders GLSL avançados" />
                <label style="font-size: 0.78rem; color: var(--text-muted);">Conteúdo / Regra:</label>
                <textarea id="skill-content" class="input-box" rows="3" placeholder="Descreve o conhecimento, regras ou código..."></textarea>
                <button class="btn-action" onclick="addSkillRecord()">+ Ensinar ao Núcleo & Render</button>
            </div>
            <div class="card">
                <h3 style="margin-bottom: 12px;">Arsenal de Skills & Directrizes Ativas (<span id="skills-count">0</span>)</h3>
                <div id="skills-list-container">A carregar skills...</div>
            </div>
        </div>
    </div>

    <div id="view-projects" class="app-view">
        <div class="view-content">
            <div class="card">
                <h3 style="color: var(--accent); margin-bottom: 10px;">🚀 Central de Projetos & Nuvem Render</h3>
                <p style="font-size: 0.85rem; color: var(--text-muted); margin-bottom: 14px;">O teu modelo está ligado a <b>https://gois-v1.onrender.com</b>.</p>
                <div style="display: flex; gap: 10px; flex-wrap: wrap;">
                    <button class="btn-action" onclick="window.open('/api/knowledge/export', '_blank')">⬇️ Exportar Conhecimento Completo (.MD)</button>
                    <button class="nav-btn" style="background: var(--accent); color: #fff; padding: 10px 16px;" onclick="manualCloudSync()">☁️ Forçar Sincronização PC ↔ Render</button>
                </div>
            </div>
            <div class="card">
                <h3 style="margin-bottom: 12px;">📚 Últimas Lições Aprendidas na Sala de Aula</h3>
                <div id="knowledge-list-container">A carregar lições...</div>
            </div>
        </div>
    </div>

    <script>
        const chatContainer = document.getElementById('chat-container');
        const userInput = document.getElementById('user-input');
        const sendBtn = document.getElementById('send-btn');
        const allAvailableCategories = ["Tecnologia & Software", "Game Design & Unity", "Engenharia & AutoCAD", "Inteligência Artificial", "Ciências & Exatas", "Humanidades & Filosofia", "DevOps & Ferramentas"];
        let messages = [];

        function switchView(viewId, btn) {
            document.querySelectorAll('.app-view').forEach(v => v.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(viewId).classList.add('active');
            btn.classList.add('active');
            if (viewId === 'view-keys') loadKeysUI();
            if (viewId === 'view-skills') { loadSkillsUI(); loadSettingsUI(); }
            if (viewId === 'view-projects') loadKnowledgeListUI();
        }

        async function manualCloudSync() {
            const badge = document.getElementById('sync-badge');
            badge.innerText = '⏳ A sincronizar...';
            try {
                const r = await fetch('/api/sync/trigger', { method: 'POST' });
                const data = await r.json();
                badge.innerText = (data.push_ok === false || data.pull_ok === false) ? '⚠️ Sync parcial' : '✅ Sincronizado!';
                loadKeysUI(); loadSkillsUI(); loadSettingsUI();
                setTimeout(() => badge.innerText = '☁️ Sync Render', 2500);
            } catch (e) { badge.innerText = '⚠️ Erro Sync'; }
        }

        function usePrompt(text) { userInput.value = text; sendMessage(); }
        userInput.addEventListener('input', function() { this.style.height = 'auto'; this.style.height = Math.min(this.scrollHeight, 120) + 'px'; });
        userInput.addEventListener('keydown', function(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });

        async function sendMessage() {
            const text = userInput.value.trim();
            if (!text) return;
            const wb = chatContainer.querySelector('.welcome-box');
            if (wb) wb.remove();
            userInput.value = ''; userInput.style.height = 'auto'; sendBtn.disabled = true;
            messages.push({ role: 'user', content: text });
            appendMsg('user', text);
            const loadDiv = appendMsg('assistant typing', 'A processar instrução no núcleo...');
            try {
                const res = await fetch('/v1/chat/completions', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ messages: messages.slice(-14) })
                });
                const data = await res.json();
                chatContainer.removeChild(loadDiv);
                if (res.ok && data.choices) {
                    const reply = data.choices[0].message.content;
                    messages.push({ role: 'assistant', content: reply });
                    appendMsg('assistant', reply);
                } else {
                    appendMsg('assistant', '**Erro:** ' + (data.detail || 'Falha na resposta. Verifica as chaves API.'));
                }
            } catch (err) {
                chatContainer.removeChild(loadDiv);
                appendMsg('assistant', '**Erro de ligação ao servidor.**');
            } finally { sendBtn.disabled = false; }
        }

        function appendMsg(role, content) {
            const div = document.createElement('div');
            div.className = 'msg ' + role;
            if (role.includes('assistant') && !role.includes('typing')) {
                div.innerHTML = marked.parse(content);
                div.querySelectorAll('pre').forEach(pre => {
                    const btn = document.createElement('button');
                    btn.className = 'nav-btn';
                    btn.style.cssText = 'float:right; background:#1f2937; padding:2px 8px; font-size:0.7rem;';
                    btn.innerText = 'Copiar';
                    btn.onclick = () => { navigator.clipboard.writeText(pre.innerText.replace('Copiar','').trim()); btn.innerText = 'Copiado!'; setTimeout(() => btn.innerText = 'Copiar', 1500); };
                    pre.prepend(btn);
                });
            } else { div.textContent = content; }
            chatContainer.appendChild(div);
            chatContainer.scrollTop = chatContainer.scrollHeight;
            return div;
        }

        async function loadKeysUI() {
            const res = await fetch('/api/keys'); const keys = await res.json();
            const container = document.getElementById('keys-list-container'); container.innerHTML = '';
            keys.forEach(k => {
                container.innerHTML += `<div class="key-item"><div><b>${k.provider}</b> <span style="color:var(--text-muted); font-size:0.8rem;">(${k.name || 'Geral'})</span><div style="font-size:0.75rem; color:var(--accent-green);">● ${k.status || 'Ativo'}</div></div><button class="nav-btn" style="background:#ef4444; color:#fff;" onclick="deleteKey('${k.id}')">Remover</button></div>`;
            });
        }

        async function addApiKey() {
            const provider = document.getElementById('key-provider').value;
            const name = document.getElementById('key-name').value.trim();
            const key = document.getElementById('key-secret').value.trim();
            const fb = document.getElementById('key-sync-feedback');
            if (!key) return alert('Insere uma chave válida!');
            fb.innerText = '⏳ A guardar e a sincronizar com o Render...'; fb.style.color = 'var(--text-muted)';
            const res = await fetch('/api/keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider, name, key }) });
            const data = await res.json();
            document.getElementById('key-secret').value = ''; document.getElementById('key-name').value = '';
            fb.innerText = data.sync === false ? '⚠️ Chave guardada localmente, mas o Render não respondeu.' : '✅ Chave guardada e sincronizada com o Render.';
            fb.style.color = data.sync === false ? '#f59e0b' : 'var(--accent-green)';
            loadKeysUI();
        }

        async function deleteKey(id) { await fetch('/api/keys/' + id, { method: 'DELETE' }); loadKeysUI(); }

        async function loadSkillsUI() {
            const res = await fetch('/api/skills'); const skills = await res.json();
            document.getElementById('skills-count').innerText = skills.length;
            const container = document.getElementById('skills-list-container'); container.innerHTML = '';
            skills.forEach(s => {
                const badge = s.type === 'SKILL' ? '<span class="skill-badge">SKILL</span>' : '<span class="directive-badge">DIRECTRIZ</span>';
                container.innerHTML += `<div class="card" style="margin-bottom: 10px; padding: 12px;"><div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><b>${s.title}</b> ${badge}</div><p style="font-size:0.8rem; color:var(--text-muted);">${s.content}</p></div>`;
            });
        }

        async function addSkillRecord() {
            const type = document.getElementById('skill-type').value;
            const title = document.getElementById('skill-title').value.trim();
            const content = document.getElementById('skill-content').value.trim();
            if (!title || !content) return alert('Preenche todos os campos!');
            await fetch('/api/skills', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ type, title, content }) });
            document.getElementById('skill-title').value = ''; document.getElementById('skill-content').value = '';
            loadSkillsUI();
        }

        async function loadSettingsUI() {
            const res = await fetch('/api/settings'); const data = await res.json();
            const st = document.getElementById('classroom-status-text');
            st.innerText = data.classroom_mode ? "🟢 Ativo (A aprender autonomamente)" : "🔴 Desativado";
            st.style.color = data.classroom_mode ? "var(--accent-green)" : "#ef4444";
            document.getElementById('active-topic-display').innerText = data.current_topic || "Em espera...";
            const container = document.getElementById('categories-container'); container.innerHTML = '';
            allAvailableCategories.forEach(cat => {
                const checked = (data.active_categories || []).includes(cat) ? 'checked' : '';
                container.innerHTML += `<label class="cat-checkbox"><input type="checkbox" value="${cat}" ${checked} onchange="updateCategories()"><span>${cat}</span></label>`;
            });
            loadMetricsData();
        }

        async function updateCategories() {
            const selected = []; document.querySelectorAll('#categories-container input:checked').forEach(cb => selected.push(cb.value));
            await fetch('/api/settings/categories', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ active_categories: selected }) });
        }

        async function toggleClassroom() { await fetch('/api/settings/classroom', { method: 'POST' }); loadSettingsUI(); }

        async function loadMetricsData() {
            const res = await fetch('/api/knowledge'); const data = await res.json(); const items = data.items || [];
            const catCounts = {}; allAvailableCategories.forEach(cat => catCounts[cat] = 0);
            items.forEach(i => { const c = i.category || 'Tecnologia & Software'; catCounts[c] = (catCounts[c] || 0) + 1; });
            const total = items.length || 1; let progHTML = '';
            allAvailableCategories.forEach(cat => {
                const count = catCounts[cat] || 0; const pct = items.length === 0 ? 0 : Math.min(100, Math.round((count / total) * 100));
                progHTML += `<div style="margin-bottom: 8px;"><div style="display: flex; justify-content: space-between; font-size: 0.78rem; margin-bottom: 2px;"><span>📁 ${cat} (${count} lições)</span><span style="color: var(--accent); font-weight:700;">${pct}%</span></div><div style="width: 100%; background: var(--border); height: 6px; border-radius: 3px; overflow: hidden;"><div style="width: ${pct}%; background: var(--accent-green); height: 100%;"></div></div></div>`;
            });
            document.getElementById('category-progress-container').innerHTML = progHTML;
        }

        async function loadKnowledgeListUI() {
            const res = await fetch('/api/knowledge'); const data = await res.json(); const items = data.items || [];
            const container = document.getElementById('knowledge-list-container');
            if (!items.length) { container.innerHTML = '<p style="font-size:0.82rem; color:var(--text-muted);">Ainda sem lições gravadas.</p>'; return; }
            container.innerHTML = '';
            items.slice(0, 15).forEach(k => {
                container.innerHTML += `<div class="card" style="margin-bottom: 10px; padding: 12px;"><div style="display:flex; justify-content:space-between; font-size:0.75rem; color:var(--accent); margin-bottom:4px;"><span>${k.category || 'Geral'}</span><span>${k.timestamp || ''}</span></div><b>${k.title}</b><p style="font-size:0.8rem; color:var(--text-muted); margin-top:4px;">${k.content}</p></div>`;
            });
        }

        setInterval(() => { if (document.getElementById('view-skills').classList.contains('active')) { loadSettingsUI(); } }, 15000);
    </script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# ROTAS API
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_app():
    return HTMLResponse(content=UNIFIED_OFFICE_HTML)

@app.get("/api/sync/export")
async def sync_export(request: Request):
    if SYNC_SECRET and request.headers.get("X-Sync-Secret") != SYNC_SECRET:
        raise HTTPException(401, "Não autorizado")
    return {"keys": API_KEYS, "skills": SKILLS_DB, "settings": SERVER_SETTINGS, "knowledge": KNOWLEDGE_DB[:50]}

@app.post("/api/sync/import")
async def sync_import(request: Request):
    global API_KEYS, SKILLS_DB, SERVER_SETTINGS
    if SYNC_SECRET and request.headers.get("X-Sync-Secret") != SYNC_SECRET:
        raise HTTPException(401, "Não autorizado")
    body = await request.json()

    incoming_keys = body.get("keys", []) or []
    existing_k = {k.get("key") for k in API_KEYS if k.get("key")}
    changed = False
    for ik in incoming_keys:
        if ik.get("key") and ik["key"] not in existing_k:
            API_KEYS.append(ik); existing_k.add(ik["key"]); changed = True
    if changed: save_keys(API_KEYS)

    incoming_skills = body.get("skills", []) or []
    existing_s = {s.get("title") for s in SKILLS_DB if s.get("title")}
    changed = False
    for isk in incoming_skills:
        if isk.get("title") and isk["title"] not in existing_s:
            SKILLS_DB.insert(0, isk); existing_s.add(isk["title"]); changed = True
    if changed: save_skills(SKILLS_DB)

    if isinstance(body.get("settings"), dict):
        SERVER_SETTINGS.update(body["settings"]); save_settings(SERVER_SETTINGS)

    return {"status": "synced", "keys_total": len(API_KEYS), "skills_total": len(SKILLS_DB)}

@app.post("/api/sync/trigger")
async def sync_trigger():
    pull_ok = await pull_state_from_render()
    push_ok = await push_state_to_render()
    return {"status": "ok", "pull_ok": pull_ok, "push_ok": push_ok}

@app.get("/api/keys")
async def get_api_keys():
    return [{"id": k.get("id"), "provider": k.get("provider"), "name": k.get("name"), "status": k.get("status", "Ativo"), "key_preview": (k.get("key", "")[:8] + "…") if k.get("key") else ""} for k in API_KEYS]

@app.post("/api/keys")
async def add_api_key(request: Request):
    global API_KEYS
    body = await request.json()
    key_value = (body.get("key") or "").strip()
    if not key_value: raise HTTPException(400, "Chave vazia")
    new_key = {"id": f"k_{int(time.time())}", "provider": body.get("provider", "OpenRouter"), "name": body.get("name", "Principal"), "key": key_value, "status": "Ativo"}
    API_KEYS.append(new_key); save_keys(API_KEYS)
    sync_ok = True
    if not IS_PRODUCTION: sync_ok = await push_state_to_render()
    return {"status": "success", "sync": sync_ok, "id": new_key["id"]}

@app.delete("/api/keys/{key_id}")
async def delete_api_key(key_id: str):
    global API_KEYS
    API_KEYS = [k for k in API_KEYS if k.get("id") != key_id]
    save_keys(API_KEYS)
    if not IS_PRODUCTION: await push_state_to_render()
    return {"status": "success"}

@app.get("/api/skills")
async def get_skills(): return SKILLS_DB

@app.post("/api/skills")
async def add_skill(request: Request):
    global SKILLS_DB
    body = await request.json()
    new_skill = {"id": f"s_{int(time.time())}", "type": body.get("type", "SKILL"), "title": body.get("title", ""), "content": body.get("content", "")}
    SKILLS_DB.insert(0, new_skill); save_skills(SKILLS_DB)
    if not IS_PRODUCTION: await push_state_to_render()
    return {"status": "success"}

@app.get("/api/settings")
async def get_settings(): return SERVER_SETTINGS

@app.post("/api/settings/classroom")
async def toggle_classroom():
    SERVER_SETTINGS["classroom_mode"] = not SERVER_SETTINGS.get("classroom_mode", False)
    save_settings(SERVER_SETTINGS)
    if not IS_PRODUCTION: await push_state_to_render()
    return SERVER_SETTINGS

@app.post("/api/settings/categories")
async def set_categories(request: Request):
    body = await request.json()
    SERVER_SETTINGS["active_categories"] = body.get("active_categories", [])
    save_settings(SERVER_SETTINGS)
    if not IS_PRODUCTION: await push_state_to_render()
    return SERVER_SETTINGS

@app.get("/api/knowledge")
async def get_knowledge(): return {"items": KNOWLEDGE_DB}

@app.get("/api/knowledge/export")
async def api_export_knowledge():
    lines = ["# BASE DE CONHECIMENTO, SALA DE AULA & SKILLS - GOIS.V1\n"]
    for s in SKILLS_DB: lines.append(f"## [{s.get('type','SKILL')}] {s.get('title','')}\n{s.get('content','')}\n\n---\n")
    for k in KNOWLEDGE_DB: lines.append(f"## [SALA DE AULA] {k.get('title','')} ({k.get('category','Geral')})\n{k.get('content','')}\n\n---\n")
    return StreamingResponse(iter(["\n".join(lines)]), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="gois_v1_office_complete.md"'})

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    body = await request.json()
    messages = body.get("messages", []) or []
    system_parts = [GOIS_SYSTEM_PROMPT]
    
    if SKILLS_DB:
        blocks = ["[DIRECTRIZES E SKILLS DO NÚCLEO]:"]
        for s in SKILLS_DB: blocks.append(f"- [{s.get('type','SKILL')}] {s.get('title','')}: {s.get('content','')}")
        system_parts.append("\n".join(blocks))

    if KNOWLEDGE_DB:
        blocks = ["[REGISTOS DA SALA DE AULA & APRENDIZAGEM]:"]
        for k in KNOWLEDGE_DB[:8]:
            content = (k.get("content") or "")[:250]
            blocks.append(f"- {k.get('title','')}: {content}...")
        system_parts.append("\n".join(blocks))

    formatted = [{"role": "system", "content": "\n\n".join(system_parts)}]
    for m in messages:
        role = m.get("role")
        if role in ("user", "assistant"):
            formatted.append({"role": role, "content": str(m.get("content", ""))})

    payload = {"messages": formatted, "stream": False}
    resp = await make_llm_request(payload)

    if resp and resp.status_code == 200:
        return JSONResponse(content=resp.json())

    raise HTTPException(status_code=502, detail="Erro: Nenhuma chave API ativa ou válida respondeu. Vê os logs do Render para saber o motivo.")

# ---------------------------------------------------------------------------
# BOOTSTRAP
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    def start_background_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(background_classroom_loop())
        except Exception as e:
            print(f"[BG] loop terminou com erro: {e}")

    threading.Thread(target=start_background_loop, daemon=True).start()

    if IS_PRODUCTION:
        port = int(os.environ.get("PORT", 10000))
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    else:
        import webview
        def run_server():
            uvicorn.run(app, host="127.0.0.1", port=5000, log_level="error")
        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()
        print("[INÍCIO] A aguardar estabilização do servidor local...")
        for _ in range(40):
            try:
                r = httpx.get("http://127.0.0.1:5000/", timeout=1.0)
                if r.status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        webview.create_window("gois.v1 | Office & Command Center", "http://127.0.0.1:5000", width=1060, height=760)
        webview.start()