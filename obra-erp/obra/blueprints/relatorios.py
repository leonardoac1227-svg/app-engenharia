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
    copia["terceirizados"] = []
    copia["terceirizados_resumo"] = {}
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


@bp.get("/semanas/<int:semana_id>/pagamentos.xlsx")
@login_obrigatorio
def exportar_planilha_pagamento(semana_id: int):
    """Relatório de Lançamento por Categoria, no modelo usado pelo escritório.

    Por padrão o arquivo sai com **uma aba por obra** que tenha uma semana no
    mesmo período — é assim que o relatório circula hoje (I19 e O13 no mesmo
    arquivo). Com `?obra=1`, sai só a obra da semana pedida.
    """
    from ..db import todos, um
    from ..security import obras_permitidas
    from ..services.planilha_pagamento import gerar_planilha, nome_arquivo as nome_planilha
    from ..utils import ApiErro

    if usuario_atual()["papel"] != "gestor":
        raise ApiErro("Somente o gestor exporta o financeiro.", 403)

    obra_id = obra_da_semana(semana_id)
    dados = dados_fechamento(semana_id)
    obra = dict(um("SELECT * FROM obras WHERE id = ?", (obra_id,)))

    abas = [(obra, dados)]

    if request.args.get("obra") != "1":
        permitidas = obras_permitidas()
        irmas = todos(
            """SELECT s.id AS semana_id, o.id AS obra_id
                 FROM semanas s JOIN obras o ON o.id = s.obra_id
                WHERE s.data_inicio = ? AND s.obra_id != ? AND o.arquivada = 0
                ORDER BY o.nome""",
            (dados["data_inicio"], obra_id))

        for irma in irmas:
            if permitidas is not None and irma["obra_id"] not in permitidas:
                continue
            abas.append((
                dict(um("SELECT * FROM obras WHERE id = ?", (irma["obra_id"],))),
                dados_fechamento(irma["semana_id"]),
            ))

    conteudo = gerar_planilha(abas)
    registrar_auditoria(usuario_atual(), "exportou_planilha_pagamento", "semana", semana_id,
                        f"{len(abas)} obra(s)")

    return Response(
        conteudo,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome_planilha(obra, dados)}"'},
    )


@bp.get("/semanas/<int:semana_id>/arquivos")
@login_obrigatorio
def listar_arquivos_semana(semana_id: int):
    """Índice de tudo que foi anexado na semana: notas fiscais e fotos."""
    from ..services.arquivos_semana import listar_arquivos

    obra_da_semana(semana_id)
    indice = listar_arquivos(semana_id)

    if usuario_atual()["papel"] == "mestre":
        # O mestre vê as fotos do canteiro, não as notas fiscais nem valores.
        indice["arquivos"] = [a for a in indice["arquivos"] if a["tipo"] == "foto"]
        indice["notas"] = 0
        indice["valor_com_nota"] = 0
        indice["total"] = len(indice["arquivos"])

    return jsonify(indice)


@bp.get("/semanas/<int:semana_id>/arquivos.zip")
@login_obrigatorio
def baixar_arquivos_semana(semana_id: int):
    """Todos os anexos da semana num pacote, com nomes legíveis."""
    from ..services.arquivos_semana import gerar_zip
    from ..utils import ApiErro, bytes_anexo, slug

    if usuario_atual()["papel"] != "gestor":
        raise ApiErro("Somente o gestor baixa o pacote de anexos.", 403)

    obra_da_semana(semana_id)
    dados = dados_fechamento(semana_id)
    conteudo, resumo = gerar_zip(semana_id, dados, bytes_anexo)

    if not resumo["total"]:
        raise ApiErro("Esta semana ainda não tem nenhum arquivo anexado.", 404)

    registrar_auditoria(usuario_atual(), "baixou_arquivos", "semana", semana_id,
                        f"{resumo['incluidos']} arquivo(s)")

    nome = (f"ARQUIVOS_{slug(dados['obra_nome']).upper()}"
            f"_SEMANA_{dados['semana_numero']:02d}.zip")
    return Response(conteudo, mimetype="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})
