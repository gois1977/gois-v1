import os
import re
import time
import json
import hmac
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger("uvicorn.error")

BASE_DIR = Path(__file__).resolve().parent
SKILLS_SEED_FILE = Path(os.environ.get("SKILLS_FILE", str(BASE_DIR / "gois_skills_db.json")))
SYNC_TOKEN = os.environ.get("SYNC_TOKEN", "").strip()

MAX_KNOWLEDGE = 200
MAX_CONTENT_CHARS = 6000

DEFAULT_CATEGORIES = [
    "Tecnologia & Software",
    "Game Design & Unity",
    "Engenharia & AutoCAD",
    "Inteligência Artificial",
    "Ciências & Exatas",
    "Humanidades & Filosofia",
    "DevOps & Ferramentas",
]

UNIVERSAL_CLASSROOM_TOPICS = [
    ("Arquiteturas Python Assíncronas, Event Loops e Concorrência de Alto Desempenho", "Tecnologia & Software"),
    ("Design de APIs FastAPI, WebSockets em Tempo Real e Baixa Latência", "Tecnologia & Software"),
    ("Sistemas de Grelha (Tile Grids), Câmaras Isométricas e Map Editors 3D em Unity", "Game Design & Unity"),
    ("Character Controllers Avançados, Máquinas de Estado (FSM) e Física Customizada em C#", "Game Design & Unity"),
    ("Sistemas de Construção RTS, Spatial Hashing, Object Pooling e Shaders Otimizados", "Game Design & Unity"),
    ("Renderização 3D/2D no Browser com Three.js, WebGL e Otimização de Draw Calls", "Game Design & Unity"),
    ("Pipelines de Desenho Automatizado, Geometria Paramétrica e Algoritmos CAD", "Engenharia & AutoCAD"),
    ("Destilação de Conhecimento entre Modelos, RAG, Agentes Autónomos e Prompt Engineering", "Inteligência Artificial"),
    ("Física Computacional, Cálculo Vetorial, Matrizes de Transformação e Simulação", "Ciências & Exatas"),
    ("Teoria de Jogos, Sistemas de Decisão Estratégica e Economia de Jogos", "Humanidades & Filosofia"),
    ("Contentorização Docker, CI/CD, Deploy Cloud no Render e Otimização para Hardware Leve", "DevOps & Ferramentas"),
]

SERVER_SETTINGS = {
    "classroom_mode": True,
    "learning_now": False,
    "current_topic": "A aguardar próximo ciclo de estudo...",
    "current_teacher": "Rotação Automática (OpenRouter / Groq / Mistral)",
    "last_learned_title": "",
    "last_learned_at": "",
    "last_teacher": "",
    "teacher_provider": "auto",
    "topic_index": 0,
    "active_categories": list(DEFAULT_CATEGORIES),
}

SKILLS_DB: list = []
KNOWLEDGE_DB: list = []
RUNTIME_KEYS: dict = {}

GOIS_SYSTEM_PROMPT = """És o gois.v1, um assistente de engenharia de software, arquitetura de sistemas e desenvolvimento de jogos (Unity/C#, Python/FastAPI, WebGL/Three.js, automação e otimização para hardware leve).

Regras de comportamento:
1. Fala sempre em Português de Portugal (PT-PT) de forma natural, clara e direta, a menos que o utilizador peça outro idioma.
2. Adapta a resposta à pergunta: se o utilizador apenas cumprimentar ou fizer uma pergunta casual (ex.: "olá", "tudo bem?"), responde com naturalidade e simpatia numa ou duas frases curtas, sem listar competências, sem menus e sem jargão robótico.
3. NUNCA inventes telemetria falsa, latências inventadas (ex.: "Latência: 12ms"), utilização de CPU/RAM ou logs fictícios.
4. Quando o utilizador perguntar o que estás a aprender ou o que já aprendeste na Sala de Aula, responde de forma honesta com base apenas nos registos reais de aprendizagem presentes no teu contexto.
5. Quando o pedido for técnico ou de programação, sê rigoroso e completo: entrega código funcional, limpo, bem estruturado e sem omissões preguiçosas."""

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


# =====================================================================
# UTILITÁRIOS
# =====================================================================

def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def candidate_keys(provider: dict) -> list:
    keys = []
    for k in (RUNTIME_KEYS.get(provider["env"], ""), os.environ.get(provider["env"], "")):
        k = (k or "").strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def key_source(provider: dict):
    if RUNTIME_KEYS.get(provider["env"]):
        return "sincronizada"
    if os.environ.get(provider["env"], "").strip():
        return "environment"
    return None


def ordered_providers(preferred: str = ""):
    pref = (preferred or "").strip().lower()
    if not pref or pref == "auto":
        return list(PROVIDERS)
    first = [p for p in PROVIDERS if p["name"].lower() == pref]
    rest = [p for p in PROVIDERS if p["name"].lower() != pref]
    return first + rest


def check_token(request: Request):
    if not SYNC_TOKEN:
        return
    supplied = request.headers.get("x-sync-token", "").strip()
    auth = request.headers.get("authorization", "").strip()
    if not supplied and auth.lower().startswith("bearer "):
        supplied = auth[7:].strip()
    if not hmac.compare_digest(supplied.encode("utf-8"), SYNC_TOKEN.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Token em falta ou inválido.")


async def read_json(request: Request):
    try:
        return await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Corpo do pedido não é JSON válido.")


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


def clean_item(item: dict) -> dict:
    out = {
        "id": str(item.get("id") or "")[:120],
        "title": str(item.get("title") or "")[:200],
        "type": str(item.get("type") or "SKILL").upper()[:30],
        "content": str(item.get("content") or "")[:MAX_CONTENT_CHARS],
    }
    for extra in ("category", "teacher", "timestamp", "topic"):
        if item.get(extra):
            out[extra] = str(item[extra])[:160]
    if not out["id"]:
        out["id"] = f"item_{int(time.time() * 1000)}"
    return out


def normalize_items(raw) -> list:
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    items = []
    for it in raw:
        if isinstance(it, dict) and (it.get("content") or it.get("title")):
            cleaned = clean_item(it)
            if cleaned["id"]:
                items.append(cleaned)
    return items


def upsert(db: list, items: list, front: bool = False) -> int:
    count = 0
    for item in items:
        pos = next((i for i, x in enumerate(db) if x.get("id") == item["id"]), None)
        if pos is None:
            if front:
                db.insert(0, item)
            else:
                db.append(item)
        else:
            db[pos] = item
        count += 1
    return count


def load_seed_skills():
    if not SKILLS_SEED_FILE.is_file():
        return
    try:
        raw = json.loads(SKILLS_SEED_FILE.read_text(encoding="utf-8-sig"))
        n = upsert(SKILLS_DB, normalize_items(raw))
        logger.info(f"[SKILLS] {n} skills carregadas de {SKILLS_SEED_FILE.name}")
    except Exception as e:
        logger.error(f"[SKILLS] Erro a ler {SKILLS_SEED_FILE.name}: {e!r}")


KEY_FIELDS = ("key", "api_key", "apikey", "value", "token", "secret")
NAME_FIELDS = ("provider", "name", "service", "label", "env", "id")


def provider_env_for(name: str, key: str):
    n = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for p in PROVIDERS:
        if p["name"].lower() in n:
            return p["env"]
    if key.startswith("sk-or-"):
        return "OPENROUTER_API_KEY"
    if key.startswith("gsk_"):
        return "GROQ_API_KEY"
    return None


def extract_keys(data, hint: str = "", depth: int = 0) -> dict:
    found = {}
    if depth > 3:
        return found
    if isinstance(data, str):
        value = data.strip()
        if len(value) >= 8:
            env = provider_env_for(hint, value)
            if env:
                found[env] = value
    elif isinstance(data, dict):
        if data.get("active") is False or data.get("enabled") is False:
            return found
        status = str(data.get("status", "")).strip().lower()
        if status in ("inativo", "inactive", "disabled", "desativado", "off"):
            return found
        key_val = next((data[f] for f in KEY_FIELDS if isinstance(data.get(f), str)), None)
        if key_val is not None:
            name = " ".join(str(data[f]) for f in NAME_FIELDS if data.get(f)) or hint
            found.update(extract_keys(key_val, name, depth + 1))
        else:
            for k, v in data.items():
                found.update(extract_keys(v, str(k), depth + 1))
    elif isinstance(data, list):
        for item in data:
            found.update(extract_keys(item, hint, depth + 1))
    return found


# =====================================================================
# MOTOR DE IA COM FAILOVER E STREAMING REAL ENTRE FORNECEDORES
# =====================================================================

def build_provider_request(p: dict, key: str, payload: dict, stream: bool = False):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **p["extra_headers"],
    }
    body = {
        "model": p["model"],
        "messages": payload.get("messages", []),
        "temperature": payload.get("temperature", 0.6),
        "stream": stream,
    }
    if payload.get("max_tokens"):
        body["max_tokens"] = payload["max_tokens"]
    return headers, body


async def make_llm_request(payload: dict, preferred_provider: str = ""):
    """Pedido completo (sem streaming) com failover entre fornecedores."""
    errors = []
    timeout = httpx.Timeout(60.0, connect=15.0)
    providers = ordered_providers(preferred_provider)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in providers:
            keys = candidate_keys(p)
            if not keys:
                errors.append(f"{p['name']}: sem chave ({p['env']})")
                continue
            for key in keys:
                try:
                    headers, body = build_provider_request(p, key, payload, stream=False)
                    resp = await client.post(p["url"], json=body, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("choices"):
                            data["model"] = f"{p['name']} · {p['model']}"
                            data["provider_name"] = p["name"]
                            return data, errors
                        errors.append(f"{p['name']}: resposta 200 sem 'choices'")
                    else:
                        errors.append(f"{p['name']}: HTTP {resp.status_code}")
                except Exception as e:
                    errors.append(f"{p['name']}: {type(e).__name__}")
    return None, errors


async def stream_llm_request(payload: dict, preferred_provider: str = ""):
    """Streaming SSE real com failover automático antes do primeiro token."""
    errors = []
    timeout = httpx.Timeout(120.0, connect=15.0)
    providers = ordered_providers(preferred_provider)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for p in providers:
            keys = candidate_keys(p)
            if not keys:
                errors.append(f"{p['name']}: sem chave ({p['env']})")
                continue
            for key in keys:
                got_content = False
                try:
                    headers, body = build_provider_request(p, key, payload, stream=True)
                    async with client.stream("POST", p["url"], json=body, headers=headers) as resp:
                        if resp.status_code != 200:
                            errors.append(f"{p['name']}: HTTP {resp.status_code}")
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

                            if chunk.get("error"):
                                err_msg = chunk["error"]
                                if isinstance(err_msg, dict):
                                    err_msg = err_msg.get("message", str(err_msg))
                                errors.append(f"{p['name']}: {err_msg}")
                                if not got_content:
                                    break
                                yield "data: " + json.dumps({"error": {"message": str(err_msg)}}, ensure_ascii=False) + "\n\n"
                                yield "data: [DONE]\n\n"
                                return

                            if not sent_model:
                                chunk["model"] = f"{p['name']} · {p['model']}"
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
                        errors.append(f"{p['name']}: stream vazio")
                except Exception as e:
                    errors.append(f"{p['name']}: {type(e).__name__}")
                    if got_content:
                        yield "data: " + json.dumps({"error": {"message": f"Ligação interrompida ({p['name']})."}}, ensure_ascii=False) + "\n\n"
                        yield "data: [DONE]\n\n"
                        return

    detail = "Erro de Failover: nenhum fornecedor de IA respondeu com sucesso. " + " | ".join(errors)
    yield "data: " + json.dumps({"error": {"message": detail}}, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


# =====================================================================
# SALA DE AULA AUTÓNOMA (APRENDIZAGEM COM MODELOS REAIS)
# =====================================================================

CLASSROOM_LOCK = asyncio.Lock()


def parse_lesson_response(raw_text: str, default_title: str) -> tuple[str, str]:
    text = (raw_text or "").strip()
    # Se o modelo respondeu em JSON, extrai title/content; caso contrário, usa Markdown direto
    if text.startswith("{") or "```json" in text:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict) and obj.get("content"):
                    return str(obj.get("title") or default_title).strip(), str(obj["content"]).strip()
            except Exception:
                pass
    lines = text.splitlines()
    title = default_title
    if lines and lines[0].lstrip().startswith("#"):
        title = lines[0].lstrip("#").strip() or default_title
        content = "\n".join(lines[1:]).strip() or text
        return title, content
    return title, text


async def learn_one_topic(custom_topic: str = "", custom_category: str = "", preferred_provider: str = "") -> dict:
    """Liga-se a um modelo real (OpenRouter/Groq/Mistral), estuda um tópico e guarda na KNOWLEDGE_DB."""
    async with CLASSROOM_LOCK:
        active_cats = SERVER_SETTINGS.get("active_categories") or DEFAULT_CATEGORIES
        valid_topics = [t for t in UNIVERSAL_CLASSROOM_TOPICS if t[1] in active_cats] or UNIVERSAL_CLASSROOM_TOPICS

        idx = int(SERVER_SETTINGS.get("topic_index", 0))
        if custom_topic.strip():
            topic = custom_topic.strip()
            cat = custom_category.strip() or ( active_cats[0] if active_cats else "Tecnologia & Software" )
        else:
            topic, cat = valid_topics[idx % len(valid_topics)]
            SERVER_SETTINGS["topic_index"] = idx + 1

        # Rotação entre fornecedores ativos para aprender com vários modelos reais
        active_providers = [p["name"] for p in PROVIDERS if key_source(p)]
        pref = preferred_provider or SERVER_SETTINGS.get("teacher_provider", "auto")
        if (not pref or pref == "auto") and active_providers:
            chosen_provider = active_providers[idx % len(active_providers)]
        else:
            chosen_provider = pref if pref != "auto" else ""

        SERVER_SETTINGS["learning_now"] = True
        SERVER_SETTINGS["current_topic"] = f"[{cat}] {topic}"
        SERVER_SETTINGS["current_teacher"] = chosen_provider or "Auto"

        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "És um mentor sénior de engenharia que está a ensinar uma lição técnica avançada ao núcleo gois.v1.\n"
                    "Escreve em Português de Portugal (PT-PT) usando Markdown claro.\n"
                    "Começa na primeira linha com `# Título Conciso da Lição` e depois apresenta:\n"
                    "1. **Conceito-Chave e Arquitetura** (resumo direto e denso)\n"
                    "2. **Padrão Prático / Exemplo de Código Funcional** (curto, limpo e otimizado)\n"
                    "3. **Boas Práticas de Performance** (3 pontos práticos)."
                ),
            },
            {
                "role": "user",
                "content": f"Área: {cat}\nTópico a ensinar ao gois.v1: {topic}",
            },
        ]

        try:
            data, errors = await make_llm_request(
                {"messages": prompt_messages, "temperature": 0.5, "max_tokens": 900},
                preferred_provider=chosen_provider,
            )
            if not data:
                SERVER_SETTINGS["learning_now"] = False
                raise RuntimeError("Nenhum modelo respondeu: " + " | ".join(errors))

            raw_content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            teacher_model = data.get("model", "Modelo IA")
            title, body_md = parse_lesson_response(raw_content, topic)

            entry = clean_item({
                "id": f"k_{int(time.time() * 1000)}",
                "title": title,
                "category": cat,
                "topic": topic,
                "teacher": teacher_model,
                "timestamp": now_str(),
                "type": "LIÇÃO",
                "content": body_md,
            })
            upsert(KNOWLEDGE_DB, [entry], front=True)
            del KNOWLEDGE_DB[MAX_KNOWLEDGE:]

            SERVER_SETTINGS["last_learned_title"] = title
            SERVER_SETTINGS["last_learned_at"] = entry["timestamp"]
            SERVER_SETTINGS["last_teacher"] = teacher_model
            SERVER_SETTINGS["current_teacher"] = teacher_model
            logger.info(f"[SALA DE AULA] Lição aprendida com {teacher_model}: {title}")
            return entry
        finally:
            SERVER_SETTINGS["learning_now"] = False


async def classroom_background_worker():
    """Ciclo autónomo da Sala de Aula: estuda um tópico no arranque e depois a cada 75 segundos."""
    await asyncio.sleep(4)
    while True:
        try:
            if SERVER_SETTINGS.get("classroom_mode") and any(key_source(p) for p in PROVIDERS):
                await learn_one_topic()
        except Exception as e:
            logger.warning(f"[SALA DE AULA] ciclo falhou: {e!r}")
        await asyncio.sleep(75)


# =====================================================================
# CICLO DE VIDA
# =====================================================================

@asynccontextmanager
async def lifespan(_: FastAPI):
    for p in PROVIDERS:
        if os.environ.get(p["env"], "").strip():
            logger.info(f"[CHAVES] {p['name']}: {p['env']} encontrada")
        else:
            logger.warning(f"[CHAVES] {p['name']}: {p['env']} NÃO definida no Environment do Render")
    load_seed_skills()
    task = asyncio.create_task(classroom_background_worker())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="gois.v1 Command Center", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def build_system_message() -> str:
    system_parts = [GOIS_SYSTEM_PROMPT]

    if SKILLS_DB:
        blocks = [
            "Especialidades e diretrizes técnicas (aplica apenas quando relevante para a tarefa; não listes isto em cumprimentos casuais):"
        ]
        for s in SKILLS_DB:
            blocks.append(f"- {s.get('title', '')}: {s.get('content', '')}")
        system_parts.append("\n".join(blocks))

    if KNOWLEDGE_DB:
        blocks = [
            f"Registos reais aprendidos na Sala de Aula Autónoma ({len(KNOWLEDGE_DB)} lições aprendidas com modelos reais; usa este conhecimento quando relevante ou se o utilizador perguntar o que estiveste a aprender):"
        ]
        for k in KNOWLEDGE_DB[:8]:
            snippet = (k.get("content") or "").replace("\n", " ")[:260]
            teacher = k.get("teacher", "Modelo IA")
            blocks.append(f"- [{k.get('category', 'Geral')}] {k.get('title', '')} (ensinado por {teacher}): {snippet}...")
        system_parts.append("\n".join(blocks))

    return "\n\n".join(system_parts)


# =====================================================================
# ROTAS DE CHAT
# =====================================================================

@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    body = await read_json(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Corpo do pedido inválido.")
    messages = body.get("messages", []) or []

    formatted = [{"role": "system", "content": build_system_message()}]
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("user", "assistant"):
            formatted.append({"role": role, "content": flatten_content(m.get("content"))})

    payload = {"messages": formatted}
    if isinstance(body.get("temperature"), (int, float)):
        payload["temperature"] = body["temperature"]
    if isinstance(body.get("max_tokens"), int):
        payload["max_tokens"] = body["max_tokens"]
    preferred = str(body.get("provider") or "")

    if body.get("stream"):
        return StreamingResponse(
            stream_llm_request(payload, preferred_provider=preferred),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    data, errors = await make_llm_request(payload, preferred_provider=preferred)
    if data is not None:
        return JSONResponse(content=data)

    raise HTTPException(
        status_code=502,
        detail="Erro de Failover: nenhum dos fornecedores de IA respondeu com sucesso. "
        + " | ".join(errors),
    )


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "gois.v1", "object": "model", "owned_by": "gois"},
            *[{"id": p["model"], "object": "model", "owned_by": p["name"]} for p in PROVIDERS],
        ],
    }


# =====================================================================
# ROTAS DA SALA DE AULA, CONHECIMENTO, SKILLS E CHAVES
# =====================================================================

def category_metrics() -> dict:
    counts = {c: 0 for c in DEFAULT_CATEGORIES}
    for k in KNOWLEDGE_DB:
        cat = k.get("category") or "Tecnologia & Software"
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def full_status_payload() -> dict:
    return {
        **SERVER_SETTINGS,
        "all_categories": DEFAULT_CATEGORIES,
        "category_counts": category_metrics(),
        "total_knowledge": len(KNOWLEDGE_DB),
        "total_skills": len(SKILLS_DB),
        "providers": [
            {
                "name": p["name"],
                "model": p["model"],
                "configured": key_source(p) is not None,
                "source": key_source(p),
            }
            for p in PROVIDERS
        ],
        "providers_with_key": [p["name"] for p in PROVIDERS if key_source(p)],
    }


def apply_settings(body: dict):
    if "classroom_mode" in body:
        SERVER_SETTINGS["classroom_mode"] = bool(body["classroom_mode"])
    if "current_topic" in body:
        SERVER_SETTINGS["current_topic"] = str(body["current_topic"])[:300]
    if "teacher_provider" in body:
        SERVER_SETTINGS["teacher_provider"] = str(body["teacher_provider"])[:60]
    if isinstance(body.get("active_categories"), list):
        cats = [str(c)[:100] for c in body["active_categories"] if str(c).strip()][:50]
        if cats:
            SERVER_SETTINGS["active_categories"] = cats


@app.get("/api/settings")
@app.get("/api/classroom/status")
async def get_settings():
    return full_status_payload()


@app.post("/api/settings")
@app.post("/api/classroom/toggle")
@app.post("/api/settings/classroom")
@app.post("/api/settings/categories")
async def update_settings(request: Request):
    check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if request.url.path.endswith(("/toggle", "/classroom")) and "classroom_mode" not in body:
        SERVER_SETTINGS["classroom_mode"] = not SERVER_SETTINGS.get("classroom_mode", False)
    else:
        apply_settings(body)

    return {"status": "success", **full_status_payload()}


@app.post("/api/classroom/learn")
async def trigger_classroom_learn(request: Request):
    """Pede ao gois.v1 para aprender imediatamente um tópico com um modelo real."""
    check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if SERVER_SETTINGS.get("learning_now"):
        raise HTTPException(status_code=409, detail="A Sala de Aula já está a estudar um tópico neste momento. Aguarda alguns segundos.")

    try:
        entry = await learn_one_topic(
            custom_topic=str(body.get("topic") or ""),
            custom_category=str(body.get("category") or ""),
            preferred_provider=str(body.get("provider") or ""),
        )
        return {"status": "success", "lesson": entry, "settings": full_status_payload()}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/knowledge")
async def get_knowledge():
    return KNOWLEDGE_DB


@app.post("/api/knowledge")
async def add_knowledge(request: Request):
    check_token(request)
    body = await read_json(request)
    items = normalize_items(body if isinstance(body, list) else [body])
    n = upsert(KNOWLEDGE_DB, items, front=True)
    del KNOWLEDGE_DB[MAX_KNOWLEDGE:]
    return {"status": "success", "imported": n, "total": len(KNOWLEDGE_DB)}


@app.delete("/api/knowledge/{item_id}")
async def delete_knowledge(item_id: str, request: Request):
    check_token(request)
    before = len(KNOWLEDGE_DB)
    KNOWLEDGE_DB[:] = [k for k in KNOWLEDGE_DB if k.get("id") != item_id]
    if len(KNOWLEDGE_DB) == before:
        raise HTTPException(status_code=404, detail="Lição não encontrada.")
    return {"status": "success", "total": len(KNOWLEDGE_DB)}


@app.get("/api/knowledge/export")
async def export_knowledge():
    lines = ["# BASE DE CONHECIMENTO, SALA DE AULA & SKILLS — gois.v1\n"]
    lines.append(f"*Exportado em {now_str()} — {len(SKILLS_DB)} skills, {len(KNOWLEDGE_DB)} lições aprendidas.*\n")
    lines.append("## 1. Skills & Diretrizes do Núcleo\n")
    for s in SKILLS_DB:
        lines.append(f"### [{s.get('type', 'SKILL')}] {s.get('title', '')}\n{s.get('content', '')}\n")
    lines.append("\n---\n\n## 2. Lições Aprendidas na Sala de Aula Autónoma\n")
    for k in KNOWLEDGE_DB:
        meta = f"Categoria: **{k.get('category', 'Geral')}** · Modelo Professor: `{k.get('teacher', 'IA')}` · Data: {k.get('timestamp', '')}"
        lines.append(f"### {k.get('title', '')}\n*{meta}*\n\n{k.get('content', '')}\n\n---\n")
    text = "\n".join(lines)
    return StreamingResponse(
        iter([text]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gois_v1_conhecimento_completo.md"'},
    )


@app.get("/api/skills")
async def get_skills():
    return SKILLS_DB


@app.post("/api/skills")
async def add_skills(request: Request):
    check_token(request)
    body = await read_json(request)
    items = normalize_items(body if isinstance(body, list) else [body])
    n = upsert(SKILLS_DB, items, front=True)
    return {"status": "success", "imported": n, "total": len(SKILLS_DB)}


@app.delete("/api/skills/{skill_id}")
async def delete_skill(skill_id: str, request: Request):
    check_token(request)
    before = len(SKILLS_DB)
    SKILLS_DB[:] = [s for s in SKILLS_DB if s.get("id") != skill_id]
    if len(SKILLS_DB) == before:
        raise HTTPException(status_code=404, detail="Skill não encontrada.")
    return {"status": "success", "total": len(SKILLS_DB)}


@app.get("/api/keys/status")
async def keys_status():
    return {
        p["name"]: {
            "configured": key_source(p) is not None,
            "source": key_source(p),
            "model": p["model"],
        }
        for p in PROVIDERS
    }


@app.post("/api/keys")
async def set_runtime_key(request: Request):
    check_token(request)
    body = await read_json(request)
    found = extract_keys(body)
    if not found:
        raise HTTPException(status_code=400, detail="Nenhuma chave válida reconhecida para OpenRouter, Groq ou Mistral.")
    for env, val in found.items():
        RUNTIME_KEYS[env] = val
    return {
        "status": "success",
        "updated": [p["name"] for p in PROVIDERS if p["env"] in found],
        "providers_with_key": [p["name"] for p in PROVIDERS if key_source(p)],
    }


KNOWN_SYNC_FIELDS = {
    "skills", "skills_db", "knowledge", "knowledge_db", "keys", "api_keys", "chaves",
    "providers", "settings", "classroom_mode", "current_topic", "replace",
}
KEY_CONTAINERS = ("keys", "api_keys", "chaves", "providers")


@app.post("/api/sync/import")
@app.post("/api/sync")
@app.post("/sync/import")
async def sync_import(request: Request):
    check_token(request)
    payload = await read_json(request)
    if isinstance(payload, list):
        payload = {"skills": payload, "keys": payload}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Corpo do pedido inválido.")

    received = sorted(str(k) for k in payload.keys())
    warnings = []
    imported = {"keys": [], "skills": 0, "knowledge": 0, "settings": False}

    found = {}
    for field in KEY_CONTAINERS:
        if field in payload:
            got = extract_keys(payload[field])
            if not got:
                warnings.append(f"Campo '{field}' recebido, mas nenhuma chave reconhecida.")
            found.update(got)
    found.update(extract_keys({k: v for k, v in payload.items() if isinstance(v, str)}))
    for env, value in found.items():
        RUNTIME_KEYS[env] = value
    imported["keys"] = sorted(p["name"] for p in PROVIDERS if p["env"] in found)

    skills_raw = payload.get("skills", payload.get("skills_db"))
    if skills_raw is not None:
        items = normalize_items(skills_raw)
        if payload.get("replace") is True:
            SKILLS_DB.clear()
        imported["skills"] = upsert(SKILLS_DB, items)

    knowledge_raw = payload.get("knowledge", payload.get("knowledge_db"))
    if knowledge_raw is not None:
        items = normalize_items(knowledge_raw)
        imported["knowledge"] = upsert(KNOWLEDGE_DB, items, front=True)
        del KNOWLEDGE_DB[MAX_KNOWLEDGE:]

    settings_in = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    for field in ("classroom_mode", "current_topic", "teacher_provider"):
        if field in payload:
            settings_in = {**settings_in, field: payload[field]}
    if settings_in:
        apply_settings(settings_in)
        imported["settings"] = True

    return {
        "status": "success",
        "ok": True,
        "imported": imported,
        "totals": {"skills": len(SKILLS_DB), "knowledge": len(KNOWLEDGE_DB)},
        "warnings": warnings,
    }


@app.get("/api/sync/export")
async def sync_export(request: Request):
    check_token(request)
    return {
        "skills": SKILLS_DB,
        "knowledge": KNOWLEDGE_DB,
        "settings": full_status_payload(),
    }


@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "system": "gois.v1",
        "classroom_mode": SERVER_SETTINGS["classroom_mode"],
        "learning_now": SERVER_SETTINGS["learning_now"],
        "current_topic": SERVER_SETTINGS["current_topic"],
        "last_learned_title": SERVER_SETTINGS["last_learned_title"],
        "last_teacher": SERVER_SETTINGS["last_teacher"],
        "skills": len(SKILLS_DB),
        "knowledge": len(KNOWLEDGE_DB),
        "providers_with_key": [p["name"] for p in PROVIDERS if key_source(p)],
    }


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
            --warn: #f59e0b; --danger: #ef4444;
            --text: #f3f4f6; --text-muted: #9ca3af; --user-msg: #1e293b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: var(--bg); color: var(--text);
            height: 100dvh; display: flex; flex-direction: column; overflow: hidden;
        }
        header {
            background: var(--panel); border-bottom: 1px solid var(--border);
            padding: 10px 16px; display: flex; align-items: center;
            justify-content: space-between; z-index: 10; flex-wrap: wrap; gap: 10px;
        }
        .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; color: #fff; font-size: 1.05rem; }
        .status-dot { width: 10px; height: 10px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 10px var(--accent-green); }
        .cloud-badge {
            font-size: 0.74rem; background: rgba(16, 185, 129, 0.14); color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.5); padding: 4px 10px; border-radius: 99px; cursor: pointer;
        }
        .nav-tabs { display: flex; gap: 6px; background: var(--panel-alt); padding: 4px; border-radius: 8px; border: 1px solid var(--border); flex-wrap: wrap; }
        .nav-btn {
            background: transparent; border: none; color: var(--text-muted);
            padding: 7px 14px; border-radius: 6px; font-size: 0.82rem; font-weight: 600;
            cursor: pointer; transition: 0.18s;
        }
        .nav-btn:hover { color: #fff; background: rgba(255,255,255,0.05); }
        .nav-btn.active { background: var(--accent); color: #fff; }
        .app-view { flex: 1; display: none; flex-direction: column; overflow: hidden; }
        .app-view.active { display: flex; }

        /* Sub-banner no topo do chat */
        .sub-banner {
            background: var(--panel-alt); border-bottom: 1px solid var(--border);
            padding: 7px 16px; display: flex; justify-content: space-between; align-items: center;
            font-size: 0.8rem; color: var(--text-muted); gap: 10px; flex-wrap: wrap;
        }
        .sub-banner-left { cursor: pointer; display: flex; align-items: center; gap: 8px; }
        .sub-banner-left:hover { color: #fff; }

        /* Chat */
        #chat-container { flex: 1; overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 14px; }
        .msg { max-width: 88%; padding: 14px 16px; border-radius: 12px; font-size: 0.94rem; line-height: 1.55; word-wrap: break-word; }
        .msg.user { align-self: flex-end; background: var(--user-msg); border: 1px solid #334155; }
        .msg.assistant { align-self: flex-start; background: var(--panel); border: 1px solid var(--border); width: 100%; max-width: 920px; }
        .msg-meta { font-size: 0.72rem; color: var(--text-muted); margin-bottom: 6px; font-weight: 600; display: flex; justify-content: space-between; }
        .msg.assistant pre { background: #05070c; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; overflow-x: auto; margin: 10px 0; position: relative; }
        .msg.assistant code { color: #a7f3d0; font-family: Consolas, monospace; font-size: 0.88rem; }
        .msg.assistant p { margin-bottom: 8px; }
        .msg.assistant p:last-child { margin-bottom: 0; }
        .msg.assistant ul, .msg.assistant ol { margin: 6px 0 10px 22px; }
        .welcome-box { max-width: 720px; margin: auto; text-align: center; padding: 24px; }
        .welcome-box h1 { font-size: 1.7rem; margin-bottom: 8px; color: #fff; }
        .welcome-box p { color: var(--text-muted); font-size: 0.92rem; margin-bottom: 20px; }
        .prompt-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px; text-align: left; }
        .prompt-card { background: var(--panel); border: 1px solid var(--border); padding: 13px 15px; border-radius: 9px; font-size: 0.85rem; cursor: pointer; transition: 0.2s; }
        .prompt-card:hover { border-color: var(--accent); background: var(--panel-alt); }
        .input-bar { background: var(--panel); border-top: 1px solid var(--border); padding: 12px 16px; display: flex; gap: 10px; align-items: flex-end; }
        textarea#user-input {
            flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text);
            border-radius: 10px; padding: 11px 14px; font-size: 0.95rem; resize: none; outline: none; font-family: inherit;
        }
        textarea#user-input:focus { border-color: var(--accent); }
        .btn-send { background: var(--accent-green); color: #04130e; border: none; border-radius: 10px; padding: 0 20px; height: 42px; font-weight: 700; cursor: pointer; }
        .btn-stop { background: var(--danger); color: #fff; border: none; border-radius: 10px; padding: 0 18px; height: 42px; font-weight: 700; cursor: pointer; display: none; }

        /* Secções internas */
        .view-content { padding: 20px; overflow-y: auto; flex: 1; max-width: 1040px; width: 100%; margin: 0 auto; }
        .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 16px; }
        .input-box {
            width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text);
            padding: 10px 13px; border-radius: 8px; font-size: 0.88rem; margin-bottom: 10px; outline: none; font-family: inherit;
        }
        .input-box:focus { border-color: var(--accent); }
        .btn-action { background: var(--accent-green); color: #04130e; border: none; padding: 9px 16px; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 0.84rem; }
        .btn-blue { background: var(--accent); color: #fff; border: none; padding: 9px 16px; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 0.84rem; }
        .btn-outline { background: var(--bg); color: var(--text); border: 1px solid var(--border); padding: 7px 12px; border-radius: 7px; font-size: 0.8rem; cursor: pointer; }
        .btn-outline:hover { border-color: var(--accent); }

        .provider-pills { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
        .provider-pill {
            background: var(--bg); border: 1px solid var(--border); padding: 6px 12px;
            border-radius: 8px; font-size: 0.8rem; display: flex; align-items: center; gap: 6px;
        }
        .cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 8px; margin: 10px 0 14px; }
        .cat-checkbox { display: flex; align-items: center; gap: 8px; background: var(--bg); border: 1px solid var(--border); padding: 8px 10px; border-radius: 8px; font-size: 0.8rem; cursor: pointer; }
        .skill-badge { font-size: 0.7rem; padding: 3px 8px; border-radius: 99px; font-weight: 700; background: rgba(59,130,246,0.2); color: #93c5fd; border: 1px solid var(--accent); }
        .directive-badge { font-size: 0.7rem; padding: 3px 8px; border-radius: 99px; font-weight: 700; background: rgba(245,158,11,0.2); color: #fcd34d; border: 1px solid var(--warn); }
        .teacher-badge { font-size: 0.72rem; padding: 3px 9px; border-radius: 99px; font-weight: 600; background: rgba(16,185,129,0.16); color: #34d399; border: 1px solid rgba(16,185,129,0.45); }
        .lesson-card { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
        .lesson-body { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); font-size: 0.9rem; line-height: 1.55; }
        .lesson-body pre { background: #05070c; border: 1px solid #1e293b; border-radius: 8px; padding: 10px; overflow-x: auto; margin: 8px 0; }
        .lesson-body code { color: #a7f3d0; font-family: Consolas, monospace; }
    </style>
</head>
<body>
    <header>
        <div class="brand">
            <div class="status-dot"></div>
            <span>gois.v1 | Office & Command Center</span>
            <span id="header-status-badge" class="cloud-badge" onclick="refreshAll()" title="Clica para atualizar estado">☁️ Online</span>
        </div>
        <div class="nav-tabs">
            <button class="nav-btn active" id="tab-btn-chat" onclick="switchView('view-chat', this)">💬 Chat</button>
            <button class="nav-btn" id="tab-btn-classroom" onclick="switchView('view-classroom', this)">🏫 Sala de Aula (Modelos Reais)</button>
            <button class="nav-btn" id="tab-btn-skills" onclick="switchView('view-skills', this)">🧠 Skills & Diretrizes</button>
            <button class="nav-btn" id="tab-btn-keys" onclick="switchView('view-keys', this)">🔑 Chaves API</button>
        </div>
    </header>

    <!-- VISTA 1: CHAT -->
    <div id="view-chat" class="app-view active">
        <div class="sub-banner">
            <div class="sub-banner-left" onclick="document.getElementById('tab-btn-classroom').click()">
                <span id="banner-classroom-text">🏫 Sala de Aula Autónoma: A verificar estado...</span>
            </div>
            <div style="display:flex; gap:6px;">
                <button class="btn-outline" onclick="exportChat()">⬇️ Exportar Chat</button>
                <button class="btn-outline" onclick="clearChat()">🗑️ Nova Conversa</button>
            </div>
        </div>
        <div id="chat-container">
            <div class="welcome-box" id="welcome-box">
                <h1>gois.v1 Command Center</h1>
                <p>Chat em Streaming SSE com Failover Multi-Modelo (OpenRouter, Groq, Mistral) e Sala de Aula Autónoma ativa.</p>
                <div class="prompt-grid">
                    <div class="prompt-card" onclick="usePrompt('Cria um editor de mapas 3D isométrico em Three.js com grelha de construção estilo estratégia')">🏗️ Editor de Mapas 3D (Tile Grid)</div>
                    <div class="prompt-card" onclick="usePrompt('Gera um Character Controller em C# para Unity com dash, coyote time e máquina de estados')">⚡ Script C# Unity: Character Controller</div>
                    <div class="prompt-card" onclick="usePrompt('Cria uma arquitetura FastAPI assíncrona com WebSockets em tempo real')">⚙️ Arquitetura FastAPI Assíncrona</div>
                    <div class="prompt-card" onclick="usePrompt('O que tens estado a aprender recentemente na Sala de Aula Autónoma?')">🏫 O que aprendeste na Sala de Aula?</div>
                </div>
            </div>
        </div>
        <div class="input-bar">
            <textarea id="user-input" rows="1" placeholder="Escreve uma mensagem ou instrução técnica... (Enter envia, Shift+Enter muda de linha)"></textarea>
            <button id="stop-btn" class="btn-stop" onclick="stopStream()">Parar</button>
            <button id="send-btn" class="btn-send" onclick="sendMessage()">Enviar</button>
        </div>
    </div>

    <!-- VISTA 2: SALA DE AULA (MODELOS REAIS) -->
    <div id="view-classroom" class="app-view">
        <div class="view-content">
            <div class="card" style="border-color: var(--accent-green);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:10px;">
                    <div>
                        <h3 style="color: var(--accent-green); margin-bottom:4px;">🏫 Sala de Aula Autónoma — Aprendizagem com Modelos Reais</h3>
                        <p style="font-size:0.82rem; color:var(--text-muted);">O teu <b>gois.v1</b> liga-se a modelos reais (DeepSeek, Llama 3.3 70B, Mistral) para estudar tópicos técnicos ciclicamente ou a pedido.</p>
                    </div>
                    <div style="display:flex; gap:8px; flex-wrap:wrap;">
                        <button class="btn-outline" onclick="loadClassroomUI()">🔄 Atualizar</button>
                        <button class="btn-outline" onclick="window.open('/api/knowledge/export', '_blank')">⬇️ Exportar Tudo (.MD)</button>
                        <button class="btn-action" onclick="toggleClassroom()">Ligar / Desligar Sala de Aula</button>
                    </div>
                </div>

                <div style="background:var(--bg); border:1px solid var(--border); padding:12px 14px; border-radius:10px; margin-bottom:12px; font-size:0.86rem; line-height:1.6;">
                    <div><b>Estado da Sala de Aula:</b> <span id="classroom-status-text">A carregar...</span></div>
                    <div><b>Tópico em Estudo / Foco:</b> <span id="active-topic-display" style="color:var(--accent);">—</span></div>
                    <div><b>Última Lição Aprendida:</b> <span id="last-learned-display" style="color:#34d399;">—</span></div>
                </div>

                <div style="font-size:0.82rem; font-weight:700; margin-bottom:4px;">🤖 Modelos Reais Ligados (Professores do gois.v1):</div>
                <div id="providers-pills" class="provider-pills"></div>

                <!-- Aprender Agora com Modelo Real -->
                <div style="background:var(--panel-alt); border:1px solid var(--border); border-radius:10px; padding:14px; margin-top:14px;">
                    <div style="font-size:0.86rem; font-weight:700; color:#fff; margin-bottom:8px;">⚡ Estudar Tópico Agora com Modelo Real</div>
                    <div style="display:grid; grid-template-columns: 180px 220px 1fr auto; gap:8px; align-items:center;">
                        <select id="learn-provider" class="input-box" style="margin:0;">
                            <option value="auto">Rotação Automática</option>
                            <option value="OpenRouter">OpenRouter (DeepSeek)</option>
                            <option value="Groq">Groq (Llama 3.3 70B)</option>
                            <option value="Mistral">Mistral (Mistral Small)</option>
                        </select>
                        <select id="learn-category" class="input-box" style="margin:0;"></select>
                        <input id="learn-topic" class="input-box" style="margin:0;" type="text" placeholder="Tópico específico (ou deixa em branco para automático)..." />
                        <button id="btn-learn-now" class="btn-blue" onclick="learnNow()">⚡ Aprender Agora</button>
                    </div>
                    <div id="learn-feedback" style="font-size:0.8rem; margin-top:6px; color:var(--warn);"></div>
                </div>

                <div style="margin-top:16px;">
                    <span style="font-size:0.82rem; font-weight:700;">Categorias de Estudo Autónomo (ON/OFF):</span>
                    <div id="categories-container" class="cat-grid"></div>
                </div>

                <h4 style="font-size:0.85rem; margin-top:10px; margin-bottom:8px; color:var(--accent);">📊 Progresso e Distribuição do que já aprendeu por Categoria</h4>
                <div id="category-progress-container"></div>
            </div>

            <div class="card">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <h3>📚 Tudo o que o gois.v1 já aprendeu (<span id="knowledge-count">0</span> lições)</h3>
                    <button class="btn-outline" onclick="window.open('/api/knowledge/export', '_blank')">⬇️ Descarregar Base Completa (.MD)</button>
                </div>
                <div id="knowledge-list-container">A carregar lições aprendidas...</div>
            </div>
        </div>
    </div>

    <!-- VISTA 3: SKILLS & DIRETRIZES -->
    <div id="view-skills" class="app-view">
        <div class="view-content">
            <div class="card">
                <h3 style="color: var(--accent); margin-bottom: 10px;">🧠 Ensinar Nova Skill ou Diretriz Permanente ao Núcleo</h3>
                <div style="display:grid; grid-template-columns: 220px 1fr; gap:10px;">
                    <div>
                        <label style="font-size: 0.78rem; color: var(--text-muted);">Tipo:</label>
                        <select id="skill-type" class="input-box">
                            <option value="SKILL">SKILL (Capacidade Técnica)</option>
                            <option value="DIRECTRIZ">DIRECTRIZ (Regra / Postura)</option>
                        </select>
                    </div>
                    <div>
                        <label style="font-size: 0.78rem; color: var(--text-muted);">Título:</label>
                        <input id="skill-title" class="input-box" type="text" placeholder="ex: Arquitetura ECS e Job System em Unity" />
                    </div>
                </div>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Conteúdo / Instrução Técnica:</label>
                <textarea id="skill-content" class="input-box" rows="3" placeholder="Descreve o conhecimento técnico, padrões ou diretrizes..."></textarea>
                <button class="btn-action" onclick="addSkillRecord()">+ Guardar no Núcleo</button>
            </div>
            <div class="card">
                <h3 style="margin-bottom: 12px;">Arsenal de Skills & Diretrizes Ativas (<span id="skills-count">0</span>)</h3>
                <div id="skills-list-container">A carregar skills...</div>
            </div>
        </div>
    </div>

    <!-- VISTA 4: CHAVES API -->
    <div id="view-keys" class="app-view">
        <div class="view-content">
            <div class="card">
                <h3 style="color: var(--accent); margin-bottom: 6px;">🔑 Configurar Chaves API dos Modelos Reais</h3>
                <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 12px;">Estas chaves alimentam tanto o Chat como a Sala de Aula Autónoma (com failover automático entre fornecedores).</p>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Fornecedor:</label>
                <select id="key-provider" class="input-box">
                    <option value="OpenRouter">OpenRouter (DeepSeek Chat)</option>
                    <option value="Groq">Groq (Llama 3.3 70B Versatile)</option>
                    <option value="Mistral">Mistral AI (Mistral Small Latest)</option>
                </select>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Chave API:</label>
                <input id="key-secret" class="input-box" type="password" placeholder="sk-or-... ou gsk_..." />
                <button class="btn-action" onclick="saveApiKey()">+ Ativar Chave no Servidor</button>
                <div id="key-feedback" style="font-size:0.82rem; margin-top:8px;"></div>
            </div>
            <div class="card">
                <h3 style="margin-bottom: 12px;">Estado dos Fornecedores no Servidor</h3>
                <div id="keys-status-container">A verificar...</div>
            </div>
        </div>
    </div>

    <script>
        const chatContainer = document.getElementById('chat-container');
        const userInput = document.getElementById('user-input');
        const sendBtn = document.getElementById('send-btn');
        const stopBtn = document.getElementById('stop-btn');
        const allCategories = ["Tecnologia & Software", "Game Design & Unity", "Engenharia & AutoCAD", "Inteligência Artificial", "Ciências & Exatas", "Humanidades & Filosofia", "DevOps & Ferramentas"];

        let messages = JSON.parse(localStorage.getItem('gois_chat_history') || '[]');
        let cachedKnowledge = JSON.parse(localStorage.getItem('gois_knowledge_backup') || '[]');
        let abortCtrl = null;

        // Preenche dropdown de categorias
        const learnCatSel = document.getElementById('learn-category');
        allCategories.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c; opt.textContent = c;
            learnCatSel.appendChild(opt);
        });

        function saveChatHistory() {
            localStorage.setItem('gois_chat_history', JSON.stringify(messages.slice(-50)));
        }

        function renderSavedChat() {
            if (!messages.length) return;
            const wb = document.getElementById('welcome-box');
            if (wb) wb.remove();
            messages.forEach(m => appendMsg(m.role, m.content, m.model || ''));
        }

        function switchView(viewId, btn) {
            document.querySelectorAll('.app-view').forEach(v => v.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(viewId).classList.add('active');
            btn.classList.add('active');
            if (viewId === 'view-classroom') loadClassroomUI();
            if (viewId === 'view-skills') loadSkillsUI();
            if (viewId === 'view-keys') loadKeysUI();
        }

        function usePrompt(text) {
            userInput.value = text;
            sendMessage();
        }

        userInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 130) + 'px';
        });
        userInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        function addCopyButtons(container) {
            container.querySelectorAll('pre').forEach(pre => {
                if (pre.querySelector('.copy-code-btn')) return;
                const btn = document.createElement('button');
                btn.className = 'btn-outline copy-code-btn';
                btn.style.cssText = 'position:absolute; top:6px; right:6px; padding:2px 8px; font-size:0.72rem; background:#111827;';
                btn.innerText = 'Copiar';
                btn.onclick = () => {
                    const code = pre.querySelector('code') ? pre.querySelector('code').innerText : pre.innerText;
                    navigator.clipboard.writeText(code.trim());
                    btn.innerText = 'Copiado!';
                    setTimeout(() => btn.innerText = 'Copiar', 1500);
                };
                pre.appendChild(btn);
            });
        }

        function appendMsg(role, content, modelName = '') {
            const div = document.createElement('div');
            div.className = 'msg ' + role;
            const meta = document.createElement('div');
            meta.className = 'msg-meta';
            meta.innerHTML = role === 'user'
                ? '<span>Tu</span>'
                : `<span>gois.v1</span><span class="model-tag">${modelName}</span>`;
            const body = document.createElement('div');
            body.className = 'msg-body';
            if (role === 'assistant') {
                body.innerHTML = marked.parse(content || '');
                addCopyButtons(body);
            } else {
                body.textContent = content;
            }
            div.appendChild(meta);
            div.appendChild(body);
            chatContainer.appendChild(div);
            chatContainer.scrollTop = chatContainer.scrollHeight;
            return { div, body, meta };
        }

        function stopStream() {
            if (abortCtrl) abortCtrl.abort();
        }

        async function sendMessage() {
            const text = userInput.value.trim();
            if (!text || abortCtrl) return;
            const wb = document.getElementById('welcome-box');
            if (wb) wb.remove();
            userInput.value = '';
            userInput.style.height = 'auto';
            sendBtn.style.display = 'none';
            stopBtn.style.display = 'inline-block';

            messages.push({ role: 'user', content: text });
            saveChatHistory();
            appendMsg('user', text);

            const botMsg = appendMsg('assistant', 'A pensar...');
            let replyText = '';
            let replyModel = '';
            abortCtrl = new AbortController();

            try {
                const res = await fetch('/v1/chat/completions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                    body: JSON.stringify({ messages: messages.slice(-24), stream: true }),
                    signal: abortCtrl.signal
                });
                if (!res.ok) {
                    const errTxt = await res.text();
                    throw new Error(errTxt || ('HTTP ' + res.status));
                }
                const reader = res.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\\n');
                    buffer = lines.pop() || '';
                    for (const raw of lines) {
                        const line = raw.trim();
                        if (!line.startsWith('data:')) continue;
                        const dataStr = line.slice(5).trim();
                        if (!dataStr || dataStr === '[DONE]') continue;
                        try {
                            const obj = JSON.parse(dataStr);
                            if (obj.error) throw new Error(obj.error.message || JSON.stringify(obj.error));
                            if (obj.model && !replyModel) {
                                replyModel = obj.model;
                                botMsg.meta.innerHTML = `<span>gois.v1</span><span class="model-tag">${replyModel}</span>`;
                            }
                            const delta = ((obj.choices || [])[0] || {}).delta || {};
                            if (delta.content) {
                                replyText += delta.content;
                                botMsg.body.innerHTML = marked.parse(replyText);
                                chatContainer.scrollTop = chatContainer.scrollHeight;
                            }
                        } catch (innerErr) {
                            if (innerErr.message && !innerErr.message.includes('JSON')) throw innerErr;
                        }
                    }
                }
                addCopyButtons(botMsg.body);
                messages.push({ role: 'assistant', content: replyText, model: replyModel });
                saveChatHistory();
            } catch (err) {
                if (err.name === 'AbortError') {
                    replyText = replyText ? (replyText + '\\n\\n*(resposta interrompida)*') : '*(pedido cancelado)*';
                    botMsg.body.innerHTML = marked.parse(replyText);
                } else {
                    botMsg.body.innerHTML = `<span style="color:var(--danger);"><b>Erro:</b> ${err.message || err}</span>`;
                }
            } finally {
                abortCtrl = null;
                stopBtn.style.display = 'none';
                sendBtn.style.display = 'inline-block';
            }
        }

        function clearChat() {
            if (abortCtrl) abortCtrl.abort();
            messages = [];
            saveChatHistory();
            chatContainer.innerHTML = '';
        }

        function exportChat() {
            if (!messages.length) return alert('Ainda não há conversa para exportar.');
            const md = messages.map(m => (m.role === 'user' ? '## Tu\\n\\n' : '## gois.v1\\n\\n') + m.content).join('\\n\\n');
            const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
            const a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'conversa_gois_v1.md';
            a.click();
        }

        // --- SALA DE AULA & MODELOS REAIS ---
        async function loadClassroomUI() {
            try {
                const [stRes, knRes] = await Promise.all([fetch('/api/settings'), fetch('/api/knowledge')]);
                const st = await stRes.json();
                let kn = await knRes.json();

                // Se o Render reiniciou e perdeu memória, restaura automaticamente o backup do browser
                if (Array.isArray(kn) && kn.length === 0 && cachedKnowledge.length > 0) {
                    await fetch('/api/sync/import', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ knowledge: cachedKnowledge })
                    });
                    const r2 = await fetch('/api/knowledge');
                    kn = await r2.json();
                }
                if (Array.isArray(kn) && kn.length > 0) {
                    cachedKnowledge = kn;
                    localStorage.setItem('gois_knowledge_backup', JSON.stringify(kn.slice(0, 150)));
                }

                // Header & Banner
                const provs = (st.providers_with_key || []).join(', ') || 'Sem chaves';
                document.getElementById('header-status-badge').innerText = `☁️ Online · ${kn.length} lições · ${provs}`;
                const lastTitle = st.last_learned_title || (kn[0] ? kn[0].title : 'A iniciar primeiro ciclo...');
                const lastTeacher = st.last_teacher || (kn[0] ? kn[0].teacher : '');
                document.getElementById('banner-classroom-text').innerHTML =
                    `🏫 <b>Sala de Aula:</b> ${st.classroom_mode ? '<span style="color:#34d399;">ATIVA</span>' : '<span style="color:#ef4444;">PAUSADA</span>'} · <b>${kn.length} lições aprendidas</b> · Última: <i>${lastTitle}</i> ${lastTeacher ? '(' + lastTeacher + ')' : ''} — <u>ver Sala de Aula</u>`;

                const statusEl = document.getElementById('classroom-status-text');
                if (st.learning_now) {
                    statusEl.innerHTML = `<span style="color:var(--warn);">⚡ A APRENDER AGORA COM MODELO REAL (${st.current_teacher || 'IA'})...</span>`;
                } else if (st.classroom_mode) {
                    statusEl.innerHTML = `<span style="color:var(--accent-green);">🟢 ATIVO (A aprender autonomamente com modelos reais a cada 75s)</span>`;
                } else {
                    statusEl.innerHTML = `<span style="color:var(--danger);">🔴 PAUSADO</span>`;
                }

                document.getElementById('active-topic-display').innerText = st.current_topic || 'Em espera...';
                document.getElementById('last-learned-display').innerText = lastTitle + (lastTeacher ? ` — ensinado por ${lastTeacher}` : '');

                // Pílulas dos Modelos Reais
                const pillsDiv = document.getElementById('providers-pills');
                pillsDiv.innerHTML = '';
                (st.providers || []).forEach(p => {
                    const dot = p.configured ? '🟢' : '🔴';
                    pillsDiv.innerHTML += `<div class="provider-pill">${dot} <b>${p.name}</b> <span style="color:var(--text-muted);">(${p.model})</span></div>`;
                });

                // Categorias
                const catContainer = document.getElementById('categories-container');
                catContainer.innerHTML = '';
                allCategories.forEach(cat => {
                    const checked = (st.active_categories || []).includes(cat) ? 'checked' : '';
                    catContainer.innerHTML += `<label class="cat-checkbox"><input type="checkbox" value="${cat}" ${checked} onchange="updateCategories()"><span>${cat}</span></label>`;
                });

                // Barras de progresso por categoria
                const counts = st.category_counts || {};
                const total = Math.max(1, kn.length);
                let progHTML = '';
                allCategories.forEach(cat => {
                    const c = counts[cat] || 0;
                    const pct = kn.length === 0 ? 0 : Math.min(100, Math.round((c / total) * 100));
                    progHTML += `
                        <div style="margin-bottom:7px;">
                            <div style="display:flex; justify-content:space-between; font-size:0.78rem; margin-bottom:2px;">
                                <span>${cat}</span><span>${c} lições (${pct}%)</span>
                            </div>
                            <div style="background:var(--bg); height:7px; border-radius:4px; overflow:hidden; border:1px solid var(--border);">
                                <div style="width:${pct}%; height:100%; background:var(--accent-green);"></div>
                            </div>
                        </div>`;
                });
                document.getElementById('category-progress-container').innerHTML = progHTML;

                // Lista detalhada de tudo o que já aprendeu
                document.getElementById('knowledge-count').innerText = kn.length;
                const kList = document.getElementById('knowledge-list-container');
                if (!kn.length) {
                    kList.innerHTML = `<div style="color:var(--text-muted); font-size:0.88rem;">Ainda nenhuma lição registada neste ciclo. Clica em <b>"⚡ Aprender Agora"</b> acima para o gois.v1 aprender imediatamente com um modelo real!</div>`;
                } else {
                    kList.innerHTML = '';
                    kn.forEach(item => {
                        const card = document.createElement('div');
                        card.className = 'lesson-card';
                        card.innerHTML = `
                            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                                <div style="font-weight:700; font-size:0.96rem; color:#fff;">${item.title || 'Lição'}</div>
                                <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                                    <span class="skill-badge">${item.category || 'Geral'}</span>
                                    <span class="teacher-badge">🤖 ${item.teacher || 'Modelo Real'}</span>
                                    <span style="font-size:0.72rem; color:var(--text-muted);">${item.timestamp || ''}</span>
                                </div>
                            </div>
                            <div class="lesson-body">${marked.parse(item.content || '')}</div>
                        `;
                        addCopyButtons(card);
                        kList.appendChild(card);
                    });
                }
            } catch (e) {
                console.error('Erro ao carregar Sala de Aula:', e);
            }
        }

        async function toggleClassroom() {
            await fetch('/api/classroom/toggle', { method: 'POST' });
            loadClassroomUI();
        }

        async function updateCategories() {
            const selected = [];
            document.querySelectorAll('#categories-container input:checked').forEach(cb => selected.push(cb.value));
            await fetch('/api/settings/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active_categories: selected })
            });
            loadClassroomUI();
        }

        async function learnNow() {
            const btn = document.getElementById('btn-learn-now');
            const fb = document.getElementById('learn-feedback');
            const provider = document.getElementById('learn-provider').value;
            const category = document.getElementById('learn-category').value;
            const topic = document.getElementById('learn-topic').value.trim();

            btn.disabled = true;
            btn.innerText = '⏳ A aprender...';
            fb.style.color = 'var(--warn)';
            fb.innerText = `A ligar ao modelo real (${provider}) para estudar e extrair conhecimento técnico...`;

            try {
                const res = await fetch('/api/classroom/learn', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider, category, topic })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Falha ao aprender lição');
                document.getElementById('learn-topic').value = '';
                fb.style.color = 'var(--accent-green)';
                fb.innerText = `✅ Nova lição aprendida com ${data.lesson.teacher}: "${data.lesson.title}"!`;
                await loadClassroomUI();
            } catch (e) {
                fb.style.color = 'var(--danger)';
                fb.innerText = '❌ Erro: ' + e.message;
            } finally {
                btn.disabled = false;
                btn.innerText = '⚡ Aprender Agora';
            }
        }

        // --- SKILLS & DIRETRIZES ---
        async function loadSkillsUI() {
            const res = await fetch('/api/skills');
            const skills = await res.json();
            document.getElementById('skills-count').innerText = skills.length;
            const container = document.getElementById('skills-list-container');
            container.innerHTML = '';
            skills.forEach(s => {
                const badge = s.type === 'SKILL' ? '<span class="skill-badge">SKILL</span>' : '<span class="directive-badge">DIRECTRIZ</span>';
                container.innerHTML += `
                    <div class="lesson-card" style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px;">
                        <div>
                            <div style="margin-bottom:6px;"><b>${s.title}</b> ${badge}</div>
                            <p style="font-size:0.84rem; color:var(--text-muted);">${s.content}</p>
                        </div>
                        <button class="btn-outline" style="color:var(--danger); border-color:var(--danger);" onclick="deleteSkill('${s.id}')">Remover</button>
                    </div>`;
            });
        }

        async function addSkillRecord() {
            const type = document.getElementById('skill-type').value;
            const title = document.getElementById('skill-title').value.trim();
            const content = document.getElementById('skill-content').value.trim();
            if (!title || !content) return alert('Preenche o título e o conteúdo!');
            await fetch('/api/skills', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id: 's_' + Date.now(), type, title, content })
            });
            document.getElementById('skill-title').value = '';
            document.getElementById('skill-content').value = '';
            loadSkillsUI();
        }

        async function deleteSkill(id) {
            await fetch('/api/skills/' + encodeURIComponent(id), { method: 'DELETE' });
            loadSkillsUI();
        }

        // --- CHAVES API ---
        async function loadKeysUI() {
            const res = await fetch('/api/keys/status');
            const data = await res.json();
            const container = document.getElementById('keys-status-container');
            container.innerHTML = '';
            Object.entries(data).forEach(([name, info]) => {
                const badge = info.configured
                    ? `<span style="color:var(--accent-green);">🟢 Ativa (${info.source})</span>`
                    : `<span style="color:var(--danger);">🔴 Sem chave configurada</span>`;
                container.innerHTML += `
                    <div class="lesson-card" style="display:flex; justify-content:space-between; align-items:center;">
                        <div><b>${name}</b> <span style="color:var(--text-muted); font-size:0.82rem;">Modelo: <code>${info.model}</code></span></div>
                        <div>${badge}</div>
                    </div>`;
            });
        }

        async function saveApiKey() {
            const provider = document.getElementById('key-provider').value;
            const key = document.getElementById('key-secret').value.trim();
            const fb = document.getElementById('key-feedback');
            if (!key) return alert('Cola uma chave API válida!');
            const res = await fetch('/api/keys', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, key })
            });
            if (res.ok) {
                document.getElementById('key-secret').value = '';
                fb.style.color = 'var(--accent-green)';
                fb.innerText = `✅ Chave para ${provider} ativada com sucesso!`;
                loadKeysUI();
                loadClassroomUI();
            } else {
                fb.style.color = 'var(--danger)';
                fb.innerText = '❌ Chave inválida ou não reconhecida.';
            }
        }

        function refreshAll() {
            loadClassroomUI();
            if (document.getElementById('view-skills').classList.contains('active')) loadSkillsUI();
            if (document.getElementById('view-keys').classList.contains('active')) loadKeysUI();
        }

        renderSavedChat();
        loadClassroomUI();
        setInterval(loadClassroomUI, 12000);
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=UNIFIED_OFFICE_HTML)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
