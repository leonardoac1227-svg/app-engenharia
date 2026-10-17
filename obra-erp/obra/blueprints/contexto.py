# -*- coding: utf-8 -*-
"""Contexto — tudo que o app precisa para abrir, numa requisição só.

Abrir o aplicativo custava sete idas ao servidor, quatro delas em fila: sessão,
lista de obras, funcionários, semanas e dias. Cada uma dessas idas é
uma viagem completa até o Render e de lá até o Supabase; no 4G do canteiro,
com 300 ms de ida e volta, são mais de dois segundos olhando para o esqueleto
da tela — sem que o servidor tenha trabalhado quase nada.

Aqui as mesmas informações vão juntas. O ganho não é de processamento: é de
latência, que é o que o usuário sente.

Os endpoints antigos continuam existindo. Eles são usados para recarregar uma
parte específica depois de um lançamento, e é bom que continuem pequenos.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..security import garantir_acesso_obra, login_obrigatorio, obras_permitidas, usuario_atual
from ..services.fechamento import garantir_dias, listar_dias
from ..utils import hoje_iso, somar_dias
from ..db import todos, um

bp = Blueprint("contexto", __name__, url_prefix="/api")


def _obras_visiveis(incluir_arquivadas: bool = False) -> list[dict]:
    permitidas = obras_permitidas()
    if permitidas is not None and not permitidas:
        return []

    condicoes, args = [], []
    if not incluir_arquivadas:
        condicoes.append("o.arquivada = 0")
    if permitidas is not None:
        condicoes.append(f"o.id IN ({','.join('?' * len(permitidas))})")
        args += permitidas

    sql = """SELECT o.id, o.nome, o.cliente, o.endereco, o.orcamento_total, o.data_inicio,
                    o.previsao_fim, o.status, o.sigla, o.arquivada,
                    (SELECT COUNT(*) FROM semanas s WHERE s.obra_id = o.id) AS total_semanas
               FROM obras o"""
    if condicoes:
        sql += " WHERE " + " AND ".join(condicoes)
    sql += " ORDER BY o.arquivada, o.nome"

    return [dict(linha) for linha in todos(sql, args)]


def _semanas_da_obra(obra_id: int) -> list[dict]:
    semanas = []
    for linha in todos(
        """SELECT id, numero, data_inicio, meta, meta_atingida, orcamento_previsto,
                  fechada, folha_paga
             FROM semanas WHERE obra_id = ? ORDER BY numero""", (obra_id,)):
        item = dict(linha)
        item["data_fim"] = somar_dias(linha["data_inicio"], 6) if linha["data_inicio"] else None
        item["atual"] = bool(
            linha["data_inicio"] and linha["data_inicio"] <= hoje_iso() <= item["data_fim"])
        semanas.append(item)
    return semanas


def _funcionarios_da_obra(obra_id: int, papel: str) -> list[dict]:
    funcionarios = []
    for linha in todos(
        """SELECT id, nome, funcao, documento, telefone, ativo, chave_pix
             FROM funcionarios WHERE obra_id = ? AND ativo = 1
             ORDER BY nome COLLATE NOCASE""", (obra_id,)):
        funcionarios.append(dict(linha))
    return funcionarios


def _contexto_da_obra(obra_id: int, papel: str, semana_id: int | None = None) -> dict:
    semanas = _semanas_da_obra(obra_id)

    alvo = None
    if semana_id:
        alvo = next((s for s in semanas if s["id"] == semana_id), None)
    if alvo is None:
        alvo = next((s for s in semanas if s["atual"]), None) or (semanas[-1] if semanas else None)

    dias = []
    if alvo:
        garantir_dias(alvo["id"])
        dias = listar_dias(alvo["id"])

    return {
        "obra": dict(um("SELECT * FROM obras WHERE id = ?", (obra_id,)) or {}),
        "funcionarios": _funcionarios_da_obra(obra_id, papel),
        "semanas": semanas,
        "semana_id": alvo["id"] if alvo else None,
        "dias": dias,
    }


@bp.get("/inicio")
@login_obrigatorio
def inicio():
    """Sessão, obras e o contexto da obra escolhida — tudo de uma vez.

    `?obra=<id>` indica a obra que o aparelho tinha aberto da última vez. Se
    ela não existir mais (foi arquivada, ou o acesso foi retirado), cai na
    primeira disponível em vez de devolver erro.
    """
    from .. import __version__
    from ..config import REEMBOLSO_PADRAO
    from ..security import token_csrf
    from .auth import _usuario_publico

    usuario = usuario_atual()
    incluir_arquivadas = request.args.get("arquivadas") == "1"
    obras = _obras_visiveis(incluir_arquivadas)

    preferida = request.args.get("obra", type=int)
    escolhida = next((o for o in obras if o["id"] == preferida), None) or (obras[0] if obras else None)

    resposta = {
        "autenticado": True,
        # Mesmo formato de /api/sessao: sem os campos pode_*, a tela escondia
        # o Caixa, o "Nova obra" e o botão de lançamento até do gestor.
        "usuario": _usuario_publico(usuario),
        "csrf": token_csrf(),
        "versao": __version__,
        "reembolso_padrao": REEMBOLSO_PADRAO,
        "obras": obras,
        "contexto": None,
    }
    if escolhida:
        resposta["contexto"] = _contexto_da_obra(escolhida["id"], usuario["papel"])
        resposta["contexto"]["obra_id"] = escolhida["id"]
    return jsonify(resposta)


@bp.get("/obras/<int:obra_id>/contexto")
@login_obrigatorio
def contexto_da_obra(obra_id: int):
    """Contexto de uma obra — usado ao trocar de obra no menu lateral."""
    garantir_acesso_obra(obra_id)
    semana_id = request.args.get("semana", type=int)
    dados = _contexto_da_obra(obra_id, usuario_atual()["papel"], semana_id)
    dados["obra_id"] = obra_id
    return jsonify(dados)
