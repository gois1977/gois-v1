import os
import time
import json
import asyncio
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

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

# --- FUNÇÃO UNIVERSAL COM FAILOVER ROBUSTO ENTRE MÚLTIPLOS FORNECEDORES ---
async def make_llm_request(payload: dict):
    groq_key = os.environ.get("GROQ_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    mistral_key = os.environ.get("MISTRAL_API_KEY", "")
    
    async with httpx.AsyncClient(timeout=35.0) as client:
        
        # 1. TENTATIVA: OpenRouter (DeepSeek)
        if openrouter_key:
            try:
                headers = {
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://gois-v1.onrender.com",
                    "X-Title": "gois.v1 Command Center"
                }
                or_payload = {
                    "model": "deepseek/deepseek-chat",
                    "messages": payload.get("messages", []),
                    "temperature": 0.7
                }
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions", json=or_payload, headers=headers)
                if resp.status_code == 200:
                    return resp
            except Exception:
                pass

        # 2. TENTATIVA: Groq (Llama 3.3)
        if groq_key:
            try:
                headers = {
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json"
                }
                groq_payload = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": payload.get("messages", []),
                    "temperature": 0.7
                }
                resp = await client.post("https://api.groq.com/openai/v1/chat/completions", json=groq_payload, headers=headers)
                if resp.status_code == 200:
                    return resp
            except Exception:
                pass

        # 3. TENTATIVA: Mistral
        if mistral_key:
            try:
                headers = {
                    "Authorization": f"Bearer {mistral_key}",
                    "Content-Type": "application/json"
                }
                mistral_payload = {
                    "model": "mistral-small-latest",
                    "messages": payload.get("messages", []),
                    "temperature": 0.7
                }
                resp = await client.post("https://api.mistral.ai/v1/chat/completions", json=mistral_payload, headers=headers)
                if resp.status_code == 200:
                    return resp
            except Exception:
                pass

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
        detail="Erro de Failover: Nenhum dos fornecedores de IA (OpenRouter, Groq, Mistral) respondeu com sucesso.",
    )

@app.get("/api/settings")
async def get_settings():
    return SERVER_SETTINGS

@app.post("/api/settings")
async def update_settings(request: Request):
    global SERVER_SETTINGS
    body = await request.json()
    SERVER_SETTINGS.update(body)
    return {"status": "success", "settings": SERVER_SETTINGS}

@app.get("/api/knowledge")
async def get_knowledge():
    return KNOWLEDGE_DB

@app.get("/health")
async def health_check():
    return {"status": "online", "system": "gois.v1", "classroom_mode": SERVER_SETTINGS["classroom_mode"]}

@app.get("/")
async def root():
    return {"message": "gois.v1 Command Center Backend Online", "docs": "/docs"}