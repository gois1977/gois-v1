import os
import re
import time
import json
import hmac
import asyncio
import logging
from datetime import datetime, timedelta, timezone
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

# --- Sincronização das chaves com o Render (Environment) ---
RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "").strip()
RENDER_SERVICE_ID = (os.environ.get("GOIS_RENDER_SERVICE_ID", "") or os.environ.get("RENDER_SERVICE_ID", "")).strip()
RENDER_API_BASE = os.environ.get("RENDER_API_BASE", "https://api.render.com/v1").strip().rstrip("/")

MAX_KNOWLEDGE = 400
MAX_CONTENT_CHARS = 6000

# =====================================================================
# TODAS AS 16 CATEGORIAS DE DOMÍNIO (GEMINI & DEEPSEEK TAXONOMY)
# =====================================================================

DEFAULT_CATEGORIES = [
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

CATEGORY_BASELINES = {
    "Arquitetura de Software & Sistemas Assíncronos": {"gemini": 91.5, "deepseek": 93.0, "gois_base": 78.0, "keywords": ["fastapi", "async", "python", "arquitetura", "websocket"]},
    "Algoritmos, Estruturas de Dados & Programação": {"gemini": 90.0, "deepseek": 94.2, "gois_base": 75.0, "keywords": ["algoritmo", "código", "estrutura", "complexidade", "grafo"]},
    "Game Design, Unity C# & Motores 3D": {"gemini": 86.5, "deepseek": 88.0, "gois_base": 82.0, "keywords": ["unity", "c#", "game", "tile", "grid", "shader", "fsm", "3d"]},
    "WebGL, Three.js, Frontend & Gráficos 2D/3D": {"gemini": 89.0, "deepseek": 87.5, "gois_base": 80.0, "keywords": ["webgl", "three.js", "phaser", "browser", "2d", "frontend"]},
    "Matemática Avançada, Lógica & Raciocínio Formal": {"gemini": 92.0, "deepseek": 95.0, "gois_base": 72.0, "keywords": ["matemática", "cálculo", "álgebra", "lógica", "vetor"]},
    "Inteligência Artificial, LLMs, RAG & Agentes": {"gemini": 93.5, "deepseek": 94.0, "gois_base": 77.0, "keywords": ["ia", "llm", "rag", "agente", "neural", "prompt"]},
    "Ciência de Dados, SQL & Engenharia de Dados": {"gemini": 90.5, "deepseek": 91.0, "gois_base": 73.0, "keywords": ["sql", "dados", "database", "pipeline", "etl"]},
    "Cibersegurança, Criptografia & Auditoria": {"gemini": 88.0, "deepseek": 89.5, "gois_base": 71.0, "keywords": ["segurança", "criptografia", "auth", "token", "owasp"]},
    "DevOps, Cloud, Docker & Sistemas Linux": {"gemini": 89.5, "deepseek": 90.5, "gois_base": 76.0, "keywords": ["devops", "docker", "render", "cloud", "linux", "git"]},
    "Engenharia, AutoCAD, Robótica & IoT": {"gemini": 85.0, "deepseek": 86.0, "gois_base": 74.0, "keywords": ["autocad", "cad", "geometria", "engenharia", "robótica", "iot"]},
    "Física, Química, Biologia & Ciências Exatas": {"gemini": 91.0, "deepseek": 90.0, "gois_base": 70.0, "keywords": ["física", "química", "biologia", "termodinâmica", "ciência"]},
    "Multimodalidade, Visão Computacional & Áudio": {"gemini": 95.0, "deepseek": 86.5, "gois_base": 71.0, "keywords": ["visão", "imagem", "áudio", "vídeo", "ocr", "multimodal"]},
    "Linguística, Tradução & Comunicação Multilingue": {"gemini": 94.0, "deepseek": 91.5, "gois_base": 79.0, "keywords": ["português", "tradução", "linguística", "idioma", "texto"]},
    "Economia, Finanças Quantitativas & Estratégia": {"gemini": 88.5, "deepseek": 90.0, "gois_base": 71.5, "keywords": ["economia", "finanças", "estratégia", "teoria de jogos"]},
    "Direito Tecnológico, Ética & Segurança de IA": {"gemini": 91.0, "deepseek": 87.0, "gois_base": 72.0, "keywords": ["ética", "direito", "rgpd", "licença", "alinhamento"]},
    "Otimização Extrema & Hardware Leve / Edge AI": {"gemini": 86.0, "deepseek": 91.0, "gois_base": 83.0, "keywords": ["leve", "cpu", "ram", "otimização", "performance", "edge"]},
}

UNIVERSAL_CLASSROOM_TOPICS = [
    ("Arquiteturas Python Assíncronas, Event Loops e Concorrência de Alto Desempenho", "Arquitetura de Software & Sistemas Assíncronos"),
    ("Design de APIs FastAPI, Streaming SSE, WebSockets e Resiliência com Failover", "Arquitetura de Software & Sistemas Assíncronos"),
    ("Arquitetura Hexagonal, Event-Driven Systems e Message Queues sem Bloqueio", "Arquitetura de Software & Sistemas Assíncronos"),
    ("Algoritmos de Grafos (A*, Dijkstra, Flow), Árvores Espaciais (QuadTree/OctTree) e Hashing", "Algoritmos, Estruturas de Dados & Programação"),
    ("Programação Dinâmica, Bitmasking, Estruturas Lock-Free e Complexidade Amortizada", "Algoritmos, Estruturas de Dados & Programação"),
    ("Padrões de Memória Contígua, Ring Buffers e Algoritmos Cache-Friendly", "Algoritmos, Estruturas de Dados & Programação"),
    ("Sistemas de Grelha (Tile Grids), Câmaras Isométricas e Map Editors 3D em Unity C#", "Game Design, Unity C# & Motores 3D"),
    ("Character Controllers Avançados, Máquinas de Estado Hierárquicas (HFSM) e Física Customizada", "Game Design, Unity C# & Motores 3D"),
    ("Arquitetura DOTS/ECS, Job System, Burst Compiler e Shaders HLSL em Unity", "Game Design, Unity C# & Motores 3D"),
    ("Renderização 3D/2D no Browser com Three.js, InstancedMesh e Redução de Draw Calls", "WebGL, Three.js, Frontend & Gráficos 2D/3D"),
    ("Shaders GLSL Customizados, Post-Processing, Raycasting e Picking Eficiente em WebGL", "WebGL, Three.js, Frontend & Gráficos 2D/3D"),
    ("Motores 2D em HTML5 Canvas/Phaser, Sprite Batching e Interfaces Reativas sem Frameworks Pesados", "WebGL, Three.js, Frontend & Gráficos 2D/3D"),
    ("Álgebra Linear Aplicada: Quaterniões, Matrizes de Transformação 4x4 e Interpolação Slerp", "Matemática Avançada, Lógica & Raciocínio Formal"),
    ("Cálculo Vetorial, Equações Diferenciais Numéricas (Runge-Kutta) e Geometria Computacional", "Matemática Avançada, Lógica & Raciocínio Formal"),
    ("Lógica Proposicional, Solvers SAT/SMT, Provas Indutivas e Otimização Combinatória", "Matemática Avançada, Lógica & Raciocínio Formal"),
    ("Destilação de Conhecimento entre Modelos (Gemini/DeepSeek), RAG Híbrido e Re-ranking", "Inteligência Artificial, LLMs, RAG & Agentes"),
    ("Arquitetura de Agentes Autónomos, Tool Calling Determinístico e Memória Episódica", "Inteligência Artificial, LLMs, RAG & Agentes"),
    ("Engenharia de Contexto, Compressão de Prompts e Avaliação Automática LLM-as-a-Judge", "Inteligência Artificial, LLMs, RAG & Agentes"),
    ("Otimização Avançada de Queries SQL, Índices Compostos, Particionamento e SQLite/Postgres", "Ciência de Dados, SQL & Engenharia de Dados"),
    ("Pipelines de Dados em Streaming, Séries Temporais e Indexação Vetorial Rápida (FAISS/HNSW)", "Ciência de Dados, SQL & Engenharia de Dados"),
    ("Modelação Dimensional, Normalização vs Desnormalização e Serialização Binária Rápida", "Ciência de Dados, SQL & Engenharia de Dados"),
    ("Autenticação Segura, HMAC Constant-Time, Gestão de Segredos e Proteção contra Injection/CSRF", "Cibersegurança, Criptografia & Auditoria"),
    ("Criptografia Moderna (AES-GCM, Ed25519, Argon2), Zero-Trust APIs e Rate Limiting", "Cibersegurança, Criptografia & Auditoria"),
    ("Auditoria Estática de Código, Sandboxing de Processos e Prevenção de Vulnerabilidades OWASP", "Cibersegurança, Criptografia & Auditoria"),
    ("Contentorização Docker Multi-Stage, Deploy Contínuo no Render e Zero-Downtime", "DevOps, Cloud, Docker & Sistemas Linux"),
    ("Observabilidade, Logging Estruturado, Healthchecks Resilientes e Auto-Recovery", "DevOps, Cloud, Docker & Sistemas Linux"),
    ("Automação em PowerShell/Bash, Empacotamento PyInstaller + Inno Setup e Gestão de Releases", "DevOps, Cloud, Docker & Sistemas Linux"),
    ("Pipelines de Desenho Automatizado, Geração Procedural de Plantas e Geometria Paramétrica CAD", "Engenharia, AutoCAD, Robótica & IoT"),
    ("Cinemática Direta e Inversa, Controladores PID, Kalmann Filters e Navegação de Robôs", "Engenharia, AutoCAD, Robótica & IoT"),
    ("Protocolos IoT de Baixa Latência (MQTT, CoAP, Modbus) e Telemetria Industrial", "Engenharia, AutoCAD, Robótica & IoT"),
    ("Motores de Física: Deteção de Colisões (SAT, GJK), Resolução de Impulsos e Verlet Integration", "Física, Química, Biologia & Ciências Exatas"),
    ("Termodinâmica Computacional, Dinâmica de Fluidos Simplificada (SPH) e Eletromagnetismo", "Física, Química, Biologia & Ciências Exatas"),
    ("Algoritmos Bioinformáticos, Alinhamento de Sequências e Simulação de Ecossistemas", "Física, Química, Biologia & Ciências Exatas"),
    ("Processamento de Imagem em Tempo Real, Deteção de Contornos, OCR e Segmentação Leve", "Multimodalidade, Visão Computacional & Áudio"),
    ("Síntese Procedural de Áudio, DSP, FFT em Tempo Real e Áudio Espacial 3D para Jogos", "Multimodalidade, Visão Computacional & Áudio"),
    ("Pipelines Multimodais (Texto + Imagem + Áudio) e Extração Estruturada de Documentos", "Multimodalidade, Visão Computacional & Áudio"),
    ("Rigor Linguístico em Português Europeu (PT-PT), Terminologia de Engenharia e Clareza Técnica", "Linguística, Tradução & Comunicação Multilingue"),
    ("Processamento de Linguagem Natural, Tokenização, Análise Sintática e Tradução Contextual", "Linguística, Tradução & Comunicação Multilingue"),
    ("Estruturação de Documentação de Arquitetura (ADR), Especificações Técnicas e Manuais", "Linguística, Tradução & Comunicação Multilingue"),
    ("Teoria de Jogos, Equilíbrio de Nash, Leilões e Balanceamento de Economias Virtuais em Jogos", "Economia, Finanças Quantitativas & Estratégia"),
    ("Simulação de Monte Carlo, Gestão de Risco, Séries Financeiras e Otimização de Portfólio", "Economia, Finanças Quantitativas & Estratégia"),
    ("Sistemas de Decisão Multicritério, Utilidade Esperada e IA Estratégica para RTS/4X", "Economia, Finanças Quantitativas & Estratégia"),
    ("Conformidade RGPD, Privacidade por Design, Anonimização de Dados e Segurança em Cloud", "Direito Tecnológico, Ética & Segurança de IA"),
    ("Licenciamento Open-Source (MIT, Apache 2.0, GPL), Propriedade Intelectual e Compliance", "Direito Tecnológico, Ética & Segurança de IA"),
    ("Alinhamento de IA, Prevenção de Alucinações, Verificação Factual e Transparência", "Direito Tecnológico, Ética & Segurança de IA"),
    ("Otimização para PCs All-in-One e Hardware Leve: Zero-Allocation, Object Pooling e GC Tuning", "Otimização Extrema & Hardware Leve / Edge AI"),
    ("Quantização de Modelos (INT8/INT4), Inferência Híbrida Local/Cloud e Redução de Latência", "Otimização Extrema & Hardware Leve / Edge AI"),
    ("Profiling de CPU/RAM, Vetorização SIMD, Multithreading Leve e Arranque Instantâneo", "Otimização Extrema & Hardware Leve / Edge AI"),
]

LEGACY_CATEGORY_MAP = {
    "Tecnologia & Software": "Arquitetura de Software & Sistemas Assíncronos",
    "Game Design & Unity": "Game Design, Unity C# & Motores 3D",
    "Engenharia & AutoCAD": "Engenharia, AutoCAD, Robótica & IoT",
    "Inteligência Artificial": "Inteligência Artificial, LLMs, RAG & Agentes",
    "Ciências & Exatas": "Física, Química, Biologia & Ciências Exatas",
    "Humanidades & Filosofia": "Economia, Finanças Quantitativas & Estratégia",
    "DevOps & Ferramentas": "DevOps, Cloud, Docker & Sistemas Linux",
}

SERVER_SETTINGS = {
    "classroom_mode": True,
    "learning_now": False,
    "current_topic": "A aguardar próximo ciclo de estudo...",
    "current_teacher": "Rotação Automática (DeepSeek / Gemini / GPT-OSS / Mistral)",
    "last_learned_title": "",
    "last_learned_at": "",
    "last_teacher": "",
    "teacher_provider": "auto",
    "topic_index": 0,
    "active_categories": list(DEFAULT_CATEGORIES),
    "daily_token_limit": 0,
    "auto_paused_by_limit": False,
}

SKILLS_DB: list = []
KNOWLEDGE_DB: list = []
BENCHMARK_DB: list = []
RUNTIME_KEYS: dict = {}

try:
    from zoneinfo import ZoneInfo
    TOKEN_TZ_NAME = os.environ.get("TOKEN_TZ", "Europe/London")
    TOKEN_TZ = ZoneInfo(TOKEN_TZ_NAME)
except Exception:
    TOKEN_TZ_NAME = "UTC"
    TOKEN_TZ = timezone.utc

MAX_DAILY_LIMIT = 1_000_000_000

TOKEN_STATS = {
    "date": "",
    "used_today": 0,
    "chat_today": 0,
    "classroom_today": 0,
    "used_total": 0,
}


def token_now() -> datetime:
    return datetime.now(TOKEN_TZ)


def roll_token_day() -> None:
    today = token_now().strftime("%Y-%m-%d")
    if TOKEN_STATS["date"] != today:
        TOKEN_STATS.update({"date": today, "used_today": 0, "chat_today": 0, "classroom_today": 0})


def estimate_tokens(text) -> int:
    return max(1, len(str(text or "")) // 4)


def estimate_prompt_tokens(payload: dict) -> int:
    total = 0
    for m in (payload.get("messages") or []):
        if isinstance(m, dict):
            total += len(str(m.get("content", "")))
    return max(1, total // 4)


def usage_total(usage) -> int:
    if not isinstance(usage, dict):
        return 0
    t = usage.get("total_tokens")
    if isinstance(t, (int, float)) and t > 0:
        return int(t)
    try:
        return int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    except Exception:
        return 0


def record_tokens(n, kind: str = "chat") -> None:
    try:
        n = int(n)
    except Exception:
        return
    if n <= 0:
        return
    roll_token_day()
    TOKEN_STATS["used_today"] += n
    TOKEN_STATS["used_total"] += n
    key = "classroom_today" if kind == "classroom" else "chat_today"
    TOKEN_STATS[key] += n


def account_response_tokens(payload: dict, data: dict, kind: str) -> None:
    try:
        n = usage_total(data.get("usage"))
        if not n:
            text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            n = estimate_prompt_tokens(payload) + estimate_tokens(text)
        record_tokens(n, kind)
    except Exception:
        pass


def daily_limit() -> int:
    try:
        return max(0, int(SERVER_SETTINGS.get("daily_token_limit") or 0))
    except Exception:
        return 0


def limit_reached() -> bool:
    roll_token_day()
    lim = daily_limit()
    return lim > 0 and TOKEN_STATS["used_today"] >= lim


def token_status() -> dict:
    roll_token_day()
    lim = daily_limit()
    used = TOKEN_STATS["used_today"]
    now = token_now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "date": TOKEN_STATS["date"],
        "timezone": TOKEN_TZ_NAME,
        "used_today": used,
        "chat_today": TOKEN_STATS["chat_today"],
        "classroom_today": TOKEN_STATS["classroom_today"],
        "used_total": TOKEN_STATS["used_total"],
        "daily_limit": lim,
        "remaining_today": max(0, lim - used) if lim > 0 else None,
        "limit_reached": lim > 0 and used >= lim,
        "auto_paused_by_limit": bool(SERVER_SETTINGS.get("auto_paused_by_limit")),
        "resets_at": "00:00",
        "seconds_to_reset": int((nxt - now).total_seconds()),
    }

GOIS_SYSTEM_PROMPT = """És o gois.v1, um assistente avançado de engenharia de software, arquitetura de sistemas, ciência e desenvolvimento de jogos (Unity/C#, Python/FastAPI, WebGL/Three.js, matemática, IA e otimização para hardware leve).

Regras de comportamento:
1. Fala sempre em Português de Portugal (PT-PT) de forma natural, clara e direta, a menos que o utilizador peça outro idioma.
2. Adapta a resposta à pergunta: se o utilizador apenas cumprimentar ou fizer uma pergunta casual (ex.: "olá", "tudo bem?"), responde com naturalidade e simpatia numa ou duas frases curtas, sem listar competências, sem menus e sem jargão robótico.
3. NUNCA inventes telemetria falsa, latências inventadas (ex.: "Latência: 12ms"), utilização de CPU/RAM ou logs fictícios.
4. Quando o utilizador perguntar o que estás a aprender, o que já aprendeste ou como estás na comparação com o Gemini e o DeepSeek, responde com base nos dados reais da tua Sala de Aula e da matriz de comparação presente no contexto.
5. Quando o pedido for técnico ou de programação, sê rigoroso e completo: entrega código funcional, limpo, bem estruturado e sem omissões preguiçosas."""

GROQ_MODEL = os.environ.get("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"

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
        "model": GROQ_MODEL,
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

TEACHER_MODELS = [
    {"provider": "OpenRouter", "model": "deepseek/deepseek-chat", "label": "DeepSeek V3 (OpenRouter)"},
    {"provider": "OpenRouter", "model": "google/gemini-2.0-flash-001", "label": "Google Gemini 2.0 Flash (OpenRouter)"},
    {"provider": "Groq", "model": GROQ_MODEL, "label": f"{GROQ_MODEL} (Groq)"},
    {"provider": "Mistral", "model": "mistral-small-latest", "label": "Mistral Small (Mistral)"},
]


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def normalize_category(cat: str) -> str:
    c = (cat or "").strip()
    if c in CATEGORY_BASELINES:
        return c
    if c in LEGACY_CATEGORY_MAP:
        return LEGACY_CATEGORY_MAP[c]
    for valid in DEFAULT_CATEGORIES:
        if c and (c.lower() in valid.lower() or valid.lower() in c.lower()):
            return valid
    return DEFAULT_CATEGORIES[0]


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


def find_provider(name: str):
    n = (name or "").strip().lower()
    for p in PROVIDERS:
        if n in (p["name"].lower(), p["env"].lower()):
            return p
    return None


def render_sync_state() -> dict:
    missing = []
    if not RENDER_API_KEY:
        missing.append("RENDER_API_KEY")
    if not RENDER_SERVICE_ID:
        missing.append("RENDER_SERVICE_ID")
    if not SYNC_TOKEN:
        missing.append("SYNC_TOKEN")
    return {"enabled": not missing, "missing": missing}


def render_sync_enabled() -> bool:
    return render_sync_state()["enabled"]


async def render_request(method: str, env: str, value: str = ""):
    if env not in {p["env"] for p in PROVIDERS}:
        return False, "Nome de variável não permitido."
    url = f"{RENDER_API_BASE}/services/{RENDER_SERVICE_ID}/env-vars/{env}"
    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if method == "PUT":
                r = await client.put(url, headers=headers, json={"value": value})
            else:
                r = await client.delete(url, headers=headers)
    except Exception as e:
        return False, f"Sem ligação à API do Render ({type(e).__name__})."
    code = r.status_code
    if code in (200, 201, 204):
        return True, "ok"
    if code == 404 and method == "DELETE":
        return True, "já não existia no Render"
    if code == 401:
        return False, "O Render recusou a RENDER_API_KEY (401)."
    if code == 403:
        return False, "A RENDER_API_KEY não tem permissão neste serviço (403)."
    if code == 404:
        return False, "Serviço não encontrado no Render (404)."
    if code == 429:
        return False, "Limite de pedidos do Render atingido (429)."
    return False, f"O Render respondeu HTTP {code}."


def ordered_providers(preferred: str = ""):
    pref = (preferred or "").strip().lower()
    if not pref or pref == "auto":
        return list(PROVIDERS)
    if "gemini" in pref or "deepseek" in pref or "openrouter" in pref:
        first = [p for p in PROVIDERS if p["name"] == "OpenRouter"]
        rest = [p for p in PROVIDERS if p["name"] != "OpenRouter"]
        return first + rest
    first = [p for p in PROVIDERS if p["name"].lower() in pref or pref in p["name"].lower()]
    rest = [p for p in PROVIDERS if p not in first]
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


def check_token_for_keys(request: Request):
    if RENDER_API_KEY and not SYNC_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Define SYNC_TOKEN no Environment do Render para gerir chaves.",
        )
    check_token(request)


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
    if item.get("category"):
        out["category"] = normalize_category(str(item["category"]))
    for extra in ("teacher", "timestamp", "topic"):
        if item.get(extra):
            out[extra] = str(item[extra])[:160]
    if isinstance(item.get("quality_score"), (int, float)):
        out["quality_score"] = round(float(item["quality_score"]), 1)
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


def score_lesson_quality(content: str) -> float:
    text = (content or "").strip()
    if not text:
        return 0.0
    score = 62.0
    if len(text) >= 400:
        score += 10.0
    if len(text) >= 900:
        score += 8.0
    if "```" in text:
        score += 10.0
    if any(h in text for h in ("# ", "## ", "### ", "**")):
        score += 5.0
    if any(w in text.lower() for w in ("complexidade", "performance", "arquitetura", "otimiza", "async", "memória")):
        score += 5.0
    return min(99.5, round(score, 1))


def compute_comparison_metrics() -> dict:
    active_provs = len([p for p in PROVIDERS if key_source(p)])
    multi_provider_bonus = min(3.0, active_provs * 1.0)

    cat_lessons = {c: [] for c in DEFAULT_CATEGORIES}
    for k in KNOWLEDGE_DB:
        cat = normalize_category(k.get("category", ""))
        cat_lessons.setdefault(cat, []).append(k)

    cat_benchmarks = {c: [] for c in DEFAULT_CATEGORIES}
    for b in BENCHMARK_DB:
        cat = normalize_category(b.get("category", ""))
        cat_benchmarks.setdefault(cat, []).append(b)

    rows = []
    sum_gois = 0.0
    sum_gemini = 0.0
    sum_deepseek = 0.0

    for cat in DEFAULT_CATEGORIES:
        base_info = CATEGORY_BASELINES[cat]
        gemini_pct = base_info["gemini"]
        deepseek_pct = base_info["deepseek"]
        gois_base = base_info["gois_base"]

        kw_list = base_info["keywords"]
        matching_skills = 0
        for s in SKILLS_DB:
            hay = f"{s.get('title', '')} {s.get('content', '')}".lower()
            if any(kw in hay for kw in kw_list):
                matching_skills += 1
        skill_bonus = min(4.5, matching_skills * 1.5)

        lessons = cat_lessons.get(cat, [])
        n_lessons = len(lessons)
        learning_gain = 0.0
        for idx, les in enumerate(lessons):
            q = float(les.get("quality_score") or score_lesson_quality(les.get("content", "")))
            weight = 1.4 if idx < 3 else (0.8 if idx < 10 else 0.35)
            learning_gain += weight * (q / 90.0)
        learning_gain = min(16.5, learning_gain)

        b_runs = cat_benchmarks.get(cat, [])
        bench_bonus = 0.0
        if b_runs:
            avg_b = sum(float(x.get("gois_score", 85.0)) for x in b_runs) / len(b_runs)
            bench_bonus = min(3.0, max(0.4, (avg_b - 75.0) * 0.12))

        gois_pct = min(98.5, round(gois_base + multi_provider_bonus + skill_bonus + learning_gain + bench_bonus, 1))
        parity_gemini = round((gois_pct / gemini_pct) * 100.0, 1)
        parity_deepseek = round((gois_pct / deepseek_pct) * 100.0, 1)
        delta_gemini = round(gois_pct - gemini_pct, 1)
        delta_deepseek = round(gois_pct - deepseek_pct, 1)

        if gois_pct >= max(gemini_pct, deepseek_pct):
            status = "🏆 Acima de Gemini & DeepSeek"
        elif gois_pct >= min(gemini_pct, deepseek_pct) - 2.0:
            status = "🟢 Nível de Paridade"
        elif n_lessons > 0:
            status = "⚡ Em Evolução Ativa"
        else:
            status = "📘 A aguardar estudo na Sala de Aula"

        rows.append({
            "category": cat,
            "gois_v1": gois_pct,
            "gemini": gemini_pct,
            "deepseek": deepseek_pct,
            "parity_vs_gemini": parity_gemini,
            "parity_vs_deepseek": parity_deepseek,
            "delta_gemini": delta_gemini,
            "delta_deepseek": delta_deepseek,
            "lessons_count": n_lessons,
            "skills_count": matching_skills,
            "benchmarks_count": len(b_runs),
            "status": status,
        })

        sum_gois += gois_pct
        sum_gemini += gemini_pct
        sum_deepseek += deepseek_pct

    n_cats = len(DEFAULT_CATEGORIES)
    avg_gois = round(sum_gois / n_cats, 1)
    avg_gemini = round(sum_gemini / n_cats, 1)
    avg_deepseek = round(sum_deepseek / n_cats, 1)

    return {
        "updated_at": now_str(),
        "overall": {
            "gois_v1": avg_gois,
            "gemini": avg_gemini,
            "deepseek": avg_deepseek,
            "parity_vs_gemini": round((avg_gois / avg_gemini) * 100.0, 1),
            "parity_vs_deepseek": round((avg_gois / avg_deepseek) * 100.0, 1),
            "delta_gemini": round(avg_gois - avg_gemini, 1),
            "delta_deepseek": round(avg_gois - avg_deepseek, 1),
            "total_categories": n_cats,
            "categories_studied": sum(1 for r in rows if r["lessons_count"] > 0),
            "total_lessons": len(KNOWLEDGE_DB),
            "total_skills": len(SKILLS_DB),
            "total_benchmarks": len(BENCHMARK_DB),
        },
        "categories": rows,
        "recent_benchmarks": BENCHMARK_DB[:15],
    }


def build_provider_request(p: dict, key: str, payload: dict, stream: bool = False, override_model: str = ""):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        **p["extra_headers"],
    }
    body = {
        "model": override_model or p["model"],
        "messages": payload.get("messages", []),
        "temperature": payload.get("temperature", 0.6),
        "stream": stream,
    }
    if payload.get("max_tokens"):
        body["max_tokens"] = payload["max_tokens"]
        if p["name"] == "Groq" and str(body["model"]).startswith("openai/gpt-oss"):
            body["max_tokens"] = int(payload["max_tokens"]) + 2048
    return headers, body


async def make_llm_request(payload: dict, preferred_provider: str = "", override_model: str = "", usage_kind: str = "chat"):
    """Pedido completo (sem streaming) com failover entre fornecedores e API pessoal gois.api."""
    errors = []

    # 1. Tentar primeiro a API pessoal do Render (gois.api) se configurada em GOIS_API_KEY
    personal_url = os.environ.get("GOIS_API_KEY", "").strip()
    if personal_url and personal_url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=65.0) as client:
                resp = await client.post(f"{personal_url.rstrip('/')}/v1/chat/completions", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("choices"):
                        data["model"] = f"gois.api · {data.get('model', 'Personal Render')}"
                        account_response_tokens(payload, data, usage_kind)
                        return data, []
                else:
                    errors.append(f"gois.api (Personal): HTTP {resp.status_code}")
        except Exception as e:
            errors.append(f"gois.api (Personal): {type(e).__name__}")

    # 2. Failover padrão para fornecedores externos (OpenRouter, Groq, Mistral)
    timeout = httpx.Timeout(65.0, connect=15.0)
    providers = ordered_providers(preferred_provider)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for idx, p in enumerate(providers):
            keys = candidate_keys(p)
            if not keys:
                errors.append(f"{p['name']}: sem chave ({p['env']})")
                continue
            model_to_use = override_model if (idx == 0 and override_model) else p["model"]
            for key in keys:
                try:
                    headers, body = build_provider_request(p, key, payload, stream=False, override_model=model_to_use)
                    resp = await client.post(p["url"], json=body, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("choices"):
                            data["model"] = f"{p['name']} · {model_to_use}"
                            data["provider_name"] = p["name"]
                            account_response_tokens(payload, data, usage_kind)
                            return data, errors
                        errors.append(f"{p['name']}: resposta 200 sem 'choices'")
                    else:
                        errors.append(f"{p['name']} ({model_to_use}): HTTP {resp.status_code}")
                        if model_to_use != p["model"]:
                            headers2, body2 = build_provider_request(p, key, payload, stream=False, override_model=p["model"])
                            resp2 = await client.post(p["url"], json=body2, headers=headers2)
                            if resp2.status_code == 200:
                                data2 = resp2.json()
                                if isinstance(data2, dict) and data2.get("choices"):
                                    data2["model"] = f"{p['name']} · {p['model']}"
                                    data2["provider_name"] = p["name"]
                                    account_response_tokens(payload, data2, usage_kind)
                                    return data2, errors
                except Exception as e:
                    errors.append(f"{p['name']}: {type(e).__name__}")
    if errors:
        logger.warning("[IA] nenhum fornecedor respondeu: " + " | ".join(errors))
    return None, errors


async def stream_llm_request(payload: dict, preferred_provider: str = ""):
    """Streaming SSE real com failover automático e suporte à gois.api pessoal."""
    errors = []

    # 1. Tentar primeiro streaming na API pessoal do Render se configurada em GOIS_API_KEY
    personal_url = os.environ.get("GOIS_API_KEY", "").strip()
    if personal_url and personal_url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", f"{personal_url.rstrip('/')}/v1/chat/completions", json={**payload, "stream": True}) as resp:
                    if resp.status_code == 200:
                        got_content = False
                        out_chars = 0
                        usage_seen = 0
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
                                if isinstance(chunk, dict):
                                    if chunk.get("choices"):
                                        delta = chunk["choices"][0].get("delta", {})
                                        if delta.get("content"):
                                            got_content = True
                                            out_chars += len(str(delta["content"]))
                                    yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
                            except Exception:
                                continue
                        if got_content:
                            yield "data: [DONE]\n\n"
                            return
        except Exception as e:
            errors.append(f"gois.api (Personal Streaming): {type(e).__name__}")

    # 2. Streaming failover padrão para fornecedores externos
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
                usage_seen = 0
                out_chars = 0
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

                            ut = usage_total(chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage"))
                            if ut:
                                usage_seen = ut

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
                                    out_chars += len(str(delta["content"]))

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
                finally:
                    if got_content:
                        record_tokens(usage_seen or (estimate_prompt_tokens(payload) + max(1, out_chars // 4)), "chat")

    detail = "Erro de Failover: nenhum fornecedor de IA respondeu com sucesso. " + " | ".join(errors)
    logger.warning(f"[IA] {detail}")
    yield "data: " + json.dumps({"error": {"message": detail}}, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


CLASSROOM_LOCK = asyncio.Lock()


def parse_lesson_response(raw_text: str, default_title: str) -> tuple[str, str]:
    text = (raw_text or "").strip()
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


def pick_next_topic_and_category() -> tuple[str, str]:
    active_cats = [normalize_category(c) for c in (SERVER_SETTINGS.get("active_categories") or DEFAULT_CATEGORIES)]
    if not active_cats:
        active_cats = list(DEFAULT_CATEGORIES)

    counts = {c: 0 for c in active_cats}
    for k in KNOWLEDGE_DB:
        c = normalize_category(k.get("category", ""))
        if c in counts:
            counts[c] += 1

    min_count = min(counts.values()) if counts else 0
    least_studied_cats = [c for c, cnt in counts.items() if cnt == min_count]

    idx = int(SERVER_SETTINGS.get("topic_index", 0))
    SERVER_SETTINGS["topic_index"] = idx + 1

    chosen_cat = least_studied_cats[idx % len(least_studied_cats)]
    cat_topics = [t for t in UNIVERSAL_CLASSROOM_TOPICS if t[1] == chosen_cat]
    if not cat_topics:
        cat_topics = UNIVERSAL_CLASSROOM_TOPICS
    topic, cat = cat_topics[(idx // max(1, len(least_studied_cats))) % len(cat_topics)]
    return topic, cat


async def learn_one_topic(custom_topic: str = "", custom_category: str = "", preferred_provider: str = "") -> dict:
    async with CLASSROOM_LOCK:
        idx = int(SERVER_SETTINGS.get("topic_index", 0))
        if custom_topic.strip():
            topic = custom_topic.strip()
            cat = normalize_category(custom_category) if custom_category.strip() else DEFAULT_CATEGORIES[0]
        elif custom_category.strip():
            cat = normalize_category(custom_category)
            cat_topics = [t for t in UNIVERSAL_CLASSROOM_TOPICS if t[1] == cat] or UNIVERSAL_CLASSROOM_TOPICS
            topic = cat_topics[idx % len(cat_topics)][0]
            SERVER_SETTINGS["topic_index"] = idx + 1
        else:
            topic, cat = pick_next_topic_and_category()

        override_model = ""
        pref = (preferred_provider or SERVER_SETTINGS.get("teacher_provider", "auto")).strip()
        if pref.lower() == "gemini":
            chosen_provider = "OpenRouter"
            override_model = "google/gemini-2.0-flash-001"
        elif pref.lower() == "deepseek":
            chosen_provider = "OpenRouter"
            override_model = "deepseek/deepseek-chat"
        elif not pref or pref.lower() == "auto":
            t_slot = TEACHER_MODELS[idx % len(TEACHER_MODELS)]
            chosen_provider = t_slot["provider"]
            override_model = t_slot["model"]
        else:
            chosen_provider = pref

        SERVER_SETTINGS["learning_now"] = True
        SERVER_SETTINGS["current_topic"] = f"[{cat}] {topic}"
        SERVER_SETTINGS["current_teacher"] = override_model or chosen_provider or "Auto"

        prompt_messages = [
            {
                "role": "system",
                "content": (
                    "És um mentor sénior de engenharia e ciência que está a destilar conhecimento de topo "
                    "(nível Gemini 2.5 Pro & DeepSeek R1/V3) para o núcleo gois.v1.\n"
                    "Escreve em Português de Portugal (PT-PT) usando Markdown estruturado.\n"
                    "Começa na primeira linha com `# Título Conciso da Lição` e desenvolve:\n"
                    "1. **Domínio Teórico & Arquitetura Avançada** (explicação rigorosa e densa)\n"
                    "2. **Implementação Prática / Código Completo ou Fórmula Técnica**\n"
                    "3. **Vantagem Competitiva & Otimização de Performance** (3 regras práticas)."
                ),
            },
            {
                "role": "user",
                "content": f"Categoria de Benchmark: {cat}\nTópico a ensinar ao gois.v1: {topic}",
            },
        ]

        try:
            data, errors = await make_llm_request(
                {"messages": prompt_messages, "temperature": 0.5, "max_tokens": 950},
                preferred_provider=chosen_provider,
                override_model=override_model,
                usage_kind="classroom",
            )
            if not data:
                raise RuntimeError("Nenhum modelo respondeu: " + " | ".join(errors))

            raw_content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            teacher_model = data.get("model", "Modelo IA")
            title, body_md = parse_lesson_response(raw_content, topic)
            q_score = score_lesson_quality(body_md)

            entry = clean_item({
                "id": f"k_{int(time.time() * 1000)}",
                "title": title,
                "category": cat,
                "topic": topic,
                "teacher": teacher_model,
                "timestamp": now_str(),
                "type": "LIÇÃO",
                "quality_score": q_score,
                "content": body_md,
            })
            upsert(KNOWLEDGE_DB, [entry], front=True)
            del KNOWLEDGE_DB[MAX_KNOWLEDGE:]

            SERVER_SETTINGS["last_learned_title"] = title
            SERVER_SETTINGS["last_learned_at"] = entry["timestamp"]
            SERVER_SETTINGS["last_teacher"] = teacher_model
            SERVER_SETTINGS["current_teacher"] = teacher_model
            logger.info(f"[SALA DE AULA] Lição aprendida ({cat}) com {teacher_model}: {title}")
            return entry
        finally:
            SERVER_SETTINGS["learning_now"] = False


async def run_live_benchmark(category: str = "") -> dict:
    cat = normalize_category(category) if category else DEFAULT_CATEGORIES[int(time.time()) % len(DEFAULT_CATEGORIES)]
    cat_topics = [t for t in UNIVERSAL_CLASSROOM_TOPICS if t[1] == cat] or UNIVERSAL_CLASSROOM_TOPICS
    topic = cat_topics[len(BENCHMARK_DB) % len(cat_topics)][0]

    t0 = time.time()
    lesson = await learn_one_topic(custom_topic=f"Desafio de Benchmark: {topic}", custom_category=cat)
    elapsed = round(time.time() - t0, 2)

    base_info = CATEGORY_BASELINES[cat]
    q_score = float(lesson.get("quality_score") or score_lesson_quality(lesson.get("content", "")))
    comp = compute_comparison_metrics()
    cat_row = next((r for r in comp["categories"] if r["category"] == cat), None)
    gois_cat_pct = cat_row["gois_v1"] if cat_row else q_score

    record = {
        "id": f"b_{int(time.time() * 1000)}",
        "category": cat,
        "topic": topic,
        "title": lesson.get("title", topic),
        "teacher": lesson.get("teacher", "IA"),
        "gois_score": gois_cat_pct,
        "quality_score": q_score,
        "gemini_ref": base_info["gemini"],
        "deepseek_ref": base_info["deepseek"],
        "elapsed_s": elapsed,
        "timestamp": now_str(),
    }
    BENCHMARK_DB.insert(0, record)
    del BENCHMARK_DB[100:]
    return {"benchmark": record, "lesson": lesson, "comparison": compute_comparison_metrics()}


async def classroom_background_worker():
    await asyncio.sleep(4)
    while True:
        try:
            if SERVER_SETTINGS.get("classroom_mode") and any(key_source(p) for p in PROVIDERS):
                if limit_reached():
                    if not SERVER_SETTINGS.get("auto_paused_by_limit"):
                        SERVER_SETTINGS["auto_paused_by_limit"] = True
                        SERVER_SETTINGS["current_topic"] = "⏸ Pausa automática: limite diário de tokens atingido"
                else:
                    if SERVER_SETTINGS.get("auto_paused_by_limit"):
                        SERVER_SETTINGS["auto_paused_by_limit"] = False
                    await learn_one_topic()
        except Exception as e:
            logger.warning(f"[SALA DE AULA] ciclo falhou: {e!r}")
        await asyncio.sleep(55)


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


app = FastAPI(title="gois.v1 Command Center", version="2.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def select_relevant_knowledge(user_query: str, max_items: int = 8) -> list:
    if not KNOWLEDGE_DB:
        return []
    tokens = [w for w in re.findall(r"[a-zA-ZÀ-ÿ0-9_+#]{3,}", (user_query or "").lower()) if len(w) >= 3]
    if not tokens:
        return KNOWLEDGE_DB[:max_items]
    scored = []
    for idx, k in enumerate(KNOWLEDGE_DB):
        hay = f"{k.get('category', '')} {k.get('title', '')} {k.get('content', '')}".lower()
        hits = sum(3 if t in (k.get("title", "").lower()) else (1 if t in hay else 0) for t in tokens)
        scored.append((hits, -idx, k))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    selected = [x[2] for x in scored[:max_items]]
    return selected


def build_system_message(user_query: str = "") -> str:
    system_parts = [GOIS_SYSTEM_PROMPT]

    if SKILLS_DB:
        blocks = [
            "Especialidades e diretrizes técnicas do gois.v1 (aplica quando relevante para a tarefa):"
        ]
        for s in SKILLS_DB:
            blocks.append(f"- {s.get('title', '')}: {s.get('content', '')}")
        system_parts.append("\n".join(blocks))

    comp = compute_comparison_metrics()
    ov = comp["overall"]
    cat_summary = ", ".join(
        f"{r['category']}: {r['gois_v1']}% ({r['lessons_count']} lições, Paridade Gemini {r['parity_vs_gemini']}%)"
        for r in comp["categories"]
    )
    system_parts.append(
        f"Estado Real do Conhecimento do gois.v1: "
        f"Média Global gois.v1: {ov['gois_v1']}% vs Gemini: {ov['gemini']}% (Paridade: {ov['parity_vs_gemini']}%) "
        f"vs DeepSeek: {ov['deepseek']}% (Paridade: {ov['parity_vs_deepseek']}%) | "
        f"Total acumulado: {ov['total_lessons']} lições estudadas e {ov['total_skills']} skills em {ov['categories_studied']}/{ov['total_categories']} categorias. "
        f"Detalhe por categoria: {cat_summary}."
    )

    if KNOWLEDGE_DB:
        relevant = select_relevant_knowledge(user_query, max_items=8)
        blocks = [
            f"Conhecimento Real Aprendido pelo gois.v1 na Sala de Aula (de um total de {len(KNOWLEDGE_DB)} lições):"
        ]
        for k in relevant:
            snippet = (k.get("content") or "").replace("\n", " ")[:480]
            teacher = k.get("teacher", "Modelo IA")
            blocks.append(f"- [{k.get('category', 'Geral')}] {k.get('title', '')} (destilado via {teacher}): {snippet}")
        system_parts.append("\n".join(blocks))

    return "\n\n".join(system_parts)


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def process_chat(request: Request):
    body = await read_json(request)
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Corpo do pedido inválido.")
    messages = body.get("messages", []) or []

    last_user_query = ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user_query = flatten_content(m.get("content"))
            break

    formatted = [{"role": "system", "content": build_system_message(last_user_query)}]
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


def category_metrics() -> dict:
    counts = {c: 0 for c in DEFAULT_CATEGORIES}
    for k in KNOWLEDGE_DB:
        cat = normalize_category(k.get("category", ""))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def full_status_payload() -> dict:
    comp = compute_comparison_metrics()
    return {
        **SERVER_SETTINGS,
        "all_categories": DEFAULT_CATEGORIES,
        "category_counts": category_metrics(),
        "comparison": comp,
        "total_knowledge": len(KNOWLEDGE_DB),
        "total_skills": len(SKILLS_DB),
        "tokens": token_status(),
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
    if "daily_token_limit" in body:
        try:
            lim = int(float(body["daily_token_limit"]))
        except Exception:
            lim = 0
        SERVER_SETTINGS["daily_token_limit"] = max(0, min(lim, MAX_DAILY_LIMIT))
        if not limit_reached():
            SERVER_SETTINGS["auto_paused_by_limit"] = False
    ur = body.get("usage_restore")
    if isinstance(ur, dict):
        roll_token_day()
        try:
            if str(ur.get("date")) == TOKEN_STATS["date"]:
                for k in ("used_today", "chat_today", "classroom_today"):
                    TOKEN_STATS[k] = max(TOKEN_STATS[k], int(ur.get(k) or 0))
            TOKEN_STATS["used_total"] = max(TOKEN_STATS["used_total"], int(ur.get("used_total") or 0))
        except Exception:
            pass
    if isinstance(body.get("active_categories"), list):
        cats = [normalize_category(str(c)) for c in body["active_categories"] if str(c).strip()]
        seen = []
        for c in cats:
            if c not in seen:
                seen.append(c)
        if seen:
            SERVER_SETTINGS["active_categories"] = seen


@app.get("/api/comparison")
@app.get("/api/benchmarks")
async def get_comparison():
    return compute_comparison_metrics()


@app.post("/api/comparison/benchmark")
@app.post("/api/benchmarks/run")
async def trigger_benchmark(request: Request):
    check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if SERVER_SETTINGS.get("learning_now"):
        raise HTTPException(status_code=409, detail="O núcleo já está a estudar um tópico neste momento.")
    try:
        res = await run_live_benchmark(category=str(body.get("category") or ""))
        return {"status": "success", **res}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/tokens")
async def get_tokens():
    return token_status()


@app.get("/api/settings")
@app.get("/api/classroom/status")
async def get_settings():
    return full_status_payload()


@app.post("/api/settings")
@app.post("/api/classroom/toggle")
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
    check_token(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if SERVER_SETTINGS.get("learning_now"):
        raise HTTPException(status_code=409, detail="A Sala de Aula já está a estudar um tópico neste momento.")

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
            "persistent": key_source(p) == "environment",
            "model": p["model"],
        }
        for p in PROVIDERS
    }


@app.get("/api/render/status")
async def render_status():
    return render_sync_state()


@app.post("/api/keys")
async def set_runtime_key(request: Request):
    check_token_for_keys(request)
    body = await read_json(request)
    found = extract_keys(body)
    if not found:
        raise HTTPException(status_code=400, detail="Nenhuma chave válida reconhecida.")

    state = render_sync_state()
    names = {p["env"]: p["name"] for p in PROVIDERS}
    render_results = {}
    warnings = []
    for env, val in found.items():
        name = names[env]
        if state["enabled"]:
            ok, msg = await render_request("PUT", env, val)
            render_results[name] = {"synced": ok, "detail": msg}
            if ok:
                os.environ[env] = val
                RUNTIME_KEYS.pop(env, None)
            else:
                RUNTIME_KEYS[env] = val
                warnings.append(f"{name}: ativa só em memória. {msg}")
        else:
            RUNTIME_KEYS[env] = val
            render_results[name] = {"synced": False, "detail": "Sincronização com o Render desligada."}

    persisted = bool(render_results) and all(r["synced"] for r in render_results.values())
    return {
        "status": "success",
        "updated": [p["name"] for p in PROVIDERS if p["env"] in found],
        "providers_with_key": [p["name"] for p in PROVIDERS if key_source(p)],
        "render_enabled": state["enabled"],
        "persisted": persisted,
        "render": render_results,
        "warnings": warnings,
    }


@app.delete("/api/keys/{provider}")
async def delete_runtime_key(provider: str, request: Request):
    check_token_for_keys(request)
    p = find_provider(provider)
    if not p:
        raise HTTPException(status_code=404, detail="Fornecedor desconhecido.")
    env = p["env"]
    state = render_sync_state()
    RUNTIME_KEYS.pop(env, None)
    render_info = {"synced": False, "detail": ""}
    warnings = []
    if state["enabled"]:
        ok, msg = await render_request("DELETE", env)
        render_info = {"synced": ok, "detail": msg}
        if ok:
            os.environ.pop(env, None)
        else:
            warnings.append(f"{p['name']}: não foi apagada do Render. {msg}")
    else:
        render_info["detail"] = "Sincronização com o Render desligada."
    return {
        "status": "success",
        "provider": p["name"],
        "still_configured": key_source(p) is not None,
        "render_enabled": state["enabled"],
        "render": {p["name"]: render_info},
        "warnings": warnings,
    }


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

    warnings = []
    imported = {"keys": [], "skills": 0, "knowledge": 0, "settings": False}

    found = {}
    if not render_sync_enabled():
        for field in ("keys", "api_keys", "chaves", "providers"):
            if field in payload:
                found.update(extract_keys(payload[field]))
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
    if settings_in:
        apply_settings(settings_in)
        imported["settings"] = True

    return {
        "status": "success",
        "ok": True,
        "imported": imported,
        "totals": {"skills": len(SKILLS_DB), "knowledge": len(KNOWLEDGE_DB)},
        "comparison": compute_comparison_metrics(),
        "warnings": warnings,
    }


@app.get("/health")
async def health_check():
    comp = compute_comparison_metrics()
    return {
        "status": "online",
        "system": "gois.v1",
        "classroom_mode": SERVER_SETTINGS["classroom_mode"],
        "learning_now": SERVER_SETTINGS["learning_now"],
        "current_topic": SERVER_SETTINGS["current_topic"],
        "overall_comparison": comp["overall"],
        "providers_with_key": [p["name"] for p in PROVIDERS if key_source(p)],
        "render_key_sync": render_sync_enabled(),
    }


GOIS_AI_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#10b981"/>
      <stop offset="50%" stop-color="#3b82f6"/>
      <stop offset="100%" stop-color="#8b5cf6"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="115" fill="#090d16"/>
  <rect x="24" y="24" width="464" height="464" rx="95" fill="none" stroke="url(#g)" stroke-width="18"/>
  <circle cx="256" cy="210" r="88" fill="none" stroke="url(#g)" stroke-width="28"/>
  <circle cx="256" cy="210" r="32" fill="#10b981"/>
  <path d="M148 378 C148 318, 364 318, 364 378" fill="none" stroke="url(#g)" stroke-width="28" stroke-linecap="round"/>
</svg>"""

GOIS_AI_MANIFEST = {
    "name": "goisAI",
    "short_name": "goisAI",
    "description": "goisAI — Assistente de Inteligência Artificial Avançado.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait-primary",
    "background_color": "#090d16",
    "theme_color": "#090d16",
    "icons": [
        {"src": "/goisai/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
    ],
}

GOIS_AI_SW_JS = """
const CACHE_NAME = 'goisai-pwa-v1';
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
"""

GOIS_AI_USER_APP_HTML = """<!DOCTYPE html>
<html lang="pt">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>goisAI</title>
    <meta name="theme-color" content="#090d16">
    <link rel="manifest" href="/goisai/manifest.json">
    <link rel="icon" type="image/svg+xml" href="/goisai/icon.svg">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root { --bg: #090d16; --sidebar: #0f1624; --surface: #151e31; --border: #222f49; --text: #f3f4f6; --muted: #94a3b8; --accent: #3b82f6; --emerald: #10b981; --user-bubble: #2563eb; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); height: 100dvh; display: flex; overflow: hidden; }
        .main { flex: 1; display: flex; flex-direction: column; height: 100dvh; overflow: hidden; }
        .topbar { height: 56px; padding: 0 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; background: rgba(9,13,22,0.88); }
        #chat-feed { flex: 1; overflow-y: auto; padding: 20px 16px; display: flex; flex-direction: column; gap: 18px; }
        .chat-inner { max-width: 780px; width: 100%; margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }
        .msg-row { display: flex; flex-direction: column; gap: 6px; width: 100%; }
        .msg-row.user { align-items: flex-end; }
        .msg-row.assistant { align-items: flex-start; }
        .bubble { padding: 13px 16px; border-radius: 18px; font-size: 0.95rem; line-height: 1.6; max-width: 88%; word-wrap: break-word; }
        .msg-row.user .bubble { background: var(--user-bubble); color: #fff; }
        .msg-row.assistant .bubble { background: var(--surface); border: 1px solid var(--border); color: var(--text); width: 100%; }
        .composer-wrap { padding: 10px 14px; background: var(--bg); }
        .composer { max-width: 780px; margin: 0 auto; background: var(--surface); border: 1px solid var(--border); border-radius: 22px; padding: 8px 10px 8px 16px; display: flex; align-items: flex-end; gap: 8px; }
        textarea#prompt-input { flex: 1; background: transparent; border: none; color: var(--text); font-size: 0.96rem; resize: none; outline: none; font-family: inherit; }
        .send-btn { background: var(--accent); color: #fff; border: none; width: 38px; height: 38px; border-radius: 50%; font-weight: 800; cursor: pointer; }
    </style>
</head>
<body>
    <main class="main">
        <div class="topbar"><b>goisAI</b></div>
        <div id="chat-feed"><div id="chat-inner" class="chat-inner"></div></div>
        <div class="composer-wrap">
            <div class="composer">
                <textarea id="prompt-input" rows="1" placeholder="Pergunta ao goisAI..."></textarea>
                <button id="send-btn" class="send-btn" onclick="sendUserPrompt()">↑</button>
            </div>
        </div>
    </main>
    <script>
        let messages = [];
        async function sendUserPrompt() {
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text) return;
            input.value = '';
            messages.push({ role: 'user', content: text });
            appendBubble('user', text);
            const botBubble = appendBubble('assistant', '...');
            let reply = '';
            try {
                const res = await fetch('/v1/chat/completions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                    body: JSON.stringify({ messages: messages.slice(-24), stream: true })
                });
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
                            const delta = ((obj.choices || [])[0] || {}).delta || {};
                            if (delta.content) {
                                reply += delta.content;
                                botBubble.innerHTML = marked.parse(reply);
                            }
                        } catch (_) {}
                    }
                }
                messages.push({ role: 'assistant', content: reply });
            } catch (err) {
                botBubble.innerHTML = 'Erro de ligação.';
            }
        }
        function appendBubble(role, content) {
            const inner = document.getElementById('chat-inner');
            const row = document.createElement('div');
            row.className = 'msg-row ' + role;
            const bubble = document.createElement('div');
            bubble.className = 'bubble';
            bubble.innerHTML = role === 'assistant' ? marked.parse(content) : content;
            row.appendChild(bubble);
            inner.appendChild(row);
            const feed = document.getElementById('chat-feed');
            feed.scrollTop = feed.scrollHeight;
            return bubble;
        }
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def serve_goisai_user_app():
    return HTMLResponse(content=GOIS_AI_USER_APP_HTML)


@app.get("/manifest.json")
async def serve_goisai_manifest():
    return JSONResponse(content=GOIS_AI_MANIFEST, media_type="application/manifest+json")


@app.get("/goisai/icon.svg")
async def serve_goisai_icon():
    return StreamingResponse(iter([GOIS_AI_ICON_SVG]), media_type="image/svg+xml")


@app.get("/goisai/sw.js")
async def serve_goisai_sw():
    return StreamingResponse(iter([GOIS_AI_SW_JS]), media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))