# -*- coding: utf-8 -*-
"""Relatório de Lançamento por Categoria — planilha no modelo do escritório.

Este módulo reproduz, célula a célula, o arquivo que já circula entre a obra e
o financeiro: mesmo cabeçalho, mesmas colunas, mesmas cores, mesmas fórmulas de
totalização. A ideia é que a planilha gerada aqui possa substituir a digitada à
mão sem que ninguém precise reaprender a ler.

Estrutura de cada aba (uma por obra):

    A1  RELATORIO DE LANÇAMENTO POR CATEGORIA
    A2  <NOME DA OBRA> - <CLIENTE>
    A3  PAG  - DD/MM/AAAA A DD/MM/AAAA
    A5  Fornecedor | Descrição | Data | DOC | Valor (R$)
        · NOTAS PARA REEMBOLSO SEMANA NN      (despesas marcadas Reembolso)
        · NOTAS ABERTAS A PAGAR <SIGLA>       (despesas Em Aberto)
        · MEDIÇÃO <SERVIÇO>                   (uma seção por terceirizado medido)
        · FOLHA DE MÃO DE OBRA                (ponto da semana, por profissional)
        · NOTAS JÁ PAGAS                      (fora do total, só conferência)
        TOTAL A PAGAR - SEMANA NN

A coluna F repete o destino de cada lançamento (A PAGAR / REEMBOLSO / PAGO),
como no arquivo original.

Os totais são **fórmulas**, não números calculados no Python: quem receber a
planilha pode corrigir um valor e ver o total acertar sozinho — que é como o
financeiro trabalha de fato.
"""

from __future__ import annotations

import io
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

from ..utils import data_br

# ---------------------------------------------------------------- estilo

FONTE = "Calibri"
MOEDA = '"R$"\\ #,##0.00'
DATA_BR = "DD/MM/YYYY"

CINZA_CABECALHO = "FFBFBFBF"   # tema branco escurecido 25%, como no original
ROSA_MEDICAO = "FFFEA6A6"
CINZA_STATUS = "FFD8D8D8"

LARGURAS = {"A": 33.0, "B": 48.7, "C": 26.5, "D": 11.7, "E": 16.3, "F": 13.2}

_borda_fina = Side(style="thin")
BORDA = Border(left=_borda_fina, right=_borda_fina, top=_borda_fina, bottom=_borda_fina)

CENTRO = Alignment(horizontal="center", vertical="center", wrap_text=True)
ESQUERDA = Alignment(horizontal="left", vertical="center", wrap_text=True)


def _preencher(cor: str) -> PatternFill:
    return PatternFill(start_color=cor, end_color=cor, fill_type="solid")


def _sigla_da_obra(obra: dict) -> str:
    """Código curto da obra para o título da seção e o nome da aba.

    Se o gestor cadastrou uma sigla, ela manda. Senão, tentamos o padrão dos
    canteiros — o identificador do lote costuma ser a última palavra com
    número no nome ("... LOTE I19" → "I19"). Em último caso, as iniciais.
    """
    sigla = (obra.get("sigla") or "").strip()
    if sigla:
        return sigla.upper()[:12]

    nome = (obra.get("nome") or "OBRA").upper()
    candidatos = [p for p in re.split(r"[\s\-/]+", nome) if any(c.isdigit() for c in p)]
    if candidatos:
        return candidatos[-1][:12]

    iniciais = "".join(p[0] for p in nome.split() if p)[:4]
    return iniciais or "OBRA"


def _altura_da_linha(*textos: str) -> float:
    """Estima a altura para o texto não ficar cortado.

    O openpyxl não faz auto-ajuste, e descrição de nota fiscal costuma ter duas
    ou três linhas. A conta é grosseira de propósito: melhor sobrar um pouco de
    espaço do que cortar a descrição do material.
    """
    linhas = 1
    for texto, largura in zip(textos, (33, 48, 26, 11)):
        if not texto:
            continue
        conteudo = str(texto)
        quebras = conteudo.count("\n") + 1
        estimadas = max(quebras, -(-len(conteudo) // largura))
        linhas = max(linhas, estimadas)
    return min(18 + linhas * 16, 150)


# ---------------------------------------------------------------- montagem

class _Aba:
    """Escreve uma aba do relatório, controlando a numeração das linhas."""

    def __init__(self, planilha, obra: dict, dados: dict):
        self.ws = planilha
        self.obra = obra
        self.dados = dados
        self.sigla = _sigla_da_obra(obra)
        self.semana = dados.get("semana_numero", 0)
        self.linha = 1
        self.celulas_de_secao: list[str] = []   # alimentam a fórmula do total

    # -------------------------------------------------- blocos

    def cabecalho(self) -> None:
        ws = self.ws
        for letra, largura in LARGURAS.items():
            ws.column_dimensions[letra].width = largura

        # A planilha é impressa e assinada no escritório: paisagem, ajustada à
        # largura da página, com o cabeçalho repetindo em toda folha.
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.print_area = f"A1:F{1}"           # ajustado ao final da montagem
        ws.print_title_rows = "1:5"
        ws.page_margins = PageMargins(left=0.3, right=0.3, top=0.4, bottom=0.4)
        ws.freeze_panes = "A6"

        titulos = [
            "RELATORIO DE LANÇAMENTO POR CATEGORIA",
            f"{(self.obra.get('nome') or '').upper()}"
            + (f" - {self.obra['cliente'].upper()}" if self.obra.get("cliente") else ""),
            f"PAG  - {data_br(self.dados['data_inicio'])} A {data_br(self.dados['data_fim'])}",
        ]
        for texto in titulos:
            ws.merge_cells(start_row=self.linha, start_column=1, end_row=self.linha, end_column=5)
            celula = ws.cell(self.linha, 1, texto)
            celula.font = Font(name=FONTE, size=15, bold=True)
            celula.fill = _preencher(CINZA_CABECALHO)
            celula.alignment = Alignment(horizontal="center", vertical="center")
            for coluna in range(1, 6):
                ws.cell(self.linha, coluna).border = BORDA
            ws.row_dimensions[self.linha].height = 21
            self.linha += 1

        ws.row_dimensions[self.linha].height = 15.75
        self.linha += 1

        for coluna, titulo in enumerate(["Fornecedor", "Descrição", "Data", "DOC", "Valor (R$)"], 1):
            celula = ws.cell(self.linha, coluna, titulo)
            celula.font = Font(name=FONTE, size=16, bold=True)
            celula.alignment = CENTRO
            celula.border = BORDA
        ws.row_dimensions[self.linha].height = 24.75
        self.linha += 1

    def secao(self, titulo: str, itens: list[dict], cor: str = CINZA_CABECALHO,
              entra_no_total: bool = True) -> None:
        """Cabeçalho da seção + suas linhas + o total da seção (fórmula)."""
        ws = self.ws
        linha_titulo = self.linha

        ws.merge_cells(start_row=linha_titulo, start_column=1, end_row=linha_titulo, end_column=4)
        celula = ws.cell(linha_titulo, 1, titulo)
        celula.font = Font(name=FONTE, size=15, bold=True)
        celula.fill = _preencher(cor)
        celula.alignment = Alignment(horizontal="center", vertical="center")
        for coluna in range(1, 6):
            ws.cell(linha_titulo, coluna).border = BORDA
        ws.row_dimensions[linha_titulo].height = 34.95

        total = ws.cell(linha_titulo, 5)
        total.font = Font(name=FONTE, size=16, bold=True)
        total.fill = _preencher(cor)
        total.alignment = CENTRO
        total.number_format = MOEDA
        self.linha += 1

        primeira = self.linha
        for item in itens:
            self._linha_de_item(item)

        ultima = self.linha - 1
        if ultima >= primeira:
            total.value = f"=SUM(E{primeira}:E{ultima})"
        else:
            # Seção sem lançamento continua no relatório, zerada: a ausência é
            # informação (ninguém pediu reembolso nesta semana).
            total.value = 0

        if entra_no_total:
            self.celulas_de_secao.append(f"E{linha_titulo}")

        ws.row_dimensions[self.linha].height = 10.05
        self.linha += 1

    def _linha_de_item(self, item: dict) -> None:
        ws = self.ws
        linha = self.linha

        fornecedor = item.get("fornecedor") or "-"
        descricao = item.get("descricao") or ""
        documento = item.get("documento") or "-"

        celula = ws.cell(linha, 1, fornecedor)
        celula.font = Font(name=FONTE, size=12, bold=True)
        celula.alignment = ESQUERDA

        celula = ws.cell(linha, 2, descricao)
        celula.font = Font(name=FONTE, size=12, bold=True)
        celula.alignment = ESQUERDA

        celula = ws.cell(linha, 3)
        data = item.get("data")
        if data:
            try:
                celula.value = datetime.strptime(str(data)[:10], "%Y-%m-%d")
                celula.number_format = DATA_BR
            except ValueError:
                celula.value = str(data)
        else:
            celula.value = "-"
        celula.font = Font(name=FONTE, size=12, bold=True)
        celula.alignment = CENTRO

        celula = ws.cell(linha, 4, documento)
        celula.font = Font(name=FONTE, size=12, bold=True)
        celula.alignment = CENTRO

        celula = ws.cell(linha, 5, round(float(item.get("valor") or 0), 2))
        celula.font = Font(name=FONTE, size=14, bold=True)
        celula.alignment = CENTRO
        celula.number_format = MOEDA

        for coluna in range(1, 6):
            ws.cell(linha, coluna).border = BORDA

        if item.get("status"):
            marca = ws.cell(linha, 6, item["status"])
            marca.font = Font(name=FONTE, size=14)
            marca.fill = _preencher(CINZA_STATUS)
            marca.alignment = Alignment(vertical="center")

        ws.row_dimensions[linha].height = _altura_da_linha(fornecedor, descricao, str(documento))
        self.linha += 1

    def total_geral(self) -> None:
        ws = self.ws
        self.linha += 1
        linha = self.linha

        ws.merge_cells(start_row=linha, start_column=1, end_row=linha, end_column=3)
        celula = ws.cell(linha, 1, f"TOTAL A PAGAR - SEMANA {self.semana:02d}")
        celula.font = Font(name=FONTE, size=20, bold=True, color="FFFFFFFF")
        celula.fill = _preencher(CINZA_CABECALHO)
        celula.alignment = CENTRO

        ws.merge_cells(start_row=linha, start_column=4, end_row=linha, end_column=5)
        total = ws.cell(linha, 4)
        total.value = ("=" + "+".join(self.celulas_de_secao)) if self.celulas_de_secao else 0
        total.font = Font(name=FONTE, size=20, bold=True, color="FFFFFFFF")
        total.fill = _preencher(CINZA_CABECALHO)
        total.alignment = CENTRO
        total.number_format = MOEDA

        for coluna in range(1, 6):
            ws.cell(linha, coluna).border = BORDA
        ws.row_dimensions[linha].height = 40

        ws.print_area = f"A1:F{linha}"


# ---------------------------------------------------------------- dados

ROTULO_STATUS = {"Em Aberto": "A PAGAR", "Reembolso": "REEMBOLSO", "Pago": "PAGO"}


def _itens_de_despesas(despesas: list[dict], status_alvo: str,
                       ignorar: set[int] | None = None) -> list[dict]:
    """Despesas de um status, fora as que são espelho de medição.

    Medição de terceirizado gera uma despesa de verdade no caixa (é o que faz
    ela entrar em contas a pagar). Se ela aparecesse aqui **e** na sua própria
    seção de medição, o total sairia com o valor contado duas vezes — erro que
    o financeiro só descobriria ao conferir nota por nota.
    """
    ignorar = ignorar or set()
    return [{
        "fornecedor": d.get("fornecedor") or d.get("categoria") or "-",
        "descricao": d.get("descricao") or "",
        "data": d.get("data"),
        "documento": d.get("numero_nota") or "-",
        "valor": d.get("valor") or 0,
        "status": ROTULO_STATUS.get(d.get("status_pagamento"), ""),
    } for d in despesas
      if d.get("status_pagamento") == status_alvo and d.get("id") not in ignorar]


def _despesas_espelho(dados: dict) -> set[int]:
    """IDs das despesas criadas automaticamente por medição de contrato."""
    return {m["despesa_id"] for m in (dados.get("terceirizados") or []) if m.get("despesa_id")}


def _secoes_de_medicao(dados: dict) -> list[tuple[str, list[dict]]]:
    """Uma seção por terceirizado medido na semana, como no modelo."""
    secoes = []
    medicoes = sorted(dados.get("terceirizados") or [],
                      key=lambda m: ((m.get("servico") or "").upper(), (m.get("empresa") or "").upper()))
    for medicao in medicoes:
        titulo = f"MEDIÇÃO {(medicao.get('servico') or 'SERVIÇO').upper()}"
        descricao = (f"{medicao['percentual']:g}% medidos nesta semana · "
                     f"acumulado {medicao['acumulado']:g}% de 100%")
        if medicao.get("observacao"):
            descricao += f"\n{medicao['observacao']}"
        if not medicao.get("entra_no_caixa"):
            descricao += "\n(não entra no pagamento desta semana)"

        secoes.append((titulo, [{
            "fornecedor": medicao.get("empresa") or "-",
            "descricao": descricao,
            "data": dados.get("data_fim"),
            "documento": f"MED{medicao.get('numero_medicao', '')}" if medicao.get("numero_medicao") else "MED",
            "valor": medicao.get("valor") or 0,
            "status": "A PAGAR" if medicao.get("entra_no_caixa") else "NÃO ENTRA",
        }]))
    return secoes


def _itens_de_folha(dados: dict) -> list[dict]:
    return [{
        "fornecedor": p.get("nome") or "-",
        "descricao": (f"{p.get('funcao') or 'mão de obra'} · "
                      f"{p.get('horas', 0):g} h trabalhadas na semana"),
        "data": dados.get("data_fim"),
        "documento": "FOLHA",
        "valor": p.get("total") or 0,
        "status": "A PAGAR",
    } for p in (dados.get("folha_detalhada") or [])]


def montar_aba(planilha, obra: dict, dados: dict) -> None:
    aba = _Aba(planilha, obra, dados)
    aba.cabecalho()

    despesas = dados.get("despesas") or []
    semana = dados.get("semana_numero", 0)

    espelhos = _despesas_espelho(dados)

    aba.secao(f" NOTAS PARA REEMBOLSO SEMANA {semana:02d}",
              _itens_de_despesas(despesas, "Reembolso", espelhos))
    aba.secao(f"NOTAS ABERTAS A PAGAR {aba.sigla}",
              _itens_de_despesas(despesas, "Em Aberto", espelhos))

    for titulo, itens in _secoes_de_medicao(dados):
        aba.secao(titulo, itens, cor=ROSA_MEDICAO,
                  entra_no_total=any(i["status"] == "A PAGAR" for i in itens))

    folha = _itens_de_folha(dados)
    if folha:
        aba.secao(f"FOLHA DE MÃO DE OBRA - SEMANA {semana:02d}", folha, cor=ROSA_MEDICAO)

    pagas = _itens_de_despesas(despesas, "Pago", espelhos)
    if pagas:
        # Fora do total de propósito: o número que o financeiro precisa é o
        # que ainda falta pagar. O que já saiu fica listado só para conferência.
        aba.secao(f"NOTAS JÁ PAGAS NA SEMANA {semana:02d} (fora do total)", pagas,
                  entra_no_total=False)

    aba.total_geral()


def _nome_da_aba(obra: dict, usados: set[str]) -> str:
    base = f"RelatorioLancamento {_sigla_da_obra(obra)}"[:31]
    nome, contador = base, 2
    while nome in usados:
        sufixo = f" {contador}"
        nome = base[:31 - len(sufixo)] + sufixo
        contador += 1
    usados.add(nome)
    return nome


def gerar_planilha(abas: list[tuple[dict, dict]]) -> bytes:
    """Monta o arquivo com uma aba por obra. `abas` = [(obra, dados_fechamento)]."""
    planilha = Workbook()
    planilha.remove(planilha.active)

    usados: set[str] = set()
    for obra, dados in abas:
        aba = planilha.create_sheet(_nome_da_aba(obra, usados))
        montar_aba(aba, obra, dados)

    buffer = io.BytesIO()
    planilha.save(buffer)
    return buffer.getvalue()


def nome_arquivo(obra: dict, dados: dict) -> str:
    """Nome no padrão do arquivo que já circula no escritório."""
    def limpo(texto: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", (texto or "").upper()).strip("_")

    inicio = data_br(dados["data_inicio"]).replace("/", "_")
    fim = data_br(dados["data_fim"]).replace("/", "_")
    return f"REL_DE_PAGAMENTO_{limpo(_sigla_da_obra(obra))}_{inicio}_A_{fim}.xlsx"
