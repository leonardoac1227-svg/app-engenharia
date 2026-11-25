# -*- coding: utf-8 -*-
"""Nomes de obra que o áudio e o teclado erram: "san remi", "são rémi", "soneto".

O texto é comparado sem acento. Só entram escritas que não são palavras comuns
na fala de obra; as curtas valem apenas depois de "obra".
"""

from __future__ import annotations

import re
import unicodedata

_OBRA = r"obras?\s+(?:(?:da|do|de|a|o|na|no)\s+)?"

# Saint Remy: "Saint" vira san/sant/são/sem/cem...; "Remy" vira remi/reme/hemi/rami/remo...
_SAINT = (r"(?:saint|sainte|sain|seint|sein|sent|sant|sante|santo|san|sam|sao|sa|sem|sen|cem|cen|sin|sim|som|son"
          r"|sun|zan)")
_REMY = r"(?:r+|h|rh)(?:e|i|a)m+(?:y|i|ie|ir|e|ez|im|in|is|it|ys|o)"
_SAINT_REMY = (rf"{_SAINT}[\s.\-']*{_REMY}"
               rf"|(?:saint|sant|san|sao)[\s.\-']*(?:rene|reni|renir)"
               rf"|{_OBRA}(?:remy|remi|hemi|remmy|saint)")

# "Sonnet", "soneto": o ditado também escreve Saint Remy assim.
_SONNET = (r"son+et+(?:e|i|y|is|es|s|h|o|a)?|sonata|sonate|sun+et+(?:e|i)?|zon+et+(?:e|i)?"
           rf"|{_OBRA}(?:so|som|son|sol|sou)[\s\-]+(?:net|nete|neti|neto)")

PRONUNCIAS = {"Saint Remy": rf"{_SAINT_REMY}|{_SONNET}"}
_DICAS = {"saintremy": 'Saint Remy (fala-se "san remi", "são remi", "sant remí")'}


def _sem_acento(texto: str) -> str:
    """Sem acento e minúsculo, letra por letra: as posições continuam as do texto original."""
    return "".join((unicodedata.normalize("NFD", c)[0].lower() or c)[0] for c in texto)


def _chave(nome: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _sem_acento(nome or ""))


def nome_certo(nome: str) -> str:
    """Grafia oficial de um nome dito sozinho ("san remi" → "Saint Remy"); outro nome volta igual."""
    limpo = _sem_acento(unicodedata.normalize("NFC", nome or "")).strip()
    return next((oficial for oficial, padrao in PRONUNCIAS.items() if re.fullmatch(padrao, limpo)), nome)


def corrigir(texto: str, nomes_das_obras: list[str]) -> str:
    """Troca a escrita errada pelo nome da obra, se ela existe. O resto do texto fica como veio."""
    texto = unicodedata.normalize("NFC", texto or "")
    existentes = {_chave(n): n for n in nomes_das_obras}
    for oficial, padrao in PRONUNCIAS.items():
        destino = existentes.get(_chave(oficial))
        if not destino:
            continue
        achados = list(re.finditer(rf"(?<!\w)(?:{padrao})(?!\w)", _sem_acento(texto)))
        for achado in reversed(achados):
            prefixo = re.match(_OBRA, achado.group(0))
            inicio = achado.start() + (prefixo.end() if prefixo else 0)
            texto = texto[:inicio] + destino + texto[achado.end():]
    return texto


def dica_de_audio(nomes_das_obras: list[str]) -> str:
    """Como se fala, para a transcrição já sair com a grafia certa."""
    return "; ".join(_DICAS[_chave(n)] for n in nomes_das_obras if _chave(n) in _DICAS)
