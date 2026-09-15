# -*- coding: utf-8 -*-
"""Relatório semanal em PDF — o documento que vai para o cliente.

Mesma lógica de dados do app (`fechamento.dados_fechamento`), mas com
apresentação de documento: carimbo de prancha técnica, quadro financeiro,
prancha fotográfica e campo de assinatura.
"""

from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image, KeepTogether,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

from ..utils import bytes_anexo, data_br, moeda, numero_br

LATAO = colors.HexColor("#9A6F35")
LATAO_CLARO = colors.HexColor("#F2E7D5")
GRAFITE = colors.HexColor("#1F2529")
CINZA = colors.HexColor("#6B7378")
CINZA_CLARO = colors.HexColor("#E4E6E7")
VERDE = colors.HexColor("#3F7D5C")
VERMELHO = colors.HexColor("#B4472F")
AMBAR = colors.HexColor("#B98B4E")

MARGEM = 16 * mm
LARGURA_UTIL = A4[0] - 2 * MARGEM


def _estilos() -> dict:
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=GRAFITE)
    return {
        "base": base,
        "titulo": ParagraphStyle("titulo", parent=base, fontName="Helvetica-Bold", fontSize=19,
                                 leading=22, textColor=GRAFITE, spaceAfter=2),
        "subtitulo": ParagraphStyle("subtitulo", parent=base, fontSize=9, textColor=CINZA),
        "secao": ParagraphStyle("secao", parent=base, fontName="Helvetica-Bold", fontSize=10.5,
                                textColor=LATAO, spaceBefore=10, spaceAfter=4),
        "rotulo": ParagraphStyle("rotulo", parent=base, fontName="Helvetica-Bold", fontSize=7,
                                 textColor=CINZA, leading=9),
        "valor": ParagraphStyle("valor", parent=base, fontName="Helvetica-Bold", fontSize=12,
                                textColor=GRAFITE, leading=14),
        "corpo": base,
        "pequeno": ParagraphStyle("pequeno", parent=base, fontSize=8, textColor=CINZA, leading=11),
        "celula": ParagraphStyle("celula", parent=base, fontSize=8.5, leading=11),
        "celula_dir": ParagraphStyle("celula_dir", parent=base, fontSize=8.5, leading=11, alignment=TA_RIGHT),
        "legenda": ParagraphStyle("legenda", parent=base, fontSize=7.5, textColor=CINZA,
                                  leading=9.5, alignment=TA_CENTER),
    }


class BarraProgresso(Flowable):
    """Barra previsto x realizado com semáforo (verde / amarelo / vermelho)."""

    def __init__(self, percentual: float, largura: float, altura: float = 9,
                 invertido: bool = False):
        super().__init__()
        self.percentual = max(0.0, float(percentual or 0))
        self.width = largura
        self.height = altura
        # invertido=True para metas: ali 100% é a meta cumprida, não estouro.
        self.invertido = invertido

    def draw(self):
        c = self.canv
        c.setFillColor(CINZA_CLARO)
        c.roundRect(0, 0, self.width, self.height, 2, stroke=0, fill=1)
        if self.invertido:
            cor = VERDE if self.percentual >= 100 else (LATAO if self.percentual > 0 else CINZA_CLARO)
        else:
            cor = VERDE if self.percentual <= 85 else (AMBAR if self.percentual <= 100 else VERMELHO)
        preenchido = min(self.percentual, 100) / 100 * self.width
        if preenchido > 0:
            c.setFillColor(cor)
            c.roundRect(0, 0, max(preenchido, 3), self.height, 2, stroke=0, fill=1)
        if self.percentual > 100 and not self.invertido:  # estouro: marca vermelha no fim
            c.setFillColor(VERMELHO)
            c.rect(self.width - 3, 0, 3, self.height, stroke=0, fill=1)


class _Canvas(pdfcanvas.Canvas):
    """Canvas em dois passes, para imprimir "página X de Y"."""

    def __init__(self, *args, **kwargs):
        self._cabecalho = kwargs.pop("cabecalho", "")
        super().__init__(*args, **kwargs)
        self._paginas = []

    def showPage(self):
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self._rodape(total)
            super().showPage()
        super().save()

    def _rodape(self, total: int):
        self.saveState()
        self.setStrokeColor(CINZA_CLARO)
        self.setLineWidth(0.5)
        self.line(MARGEM, 13 * mm, A4[0] - MARGEM, 13 * mm)
        self.setFont("Helvetica", 7)
        self.setFillColor(CINZA)
        self.drawString(MARGEM, 9 * mm, self._cabecalho[:90])
        self.drawRightString(A4[0] - MARGEM, 9 * mm, f"Página {self._pageNumber} de {total}")
        self.restoreState()


def _faixa_topo(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(LATAO)
    canvas.rect(0, A4[1] - 6 * mm, A4[0], 6 * mm, stroke=0, fill=1)
    canvas.restoreState()


def _kpi(rotulo: str, valor: str, estilos, cor=None):
    return [
        Paragraph(rotulo.upper(), estilos["rotulo"]),
        Paragraph(f'<font color="{(cor or GRAFITE).hexval()}">{escape(valor)}</font>', estilos["valor"]),
    ]



def _bloco_metas(d, estilos):
    """Quadro de metas com barra de progresso, arrasto e status.

    Substitui o antigo campo de meta em texto livre. Se a obra ainda não usa
    metas nomeadas, cai no texto antigo para não deixar o relatório mudo.
    """
    metas = d.get("metas") or []
    if not metas:
        texto_legado = d.get("meta") or ""
        cor = VERDE if d.get("meta_atingida") else (VERMELHO if texto_legado else CINZA)
        rotulo = ("META ATINGIDA" if d.get("meta_atingida")
                  else ("META NÃO ATINGIDA" if texto_legado else "SEM META DEFINIDA"))
        tabela = Table([[
            Paragraph("META DA SEMANA", estilos["rotulo"]),
            Paragraph(f'<font color="{cor.hexval()}"><b>{rotulo}</b></font>', estilos["celula_dir"]),
        ], [
            Paragraph(escape(texto_legado or "Nenhuma meta foi definida para esta semana."),
                      estilos["corpo"]), "",
        ]], colWidths=[LARGURA_UTIL * 0.6, LARGURA_UTIL * 0.4])
        tabela.setStyle(TableStyle([
            ("SPAN", (0, 1), (1, 1)),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAF8F4")),
            ("BOX", (0, 0), (-1, -1), 0.6, CINZA_CLARO),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return [tabela, Spacer(1, 9)]

    resumo = d.get("metas_resumo") or {}
    elementos = [Paragraph(
        f"METAS DA SEMANA · {resumo.get('concluidas', 0)}/{resumo.get('total', 0)} CONCLUÍDAS",
        estilos["secao"])]

    linhas = [[
        Paragraph("META", estilos["rotulo"]),
        Paragraph("AVANÇO NA SEMANA", estilos["rotulo"]),
        Paragraph("SITUAÇÃO", estilos["celula_dir"]),
    ]]

    for meta in metas:
        nome = escape(meta["nome"])
        if meta.get("semanas_arrastada"):
            aviso = "ª semana arrastando"
            nome += (f'<br/><font size="7" color="{VERMELHO.hexval()}">'
                     f'{meta["semanas_arrastada"] + 1}{aviso}</font>')
        elif meta.get("descricao"):
            nome += f'<br/><font size="7" color="{CINZA.hexval()}">{escape(meta["descricao"])}</font>'

        if meta["concluida"]:
            cor, situacao = VERDE, "concluída"
        elif meta.get("transferida"):
            cor, situacao = VERMELHO, "vai para a próxima semana"
        elif meta["percentual"] > 0:
            cor, situacao = LATAO, "em andamento"
        else:
            cor, situacao = CINZA, "não iniciada"

        avanco = meta["percentual"] - meta["percentual_inicial"]
        legenda = f'{numero_br(meta["percentual_inicial"], 0)}% → {numero_br(meta["percentual"], 0)}%'
        if avanco > 0:
            legenda += f' (+{numero_br(avanco, 0)} p.p.)'
        elif avanco == 0 and meta["percentual"] > 0:
            legenda += " (sem avanço)"

        linhas.append([
            Paragraph(nome, estilos["celula"]),
            [BarraProgresso(meta["percentual"], largura=LARGURA_UTIL * 0.30, invertido=True),
             Paragraph(f'<font size="7" color="{CINZA.hexval()}">{legenda}</font>', estilos["celula"])],
            Paragraph(f'<font color="{cor.hexval()}"><b>{situacao}</b></font>', estilos["celula_dir"]),
        ])

    tabela = Table(linhas, colWidths=[LARGURA_UTIL * 0.42, LARGURA_UTIL * 0.33, LARGURA_UTIL * 0.25],
                   repeatRows=1)
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LATAO_CLARO),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, CINZA_CLARO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    elementos += [tabela, Spacer(1, 9)]
    return elementos


def _tabela_kpis(itens: list, estilos, colunas: int = 4):
    linhas_rotulo, linhas_valor = [], []
    for rotulo, valor, cor in itens:
        celula = _kpi(rotulo, valor, estilos, cor)
        linhas_rotulo.append(celula[0])
        linhas_valor.append(celula[1])
    largura = LARGURA_UTIL / colunas
    t = Table([linhas_rotulo, linhas_valor], colWidths=[largura] * len(itens))
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAF8F4")),
        ("BOX", (0, 0), (-1, -1), 0.6, LATAO),
        ("LINEAFTER", (0, 0), (-2, -1), 0.4, CINZA_CLARO),
    ]))
    return t


def _tabela(dados, larguras, estilos, cabecalho=True, alinhar_direita=()):
    t = Table(dados, colWidths=larguras, repeatRows=1 if cabecalho else 0)
    estilo = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), GRAFITE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, CINZA_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, CINZA_CLARO),
    ]
    if cabecalho:
        estilo += [
            ("BACKGROUND", (0, 0), (-1, 0), LATAO_CLARO),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6B4E22")),
        ]
    for col in alinhar_direita:
        estilo.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    t.setStyle(TableStyle(estilo))
    return t


def _grade_fotos(fotos, estilos, colunas: int = 2, limite: int = 12):
    """Prancha fotográfica: miniaturas com legenda e ambiente."""
    celulas = []
    largura_celula = LARGURA_UTIL / colunas - 6
    for foto in fotos[:limite]:
        conteudo = bytes_anexo(foto["arquivo"] if "arquivo" in foto else foto["url"].split("/")[-1])
        if not conteudo:
            continue
        try:
            leitor = ImageReader(io.BytesIO(conteudo))
            iw, ih = leitor.getSize()
            altura = largura_celula * ih / iw
            altura = min(altura, 62 * mm)
            largura = altura * iw / ih
            img = Image(io.BytesIO(conteudo), width=largura, height=altura)
            img.hAlign = "CENTER"
        except Exception:
            continue
        rotulo = foto.get("legenda") or foto.get("ambiente") or ""
        texto = escape(rotulo) if rotulo else "&nbsp;"
        celulas.append([img, Paragraph(texto, estilos["legenda"])])

    if not celulas:
        return None

    linhas = []
    for i in range(0, len(celulas), colunas):
        grupo = celulas[i:i + colunas]
        while len(grupo) < colunas:
            grupo.append([Spacer(1, 1), Paragraph("&nbsp;", estilos["legenda"])])
        linhas.append([c[0] for c in grupo])
        linhas.append([c[1] for c in grupo])

    t = Table(linhas, colWidths=[LARGURA_UTIL / colunas] * colunas)
    estilo = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
              ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    for i in range(0, len(linhas), 2):
        estilo.append(("BOTTOMPADDING", (0, i), (-1, i), 1))
    t.setStyle(TableStyle(estilo))
    return t


def gerar_pdf(d: dict, resumo_executivo: str = "") -> bytes:
    estilos = _estilos()
    fin = d["financeiro"]
    ind = d["indicadores"]
    buffer = io.BytesIO()

    cabecalho = f"{d['obra_nome']} · Semana {d['semana_numero']} · {data_br(d['data_inicio'])} a {data_br(d['data_fim'])}"

    doc = BaseDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM,
        topMargin=18 * mm, bottomMargin=20 * mm,
        title=f"Relatório Semanal — {d['obra_nome']} — Semana {d['semana_numero']}",
        author="Obra ERP",
    )
    frame = Frame(MARGEM, 20 * mm, LARGURA_UTIL, A4[1] - 38 * mm, id="corpo")
    doc.addPageTemplates([PageTemplate(id="padrao", frames=[frame], onPage=_faixa_topo)])

    E: list = []

    # ---- capa / cabeçalho ----
    E.append(Paragraph("RELATÓRIO SEMANAL DE OBRA", estilos["rotulo"]))
    E.append(Paragraph(escape(d["obra_nome"]), estilos["titulo"]))
    linha_sub = f"Semana {d['semana_numero']} · {data_br(d['data_inicio'])} a {data_br(d['data_fim'])}"
    if d["obra"].get("cliente"):
        linha_sub += f" · Cliente: {escape(d['obra']['cliente'])}"
    E.append(Paragraph(linha_sub, estilos["subtitulo"]))
    if d["obra"].get("endereco"):
        E.append(Paragraph(escape(d["obra"]["endereco"]), estilos["pequeno"]))
    E.append(Spacer(1, 9))

    # ---- metas ----
    E.extend(_bloco_metas(d, estilos))

    # ---- KPIs ----
    E.append(_tabela_kpis([
        ("Custo da semana", moeda(fin["custo_semana"]), GRAFITE),
        ("Folha", moeda(fin["folha"]), GRAFITE),
        ("Materiais e serviços", moeda(fin["despesas"]), GRAFITE),
        ("Horas lançadas", f"{numero_br(ind['total_horas'], 1)} h", GRAFITE),
    ], estilos))
    E.append(Spacer(1, 8))

    # ---- previsto x realizado ----
    if fin["orcamento_previsto"]:
        pct = fin["percentual_orcamento"] or 0
        cor = VERDE if pct <= 85 else (AMBAR if pct <= 100 else VERMELHO)
        E.append(Paragraph("PREVISTO x REALIZADO", estilos["secao"]))
        E.append(BarraProgresso(pct, LARGURA_UTIL))
        E.append(Spacer(1, 3))
        situacao = "dentro do orçamento" if pct <= 100 else "ACIMA do orçamento"
        E.append(Paragraph(
            f'Previsto {moeda(fin["orcamento_previsto"])} · Realizado {moeda(fin["custo_semana"])} · '
            f'<font color="{cor.hexval()}"><b>{numero_br(pct, 1)}% ({situacao})</b></font> · '
            f'Saldo {moeda(fin["saldo"])}', estilos["pequeno"]))
        E.append(Spacer(1, 4))

    # ---- resumo executivo ----
    if resumo_executivo:
        E.append(Paragraph("RESUMO EXECUTIVO", estilos["secao"]))
        for paragrafo in resumo_executivo.split("\n"):
            if paragrafo.strip():
                E.append(Paragraph(escape(paragrafo.strip()), estilos["corpo"]))
        E.append(Spacer(1, 4))

    # ---- avanço físico ----
    E.append(Paragraph("AVANÇO FÍSICO DIA A DIA", estilos["secao"]))
    linhas = [["DIA", "DATA", "PRODUÇÃO REGISTRADA", "CLIMA", "EFETIVO"]]
    for dia in d["dias"]:
        if dia["dia_semana"] in ("Sábado", "Domingo") and not (dia["producao_dia"] or dia["horas"]):
            continue
        producao = dia["producao_dia"] or "<i>sem registro</i>"
        if dia["observacoes"]:
            producao += f'<br/><font color="{CINZA.hexval()}" size="7.5">Obs.: {escape(dia["observacoes"])}</font>'
        if dia["assinado"]:
            producao += f'<br/><font color="{VERDE.hexval()}" size="7">✓ assinado por {escape(dia["assinado_por"])}</font>'
        linhas.append([
            Paragraph(f"<b>{dia['dia_semana'][:3]}</b>", estilos["celula"]),
            Paragraph(data_br(dia["data"]), estilos["celula"]),
            Paragraph(producao if dia["producao_dia"] else
                      f'<font color="{CINZA.hexval()}">sem registro de produção</font>', estilos["celula"]),
            Paragraph(escape((dia["clima"] or "—").encode("ascii", "ignore").decode() or "—"), estilos["celula"]),
            Paragraph(f"{numero_br(dia['efetivo'], 0)}", estilos["celula_dir"]),
        ])
    E.append(_tabela(linhas, [16 * mm, 20 * mm, LARGURA_UTIL - 82 * mm, 26 * mm, 20 * mm], estilos,
                     alinhar_direita=(4,)))

    # ---- medições ----
    if d["medicoes"]:
        E.append(Paragraph("MEDIÇÕES DE SERVIÇO", estilos["secao"]))
        linhas = [["DIA", "SERVIÇO", "ETAPA", "QTD."]]
        for m in d["medicoes"]:
            linhas.append([
                Paragraph(m["dia_semana"][:3], estilos["celula"]),
                Paragraph(escape(m["descricao"]), estilos["celula"]),
                Paragraph(escape(m["etapa_nome"] or "—"), estilos["celula"]),
                Paragraph(f"{numero_br(m['quantidade'], 2)} {escape(m['unidade'])}", estilos["celula_dir"]),
            ])
        E.append(_tabela(linhas, [16 * mm, LARGURA_UTIL - 82 * mm, 36 * mm, 30 * mm], estilos,
                         alinhar_direita=(3,)))

    # ---- terceirizados ----
    terceiros = d.get("terceirizados") or []
    if terceiros and fin:
        resumo_t = d.get("terceirizados_resumo") or {}
        E.append(Paragraph("MEDIÇÃO DE TERCEIRIZADOS E PRODUÇÃO", estilos["secao"]))
        linhas = [["EMPRESA / SERVIÇO", "NA SEMANA", "ACUMULADO", "VALOR"]]
        for m in terceiros:
            titulo = escape(m["empresa"])
            if m.get("tipo") == "avulso":
                titulo += f' <font size="7" color="{LATAO.hexval()}">· avulso</font>'
            complemento = escape(m["servico"])
            if not m["entra_no_caixa"]:
                complemento += ' · <font color="' + AMBAR.hexval() + '">fora do caixa da semana</font>'
            linhas.append([
                Paragraph(f'{titulo}<br/><font size="7" color="{CINZA.hexval()}">{complemento}</font>',
                          estilos["celula"]),
                Paragraph(f"{numero_br(m['percentual'], 2)}%", estilos["celula_dir"]),
                Paragraph(f"{numero_br(m['acumulado'], 2)}% de 100%", estilos["celula_dir"]),
                Paragraph(moeda(m["valor"]), estilos["celula_dir"]),
            ])
        linhas.append([
            Paragraph("<b>TOTAL MEDIDO</b>", estilos["celula"]), "", "",
            Paragraph(f"<b>{moeda(resumo_t.get('valor_medido', 0))}</b>", estilos["celula_dir"]),
        ])
        E.append(_tabela(linhas, [LARGURA_UTIL - 96 * mm, 26 * mm, 34 * mm, 36 * mm], estilos,
                         alinhar_direita=(1, 2, 3)))
        if resumo_t.get("valor_producao"):
            E.append(Paragraph(
                f"Mão de obra por produção nesta semana: {moeda(resumo_t['valor_producao'])} "
                f"(profissionais pagos por tarefa, fora da folha por hora).", estilos["legenda"]))
        if resumo_t.get("valor_fora_do_caixa"):
            E.append(Paragraph(
                f"Do total medido, {moeda(resumo_t['valor_fora_do_caixa'])} não entrou no "
                f"pagamento desta semana, por decisão de fluxo de caixa.", estilos["legenda"]))

    # ---- fretes ----
    fretes = d.get("fretes") or {}
    if fretes.get("viagens") and fin:
        E.append(Paragraph("FRETES DA SEMANA", estilos["secao"]))
        linhas = [["TRANSPORTADOR", "VIAGENS", "TOTAL"]]
        for t in fretes["transportadores"]:
            linhas.append([
                Paragraph(escape(t["transportador"]), estilos["celula"]),
                Paragraph(str(t["viagens"]), estilos["celula_dir"]),
                Paragraph(moeda(t["total"]), estilos["celula_dir"]),
            ])
        linhas.append([
            Paragraph("<b>TOTAL</b>", estilos["celula"]),
            Paragraph(f"<b>{fretes['viagens']}</b>", estilos["celula_dir"]),
            Paragraph(f"<b>{moeda(fretes['total'])}</b>", estilos["celula_dir"]),
        ])
        E.append(_tabela(linhas, [LARGURA_UTIL - 70 * mm, 30 * mm, 40 * mm], estilos,
                         alinhar_direita=(1, 2)))

    # ---- clima / pendências ----
    avisos = []
    for c in d["clima_alertas"]:
        avisos.append(f"{c['dia_semana']} ({data_br(c['data'])}): {c['clima']}")
    if d["dias_pendentes"]:
        avisos.append("Dias úteis sem nenhum lançamento: " + ", ".join(d["dias_pendentes"]))
    if avisos:
        E.append(Paragraph("OCORRÊNCIAS E PENDÊNCIAS", estilos["secao"]))
        for a in avisos:
            E.append(Paragraph("• " + escape(a), estilos["corpo"]))

    # ---- quadro financeiro ----
    E.append(Paragraph("QUADRO FINANCEIRO", estilos["secao"]))
    quadro = [
        ["FOLHA", "REEMBOLSO GESTOR", "A PAGAR TERCEIROS", "TOTAL PAGO"],
        [moeda(fin["folha"]), moeda(fin["reembolso"]), moeda(fin["terceiros"]), moeda(fin["pago"])],
    ]
    t = Table(quadro, colWidths=[LARGURA_UTIL / 4] * 4)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LATAO_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#6B4E22")),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 1), (-1, 1), 11),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BOX", (0, 0), (-1, -1), 0.8, LATAO),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, CINZA_CLARO),
    ]))
    E.append(t)

    # ---- folha por profissional ----
    if d["folha_detalhada"]:
        E.append(Paragraph("FOLHA POR PROFISSIONAL", estilos["secao"]))
        linhas = [["PROFISSIONAL", "FUNÇÃO", "HORAS", "TOTAL"]]
        for p in d["folha_detalhada"]:
            linhas.append([
                Paragraph(escape(p["nome"]), estilos["celula"]),
                Paragraph(escape(p["funcao"] or "—"), estilos["celula"]),
                Paragraph(f"{numero_br(p['horas'], 1)}", estilos["celula_dir"]),
                Paragraph(moeda(p["total"]), estilos["celula_dir"]),
            ])
        linhas.append(["", Paragraph("<b>TOTAL</b>", estilos["celula_dir"]),
                       Paragraph(f"<b>{numero_br(ind['total_horas'], 1)}</b>", estilos["celula_dir"]),
                       Paragraph(f"<b>{moeda(fin['folha'])}</b>", estilos["celula_dir"])])
        E.append(_tabela(linhas, [LARGURA_UTIL - 100 * mm, 40 * mm, 25 * mm, 35 * mm], estilos,
                         alinhar_direita=(2, 3)))

    # ---- despesas ----
    E.append(Paragraph("DESPESAS DA SEMANA", estilos["secao"]))
    if d["despesas"]:
        linhas = [["DIA", "CATEGORIA", "DESCRIÇÃO / FORNECEDOR", "STATUS", "VALOR"]]
        for x in d["despesas"]:
            desc = escape(x["descricao"])
            # Fornecedor e nota fiscal na mesma linha: é assim que a
            # contabilidade confere o lançamento contra o documento.
            rodape = []
            if x["fornecedor"]:
                rodape.append(escape(x["fornecedor"]))
            if x.get("numero_nota"):
                rodape.append(f'NF {escape(x["numero_nota"])}')
            if rodape:
                desc += f'<br/><font color="{CINZA.hexval()}" size="7">{" · ".join(rodape)}</font>'
            if x["nome_arquivo"]:
                desc += f'<br/><font color="{CINZA.hexval()}" size="7">anexo: {escape(x["nome_arquivo"])}</font>'
            status = x["status_pagamento"]
            if x["vencimento"] and status == "Em Aberto":
                status += f"<br/><font size='7'>vence {data_br(x['vencimento'])}</font>"
            linhas.append([
                Paragraph(x["dia_semana"][:3], estilos["celula"]),
                Paragraph(escape(x["categoria"]), estilos["celula"]),
                Paragraph(desc, estilos["celula"]),
                Paragraph(status, estilos["celula"]),
                Paragraph(moeda(x["valor"]), estilos["celula_dir"]),
            ])
        linhas.append(["", "", "", Paragraph("<b>TOTAL</b>", estilos["celula_dir"]),
                       Paragraph(f"<b>{moeda(fin['despesas'])}</b>", estilos["celula_dir"])])
        E.append(_tabela(linhas, [14 * mm, 24 * mm, LARGURA_UTIL - 108 * mm, 32 * mm, 28 * mm],
                         estilos, alinhar_direita=(4,)))
        if d["despesas_por_categoria"]:
            resumo_cat = " · ".join(
                f"{cat}: {moeda(val)}" for cat, val in sorted(
                    d["despesas_por_categoria"].items(), key=lambda kv: -kv[1]))
            E.append(Spacer(1, 3))
            E.append(Paragraph(escape(resumo_cat), estilos["pequeno"]))
    else:
        E.append(Paragraph("Nenhum lançamento de caixa nesta semana.", estilos["pequeno"]))

    # ---- prancha fotográfica ----
    if d["fotos"]:
        grade = _grade_fotos(d["fotos"], estilos)
        if grade is not None:
            E.append(Paragraph("REGISTRO FOTOGRÁFICO", estilos["secao"]))
            E.append(grade)

    # ---- assinatura ----
    E.append(Spacer(1, 16))
    assinatura = Table([
        ["", ""],
        [Paragraph("Responsável técnico / Gestor da obra", estilos["legenda"]),
         Paragraph("Cliente / Contratante", estilos["legenda"])],
    ], colWidths=[LARGURA_UTIL / 2 - 8, LARGURA_UTIL / 2 - 8], rowHeights=[16 * mm, 14])
    assinatura.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (0, 0), 0.6, GRAFITE),
        ("LINEBELOW", (1, 0), (1, 0), 0.6, GRAFITE),
        ("VALIGN", (0, 1), (-1, 1), "TOP"),
        ("TOPPADDING", (0, 1), (-1, 1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    E.append(KeepTogether(assinatura))
    E.append(Spacer(1, 6))
    E.append(Paragraph(
        f"Documento gerado em {datetime.now().strftime('%d/%m/%Y às %H:%M')} pelo Obra ERP · "
        f"os valores refletem os lançamentos registrados no aplicativo até este momento.",
        estilos["pequeno"]))

    doc.build(E, canvasmaker=lambda *a, **kw: _Canvas(*a, cabecalho=cabecalho, **kw))
    return buffer.getvalue()
