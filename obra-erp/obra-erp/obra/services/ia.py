# -*- coding: utf-8 -*-
"""Resumo executivo da semana.

Duas camadas, nesta ordem:

1. **Automático (sempre disponível)** — texto montado por regras a partir dos
   próprios lançamentos. Funciona offline, sem chave, sem custo.
2. **IA (opcional)** — se `ANTHROPIC_API_KEY` estiver no ambiente, o mesmo
   conjunto de dados vira um parágrafo redigido em linguagem de relatório.
   Qualquer falha (sem internet no canteiro, chave inválida, timeout) cai
   silenciosamente para o resumo automático — o relatório nunca quebra.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .. import config
from ..utils import numero_br, data_br, moeda

PROMPT_SISTEMA = (
    "Você é engenheiro civil sênior redigindo o resumo executivo semanal de uma obra "
    "para o cliente contratante. Escreva 2 parágrafos curtos (máximo 120 palavras no total), "
    "em português do Brasil, tom técnico e direto, sem saudações, sem títulos, sem bullets e "
    "sem inventar informação que não esteja nos dados. Primeiro parágrafo: equipe, registro "
    "fotográfico e ocorrências. Segundo parágrafo: situação financeira e pontos de atenção."
)


def _fatos(d: dict) -> str:
    fin, ind = d["financeiro"], d["indicadores"]
    linhas = [
        f"Obra: {d['obra_nome']}",
        f"Semana {d['semana_numero']} ({data_br(d['data_inicio'])} a {data_br(d['data_fim'])})",
        f"Fotos anexadas: {ind['total_fotos']}",
        f"Horas totais: {numero_br(ind['total_horas'], 1)}; efetivo médio: {numero_br(ind['efetivo_medio'], 1)}",
        f"Despesas: {moeda(fin['despesas'])}; custo total: {moeda(fin['custo_semana'])}",
    ]
    if fin["folha"]:
        linhas.append(f"Folha (lançamentos antigos): {moeda(fin['folha'])}")
    if fin["orcamento_previsto"]:
        linhas.append(
            f"Orçamento previsto: {moeda(fin['orcamento_previsto'])}; "
            f"consumo: {numero_br(fin['percentual_orcamento'], 1)}%; saldo: {moeda(fin['saldo'])}")
    if fin["em_aberto"]:
        linhas.append(f"Contas em aberto: {moeda(fin['em_aberto'])}")
    if d["dias_pendentes"]:
        linhas.append("Dias úteis sem lançamento: " + ", ".join(d["dias_pendentes"]))
    return "\n".join(linhas)


def resumo_automatico(d: dict) -> str:
    """Resumo por regras — o que qualquer gestor escreveria olhando os números."""
    fin, ind = d["financeiro"], d["indicadores"]
    partes = []

    if ind["total_horas"]:
        partes.append(
            f"Foram lançadas {numero_br(ind['total_horas'], 1)} horas de mão de obra em "
            f"{ind['dias_trabalhados']} dia(s), com efetivo médio de {numero_br(ind['efetivo_medio'], 1)} profissionais.")

    if ind["total_fotos"]:
        partes.append(f"{ind['total_fotos']} foto(s) do andamento foram anexadas ao diário.")

    financeiro = f"O custo total da semana foi de {moeda(fin['custo_semana'])}"
    if fin["folha"]:
        financeiro += (f", sendo {moeda(fin['folha'])} de folha e {moeda(fin['despesas'])} "
                       "de materiais e serviços.")
    else:
        financeiro += "."
    if fin["orcamento_previsto"]:
        situacao = "dentro do previsto" if fin["percentual_orcamento"] <= 100 else "acima do previsto"
        financeiro += (f" Isso representa {numero_br(fin['percentual_orcamento'], 1)}% do orçamento da semana "
                       f"({moeda(fin['orcamento_previsto'])}), {situacao}.")
    if fin["em_aberto"]:
        financeiro += f" Restam {moeda(fin['em_aberto'])} em contas a pagar."
    partes.append(financeiro)

    alertas = []
    if d["dias_pendentes"]:
        alertas.append("dias úteis sem lançamento (" + ", ".join(d["dias_pendentes"]) + ")")
    if fin["percentual_orcamento"] and fin["percentual_orcamento"] > 100:
        alertas.append("estouro do orçamento semanal")
    if not d["fotos"]:
        alertas.append("nenhuma foto anexada ao diário")
    if alertas:
        partes.append("Pontos de atenção: " + "; ".join(alertas) + ".")

    return " ".join(partes)


def _chamar_anthropic(fatos: str) -> str:
    corpo = json.dumps({
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": 400,
        "system": PROMPT_SISTEMA,
        "messages": [{"role": "user", "content": f"Dados da semana:\n{fatos}"}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=corpo,
        headers={
            "content-type": "application/json",
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resposta:
        dados = json.loads(resposta.read().decode("utf-8"))
    blocos = [b.get("text", "") for b in dados.get("content", []) if b.get("type") == "text"]
    return "\n".join(t for t in blocos if t).strip()


def resumo_semanal(d: dict, usar_ia: bool = True) -> dict:
    if usar_ia and config.ANTHROPIC_API_KEY:
        try:
            texto = _chamar_anthropic(_fatos(d))
            if texto:
                return {"texto": texto, "fonte": "ia"}
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, KeyError):
            pass  # sem internet ou chave inválida: segue com o automático
    return {"texto": resumo_automatico(d), "fonte": "automatico"}
