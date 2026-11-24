# -*- coding: utf-8 -*-
"""Boleto: vencimento e valor lidos da linha digitável.

A linha digitável traz o vencimento (4 dígitos: dias desde uma data base) e o
valor (10 dígitos, em centavos), com dígitos verificadores. No PDF do banco o
texto é lido direto, sem depender da IA; na foto, a IA copia a linha e os
dígitos verificadores dizem se ela copiou certo.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date, timedelta

log = logging.getLogger("obra-erp")

_LINHA = re.compile(r"(?<!\d)\d{5}\.?\d{5}\s*\d{5}\.?\d{6}\s*\d{5}\.?\d{6}\s*\d\s*\d{14}(?!\d)")
# Comprovante também imprime a linha, mas a conta já foi paga.
_COMPROVANTE = re.compile(r"comprovante|pagamento (efetuado|realizado)|data d[oe] pagamento", re.I)
# O fator de 4 dígitos acabou em 9999 (21/02/2025) e recomeçou em 1000 no dia seguinte.
_BASES = (date(1997, 10, 7), date(2025, 2, 22) - timedelta(days=1000))
MAXIMO_PAGINAS = 5


def _mod10(campo: str) -> int:
    soma = 0
    for i, digito in enumerate(reversed(campo)):
        n = int(digito) * (2 if i % 2 == 0 else 1)
        soma += n // 10 + n % 10
    return -soma % 10


def _mod11(codigo: str) -> int:
    soma = sum(int(d) * (2 + i % 8) for i, d in enumerate(reversed(codigo)))
    dv = 11 - soma % 11
    return 1 if dv in (0, 10, 11) else dv


def ler_linha(linha, hoje: str) -> dict | None:
    """{"linha", "vencimento", "valor"} ou None se a linha não for de boleto válido."""
    n = re.sub(r"[^0-9]", "", str(linha or ""))
    if len(n) != 47:
        return None
    campos = (n[:10], n[10:21], n[21:32])
    if any(_mod10(c[:-1]) != int(c[-1]) for c in campos):
        return None
    # O dígito geral é o que protege o vencimento e o valor.
    if _mod11(n[:4] + n[33:47] + n[4:9] + n[10:20] + n[21:31]) != int(n[32]):
        return None
    fator, valor = int(n[33:37]), int(n[37:]) / 100
    vencimento = ""
    if fator >= 1000:
        ref = date.fromisoformat(hoje[:10])
        vencimento = min((base + timedelta(days=fator) for base in _BASES),
                         key=lambda d: abs((d - ref).days)).isoformat()
    return {"linha": n, "vencimento": vencimento, "valor": valor}


def _texto_do_pdf(dados: bytes) -> str:
    from pypdf import PdfReader
    leitor = PdfReader(io.BytesIO(dados))
    return "\n".join((p.extract_text() or "") for p in leitor.pages[:MAXIMO_PAGINAS])


def do_arquivo(arquivo: dict, hoje: str) -> list[dict]:
    """Boletos impressos num PDF (um carnê traz vários)."""
    if arquivo.get("mime") != "application/pdf" or not arquivo.get("dados"):
        return []
    try:
        texto = _texto_do_pdf(arquivo["dados"])
    except Exception as erro:  # noqa: BLE001 — PDF protegido ou escaneado: fica com a leitura da IA
        log.info("Boleto: PDF sem texto legível (%s)", erro)
        return []
    if _COMPROVANTE.search(texto):
        return []
    achados = (ler_linha(m, hoje) for m in _LINHA.findall(texto))
    return list({b["linha"]: b for b in achados if b}.values())
