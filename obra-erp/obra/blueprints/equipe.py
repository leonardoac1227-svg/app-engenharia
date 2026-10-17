# -*- coding: utf-8 -*-
"""Equipe: cadastro de funcionários e lançamento de ponto.

O ponto serve só para controle de presença: não há mais valor/hora nem folha
calculada pelo app — pagamentos da equipe são lançados como despesa no Caixa.
Pontos antigos guardam o valor/hora da época (`horas.valor_hora`) e esse valor
é preservado ao reeditar o dia, para não mudar fechamentos já entregues.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import executar, get_db, registrar_auditoria, todos, um
from ..security import (exigir_lancamento, garantir_acesso_obra, login_obrigatorio,
                        obra_do_funcionario, obra_do_dia, papel_obrigatorio, usuario_atual)
from ..utils import ApiErro, agora_iso, numero, texto

bp = Blueprint("equipe", __name__, url_prefix="/api")



@bp.get("/obras/<int:obra_id>/funcionarios")
@login_obrigatorio
def listar(obra_id: int):
    garantir_acesso_obra(obra_id)
    incluir_inativos = request.args.get("inativos") == "1"
    sql = """SELECT f.id, f.nome, f.funcao, f.documento, f.telefone, f.ativo, f.chave_pix
               FROM funcionarios f WHERE f.obra_id = ?"""
    if not incluir_inativos:
        sql += " AND f.ativo = 1"
    sql += " ORDER BY f.ativo DESC, f.nome COLLATE NOCASE"
    return jsonify([dict(linha) for linha in todos(sql, (obra_id,))])


@bp.post("/obras/<int:obra_id>/funcionarios")
@papel_obrigatorio("gestor")
def criar(obra_id: int):
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}
    nome = texto(dados.get("nome"), 120, True, "o nome do funcionário")

    cur = executar(
        """INSERT INTO funcionarios (obra_id, nome, funcao, tipo_contrato, chave_pix, valor_hora,
                                     documento, telefone, ativo, criado_em)
           VALUES (?, ?, ?, 'fixo', ?, 0, ?, ?, 1, ?)""",
        (obra_id, nome, texto(dados.get("funcao"), 80),
         texto(dados.get("chave_pix"), 120),
         texto(dados.get("documento"), 40), texto(dados.get("telefone"), 40), agora_iso()),
    )
    registrar_auditoria(usuario_atual(), "criou_funcionario", "funcionario", cur.lastrowid, nome)
    return jsonify({"id": cur.lastrowid}), 201


@bp.put("/funcionarios/<int:funcionario_id>")
@papel_obrigatorio("gestor")
def atualizar(funcionario_id: int):
    obra_do_funcionario(funcionario_id)
    dados = request.get_json(silent=True) or {}
    mapa = {
        "nome": lambda v: texto(v, 120, True, "o nome do funcionário"),
        "funcao": lambda v: texto(v, 80),
        "chave_pix": lambda v: texto(v, 120),
        "documento": lambda v: texto(v, 40),
        "telefone": lambda v: texto(v, 40),
        "ativo": lambda v: 1 if v else 0,
    }
    campos, valores = [], []
    for chave, conversor in mapa.items():
        if chave in dados:
            campos.append(f"{chave} = ?"); valores.append(conversor(dados[chave]))
    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(funcionario_id)
    executar(f"UPDATE funcionarios SET {', '.join(campos)} WHERE id = ?", valores)
    registrar_auditoria(usuario_atual(), "editou_funcionario", "funcionario", funcionario_id)
    return jsonify({"ok": True})


@bp.delete("/funcionarios/<int:funcionario_id>")
@papel_obrigatorio("gestor")
def desativar(funcionario_id: int):
    """Soft-delete: preserva o histórico de horas das semanas já lançadas."""
    obra_do_funcionario(funcionario_id)
    executar("UPDATE funcionarios SET ativo = 0 WHERE id = ?", (funcionario_id,))
    registrar_auditoria(usuario_atual(), "desativou_funcionario", "funcionario", funcionario_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- ponto

@bp.get("/dias/<int:dia_id>/horas")
@login_obrigatorio
def obter_horas(dia_id: int):
    obra_do_dia(dia_id)
    rows = todos("SELECT funcionario_id, horas_trabalhadas FROM horas WHERE dia_id = ?", (dia_id,))
    return jsonify({str(r["funcionario_id"]): {"horas": r["horas_trabalhadas"]} for r in rows})


@bp.put("/dias/<int:dia_id>/horas")
@login_obrigatorio
def salvar_horas(dia_id: int):
    obra_id = obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    registros = dados.get("registros", [])
    if not isinstance(registros, list):
        raise ApiErro("Formato de registros inválido.")

    validos = {r["id"] for r in todos("SELECT id FROM funcionarios WHERE obra_id = ?", (obra_id,))}
    db = get_db()
    total = 0.0
    for reg in registros:
        funcionario_id = int(reg.get("funcionario_id") or 0)
        if funcionario_id not in validos:
            continue
        horas = numero(reg.get("horas"), 0, 0, 24, "as horas")
        if horas <= 0:
            db.execute("DELETE FROM horas WHERE dia_id = ? AND funcionario_id = ?", (dia_id, funcionario_id))
            continue
        # Ponto novo entra com valor/hora zero (só presença); o de um dia
        # antigo mantém o valor gravado na época.
        db.execute(
            """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas, valor_hora)
               VALUES (?, ?, ?, 0)
               ON CONFLICT(dia_id, funcionario_id)
               DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas""",
            (dia_id, funcionario_id, horas),
        )
        total += horas
    db.commit()
    registrar_auditoria(usuario, "salvou_ponto", "dia", dia_id, f"{total:g} h")
    return jsonify({"ok": True, "total_horas": round(total, 2)})


@bp.post("/dias/<int:dia_id>/horas/repetir")
@login_obrigatorio
def repetir_ponto(dia_id: int):
    """Copia o ponto do último dia lançado — economiza minutos todo dia.

    Na prática, a equipe do canteiro é quase sempre a mesma; digitar as mesmas
    8 horas para 12 pessoas de novo é o tipo de atrito que faz o gestor
    abandonar o app.
    """
    obra_id = obra_do_dia(dia_id)
    usuario = exigir_lancamento()

    referencia = um(
        """SELECT h.dia_id FROM horas h
             JOIN dias d ON d.id = h.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.id <> ?
              AND d.data < (SELECT data FROM dias WHERE id = ?)
            ORDER BY d.data DESC LIMIT 1""",
        (obra_id, dia_id, dia_id),
    )
    if not referencia:
        raise ApiErro("Não há nenhum ponto anterior para copiar.", 404)

    origem = todos(
        """SELECT h.funcionario_id, h.horas_trabalhadas
             FROM horas h JOIN funcionarios f ON f.id = h.funcionario_id
            WHERE h.dia_id = ? AND f.ativo = 1""", (referencia["dia_id"],))
    if not origem:
        raise ApiErro("O último ponto lançado não tem funcionários ativos.", 404)

    db = get_db()
    for r in origem:
        db.execute(
            """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas, valor_hora)
               VALUES (?, ?, ?, 0)
               ON CONFLICT(dia_id, funcionario_id)
               DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas""",
            (dia_id, r["funcionario_id"], r["horas_trabalhadas"]),
        )
    db.commit()
    registrar_auditoria(usuario, "repetiu_ponto", "dia", dia_id, f"{len(origem)} registro(s)")
    return jsonify({"ok": True, "copiados": len(origem)})
