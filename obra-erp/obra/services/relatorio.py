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

    metas = d.get("metas") or []
    if metas:
        resumo_metas = d.get("metas_resumo") or {}
        L += [f"1. METAS DA SEMANA ({resumo_metas.get('concluidas', 0)}/{resumo_metas.get('total', 0)} concluidas)"]
        for meta in metas:
            if meta["concluida"]:
                situacao = "CONCLUIDA"
            elif meta.get("transferida"):
                situacao = "VAI PARA A PROXIMA SEMANA"
            elif meta["percentual"] > 0:
                situacao = "EM ANDAMENTO"
            else:
                situacao = "NAO INICIADA"

            L.append(f"   - {meta['nome']}: {numero_br(meta['percentual'], 0)}% [{situacao}]")
            avanco = meta["percentual"] - meta["percentual_inicial"]
            detalhe = (f"     inicio da semana {numero_br(meta['percentual_inicial'], 0)}% "
                       f"-> fim {numero_br(meta['percentual'], 0)}%")
            if avanco > 0:
                detalhe += f" (+{numero_br(avanco, 0)} p.p.)"
            elif meta["percentual"] > 0:
                detalhe += " (sem avanco na semana)"
            L.append(detalhe)
            if meta.get("semanas_arrastada"):
                L.append(f"     ATENCAO: {meta['semanas_arrastada'] + 1}a semana arrastando")
            for marca in meta.get("progresso", []):
                obs = f" - {marca['observacao']}" if marca.get("observacao") else ""
                L.append(f"       {marca['dia_semana'][:3]}: {numero_br(marca['percentual'], 0)}%{obs}")
        L.append("")
    else:
        L += ["1. META DA SEMANA",
              f"   - {d['meta'] or '(nao definida)'}",
              f"   - Status: {'ATINGIDA' if d['meta_atingida'] else 'NAO ATINGIDA'}", ""]

    L += ["2. INDICADORES",
          f"   - Dias com RDO preenchido: {ind['dias_com_rdo']}/7",
          f"   - Total de horas lancadas: {numero_br(ind['total_horas'], 1)} h",
          f"   - Efetivo medio por dia trabalhado: {numero_br(ind['efetivo_medio'], 1)}",
          f"   - Fotos anexadas: {ind['total_fotos']}",
          f"   - Custo medio por dia trabalhado: {moeda(ind['custo_medio_dia'])}", ""]

    L.append("3. AVANCO FISICO — SEGUNDA A SEXTA")
    for dia in d["dias"]:
        if dia["dia_semana"] in ("Sábado", "Domingo"):
            continue
        marca = "*" if dia["assinado"] else " "
        producao = dia["producao_dia"] or "sem registro de producao"
        L.append(f"  {marca}[{dia['dia_semana']} {data_br(dia['data'])}] {producao}")
    fds = [x for x in d["dias"] if x["dia_semana"] in ("Sábado", "Domingo") and (x["producao_dia"] or x["horas"])]
    if fds:
        L.append("")
        L.append("   Fim de semana:")
        for dia in fds:
            L.append(f"   [{dia['dia_semana']} {data_br(dia['data'])}] {dia['producao_dia'] or '(sem texto)'}")
    L.append("")

    if d["medicoes"]:
        L.append("4. MEDICOES DE SERVICO")
        for m in d["medicoes"]:
            etapa = f" · {m['etapa_nome']}" if m["etapa_nome"] else ""
            L.append(f"   - [{m['dia_semana']}] {m['descricao']}: {numero_br(m['quantidade'], 2)} {m['unidade']}{etapa}")
        L.append("")

    L.append("5. OCORRENCIAS DE CLIMA")
    if d["clima_alertas"]:
        for c in d["clima_alertas"]:
            L.append(f"   - {c['dia_semana']} ({data_br(c['data'])}): {c['clima']}")
    else:
        L.append("   Sem ocorrencias relevantes (semana de clima limpo).")
    L.append("")

    L.append("6. OBSERVACOES E ALERTAS")
    obs = [x for x in d["dias"] if x["observacoes"]]
    if obs:
        for dia in obs:
            L.append(f"   - [{dia['dia_semana']}] {dia['observacoes']}")
    else:
        L.append("   Nenhuma observacao registrada.")
    if d["dias_pendentes"]:
        L.append(f"   ! Dias uteis sem nenhum lancamento: {', '.join(d['dias_pendentes'])}")
    L.append("")

    L.append("7. RELATORIO FINANCEIRO")
    L.append(f"   - Folha de pagamento: {moeda(f['folha'])}")
    L.append(f"   - Reembolso ao gestor: {moeda(f['reembolso'])}")
    L.append(f"   - A pagar a terceiros (folha + em aberto): {moeda(f['terceiros'])}")
    L.append(f"   - Ja pago no periodo: {moeda(f['pago'])}")
    L.append(f"   - CUSTO TOTAL DA SEMANA: {moeda(f['custo_semana'])}")
    if f["orcamento_previsto"]:
        L.append(f"   - Orcamento previsto: {moeda(f['orcamento_previsto'])}")
        L.append(f"   - Realizado: {numero_br(f['percentual_orcamento'], 1)}% do previsto "
                 f"({'dentro' if f['percentual_orcamento'] <= 100 else 'ACIMA'} do orcamento)")
        L.append(f"   - Saldo da semana: {moeda(f['saldo'])}")
    L.append("")

    if d["folha_detalhada"]:
        L.append("8. FOLHA POR PROFISSIONAL")
        for p in d["folha_detalhada"]:
            funcao = f" ({p['funcao']})" if p["funcao"] else ""
            L.append(f"   - {p['nome']}{funcao}: {numero_br(p['horas'], 1)} h · {moeda(p['total'])}")
        L.append("")

    L.append("9. DESPESAS DETALHADAS")
    if d["despesas"]:
        for x in d["despesas"]:
            anexo = x["nome_arquivo"] or ("anexo" if x["imagem_path"] else "sem anexo")
            venc = f" · vence {data_br(x['vencimento'])}" if x["vencimento"] else ""
            forn = f" · {x['fornecedor']}" if x["fornecedor"] else ""
            L.append(f"   - [{x['dia_semana']}] {x['categoria']} · {x['descricao']}{forn} · "
                     f"{moeda(x['valor'])} · {x['status_pagamento']}{venc} · {anexo}")
    else:
        L.append("   Nenhum lancamento de caixa nesta semana.")
    L.append("")

    if d["fotos"]:
        L.append(f"10. REGISTRO FOTOGRAFICO: {len(d['fotos'])} foto(s) anexada(s) — ver versao em PDF.")
        L.append("")

    L.append(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} · Obra ERP")
    return "\n".join(L)


def despesas_csv(d: dict) -> str:
    """CSV das despesas — abre direto no Excel do escritório/contabilidade."""
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow([
        "Obra", "Semana", "Data", "Dia", "Categoria", "Etapa", "Descrição",
        "Fornecedor", "Valor", "Status", "Vencimento", "Pago em", "Anexo",
    ])
    for x in d["despesas"]:
        escritor.writerow([
            d["obra_nome"], d["semana_numero"], data_br(x["data"]), x["dia_semana"],
            x["categoria"], x["etapa_nome"] or "", x["descricao"], x["fornecedor"],
            f"{x['valor']:.2f}".replace(".", ","), x["status_pagamento"],
            data_br(x["vencimento"]), data_br(x["pago_em"]),
            x["nome_arquivo"] or "",
        ])
    escritor.writerow([])
    escritor.writerow(["Folha da semana", "", "", "", "", "", "", "",
                       f"{d['financeiro']['folha']:.2f}".replace(".", ",")])
    escritor.writerow(["Custo total da semana", "", "", "", "", "", "", "",
                       f"{d['financeiro']['custo_semana']:.2f}".replace(".", ",")])
    # BOM para o Excel reconhecer os acentos automaticamente
    return "\ufeff" + buffer.getvalue()
