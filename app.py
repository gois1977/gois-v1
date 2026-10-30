import os
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("uvicorn.error")

BASE_DIR = Path(__file__).resolve().parent

# =====================================================================
# ROTAÇÃO MULTI-MODELO E MULTI-FORNECEDOR (ZERO RATE-LIMITS / ZERO 402)
# =====================================================================

CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()

PROVIDERS = [
    {
        "name": "Groq",
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models": [
            os.environ.get("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile",
            "openai/gpt-oss-120b",
            "llama-3.1-8b-instant",
        ],
        "extra_headers": {},
    },
    {
        "name": "Gemini",
        "env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models": [
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ],
        "extra_headers": {},
    },
    {
        "name": "OpenRouter",
        "env": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models": [
            "deepseek/deepseek-chat-v3-0324:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        "extra_headers": {
            "HTTP-Referer": "https://gois-v1.onrender.com",
            "X-Title": "gois.v1 Personal Engine",
        },
    },
    {
        "name": "Mistral",
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "models": [
            "mistral-small-latest",
            "open-mistral-nemo",
        ],
        "extra_headers": {},
    },
    {
        "name": "Cloudflare",
        "env": "CLOUDFLARE_API_TOKEN",
        "url": f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/v1/chat/completions" if CF_ACCOUNT_ID else "",
        "models": [
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "@cf/meta/llama-3.1-8b-instruct",
        ],
        "extra_headers": {},
    },
]

RUNTIME_KEYS: dict = {}


def candidate_keys(provider: dict) -> list:
    if not provider.get("url"):
        return []
    keys = []
    for k in (RUNTIME_KEYS.get(provider["env"], ""), os.environ.get(provider["env"], "")):
        k = (k or "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def flatten_content(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    return str(content)


def build_provider_request(p: dict, key: str, payload: dict, model_name: str, stream: bool = False):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **p["extra_headers"],
    }
    body = {
        "model": model_name,
        "messages": payload.get("messages", []),
        "temperature": payload.get("temperature", 0.6),
        "stream": stream,
    }
    if payload.get("max_tokens"):
        body["max_tokens"] = int(payload["max_tokens"])
        if p["name"] == "Groq" and str(model_name).startswith("openai/gpt-oss"):
            body["max_tokens"] = int(payload["max_tokens"]) + 2048
    return headers, body


async def make_llm_request(payload: dict):
    errors = []
    timeout = httpx.Timeout(65.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in PROVIDERS:
            keys = candidate_keys(p)
            if not keys:
                continue
            for key in keys:
                for model_name in p["models"]:
                    try:
                        headers, body = build_provider_request(p, key, payload, model_name, stream=False)
                        resp = await client.post(p["url"], json=body, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, dict) and data.get("choices"):
                                data["model"] = f"gois.v1 ({p['name']} · {model_name})"
                                return data, []
                            errors.append(f"{p['name']}/{model_name}: sem choices")
                        elif resp.status_code == 429:
                            errors.append(f"{p['name']}/{model_name}: HTTP 429")
                            await asyncio.sleep(0.4)
                        else:
                            errors.append(f"{p['name']}/{model_name}: HTTP {resp.status_code}")
                    except Exception as e:
                        errors.append(f"{p['name']}/{model_name}: {type(e).__name__}")
    return None, errors


async def stream_llm_request(payload: dict):
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in PROVIDERS:
            keys = candidate_keys(p)
            if not keys:
                continue
            for key in keys:
                for model_name in p["models"]:
                    got_content = False
                    try:
                        headers, body = build_provider_request(p, key, payload, model_name, stream=True)
                        async with client.stream("POST", p["url"], json=body, headers=headers) as resp:
                            if resp.status_code != 200:
                                continue
                            sent_model = False
                            async for line in resp.aiter_lines():
                                line = line.strip()
                                if not line or not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if not data_str:
                                    continue
                                if data_str == "[DONE]":
                                    if got_content:
                                        yield "data: [DONE]\n\n"
                                        return
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                except Exception:
                                    continue
                                if not isinstance(chunk, dict):
                                    continue
                                if not sent_model:
                                    chunk["model"] = f"gois.v1 ({p['name']} · {model_name})"
                                    sent_model = True
                                choices = chunk.get("choices") or []
                                if choices and isinstance(choices[0], dict):
                                    delta = choices[0].get("delta") or {}
                                    if delta.get("content"):
                                        got_content = True
                                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                            if got_content:
                                yield "data: [DONE]\n\n"
                                return
                    except Exception:
                        pass

    data, errors = await make_llm_request(payload)
    if data and data.get("choices"):
        text = data["choices"][0].get("message", {}).get("content", "")
        if text:
            chunk = {
                "model": data.get("model", "gois.v1"),
                "choices": [{"index": 0, "delta": {"content": text}}],
            }
            yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return

    err_msg = " | ".join(errors[-4:]) if errors else "Sem chaves ativas no gois-api (Environment do Render)."
    yield "data: " + json.dumps({"error": {"message": f"Failover gois.api: {err_msg}"}}, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


@asynccontextmanager
async def lifespan(_: FastAPI):
    ativos = [p["name"] for p in PROVIDERS if candidate_keys(p)]
    logger.info(f"[gois.api] Fornecedores ativos no arranque: {ativos}")
    yield


app = FastAPI(title="gois.v1 Professional Workspace", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Rota Raiz (/): Interface Profissional com Sidebar, Histórico, Novo Chat e Apagar Conversas
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
        /* Sidebar */
        #sidebar {
            width: 280px;
            background-color: var(--bg-sidebar);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            transition: transform 0.3s ease;
        }
        .sidebar-header {
            padding: 16px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
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
            transition: background 0.2s;
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
            transition: background 0.15s;
            text-overflow: ellipsis;
            white-space: nowrap;
            overflow: hidden;
        }
        .history-item:hover { background-color: var(--bg-panel); }
        .history-item.active { background-color: var(--bg-panel); border: 1px solid var(--border); }
        .history-title { flex: 1; overflow: hidden; text-overflow: ellipsis; }
        .delete-chat-btn {
            background: none;
            border: none;
            color: var(--muted);
            cursor: pointer;
            padding: 4px;
            border-radius: 4px;
            display: none;
        }
        .history-item:hover .delete-chat-btn { display: block; }
        .delete-chat-btn:hover { color: var(--danger); background-color: rgba(218, 54, 51, 0.1); }

        /* Main Workspace */
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
        .message-wrapper {
            max-width: 850px;
            width: 100%;
            margin: 0 auto;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .message {
            padding: 14px 18px;
            border-radius: 12px;
            line-height: 1.6;
            word-wrap: break-word;
            white-space: pre-wrap;
            font-size: 0.95rem;
        }
        .message.user {
            background-color: var(--user-bubble);
            color: white;
            align-self: flex-end;
            border-bottom-right-radius: 4px;
            max-width: 75%;
        }
        .message.bot {
            background-color: var(--bot-bubble);
            border: 1px solid var(--border);
            color: var(--fg);
            align-self: flex-start;
            border-bottom-left-radius: 4px;
            width: 100%;
        }
        .sender-label { font-size: 0.75rem; color: var(--muted); margin-bottom: 2px; font-weight: 600; }

        /* Input Area */
        #input-panel {
            position: absolute;
            bottom: 0;
            left: 0;
            width: 100%;
            background: linear-gradient(transparent, var(--bg-main) 30%);
            padding: 20px;
            display: flex;
            justify-content: center;
            box-sizing: border-box;
        }
        .input-box-wrapper {
            max-width: 850px;
            width: 100%;
            background-color: var(--bg-sidebar);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 10px 14px;
            display: flex;
            align-items: flex-end;
            gap: 10px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }
        textarea {
            flex: 1;
            background: transparent;
            color: var(--fg);
            border: none;
            resize: none;
            height: 24px;
            max-height: 150px;
            font-family: inherit;
            font-size: 0.95rem;
            outline: none;
            padding: 4px 0;
        }
        .send-btn {
            background-color: var(--accent);
            color: white;
            border: none;
            border-radius: 8px;
            width: 36px;
            height: 36px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: background 0.2s;
            flex-shrink: 0;
        }
        .send-btn:hover { background-color: var(--accent-hover); }
        .send-btn svg { width: 18px; height: 18px; fill: currentColor; }
    </style>
</head>
<body>

    <!-- Sidebar de Histórico -->
    <div id="sidebar">
        <div class="sidebar-header">
            <button class="new-chat-btn" onclick="createNewChat()">
                <span>+</span> Nova Conversa
            </button>
        </div>
        <div class="history-list" id="historyList">
            <!-- Os chats anteriores aparecem aqui dinamicamente -->
        </div>
    </div>

    <!-- Área Principal de Chat -->
    <div id="main-content">
        <div class="top-nav">
            <span>🟢 gois.v1 | Workspace Autónomo</span>
            <span id="activeChatTitle" style="color: var(--muted); font-size: 0.85rem;">Nova Conversa</span>
        </div>

        <div id="chat-container">
            <div class="message-wrapper">
                <div class="sender-label">gois.v1</div>
                <div class="message bot">Olá! O teu workspace profissional está pronto. Como posso ajudar na tua engenharia hoje?</div>
            </div>
        </div>

        <div id="input-panel">
            <div class="input-box-wrapper">
                <textarea id="userInput" placeholder="Escreve uma mensagem..." rows="1" oninput="autoResize(this)"></textarea>
                <button class="send-btn" onclick="sendMessage()">
                    <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
                </button>
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
            if (chats.length === 0) {
                createNewChat();
            } else {
                if (!currentChatId || !chats.find(c => c.id === currentChatId)) {
                    currentChatId = chats[0].id;
                }
                renderHistory();
                loadCurrentChat();
            }
        }

        function createNewChat() {
            const newChat = {
                id: 'chat_' + Date.now(),
                title: 'Nova Conversa',
                messages: []
            };
            chats.unshift(newChat);
            currentChatId = newChat.id;
            saveState();
            renderHistory();
            loadCurrentChat();
        }

        function deleteChat(id, event) {
            event.stopPropagation();
            chats = chats.filter(c => c.id !== id);
            if (chats.length === 0) {
                createNewChat();
            } else if (currentChatId === id) {
                currentChatId = chats[0].id;
                loadCurrentChat();
            }
            saveState();
            renderHistory();
        }

        function switchChat(id) {
            currentChatId = id;
            saveState();
            renderHistory();
            loadCurrentChat();
        }

        function renderHistory() {
            const listEl = document.getElementById('historyList');
            listEl.innerHTML = '';
            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `history-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.onclick = () => switchChat(chat.id);
                item.innerHTML = `
                    <div class="history-title">${escapeHtml(chat.title)}</div>
                    <button class="delete-chat-btn" onclick="deleteChat('${chat.id}', event)" title="Apagar conversa">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                    </button>
                `;
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
                appendMessageUI('gois.v1', 'Olá! O teu workspace profissional está pronto. Como posso ajudar na tua engenharia hoje?', 'bot', false);
            } else {
                chat.messages.forEach(m => appendMessageUI(m.role === 'user' ? 'Tu' : 'gois.v1', m.content, m.role === 'user' ? 'user' : 'bot', false));
            }
            scrollToBottom();
        }

        function appendMessageUI(sender, text, type, save = true) {
            const container = document.getElementById('chat-container');
            const wrapper = document.createElement('div');
            wrapper.className = 'message-wrapper';

            const label = document.createElement('div');
            label.className = 'sender-label';
            label.textContent = sender;

            const msg = document.createElement('div');
            msg.className = `message ${type}`;
            msg.textContent = text;

            wrapper.appendChild(label);
            wrapper.appendChild(msg);
            container.appendChild(wrapper);
            scrollToBottom();
            return msg;
        }

        async function sendMessage() {
            const textarea = document.getElementById('userInput');
            const text = textarea.value.trim();
            if (!text) return;

            textarea.value = '';
            textarea.style.height = '24px';

            const chat = chats.find(c => c.id === currentChatId);
            if (chat.messages.length === 0) {
                chat.title = text.length > 28 ? text.substring(0, 28) + '...' : text;
                renderHistory();
            }

            chat.messages.push({ role: 'user', content: text });
            appendMessageUI('Tu', text, 'user');
            saveState();

            const botMsgEl = appendMessageUI('gois.v1', '...', 'bot');
            let fullReply = "";

            try {
                const response = await fetch('/v1/chat/completions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        messages: chat.messages.map(m => ({ role: m.role, content: m.content })),
                        stream: true
                    })
                });

                if (!response.ok) throw new Error("Erro de comunicação com o motor");

                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value, { stream: true });
                    const lines = chunk.split('\\n');

                    for (const line of lines) {
                        if (line.startsWith('data:')) {
                            const dataStr = line.slice(5).trim();
                            if (dataStr === '[DONE]') break;
                            try {
                                const parsed = JSON.parse(dataStr);
                                const delta = parsed.choices?.[0]?.delta?.content;
                                if (delta) {
                                    fullReply += delta;
                                    botMsgEl.textContent = fullReply;
                                    scrollToBottom();
                                }
                            } catch (e) {}
                        }
                    }
                }
                chat.messages.push({ role: 'assistant', content: fullReply });
                saveState();
            } catch (err) {
                botMsgEl.textContent = "Erro de failover no backend: " + err.message;
            }
        }

        function scrollToBottom() {
            const container = document.getElementById('chat-container');
            container.scrollTop = container.scrollHeight;
        }

        function autoResize(textarea) {
            textarea.style.height = '24px';
            textarea.style.height = (textarea.scrollHeight) + 'px';
        }

        function escapeHtml(str) {
            return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        document.getElementById('userInput').addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        window.onload = initApp;
    </script>
</body>
</html>
    """


@app.get("/health")
async def health():
    ativos = [p["name"] for p in PROVIDERS if candidate_keys(p)]
    return {"status": "online", "service": "gois.api", "active_providers": ativos}


@app.head("/")
async def home_head():
    return {"status": "online"}


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corpo inválido.")

    messages = body.get("messages", []) or []
    formatted = []
    for m in messages:
        if isinstance(m, dict):
            role = m.get("role", "user")
            content = flatten_content(m.get("content"))
            if role and content:
                formatted.append({"role": role, "content": content})

    if not formatted:
        formatted = [{"role": "user", "content": "Olá"}]

    payload = {"messages": formatted}
    if isinstance(body.get("temperature"), (int, float)):
        payload["temperature"] = body["temperature"]
    if isinstance(body.get("max_tokens"), int):
        payload["max_tokens"] = body["max_tokens"]

    if body.get("stream"):
        return StreamingResponse(
            stream_llm_request(payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    data, errors = await make_llm_request(payload)
    if data is not None:
        return JSONResponse(content=data)

    raise HTTPException(status_code=502, detail="Erro Failover gois.api: " + " | ".join(errors))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
