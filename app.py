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