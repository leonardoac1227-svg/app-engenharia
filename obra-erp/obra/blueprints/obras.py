# -*- coding: utf-8 -*-
"""Obras (centros de custo), dashboard e etapas do cronograma."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import executar, registrar_auditoria, todos, um
from ..security import (garantir_acesso_obra, login_obrigatorio, obra_da_etapa, obras_permitidas,
                        papel_obrigatorio, usuario_atual)
from ..services.fechamento import dashboard_obra
from ..utils import ApiErro, agora_iso, data_iso, hoje_iso, numero, texto

bp = Blueprint("obras", __name__, url_prefix="/api")

# Modelo de EAP para obra residencial — evita a página em branco na hora de
# montar o cronograma. Pesos somam 100.
MODELO_ETAPAS = [
    ("Serviços preliminares", 5), ("Fundação", 12), ("Estrutura", 20),
    ("Alvenaria", 12), ("Cobertura", 8), ("Instalações elétricas", 7),
    ("Instalações hidráulicas", 7), ("Revestimentos", 12),
    ("Esquadrias", 6), ("Pintura e acabamento", 8), ("Limpeza e entrega", 3),
]


@bp.get("/obras")
@login_obrigatorio
def listar():
    incluir_arquivadas = request.args.get("arquivadas") == "1"
    permitidas = obras_permitidas()

    sql = """SELECT o.id, o.nome, o.cliente, o.endereco, o.orcamento_total, o.data_inicio,
                    o.previsao_fim, o.status, o.arquivada,
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
                              previsao_fim, status, arquivada, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, 'ativa', 0, ?, ?)""",
        (nome, texto(dados.get("cliente"), 140), texto(dados.get("endereco"), 220),
         numero(dados.get("orcamento_total"), 0, 0, campo="orçamento total"),
         data_iso(dados.get("data_inicio"), hoje_iso()), data_iso(dados.get("previsao_fim")),
         agora_iso(), agora_iso()),
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


# ---------------------------------------------------------------- etapas

@bp.get("/obras/<int:obra_id>/etapas")
@login_obrigatorio
def listar_etapas(obra_id: int):
    garantir_acesso_obra(obra_id)
    rows = todos(
        """SELECT id, nome, ordem, peso, percentual, custo_previsto
             FROM etapas WHERE obra_id = ? ORDER BY ordem, id""", (obra_id,))
    etapas = [dict(r) for r in rows]

    # Custo já realizado por etapa (despesas vinculadas), para o previsto x
    # realizado financeiro por etapa do cronograma.
    realizados = {r["etapa_id"]: r["t"] for r in todos(
        """SELECT x.etapa_id, COALESCE(SUM(x.valor), 0) AS t
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND x.etapa_id IS NOT NULL GROUP BY x.etapa_id""", (obra_id,))}
    for e in etapas:
        e["custo_realizado"] = round(realizados.get(e["id"], 0) or 0, 2)
    return jsonify(etapas)


@bp.post("/obras/<int:obra_id>/etapas")
@papel_obrigatorio("gestor")
def criar_etapa(obra_id: int):
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}

    if dados.get("modelo"):  # cria a EAP padrão de uma vez
        ordem = um("SELECT COALESCE(MAX(ordem), 0) AS m FROM etapas WHERE obra_id = ?", (obra_id,))["m"]
        for nome, peso in MODELO_ETAPAS:
            ordem += 1
            executar(
                """INSERT INTO etapas (obra_id, nome, ordem, peso, percentual, custo_previsto, criado_em)
                   VALUES (?, ?, ?, ?, 0, 0, ?)""",
                (obra_id, nome, ordem, peso, agora_iso()))
        registrar_auditoria(usuario_atual(), "criou_etapas_modelo", "obra", obra_id)
        return jsonify({"ok": True, "criadas": len(MODELO_ETAPAS)}), 201

    nome = texto(dados.get("nome"), 120, True, "o nome da etapa")
    ordem = um("SELECT COALESCE(MAX(ordem), 0) AS m FROM etapas WHERE obra_id = ?", (obra_id,))["m"] + 1
    cur = executar(
        """INSERT INTO etapas (obra_id, nome, ordem, peso, percentual, custo_previsto, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (obra_id, nome, ordem,
         numero(dados.get("peso"), 0, 0, 100, "o peso"),
         numero(dados.get("percentual"), 0, 0, 100, "o percentual"),
         numero(dados.get("custo_previsto"), 0, 0, campo="o custo previsto"),
         agora_iso()),
    )
    return jsonify({"id": cur.lastrowid}), 201


@bp.put("/etapas/<int:etapa_id>")
@papel_obrigatorio("gestor")
def atualizar_etapa(etapa_id: int):
    obra_da_etapa(etapa_id)
    dados = request.get_json(silent=True) or {}
    mapa = {
        "nome": lambda v: texto(v, 120, True, "o nome da etapa"),
        "peso": lambda v: numero(v, 0, 0, 100, "o peso"),
        "percentual": lambda v: numero(v, 0, 0, 100, "o percentual"),
        "custo_previsto": lambda v: numero(v, 0, 0, campo="o custo previsto"),
        "ordem": lambda v: numero(v, 0, 0, campo="a ordem"),
    }
    campos, valores = [], []
    for chave, conversor in mapa.items():
        if chave in dados:
            campos.append(f"{chave} = ?"); valores.append(conversor(dados[chave]))
    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(etapa_id)
    executar(f"UPDATE etapas SET {', '.join(campos)} WHERE id = ?", valores)
    return jsonify({"ok": True})


@bp.delete("/etapas/<int:etapa_id>")
@papel_obrigatorio("gestor")
def remover_etapa(etapa_id: int):
    obra_da_etapa(etapa_id)
    executar("DELETE FROM etapas WHERE id = ?", (etapa_id,))
    return jsonify({"ok": True})
