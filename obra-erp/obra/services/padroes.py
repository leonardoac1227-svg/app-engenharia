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
    (r"locmaq\b.*", "LOCMAQ"),
    (r"locknal\b.*", "Locknal"),
    (r"renove\b.*", "Renove"),
    (r"posto sossego\b.*", "Posto Sossego"),
    (r"feira epi\b.*", "Feira EPI"),
    (r"alta tensao\b.*", "Alta tensão"),
    (r"petrotintas\b.*", "Petrotintas"),
    (r"feira tintas\b.*", "Feira Tintas"),
    (r"marcon\b.*", "Marcon"),
)

_NUMERO_NA_DESCRICAO = re.compile(
    r"[\s\-–·,(]*\b(recibo|nota fiscal|nota|nf-?e?|or[cç]amento|orc)\b\s*(?:n[º°o.]*\s*)?(\d{2,})\)?", re.I)
_PREFIXO_NUMERO = {"rec": "REC", "orc": "ORC", "orç": "ORC"}

# Status quando nem o gestor nem o documento disseram outra coisa (histórico real).
LOJAS_REEMBOLSO = ("O engenheiro", "Renova", "Renove", "Uniformizar", "Bahia copiadora", "Feira EPI",
                   "Posto Sossego", "Petrotintas", "Feira Tintas")

_SUFIXO_EMPRESA = re.compile(r"\s*(?:\s-\s.*|\b(?:ltda|me|epp|eireli|s/?a)\.?)\s*$", re.I)


def fornecedor_padrao(nome) -> str:
    alvo = _chave(nome)
    for grafia, oficial in FORNECEDORES:
        if re.fullmatch(grafia, alvo):
            return oficial
    # Fornecedor novo: tira o que a nota imprime além do nome ("LTDA", " - Feira de Santana").
    limpo = texto(nome, 120)
    while True:
        curto = _SUFIXO_EMPRESA.sub("", limpo).strip(" -,.")
        if curto == limpo or not curto:
            return limpo
        limpo = curto


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


# Falas e o lançamento que a empresa faria, tirados do histórico. Servem de exemplo à IA,
# que generaliza o raciocínio para falas parecidas.
EXEMPLOS = (
    ('"Lança no I19 essa nota da Inova, espaçadores pro porcelanato da piscina" + foto da nota',
     'despesa: descricao "Espaçadores para assentamento de porcelanato da piscina", fornecedor "Inova civil", '
     'valor = total da nota, numero_nota = nº da NF, Em Aberto, Material, anexos [0]'),
    ('"e o frete disso" (logo depois da nota acima)',
     'despesa: "Frete referente a espaçadores para assentamento de porcelanato da piscina", Diego motoboy, '
     '30, Em Aberto, Frete'),
    ('"Comprei broca de 10 no engenheiro pra furar a laje do O13, reembolso" + foto do cupom',
     'despesa: "Broca 10mm para furos na laje", "O engenheiro", valor do cupom, Reembolso, Material'),
    ('"Lance isso na obra 19, rolo de textura pro chapisco, nota em reembolso" + cupom',
     'despesa na obra I19: "Rolo de textura para chapisco em altura", valor do cupom, Reembolso'),
    ('"Digitalização dos projetos, 69 reais, I19, reembolso pro Luiz"',
     'despesa: "Digitalização de projetos", 69, Reembolso, reembolsar_para "Luiz", Material'),
    ('"Diesel pra desmoldar a forma, posto sossego, 11,46, O13"',
     'despesa: "Diesel para desmoldagem da forma metálica", "Posto Sossego", 11.46, Reembolso'),
    ('"Três caixas de entulho no O13"',
     'três ações despesa, cada uma "Caixa de entulho", SOS Entulho, 1, Em Aberto'),
    ('"Mandei 8 sacos de cimento do I19 pro T21"',
     'despesa na obra T21: "8 sacos de cimento vindo do I19", sem fornecedor, 1, Em Aberto'),
    ('"Nota da Central em 3 vezes, materiais hidráulicos da piscina" + nota sem quadro de parcelas',
     'despesa: "Materiais hidráulicos para piscina", "Central da construção", valor = TOTAL da nota, '
     'parcelas 3, parcela_frequencia mensal, Em Aberto'),
    ('"Nota da Marcon parcelada, registros e tubos pra hidráulica do I19" + NF com quadro PARCELAS '
     '(001 21/11/2026 R$ 150,00; 002 21/12/2026 R$ 139,00)',
     'despesa: "Registros e tubos para hidráulica", fornecedor "Marcon", numero_nota "343245", valor 289.00, '
     'Em Aberto, parcelas_lista [{vencimento 2026-11-21, valor 150.00}, {vencimento 2026-12-21, valor 139.00}], '
     'anexos [0]'),
    ('"Nota a prazo da Marcon" + NF com uma parcela só (001 21/11/2026 R$ 289,00)',
     'despesa: valor 289.00, Em Aberto, parcelas_lista [{vencimento 2026-11-21, valor 289.00}]'),
    ('"Conta da internet de outubro, 79,90, em aberto" + boleto',
     'despesa: "Conta da internet referente ao mês de outubro", fornecedor do boleto, 79.90, Em Aberto, '
     'categoria Serviço, vencimento do boleto'),
    ('"Passa a última nota pra reembolso"',
     'editar_despesa: despesa_id = o primeiro de lancamentos_recentes, alteracoes {status_pagamento: Reembolso}'),
    ('"Paguei a nota da Somar do aço"',
     'pagar_despesa: despesa_id da conta em aberto da Somar com aço (contas_em_aberto)'),
)


def exemplos_para_prompt(pode_financeiro: bool) -> str:
    if not pode_financeiro:
        return "(não se aplica)"
    return "\n".join(f"- {fala}\n  → {acao}" for fala, acao in EXEMPLOS)


def texto_para_prompt() -> str:
    linhas = [f"- {p.regra}" for p in PADROES] + [f"- {r}" for r in REGRAS_LIVRES]
    linhas.append("- Nomes de fornecedor: " + ", ".join(dict.fromkeys(n for _, n in FORNECEDORES)) + ".")
    linhas.append("- Lojas de reembolso, salvo se o usuário disser outra coisa: "
                  + ", ".join(LOJAS_REEMBOLSO) + ". Inova civil, Somar e Central da construção ficam Em Aberto.")
    linhas.append("- Mais de um frete do mesmo material: um lançamento por frete, \"(frete 1/2)\", \"(frete 2/2)\".")
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
