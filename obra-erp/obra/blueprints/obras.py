# -*- coding: utf-8 -*-
"""Obras (centros de custo) e dashboard."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import executar, registrar_auditoria, todos, um
from ..security import (garantir_acesso_obra, login_obrigatorio, obras_permitidas,
                        papel_obrigatorio, usuario_atual)
from ..services.fechamento import dashboard_obra
from ..utils import ApiErro, agora_iso, data_iso, hoje_iso, numero, texto

bp = Blueprint("obras", __name__, url_prefix="/api")


@bp.get("/obras")
@login_obrigatorio
def listar():
    incluir_arquivadas = request.args.get("arquivadas") == "1"
    permitidas = obras_permitidas()

    sql = """SELECT o.id, o.nome, o.cliente, o.endereco, o.orcamento_total, o.data_inicio,
                    o.previsao_fim, o.status, o.sigla, o.arquivada,
                    (SELECT COUNT(*) FROM semanas s WHERE s.obra_id = o.id) AS total_semanas
               FROM obras o"""
    condicoes, args = [], []
    if not incluir_arquivadas:
        condicoes.append("o.arquivada = 0")
    if permitidas is not None:
        if not permitidas:
            return jsonify([])
        condicoes.append(f"o.id IN ({','.join('?' * len(permitidas))})")
        args += permitidas
    if condicoes:
        sql += " WHERE " + " AND ".join(condicoes)
    sql += " ORDER BY o.arquivada, o.nome COLLATE NOCASE"

    return jsonify([dict(r) for r in todos(sql, args)])


@bp.post("/obras")
@papel_obrigatorio("gestor")
def criar():
    dados = request.get_json(silent=True) or {}
    nome = texto(dados.get("nome"), 140, True, "o nome da obra")
    cur = executar(
        """INSERT INTO obras (nome, cliente, endereco, orcamento_total, data_inicio,
                              previsao_fim, sigla, status, arquivada, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'ativa', 0, ?, ?)""",
        (nome, texto(dados.get("cliente"), 140), texto(dados.get("endereco"), 220),
         numero(dados.get("orcamento_total"), 0, 0, campo="orçamento total"),
         data_iso(dados.get("data_inicio"), hoje_iso()), data_iso(dados.get("previsao_fim")),
         texto(dados.get("sigla"), 12).upper(), agora_iso(), agora_iso()),
    )
    registrar_auditoria(usuario_atual(), "criou_obra", "obra", cur.lastrowid, nome)
    return jsonify(dict(um("SELECT * FROM obras WHERE id = ?", (cur.lastrowid,)))), 201


@bp.get("/obras/<int:obra_id>")
@login_obrigatorio
def detalhar(obra_id: int):
    garantir_acesso_obra(obra_id)
    row = um("SELECT * FROM obras WHERE id = ?", (obra_id,))
    return jsonify(dict(row))


@bp.put("/obras/<int:obra_id>")
@papel_obrigatorio("gestor")
def atualizar(obra_id: int):
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}
    mapa = {
        "nome": lambda v: texto(v, 140, True, "o nome da obra"),
        "cliente": lambda v: texto(v, 140),
        "sigla": lambda v: texto(v, 12).upper(),
        "endereco": lambda v: texto(v, 220),
        "orcamento_total": lambda v: numero(v, 0, 0, campo="orçamento total"),
        "data_inicio": lambda v: data_iso(v),
        "previsao_fim": lambda v: data_iso(v),
        "status": lambda v: texto(v, 20) if texto(v, 20) in ("ativa", "pausada", "concluida") else "ativa",
        "arquivada": lambda v: 1 if v else 0,
    }
    campos, valores = [], []
    for chave, conversor in mapa.items():
        if chave in dados:
            campos.append(f"{chave} = ?")
            valores.append(conversor(dados[chave]))
    if not campos:
        raise ApiErro("Nada para atualizar.")
    campos.append("atualizado_em = ?"); valores.append(agora_iso())
    valores.append(obra_id)
    executar(f"UPDATE obras SET {', '.join(campos)} WHERE id = ?", valores)
    registrar_auditoria(usuario_atual(), "editou_obra", "obra", obra_id)
    return jsonify({"ok": True})


@bp.delete("/obras/<int:obra_id>")
@papel_obrigatorio("gestor")
def remover(obra_id: int):
    """Arquiva por padrão. `?definitivo=1` apaga tudo em cascata (irreversível)."""
    garantir_acesso_obra(obra_id)
    if request.args.get("definitivo") == "1":
        executar("DELETE FROM obras WHERE id = ?", (obra_id,))
        registrar_auditoria(usuario_atual(), "excluiu_obra", "obra", obra_id)
        return jsonify({"ok": True, "excluida": True})
    executar("UPDATE obras SET arquivada = 1, status = 'concluida', atualizado_em = ? WHERE id = ?",
             (agora_iso(), obra_id))
    registrar_auditoria(usuario_atual(), "arquivou_obra", "obra", obra_id)
    return jsonify({"ok": True, "arquivada": True})


@bp.get("/obras/<int:obra_id>/dashboard")
@login_obrigatorio
def dashboard(obra_id: int):
    garantir_acesso_obra(obra_id)
    dados = dashboard_obra(obra_id)
    if usuario_atual()["papel"] == "mestre":
        # Mestre de obra não enxerga números financeiros.
        dados["totais"] = {
            k: v for k, v in dados["totais"].items()
            if k in ("equipe_ativa", "total_fotos", "total_semanas", "dias_de_obra")
        }
        dados["ultimas_despesas"] = []
        dados["contas_a_vencer"] = []
        if dados["semana_atual"]:
            dados["semana_atual"]["financeiro"] = {}
    return jsonify(dados)

