import os
import json
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
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
        # Cada modelo na Groq tem rate-limit separado; rodar entre eles evita o HTTP 429!
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
        # Modelos 100% gratuitos (:free) para eliminar o erro HTTP 402!
        "models": [
            "deepseek/deepseek-chat-v3-0324:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
        "extra_headers": {
            "HTTP-Referer": "https://gois-api.onrender.com",
            "X-Title": "gois.api Personal Engine",
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
                            await asyncio.sleep(0.4)  # Pausa curta e tenta logo o modelo seguinte
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

    # Fallback non-stream caso todos os streams falhem
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


app = FastAPI(title="gois.api Personal Engine", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/health")
async def home():
    ativos = [p["name"] for p in PROVIDERS if candidate_keys(p)]
    return {
        "status": "online",
        "service": "gois.api",
        "model": "gois.v1",
        "active_providers": ativos,
    }


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
