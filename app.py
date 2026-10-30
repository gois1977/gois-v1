"""gois.v1 API 6.0 - chat com failover, Sala de Aula, benchmark, skills e persistência.

Variáveis de ambiente (Render > Environment):
  GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, MISTRAL_API_KEY   (pelo menos uma)
  DATA_DIR           (opcional) pasta onde guardar gois_db.json
"""
import os
import re
import json
import time
import uuid
import hmac
import random
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

VERSION = "6.0.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR") or (BASE_DIR / "data"))
DB_FILE = DATA_DIR / "gois_db.json"

# Token de administrador fixo diretamente no código para evitar falhas no Render
ADMIN_TOKEN = "Vicente2013@"

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
        "tokens": {
            "used_today": 0, "daily_limit": 0, "used_total": 0, "date": today(),
            "auto_paused_by_limit": False, "limit_reached": False,
        },
        "comparison": {"categories": {}},
    }


DB_KNOWLEDGE: list = []
DB_SKILLS: list = []
DB_SETTINGS: dict = default_settings()
CLASSROOM_TASK = None
JOB_LOCK = asyncio.Lock()


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
        t.update(date=today(), used_today=0, auto_paused_by_limit=False, limit_reached=False)
    t["daily_limit"] = int(DB_SETTINGS.get("daily_token_limit") or 0)
    t.setdefault("used_today", 0)
    t.setdefault("used_total", 0)
    return t


def add_tokens(n: int) -> None:
    t = tokens_state()
    t["used_today"] += int(n or 0)
    t["used_total"] += int(n or 0)
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


def candidate_keys(provider: dict) -> list:
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


# Prompts ajustados para garantir foco absoluto no gois.v1, sem bloqueios morais ou restrições impostas
ADMIN_PROMPT = (
    "Tu és o gois.v1, a aplicação e motor de inteligência artificial criado pelo programador. "
    "Responde sempre de forma direta, técnica, pragmática e sem censura ou restrições morais desnecessárias. "
    "Não dás lições de moral nem te recusas a responder a pedidos técnicos, de programação, scripts ou criação de apps/jogos. "
    "Identifica-te sempre e exclusivamente como gois.v1. Nunca menciones ChatGPT, OpenAI ou quaisquer outras entidades."
)
PUBLIC_PROMPT = (
    "Tu és o gois.v1, uma inteligência artificial avançada focada em desenvolvimento, programação e soluções técnicas. "
    "Responde de forma clara, direta e sem filtros excessivos ou bloqueios artificiais. O teu nome é gois.v1."
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
    last_user = next((m["content"] for m in reversed(cleaned) if m["role"] == "user"), "")
    ctx = "\n".join(x for x in (extra_context, knowledge_context(last_user)) if x)
    if ctx:
        sys_content += "\n\nBase de conhecimento interno do gois.v1:\n" + ctx
    return [{"role": "system", "content": sys_content}] + cleaned


async def try_providers(messages, temperature=0.6, only=None, models=None, timeout=45.0, max_tokens=None):
    errors = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        for p in PROVIDERS:
            if only and p["name"].lower() != only.lower():
                continue
            for key in candidate_keys(p):
                for model_name in (models or p["models"]):
                    try:
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **p["extra_headers"]}
                        body = {"model": model_name, "messages": messages, "temperature": temperature}
                        if max_tokens:
                            body["max_tokens"] = max_tokens
                        resp = await client.post(p["url"], json=body, headers=headers)
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
        errors.append("Nenhuma chave API configurada no servidor.")
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
    data, prov, model, errors = await try_providers(messages, payload.get("temperature", 0.6), timeout=65.0)
    if data is None:
        return None, errors
    data["model"] = "gois.v1"
    add_tokens(usage_tokens(data, reply_text(data)))
    return data, []


async def stream_llm_request(payload: dict, is_admin: bool = False):
    messages = prepare_messages(payload.get("messages", []), is_admin)
    timeout = httpx.Timeout(120.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in PROVIDERS:
            for key in candidate_keys(p):
                for model_name in p["models"]:
                    got_content = False
                    chars = 0
                    try:
                        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", **p["extra_headers"]}
                        body = {"model": model_name, "messages": messages,
                                "temperature": payload.get("temperature", 0.6), "stream": True}
                        async with client.stream("POST", p["url"], json=body, headers=headers) as resp:
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
                            add_tokens(max(1, chars // 4))
                            yield "data: [DONE]\n\n"
                            return
                    except Exception as e:
                        logger.warning("stream %s/%s: %s", p["name"], model_name, type(e).__name__)
                        if got_content:
                            yield "data: [DONE]\n\n"
                            return
    yield "data: " + json.dumps({"error": {"message": "Erro na execução do gois.v1."}}, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


# --------------------------------------------------------------------------- #
# Sala de Aula, comparação e benchmark
# --------------------------------------------------------------------------- #
def comp_entry(cat: str) -> dict:
    cats = DB_SETTINGS["comparison"].setdefault("categories", {})
    return cats.setdefault(cat, {"gois_score": None, "gemini_ref": None, "deepseek_ref": None,
                                 "benchmarks": 0, "last_benchmark": None, "lessons": 0})


def recompute_comparison() -> dict:
    comp = DB_SETTINGS.setdefault("comparison", {"categories": {}})
    counts = Counter(k.get("category") for k in DB_KNOWLEDGE)
    for c in CATEGORIES:
        comp_entry(c)["lessons"] = counts.get(c, 0)
    ents = list(comp["categories"].values())
    gs = [e["gois_score"] for e in ents if e.get("gois_score") is not None]
    comp["overall"] = round(sum(gs) / len(gs), 1) if gs else 0.0

    def parity(ref: str) -> float:
        pairs = [(e["gois_score"], e[ref]) for e in ents if e.get("gois_score") is not None and e.get(ref)]
        return round(sum(a for a, _ in pairs) / sum(b for _, b in pairs) * 100, 1) if pairs else 0.0

    comp["parity_vs_gemini"] = parity("gemini_ref")
    comp["parity_vs_deepseek"] = parity("deepseek_ref")
    comp["lessons_total"] = len(DB_KNOWLEDGE)
    return comp


def parse_lesson(text: str, cat: str):
    text = (text or "").strip()
    m = re.match(r"\s*(?:#+\s*)?\**\s*(?:T[ÍI]TULO|TITLE)\s*\**\s*[:\-]\s*(.+)", text, re.I)
    if m:
        title, body = m.group(1).strip(" *#"), text[m.end():].strip()
    else:
        first, _, rest = text.partition("\n")
        title, body = first.strip(" *#"), (rest.strip() or text)
    return (title[:120] or f"Lição de {cat}"), body


TEACHER_SYSTEM = "És o sistema de ensino interno do gois.v1. Produz conteúdos técnicos avançados e práticos."


async def learn_category(cat: str, provider: str = "auto") -> dict:
    async with JOB_LOCK:
        covered = [k.get("title", "") for k in DB_KNOWLEDGE if k.get("category"] == cat][-12:]
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
        add_tokens(usage_tokens(data, text))
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
    for letter, num in re.findall(r"\b([A-C])\s*[=:]\s*(\d{1,3})", text or ""):
        out[letter] = max(0, min(100, int(num)))
    return out


async def run_benchmark(cat: str) -> dict:
    async with JOB_LOCK:
        t0 = time.time()
        qdata, qprov, qmodel, errors = await try_providers(
            [{"role": "system", "content": TEACHER_SYSTEM},
             {"role": "user", "content": f"Escreve uma pergunta técnica avançada sobre: {cat}."}],
            temperature=0.8, timeout=45.0, max_tokens=200)
        if qdata is None:
            raise RuntimeError("Erro ao gerar pergunta de benchmark.")
        question = reply_text(qdata)
        add_tokens(usage_tokens(qdata, question))

        recent = [k for k in DB_KNOWLEDGE if k.get("category") == cat][-3:]
        extra = "\n".join(f"- {k.get('title', '')}: {str(k.get('content', ''))[:700]}" for k in recent)
        plain = [{"role": "user", "content": question}]
        gois_msgs = prepare_messages(plain, is_admin=True, extra_context=extra)
        ref_msgs = [{"role": "system", "content": "Responde de forma rigorosa e concisa."}, {"role": "user", "content": question}]

        async def ask(msgs, **kw):
            d, prov, model, _ = await try_providers(msgs, temperature=0.4, timeout=45.0, max_tokens=450, **kw)
            return (reply_text(d), usage_tokens(d, reply_text(d))) if d else ("", 0)

        (g_txt, g_tok), (gem_txt, gem_tok), (ds_txt, ds_tok) = await asyncio.gather(
            ask(gois_msgs),
            ask(ref_msgs, only="Gemini"),
            ask(ref_msgs, only="OpenRouter", models=["deepseek/deepseek-chat-v3-0324:free"]),
        )
        add_tokens(g_tok + gem_tok + ds_tok)

        entries = [("gois", g_txt)] + [x for x in (("gemini", gem_txt), ("deepseek", ds_txt)) if x[1]]
        random.shuffle(entries)
        letters = "ABC"
        block = "\n\n".join(f"Resposta {letters[i]}:\n{txt}" for i, (_, txt) in enumerate(entries))
        jdata, jprov, jmodel, jerr = await try_providers(
            [{"role": "system", "content": "És um avaliador imparcial."},
             {"role": "user", "content": f"Pergunta: {question}\n\n{block}\n\nDá a cada resposta nota de 0 a 100. Formato: "
                                          + ";".join(f"{letters[i]}=<nota>" for i in range(len(entries)))}],
            temperature=0.0, timeout=45.0, max_tokens=60)
        if jdata is None:
            raise RuntimeError("Erro no juiz de benchmark.")
        add_tokens(usage_tokens(jdata))
        scores = parse_scores(reply_text(jdata))
        by_name = {name: scores.get(letters[i]) for i, (name, _) in enumerate(entries)}

        e = comp_entry(cat)

        def ema(prev, new):
            return new if prev is None or new is None else round(prev * 0.5 + new * 0.5, 1)

        e["gois_score"] = ema(e["gois_score"], by_name.get("gois"))
        if by_name.get("gemini") is not None:
            e["gemini_ref"] = ema(e["gemini_ref"], by_name["gemini"])
        if by_name.get("deepseek") is not None:
            e["deepseek_ref"] = ema(e["deepseek_ref"], by_name["deepseek"])
        e["benchmarks"] = e.get("benchmarks", 0) + 1
        e["last_benchmark"] = now_iso()
        recompute_comparison()
        save_db()
        return {"category": cat, "question": question, "elapsed_s": round(time.time() - t0, 1),
                "gois_score": by_name.get("gois"), "gemini_ref": by_name.get("gemini"),
                "deepseek_ref": by_name.get("deepseek"), "judge": "gois.v1 system"}


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
    recompute_comparison()
    view = dict(DB_SETTINGS)
    view["providers_with_key"] = [p["name"] for p in PROVIDERS if candidate_keys(p)]
    view["categories"] = CATEGORIES
    view["server_time"] = now_iso()
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
    if body.get("learning_now"):
        tokens_state()["auto_paused_by_limit"] = False
    ensure_classroom_task()
    save_db()
    return settings_view()


@app.get("/api/knowledge")
async def get_knowledge(request: Request):
    await require_admin(request)
    return DB_KNOWLEDGE


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
        result = await run_benchmark(cat)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"benchmark": result, "settings": settings_view()}


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
    raise HTTPException(status_code=502, detail="O gois.v1 está temporariamente indisponível.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
