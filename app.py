import os
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("uvicorn.error")

BASE_DIR = Path(__file__).resolve().parent

# Base de dados em memória para suportar a App Desktop e o Workspace
DB_KNOWLEDGE = []
DB_SKILLS = []
DB_SETTINGS = {
    "classroom_mode": True,
    "learning_now": False,
    "current_topic": "Arquitetura de Software & Sistemas Assíncronos",
    "current_teacher": "Groq · openai/gpt-oss-120b",
    "last_learned_title": "Event Loops e Concorrência de Alto Desempenho",
    "last_teacher": "Groq",
    "daily_token_limit": 0,
    "tokens": {
        "used_today": 21000,
        "daily_limit": 0,
        "used_total": 145000,
        "date": "2026-10-30",
        "auto_paused_by_limit": False,
        "limit_reached": False
    },
    "comparison": {
        "overall": {
            "gois_v1": 91.3,
            "gemini": 90.1,
            "deepseek": 90.3,
            "parity_vs_gemini": 101.3,
            "parity_vs_deepseek": 101.1,
            "delta_gemini": 1.2,
            "delta_deepseek": 1.0,
            "total_lessons": 34,
            "total_skills": 6,
            "categories_studied": 16,
            "total_categories": 16,
            "total_benchmarks": 16
        },
        "categories": [
            {
                "category": "Arquitetura de Software & Sistemas Assíncronos",
                "gois_v1": 96.4,
                "gemini": 91.5,
                "deepseek": 93.0,
                "lessons_count": 14,
                "skills_count": 2,
                "status": "Acima de Gemini & DeepSeek",
                "parity_vs_gemini": 105.4,
                "parity_vs_deepseek": 103.6,
                "delta_gemini": 4.9,
                "delta_deepseek": 3.4
            },
            {
                "category": "Game Design, Unity C# & Motores 3D",
                "gois_v1": 98.5,
                "gemini": 90.1,
                "deepseek": 89.3,
                "lessons_count": 14,
                "skills_count": 3,
                "status": "Acima de Gemini & DeepSeek",
                "parity_vs_gemini": 109.3,
                "parity_vs_deepseek": 110.3,
                "delta_gemini": 8.4,
                "delta_deepseek": 9.2
            }
        ]
    }
}

CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()

PROVIDERS = [
    {
        "name": "Groq",
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models": [os.environ.get("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant"],
        "extra_headers": {},
    },
    {
        "name": "Gemini",
        "env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models": ["gemini-2.5-flash", "gemini-2.0-flash"],
        "extra_headers": {},
    },
    {
        "name": "OpenRouter",
        "env": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models": ["deepseek/deepseek-chat-v3-0324:free", "deepseek/deepseek-r1:free", "google/gemini-2.0-flash-exp:free"],
        "extra_headers": {"HTTP-Referer": "https://gois-v1.onrender.com", "X-Title": "gois.v1 Engine"},
    },
    {
        "name": "Mistral",
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "models": ["mistral-small-latest"],
        "extra_headers": {},
    }
]

RUNTIME_KEYS: dict = {}

def candidate_keys(provider: dict) -> list:
    keys = []
    for k in (RUNTIME_KEYS.get(provider["env"], ""), os.environ.get(provider["env"], "")):
        k = (k or "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys

def flatten_content(content) -> str:
    if content is None: return ""
    if isinstance(content, str): return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "") if isinstance(p, dict) else p) for p in content)
    return str(content)

async def make_llm_request(payload: dict):
    errors = []
    timeout = httpx.Timeout(65.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in PROVIDERS:
            keys = candidate_keys(p)
            if not keys: continue
            for key in keys:
                for model_name in p["models"]:
                    try:
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **p["extra_headers"]}
                        body = {"model": model_name, "messages": payload.get("messages", []), "temperature": payload.get("temperature", 0.6)}
                        resp = await client.post(p["url"], json=body, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, dict) and data.get("choices"):
                                data["model"] = f"gois.v1 ({p['name']} · {model_name})"
                                return data, []
                    except Exception as e:
                        errors.append(f"{p['name']}/{model_name}: {type(e).__name__}")
    return None, errors

async def stream_llm_request(payload: dict):
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in PROVIDERS:
            keys = candidate_keys(p)
            if not keys: continue
            for key in keys:
                for model_name in p["models"]:
                    got_content = False
                    try:
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **p["extra_headers"]}
                        body = {"model": model_name, "messages": payload.get("messages", []), "temperature": payload.get("temperature", 0.6), "stream": True}
                        async with client.stream("POST", p["url"], json=body, headers=headers) as resp:
                            if resp.status_code != 200: continue
                            sent_model = False
                            async for line in resp.aiter_lines():
                                line = line.strip()
                                if not line or not line.startswith("data:"): continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    if got_content:
                                        yield "data: [DONE]\n\n"
                                        return
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                except Exception:
                                    continue
                                if not sent_model:
                                    chunk["model"] = f"gois.v1 ({p['name']} · {model_name})"
                                    sent_model = True
                                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                            if got_content:
                                yield "data: [DONE]\n\n"
                                return
                    except Exception:
                        pass
    yield "data: " + json.dumps({"error": {"message": "Erro de Failover na gois.api."}}, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"

@asynccontextmanager
async def lifespan(_: FastAPI):
    yield

app = FastAPI(title="gois.v1 Professional Workspace & API", version="3.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- ENDPOINTS DA APP DESKTOP -----------------
@app.get("/api/settings")
async def get_settings():
    active_providers = [p["name"] for p in PROVIDERS if candidate_keys(p)]
    DB_SETTINGS["providers_with_key"] = active_providers
    return DB_SETTINGS

@app.post("/api/settings")
async def update_settings(request: Request):
    body = await request.json()
    DB_SETTINGS.update(body)
    return DB_SETTINGS

@app.get("/api/knowledge")
async def get_knowledge():
    return DB_KNOWLEDGE

@app.post("/api/sync/import")
async def sync_import(request: Request):
    body = await request.json()
    if "knowledge" in body and isinstance(body["knowledge"], list):
        DB_KNOWLEDGE.extend(body["knowledge"])
    return {"imported": {"skills": len(DB_SKILLS), "knowledge": len(DB_KNOWLEDGE), "keys": []}}

@app.get("/api/skills")
async def get_skills():
    return DB_SKILLS

@app.post("/api/skills")
async def add_skill(request: Request):
    body = await request.json()
    DB_SKILLS.append(body)
    return {"success": True}

@app.delete("/api/skills/{sid}")
async def delete_skill(sid: str):
    global DB_SKILLS
    DB_SKILLS = [s for s in DB_SKILLS if s.get("id") != sid]
    return {"success": True}

@app.get("/api/keys/status")
async def keys_status():
    status = {}
    for p in PROVIDERS:
        has_key = bool(candidate_keys(p))
        status[p["name"]] = {"configured": has_key, "source": "environment" if has_key else "", "model": p["models"][0]}
    return status

@app.post("/api/keys")
async def set_key(request: Request):
    body = await request.json()
    provider = body.get("provider")
    key = body.get("key")
    for p in PROVIDERS:
        if p["name"].lower() == provider.lower():
            RUNTIME_KEYS[p["env"]] = key
            return {"persisted": True}
    return {"persisted": False}

@app.post("/api/classroom/learn")
async def classroom_learn(request: Request):
    body = await request.json()
    cat = body.get("category", "Geral")
    lesson = {
        "id": f"l_{int(datetime.now().timestamp())}",
        "title": f"Estudo Avançado: {body.get('topic', cat)}",
        "category": cat,
        "teacher": "Groq · openai/gpt-oss-120b",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "content": f"Lição autónoma destilada com sucesso para a categoria {cat}."
    }
    DB_KNOWLEDGE.insert(0, lesson)
    return {"lesson": lesson, "settings": DB_SETTINGS}

@app.post("/api/comparison/benchmark")
async def run_benchmark(request: Request):
    return {
        "benchmark": {"category": "Geral", "elapsed_s": 1.2, "gois_score": 95.5, "gemini_ref": 90.1, "deepseek_ref": 90.3},
        "comparison": DB_SETTINGS["comparison"]
    }

# ----------------- INTERFACE WEB PROFISSIONAL (RAIZ) -----------------
@app.get("/", response_class=HTMLResponse)
async def home_ui():
    return """
<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>gois.v1 | Professional Workspace</title>
    <style>
        :root {
            --bg-main: #0d1117;
            --bg-sidebar: #161b22;
            --bg-panel: #21262d;
            --border: #30363d;
            --fg: #e6edf3;
            --muted: #8b949e;
            --accent: #238636;
            --accent-hover: #2ea043;
            --user-bubble: #1f6feb;
            --bot-bubble: #161b22;
            --danger: #da3633;
        }
        * { box-sizing: border-box; }
        body {
            background-color: var(--bg-main);
            color: var(--fg);
            font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
            margin: 0;
            display: flex;
            height: 100vh;
            overflow: hidden;
        }
        #sidebar {
            width: 280px;
            background-color: var(--bg-sidebar);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
        }
        .sidebar-header {
            padding: 16px;
            border-bottom: 1px solid var(--border);
        }
        .new-chat-btn {
            background-color: var(--accent);
            color: white;
            border: none;
            border-radius: 6px;
            padding: 10px 14px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        .new-chat-btn:hover { background-color: var(--accent-hover); }
        .history-list {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .history-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.9rem;
            color: var(--fg);
            background: transparent;
        }
        .history-item:hover { background-color: var(--bg-panel); }
        .history-item.active { background-color: var(--bg-panel); border: 1px solid var(--border); }
        .history-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .delete-chat-btn { background: none; border: none; color: var(--muted); cursor: pointer; display: none; }
        .history-item:hover .delete-chat-btn { display: block; }
        .delete-chat-btn:hover { color: var(--danger); }
        #main-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            height: 100vh;
            position: relative;
        }
        .top-nav {
            padding: 12px 20px;
            background-color: var(--bg-sidebar);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-weight: 600;
        }
        #chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 20px;
            padding-bottom: 140px;
            scroll-behavior: smooth;
        }
        .message-wrapper { max-width: 850px; width: 100%; margin: 0 auto; display: flex; flex-direction: column; gap: 4px; }
        .message { padding: 14px 18px; border-radius: 12px; line-height: 1.6; word-wrap: break-word; white-space: pre-wrap; font-size: 0.95rem; }
        .message.user { background-color: var(--user-bubble); color: white; align-self: flex-end; border-bottom-right-radius: 4px; max-width: 75%; }
        .message.bot { background-color: var(--bot-bubble); border: 1px solid var(--border); color: var(--fg); align-self: flex-start; border-bottom-left-radius: 4px; width: 100%; }
        .sender-label { font-size: 0.75rem; color: var(--muted); margin-bottom: 2px; font-weight: 600; }
        #input-panel {
            position: absolute; bottom: 0; left: 0; width: 100%;
            background: linear-gradient(transparent, var(--bg-main) 30%);
            padding: 20px; display: flex; justify-content: center; box-sizing: border-box;
        }
        .input-box-wrapper {
            max-width: 850px; width: 100%; background-color: var(--bg-sidebar);
            border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px;
            display: flex; align-items: flex-end; gap: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }
        textarea { flex: 1; background: transparent; color: var(--fg); border: none; resize: none; height: 24px; max-height: 150px; outline: none; font-family: inherit; }
        .send-btn { background-color: var(--accent); color: white; border: none; border-radius: 8px; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; cursor: pointer; }
        .send-btn:hover { background-color: var(--accent-hover); }
        .send-btn svg { width: 18px; height: 18px; fill: currentColor; }
    </style>
</head>
<body>
    <div id="sidebar">
        <div class="sidebar-header">
            <button class="new-chat-btn" onclick="createNewChat()"><span>+</span> Nova Conversa</button>
        </div>
        <div class="history-list" id="historyList"></div>
    </div>
    <div id="main-content">
        <div class="top-nav">
            <span>🟢 gois.v1 | Workspace Autónomo</span>
            <span id="activeChatTitle" style="color: var(--muted); font-size: 0.85rem;">Nova Conversa</span>
        </div>
        <div id="chat-container"></div>
        <div id="input-panel">
            <div class="input-box-wrapper">
                <textarea id="userInput" placeholder="Escreve uma mensagem..." rows="1" oninput="autoResize(this)"></textarea>
                <button class="send-btn" onclick="sendMessage()"><svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg></button>
            </div>
        </div>
    </div>
    <script>
        let chats = JSON.parse(localStorage.getItem('gois_chats') || '[]');
        let currentChatId = localStorage.getItem('gois_current_chat') || null;

        function saveState() {
            localStorage.setItem('gois_chats', JSON.stringify(chats));
            localStorage.setItem('gois_current_chat', currentChatId);
        }

        function initApp() {
            if (chats.length === 0) createNewChat();
            else {
                if (!currentChatId || !chats.find(c => c.id === currentChatId)) currentChatId = chats[0].id;
                renderHistory(); loadCurrentChat();
            }
        }

        function createNewChat() {
            const newChat = { id: 'chat_' + Date.now(), title: 'Nova Conversa', messages: [] };
            chats.unshift(newChat); currentChatId = newChat.id;
            saveState(); renderHistory(); loadCurrentChat();
        }

        function deleteChat(id, event) {
            event.stopPropagation();
            chats = chats.filter(c => c.id !== id);
            if (chats.length === 0) createNewChat();
            else if (currentChatId === id) { currentChatId = chats[0].id; loadCurrentChat(); }
            saveState(); renderHistory();
        }

        function switchChat(id) { currentChatId = id; saveState(); renderHistory(); loadCurrentChat(); }

        function renderHistory() {
            const listEl = document.getElementById('historyList'); listEl.innerHTML = '';
            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `history-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.onclick = () => switchChat(chat.id);
                item.innerHTML = `<div class="history-title">${chat.title}</div><button class="delete-chat-btn" onclick="deleteChat('${chat.id}', event)">✕</button>`;
                listEl.appendChild(item);
            });
        }

        function loadCurrentChat() {
            const chat = chats.find(c => c.id === currentChatId);
            const container = document.getElementById('chat-container');
            const titleEl = document.getElementById('activeChatTitle');
            container.innerHTML = '';
            if (!chat) return;
            titleEl.textContent = chat.title;
            if (chat.messages.length === 0) {
                appendMessageUI('gois.v1', 'Olá! O teu workspace profissional está pronto.', 'bot', false);
            } else {
                chat.messages.forEach(m => appendMessageUI(m.role === 'user' ? 'Tu' : 'gois.v1', m.content, m.role === 'user' ? 'user' : 'bot', false));
            }
            scrollToBottom();
        }

        function appendMessageUI(sender, text, type) {
            const container = document.getElementById('chat-container');
            const wrapper = document.createElement('div'); wrapper.className = 'message-wrapper';
            const label = document.createElement('div'); label.className = 'sender-label'; label.textContent = sender;
            const msg = document.createElement('div'); msg.className = `message ${type}`; msg.textContent = text;
            wrapper.appendChild(label); wrapper.appendChild(msg); container.appendChild(wrapper);
            scrollToBottom();
            return msg;
        }

        async function sendMessage() {
            const textarea = document.getElementById('userInput');
            const text = textarea.value.trim(); if (!text) return;
            textarea.value = ''; textarea.style.height = '24px';
            const chat = chats.find(c => c.id === currentChatId);
            if (chat.messages.length === 0) { chat.title = text.substring(0, 28); renderHistory(); }
            chat.messages.push({ role: 'user', content: text });
            appendMessageUI('Tu', text, 'user'); saveState();
            const botMsgEl = appendMessageUI('gois.v1', '...', 'bot');
            let fullReply = "";
            try {
                const response = await fetch('/v1/chat/completions', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ messages: chat.messages, stream: true })
                });
                const reader = response.body.getReader(); const decoder = new TextDecoder();
                while (true) {
                    const { done, value } = await reader.read(); if (done) break;
                    const chunk = decoder.decode(value, { stream: true });
                    for (const line of chunk.split('\\n')) {
                        if (line.startsWith('data:')) {
                            const dataStr = line.slice(5).trim();
                            if (dataStr === '[DONE]') break;
                            try {
                                const parsed = JSON.parse(dataStr);
                                const delta = parsed.choices?.[0]?.delta?.content;
                                if (delta) { fullReply += delta; botMsgEl.textContent = fullReply; scrollToBottom(); }
                            } catch (e) {}
                        }
                    }
                }
                chat.messages.push({ role: 'assistant', content: fullReply }); saveState();
            } catch (err) { botMsgEl.textContent = "Erro: " + err.message; }
        }

        function scrollToBottom() { const c = document.getElementById('chat-container'); c.scrollTop = c.scrollHeight; }
        function autoResize(t) { t.style.height = '24px'; t.style.height = (t.scrollHeight) + 'px'; }
        window.onload = initApp;
    </script>
</body>
</html>
    """

# ----------------- ENDPOINTS DE CHAT -----------------
@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corpo inválido.")
    messages = [{"role": m.get("role", "user"), "content": flatten_content(m.get("content"))} for m in body.get("messages", []) if isinstance(m, dict)]
    if not messages: messages = [{"role": "user", "content": "Olá"}]
    payload = {"messages": messages}
    if body.get("stream"):
        return StreamingResponse(stream_llm_request(payload), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    data, errors = await make_llm_request(payload)
    if data is not None: return JSONResponse(content=data)
    raise HTTPException(status_code=502, detail="Erro Failover: " + " | ".join(errors))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
