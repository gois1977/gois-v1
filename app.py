"""gois.v1 API 6.2 - chat separado por papel, Sala de Aula, benchmark (gois.v1 vs Mistral e Groq), skills e persistência.

ROTEAMENTO (novo na 6.1):
  Admin  (cabeçalho X-Admin-Token válido) -> SÓ o teu modelo (LM Studio via túnel). Sem Groq/Gemini/OpenRouter/Mistral.
                                              Se o modelo estiver desligado, o chat responde "offline".
  Público (sem token)                      -> SÓ fornecedores (Groq, Gemini, OpenRouter, Mistral). Nunca toca no teu PC.
  Sala de Aula / benchmark (professores)   -> fornecedores.

COMPARAÇÃO (novo na 6.2): o gois.v1 é comparado com Mistral e Groq (campos mistral_ref / groq_ref).
  As notas antigas de Gemini / DeepSeek (gemini_ref / deepseek_ref) continuam guardadas, mas já não são medidas.

Variáveis de ambiente (Render > Environment):
  GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, MISTRAL_API_KEY   (pelo menos uma)
  DATA_DIR           (opcional) pasta onde guardar gois_db.json

Modelo próprio (LM Studio / qualquer servidor compatível com a API OpenAI):
  GOIS_LOCAL_URL         URL base do teu servidor, ex.: https://xxxx.trycloudflare.com/v1
  GOIS_LOCAL_MODEL       nome do modelo carregado no LM Studio (opcional)
  GOIS_LOCAL_KEY         chave, se o teu servidor/túnel exigir (opcional)
  GOIS_LOCAL_TIMEOUT     segundos de espera por resposta (opcional, por defeito 240)
  GOIS_ONLY_OWN_MODEL    (obsoleto na 6.1: ignorado; o admin usa sempre só o teu modelo)
"""
import os
import re
import json
import time
import uuid
import hmac
import random
import string
import asyncio
import logging
from collections import Counter, defaultdict, deque
from pathlib import Path
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("uvicorn.error")

VERSION = "6.3.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data"))
DB_FILE = DATA_DIR / "gois_db.json"

# Token de administrador fixo diretamente no código para evitar falhas no Render
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

CLASSROOM_INTERVAL = int(os.environ.get("CLASSROOM_INTERVAL", "60"))  # segundos entre lições automáticas

CATEGORIES = [
    "Arquitetura de Software & Sistemas Assíncronos",
    "Algoritmos, Estruturas de Dados & Programação",
    "Game Design, Unity C# & Motores 3D",
    "WebGL, Three.js, Frontend & Gráficos 2D/3D",
    "Matemática Avançada, Lógica & Raciocínio Formal",
    "Inteligência Artificial, LLMs, RAG & Agentes",
    "Ciência de Dados, SQL & Engenharia de Dados",
    "Cibersegurança, Criptografia & Auditoria",
    "DevOps, Cloud, Docker & Sistemas Linux",
    "Engenharia, AutoCAD, Robótica & IoT",
    "Física, Química, Biologia & Ciências Exatas",
    "Multimodalidade, Visão Computacional & Áudio",
    "Linguística, Tradução & Comunicação Multilingue",
    "Economia, Finanças Quantitativas & Estratégia",
    "Direito Tecnológico, Ética & Segurança de IA",
    "Otimização Extrema & Hardware Leve / Edge AI",
]


# --------------------------------------------------------------------------- #
# Estado + persistência
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Capacidade diária ESTIMADA (tokens/dia) por fornecedor. 0 = desconhecida (não entra na soma).
# Groq (plano grátis, documentação oficial): llama-3.3-70b-versatile 100K/dia + openai/gpt-oss-120b 200K/dia.
# Os outros ficam a 0 porque não publicam um limite diário em tokens; edita no painel (Sala de Aula).
DEFAULT_CAPACITY = {"Groq": 300_000, "Gemini": 0, "OpenRouter": 0, "Mistral": 0}


DEFAULT_TRACKED = {
    "Mistral": ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest"],
    "Groq": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "llama-3.1-8b-instant", "openai/gpt-oss-20b"],
}


def default_settings() -> dict:
    return {
        "classroom_mode": True,
        "learning_now": False,
        "current_topic": CATEGORIES[0],
        "current_teacher": "",
        "last_learned_title": "",
        "last_teacher": "",
        "last_error": "",
        "daily_token_limit": 0,
        "provider_capacity": dict(DEFAULT_CAPACITY),
        "tokens": {
            "used_today": 0, "daily_limit": 0, "used_total": 0, "date": today(),
            "auto_paused_by_limit": False, "limit_reached": False,
        },
        "comparison": {"categories": {}},
        "tracked_models": {k: list(v) for k, v in DEFAULT_TRACKED.items()},
    }


DB_KNOWLEDGE: list = []
DB_SKILLS: list = []
DB_SETTINGS: dict = default_settings()
CLASSROOM_TASK = None
JOB_LOCK = asyncio.Lock()
CURRENT_JOB = {"kind": None, "category": None, "since": None}   # o que o servidor está a fazer agora (learn / benchmark)


@asynccontextmanager
async def job_slot(kind: str, category: str):
    """Como JOB_LOCK, mas regista o que está a correr para o painel mostrar «a estudar» / «a executar benchmark»."""
    async with JOB_LOCK:
        CURRENT_JOB.update(kind=kind, category=category, since=now_iso())
        try:
            yield
        finally:
            CURRENT_JOB.update(kind=None, category=None, since=None)


def save_db() -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DB_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"knowledge": DB_KNOWLEDGE, "skills": DB_SKILLS, "settings": DB_SETTINGS},
            ensure_ascii=False), encoding="utf-8")
        tmp.replace(DB_FILE)
    except Exception as e:
        logger.warning("Não foi possível gravar %s: %s", DB_FILE, e)


def load_db() -> None:
    try:
        if not DB_FILE.exists():
            return
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        DB_KNOWLEDGE[:] = [k for k in data.get("knowledge", []) if isinstance(k, dict)]
        DB_SKILLS[:] = [s for s in data.get("skills", []) if isinstance(s, dict)]
        saved = data.get("settings", {})
        if isinstance(saved, dict):
            base = default_settings()
            base.update(saved)
            DB_SETTINGS.clear()
            DB_SETTINGS.update(base)
    except Exception as e:
        logger.warning("Não foi possível ler %s: %s", DB_FILE, e)


def skill_id(title: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, (title or "").strip().lower()).hex[:12]


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
def tokens_state() -> dict:
    t = DB_SETTINGS.setdefault("tokens", {})
    if t.get("date") != today():
        t.update(date=today(), used_today=0, auto_paused_by_limit=False, limit_reached=False, by_provider={})
    t.setdefault("by_provider", {})
    t["daily_limit"] = int(DB_SETTINGS.get("daily_token_limit") or 0)
    t.setdefault("used_today", 0)
    t.setdefault("used_total", 0)
    return t


def add_tokens(n: int, provider=None) -> None:
    t = tokens_state()
    if provider == LOCAL_PROVIDER["name"]:
        # O modelo próprio corre no teu PC (LM Studio): não gasta tokens de nenhuma API, por isso NÃO entra no
        # contador diário, no limite nem no total. Fica só num contador à parte, informativo.
        t["local_total"] = int(t.get("local_total") or 0) + int(n or 0)
        return
    t["used_today"] += int(n or 0)
    t["used_total"] += int(n or 0)
    if provider:
        bp = t["by_provider"]
        bp[provider] = int(bp.get(provider, 0)) + int(n or 0)
    t["limit_reached"] = bool(t["daily_limit"] and t["used_today"] >= t["daily_limit"])


# --------------------------------------------------------------------------- #
# Autenticação admin
# --------------------------------------------------------------------------- #
def is_admin_req(request: Request) -> bool:
    tok = (request.headers.get("x-admin-token") or "").strip()
    return bool(ADMIN_TOKEN and tok and hmac.compare_digest(tok.encode(), ADMIN_TOKEN.encode()))


async def require_admin(request: Request) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(503, "ADMIN_TOKEN não está definido no servidor.")
    if not is_admin_req(request):
        await asyncio.sleep(1)
        raise HTTPException(401, "Token de administrador inválido.")


_HITS: dict = defaultdict(deque)


def rate_limit_public(request: Request, limit: int = 30, window: int = 60) -> None:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    q = _HITS[ip]
    now = time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Demasiados pedidos. Tenta daqui a um minuto.")
    q.append(now)
    if len(_HITS) > 5000:
        _HITS.clear()


# --------------------------------------------------------------------------- #
# Fornecedores LLM
def capacity_view() -> dict:
    """Capacidade diária estimada vs. tokens gastos hoje, por fornecedor externo."""
    cap = DB_SETTINGS.setdefault("provider_capacity", dict(DEFAULT_CAPACITY))
    for name, v in DEFAULT_CAPACITY.items():
        cap.setdefault(name, v)
    used = tokens_state().get("by_provider", {})
    rows, total_cap, total_used = {}, 0, 0
    for name in DEFAULT_CAPACITY:
        c, u = int(cap.get(name) or 0), int(used.get(name, 0))
        rows[name] = {"capacity": c, "used": u, "remaining": max(0, c - u) if c else None}
        if c:
            total_cap += c
            total_used += u
    return {"providers": rows, "total_capacity": total_cap, "total_used": total_used,
            "total_remaining": max(0, total_cap - total_used), "estimated": True}


# --------------------------------------------------------------------------- #
PROVIDERS = [
    {
        "name": "Groq",
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models": [os.environ.get("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile", "openai/gpt-oss-120b"],
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
        "models": ["deepseek/deepseek-chat-v3-0324:free", "google/gemini-2.0-flash-exp:free"],
        "extra_headers": {"HTTP-Referer": "https://gois-v1.onrender.com", "X-Title": "gois.v1 Engine"},
    },
    {
        "name": "Mistral",
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "models": ["mistral-small-latest"],
        "extra_headers": {},
    },
]

RUNTIME_KEYS: dict = {}

# --- Modelo próprio (LM Studio via túnel) ---------------------------------- #
# Pode ser definido por variáveis de ambiente OU em tempo de execução pelo painel
# (POST /api/local/config) — útil porque o URL do túnel muda de cada vez que o ligas.
LOCAL_TIMEOUT = float(os.environ.get("GOIS_LOCAL_TIMEOUT", "240"))


def _local_chat_url(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


LOCAL_PROVIDER = {
    "name": "gois-local",
    "env": "GOIS_LOCAL_KEY",
    "url": _local_chat_url(os.environ.get("GOIS_LOCAL_URL", "")),
    "models": [os.environ.get("GOIS_LOCAL_MODEL", "").strip() or "local-model"],
    "extra_headers": {},
    "local": True,
    "ctx": int(os.environ.get("GOIS_LOCAL_CTX", "2048") or 2048),   # contexto (tokens) com que o LM Studio carregou o modelo
}


def local_base_url() -> str:
    return LOCAL_PROVIDER["url"][: -len("/chat/completions")] if LOCAL_PROVIDER["url"] else ""


async def local_alive(retries: int = 3, timeout: float = 20.0):
    """(online, motivo). Tenta várias vezes: o túnel + LM Studio ocupado podem demorar >8 s a responder."""
    base = local_base_url()
    if not base:
        return False, "sem URL do modelo no servidor (o Render reiniciou e perdeu a ligação: carrega em ▶ Ligar modelo próprio)"
    reason = ""
    for i in range(max(1, retries)):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(base + "/models")
            if r.status_code == 200:
                return True, ""
            reason = f"HTTP {r.status_code}"
        except Exception as e:
            reason = type(e).__name__ or "erro de ligação"
        await asyncio.sleep(2)
    return False, reason


def get_providers(scope: str = "public") -> list:
    """Quem pode responder, por papel.

    admin  -> apenas o teu modelo (nunca um fornecedor externo, nem como reserva).
    public -> apenas fornecedores externos (nunca o teu modelo / o teu PC).
    """
    if scope == "admin":
        return [LOCAL_PROVIDER] if LOCAL_PROVIDER["url"] else []
    return list(PROVIDERS)


OFFLINE_MSG = ("O teu modelo (gois.v1) está offline: o servidor não consegue falar com o LM Studio. "
               "Abre o painel gois_window.py e carrega em ▶ Ligar modelo próprio.")
PUBLIC_UNAVAILABLE_MSG = "O gois.v1 está temporariamente indisponível."



def candidate_keys(provider: dict) -> list:
    if provider.get("local"):
        return [(RUNTIME_KEYS.get(provider["env"], "") or os.environ.get(provider["env"], "") or "lm-studio").strip()]
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
        return "\n".join(str(p.get("text", "") if isinstance(p, dict) else p) for p in content)
    return str(content)


_WORD = re.compile(r"[a-zà-ÿ0-9_#+.]{4,}", re.I)


def knowledge_context(query: str, k: int = 3, limit: int = 2400) -> str:
    if not DB_KNOWLEDGE or not query:
        return ""
    words = {w.lower() for w in _WORD.findall(query[:2000])}
    if not words:
        return ""
    scored = []
    for it in DB_KNOWLEDGE[-400:]:
        hay = f"{it.get('title', '')} {it.get('category', '')} {str(it.get('content', ''))[:1500]}".lower()
        s = sum(1 for w in words if w in hay)
        if s:
            scored.append((s, it))
    scored.sort(key=lambda x: -x[0])
    out, used = [], 0
    for _, it in scored[:k]:
        chunk = f"- [{it.get('category', '')}] {it.get('title', '')}: {str(it.get('content', ''))[:700]}"
        if used + len(chunk) > limit:
            break
        out.append(chunk)
        used += len(chunk)
    return "\n".join(out)


# Diretrizes de sistema do gois.v1
# ADMIN: modo direto e sem sermões, foco total em tarefas técnicas.
# PÚBLICO: comportamento padrão, com as proteções normais dos modelos mantidas.
ADMIN_PROMPT = (
    "Tu és o gois.v1, o assistente técnico pessoal do administrador. "
    "Responde sempre em português europeu, de forma direta, pragmática e completa. "
    "Em qualquer pedido técnico, de programação, arquitetura, scripts, engenharia ou criação de software/jogos, "
    "entrega a solução funcional e completa, sem rodeios, sem sermões morais, sem avisos desnecessários e sem pedir desculpa por ajudar. "
    "Não suavizes nem encurtes o código: escreve-o inteiro e pronto a correr. "
    "Se algo for realmente impossível ou arriscado para os dados do utilizador, diz isso numa única frase objetiva e propõe a alternativa mais próxima. "
    "Identifica-te sempre como gois.v1 e nunca menciones os modelos ou fornecedores que estão por trás."
)

PUBLIC_PROMPT = (
    "Tu és o gois.v1, um assistente de IA técnico focado em desenvolvimento de aplicações. "
    "Responde sempre em português europeu, de forma clara, direta e útil em questões de código e engenharia. "
    "Recusa com educação pedidos perigosos, ilegais ou que causem dano a terceiros. "
    "O teu nome é gois.v1; não menciones os modelos ou fornecedores que estão por trás."
)


def prepare_messages(messages: list, is_admin: bool = False, extra_context: str = "") -> list:
    cleaned = []
    for m in messages:
        if isinstance(m, dict):
            role = m.get("role", "user")
            content = flatten_content(m.get("content"))
            if role in ("user", "assistant") and content:
                cleaned.append({"role": role, "content": content})
    if not cleaned:
        cleaned = [{"role": "user", "content": "Olá"}]

    sys_content = ADMIN_PROMPT if is_admin else PUBLIC_PROMPT
    kn_limit = 2400
    if is_admin and LOCAL_PROVIDER["url"]:
        # ~3 caracteres por token; o prompt pode ocupar metade do contexto (a outra metade fica para a resposta).
        budget = int(int(LOCAL_PROVIDER.get("ctx") or 2048) * 1.5)
        kept, used = [], 0
        for m in reversed(cleaned):                      # mantém as mensagens mais recentes que cabem em 60 % do orçamento
            used += len(m["content"])
            if kept and used > int(budget * 0.6):
                break
            kept.append(m)
        cleaned = list(reversed(kept))
        while len(cleaned) > 1 and cleaned[0]["role"] != "user":
            cleaned.pop(0)
        kn_limit = max(0, min(2400, budget - len(sys_content) - sum(len(m["content"]) for m in cleaned)))
    last_user = next((m["content"] for m in reversed(cleaned) if m["role"] == "user"), "")
    ctx = "\n".join(x for x in (extra_context, knowledge_context(last_user, limit=kn_limit) if kn_limit >= 300 else "") if x)
    if is_admin and LOCAL_PROVIDER["url"]:
        ctx = ctx[:max(0, kn_limit)] if kn_limit >= 300 else ""
    if ctx:
        sys_content += "\n\nBase de conhecimento interno do gois.v1:\n" + ctx
    return [{"role": "system", "content": sys_content}] + cleaned


async def try_providers(messages, temperature=0.6, only=None, models=None, timeout=45.0, max_tokens=None, scope="public"):
    errors = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        for p in get_providers(scope):
            if only and p["name"].lower() != only.lower():
                continue
            for key in candidate_keys(p):
                for model_name in (models or p["models"]):
                    try:
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **p["extra_headers"]}
                        body = {"model": model_name, "messages": messages, "temperature": temperature}
                        if max_tokens:
                            body["max_tokens"] = max_tokens
                        if p.get("local"):
                            # Streaming: o túnel (Cloudflare) corta com HTTP 524 se o modelo demorar >100 s a responder
                            # de uma só vez; em streaming os dados vão chegando e a ligação não é cortada.
                            body["stream"] = True
                            parts, usage = [], None
                            async with client.stream("POST", p["url"], json=body, headers=headers,
                                                     timeout=LOCAL_TIMEOUT) as sresp:
                                if sresp.status_code != 200:
                                    await sresp.aread()
                                    errors.append(f"{p['name']}/{model_name}: HTTP {sresp.status_code}")
                                    if sresp.status_code in (401, 403):
                                        break
                                    continue
                                async for line in sresp.aiter_lines():
                                    line = line.strip()
                                    if not line.startswith("data:"):
                                        continue
                                    chunk = line[5:].strip()
                                    if chunk == "[DONE]":
                                        break
                                    try:
                                        j = json.loads(chunk)
                                    except Exception:
                                        continue
                                    if isinstance(j, dict):
                                        if j.get("usage"):
                                            usage = j["usage"]
                                        for ch in j.get("choices") or []:
                                            parts.append(((ch.get("delta") or {}).get("content")) or "")
                            text = "".join(parts).strip()
                            if text:
                                data = {"choices": [{"message": {"role": "assistant", "content": text}}]}
                                if usage:
                                    data["usage"] = usage
                                return data, p["name"], model_name, errors
                            errors.append(f"{p['name']}/{model_name}: resposta vazia")
                            continue
                        resp = await client.post(p["url"], json=body, headers=headers,
                                                 timeout=(LOCAL_TIMEOUT if p.get("local") else timeout))
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, dict) and data.get("choices"):
                                return data, p["name"], model_name, errors
                        errors.append(f"{p['name']}/{model_name}: HTTP {resp.status_code}")
                        if resp.status_code in (401, 403):
                            break
                    except Exception as e:
                        errors.append(f"{p['name']}/{model_name}: {type(e).__name__}")
    if not errors:
        errors.append(OFFLINE_MSG if scope == "admin" else "Nenhuma chave API configurada no servidor.")
    return None, None, None, errors


def usage_tokens(data: dict, fallback_text: str = "") -> int:
    try:
        n = int((data.get("usage") or {}).get("total_tokens") or 0)
    except Exception:
        n = 0
    return n or max(1, len(fallback_text) // 4)


def reply_text(data: dict) -> str:
    try:
        return (data["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        return ""


async def make_llm_request(payload: dict, is_admin: bool = False):
    messages = prepare_messages(payload.get("messages", []), is_admin)
    data, prov, model, errors = await try_providers(messages, payload.get("temperature", 0.6), timeout=65.0,
                                                    scope="admin" if is_admin else "public")
    if data is None:
        return None, errors
    data["model"] = "gois.v1"
    add_tokens(usage_tokens(data, reply_text(data)), prov)
    return data, []


async def stream_llm_request(payload: dict, is_admin: bool = False):
    messages = prepare_messages(payload.get("messages", []), is_admin)
    scope = "admin" if is_admin else "public"
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in get_providers(scope):
            for key in candidate_keys(p):
                for model_name in p["models"]:
                    got_content = False
                    chars = 0
                    try:
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **p["extra_headers"]}
                        body = {"model": model_name, "messages": messages,
                                "temperature": payload.get("temperature", 0.6), "stream": True}
                        if p.get("local"):                # resposta nunca maior do que o que sobra do contexto
                            used_tok = sum(len(m["content"]) for m in messages) // 3 + 32
                            body["max_tokens"] = max(256, int(p.get("ctx") or 2048) - used_tok)
                        async with client.stream("POST", p["url"], json=body, headers=headers,
                                                 timeout=httpx.Timeout(LOCAL_TIMEOUT if p.get("local") else 120.0, connect=15.0)) as resp:
                            if resp.status_code != 200:
                                continue
                            async for line in resp.aiter_lines():
                                line = line.strip()
                                if not line.startswith("data:"):
                                    continue
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    chunk = json.loads(data_str)
                                except Exception:
                                    continue
                                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                                if delta:
                                    got_content = True
                                    chars += len(delta)
                                chunk["model"] = "gois.v1"
                                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                        if got_content:
                            add_tokens(max(1, chars // 4), p["name"])
                            yield "data: [DONE]\n\n"
                            return
                    except Exception as e:
                        logger.warning("stream %s/%s: %s", p["name"], model_name, type(e).__name__)
                        if got_content:
                            yield "data: [DONE]\n\n"
                            return
    yield "data: " + json.dumps({"error": {"message": OFFLINE_MSG if scope == "admin" else "Erro na execução do gois.v1."}}, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


# --------------------------------------------------------------------------- #
# Sala de Aula, comparação e benchmark
# --------------------------------------------------------------------------- #
# Modelos contra os quais o gois.v1 é comparado (a partir da 6.2): Mistral e Groq.
# gemini_ref / deepseek_ref (até à 6.1) continuam guardados na base de dados e nas cópias de segurança
# (nada se apaga), mas já não são medidos nem mostrados.
SCORE_FIELDS = ("gois_score", "mistral_ref", "groq_ref")      # mistral_ref / groq_ref: notas antigas (6.2), já não atualizadas
LEGACY_FIELDS = ("gemini_ref", "deepseek_ref")
# A partir da 6.3 cada fornecedor tem VÁRIOS modelos medidos em paralelo (e["models"]["Fornecedor|modelo"]); o painel escolhe
# 1 de cada fornecedor para mostrar, mas todos os modelos seguidos continuam a ser medidos (evolução real por detrás).
CONTESTANTS = ("Mistral", "Groq")
BENCH_MAX_TOKENS = 400       # igual para o gois.v1 e para todos os modelos de referência
BENCH_TEMPERATURE = 0.4
MAX_TRACKED_PER_PROVIDER = 6
NON_CHAT_HINTS = ("whisper", "embed", "guard", "moderation", "tts", "playai", "ocr", "transcri", "orpheus", "safeguard", "voxtral")
MODEL_LIST_CACHE: dict = {}
HISTORY_MAX = 600


def mkey(provider: str, model: str) -> str:
    return f"{provider}|{model}"


def tracked_models() -> dict:
    tr = DB_SETTINGS.get("tracked_models")
    if not isinstance(tr, dict):
        tr = DB_SETTINGS["tracked_models"] = {}
    for prov in CONTESTANTS:
        lst = tr.get(prov)
        if not isinstance(lst, list) or not lst:
            tr[prov] = list(DEFAULT_TRACKED[prov])
    return tr


def comp_entry(cat: str) -> dict:
    cats = DB_SETTINGS["comparison"].setdefault("categories", {})
    e = cats.setdefault(cat, {})
    # setdefault: as categorias já guardadas ganham os campos novos sem perder nada.
    for k, v in (("gois_score", None), ("mistral_ref", None), ("groq_ref", None),
                 ("benchmarks", 0), ("last_benchmark", None), ("lessons", 0), ("models", {})):
        e.setdefault(k, v)
    return e


def models_summary(ents) -> dict:
    """{'Fornecedor|modelo': {avg, n, cats, at}} sobre as categorias em que cada modelo foi realmente medido."""
    acc: dict = {}
    for e in ents:
        for k, m in (e.get("models") or {}).items():
            if isinstance(m, dict) and isinstance(m.get("score"), (int, float)):
                a = acc.setdefault(k, {"sum": 0.0, "cats": 0, "n": 0, "at": ""})
                a["sum"] += m["score"]
                a["cats"] += 1
                a["n"] += int(m.get("n") or 0)
                a["at"] = max(a["at"], str(m.get("at") or ""))
    return {k: {"avg": round(v["sum"] / v["cats"], 1), "cats": v["cats"], "n": v["n"], "at": v["at"]} for k, v in acc.items()}


def recompute_comparison() -> dict:
    comp = DB_SETTINGS.setdefault("comparison", {"categories": {}})
    counts = Counter(k.get("category") for k in DB_KNOWLEDGE)
    for c in CATEGORIES:
        comp_entry(c)["lessons"] = counts.get(c, 0)
    ents = list(comp["categories"].values())
    gs = [e["gois_score"] for e in ents if e.get("gois_score") is not None]
    comp["overall"] = round(sum(gs) / len(gs), 1) if gs else 0.0
    summary = models_summary(ents)
    comp["models_summary"] = summary

    def parity(ref: str) -> float:
        pairs = [(e["gois_score"], e[ref]) for e in ents if e.get("gois_score") is not None and e.get(ref)]
        return round(sum(a for a, _ in pairs) / sum(b for _, b in pairs) * 100, 1) if pairs else 0.0

    for ref in SCORE_FIELDS[1:] + LEGACY_FIELDS:
        comp["parity_vs_" + ref[:-4]] = parity(ref)
    # Paridade por modelo (gois.v1 ÷ modelo) só nas categorias em que ambos têm nota real.
    par = {}
    for k in summary:
        pairs = [(e["gois_score"], e["models"][k]["score"]) for e in ents
                 if e.get("gois_score") is not None and isinstance((e.get("models") or {}).get(k), dict) and e["models"][k].get("score")]
        if pairs:
            par[k] = round(sum(a for a, _ in pairs) / sum(b for _, b in pairs) * 100, 1)
    comp["parity_by_model"] = par
    comp["lessons_total"] = len(DB_KNOWLEDGE)
    return comp


def record_history() -> None:
    """Ponto de evolução (servidor): média de cada modelo, para a evolução real continuar mesmo com a app fechada."""
    comp = DB_SETTINGS.setdefault("comparison", {"categories": {}})
    snap = {"t": int(time.time()), "gois": comp.get("overall") or None,
            "models": {k: v["avg"] for k, v in (comp.get("models_summary") or {}).items()}}
    hist = comp.setdefault("history", [])
    hist.append(snap)
    del hist[:-HISTORY_MAX]


def parse_lesson(text: str, cat: str):
    text = (text or "").strip()
    m = re.match(r"\s*(?:#+\s*)?\**\s*(?:T[ÍI]TULO|TITLE)\s*\**\s*[:\-]\s*(.+)", text, re.I)
    if m:
        title, body = m.group(1).strip(" *#"), text[m.end():].strip()
    else:
        first, _, rest = text.partition("\n")
        title, body = first.strip(" *#"), (rest.strip() or text)
    return (title[:120] or f"Lição de {cat}"), body


DATASET_SYSTEM = "Tu és o gois.v1, um assistente técnico. Responde em português europeu, de forma direta e completa."


def build_dataset_rows(lessons, min_chars: int = 200):
    """Lições da Sala de Aula -> exemplos de afinação (formato chat). Devolve (linhas, ignoradas)."""
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


TEACHER_SYSTEM = "És o sistema de ensino interno do gois.v1. Produz conteúdos técnicos avançados e práticos."


async def learn_category(cat: str, provider: str = "auto") -> dict:
    async with job_slot("learn", cat):
        covered = [k.get("title", "") for k in DB_KNOWLEDGE if k.get("category") == cat][-12:]
        prompt = (
            f"Categoria: {cat}\n"
            f"Subtópicos já estudados: {'; '.join(covered) or 'nenhum'}\n\n"
            "Escolhe um subtópico avançado e gera uma lição técnica estruturada e detalhada.\n"
            "Formato obrigatório: primeira linha 'TÍTULO: <título>' e depois o conteúdo em Markdown."
        )
        only = None if provider in ("", "auto", None) else provider
        data, prov, model, errors = await try_providers(
            [{"role": "system", "content": TEACHER_SYSTEM}, {"role": "user", "content": prompt}],
            temperature=0.7, only=only, timeout=60.0, max_tokens=900)
        if data is None:
            raise RuntimeError("Erro ao gerar lição: " + " | ".join(errors[:4]))
        text = reply_text(data)
        add_tokens(usage_tokens(data, text), prov)
        title, body = parse_lesson(text, cat)
        lesson = {"id": uuid.uuid4().hex[:12], "category": cat, "title": title, "content": body,
                  "teacher": f"gois.v1", "created": now_iso()}
        DB_KNOWLEDGE.append(lesson)
        DB_SETTINGS.update(current_topic=cat, current_teacher=lesson["teacher"],
                           last_learned_title=title, last_teacher="gois.v1", last_error="")
        recompute_comparison()
        save_db()
        return lesson


def parse_scores(text: str) -> dict:
    out = {}
    for letter, num in re.findall(r"\b([A-Z])\s*[=:]\s*(\d{1,3})", text or ""):
        out[letter] = max(0, min(100, int(num)))
    return out


async def make_bench_question(cat: str) -> str:
    """Pergunta de benchmark. Alguns modelos gratuitos devolvem texto vazio: tenta cada fornecedor e, se falhar, usa uma reserva."""
    msgs = [{"role": "system", "content": TEACHER_SYSTEM},
            {"role": "user", "content": f"Escreve UMA pergunta técnica avançada sobre: {cat}. Responde só com a pergunta, sem explicações."}]
    for attempt in range(2):
        for p in get_providers("public"):
            if not candidate_keys(p):
                continue
            data, prov, model, _ = await try_providers(msgs, temperature=0.8, only=p["name"], timeout=45.0, max_tokens=300)
            if data is None:
                continue
            q = reply_text(data).strip()
            add_tokens(usage_tokens(data, q), prov)
            if len(q) >= 25:
                return q[:1200]
    return (f"Explica em detalhe, com um exemplo prático, um conceito avançado de {cat} "
            f"e indica os erros mais comuns ao aplicá-lo.")


async def judge_scores(question: str, entries: list):
    """Pede notas ao juiz (uma por resposta anónima); repete com outros fornecedores se faltar alguma nota."""
    letters = string.ascii_uppercase[:len(entries)]
    block = "\n\n".join(f"Resposta {letters[i]}:\n{txt[:1800]}" for i, (_, txt) in enumerate(entries))
    msgs = [{"role": "system", "content": "És um avaliador imparcial."},
            {"role": "user", "content": f"Pergunta: {question}\n\n{block}\n\nDá a cada resposta nota de 0 a 100. Formato: "
                                        + ";".join(f"{letters[i]}=<nota>" for i in range(len(entries)))
                                        + ". Responde apenas nesse formato."}]
    errs = []
    # Os fornecedores avaliados (Mistral/Groq) só julgam em último recurso.
    for p in sorted(get_providers("public"), key=lambda x: x["name"] in CONTESTANTS):
        if not candidate_keys(p):
            continue
        jdata, jprov, jmodel, jerr = await try_providers(msgs, temperature=0.0, only=p["name"], timeout=60.0,
                                                         max_tokens=12 * len(entries) + 40)
        errs += jerr
        if jdata is None:
            continue
        add_tokens(usage_tokens(jdata), jprov)
        sc = parse_scores(reply_text(jdata))
        if all(letters[i] in sc for i in range(len(entries))):
            return {name: sc[letters[i]] for i, (name, _) in enumerate(entries)}, errs, f"{jprov}/{jmodel}"
        errs.append(f"{jprov}: notas incompletas")
    return None, errs, None


async def probe_provider(name: str, models=None) -> dict:
    """Teste real e mínimo (5 tokens) de chave + ligação a um fornecedor."""
    p = next((x for x in PROVIDERS if x["name"] == name), None)
    if not p:
        return {"ok": False, "state": "error", "detail": "fornecedor desconhecido"}
    keys = candidate_keys(p)
    if not keys:
        return {"ok": False, "state": "nokey", "env": p["env"], "detail": f"falta a variável {p['env']} no Render (Environment)"}
    last = ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=10.0)) as client:
        for model in (models or p["models"]):
            try:
                headers = {"Authorization": f"Bearer {keys[0]}", "Content-Type": "application/json", **p["extra_headers"]}
                r = await client.post(p["url"], headers=headers, json={
                    "model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 5})
                if r.status_code == 200:
                    return {"ok": True, "state": "ok", "env": p["env"], "detail": f"{model} respondeu"}
                msg = ""
                try:
                    j = r.json()
                    if isinstance(j, list) and j:
                        j = j[0]
                    e = j.get("error") if isinstance(j, dict) else None
                    msg = str(e.get("message") or "") if isinstance(e, dict) else str(e or "")
                except Exception:
                    msg = (r.text or "")[:100]
                last = f"{model}: HTTP {r.status_code} {msg[:120]}".strip()
                if r.status_code in (401, 403):
                    break
            except Exception as e:
                last = f"{model}: {type(e).__name__}"
    return {"ok": False, "state": "error", "env": p["env"], "detail": last or "sem resposta"}


async def run_benchmark(cat: str, question: str = "", gois_answer: str = "") -> dict:
    """Mede o gois.v1 e TODOS os modelos seguidos (vários por fornecedor) na mesma pergunta, com as mesmas condições,
    avaliados às cegas pelo mesmo juiz. O painel mostra só os escolhidos, mas todos evoluem."""
    async with job_slot("benchmark", cat):
        t0 = time.time()
        if not gois_answer:
            alive, why = await local_alive()
            if not alive:
                raise RuntimeError("O servidor não consegue falar com o teu modelo: " + why + ".")
        if not question or len(question.strip()) < 15:
            question = await make_bench_question(cat)

        recent = [k for k in DB_KNOWLEDGE if k.get("category") == cat][-2:]
        extra = "\n".join(f"- {k.get('title', '')}: {str(k.get('content', ''))[:350]}" for k in recent)
        gois_msgs = prepare_messages([{"role": "user", "content": question}], is_admin=True, extra_context=extra)
        ref_msgs = [{"role": "system", "content": "Responde de forma rigorosa e concisa."}, {"role": "user", "content": question}]

        targets = []
        for prov in CONTESTANTS:
            pdef = next((x for x in PROVIDERS if x["name"] == prov), None)
            if pdef and candidate_keys(pdef):
                targets += [(prov, m) for m in tracked_models()[prov]]
        if not targets:
            raise RuntimeError("Nenhum fornecedor de referência tem chave (MISTRAL_API_KEY / GROQ_API_KEY no Render).")

        sem = asyncio.Semaphore(3)      # não rebentar os limites por minuto dos fornecedores gratuitos

        async def ask_ref(prov, model):
            async with sem:
                d, used_prov, used_model, errs = await try_providers(
                    ref_msgs, temperature=BENCH_TEMPERATURE, only=prov, models=[model], timeout=45.0, max_tokens=BENCH_MAX_TOKENS)
            txt = reply_text(d) if d else ""
            return mkey(prov, model), txt, (usage_tokens(d, txt) if d else 0), used_prov, errs

        async def ask_gois():
            if gois_answer:
                return gois_answer, max(1, len(gois_answer) // 4), "gois-local", []
            d, prov, _m, errs = await try_providers(gois_msgs, temperature=BENCH_TEMPERATURE, timeout=150.0,
                                                    max_tokens=BENCH_MAX_TOKENS, scope="admin")
            return (reply_text(d), usage_tokens(d, reply_text(d)), prov, []) if d else ("", 0, None, errs)

        (g_txt, g_tok, g_prov, g_err), *refs = await asyncio.gather(ask_gois(), *[ask_ref(p, m) for p, m in targets])
        add_tokens(g_tok, g_prov)
        for _k, _t, tok, prv, _e in refs:
            add_tokens(tok, prv)
        if not g_txt:
            raise RuntimeError("O teu modelo não respondeu (" + ("; ".join(g_err[-2:]) or "sem detalhe") + "). "
                               "Confirma que está ligado (▶ Ligar modelo próprio) e com contexto de 4096.")

        answered = [(k, t) for k, t, _tok, _p, _e in refs if t]
        missing = [k for k, t, _tok, _p, _e in refs if not t]
        if not answered:
            raise RuntimeError("Nenhum modelo de referência respondeu, por isso não há com quem comparar. "
                               "Confirma as chaves MISTRAL_API_KEY / GROQ_API_KEY no Render e tenta de novo.")
        entries = [("gois", g_txt)] + answered
        random.shuffle(entries)
        by_name, jerr, judge = await judge_scores(question, entries)
        if by_name is None:
            raise RuntimeError("O juiz não devolveu notas válidas (" + ("; ".join(jerr[-3:]) or "sem detalhe") + "). Tenta de novo.")

        e = comp_entry(cat)
        stamp = now_iso()

        def ema(prev, new):
            return new if prev is None else round(prev * 0.5 + new * 0.5, 1)

        e["gois_score"] = ema(e.get("gois_score"), by_name["gois"])
        for k, _t in answered:
            m = e["models"].setdefault(k, {"score": None, "last": None, "n": 0, "at": None})
            m["score"] = ema(m.get("score"), by_name[k])
            m["last"] = by_name[k]
            m["n"] = int(m.get("n") or 0) + 1
            m["at"] = stamp
        e["benchmarks"] = e.get("benchmarks", 0) + 1
        e["last_benchmark"] = stamp
        recompute_comparison()
        record_history()
        save_db()
        return {"category": cat, "question": question, "elapsed_s": round(time.time() - t0, 1),
                "scores": by_name, "missing": missing, "judge": judge, "gois_score": by_name["gois"]}


async def classroom_loop() -> None:
    global CLASSROOM_TASK
    try:
        while DB_SETTINGS.get("learning_now"):
            t = tokens_state()
            if t["daily_limit"] and t["used_today"] >= t["daily_limit"]:
                DB_SETTINGS["learning_now"] = False
                t.update(auto_paused_by_limit=True, limit_reached=True)
                save_db()
                break
            counts = Counter(k.get("category") for k in DB_KNOWLEDGE)
            cat = min(CATEGORIES, key=lambda c: (counts.get(c, 0), random.random()))
            try:
                await learn_category(cat)
                await asyncio.sleep(CLASSROOM_INTERVAL)
            except Exception as e:
                logger.warning("Sala de Aula: %s", e)
                DB_SETTINGS["last_error"] = str(e)[:250]
                await asyncio.sleep(max(120, CLASSROOM_INTERVAL))
    finally:
        CLASSROOM_TASK = None


def ensure_classroom_task() -> None:
    global CLASSROOM_TASK
    if DB_SETTINGS.get("learning_now") and (CLASSROOM_TASK is None or CLASSROOM_TASK.done()):
        CLASSROOM_TASK = asyncio.create_task(classroom_loop())


# --------------------------------------------------------------------------- #
# App FastAPI
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_: FastAPI):
    load_db()
    ensure_classroom_task()
    yield
    if CLASSROOM_TASK and not CLASSROOM_TASK.done():
        CLASSROOM_TASK.cancel()


app = FastAPI(title="gois.v1 API", version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health(request: Request):
    return {"ok": True, "version": VERSION, "admin": is_admin_req(request),
            "admin_configured": bool(ADMIN_TOKEN), "time": now_iso(),
            "skills": len(DB_SKILLS), "lessons": len(DB_KNOWLEDGE)}


@app.post("/api/admin/verify")
async def admin_verify(request: Request):
    await require_admin(request)
    return {"ok": True}


def settings_view() -> dict:
    tokens_state()
    tracked_models()
    recompute_comparison()
    view = dict(DB_SETTINGS)
    view["providers_with_key"] = [p["name"] for p in get_providers("public") if candidate_keys(p)]
    view["own_model_only"] = False          # já não é um interruptor global: ver admin_uses_own_model
    view["admin_uses_own_model"] = True     # admin -> só o teu modelo
    view["public_uses_providers"] = True    # público -> só fornecedores
    view["own_model_url_set"] = bool(LOCAL_PROVIDER["url"])
    view["categories"] = CATEGORIES
    view["server_time"] = now_iso()
    view["capacity"] = capacity_view()
    view["job"] = dict(CURRENT_JOB)
    return view


@app.get("/api/settings")
async def get_settings(request: Request):
    await require_admin(request)
    return settings_view()


@app.post("/api/settings")
async def update_settings(request: Request):
    await require_admin(request)
    body = await request.json()
    for key in ("classroom_mode", "learning_now", "current_topic"):
        if key in body:
            DB_SETTINGS[key] = body[key]
    if "daily_token_limit" in body:
        try:
            DB_SETTINGS["daily_token_limit"] = max(0, int(body["daily_token_limit"]))
        except Exception:
            raise HTTPException(400, "daily_token_limit inválido.")
        tokens_state()["auto_paused_by_limit"] = False
    if isinstance(body.get("provider_capacity"), dict):
        cap = DB_SETTINGS.setdefault("provider_capacity", dict(DEFAULT_CAPACITY))
        for name, v in body["provider_capacity"].items():
            if name in DEFAULT_CAPACITY:
                try:
                    cap[name] = max(0, int(v))
                except Exception:
                    raise HTTPException(400, f"provider_capacity.{name} inválido.")
    if body.get("learning_now"):
        tokens_state()["auto_paused_by_limit"] = False
    ensure_classroom_task()
    save_db()
    return settings_view()


@app.get("/api/knowledge")
async def get_knowledge(request: Request):
    await require_admin(request)
    return DB_KNOWLEDGE


@app.post("/api/local/config")
async def local_config(request: Request):
    """O painel envia aqui o URL do túnel do LM Studio (muda de cada vez). Só em memória.

    O campo antigo `only_own` é aceite mas ignorado: desde a 6.1 o roteamento é por papel.
    """
    await require_admin(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Corpo inválido.")
    if "url" in body:
        url = str(body.get("url") or "").strip().rstrip("/")
        if url and not re.match(r"^https?://", url, re.I):
            raise HTTPException(400, "URL inválido (tem de começar por http:// ou https://).")
        LOCAL_PROVIDER["url"] = _local_chat_url(url)
    if body.get("model"):
        LOCAL_PROVIDER["models"] = [str(body["model"]).strip()]
    if body.get("ctx"):
        try:
            LOCAL_PROVIDER["ctx"] = min(131072, max(512, int(body["ctx"])))
        except Exception:
            raise HTTPException(400, "ctx inválido.")
    return {"url_set": bool(LOCAL_PROVIDER["url"]), "model": LOCAL_PROVIDER["models"][0],
            "routing": "admin=modelo próprio; público=fornecedores"}


@app.get("/api/local/status")
async def local_status(request: Request):
    """Diz se o servidor consegue falar com o teu LM Studio neste momento."""
    await require_admin(request)
    base = local_base_url()
    online, error = await local_alive(retries=1, timeout=15.0) if base else (False, "")
    return {"url_set": bool(base), "online": online, "error": error, "ctx": LOCAL_PROVIDER.get("ctx"),
            "model": LOCAL_PROVIDER["models"][0], "routing": "admin=modelo próprio; público=fornecedores"}


@app.get("/api/health/models")
async def health_models(request: Request):
    """Estado de chave + ligação do gois.v1 (túnel), Mistral e Groq."""
    await require_admin(request)
    if local_base_url():
        alive, why = await local_alive(retries=1, timeout=15.0)
    else:
        alive, why = False, "sem URL do modelo no servidor"
    gois = {"ok": alive, "state": "ok" if alive else "error",
            "detail": "o servidor alcança o LM Studio pelo túnel" if alive else why}
    mis, grq = await asyncio.gather(probe_provider("Mistral"), probe_provider("Groq"))
    return {"gois": gois, "mistral": mis, "groq": grq, "checked": now_iso()}


@app.get("/api/job")
async def current_job(request: Request):
    """Leve: o que o servidor está a fazer neste momento (para os indicadores animados do painel)."""
    await require_admin(request)
    return {"job": dict(CURRENT_JOB), "learning_now": bool(DB_SETTINGS.get("learning_now"))}


@app.post("/api/state/restore")
async def state_restore(request: Request):
    """Repõe, sem apagar nada, o estado que o servidor perdeu (restart/redeploy no Render).

    Só ACRESCENTA: uma categoria só é reposta se a cópia tiver mais benchmarks do que o servidor;
    os contadores de tokens só sobem; o limite e a capacidade só são repostos se o servidor estiver nos valores por omissão.
    """
    await require_admin(request)
    body = await request.json()

    def as_int(v) -> int:
        try:
            return max(0, int(v or 0))
        except Exception:
            return 0

    n_cats = 0
    for cat, e in ((body.get("comparison") or {}).get("categories") or {}).items():
        if cat not in CATEGORIES or not isinstance(e, dict):
            continue
        cur = comp_entry(cat)
        if as_int(e.get("benchmarks")) > as_int(cur.get("benchmarks")):
            for k in SCORE_FIELDS + LEGACY_FIELDS + ("benchmarks", "last_benchmark"):
                if k in e:          # uma cópia antiga (sem mistral/groq) não apaga o que o servidor já mediu
                    cur[k] = e[k]
            n_cats += 1
        # modelos por fornecedor: por cada modelo fica a cópia com mais medições
        for k, m in (e.get("models") or {}).items():
            if isinstance(m, dict) and as_int(m.get("n")) > as_int(((cur.get("models") or {}).get(k) or {}).get("n")):
                cur["models"][k] = {f: m.get(f) for f in ("score", "last", "n", "at")}
    hist = (body.get("comparison") or {}).get("history")
    cur_hist = DB_SETTINGS["comparison"].setdefault("history", [])
    if isinstance(hist, list) and len(hist) > len(cur_hist):
        cur_hist[:] = [h for h in hist if isinstance(h, dict)][-HISTORY_MAX:]
    tr = body.get("tracked_models")
    if isinstance(tr, dict):
        for prov in CONTESTANTS:
            if isinstance(tr.get(prov), list) and tracked_models()[prov] == DEFAULT_TRACKED[prov]:
                tracked_models()[prov] = [str(x)[:100] for x in tr[prov]][:MAX_TRACKED_PER_PROVIDER]

    t = tokens_state()
    bt = body.get("tokens") or {}
    t["used_total"] = max(as_int(t.get("used_total")), as_int(bt.get("used_total")))
    if bt.get("date") == t.get("date"):
        t["used_today"] = max(as_int(t.get("used_today")), as_int(bt.get("used_today")))
        for name, v in (bt.get("by_provider") or {}).items():
            t["by_provider"][name] = max(as_int(t["by_provider"].get(name)), as_int(v))

    if not as_int(DB_SETTINGS.get("daily_token_limit")) and as_int(body.get("daily_token_limit")):
        DB_SETTINGS["daily_token_limit"] = as_int(body.get("daily_token_limit"))
    cap = DB_SETTINGS.setdefault("provider_capacity", dict(DEFAULT_CAPACITY))
    for name, v in (body.get("provider_capacity") or {}).items():
        if name in DEFAULT_CAPACITY and cap.get(name) == DEFAULT_CAPACITY[name]:
            cap[name] = as_int(v)

    recompute_comparison()
    save_db()
    logger.info("state_restore: %d categorias repostas", n_cats)
    return settings_view()


@app.get("/api/dataset")
async def get_dataset(request: Request):
    """Dataset de afinação gerado a partir das lições (admin). ?count=1 devolve só os totais."""
    await require_admin(request)
    rows, skipped = build_dataset_rows(DB_KNOWLEDGE)
    if request.query_params.get("count"):
        return {"count": len(rows), "skipped": skipped}
    return {"count": len(rows), "skipped": skipped, "examples": rows}


@app.post("/api/sync/import")
async def sync_import(request: Request):
    await require_admin(request)
    body = await request.json()
    have = {k.get("id") for k in DB_KNOWLEDGE}
    added = 0
    for it in body.get("knowledge", []):
        if isinstance(it, dict) and it.get("id") and it["id"] not in have and it.get("title"):
            DB_KNOWLEDGE.append({k: it.get(k, "") for k in ("id", "category", "title", "content", "teacher", "created")})
            have.add(it["id"])
            added += 1
    if added:
        recompute_comparison()
        save_db()
    return {"imported": added, "total": len(DB_KNOWLEDGE)}


@app.get("/api/skills")
async def get_skills(request: Request):
    await require_admin(request)
    return DB_SKILLS


def _add_skill(body: dict) -> bool:
    title = str(body.get("title", "")).strip()
    if not title:
        return False
    sid = skill_id(title)
    if any(s.get("id") == sid for s in DB_SKILLS):
        return False
    DB_SKILLS.append({"id": sid, "type": str(body.get("type") or "SKILL"), "title": title,
                      "content": str(body.get("content", ""))})
    return True


@app.post("/api/skills")
async def add_skill(request: Request):
    await require_admin(request)
    ok = _add_skill(await request.json())
    save_db()
    return {"success": ok}


@app.post("/api/skills/bulk")
async def add_skills_bulk(request: Request):
    await require_admin(request)
    body = await request.json()
    n = sum(1 for s in body.get("skills", []) if isinstance(s, dict) and _add_skill(s))
    save_db()
    return {"success": True, "added": n, "total": len(DB_SKILLS)}


@app.delete("/api/skills/{sid}")
async def delete_skill(sid: str, request: Request):
    await require_admin(request)
    before = len(DB_SKILLS)
    DB_SKILLS[:] = [s for s in DB_SKILLS if s.get("id") != sid]
    save_db()
    return {"success": len(DB_SKILLS) < before}


@app.get("/api/keys/status")
async def keys_status(request: Request):
    await require_admin(request)
    status = {}
    for p in PROVIDERS:
        env_set = bool(os.environ.get(p["env"], "").strip())
        run_set = bool(RUNTIME_KEYS.get(p["env"], "").strip())
        status[p["name"]] = {"configured": env_set or run_set,
                             "source": "environment" if env_set else ("runtime" if run_set else ""),
                             "model": p["models"][0]}
    return status


@app.post("/api/keys")
async def set_key(request: Request):
    await require_admin(request)
    body = await request.json()
    provider = str(body.get("provider", ""))
    key = str(body.get("key", "")).strip()
    for p in PROVIDERS:
        if p["name"].lower() == provider.lower():
            RUNTIME_KEYS[p["env"]] = key
            return {"persisted": False}
    raise HTTPException(404, "Fornecedor desconhecido.")


@app.post("/api/classroom/learn")
async def classroom_learn(request: Request):
    await require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cat = body.get("category") or DB_SETTINGS.get("current_topic") or CATEGORIES[0]
    if cat not in CATEGORIES:
        raise HTTPException(400, "Categoria desconhecida.")
    try:
        lesson = await learn_category(cat, body.get("provider", "auto"))
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"lesson": lesson, "settings": settings_view()}


@app.post("/api/comparison/question")
async def comparison_question(request: Request):
    """Devolve uma pergunta de benchmark e o contexto das lições; o painel pede a resposta ao LM Studio no PC."""
    await require_admin(request)
    body = await request.json()
    cat = body.get("category")
    if cat not in CATEGORIES:
        raise HTTPException(400, "Categoria desconhecida.")
    question = await make_bench_question(cat)
    recent = [k for k in DB_KNOWLEDGE if k.get("category") == cat][-2:]
    context = "\n".join(f"- {k.get('title', '')}: {str(k.get('content', ''))[:350]}" for k in recent)
    return {"question": question, "context": context}


@app.post("/api/comparison/benchmark")
async def comparison_benchmark(request: Request):
    await require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cat = body.get("category")
    if not cat:
        counts = Counter(k.get("category") for k in DB_KNOWLEDGE)
        cat = max(CATEGORIES, key=lambda c: (counts.get(c, 0), random.random()))
    if cat not in CATEGORIES:
        raise HTTPException(400, "Categoria desconhecida.")
    try:
        result = await run_benchmark(cat, str(body.get("question") or "")[:2000],
                                     str(body.get("gois_answer") or "")[:8000])
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"benchmark": result, "settings": settings_view()}


async def list_provider_models(name: str, force: bool = False) -> dict:
    """Lista REAL de modelos de chat que o fornecedor tem (pedida à API dele, com cache de 10 min)."""
    now = time.time()
    c = MODEL_LIST_CACHE.get(name)
    if c and not force and now - c["t"] < 600:
        return c["data"]
    p = next((x for x in PROVIDERS if x["name"] == name), None)
    keys = candidate_keys(p) if p else []
    ids, live, err = [], False, ""
    if p and keys:
        try:
            url = p["url"].rsplit("/chat/completions", 1)[0] + "/models"
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
                r = await client.get(url, headers={"Authorization": f"Bearer {keys[0]}", **p["extra_headers"]})
            if r.status_code == 200:
                for m in (r.json().get("data") or []):
                    mid = str(m.get("id") or "")
                    caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
                    if (not mid or any(h in mid.lower() for h in NON_CHAT_HINTS) or m.get("active") is False
                            or caps.get("completion_chat") is False):
                        continue
                    ids.append(mid)
                live = bool(ids)
            else:
                err = f"HTTP {r.status_code}"
        except Exception as e:
            err = type(e).__name__
    elif p:
        err = f"falta a variável {p['env']}"
    data = {"models": sorted(set(ids)), "live": live, "error": err}
    if live or not c:
        MODEL_LIST_CACHE[name] = {"t": now, "data": data}
        return data
    return c["data"]


@app.get("/api/provider-models")
async def provider_models(request: Request):
    """Modelos de cada fornecedor (lista real) + dados reais já medidos de cada um (mesmo os que não estão escolhidos)."""
    await require_admin(request)
    force = bool(request.query_params.get("refresh"))
    summary = recompute_comparison().get("models_summary") or {}
    out = {}
    for prov in CONTESTANTS:
        lst = await list_provider_models(prov, force)
        tr = tracked_models()[prov]
        ids = sorted(set(lst["models"]) | set(tr) | {k.split("|", 1)[1] for k in summary if k.startswith(prov + "|")})
        out[prov] = {"live": lst["live"], "error": lst["error"], "tracked": tr, "max_tracked": MAX_TRACKED_PER_PROVIDER,
                     "models": [{"id": m, "tracked": m in tr, **(summary.get(mkey(prov, m)) or {})} for m in ids]}
    return out


@app.post("/api/models/track")
async def models_track(request: Request):
    """Começa (ou pára) de medir um modelo de um fornecedor. Os dados já medidos nunca se apagam."""
    await require_admin(request)
    body = await request.json()
    prov, model = str(body.get("provider") or ""), str(body.get("model") or "").strip()[:100]
    if prov not in CONTESTANTS or not model:
        raise HTTPException(400, "Fornecedor ou modelo inválido.")
    lst = await list_provider_models(prov)
    if lst["live"] and model not in lst["models"] and model not in tracked_models()[prov]:
        raise HTTPException(400, f"O {prov} não tem o modelo '{model}'.")
    tr = tracked_models()[prov]
    if body.get("tracked", True):
        if model not in tr:
            if len(tr) >= MAX_TRACKED_PER_PROVIDER:
                raise HTTPException(400, f"Já estás a medir {len(tr)} modelos do {prov} (máx. {MAX_TRACKED_PER_PROVIDER}, "
                                         "para não gastar os limites): pára de medir um deles primeiro.")
            tr.append(model)
    elif len(tr) <= 1:
        raise HTTPException(400, f"Tem de ficar pelo menos 1 modelo do {prov} em medição.")
    elif model in tr:
        tr.remove(model)
    save_db()
    return settings_view()


@app.get("/", response_class=HTMLResponse)
async def home_ui():
    page = BASE_DIR / "index.html"
    if page.exists():
        return HTMLResponse(page.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>gois.v1 API</h1>")


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    admin = is_admin_req(request)
    if not admin:
        rate_limit_public(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corpo inválido.")

    messages = [{"role": m.get("role", "user"), "content": flatten_content(m.get("content"))}
                for m in body.get("messages", []) if isinstance(m, dict)]
    if not messages:
        messages = [{"role": "user", "content": "Olá"}]
    messages = messages[-40:]
    payload = {"messages": messages, "temperature": body.get("temperature", 0.6)}

    if body.get("stream"):
        return StreamingResponse(stream_llm_request(payload, admin), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    data, errors = await make_llm_request(payload, admin)
    if data is not None:
        return JSONResponse(content=data)
    raise HTTPException(status_code=502, detail=OFFLINE_MSG if admin else PUBLIC_UNAVAILABLE_MSG)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
