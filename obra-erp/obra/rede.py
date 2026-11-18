# -*- coding: utf-8 -*-
"""Resolução de endereços que não trava o servidor.

No Render, a consulta de endereço (DNS) às vezes fica presa — principalmente a
de IPv6, que o servidor nem usa. Enquanto isso, a chamada ao Telegram ou ao
Gemini para sem erro e sem log: o bot recebe a mensagem e nunca responde.

Aqui toda resolução:
  - pede só IPv4 (a consulta de IPv6 é a que costuma prender);
  - tem prazo de PRAZO segundos;
  - se travar ou falhar, usa o último endereço que funcionou para aquele nome.
"""

from __future__ import annotations

import logging
import socket
import threading

log = logging.getLogger("obra-erp")

PRAZO = 5.0
_original = socket.getaddrinfo
_ultimos: dict = {}
_instalado = False


def _com_prazo(host, port, family, type, proto, flags) -> dict:
    resultado: dict = {}

    def rodar():
        try:
            resultado["ok"] = _original(host, port, family, type, proto, flags)
        except Exception as erro:  # noqa: BLE001 — tratado por quem chama
            resultado["erro"] = erro

    linha = threading.Thread(target=rodar, daemon=True)
    linha.start()
    linha.join(PRAZO)
    return resultado


def _resolver(host, port, family=0, type=0, proto=0, flags=0):
    chave = (host, port, family, type, proto, flags)
    if family in (0, socket.AF_UNSPEC):
        # IPv4 primeiro; nome sem IPv4 (ex.: banco só IPv6) cai na consulta original.
        resultado = _com_prazo(host, port, socket.AF_INET, type, proto, flags)
        if isinstance(resultado.get("erro"), socket.gaierror):
            resultado = _com_prazo(host, port, family, type, proto, flags)
    else:
        resultado = _com_prazo(host, port, family, type, proto, flags)

    if "ok" in resultado:
        _ultimos[chave] = resultado["ok"]
        return resultado["ok"]
    if chave in _ultimos:
        log.warning("DNS: %s %s; usando o último endereço conhecido", host,
                    "falhou" if "erro" in resultado else f"travou por {PRAZO:.0f}s")
        return _ultimos[chave]
    if "erro" in resultado:
        raise resultado["erro"]
    log.warning("DNS: %s travou por %.0fs", host, PRAZO)
    raise socket.gaierror(socket.EAI_AGAIN, f"resolução de {host} travou por {PRAZO:.0f}s")


def instalar() -> None:
    """Vale para o processo inteiro: urllib (Telegram, Gemini, Supabase Storage)."""
    global _instalado
    if not _instalado:
        socket.getaddrinfo = _resolver
        _instalado = True
