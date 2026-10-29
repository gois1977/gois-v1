import os
import time
import json
import asyncio
import logging
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("uvicorn.error")

# 1. INICIALIZAÇÃO DA APLICAÇÃO FASTAPI
app = FastAPI(title="gois.v1 Command Center", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- BASES DE DADOS E ESTADO EM MEMÓRIA ---
SERVER_SETTINGS = {
    "classroom_mode": True,
    "current_topic": "Em espera...",
    "active_categories": [
        "Tecnologia & Software",
        "Game Design & Unity",
        "Engenharia & AutoCAD",
        "Inteligência Artificial",
        "Ciências & Exatas",
        "Humanidades & Filosofia",
        "DevOps & Ferramentas"
    ]
}

SKILLS_DB = []
KNOWLEDGE_DB = []

GOIS_SYSTEM_PROMPT = """
És o gois.v1, um núcleo de IA privado, avançado e autónomo que funciona como um escritório completo de engenharia e desenvolvimento.
Ajudas o utilizador com arquiteturas de software assíncronas, game design, Unity C#, automação, DevOps e otimização para hardware leve.
Mantém uma postura altamente técnica, precisa, prestativa e estruturada.
"""

# --- LISTA DE FORNECEDORES (ordem de tentativa) ---
PROVIDERS = [
    {
        "name": "OpenRouter",
        "env": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "deepseek/deepseek-chat",
        "extra_headers": {
            "HTTP-Referer": "https://gois-v1.onrender.com",
            "X-Title": "gois.v1 Command Center",
        },
    },
    {
        "name": "Groq",
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "extra_headers": {},
    },
    {
        "name": "Mistral",
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-small-latest",
        "extra_headers": {},
    },
]


@app.on_event("startup")
async def check_keys_on_startup():
    for p in PROVIDERS:
        if os.environ.get(p["env"], "").strip():
            logger.info(f"[CHAVES] {p['name']}: {p['env']} encontrada")
        else:
            logger.warning(f"[CHAVES] {p['name']}: {p['env']} NÃO definida no Environment do Render")


# --- FUNÇÃO UNIVERSAL COM FAILOVER ROBUSTO ENTRE MÚLTIPLOS FORNECEDORES ---
async def make_llm_request(payload: dict):
    async with httpx.AsyncClient(timeout=35.0) as client:
        for p in PROVIDERS:
            key = os.environ.get(p["env"], "").strip()
            if not key:
                logger.warning(f"{p['name']}: variável {p['env']} não definida, a saltar")
                continue
            try:
                headers = {
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    **p["extra_headers"],
                }
                body = {
                    "model": p["model"],
                    "messages": payload.get("messages", []),
                    "temperature": 0.7,
                }
                resp = await client.post(p["url"], json=body, headers=headers)
                if resp.status_code == 200:
                    logger.info(f"{p['name']}: resposta OK")
                    return resp
                logger.error(f"{p['name']}: HTTP {resp.status_code} - {resp.text[:300]}")
            except Exception as e:
                logger.error(f"{p['name']}: exceção {e!r}")

    return None


# --- ROTAS DA API ---

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    body = await request.json()
    messages = body.get("messages", []) or []

    current_topic = SERVER_SETTINGS.get("current_topic", "Em espera...")
    classroom_active = SERVER_SETTINGS.get("classroom_mode", False)
    status_classroom = "Ativo e a aprender" if classroom_active else "Desativado"

    system_parts = [
        GOIS_SYSTEM_PROMPT,
        f"[ESTADO DA SALA DE AULA AUTÓNOMA]: Modo: {status_classroom} | Tópico Atual / Em Estudo: {current_topic}"
    ]

    if SKILLS_DB:
        blocks = ["[DIRECTRIZES E SKILLS DO NÚCLEO]:"]
        for s in SKILLS_DB:
            blocks.append(f"- [{s.get('type','SKILL')}] {s.get('title','')}: {s.get('content','')}")
        system_parts.append("\n".join(blocks))

    if KNOWLEDGE_DB:
        blocks = ["[REGISTOS DA SALA DE AULA & APRENDIZAGEM RECENTE]:"]
        for k in KNOWLEDGE_DB[:10]:
            content = (k.get("content") or "")[:300]
            blocks.append(f"- [{k.get('category','Geral')}] {k.get('title','')}: {content}...")
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

    raise HTTPException(
        status_code=502,
        detail="Erro de Failover: Nenhum dos fornecedores de IA respondeu com sucesso. Vê os logs do Render.",
    )


@app.get("/api/settings")
async def get_settings():
    return SERVER_SETTINGS


@app.post("/api/settings")
@app.post("/api/classroom/toggle")
@app.post("/api/settings/classroom")
async def update_settings(request: Request):
    global SERVER_SETTINGS
    try:
        body = await request.json()
    except Exception:
        body = {}

    if "classroom_mode" in body:
        SERVER_SETTINGS["classroom_mode"] = bool(body["classroom_mode"])
    if "current_topic" in body:
        SERVER_SETTINGS["current_topic"] = str(body["current_topic"])
    SERVER_SETTINGS.update(body)

    return {
        "status": "success",
        "settings": SERVER_SETTINGS,
        "classroom_mode": SERVER_SETTINGS["classroom_mode"],
        "current_topic": SERVER_SETTINGS["current_topic"],
    }


@app.get("/api/knowledge")
async def get_knowledge():
    return KNOWLEDGE_DB


@app.get("/health")
async def health_check():
    return {"status": "online", "system": "gois.v1", "classroom_mode": SERVER_SETTINGS["classroom_mode"]}


@app.get("/")
async def root():
    return {"message": "gois.v1 Command Center Backend Online", "docs": "/docs"}
