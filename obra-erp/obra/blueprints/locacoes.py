# -*- coding: utf-8 -*-
"""Rotas de locação de equipamentos — financeiro, portanto restrito ao gestor."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import registrar_auditoria, um
from ..security import exigir_financeiro, garantir_acesso_obra, login_obrigatorio, usuario_atual
from ..services.locacoes import (alertas, criar_locacao, encerrar_locacao, lancar_parcela,
                                 listar_locacoes, painel_obra, prorrogar_locacao, remover_locacao)
from ..utils import ApiErro, numero

bp = Blueprint("locacoes", __name__, url_prefix="/api")


def _obra_da_locacao(locacao_id: int) -> int:
    linha = um("SELECT obra_id FROM locacoes WHERE id = ?", (locacao_id,))
    if not linha:
        raise ApiErro("Locação não encontrada.", 404)
    return garantir_acesso_obra(linha["obra_id"])


def _obra_da_parcela(parcela_id: int) -> int:
    linha = um(
        """SELECT l.obra_id AS obra_id FROM locacao_parcelas p
             JOIN locacoes l ON l.id = p.locacao_id
            WHERE p.id = ?""", (parcela_id,))
    if not linha:
        raise ApiErro("Renovação não encontrada.", 404)
    return garantir_acesso_obra(linha["obra_id"])


@bp.get("/obras/<int:obra_id>/locacoes")
@login_obrigatorio
def painel(obra_id: int):
    exigir_financeiro()
    garantir_acesso_obra(obra_id)
    return jsonify(painel_obra(obra_id))


@bp.get("/obras/<int:obra_id>/locacoes/alertas")
@login_obrigatorio
def listar_alertas(obra_id: int):
    exigir_financeiro()
    garantir_acesso_obra(obra_id)
    return jsonify(alertas(obra_id))


@bp.post("/obras/<int:obra_id>/locacoes")
@login_obrigatorio
def criar(obra_id: int):
    exigir_financeiro()
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}
    locacao_id = criar_locacao(obra_id, dados)
    registrar_auditoria(usuario_atual(), "criou_locacao", "locacao", locacao_id,
                        f"{dados.get('equipamento', '')} · {dados.get('frequencia', '')}")
    return jsonify({"id": locacao_id}), 201


@bp.get("/locacoes/<int:locacao_id>")
@login_obrigatorio
def detalhe(locacao_id: int):
    exigir_financeiro()
    obra_id = _obra_da_locacao(locacao_id)
    locacao = next((l for l in listar_locacoes(obra_id) if l["id"] == locacao_id), None)
    if not locacao:
        raise ApiErro("Locação não encontrada.", 404)
    return jsonify(locacao)


@bp.post("/locacoes/parcelas/<int:parcela_id>/lancar")
@login_obrigatorio
def lancar(parcela_id: int):
    usuario = exigir_financeiro()
    _obra_da_parcela(parcela_id)
    resultado = lancar_parcela(parcela_id, request.get_json(silent=True) or {}, usuario.get("id"))
    registrar_auditoria(usuario, "lancou_renovacao", "locacao", parcela_id,
                        f"R$ {resultado['valor']:.2f}")
    return jsonify(resultado), 201


@bp.post("/locacoes/<int:locacao_id>/encerrar")
@login_obrigatorio
def encerrar(locacao_id: int):
    exigir_financeiro()
    _obra_da_locacao(locacao_id)
    canceladas = encerrar_locacao(locacao_id)
    registrar_auditoria(usuario_atual(), "encerrou_locacao", "locacao", locacao_id,
                        f"{canceladas} renovação(ões) cancelada(s)")
    return jsonify({"ok": True, "canceladas": canceladas})


@bp.post("/locacoes/<int:locacao_id>/prorrogar")
@login_obrigatorio
def prorrogar(locacao_id: int):
    exigir_financeiro()
    _obra_da_locacao(locacao_id)
    dados = request.get_json(silent=True) or {}
    extras = prorrogar_locacao(locacao_id, numero(dados.get("ciclos"), 1, 1, campo="a prorrogação"))
    registrar_auditoria(usuario_atual(), "prorrogou_locacao", "locacao", locacao_id,
                        f"+{extras} período(s)")
    return jsonify({"ok": True, "adicionadas": extras})


@bp.delete("/locacoes/<int:locacao_id>")
@login_obrigatorio
def remover(locacao_id: int):
    exigir_financeiro()
    _obra_da_locacao(locacao_id)
    remover_locacao(locacao_id)
    registrar_auditoria(usuario_atual(), "removeu_locacao", "locacao", locacao_id, "")
    return jsonify({"ok": True})
