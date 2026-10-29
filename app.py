import os
import time
import json
import asyncio
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# 1. INICIALIZAÇÃO DA APLICAÇÃO FASTAPI (TEM DE ESTAR NO TOPO ANTES DE QUALQUER ROTA)
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

# --- FUNÇÃO UNIVERSAL DE PEDIDOS À LLM (COM FAILOVER INTELIGENTE) ---
async def make_llm_request(payload: dict):
    # Obter chaves do ambiente
    groq_key = os.environ.get("GROQ_API_KEY", "")
    mistral_key = os.environ.get("MISTRAL_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    
    # Tentativa 1: Groq (se chave disponível)
    if groq_key:
        headers = {
            "Authorization": f"Bearer {groq_key}",
            "Content-Type": "application/json"
        }
        groq_payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": payload.get("messages", []),
            "temperature": 0.7
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post("https://api.groq.com/openai/v1/chat/completions", json=groq_payload, headers=headers)
                if resp.status_code == 200:
                    return resp
            except Exception:
                pass

    # Tentativa 2: OpenRouter (se chave disponível)
    if openrouter_key:
        headers = {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json"
        }
        or_payload = {
            "model": "deepseek/deepseek-chat",
            "messages": payload.get("messages", []),
            "temperature": 0.7
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post("https://openrouter.ai/api/v1/chat/completions", json=or_payload, headers=headers)
                if resp.status_code == 200:
                    return resp
            except Exception:
                pass

    # Tentativa 3: Mistral (se chave disponível)
    if mistral_key:
        headers = {
            "Authorization": f"Bearer {mistral_key}",
            "Content-Type": "application/json"
        }
        mistral_payload = {
            "model": "mistral-small-latest",
            "messages": payload.get("messages", []),
            "temperature": 0.7
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
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
    
    # Injetar o estado atual da Sala de Aula e o Tópico Ativo no contexto do sistema
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
        detail="Erro: Nenhuma chave API ativa ou válida respondeu. Vê os logs do Render para saber o motivo.",
    )

@app.get("/health")
async def health_check():
    return {"status": "online", "system": "gois.v1", "classroom_mode": SERVER_SETTINGS["classroom_mode"]}

@app.get("/")
async def root():
    return {"message": "gois.v1 Command Center Backend Online", "docs": "/docs"}