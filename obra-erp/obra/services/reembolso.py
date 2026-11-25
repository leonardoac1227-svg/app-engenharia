# -*- coding: utf-8 -*-
"""Para quem vai o reembolso quando ninguém diz o nome: quem mandou a mensagem.

"Reembolso" na conta do Caio é do Caio; na do gestor principal, do padrão da
empresa (`REEMBOLSO_PADRAO`, Léo). Nome dito vale sempre: "reembolso Léo".
"""

from __future__ import annotations

import logging
import re

from .. import config
from ..db import get_db, todos, um

log = logging.getLogger("obra-erp")

# Destino que não é pessoa: a empresa. "Reembolso projeto" é como o áudio de "Progetto" costuma sair.
DESTINOS = ("Progetto",)
APELIDOS = {"projeto": "Progetto"}


def padrao_de(usuario_id: int | None) -> str:
    """O nome do reembolso desta conta. Conta sem nome próprio usa o padrão da empresa."""
    if not usuario_id:
        return config.REEMBOLSO_PADRAO
    try:
        linha = um("SELECT reembolso FROM usuarios WHERE id = ?", (usuario_id,))
    except Exception as erro:  # noqa: BLE001 — coluna que não pôde ser criada não derruba o lançamento
        log.warning("Reembolso do usuário %s: %s", usuario_id, erro)
        get_db().rollback()
        return config.REEMBOLSO_PADRAO
    return (linha["reembolso"] if linha else "") or config.REEMBOLSO_PADRAO


def conhecidos() -> list[str]:
    """Quem pode ser dito logo depois de "reembolso", sem "para": contas, padrão e nomes já usados."""
    nomes = [config.REEMBOLSO_PADRAO, *DESTINOS]
    try:
        nomes += [str(r["nome"]).split(" ")[0] for r in todos("SELECT nome FROM usuarios WHERE ativo = 1")]
        nomes += [r["n"] for r in todos("SELECT DISTINCT reembolsar_para AS n FROM despesas "
                                        "WHERE reembolsar_para <> ''")]
    except Exception as erro:  # noqa: BLE001
        log.warning("Nomes de reembolso: %s", erro)
        get_db().rollback()
    return list(dict.fromkeys(n for n in nomes if n))


def _chave(texto: str) -> str:
    from .acoes import sem_acento
    return sem_acento(texto)


def nome_oficial(nome: str, nomes: list[str]) -> str:
    """"leo" → "Léo": a grafia de quem já existe."""
    apelido = APELIDOS.get(_chave(nome))
    return apelido or next((n for n in nomes if _chave(n) == _chave(nome)), nome)


def dito_junto(fala: str, nomes: list[str]) -> str:
    """"reembolso Léo", "reembolso: projeto". Só nome conhecido, para "reembolso cimento" não virar pessoa."""
    achado = re.search(r"reembols\w*\s*[:,\-]?\s+(?:o\s+|a\s+)?([a-z]{3,})(?:\s+([a-z0-9]+))?", _chave(fala))
    if not achado:
        return ""
    nome, depois = achado.group(1), achado.group(2) or ""
    oficial = APELIDOS.get(nome) or next((n for n in nomes if _chave(n) == nome), "")
    if oficial in DESTINOS and depois and depois not in ("na", "no", "obra", "e"):
        return ""   # "reembolso, projeto elétrico": é o material, não para quem vai
    return oficial
