# -*- coding: utf-8 -*-
"""Padrões de lançamento da empresa — o que se repete no dia a dia do canteiro.

"Três metros de areia para o I19" já é um lançamento completo para quem conhece
a obra: fornecedor, valor e status são sempre os mesmos. Estas regras vieram do
histórico real de despesas e foram conferidas com o gestor.

Elas valem em dois lugares:

* `texto_para_prompt`: a IA lê as regras e já monta o lançamento certo;
* `completar`: o servidor preenche o que a IA deixou vazio. Assim, entulho,
  frete e cimento saem certos mesmo que o modelo esqueça um campo.

`completar` nunca troca o que veio preenchido: valor falado ou lido de uma nota
vale mais que o padrão. Para ensinar um padrão novo, acrescente uma entrada em
PADROES. O que se repete sem regra chega à IA por `repetidos`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from ..db import todos
from ..utils import texto

JANELA_DIAS = 120
MAX_REPETIDOS = 25


def _chave(valor) -> str:
    bruto = unicodedata.normalize("NFD", str(valor or ""))
    return "".join(c for c in bruto if unicodedata.category(c) != "Mn").lower().strip()


@dataclass(frozen=True)
class Padrao:
    gatilho: str                  # regex sobre descrição + fornecedor, sem acento
    regra: str                    # como a regra é explicada à IA
    fornecedor: str = ""
    valor: float = 0
    status: str = "Em Aberto"
    categoria: str = "Material"
    descricao: str = ""           # usada só quando vier sem descrição
    obras: tuple[str, ...] = ()   # vazio = qualquer obra


PADROES = (
    Padrao(r"\bentulho\b",
           "Caixa ou caçamba de entulho, em qualquer obra: fornecedor SOS Entulho, valor R$ 1 por caixa, "
           "Em Aberto, Material, descrição \"Caixa de entulho\". Duas caixas são dois lançamentos.",
           fornecedor="SOS Entulho", valor=1, descricao="Caixa de entulho"),
    Padrao(r"\bfretes?\b",
           "Frete: sempre Diego motoboy, R$ 30, Em Aberto, categoria Frete, descrição \"Frete referente a "
           "[material]\". Dois fretes do mesmo material são dois lançamentos: \"(frete 1/2)\" e \"(frete 2/2)\".",
           fornecedor="Diego motoboy", valor=30, categoria="Frete"),
    Padrao(r"\bcimento\b.*(\bvind[oa]s?\b|\bveio\b|\btransfer|\bemprestad|\bdo [a-z]-?\d{2}\b)",
           "Cimento que veio de outra obra (\"6 sacos de cimento vindo do O13\"): sem fornecedor, R$ 1, "
           "Em Aberto, Material.",
           valor=1),
    Padrao(r"^(50 sacos de )?cimento\b|\b50 sacos de cimento\b",
           "Cimento comprado: é sempre \"50 sacos de cimento\", sem fornecedor, R$ 2.200, Em Aberto, Material, "
           "em qualquer obra.",
           valor=2200, descricao="50 sacos de cimento"),
    Padrao(r"\bareia\b",
           "Areia no I19 (reboco ou branca): sempre Hitalo, R$ 1, Em Aberto, Material. O gestor fala \"Ítalo\". "
           "\"3 metros de areia\" = \"3m3 de areia para reboco\"; se for branca, \"3m3 de areia branca\".",
           fornecedor="Hitalo", valor=1, descricao="3m3 de areia para reboco", obras=("i19",)),
    Padrao(r"\bareia\b|\bbrita\b|\bagregados?\b",
           "Areia e agregados no O13 e no T21: sempre Somar, com o valor da nota que o gestor manda. "
           "Sem nota, deixe o valor 0: o bot pergunta.",
           fornecedor="Somar", obras=("o13", "t21")),
)

REGRAS_LIVRES = (
    "Escoras: o gestor manda o contrato e o boleto, como dois arquivos do mesmo lançamento. Gere UMA despesa "
    "categoria Locação com os dois em \"anexos\": fornecedor, quantidade e período vêm do contrato; o valor "
    "vem do boleto.",
)

# Grafias que aparecem no histórico ou no áudio → nome usado nos lançamentos.
FORNECEDORES = (
    (r"h?[iy]tall?o|italy", "Hitalo"),
    (r"inova\b.*|inovacivil.*", "Inova civil"),
    (r"diego\b.*", "Diego motoboy"),
    (r"sos\b.*", "SOS Entulho"),
    (r"bahia copiadora", "Bahia copiadora"),
    (r"casa do construtor\b.*", "Casa do Construtor"),
    (r"somar\b.*", "Somar"),
    (r"central da construcao", "Central da construção"),
    (r"o engenheiro", "O engenheiro"),
    (r"renova", "Renova"),
    (r"uniformizar", "Uniformizar"),
    (r"locmaq", "LOCMAQ"),
    (r"locknal", "Locknal"),
)

_NUMERO_NA_DESCRICAO = re.compile(
    r"[\s\-–·,(]*\b(recibo|nota fiscal|nota|nf-?e?|or[cç]amento|orc)\b\s*(?:n[º°o.]*\s*)?(\d{2,})\)?", re.I)
_PREFIXO_NUMERO = {"rec": "REC", "orc": "ORC", "orç": "ORC"}

# Status quando nem o gestor nem o documento disseram outra coisa.
LOJAS_REEMBOLSO = ("O engenheiro", "Renova", "Uniformizar", "Bahia copiadora")


def fornecedor_padrao(nome) -> str:
    alvo = _chave(nome)
    for grafia, oficial in FORNECEDORES:
        if re.fullmatch(grafia, alvo):
            return oficial
    return texto(nome, 120)


def _separar_numero(saida: dict) -> None:
    """Número do documento escrito na descrição vai para o campo próprio.

    A trava de nota repetida só olha `numero_nota`: "Bomba - Recibo 1191" na
    descrição deixava a mesma nota entrar duas vezes.
    """
    achado = _NUMERO_NA_DESCRICAO.search(texto(saida.get("descricao"), 200))
    if not achado or texto(saida.get("numero_nota"), 40):
        return
    prefixo = _PREFIXO_NUMERO.get(_chave(achado.group(1))[:3], "")
    saida["numero_nota"] = f"{prefixo} {achado.group(2)}".strip()
    saida["descricao"] = texto(saida["descricao"], 200).replace(achado.group(0), "").strip(" -–·,")


def _vazio(valor) -> bool:
    try:
        return float(valor or 0) == 0
    except (TypeError, ValueError):
        return True


def _padrao_de(alvo: str, fornecedor: str, obra: str) -> Padrao | None:
    for padrao in PADROES:
        if padrao.obras and not any(o in obra for o in padrao.obras):
            continue
        # Nota de outra loja não é o padrão: "areia" comprada de quem nunca fornece areia.
        if fornecedor and padrao.fornecedor and fornecedor != padrao.fornecedor:
            continue
        if re.search(padrao.gatilho, alvo):
            return padrao
    return None


def completar(dados: dict, nome_obra: str) -> dict:
    """Devolve os dados da despesa com o que faltava preenchido pelo padrão."""
    saida = dict(dados)
    _separar_numero(saida)
    fornecedor = fornecedor_padrao(saida.get("fornecedor"))
    alvo = _chave(saida.get("descricao"))
    padrao = _padrao_de(" ".join(p for p in (alvo, _chave(fornecedor)) if p), fornecedor, _chave(nome_obra))

    if padrao:
        fornecedor = fornecedor or padrao.fornecedor
        if _vazio(saida.get("valor")) and padrao.valor:
            saida["valor"] = padrao.valor
        if not texto(saida.get("descricao"), 200) and padrao.descricao:
            saida["descricao"] = padrao.descricao
        if not saida.get("status_pagamento"):
            saida["status_pagamento"] = padrao.status
        if padrao.categoria != "Material" or not saida.get("categoria"):
            saida["categoria"] = padrao.categoria
    elif not saida.get("status_pagamento") and fornecedor in LOJAS_REEMBOLSO:
        saida["status_pagamento"] = "Reembolso"

    saida["fornecedor"] = fornecedor
    return saida


def texto_para_prompt() -> str:
    linhas = [f"- {p.regra}" for p in PADROES] + [f"- {r}" for r in REGRAS_LIVRES]
    linhas.append("- Nomes de fornecedor: " + ", ".join(dict.fromkeys(n for _, n in FORNECEDORES)) + ".")
    linhas.append("- Lojas de reembolso, salvo se o usuário disser outra coisa: "
                  + ", ".join(LOJAS_REEMBOLSO) + ". Inova civil e Central da construção ficam Em Aberto.")
    return "\n".join(linhas)


def repetidos(obra_ids: list[int], referencia: str) -> list[dict]:
    """O que mais se repetiu nos últimos meses, para valer também o que ainda não virou regra."""
    if not obra_ids:
        return []
    inicio = (date.fromisoformat(referencia) - timedelta(days=JANELA_DIAS)).isoformat()
    grupos: dict[tuple, dict] = {}
    for linha in todos(
        f"""SELECT x.fornecedor, x.categoria, x.status_pagamento, x.valor, x.descricao
              FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
             WHERE s.obra_id IN ({', '.join('?' for _ in obra_ids)}) AND d.data >= ?
             ORDER BY d.data DESC""", [*obra_ids, inicio]):
        fornecedor = fornecedor_padrao(linha["fornecedor"])
        chave = (fornecedor, linha["categoria"], float(linha["valor"] or 0))
        grupo = grupos.setdefault(chave, {
            "fornecedor": fornecedor, "categoria": linha["categoria"], "valor": float(linha["valor"] or 0),
            "status": linha["status_pagamento"], "exemplo": texto(linha["descricao"], 80), "vezes": 0})
        grupo["vezes"] += 1
    frequentes = [g for g in grupos.values() if g["vezes"] >= 2]
    return sorted(frequentes, key=lambda g: -g["vezes"])[:MAX_REPETIDOS]
