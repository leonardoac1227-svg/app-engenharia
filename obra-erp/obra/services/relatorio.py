# -*- coding: utf-8 -*-
"""Relatórios em texto e CSV (o PDF fica em `pdf.py`)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from ..utils import numero_br, data_br, moeda, slug


def nome_arquivo(dados: dict, extensao: str) -> str:
    return f"{slug(dados['obra_nome'])}-semana-{dados['semana_numero']:02d}.{extensao}"


def relatorio_texto(d: dict, resumo_executivo: str = "") -> str:
    f = d["financeiro"]
    ind = d["indicadores"]
    L: list[str] = []
    cab = f"RELATORIO SEMANAL — {d['obra_nome']} — SEMANA {d['semana_numero']}"
    L += [cab, "=" * len(cab), ""]
    L.append(f"Periodo: {data_br(d['data_inicio'])} a {data_br(d['data_fim'])}")
    if d["obra"].get("cliente"):
        L.append(f"Cliente: {d['obra']['cliente']}")
    L.append("")

    if resumo_executivo:
        L += ["0. RESUMO EXECUTIVO", ""]
        L += ["   " + linha for linha in resumo_executivo.splitlines()]
        L.append("")

    L += ["1. INDICADORES",
          f"   - Total de horas lancadas: {numero_br(ind['total_horas'], 1)} h",
          f"   - Efetivo medio por dia trabalhado: {numero_br(ind['efetivo_medio'], 1)}",
          f"   - Fotos anexadas: {ind['total_fotos']}",
          f"   - Custo medio por dia trabalhado: {moeda(ind['custo_medio_dia'])}", ""]

    fretes = d.get("fretes") or {}
    if fretes.get("viagens"):
        L.append("2. FRETES")
        for t in fretes["transportadores"]:
            L.append(f"   - {t['transportador']}: {t['viagens']} viagem(ns) · {moeda(t['total'])}")
        L.append(f"   TOTAL EM FRETES: {moeda(fretes['total'])}")
        L.append("")

    if d["dias_pendentes"]:
        L.append(f"! Dias uteis sem nenhum lancamento: {', '.join(d['dias_pendentes'])}")
        L.append("")

    L.append("3. RELATORIO FINANCEIRO")
    if f["folha"]:
        L.append(f"   - Folha de pagamento: {moeda(f['folha'])}")
    L.append(f"   - Reembolso ao gestor: {moeda(f['reembolso'])}")
    L.append(f"   - Em aberto: {moeda(f['em_aberto'])}")
    L.append(f"   - Ja pago no periodo: {moeda(f['pago'])}")
    L.append(f"   - CUSTO TOTAL DA SEMANA: {moeda(f['custo_semana'])}")
    if f["orcamento_previsto"]:
        L.append(f"   - Orcamento previsto: {moeda(f['orcamento_previsto'])}")
        L.append(f"   - Realizado: {numero_br(f['percentual_orcamento'], 1)}% do previsto "
                 f"({'dentro' if f['percentual_orcamento'] <= 100 else 'ACIMA'} do orcamento)")
        L.append(f"   - Saldo da semana: {moeda(f['saldo'])}")
    L.append("")

    if d["folha_detalhada"]:
        L.append("4. FOLHA POR PROFISSIONAL")
        for p in d["folha_detalhada"]:
            funcao = f" ({p['funcao']})" if p["funcao"] else ""
            L.append(f"   - {p['nome']}{funcao}: {numero_br(p['horas'], 1)} h · {moeda(p['total'])}")
        L.append("")

    L.append("5. DESPESAS DETALHADAS")
    if d["despesas"]:
        for x in d["despesas"]:
            anexo = x["nome_arquivo"] or ("anexo" if x["imagem_path"] else "sem anexo")
            venc = f" · vence {data_br(x['vencimento'])}" if x["vencimento"] else ""
            forn = f" · {x['fornecedor']}" if x["fornecedor"] else ""
            nota = f" · NF {x['numero_nota']}" if x.get("numero_nota") else ""
            L.append(f"   - [{x['dia_semana']}] {x['categoria']} · {x['descricao']}{forn}{nota} · "
                     f"{moeda(x['valor'])} · {x['status_pagamento']}{venc} · {anexo}")
    else:
        L.append("   Nenhum lancamento de caixa nesta semana.")
    L.append("")

    if d["fotos"]:
        L.append(f"6. REGISTRO FOTOGRAFICO: {len(d['fotos'])} foto(s) anexada(s) — ver versao em PDF.")
        L.append("")

    L.append(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · Obra ERP")
    return "\n".join(L)


def despesas_csv(d: dict) -> str:
    """CSV das despesas — abre direto no Excel do escritório/contabilidade."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow([
        "Obra", "Semana", "Data", "Dia", "Categoria", "Descrição",
        "Fornecedor", "Nota fiscal", "Valor", "Status", "Vencimento", "Pago em", "Anexo",
    ])
    for x in d["despesas"]:
        escritor.writerow([
            d["obra_nome"], d["semana_numero"], data_br(x["data"]), x["dia_semana"],
            x["categoria"], x["descricao"], x["fornecedor"],
            x.get("numero_nota") or "",
            f"{x['valor']:.2f}".replace(".", ","), x["status_pagamento"],
            data_br(x["vencimento"]), data_br(x["pago_em"]),
            x["nome_arquivo"] or "",
        ])
    escritor.writerow([])
    if d["financeiro"]["folha"]:
        escritor.writerow(["Folha da semana", "", "", "", "", "", "", "",
                           f"{d['financeiro']['folha']:.2f}".replace(".", ",")])
    escritor.writerow(["Custo total da semana", "", "", "", "", "", "", "",
                       f"{d['financeiro']['custo_semana']:.2f}".replace(".", ",")])
    # BOM para o Excel reconhecer os acentos automaticamente
    return "\ufeff" + buffer.getvalue()
