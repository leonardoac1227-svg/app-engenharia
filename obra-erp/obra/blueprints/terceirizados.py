# -*- coding: utf-8 -*-
"""Rotas de terceirizados — contratos globais e medição semanal.

Tudo aqui é financeiro (valor de contrato, saldo devedor, o que entra no caixa
da semana), então o módulo inteiro exige o papel de gestor, pela mesma regra
que já protege a aba Caixa.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import registrar_auditoria, um
from ..security import (exigir_financeiro, garantir_acesso_obra, login_obrigatorio,
                        obra_da_semana, usuario_atual)
from ..services.terceirizados import (atualizar_contrato, criar_contrato, listar_contratos,
                                      medicoes_do_contrato, painel_obra, registrar_medicao,
                                      remover_contrato, remover_medicao)
from ..utils import ApiErro

bp = Blueprint("terceirizados", __name__, url_prefix="/api")


def _obra_do_contrato(contrato_id: int) -> int:
    linha = um("SELECT obra_id FROM contratos WHERE id = ?", (contrato_id,))
    if not linha:
        raise ApiErro("Contrato não encontrado.", 404)
    return garantir_acesso_obra(linha["obra_id"])


def _obra_da_medicao(medicao_id: int) -> int:
    linha = um(
        """SELECT c.obra_id AS obra_id FROM contrato_medicoes m
             JOIN contratos c ON c.id = m.contrato_id
            WHERE m.id = ?""", (medicao_id,))
    if not linha:
        raise ApiErro("Medição não encontrada.", 404)
    return garantir_acesso_obra(linha["obra_id"])


# ---------------------------------------------------------------- contratos

@bp.get("/obras/<int:obra_id>/terceirizados")
@login_obrigatorio
def painel(obra_id: int):
    exigir_financeiro()
    garantir_acesso_obra(obra_id)
    semana_id = request.args.get("semana_id", type=int)
    if semana_id:
        obra_da_semana(semana_id)
    return jsonify(painel_obra(obra_id, semana_id))


@bp.post("/obras/<int:obra_id>/contratos")
@login_obrigatorio
def criar(obra_id: int):
    exigir_financeiro()
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}
    contrato_id = criar_contrato(obra_id, dados)
    registrar_auditoria(usuario_atual(), "criou_contrato", "contrato", contrato_id,
                        f"{dados.get('empresa', '')} · {dados.get('servico', '')}")
    return jsonify({"id": contrato_id}), 201


@bp.put("/contratos/<int:contrato_id>")
@login_obrigatorio
def atualizar(contrato_id: int):
    exigir_financeiro()
    _obra_do_contrato(contrato_id)
    atualizar_contrato(contrato_id, request.get_json(silent=True) or {})
    registrar_auditoria(usuario_atual(), "editou_contrato", "contrato", contrato_id, "")
    return jsonify({"ok": True})


@bp.delete("/contratos/<int:contrato_id>")
@login_obrigatorio
def remover(contrato_id: int):
    exigir_financeiro()
    _obra_do_contrato(contrato_id)
    remover_contrato(contrato_id)
    registrar_auditoria(usuario_atual(), "removeu_contrato", "contrato", contrato_id, "")
    return jsonify({"ok": True})


@bp.get("/contratos/<int:contrato_id>")
@login_obrigatorio
def detalhe(contrato_id: int):
    exigir_financeiro()
    obra_id = _obra_do_contrato(contrato_id)
    contrato = next((c for c in listar_contratos(obra_id) if c["id"] == contrato_id), None)
    if not contrato:
        raise ApiErro("Contrato não encontrado.", 404)
    contrato["medicoes"] = medicoes_do_contrato(contrato_id)
    return jsonify(contrato)


# ---------------------------------------------------------------- medições

@bp.post("/contratos/<int:contrato_id>/semanas/<int:semana_id>/medicao")
@login_obrigatorio
def medir(contrato_id: int, semana_id: int):
    exigir_financeiro()
    obra_contrato = _obra_do_contrato(contrato_id)
    obra_semana = obra_da_semana(semana_id)
    if obra_contrato != obra_semana:
        raise ApiErro("Esta semana pertence a outra obra.", 400)

    usuario = usuario_atual() or {}
    resultado = registrar_medicao(contrato_id, semana_id,
                                  request.get_json(silent=True) or {}, usuario.get("nome", ""))
    registrar_auditoria(usuario, "mediu_contrato", "contrato", contrato_id,
                        f"{resultado['percentual']:g}% · R$ {resultado['valor']:.2f}")
    return jsonify(resultado), 201


@bp.delete("/medicoes-contrato/<int:medicao_id>")
@login_obrigatorio
def apagar_medicao(medicao_id: int):
    exigir_financeiro()
    _obra_da_medicao(medicao_id)
    remover_medicao(medicao_id)
    registrar_auditoria(usuario_atual(), "removeu_medicao_contrato", "medicao", medicao_id, "")
    return jsonify({"ok": True})
