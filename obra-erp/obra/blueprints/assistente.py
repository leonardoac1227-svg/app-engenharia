# -*- coding: utf-8 -*-
"""Assistente de IA na tela: interpreta a fala/arquivos e grava as ações escolhidas.

`/assistente` só propõe. `/assistente/aplicar` grava o que o usuário
confirmou, conferindo tudo de novo (o navegador não é confiável) e passando
pelos endpoints de sempre, via `Executor` — as mesmas travas dos formulários.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from datetime import date

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from .. import config
from ..db import registrar_auditoria, um
from ..security import (PODE_FINANCEIRO, exigir_lancamento, garantir_acesso_obra, login_obrigatorio,
                        obra_do_dia)
from ..services import assistente, ia_provedores
from ..services.executor_acoes import ErroAcao, Executor, ordenar
from ..utils import ApiErro, _conteudo_confere, data_iso, extensao, segunda_da_semana, texto
from .diario import criar_semana_em

bp = Blueprint("assistente", __name__, url_prefix="/api")

MAX_ARQUIVOS = 10
MIME_POR_EXTENSAO = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "heic": "image/heic", "heif": "image/heif", "pdf": "application/pdf",
    "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg", "mp3": "audio/mpeg", "m4a": "audio/mp4",
    "wav": "audio/wav", "webm": "audio/webm", "aac": "audio/aac",
}

# Freio por usuário: protege a cota gratuita do Gemini (e o bolso, no Claude)
# de um botão apertado em loop.
_pedidos: dict[int, deque] = defaultdict(deque)
_trava = threading.Lock()


def _conferir_limite(usuario_id: int) -> None:
    agora = time.time()
    with _trava:
        fila = _pedidos[usuario_id]
        while fila and agora - fila[0] > 3600:
            fila.popleft()
        if len(fila) >= config.IA_LIMITE_HORA:
            raise ApiErro("Muitos pedidos ao assistente na última hora. Aguarde alguns minutos.",
                          429, "limite_ia")
        fila.append(agora)


def _ler_arquivos() -> list[dict]:
    recebidos = [a for a in request.files.getlist("arquivos") if a and a.filename]
    if len(recebidos) > MAX_ARQUIVOS:
        raise ApiErro(f"Envie até {MAX_ARQUIVOS} arquivos por mensagem.")
    arquivos = []
    for arquivo in recebidos:
        nome = secure_filename(arquivo.filename) or "anexo"
        ext = extensao(nome)
        mime = MIME_POR_EXTENSAO.get(ext)
        if not mime:
            raise ApiErro(f"\"{arquivo.filename}\": envie foto (JPG, PNG, WEBP, HEIC) ou PDF.")
        dados = arquivo.stream.read()
        if not dados:
            continue
        if not _conteudo_confere(dados, ext):
            raise ApiErro(f"\"{arquivo.filename}\" não é uma imagem ou PDF de verdade.")
        arquivos.append({"nome": arquivo.filename[:120], "mime": mime, "dados": dados})
    return arquivos


def _historico() -> list[dict]:
    try:
        bruto = json.loads(request.form.get("historico") or "[]")
    except ValueError:
        return []
    return [h for h in bruto if isinstance(h, dict)][-assistente.MAX_HISTORICO:] \
        if isinstance(bruto, list) else []


@bp.get("/assistente/status")
@login_obrigatorio
def status():
    return jsonify({"disponivel": ia_provedores.disponivel(),
                    "provedores": ia_provedores.provedores_configurados()})


@bp.post("/assistente/testar")
@login_obrigatorio
def testar():
    """Pedido mínimo à IA: diz se ela responde, em quanto tempo, ou o motivo exato da falha."""
    usuario = exigir_lancamento()
    if usuario["papel"] != "gestor":
        raise ApiErro("Somente o gestor testa a conexão da IA.", 403)
    return jsonify({"provedores": ia_provedores.testar()})


@bp.post("/obras/<int:obra_id>/assistente")
@login_obrigatorio
def interpretar(obra_id: int):
    garantir_acesso_obra(obra_id)
    usuario = exigir_lancamento()
    _conferir_limite(usuario["id"])

    mensagem = texto(request.form.get("mensagem"), 6000)
    arquivos = _ler_arquivos()
    if not mensagem and not arquivos:
        raise ApiErro("Fale, escreva ou anexe algo para o assistente analisar.")

    obra = dict(um("SELECT id, nome, cliente, endereco FROM obras WHERE id = ?", (obra_id,)))
    try:
        resultado = assistente.interpretar(
            [obra], mensagem, request.form.get("data_referencia"), arquivos, _historico(),
            pode_financeiro=usuario["papel"] in PODE_FINANCEIRO, pode_gestor=usuario["papel"] == "gestor",
            obra_padrao=obra_id, prazo=100)
    except ia_provedores.IaIndisponivel as erro:
        raise ApiErro(str(erro), 503, "ia_indisponivel")

    registrar_auditoria(usuario, "usou_assistente", "obra", obra_id,
                        f"{len(resultado['acoes'])} ação(ões) · {resultado['fonte']} {resultado['modelo']}")
    resultado.pop("acoes_sem_obra", None)
    return jsonify(resultado)


@bp.post("/obras/<int:obra_id>/assistente/aplicar")
@login_obrigatorio
def aplicar(obra_id: int):
    """Grava as ações confirmadas na tela. Devolve o resultado de cada uma, na ordem recebida."""
    garantir_acesso_obra(obra_id)
    usuario = exigir_lancamento()
    try:
        pedidas = json.loads(request.form.get("acoes") or "[]")
    except ValueError:
        raise ApiErro("Ações em formato inválido.")
    if not isinstance(pedidas, list) or not pedidas:
        raise ApiErro("Nenhuma ação para gravar.")
    arquivos = _ler_arquivos()

    resultados = []
    executor = Executor(current_app._get_current_object(), usuario["id"], obra_id)
    pode_financeiro = usuario["papel"] in PODE_FINANCEIRO
    # Cadastro antes do ponto, lançamento antes da baixa: grava na ordem do catálogo.
    fila = [p if isinstance(p, dict) else {} for p in pedidas]
    sequencia = [x["_i"] for x in ordenar([{"tipo": p.get("tipo", ""), "_i": i} for i, p in enumerate(fila)])]
    for indice in sequencia:
        pedida = fila[indice]
        conferidas, avisos = assistente.reconferir(
            obra_id, [pedida], arquivos, pode_financeiro, usuario["papel"] == "gestor",
            request.form.get("data_referencia"))
        if not conferidas:
            resultados.append({"indice": indice, "ok": False,
                               "erro": avisos[0] if avisos else "Nada a gravar nesta ação."})
            continue
        acao = conferidas[0]
        try:
            extra = executor.gravar(acao, arquivos)
            resultados.append({"indice": indice, "ok": True, "descricao": acao["descricao"], **extra})
        except ErroAcao as erro:
            resultados.append({"indice": indice, "ok": False, "erro": erro.mensagem, "codigo": erro.codigo})
    registrar_auditoria(usuario, "aplicou_assistente", "obra", obra_id,
                        f"{sum(r['ok'] for r in resultados)}/{len(resultados)} gravada(s)")
    return jsonify({"resultados": sorted(resultados, key=lambda r: r["indice"])})


@bp.post("/obras/<int:obra_id>/dia-por-data")
@login_obrigatorio
def dia_por_data(obra_id: int):
    """Dia do diário de uma data. Sem a semana, o gestor a tem criada na hora."""
    garantir_acesso_obra(obra_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    alvo = data_iso(dados.get("data"), None, "data")
    if not alvo:
        raise ApiErro("Informe a data.")

    linha = um("""SELECT d.id, d.semana_id FROM dias d JOIN semanas s ON s.id = d.semana_id
                   WHERE s.obra_id = ? AND d.data = ?""", (obra_id, alvo))
    if linha:
        return jsonify({"dia_id": linha["id"], "semana_id": linha["semana_id"], "semana_criada": False})

    if usuario["papel"] != "gestor":
        raise ApiErro("Ainda não existe semana para essa data. Peça ao gestor para criá-la.", 404)
    inicio = segunda_da_semana(date.fromisoformat(alvo))
    existente = um("SELECT id FROM semanas WHERE obra_id = ? AND data_inicio = ?", (obra_id, inicio))
    semana_id = existente["id"] if existente else criar_semana_em(obra_id, inicio)["id"]
    if existente:
        from ..services.fechamento import garantir_dias
        garantir_dias(semana_id)
    linha = um("SELECT id FROM dias WHERE semana_id = ? AND data = ?", (semana_id, alvo))
    if not linha:
        raise ApiErro("Não foi possível localizar o dia dessa data.", 500)
    return jsonify({"dia_id": linha["id"], "semana_id": semana_id, "semana_criada": not existente})


@bp.post("/dias/<int:dia_id>/rdo/redigir")
@login_obrigatorio
def redigir_rdo(dia_id: int):
    """Texto técnico do RDO a partir do que foi lançado no dia. Não grava."""
    obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    _conferir_limite(usuario["id"])
    try:
        return jsonify(assistente.redigir_rdo(dia_id, usuario["papel"] in PODE_FINANCEIRO))
    except ia_provedores.IaIndisponivel as erro:
        raise ApiErro(str(erro), 503, "ia_indisponivel")
