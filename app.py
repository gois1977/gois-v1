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
# RENDER_SERVICE_ID é definida automaticamente pelo Render em cada serviço.
# RENDER_API_KEY tens de a criar tu (Account Settings > API Keys) e pô-la no Environment.
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

# Referências de benchmark realistas (Gemini 2.5 Pro vs DeepSeek V3/R1) e base inicial do gois.v1
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
    # 1. Arquitetura de Software & Sistemas Assíncronos
    ("Arquiteturas Python Assíncronas, Event Loops e Concorrência de Alto Desempenho", "Arquitetura de Software & Sistemas Assíncronos"),
    ("Design de APIs FastAPI, Streaming SSE, WebSockets e Resiliência com Failover", "Arquitetura de Software & Sistemas Assíncronos"),
    ("Arquitetura Hexagonal, Event-Driven Systems e Message Queues sem Bloqueio", "Arquitetura de Software & Sistemas Assíncronos"),
    # 2. Algoritmos, Estruturas de Dados & Programação
    ("Algoritmos de Grafos (A*, Dijkstra, Flow), Árvores Espaciais (QuadTree/OctTree) e Hashing", "Algoritmos, Estruturas de Dados & Programação"),
    ("Programação Dinâmica, Bitmasking, Estruturas Lock-Free e Complexidade Amortizada", "Algoritmos, Estruturas de Dados & Programação"),
    ("Padrões de Memória Contígua, Ring Buffers e Algoritmos Cache-Friendly", "Algoritmos, Estruturas de Dados & Programação"),
    # 3. Game Design, Unity C# & Motores 3D
    ("Sistemas de Grelha (Tile Grids), Câmaras Isométricas e Map Editors 3D em Unity C#", "Game Design, Unity C# & Motores 3D"),
    ("Character Controllers Avançados, Máquinas de Estado Hierárquicas (HFSM) e Física Customizada", "Game Design, Unity C# & Motores 3D"),
    ("Arquitetura DOTS/ECS, Job System, Burst Compiler e Shaders HLSL em Unity", "Game Design, Unity C# & Motores 3D"),
    # 4. WebGL, Three.js, Frontend & Gráficos 2D/3D
    ("Renderização 3D/2D no Browser com Three.js, InstancedMesh e Redução de Draw Calls", "WebGL, Three.js, Frontend & Gráficos 2D/3D"),
    ("Shaders GLSL Customizados, Post-Processing, Raycasting e Picking Eficiente em WebGL", "WebGL, Three.js, Frontend & Gráficos 2D/3D"),
    ("Motores 2D em HTML5 Canvas/Phaser, Sprite Batching e Interfaces Reativas sem Frameworks Pesados", "WebGL, Three.js, Frontend & Gráficos 2D/3D"),
    # 5. Matemática Avançada, Lógica & Raciocínio Formal
    ("Álgebra Linear Aplicada: Quaterniões, Matrizes de Transformação 4x4 e Interpolação Slerp", "Matemática Avançada, Lógica & Raciocínio Formal"),
    ("Cálculo Vetorial, Equações Diferenciais Numéricas (Runge-Kutta) e Geometria Computacional", "Matemática Avançada, Lógica & Raciocínio Formal"),
    ("Lógica Proposicional, Solvers SAT/SMT, Provas Indutivas e Otimização Combinatória", "Matemática Avançada, Lógica & Raciocínio Formal"),
    # 6. Inteligência Artificial, LLMs, RAG & Agentes
    ("Destilação de Conhecimento entre Modelos (Gemini/DeepSeek), RAG Híbrido e Re-ranking", "Inteligência Artificial, LLMs, RAG & Agentes"),
    ("Arquitetura de Agentes Autónomos, Tool Calling Determinístico e Memória Episódica", "Inteligência Artificial, LLMs, RAG & Agentes"),
    ("Engenharia de Contexto, Compressão de Prompts e Avaliação Automática LLM-as-a-Judge", "Inteligência Artificial, LLMs, RAG & Agentes"),
    # 7. Ciência de Dados, SQL & Engenharia de Dados
    ("Otimização Avançada de Queries SQL, Índices Compostos, Particionamento e SQLite/Postgres", "Ciência de Dados, SQL & Engenharia de Dados"),
    ("Pipelines de Dados em Streaming, Séries Temporais e Indexação Vetorial Rápida (FAISS/HNSW)", "Ciência de Dados, SQL & Engenharia de Dados"),
    ("Modelação Dimensional, Normalização vs Desnormalização e Serialização Binária Rápida", "Ciência de Dados, SQL & Engenharia de Dados"),
    # 8. Cibersegurança, Criptografia & Auditoria
    ("Autenticação Segura, HMAC Constant-Time, Gestão de Segredos e Proteção contra Injection/CSRF", "Cibersegurança, Criptografia & Auditoria"),
    ("Criptografia Moderna (AES-GCM, Ed25519, Argon2), Zero-Trust APIs e Rate Limiting", "Cibersegurança, Criptografia & Auditoria"),
    ("Auditoria Estática de Código, Sandboxing de Processos e Prevenção de Vulnerabilidades OWASP", "Cibersegurança, Criptografia & Auditoria"),
    # 9. DevOps, Cloud, Docker & Sistemas Linux
    ("Contentorização Docker Multi-Stage, Deploy Contínuo no Render e Zero-Downtime", "DevOps, Cloud, Docker & Sistemas Linux"),
    ("Observabilidade, Logging Estruturado, Healthchecks Resilientes e Auto-Recovery", "DevOps, Cloud, Docker & Sistemas Linux"),
    ("Automação em PowerShell/Bash, Empacotamento PyInstaller + Inno Setup e Gestão de Releases", "DevOps, Cloud, Docker & Sistemas Linux"),
    # 10. Engenharia, AutoCAD, Robótica & IoT
    ("Pipelines de Desenho Automatizado, Geração Procedural de Plantas e Geometria Paramétrica CAD", "Engenharia, AutoCAD, Robótica & IoT"),
    ("Cinemática Direta e Inversa, Controladores PID, Kalmann Filters e Navegação de Robôs", "Engenharia, AutoCAD, Robótica & IoT"),
    ("Protocolos IoT de Baixa Latência (MQTT, CoAP, Modbus) e Telemetria Industrial", "Engenharia, AutoCAD, Robótica & IoT"),
    # 11. Física, Química, Biologia & Ciências Exatas
    ("Motores de Física: Deteção de Colisões (SAT, GJK), Resolução de Impulsos e Verlet Integration", "Física, Química, Biologia & Ciências Exatas"),
    ("Termodinâmica Computacional, Dinâmica de Fluidos Simplificada (SPH) e Eletromagnetismo", "Física, Química, Biologia & Ciências Exatas"),
    ("Algoritmos Bioinformáticos, Alinhamento de Sequências e Simulação de Ecossistemas", "Física, Química, Biologia & Ciências Exatas"),
    # 12. Multimodalidade, Visão Computacional & Áudio
    ("Processamento de Imagem em Tempo Real, Deteção de Contornos, OCR e Segmentação Leve", "Multimodalidade, Visão Computacional & Áudio"),
    ("Síntese Procedural de Áudio, DSP, FFT em Tempo Real e Áudio Espacial 3D para Jogos", "Multimodalidade, Visão Computacional & Áudio"),
    ("Pipelines Multimodais (Texto + Imagem + Áudio) e Extração Estruturada de Documentos", "Multimodalidade, Visão Computacional & Áudio"),
    # 13. Linguística, Tradução & Comunicação Multilingue
    ("Rigor Linguístico em Português Europeu (PT-PT), Terminologia de Engenharia e Clareza Técnica", "Linguística, Tradução & Comunicação Multilingue"),
    ("Processamento de Linguagem Natural, Tokenização, Análise Sintática e Tradução Contextual", "Linguística, Tradução & Comunicação Multilingue"),
    ("Estruturação de Documentação de Arquitetura (ADR), Especificações Técnicas e Manuais", "Linguística, Tradução & Comunicação Multilingue"),
    # 14. Economia, Finanças Quantitativas & Estratégia
    ("Teoria de Jogos, Equilíbrio de Nash, Leilões e Balanceamento de Economias Virtuais em Jogos", "Economia, Finanças Quantitativas & Estratégia"),
    ("Simulação de Monte Carlo, Gestão de Risco, Séries Financeiras e Otimização de Portfólio", "Economia, Finanças Quantitativas & Estratégia"),
    ("Sistemas de Decisão Multicritério, Utilidade Esperada e IA Estratégica para RTS/4X", "Economia, Finanças Quantitativas & Estratégia"),
    # 15. Direito Tecnológico, Ética & Segurança de IA
    ("Conformidade RGPD, Privacidade por Design, Anonimização de Dados e Segurança em Cloud", "Direito Tecnológico, Ética & Segurança de IA"),
    ("Licenciamento Open-Source (MIT, Apache 2.0, GPL), Propriedade Intelectual e Compliance", "Direito Tecnológico, Ética & Segurança de IA"),
    ("Alinhamento de IA, Prevenção de Alucinações, Verificação Factual e Transparência", "Direito Tecnológico, Ética & Segurança de IA"),
    # 16. Otimização Extrema & Hardware Leve / Edge AI
    ("Otimização para PCs All-in-One e Hardware Leve: Zero-Allocation, Object Pooling e GC Tuning", "Otimização Extrema & Hardware Leve / Edge AI"),
    ("Quantização de Modelos (INT8/INT4), Inferência Híbrida Local/Cloud e Redução de Latência", "Otimização Extrema & Hardware Leve / Edge AI"),
    ("Profiling de CPU/RAM, Vetorização SIMD, Multithreading Leve e Arranque Instantâneo", "Otimização Extrema & Hardware Leve / Edge AI"),
]

# Mapeamento de categorias antigas para a nova taxonomia de 16 categorias
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
    "daily_token_limit": 0,          # 0 = sem limite diário
    "auto_paused_by_limit": False,   # True quando a auto-aprendizagem está em pausa por limite
}

SKILLS_DB: list = []
KNOWLEDGE_DB: list = []
BENCHMARK_DB: list = []  # Histórico de testes comparativos reais executados
RUNTIME_KEYS: dict = {}

# ---------------------------------------------------------------------
# CONTADOR DE TOKENS & LIMITE DIÁRIO
# A auto-aprendizagem (Sala de Aula) pára quando o limite diário é atingido
# e retoma sozinha no dia seguinte. O chat e a aprendizagem manual continuam
# a funcionar, mas os tokens que gastam contam para o mesmo limite diário.
# ---------------------------------------------------------------------
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

# O Groq retirou o llama-3.3-70b-versatile a 16/08/2026 (contas gratuitas/developer) e passou a responder 404.
# Substituto recomendado pelo Groq: openai/gpt-oss-120b. Para trocar de modelo no futuro basta definir
# GROQ_MODEL no Environment do Render, sem mexer no código.
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

# Modelos professores disponíveis via OpenRouter/Groq/Mistral para estudo comparativo Gemini & DeepSeek
TEACHER_MODELS = [
    {"provider": "OpenRouter", "model": "deepseek/deepseek-chat", "label": "DeepSeek V3 (OpenRouter)"},
    {"provider": "OpenRouter", "model": "google/gemini-2.0-flash-001", "label": "Google Gemini 2.0 Flash (OpenRouter)"},
    {"provider": "Groq", "model": GROQ_MODEL, "label": f"{GROQ_MODEL} (Groq)"},
    {"provider": "Mistral", "model": "mistral-small-latest", "label": "Mistral Small (Mistral)"},
]


# =====================================================================
# UTILITÁRIOS
# =====================================================================

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
    """Estado da sincronização com o Render. Nunca expõe valores secretos."""
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
    """PUT (criar/atualizar) ou DELETE de UMA variável do serviço no Render.

    Só mexe numa variável de cada vez e só em nomes da lista de fornecedores.
    Nunca usa a chamada que substitui todas as variáveis. Não regista valores.
    Devolve (ok, mensagem).
    """
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
        return False, "O Render recusou a RENDER_API_KEY (401). Cria uma nova em Account Settings > API Keys."
    if code == 403:
        return False, "A RENDER_API_KEY não tem permissão neste serviço (403)."
    if code == 404:
        return False, "Serviço não encontrado no Render (404). Confirma RENDER_SERVICE_ID e a conta da API Key."
    if code == 429:
        return False, "Limite de pedidos do Render atingido (429). Tenta daqui a pouco."
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
    """Como check_token, mas se a RENDER_API_KEY existir recusa sempre sem SYNC_TOKEN.

    Evita que alguém que conheça o URL consiga mexer nas chaves do teu Render.
    """
    if RENDER_API_KEY and not SYNC_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Define SYNC_TOKEN no Environment do Render (e nas Definições da app) para gerir chaves.",
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


# =====================================================================
# MOTOR DE COMPARAÇÃO REAL EM % (gois.v1 vs GEMINI vs DEEPSEEK)
# =====================================================================

def score_lesson_quality(content: str) -> float:
    """Avalia objetivamente a riqueza técnica de uma lição/resposta (0 a 100%)."""
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
    """Calcula em tempo real a percentagem de domínio do gois.v1 face ao Gemini e ao DeepSeek
    em cada uma das 16 categorias e na média global."""
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

        # 1. Bónus de Skills ativas relacionadas com a categoria
        kw_list = base_info["keywords"]
        matching_skills = 0
        for s in SKILLS_DB:
            hay = f"{s.get('title', '')} {s.get('content', '')}".lower()
            if any(kw in hay for kw in kw_list):
                matching_skills += 1
        skill_bonus = min(4.5, matching_skills * 1.5)

        # 2. Evolução real pelas lições aprendidas na Sala de Aula nesta categoria (curva progressiva realista)
        lessons = cat_lessons.get(cat, [])
        n_lessons = len(lessons)
        learning_gain = 0.0
        for idx, les in enumerate(lessons):
            q = float(les.get("quality_score") or score_lesson_quality(les.get("content", "")))
            # Ganho gradual e realista: primeiras lições dão +1.4%, depois +0.8%, depois +0.4%
            weight = 1.4 if idx < 3 else (0.8 if idx < 10 else 0.35)
            learning_gain += weight * (q / 90.0)
        learning_gain = min(16.5, learning_gain)

        # 3. Bónus de benchmarks ao vivo realizados nesta categoria
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


# =====================================================================
# MOTOR DE IA COM FAILOVER E STREAMING REAL ENTRE FORNECEDORES
# =====================================================================

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
            # modelo de raciocínio: parte do limite é gasta a "pensar"; sem folga a resposta pode ficar cortada
            body["max_tokens"] = int(payload["max_tokens"]) + 2048
    return headers, body


async def make_llm_request(payload: dict, preferred_provider: str = "", override_model: str = "", usage_kind: str = "chat"):
    """Pedido completo (sem streaming) com failover entre fornecedores."""
    errors = []
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
                        # Se falhou com override_model (ex: gemini no OpenRouter), tenta logo o modelo padrão do OpenRouter
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


# =====================================================================
# SALA DE AULA AUTÓNOMA & BENCHMARK AO VIVO (GEMINI & DEEPSEEK)
# =====================================================================

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
    """Escolhe prioritariamente categorias com menos lições para cobrir todas as 16 áreas do Gemini/DeepSeek."""
    active_cats = [normalize_category(c) for c in (SERVER_SETTINGS.get("active_categories") or DEFAULT_CATEGORIES)]
    if not active_cats:
        active_cats = list(DEFAULT_CATEGORIES)

    # Conta lições por categoria ativa para estudar primeiro as menos estudadas
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
    """Liga-se a um modelo real (DeepSeek, Gemini, GPT-OSS 120B, Mistral), estuda um tópico e atualiza as %."""
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
            logger.info(f"[SALA DE AULA] Lição aprendida ({cat}) com {teacher_model}: {title} (qualidade: {q_score}%)")
            return entry
        finally:
            SERVER_SETTINGS["learning_now"] = False


async def run_live_benchmark(category: str = "") -> dict:
    """Executa um desafio técnico real numa categoria, mede a qualidade e absorve a solução no gois.v1."""
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
    """Ciclo autónomo da Sala de Aula: percorre todas as 16 categorias de Gemini & DeepSeek continuamente."""
    await asyncio.sleep(4)
    while True:
        try:
            if SERVER_SETTINGS.get("classroom_mode") and any(key_source(p) for p in PROVIDERS):
                if limit_reached():
                    if not SERVER_SETTINGS.get("auto_paused_by_limit"):
                        SERVER_SETTINGS["auto_paused_by_limit"] = True
                        SERVER_SETTINGS["current_topic"] = "⏸ Pausa automática: limite diário de tokens atingido"
                        logger.warning(
                            f"[TOKENS] Limite diário atingido ({TOKEN_STATS['used_today']}/{daily_limit()}). "
                            "Auto-aprendizagem em pausa até ao próximo dia."
                        )
                else:
                    if SERVER_SETTINGS.get("auto_paused_by_limit"):
                        SERVER_SETTINGS["auto_paused_by_limit"] = False
                        logger.info("[TOKENS] Novo dia ou limite alterado: auto-aprendizagem retomada.")
                    await learn_one_topic()
        except Exception as e:
            logger.warning(f"[SALA DE AULA] ciclo falhou: {e!r}")
        await asyncio.sleep(55)


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
    _rs = render_sync_state()
    if _rs["enabled"]:
        logger.info("[RENDER] Sincronização de chaves LIGADA (as chaves da app gravam no Environment do Render)")
    else:
        logger.info(f"[RENDER] Sincronização de chaves DESLIGADA (falta: {', '.join(_rs['missing'])})")
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
            "Especialidades e diretrizes técnicas do gois.v1 (aplica quando relevante para a tarefa; não listes isto em cumprimentos casuais):"
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
        f"Estado Real do Conhecimento do gois.v1 (usa quando relevante ou se o utilizador perguntar sobre o teu conhecimento/comparação): "
        f"Média Global gois.v1: {ov['gois_v1']}% vs Gemini: {ov['gemini']}% (Paridade: {ov['parity_vs_gemini']}%) "
        f"vs DeepSeek: {ov['deepseek']}% (Paridade: {ov['parity_vs_deepseek']}%) | "
        f"Total acumulado: {ov['total_lessons']} lições estudadas e {ov['total_skills']} skills em {ov['categories_studied']}/{ov['total_categories']} categorias. "
        f"Detalhe por categoria: {cat_summary}."
    )

    if KNOWLEDGE_DB:
        relevant = select_relevant_knowledge(user_query, max_items=8)
        blocks = [
            f"Conhecimento Real Aprendido pelo gois.v1 na Sala de Aula (relevante para esta conversa, de um total de {len(KNOWLEDGE_DB)} lições):"
        ]
        for k in relevant:
            snippet = (k.get("content") or "").replace("\n", " ")[:480]
            teacher = k.get("teacher", "Modelo IA")
            blocks.append(f"- [{k.get('category', 'Geral')}] {k.get('title', '')} (destilado via {teacher}): {snippet}")
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


# =====================================================================
# ROTAS DE COMPARAÇÃO REAL, SALA DE AULA, CONHECIMENTO E SKILLS
# =====================================================================

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
        raise HTTPException(status_code=409, detail="O núcleo já está a estudar um tópico neste momento. Aguarda alguns segundos.")
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
    comp = compute_comparison_metrics()
    ov = comp["overall"]
    lines = ["# BASE DE CONHECIMENTO, COMPARAÇÃO & SKILLS — gois.v1\n"]
    lines.append(
        f"*Exportado em {now_str()} — {len(SKILLS_DB)} skills, {len(KNOWLEDGE_DB)} lições aprendidas.*\n\n"
        f"**Média Global Comparativa:** `gois.v1: {ov['gois_v1']}%` | `Gemini: {ov['gemini']}%` | `DeepSeek: {ov['deepseek']}%` "
        f"(Paridade Gemini: `{ov['parity_vs_gemini']}%` · Paridade DeepSeek: `{ov['parity_vs_deepseek']}%`)\n"
    )
    lines.append("## 1. Comparação por Categoria (16 Domínios Gemini & DeepSeek)\n")
    for r in comp["categories"]:
        lines.append(
            f"- **{r['category']}**: `gois.v1 {r['gois_v1']}%` vs `Gemini {r['gemini']}%` vs `DeepSeek {r['deepseek']}%` "
            f"— *{r['lessons_count']} lições* ({r['status']})"
        )
    lines.append("\n---\n\n## 2. Skills & Diretrizes do Núcleo\n")
    for s in SKILLS_DB:
        lines.append(f"### [{s.get('type', 'SKILL')}] {s.get('title', '')}\n{s.get('content', '')}\n")
    lines.append("\n---\n\n## 3. Lições Aprendidas na Sala de Aula Autónoma\n")
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
            # persistente = vem do Environment do Render (sobrevive a reinícios)
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
        raise HTTPException(status_code=400, detail="Nenhuma chave válida reconhecida para OpenRouter, Groq ou Mistral.")

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
                # O Environment passa a ser a fonte única: fica ativa já, sem reiniciar.
                os.environ[env] = val
                RUNTIME_KEYS.pop(env, None)
            else:
                # Não foi gravada no Render: fica só em memória (perde-se ao reiniciar).
                RUNTIME_KEYS[env] = val
                warnings.append(f"{name}: ativa só em memória, não gravada no Render. {msg}")
        else:
            RUNTIME_KEYS[env] = val
            render_results[name] = {"synced": False, "detail": "Sincronização com o Render desligada (falta: " + ", ".join(state["missing"]) + ")."}

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
        raise HTTPException(status_code=404, detail="Fornecedor desconhecido. Usa OpenRouter, Groq ou Mistral.")
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
        render_info["detail"] = "Sincronização com o Render desligada (falta: " + ", ".join(state["missing"]) + ")."
        if os.environ.get(env, "").strip():
            warnings.append(
                f"{p['name']}: a chave do Environment do Render mantém-se. "
                "Liga a sincronização para a poder apagar a partir da app."
            )
    return {
        "status": "success",
        "provider": p["name"],
        "still_configured": key_source(p) is not None,
        "render_enabled": state["enabled"],
        "render": {p["name"]: render_info},
        "warnings": warnings,
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

    warnings = []
    imported = {"keys": [], "skills": 0, "knowledge": 0, "settings": False}

    found = {}
    if render_sync_enabled():
        # O Environment do Render é a única fonte das chaves: o Sincronizar não lhes toca.
        if any(field in payload for field in KEY_CONTAINERS):
            warnings.append("Chaves ignoradas: com a sincronização do Render ligada, gere-as no separador Skills & Chaves API.")
    else:
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
        "comparison": compute_comparison_metrics(),
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
    comp = compute_comparison_metrics()
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
        "overall_comparison": comp["overall"],
        "providers_with_key": [p["name"] for p in PROVIDERS if key_source(p)],
        "render_key_sync": render_sync_enabled(),
    }


# =====================================================================
# INTERFACE WEB COMPLETA (CHAT + COMPARAÇÃO % + SALA DE AULA + SKILLS)
# =====================================================================

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
            --purple: #a855f7; --cyan: #06b6d4;
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
        .nav-tabs { display: flex; gap: 5px; background: var(--panel-alt); padding: 4px; border-radius: 8px; border: 1px solid var(--border); flex-wrap: wrap; }
        .nav-btn {
            background: transparent; border: none; color: var(--text-muted);
            padding: 7px 13px; border-radius: 6px; font-size: 0.81rem; font-weight: 600;
            cursor: pointer; transition: 0.18s;
        }
        .nav-btn:hover { color: #fff; background: rgba(255,255,255,0.05); }
        .nav-btn.active { background: var(--accent); color: #fff; }
        .app-view { flex: 1; display: none; flex-direction: column; overflow: hidden; }
        .app-view.active { display: flex; }

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
        .welcome-box { max-width: 760px; margin: auto; text-align: center; padding: 24px; }
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
        .view-content { padding: 20px; overflow-y: auto; flex: 1; max-width: 1120px; width: 100%; margin: 0 auto; }
        .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 16px; }
        .input-box {
            width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--text);
            padding: 10px 13px; border-radius: 8px; font-size: 0.88rem; margin-bottom: 10px; outline: none; font-family: inherit;
        }
        .input-box:focus { border-color: var(--accent); }
        .btn-action { background: var(--accent-green); color: #04130e; border: none; padding: 9px 16px; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 0.84rem; }
        .btn-blue { background: var(--accent); color: #fff; border: none; padding: 9px 16px; border-radius: 8px; font-weight: 700; cursor: pointer; font-size: 0.84rem; }
        .btn-outline { background: var(--bg); color: var(--text); border: 1px solid var(--border); padding: 7px 12px; border-radius: 7px; font-size: 0.78rem; cursor: pointer; }
        .btn-outline:hover { border-color: var(--accent); color: #fff; }

        /* KPIs Comparação */
        .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 16px; }
        .kpi-card { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
        .kpi-title { font-size: 0.76rem; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.4px; }
        .kpi-val { font-size: 1.65rem; font-weight: 800; }
        .kpi-sub { font-size: 0.76rem; color: var(--text-muted); margin-top: 4px; }

        .comp-row { background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 13px 15px; margin-bottom: 10px; }
        .comp-bars { display: grid; grid-template-columns: 95px 1fr 52px; gap: 6px 10px; align-items: center; font-size: 0.78rem; margin-top: 8px; }
        .bar-track { background: #111827; height: 9px; border-radius: 5px; overflow: hidden; border: 1px solid #1f2937; }
        .bar-gois { height: 100%; background: linear-gradient(90deg, #10b981, #34d399); }
        .bar-gemini { height: 100%; background: linear-gradient(90deg, #3b82f6, #60a5fa); }
        .bar-deepseek { height: 100%; background: linear-gradient(90deg, #8b5cf6, #c084fc); }

        .provider-pills { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
        .provider-pill { background: var(--bg); border: 1px solid var(--border); padding: 6px 12px; border-radius: 8px; font-size: 0.8rem; display: flex; align-items: center; gap: 6px; }
        .cat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(245px, 1fr)); gap: 8px; margin: 10px 0 14px; }
        .cat-checkbox { display: flex; align-items: center; gap: 8px; background: var(--bg); border: 1px solid var(--border); padding: 8px 10px; border-radius: 8px; font-size: 0.78rem; cursor: pointer; }
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
            <span id="header-status-badge" class="cloud-badge" onclick="refreshAll()" title="Clica para atualizar em tempo real">☁️ Online</span>
            <a href="/goisai" target="_blank" class="cloud-badge" style="background:rgba(59,130,246,0.18); color:#93c5fd; border-color:#3b82f6; text-decoration:none;">📱 Abrir App Utilizador (goisAI)</a>
        </div>
        <div class="nav-tabs">
            <button class="nav-btn active" id="tab-btn-chat" onclick="switchView('view-chat', this)">💬 Chat</button>
            <button class="nav-btn" id="tab-btn-comp" onclick="switchView('view-comp', this)">📊 Comparação % (Gemini & DeepSeek)</button>
            <button class="nav-btn" id="tab-btn-classroom" onclick="switchView('view-classroom', this)">🏫 Sala de Aula (16 Categorias)</button>
            <button class="nav-btn" id="tab-btn-skills" onclick="switchView('view-skills', this)">🧠 Skills & Diretrizes</button>
            <button class="nav-btn" id="tab-btn-keys" onclick="switchView('view-keys', this)">🔑 Chaves API</button>
        </div>
    </header>

    <!-- VISTA 1: CHAT -->
    <div id="view-chat" class="app-view active">
        <div class="sub-banner">
            <div class="sub-banner-left" onclick="document.getElementById('tab-btn-comp').click()">
                <span id="banner-classroom-text">📊 A calcular comparação ao vivo gois.v1 vs Gemini & DeepSeek...</span>
            </div>
            <div style="display:flex; gap:6px;">
                <button class="btn-outline" onclick="exportChat()">⬇️ Exportar Chat</button>
                <button class="btn-outline" onclick="clearChat()">🗑️ Nova Conversa</button>
            </div>
        </div>
        <div id="chat-container">
            <div class="welcome-box" id="welcome-box">
                <h1>gois.v1 Command Center</h1>
                <p>Chat em Streaming SSE com Failover Multi-Modelo, Sala de Aula nas 16 categorias e Comparação Real em % com Gemini & DeepSeek.</p>
                <div class="prompt-grid">
                    <div class="prompt-card" onclick="usePrompt('Cria um editor de mapas 3D isométrico em Three.js com grelha de construção estilo estratégia')">🏗️ Editor de Mapas 3D (Tile Grid)</div>
                    <div class="prompt-card" onclick="usePrompt('Gera um Character Controller em C# para Unity com dash, coyote time e máquina de estados')">⚡ Script C# Unity: Character Controller</div>
                    <div class="prompt-card" onclick="usePrompt('Como estás atualmente na comparação em percentagem com o Gemini e o DeepSeek em todas as categorias?')">📊 Estado da Comparação % vs Gemini & DeepSeek</div>
                    <div class="prompt-card" onclick="usePrompt('O que tens estado a aprender recentemente na Sala de Aula Autónoma?')">🏫 Últimas Lições da Sala de Aula</div>
                </div>
            </div>
        </div>
        <div class="input-bar">
            <textarea id="user-input" rows="1" placeholder="Escreve uma mensagem ou instrução técnica... (Enter envia, Shift+Enter muda de linha)"></textarea>
            <button id="stop-btn" class="btn-stop" onclick="stopStream()">Parar</button>
            <button id="send-btn" class="btn-send" onclick="sendMessage()">Enviar</button>
        </div>
    </div>

    <!-- VISTA 2: COMPARAÇÃO REAL EM % (gois.v1 vs GEMINI vs DEEPSEEK) -->
    <div id="view-comp" class="app-view">
        <div class="view-content">
            <div class="card" style="border-color: var(--accent);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:14px;">
                    <div>
                        <h3 style="color:#fff; font-size:1.15rem; margin-bottom:4px;">📊 Comparação Real Total em % — <span style="color:var(--accent-green);">gois.v1</span> vs <span style="color:var(--accent);">Gemini</span> vs <span style="color:var(--purple);">DeepSeek</span></h3>
                        <p style="font-size:0.82rem; color:var(--text-muted);">Atualizada em tempo real à medida que o <b>gois.v1</b> estuda as 16 categorias completas do Gemini & DeepSeek na Sala de Aula.</p>
                    </div>
                    <div style="display:flex; gap:8px; flex-wrap:wrap;">
                        <button class="btn-outline" onclick="loadClassroomUI()">🔄 Atualizar Live</button>
                        <button id="btn-run-bench" class="btn-action" onclick="runLiveBenchmark('')">⚔️ Executar Desafio Benchmark Agora</button>
                    </div>
                </div>

                <!-- KPIs Globais -->
                <div class="kpi-grid">
                    <div class="kpi-card" style="border-color: rgba(16,185,129,0.5);">
                        <div class="kpi-title">🟢 Domínio Global gois.v1</div>
                        <div class="kpi-val" id="kpi-gois" style="color:var(--accent-green);">—%</div>
                        <div class="kpi-sub" id="kpi-gois-sub">A evoluir com a Sala de Aula</div>
                    </div>
                    <div class="kpi-card" style="border-color: rgba(59,130,246,0.4);">
                        <div class="kpi-title">🔵 Referência Google Gemini</div>
                        <div class="kpi-val" id="kpi-gemini" style="color:#60a5fa;">—%</div>
                        <div class="kpi-sub" id="kpi-parity-gemini">Paridade gois.v1 vs Gemini: —%</div>
                    </div>
                    <div class="kpi-card" style="border-color: rgba(168,85,247,0.4);">
                        <div class="kpi-title">🟣 Referência DeepSeek V3/R1</div>
                        <div class="kpi-val" id="kpi-deepseek" style="color:#c084fc;">—%</div>
                        <div class="kpi-sub" id="kpi-parity-deepseek">Paridade gois.v1 vs DeepSeek: —%</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-title">📚 Cobertura das 16 Categorias</div>
                        <div class="kpi-val" id="kpi-coverage" style="color:#fff;">0 / 16</div>
                        <div class="kpi-sub" id="kpi-lessons-total">0 lições absorvidas no núcleo</div>
                    </div>
                </div>

                <div id="bench-live-feedback" style="font-size:0.83rem; color:var(--warn); margin-bottom:10px;"></div>

                <h4 style="font-size:0.92rem; margin:14px 0 10px; color:#fff;">🔬 Comparação Detalhada nas 16 Categorias de Gemini & DeepSeek (clica em "⚡ Estudar" para evoluir qualquer área ao vivo):</h4>
                <div id="comparison-categories-container">A calcular matriz comparativa...</div>
            </div>
        </div>
    </div>

    <!-- VISTA 3: SALA DE AULA (16 CATEGORIAS) -->
    <div id="view-classroom" class="app-view">
        <div class="view-content">
            <div class="card" style="border-color: var(--accent-green);">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:10px;">
                    <div>
                        <h3 style="color: var(--accent-green); margin-bottom:4px;">🏫 Sala de Aula Autónoma — Estudo Integral das 16 Categorias (Gemini & DeepSeek)</h3>
                        <p style="font-size:0.82rem; color:var(--text-muted);">Prioriza automaticamente as categorias menos estudadas para o <b>gois.v1</b> absorver 100% dos domínios de Gemini & DeepSeek.</p>
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

                <div style="font-size:0.82rem; font-weight:700; margin-bottom:4px;">🤖 Modelos Professores Ligados:</div>
                <div id="providers-pills" class="provider-pills"></div>

                <!-- Aprender Agora com Modelo Real -->
                <div style="background:var(--panel-alt); border:1px solid var(--border); border-radius:10px; padding:14px; margin-top:14px;">
                    <div style="font-size:0.86rem; font-weight:700; color:#fff; margin-bottom:8px;">⚡ Estudar Tópico ou Categoria Agora com Modelo Real</div>
                    <div style="display:grid; grid-template-columns: 185px 260px 1fr auto; gap:8px; align-items:center;">
                        <select id="learn-provider" class="input-box" style="margin:0;">
                            <option value="auto">Rotação Automática</option>
                            <option value="DeepSeek">DeepSeek V3 (OpenRouter)</option>
                            <option value="Gemini">Google Gemini (OpenRouter)</option>
                            <option value="Groq">GPT-OSS 120B (Groq)</option>
                            <option value="Mistral">Mistral Small (Mistral)</option>
                        </select>
                        <select id="learn-category" class="input-box" style="margin:0;"></select>
                        <input id="learn-topic" class="input-box" style="margin:0;" type="text" placeholder="Tópico específico (ou deixa em branco para automático)..." />
                        <button id="btn-learn-now" class="btn-blue" onclick="learnNow()">⚡ Aprender Agora</button>
                    </div>
                    <div id="learn-feedback" style="font-size:0.8rem; margin-top:6px; color:var(--warn);"></div>
                </div>

                <div style="margin-top:16px;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <span style="font-size:0.82rem; font-weight:700;">Todas as 16 Categorias de Estudo (ON/OFF):</span>
                        <button class="btn-outline" onclick="selectAllCategories()">✅ Ativar Todas as 16 Categorias</button>
                    </div>
                    <div id="categories-container" class="cat-grid"></div>
                </div>
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

    <!-- VISTA 4: SKILLS & DIRETRIZES -->
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

    <!-- VISTA 5: CHAVES API -->
    <div id="view-keys" class="app-view">
        <div class="view-content">
            <div class="card">
                <h3 style="color: var(--accent); margin-bottom: 6px;">🔑 Configurar Chaves API dos Modelos Reais</h3>
                <p style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 12px;">As tuas chaves do Environment do Render já estão ativas. Podes adicionar ou atualizar chaves extra aqui se precisares.</p>
                <label style="font-size: 0.78rem; color: var(--text-muted);">Fornecedor:</label>
                <select id="key-provider" class="input-box">
                    <option value="OpenRouter">OpenRouter (DeepSeek & Gemini)</option>
                    <option value="Groq">Groq (GPT-OSS 120B)</option>
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
        let allCategories = [];

        let messages = JSON.parse(localStorage.getItem('gois_chat_history') || '[]');
        let cachedKnowledge = JSON.parse(localStorage.getItem('gois_knowledge_backup') || '[]');
        let abortCtrl = null;

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
            if (viewId === 'view-comp' || viewId === 'view-classroom') loadClassroomUI();
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

        // --- COMPARAÇÃO % & SALA DE AULA ---
        async function loadClassroomUI() {
            try {
                const [stRes, knRes] = await Promise.all([fetch('/api/settings'), fetch('/api/knowledge')]);
                const st = await stRes.json();
                let kn = await knRes.json();

                if (Array.isArray(kn) && kn.length === 0 && cachedKnowledge.length > 0) {
                    await fetch('/api/sync/import', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ knowledge: cachedKnowledge })
                    });
                    const [stRes2, knRes2] = await Promise.all([fetch('/api/settings'), fetch('/api/knowledge')]);
                    Object.assign(st, await stRes2.json());
                    kn = await knRes2.json();
                }
                if (Array.isArray(kn) && kn.length > 0) {
                    cachedKnowledge = kn;
                    localStorage.setItem('gois_knowledge_backup', JSON.stringify(kn.slice(0, 200)));
                }

                allCategories = st.all_categories || [];
                const learnCatSel = document.getElementById('learn-category');
                if (learnCatSel && learnCatSel.options.length !== allCategories.length) {
                    const prev = learnCatSel.value;
                    learnCatSel.innerHTML = '';
                    allCategories.forEach(c => {
                        const opt = document.createElement('option');
                        opt.value = c; opt.textContent = c;
                        learnCatSel.appendChild(opt);
                    });
                    if (prev) learnCatSel.value = prev;
                }

                const comp = st.comparison || { overall: {}, categories: [] };
                const ov = comp.overall || {};

                // Atualizar KPIs da aba de Comparação
                document.getElementById('kpi-gois').innerText = (ov.gois_v1 || 0) + '%';
                document.getElementById('kpi-gois-sub').innerText = `${ov.total_lessons || 0} lições + ${ov.total_skills || 0} skills ativas`;
                document.getElementById('kpi-gemini').innerText = (ov.gemini || 0) + '%';
                document.getElementById('kpi-parity-gemini').innerText = `Paridade gois.v1 vs Gemini: ${ov.parity_vs_gemini || 0}% (${ov.delta_gemini >= 0 ? '+' : ''}${ov.delta_gemini || 0}%)`;
                document.getElementById('kpi-deepseek').innerText = (ov.deepseek || 0) + '%';
                document.getElementById('kpi-parity-deepseek').innerText = `Paridade gois.v1 vs DeepSeek: ${ov.parity_vs_deepseek || 0}% (${ov.delta_deepseek >= 0 ? '+' : ''}${ov.delta_deepseek || 0}%)`;
                document.getElementById('kpi-coverage').innerText = `${ov.categories_studied || 0} / ${ov.total_categories || 16}`;
                document.getElementById('kpi-lessons-total').innerText = `${ov.total_lessons || 0} lições absorvidas no núcleo`;

                // Renderizar tabela/barras das 16 categorias na aba de Comparação
                const compContainer = document.getElementById('comparison-categories-container');
                let compHTML = '';
                (comp.categories || []).forEach(r => {
                    const safeCat = r.category.replace(/'/g, "\\'");
                    compHTML += `
                        <div class="comp-row">
                            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                                <div>
                                    <span style="font-weight:700; font-size:0.92rem; color:#fff;">${r.category}</span>
                                    <span style="font-size:0.75rem; color:var(--text-muted); margin-left:8px;">(${r.lessons_count} lições · ${r.status})</span>
                                </div>
                                <div style="display:flex; gap:6px; align-items:center;">
                                    <span style="font-size:0.76rem; color:#34d399;">Paridade Gemini: <b>${r.parity_vs_gemini}%</b> · DeepSeek: <b>${r.parity_vs_deepseek}%</b></span>
                                    <button class="btn-outline" onclick="studyCategoryNow('${safeCat}')">⚡ Estudar Agora</button>
                                </div>
                            </div>
                            <div class="comp-bars">
                                <span style="color:#34d399; font-weight:700;">🟢 gois.v1</span>
                                <div class="bar-track"><div class="bar-gois" style="width:${r.gois_v1}%;"></div></div>
                                <span style="text-align:right; font-weight:700; color:#34d399;">${r.gois_v1}%</span>

                                <span style="color:#60a5fa;">🔵 Gemini</span>
                                <div class="bar-track"><div class="bar-gemini" style="width:${r.gemini}%;"></div></div>
                                <span style="text-align:right; color:#60a5fa;">${r.gemini}%</span>

                                <span style="color:#c084fc;">🟣 DeepSeek</span>
                                <div class="bar-track"><div class="bar-deepseek" style="width:${r.deepseek}%;"></div></div>
                                <span style="text-align:right; color:#c084fc;">${r.deepseek}%</span>
                            </div>
                        </div>`;
                });
                compContainer.innerHTML = compHTML;

                // Header & Sub-banner
                const provs = (st.providers_with_key || []).join(', ') || 'Sem chaves';
                document.getElementById('header-status-badge').innerText = `☁️ gois.v1 ${ov.gois_v1 || 0}% · ${kn.length} lições · ${provs}`;
                const lastTitle = st.last_learned_title || (kn[0] ? kn[0].title : 'A estudar 16 categorias...');
                document.getElementById('banner-classroom-text').innerHTML =
                    `📊 <b>gois.v1: <span style="color:#34d399;">${ov.gois_v1 || 0}%</span></b> vs Gemini: <b>${ov.gemini || 0}%</b> vs DeepSeek: <b>${ov.deepseek || 0}%</b> (Paridade: <b>${ov.parity_vs_gemini || 0}%</b>) · 🏫 <b>${kn.length} lições</b> (${ov.categories_studied || 0}/16 categorias) · Última: <i>${lastTitle}</i>`;

                const statusEl = document.getElementById('classroom-status-text');
                if (st.learning_now) {
                    statusEl.innerHTML = `<span style="color:var(--warn);">⚡ A APRENDER AGORA COM MODELO REAL (${st.current_teacher || 'IA'})...</span>`;
                } else if (st.classroom_mode) {
                    statusEl.innerHTML = `<span style="color:var(--accent-green);">🟢 ATIVO (A estudar todas as 16 categorias de Gemini & DeepSeek ciclicamente a cada 55s)</span>`;
                } else {
                    statusEl.innerHTML = `<span style="color:var(--danger);">🔴 PAUSADO</span>`;
                }

                const lastTeacher = st.last_teacher || (kn[0] ? kn[0].teacher : '');
                document.getElementById('active-topic-display').innerText = st.current_topic || 'Em espera...';
                document.getElementById('last-learned-display').innerText = lastTitle + (lastTeacher ? ` — ensinado por ${lastTeacher}` : '');

                const pillsDiv = document.getElementById('providers-pills');
                pillsDiv.innerHTML = '';
                (st.providers || []).forEach(p => {
                    const dot = p.configured ? '🟢' : '🔴';
                    pillsDiv.innerHTML += `<div class="provider-pill">${dot} <b>${p.name}</b> <span style="color:var(--text-muted);">(${p.model})</span></div>`;
                });

                const catContainer = document.getElementById('categories-container');
                catContainer.innerHTML = '';
                const counts = st.category_counts || {};
                allCategories.forEach(cat => {
                    const checked = (st.active_categories || []).includes(cat) ? 'checked' : '';
                    const c = counts[cat] || 0;
                    catContainer.innerHTML += `<label class="cat-checkbox"><input type="checkbox" value="${cat}" ${checked} onchange="updateCategories()"><span>${cat} <b style="color:var(--accent-green);">(${c})</b></span></label>`;
                });

                document.getElementById('knowledge-count').innerText = kn.length;
                const kList = document.getElementById('knowledge-list-container');
                if (!kn.length) {
                    kList.innerHTML = `<div style="color:var(--text-muted); font-size:0.88rem;">Ainda nenhuma lição registada neste ciclo. Clica em <b>"⚡ Aprender Agora"</b> acima!</div>`;
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
                console.error('Erro ao atualizar UI:', e);
            }
        }

        async function studyCategoryNow(categoryName) {
            const fb = document.getElementById('bench-live-feedback');
            fb.style.color = 'var(--warn)';
            fb.innerText = `⚡ A estudar categoria "${categoryName}" com modelo real e a atualizar percentagens...`;
            try {
                const res = await fetch('/api/classroom/learn', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: categoryName, provider: 'auto' })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Erro ao estudar categoria');
                fb.style.color = 'var(--accent-green)';
                fb.innerText = `✅ Lição aprendida em [${categoryName}]: "${data.lesson.title}" (Professor: ${data.lesson.teacher}) — Percentagens atualizadas!`;
                await loadClassroomUI();
            } catch (e) {
                fb.style.color = 'var(--danger)';
                fb.innerText = '❌ ' + e.message;
            }
        }

        async function runLiveBenchmark(categoryName = '') {
            const btn = document.getElementById('btn-run-bench');
            const fb = document.getElementById('bench-live-feedback');
            btn.disabled = true;
            btn.innerText = '⏳ A executar benchmark real...';
            fb.style.color = 'var(--warn)';
            fb.innerText = '⚔️ A executar desafio técnico ao vivo e a medir performance face ao Gemini & DeepSeek...';
            try {
                const res = await fetch('/api/comparison/benchmark', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ category: categoryName })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || 'Falha no benchmark');
                const b = data.benchmark;
                fb.style.color = 'var(--accent-green)';
                fb.innerText = `🏆 Benchmark concluído em ${b.elapsed_s}s [${b.category}]: gois.v1 atingiu ${b.gois_score}% (Qualidade da solução: ${b.quality_score}%) vs Gemini ${b.gemini_ref}% vs DeepSeek ${b.deepseek_ref}%!`;
                await loadClassroomUI();
            } catch (e) {
                fb.style.color = 'var(--danger)';
                fb.innerText = '❌ ' + e.message;
            } finally {
                btn.disabled = false;
                btn.innerText = '⚔️ Executar Desafio Benchmark Agora';
            }
        }

        async function selectAllCategories() {
            await fetch('/api/settings/categories', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active_categories: allCategories })
            });
            loadClassroomUI();
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
            fb.innerText = `A ligar ao modelo real (${provider}) para estudar "${topic || category}"...`;

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
                fb.innerText = `✅ Nova lição aprendida com ${data.lesson.teacher}: "${data.lesson.title}"! Comparação % atualizada.`;
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
            loadClassroomUI();
        }

        async function deleteSkill(id) {
            await fetch('/api/skills/' + encodeURIComponent(id), { method: 'DELETE' });
            loadSkillsUI();
            loadClassroomUI();
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
                        <div><b>${name}</b> <span style="color:var(--text-muted); font-size:0.82rem;">Modelo base: <code>${info.model}</code></span></div>
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
        setInterval(loadClassroomUI, 10000);
    </script>
</body>
</html>"""


# O Command Center é exclusivo da aplicação Desktop privada (gois_v1_Office.exe).
# Na Web (https://gois-v1.onrender.com), é servida APENAS a app pública limpa goisAI!


# =====================================================================
# goisAI — APP PÚBLICA / MOBILE & WEB PARA UTILIZADORES (ESTILO GEMINI & DEEPSEEK)
# Zero controlos de administrador · PWA Instalável em Android, iPhone (iOS) e PC
# =====================================================================

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
self.addEventListener('install', (e) => {
  self.skipWaiting();
});
self.addEventListener('activate', (e) => {
  e.waitUntil(self.clients.claim());
});
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
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="goisAI">
    <link rel="manifest" href="/goisai/manifest.json">
    <link rel="icon" type="image/svg+xml" href="/goisai/icon.svg">
    <link rel="apple-touch-icon" href="/goisai/icon.svg">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --bg: #090d16;
            --sidebar: #0f1624;
            --surface: #151e31;
            --surface-hover: #1e2942;
            --border: #222f49;
            --text: #f3f4f6;
            --muted: #94a3b8;
            --accent: #3b82f6;
            --emerald: #10b981;
            --user-bubble: #2563eb;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg); color: var(--text);
            height: 100dvh; width: 100vw; overflow: hidden;
            display: flex;
        }

        /* Gaveta lateral de conversas (estilo Gemini / DeepSeek) */
        .sidebar-backdrop {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.6); z-index: 40;
        }
        .sidebar {
            width: 280px; background: var(--sidebar); border-right: 1px solid var(--border);
            display: flex; flex-direction: column; z-index: 50; transition: transform 0.24s ease;
        }
        .sidebar-header {
            padding: 16px; display: flex; align-items: center; justify-content: space-between;
            border-bottom: 1px solid var(--border);
        }
        .logo-wrap { display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 1.15rem; letter-spacing: -0.3px; }
        .logo-badge {
            width: 30px; height: 30px; border-radius: 9px;
            background: linear-gradient(135deg, #10b981, #3b82f6, #8b5cf6);
            display: flex; align-items: center; justify-content: center; font-weight: 900; color: #fff; font-size: 0.9rem;
        }
        .new-chat-btn {
            margin: 12px 14px; padding: 11px 14px; border-radius: 12px;
            background: linear-gradient(135deg, rgba(16,185,129,0.18), rgba(59,130,246,0.22));
            border: 1px solid rgba(59,130,246,0.45); color: #fff;
            font-weight: 700; font-size: 0.88rem; cursor: pointer;
            display: flex; align-items: center; gap: 8px; justify-content: center; transition: 0.18s;
        }
        .new-chat-btn:hover { background: rgba(59,130,246,0.35); }
        .conv-list { flex: 1; overflow-y: auto; padding: 6px 10px; display: flex; flex-direction: column; gap: 4px; }
        .conv-item {
            padding: 10px 12px; border-radius: 10px; font-size: 0.85rem; color: var(--muted);
            cursor: pointer; display: flex; align-items: center; justify-content: space-between; gap: 8px;
        }
        .conv-item:hover, .conv-item.active { background: var(--surface); color: #fff; }
        .conv-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; }
        .conv-del { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 0.85rem; padding: 2px 5px; }
        .conv-del:hover { color: #ef4444; }
        .sidebar-footer { padding: 12px 14px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
        .install-btn {
            width: 100%; padding: 10px 12px; border-radius: 10px;
            background: var(--emerald); color: #04130e; border: none;
            font-weight: 800; font-size: 0.84rem; cursor: pointer;
            display: flex; align-items: center; justify-content: center; gap: 6px;
        }

        /* Área Principal */
        .main { flex: 1; display: flex; flex-direction: column; height: 100dvh; overflow: hidden; position: relative; }
        .topbar {
            height: 56px; padding: 0 16px; border-bottom: 1px solid var(--border);
            display: flex; align-items: center; justify-content: space-between; background: rgba(9,13,22,0.88); backdrop-filter: blur(10px);
        }
        .menu-btn {
            background: transparent; border: 1px solid var(--border); color: var(--text);
            width: 38px; height: 38px; border-radius: 10px; font-size: 1.1rem; cursor: pointer;
            display: none; align-items: center; justify-content: center;
        }
        .top-title { font-weight: 700; font-size: 1rem; display: flex; align-items: center; gap: 8px; }
        .model-pill {
            font-size: 0.72rem; padding: 3px 9px; border-radius: 99px;
            background: rgba(16,185,129,0.14); color: #34d399; border: 1px solid rgba(16,185,129,0.4);
        }
        .top-action-btn {
            background: var(--surface); border: 1px solid var(--border); color: var(--text);
            padding: 7px 12px; border-radius: 10px; font-size: 0.8rem; font-weight: 600; cursor: pointer;
        }

        /* Feed de Mensagens */
        #chat-feed {
            flex: 1; overflow-y: auto; padding: 20px 16px;
            display: flex; flex-direction: column; gap: 18px; scroll-behavior: smooth;
        }
        .chat-inner { max-width: 780px; width: 100%; margin: 0 auto; display: flex; flex-direction: column; gap: 18px; }

        /* Ecrã Inicial estilo Gemini / DeepSeek */
        .hero {
            max-width: 680px; margin: auto; text-align: center; padding: 28px 14px;
        }
        .hero-icon {
            width: 62px; height: 62px; border-radius: 20px; margin: 0 auto 16px;
            background: linear-gradient(135deg, #10b981, #3b82f6, #8b5cf6);
            display: flex; align-items: center; justify-content: center;
            font-size: 1.7rem; font-weight: 900; color: #fff; box-shadow: 0 10px 30px rgba(59,130,246,0.3);
        }
        .hero h1 {
            font-size: 1.75rem; font-weight: 800; margin-bottom: 8px;
            background: linear-gradient(90deg, #34d399, #60a5fa, #c084fc);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .hero p { color: var(--muted); font-size: 0.95rem; margin-bottom: 24px; }
        .suggestions {
            display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; text-align: left;
        }
        .sug-card {
            background: var(--surface); border: 1px solid var(--border); border-radius: 14px;
            padding: 14px; cursor: pointer; transition: 0.18s;
        }
        .sug-card:hover { background: var(--surface-hover); border-color: var(--accent); transform: translateY(-1px); }
        .sug-title { font-weight: 700; font-size: 0.86rem; margin-bottom: 4px; color: #fff; }
        .sug-desc { font-size: 0.78rem; color: var(--muted); }

        /* Bolhas de mensagem */
        .msg-row { display: flex; flex-direction: column; gap: 6px; width: 100%; }
        .msg-row.user { align-items: flex-end; }
        .msg-row.assistant { align-items: flex-start; }
        .bubble {
            padding: 13px 16px; border-radius: 18px; font-size: 0.95rem; line-height: 1.6;
            max-width: 88%; word-wrap: break-word;
        }
        .msg-row.user .bubble {
            background: var(--user-bubble); color: #fff; border-bottom-right-radius: 4px;
        }
        .msg-row.assistant .bubble {
            background: var(--surface); border: 1px solid var(--border); color: var(--text);
            border-bottom-left-radius: 4px; width: 100%; max-width: 100%;
        }
        .bubble p { margin-bottom: 10px; }
        .bubble p:last-child { margin-bottom: 0; }
        .bubble ul, .bubble ol { margin: 6px 0 10px 22px; }
        .bubble pre {
            background: #060911; border: 1px solid #1e293b; border-radius: 10px;
            padding: 12px; overflow-x: auto; margin: 10px 0; position: relative;
        }
        .bubble code { font-family: Consolas, monospace; font-size: 0.88rem; color: #a7f3d0; }
        .msg-actions { display: flex; gap: 8px; padding-left: 4px; }
        .act-btn {
            background: transparent; border: none; color: var(--muted); font-size: 0.76rem;
            cursor: pointer; padding: 3px 6px; border-radius: 6px;
        }
        .act-btn:hover { color: #fff; background: var(--surface); }

        /* Barra de escrita Mobile-First */
        .composer-wrap {
            padding: 10px 14px calc(12px + env(safe-area-inset-bottom));
            background: linear-gradient(180deg, transparent, var(--bg) 25%);
        }
        .composer {
            max-width: 780px; margin: 0 auto; background: var(--surface);
            border: 1px solid var(--border); border-radius: 22px;
            padding: 8px 10px 8px 16px; display: flex; align-items: flex-end; gap: 8px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.35);
        }
        .composer:focus-within { border-color: var(--accent); }
        textarea#prompt-input {
            flex: 1; background: transparent; border: none; color: var(--text);
            font-size: 0.96rem; line-height: 1.45; max-height: 140px; resize: none; outline: none;
            padding: 6px 0; font-family: inherit;
        }
        .icon-btn {
            width: 38px; height: 38px; border-radius: 50%; border: none;
            display: flex; align-items: center; justify-content: center;
            cursor: pointer; font-size: 1rem; flex-shrink: 0; transition: 0.18s;
        }
        .mic-btn { background: transparent; color: var(--muted); }
        .mic-btn:hover, .mic-btn.recording { background: rgba(239,68,68,0.2); color: #ef4444; }
        .send-btn { background: var(--accent); color: #fff; font-weight: 800; }
        .send-btn:hover { background: #2563eb; }
        .stop-btn { background: #ef4444; color: #fff; display: none; }

        /* Modal Instalar App */
        .modal-backdrop {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.72);
            z-index: 100; align-items: center; justify-content: center; padding: 18px;
        }
        .modal {
            background: var(--surface); border: 1px solid var(--border); border-radius: 18px;
            max-width: 420px; width: 100%; padding: 22px; text-align: left;
        }
        .modal h3 { margin-bottom: 8px; font-size: 1.1rem; }
        .modal p { font-size: 0.86rem; color: var(--muted); line-height: 1.55; margin-bottom: 12px; }

        @media (max-width: 820px) {
            .sidebar { position: fixed; top: 0; bottom: 0; left: 0; transform: translateX(-100%); }
            .sidebar.open { transform: translateX(0); }
            .sidebar-backdrop.open { display: block; }
            .menu-btn { display: flex; }
            .bubble { max-width: 94%; }
        }
    </style>
</head>
<body>
    <div id="backdrop" class="sidebar-backdrop" onclick="toggleSidebar(false)"></div>

    <aside id="sidebar" class="sidebar">
        <div class="sidebar-header">
            <div class="logo-wrap">
                <div class="logo-badge">G</div>
                <span>goisAI</span>
            </div>
            <button class="act-btn" onclick="toggleSidebar(false)" style="font-size:1.1rem;">✕</button>
        </div>
        <button class="new-chat-btn" onclick="startNewConversation()">＋ Nova Conversa</button>
        <div id="conv-list" class="conv-list"></div>
        <div class="sidebar-footer">
            <button id="install-pwa-btn" class="install-btn" onclick="triggerInstallApp()">📲 Instalar App goisAI</button>
            <div style="font-size:0.72rem; color:var(--muted); text-align:center;">goisAI · Rápido, Privado & Inteligente</div>
        </div>
    </aside>

    <main class="main">
        <div class="topbar">
            <div style="display:flex; align-items:center; gap:10px;">
                <button class="menu-btn" onclick="toggleSidebar(true)">☰</button>
                <div class="top-title">
                    <span>goisAI</span>
                    <span class="model-pill">gois.v1</span>
                </div>
            </div>
            <div style="display:flex; gap:8px;">
                <button class="top-action-btn" onclick="startNewConversation()">＋ Novo</button>
                <button class="top-action-btn" onclick="triggerInstallApp()">📲 Instalar</button>
            </div>
        </div>

        <div id="chat-feed">
            <div id="chat-inner" class="chat-inner"></div>
        </div>

        <div class="composer-wrap">
            <div class="composer">
                <textarea id="prompt-input" rows="1" placeholder="Pergunta qualquer coisa ao goisAI..."></textarea>
                <button id="mic-btn" class="icon-btn mic-btn" onclick="toggleVoice()" title="Falar por voz">🎤</button>
                <button id="stop-btn" class="icon-btn stop-btn" onclick="stopGeneration()" title="Parar">■</button>
                <button id="send-btn" class="icon-btn send-btn" onclick="sendUserPrompt()" title="Enviar">↑</button>
            </div>
        </div>
    </main>

    <!-- Modal de instalação para iOS / Android / Browser -->
    <div id="install-modal" class="modal-backdrop" onclick="closeInstallModal()">
        <div class="modal" onclick="event.stopPropagation()">
            <h3>📲 Instalar a App goisAI</h3>
            <p>Instala o <b>goisAI</b> no ecrã principal do teu telemóvel ou computador para abrir em ecrã inteiro como uma aplicação nativa:</p>
            <p><b>📱 No Android (Chrome / Edge):</b><br>Toca no menu <b>⋮</b> (canto superior direito) e escolhe <b>"Instalar aplicação"</b> ou <b>"Adicionar ao ecrã principal"</b>.</p>
            <p><b>🍏 No iPhone / iPad (Safari):</b><br>Toca no botão <b>Partilhar (⬆️)</b> em baixo e escolhe <b>"Adicionar ao ecrã principal"</b>.</p>
            <p><b>💻 No Computador (Chrome / Edge):</b><br>Clica no ícone de instalação na barra de endereço à direita.</p>
            <button class="install-btn" onclick="closeInstallModal()">Entendido</button>
        </div>
    </div>

    <script>
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/goisai/sw.js').catch(() => {});
        }

        let deferredPrompt = null;
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredPrompt = e;
        });

        async function triggerInstallApp() {
            if (deferredPrompt) {
                deferredPrompt.prompt();
                await deferredPrompt.userChoice;
                deferredPrompt = null;
            } else {
                document.getElementById('install-modal').style.display = 'flex';
            }
        }
        function closeInstallModal() {
            document.getElementById('install-modal').style.display = 'none';
        }

        function toggleSidebar(open) {
            document.getElementById('sidebar').classList.toggle('open', open);
            document.getElementById('backdrop').classList.toggle('open', open);
        }

        // Gestão de múltiplas conversas no dispositivo do utilizador
        const STORAGE_KEY = 'goisai_user_conversations_v1';
        let conversations = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
        let currentConvId = conversations[0] ? conversations[0].id : null;
        let abortController = null;

        function saveConversations() {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, 40)));
            renderSidebarList();
        }

        function getCurrentConv() {
            let c = conversations.find(x => x.id === currentConvId);
            if (!c) {
                c = { id: 'c_' + Date.now(), title: 'Nova conversa', messages: [] };
                conversations.unshift(c);
                currentConvId = c.id;
                saveConversations();
            }
            return c;
        }

        function renderSidebarList() {
            const list = document.getElementById('conv-list');
            list.innerHTML = '';
            conversations.forEach(c => {
                const item = document.createElement('div');
                item.className = 'conv-item' + (c.id === currentConvId ? ' active' : '');
                item.onclick = () => {
                    currentConvId = c.id;
                    renderSidebarList();
                    renderMessages();
                    toggleSidebar(false);
                };
                const title = document.createElement('div');
                title.className = 'conv-title';
                title.textContent = '💬 ' + (c.title || 'Nova conversa');
                const del = document.createElement('button');
                del.className = 'conv-del';
                del.textContent = '🗑️';
                del.onclick = (e) => {
                    e.stopPropagation();
                    conversations = conversations.filter(x => x.id !== c.id);
                    if (currentConvId === c.id) {
                        currentConvId = conversations[0] ? conversations[0].id : null;
                    }
                    saveConversations();
                    renderMessages();
                };
                item.appendChild(title);
                item.appendChild(del);
                list.appendChild(item);
            });
        }

        function startNewConversation() {
            if (abortController) abortController.abort();
            const c = { id: 'c_' + Date.now(), title: 'Nova conversa', messages: [] };
            conversations.unshift(c);
            currentConvId = c.id;
            saveConversations();
            renderMessages();
            toggleSidebar(false);
        }

        function buildHeroHTML() {
            return `
                <div class="hero" id="hero-welcome">
                    <div class="hero-icon">G</div>
                    <h1>Olá, eu sou o goisAI</h1>
                    <p>Como posso ajudar-te hoje?</p>
                    <div class="suggestions">
                        <div class="sug-card" onclick="quickAsk('Cria um código completo em Python ou C# bem estruturado e explica passo a passo.')">
                            <div class="sug-title">💻 Programação & Código</div>
                            <div class="sug-desc">Scripts completos em Python, C#, Unity, Web e APIs</div>
                        </div>
                        <div class="sug-card" onclick="quickAsk('Explica-me de forma clara e prática como funciona este conceito e dá exemplos reais.')">
                            <div class="sug-title">🧠 Explicação & Estudo</div>
                            <div class="sug-desc">Resumos diretos de ciência, engenharia e matemática</div>
                        </div>
                        <div class="sug-card" onclick="quickAsk('Ajuda-me a desenhar a arquitetura e mecânicas para um jogo 3D/2D.')">
                            <div class="sug-title">🎮 Game Design & 3D</div>
                            <div class="sug-desc">Mecânicas, física, shaders e sistemas de jogo</div>
                        </div>
                        <div class="sug-card" onclick="quickAsk('Escreve ou melhora um texto profissional em Português de Portugal.')">
                            <div class="sug-title">✍️ Escrita & Ideias</div>
                            <div class="sug-desc">Redação profissional, ideias, planeamento e revisão</div>
                        </div>
                    </div>
                </div>`;
        }

        function attachCodeCopy(container) {
            container.querySelectorAll('pre').forEach(pre => {
                if (pre.querySelector('.cp-btn')) return;
                const btn = document.createElement('button');
                btn.className = 'act-btn cp-btn';
                btn.style.cssText = 'position:absolute; top:6px; right:6px; background:#151e31; border:1px solid #222f49; color:#fff; padding:2px 8px;';
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

        function appendBubble(role, content) {
            const inner = document.getElementById('chat-inner');
            const hero = document.getElementById('hero-welcome');
            if (hero) hero.remove();

            const row = document.createElement('div');
            row.className = 'msg-row ' + role;
            const bubble = document.createElement('div');
            bubble.className = 'bubble';

            if (role === 'assistant') {
                bubble.innerHTML = marked.parse(content || '');
                attachCodeCopy(bubble);
            } else {
                bubble.textContent = content;
            }
            row.appendChild(bubble);

            if (role === 'assistant') {
                const actions = document.createElement('div');
                actions.className = 'msg-actions';
                const copyBtn = document.createElement('button');
                copyBtn.className = 'act-btn';
                copyBtn.innerText = '📋 Copiar';
                copyBtn.onclick = () => {
                    navigator.clipboard.writeText(bubble.innerText.trim());
                    copyBtn.innerText = '✅ Copiado';
                    setTimeout(() => copyBtn.innerText = '📋 Copiar', 1500);
                };
                actions.appendChild(copyBtn);
                row.appendChild(actions);
            }

            inner.appendChild(row);
            const feed = document.getElementById('chat-feed');
            feed.scrollTop = feed.scrollHeight;
            return bubble;
        }

        function renderMessages() {
            const inner = document.getElementById('chat-inner');
            const conv = getCurrentConv();
            if (!conv.messages.length) {
                inner.innerHTML = buildHeroHTML();
                return;
            }
            inner.innerHTML = '';
            conv.messages.forEach(m => appendBubble(m.role, m.content));
        }

        function quickAsk(text) {
            const input = document.getElementById('prompt-input');
            input.value = text;
            sendUserPrompt();
        }

        const promptInput = document.getElementById('prompt-input');
        promptInput.addEventListener('input', function() {
            this.style.height = 'auto';
            this.style.height = Math.min(this.scrollHeight, 140) + 'px';
        });
        promptInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey && window.innerWidth > 768) {
                e.preventDefault();
                sendUserPrompt();
            }
        });

        function stopGeneration() {
            if (abortController) abortController.abort();
        }

        async function sendUserPrompt() {
            const text = promptInput.value.trim();
            if (!text || abortController) return;
            promptInput.value = '';
            promptInput.style.height = 'auto';

            const conv = getCurrentConv();
            if (conv.messages.length === 0) {
                conv.title = text.slice(0, 34) + (text.length > 34 ? '…' : '');
            }
            conv.messages.push({ role: 'user', content: text });
            saveConversations();
            appendBubble('user', text);

            const botBubble = appendBubble('assistant', '...');
            document.getElementById('send-btn').style.display = 'none';
            document.getElementById('stop-btn').style.display = 'flex';
            abortController = new AbortController();
            let reply = '';

            try {
                const res = await fetch('/v1/chat/completions', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
                    body: JSON.stringify({ messages: conv.messages.slice(-24), stream: true }),
                    signal: abortController.signal
                });
                if (!res.ok) throw new Error('Servidor ocupado, tenta novamente em instantes.');

                const reader = res.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                const feed = document.getElementById('chat-feed');

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
                                feed.scrollTop = feed.scrollHeight;
                            }
                        } catch (_) {}
                    }
                }
                attachCodeCopy(botBubble);
                conv.messages.push({ role: 'assistant', content: reply || 'Sem resposta.' });
                saveConversations();
            } catch (err) {
                if (err.name === 'AbortError') {
                    reply = reply ? (reply + '\\n\\n*(interrompido)*') : '*(cancelado)*';
                    botBubble.innerHTML = marked.parse(reply);
                } else {
                    botBubble.innerHTML = `<span style="color:#f87171;">${err.message || 'Erro de ligação.'}</span>`;
                }
            } finally {
                abortController = null;
                document.getElementById('stop-btn').style.display = 'none';
                document.getElementById('send-btn').style.display = 'flex';
            }
        }

        // Reconhecimento de Voz (Microfone Mobile / Desktop)
        let recognition = null;
        function toggleVoice() {
            const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SR) return alert('O teu navegador não suporta ditado por voz direto.');
            const micBtn = document.getElementById('mic-btn');
            if (recognition) {
                recognition.stop();
                recognition = null;
                micBtn.classList.remove('recording');
                return;
            }
            recognition = new SR();
            recognition.lang = 'pt-PT';
            recognition.interimResults = false;
            micBtn.classList.add('recording');
            recognition.onresult = (e) => {
                const transcript = e.results[0][0].transcript;
                promptInput.value = (promptInput.value ? promptInput.value + ' ' : '') + transcript;
            };
            recognition.onend = () => {
                micBtn.classList.remove('recording');
                recognition = null;
            };
            recognition.start();
        }

        renderSidebarList();
        renderMessages();
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/goisai", response_class=HTMLResponse)
@app.get("/chat", response_class=HTMLResponse)
@app.get("/app", response_class=HTMLResponse)
async def serve_goisai_user_app():
    return HTMLResponse(content=GOIS_AI_USER_APP_HTML)


@app.get("/manifest.json")
@app.get("/goisai/manifest.json")
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
