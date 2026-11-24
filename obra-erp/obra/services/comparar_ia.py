# -*- coding: utf-8 -*-
"""Comparação entre as IAs com notas reais já conferidas pelo gestor.

Cada IA recebe a mesma nota pelo mesmo caminho do bot (mesmas regras e mesmo
contexto) e o que ela leu é comparado com o que está lançado. Nada é gravado.

Só entram notas antigas: as recentes e as em aberto vão no contexto, e a IA
copiaria a resposta em vez de ler o documento.
"""

from __future__ import annotations

import logging
import os
import statistics
import time

from .. import config
from ..db import liberar_leitura, todos
from ..utils import bytes_anexo, hoje_iso
from . import acoes as catalogo
from . import assistente, ia_provedores

log = logging.getLogger("obra-erp")

NOTAS = 5
RECENTES_NO_CONTEXTO = 30     # o mesmo limite de "lancamentos_recentes"
MIMES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
         ".webp": "image/webp"}


def _notas(obras: list[dict], quantas: int) -> list[dict]:
    escolhidas = []
    for obra in obras:
        linhas = todos(
            """SELECT x.id, x.valor, x.fornecedor, x.status_pagamento, x.grupo_parcela,
                      (SELECT COUNT(*) FROM despesa_anexos c WHERE c.despesa_id = x.id) AS anexos,
                      (SELECT MIN(b.arquivo) FROM despesa_anexos b WHERE b.despesa_id = x.id) AS arquivo
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? ORDER BY x.id DESC""", (obra["id"],))
        for linha in linhas[RECENTES_NO_CONTEXTO:]:
            extensao = os.path.splitext(str(linha["arquivo"] or ""))[1].lower()
            if (linha["anexos"] == 1 and extensao in MIMES and not linha["grupo_parcela"]
                    and linha["status_pagamento"] != "Em Aberto"
                    and float(linha["valor"] or 0) > 1 and linha["fornecedor"]):
                escolhidas.append({"id": linha["id"], "obra": obra, "valor": float(linha["valor"]),
                                   "fornecedor": linha["fornecedor"], "arquivo": linha["arquivo"],
                                   "mime": MIMES[extensao]})
    escolhidas.sort(key=lambda n: -n["id"])
    return escolhidas[:quantas]


SEM_RACIOCINIO = "anthropic-sem-raciocinio"
NOMES = {**ia_provedores.NOMES, SEM_RACIOCINIO: "Claude sem raciocínio"}


def _ler(provedor: str, nota: dict, dados: bytes, obras: list[dict]) -> dict:
    arquivo = {"nome": "nota" + os.path.splitext(nota["arquivo"])[1], "mime": nota["mime"], "dados": dados}
    inicio = time.monotonic()
    try:
        with ia_provedores.somente(provedor.split("-")[0], provedor == SEM_RACIOCINIO):
            r = assistente.interpretar(obras, f"Lança essa nota na obra {nota['obra']['nome']}", hoje_iso(),
                                       [arquivo], [], pode_financeiro=True, pode_gestor=True,
                                       obra_padrao=nota["obra"]["id"], prazo=90)
    except Exception as erro:  # noqa: BLE001 — falha também é resultado
        log.warning("Comparação: %s na nota #%s: %s", provedor, nota["id"], erro)
        return {"falhou": True}
    finally:
        liberar_leitura()
    segundos = time.monotonic() - inicio
    despesa = next((a["dados"] for a in r["acoes"] if a.get("tipo") == "despesa"), {})
    return {"falhou": False, "segundos": segundos,
            "valor": abs(float(despesa.get("valor") or 0) - nota["valor"]) < 0.01,
            "fornecedor": catalogo.sem_acento(str(despesa.get("fornecedor") or ""))
            == catalogo.sem_acento(nota["fornecedor"])}


def comparar(obras: list[dict], quantas: int = NOTAS) -> dict:
    """{"notas": n, "resultados": {provedor: [leituras]}}. Só lê; não lança nada."""
    provedores = ia_provedores.provedores_configurados()
    if "anthropic" in provedores and config.ANTHROPIC_MODEL == "claude-haiku-5-5":
        provedores = provedores + [SEM_RACIOCINIO]
    lidas, resultados = 0, {p: [] for p in provedores}
    for nota in _notas(obras, quantas):
        dados = bytes_anexo(nota["arquivo"])
        if not dados:
            continue
        lidas += 1
        for provedor in provedores:
            resultados[provedor].append(_ler(provedor, nota, dados, obras))
    return {"notas": lidas, "resultados": resultados}


def resumo(dados: dict) -> str:
    if not dados["notas"]:
        return "Não achei notas antigas com um arquivo só para comparar."
    linhas = [f"Comparação com {dados['notas']} nota(s) já lançada(s). Nada foi gravado."]
    for provedor, leituras in dados["resultados"].items():
        boas = [x for x in leituras if not x["falhou"]]
        nome = NOMES[provedor]
        if not boas:
            linhas.append(f"\n❌ {nome}: não respondeu nenhuma.")
            continue
        tempos = [x["segundos"] for x in boas]
        linhas.append(
            f"\n{nome}\n⏱ {statistics.median(tempos):.1f}s (mediana) · {max(tempos):.1f}s (pior)"
            f"\n💰 valor certo: {sum(x['valor'] for x in boas)}/{len(leituras)}"
            f"\n🏪 fornecedor certo: {sum(x['fornecedor'] for x in boas)}/{len(leituras)}"
            + (f"\n⚠️ sem resposta: {len(leituras) - len(boas)}" if len(boas) < len(leituras) else ""))
    return "\n".join(linhas)
