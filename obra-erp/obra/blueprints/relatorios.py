# -*- coding: utf-8 -*-
"""Fechamento da semana e exportações (JSON, TXT, PDF, CSV)."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from ..db import registrar_auditoria
from ..security import login_obrigatorio, obra_da_semana, usuario_atual
from ..services import ia as servico_ia
from ..services.fechamento import dados_fechamento
from ..services.relatorio import despesas_csv, nome_arquivo, relatorio_texto

bp = Blueprint("relatorios", __name__, url_prefix="/api")


def _dados(semana_id: int) -> dict:
    obra_da_semana(semana_id)
    return dados_fechamento(semana_id)


def _sem_financeiro(dados: dict) -> dict:
    """Versão do fechamento para o mestre de obra (sem qualquer valor)."""
    copia = dict(dados)
    copia["financeiro"] = {}
    copia["despesas"] = []
    copia["despesas_por_categoria"] = {}
    copia["folha_detalhada"] = []
    indicadores = dict(dados["indicadores"])
    indicadores.pop("custo_medio_dia", None)
    copia["indicadores"] = indicadores
    return copia


@bp.get("/semanas/<int:semana_id>/fechamento")
@login_obrigatorio
def fechamento(semana_id: int):
    dados = _dados(semana_id)
    if usuario_atual()["papel"] == "mestre":
        dados = _sem_financeiro(dados)
    return jsonify(dados)


@bp.get("/semanas/<int:semana_id>/resumo")
@login_obrigatorio
def resumo(semana_id: int):
    """Resumo executivo da semana (automático, ou redigido por IA se houver chave)."""
    dados = _dados(semana_id)
    usar_ia = request.args.get("ia", "1") != "0" and usuario_atual()["papel"] != "mestre"
    return jsonify(servico_ia.resumo_semanal(dados, usar_ia=usar_ia))


@bp.get("/semanas/<int:semana_id>/relatorio.txt")
@login_obrigatorio
def relatorio_txt(semana_id: int):
    dados = _dados(semana_id)
    if usuario_atual()["papel"] == "mestre":
        dados = _sem_financeiro(dados)
    conteudo = relatorio_texto(dados, servico_ia.resumo_automatico(dados))
    return Response(
        conteudo.encode("utf-8"),
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo(dados, "txt")}"'},
    )


@bp.get("/semanas/<int:semana_id>/relatorio.pdf")
@login_obrigatorio
def relatorio_pdf(semana_id: int):
    """PDF com identidade visual — é este arquivo que vai para o cliente."""
    dados = _dados(semana_id)
    if usuario_atual()["papel"] == "mestre":
        dados = _sem_financeiro(dados)

    usar_ia = request.args.get("ia", "1") != "0"
    resumo_texto = servico_ia.resumo_semanal(dados, usar_ia=usar_ia)["texto"]

    from ..services.pdf import gerar_pdf  # import tardio: reportlab só carrega quando usado
    conteudo = gerar_pdf(dados, resumo_texto)

    registrar_auditoria(usuario_atual(), "exportou_pdf", "semana", semana_id)
    return Response(
        conteudo,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo(dados, "pdf")}"'},
    )


@bp.get("/semanas/<int:semana_id>/despesas.csv")
@login_obrigatorio
def exportar_csv(semana_id: int):
    dados = _dados(semana_id)
    if usuario_atual()["papel"] != "gestor":
        from ..utils import ApiErro
        raise ApiErro("Somente o gestor exporta o financeiro.", 403)
    return Response(
        despesas_csv(dados).encode("utf-8"),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome_arquivo(dados, "csv")}"'},
    )
