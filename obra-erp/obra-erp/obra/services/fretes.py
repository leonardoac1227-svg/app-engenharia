# -*- coding: utf-8 -*-
"""Frete — várias viagens do mesmo transportador, cada uma no seu dia.

Frete de obra raramente é uma viagem só: o mesmo motoboy busca material na
segunda, na quarta e na sexta, e no fim da semana cobra o conjunto. Lançar isso
como uma despesa única de "3 fretes" esconde a informação que interessa — em
que dias houve movimento e quantas viagens foram feitas.

Por isso cada viagem vira um lançamento com **sua própria data**: o custo cai
no dia certo do diário, a semana correta recebe o valor, e o relatório mostra
"Frete 2/3" com a data da viagem. As viagens ficam ligadas por um grupo, o que
permite cancelar o conjunto de uma vez.

Diferente do parcelamento, aqui o valor informado é o **de cada viagem** — o
transportador cobra por corrida, não um total a ser dividido.
"""

from __future__ import annotations

import uuid

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, data_iso, numero, texto

CATEGORIA = "Frete"
MAXIMO_VIAGENS = 30


def _dia_para_data(obra_id: int, data_alvo: str) -> int | None:
    """Dia do diário correspondente a uma data (ou o mais próximo existente)."""
    exato = um(
        """SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.data = ?""", (obra_id, data_alvo))
    if exato:
        return exato["id"]

    anterior = um(
        """SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.data <= ? ORDER BY d.data DESC LIMIT 1""",
        (obra_id, data_alvo))
    if anterior:
        return anterior["id"]

    primeiro = um(
        """SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? ORDER BY d.data LIMIT 1""", (obra_id,))
    return primeiro["id"] if primeiro else None


def validar(dados) -> dict | None:
    """Lê os campos de frete. Devolve None quando não é um lançamento de frete."""
    transportador = texto(dados.get("frete_transportador"), 120)
    viagens_bruto = dados.get("frete_viagens")

    if not transportador and not viagens_bruto:
        return None

    if not transportador:
        raise ApiErro("Informe quem fez o frete.")

    viagens = int(numero(viagens_bruto, 1, 1, MAXIMO_VIAGENS, campo="a quantidade de fretes"))

    # As datas chegam como uma lista (uma por viagem). Faltando alguma, a
    # viagem herda a data da anterior — melhor do que recusar o lançamento
    # inteiro por um campo vazio.
    if hasattr(dados, "getlist"):
        datas_brutas = [d for d in dados.getlist("frete_datas") if d]
    else:
        datas_brutas = [d for d in (dados.get("frete_datas") or []) if d]

    if not datas_brutas:
        raise ApiErro("Informe a data de cada frete.")

    datas = []
    for indice in range(viagens):
        bruta = datas_brutas[indice] if indice < len(datas_brutas) else datas_brutas[-1]
        datas.append(data_iso(bruta, None, "a data do frete"))

    return {
        "transportador": transportador,
        "viagens": viagens,
        "datas": datas,
        "descricao": texto(dados.get("frete_descricao"), 200),
    }


def gerar_fretes(despesa_id: int, obra_id: int, config_frete: dict,
                 usuario_id: int | None = None) -> dict:
    """Converte a despesa lançada na 1ª viagem e cria as demais."""
    despesa = um("SELECT * FROM despesas WHERE id = ?", (despesa_id,))
    if not despesa:
        raise ApiErro("Despesa não encontrada.", 404)

    transportador = config_frete["transportador"]
    viagens = config_frete["viagens"]
    datas = config_frete["datas"]
    valor = float(despesa["valor"] or 0)
    grupo = uuid.uuid4().hex

    base = config_frete["descricao"] or despesa["descricao"] or "Frete"

    def rotulo(indice: int) -> str:
        return f"{base} (frete {indice + 1}/{viagens})" if viagens > 1 else base

    dia_inicial = _dia_para_data(obra_id, datas[0]) or despesa["dia_id"]
    executar(
        """UPDATE despesas SET dia_id = ?, descricao = ?, fornecedor = ?, categoria = ?,
                               grupo_parcela = ?, grupo_tipo = 'frete',
                               parcela_numero = 1, parcela_total = ?
            WHERE id = ?""",
        (dia_inicial, rotulo(0), transportador, CATEGORIA, grupo, viagens, despesa_id))

    criados = [{"id": despesa_id, "numero": 1, "data": datas[0], "valor": round(valor, 2)}]

    for indice in range(1, viagens):
        dia_id = _dia_para_data(obra_id, datas[indice]) or dia_inicial
        cur = executar(
            """INSERT INTO despesas (dia_id, etapa_id, categoria, descricao, fornecedor, valor,
                                     status_pagamento, numero_nota, grupo_parcela, grupo_tipo,
                                     parcela_numero, parcela_total, vencimento, criado_por, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'frete', ?, ?, ?, ?, ?)""",
            (dia_id, despesa["etapa_id"], CATEGORIA, rotulo(indice), transportador, valor,
             despesa["status_pagamento"], despesa["numero_nota"], grupo, indice + 1, viagens,
             despesa["vencimento"], usuario_id, agora_iso()))
        criados.append({"id": cur.lastrowid, "numero": indice + 1,
                        "data": datas[indice], "valor": round(valor, 2)})

    return {
        "grupo": grupo,
        "transportador": transportador,
        "viagens": viagens,
        "valor_unitario": round(valor, 2),
        "valor_total": round(valor * viagens, 2),
        "datas": datas,
        "lancamentos": criados,
    }


def listar_grupo(grupo: str) -> list[dict]:
    return [dict(linha) for linha in todos(
        """SELECT x.id, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                  x.parcela_numero, x.parcela_total, d.data, d.dia_semana
             FROM despesas x JOIN dias d ON d.id = x.dia_id
            WHERE x.grupo_parcela = ? ORDER BY x.parcela_numero""", (grupo,))]


def resumo_semana(semana_id: int) -> dict:
    """Fretes da semana agrupados por transportador — vira linha do relatório."""
    linhas = todos(
        """SELECT x.fornecedor, COUNT(*) AS viagens, SUM(x.valor) AS total
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
            WHERE d.semana_id = ? AND x.categoria = ?
            GROUP BY x.fornecedor
            ORDER BY SUM(x.valor) DESC""",
        (semana_id, CATEGORIA))

    transportadores = [{
        "transportador": linha["fornecedor"] or "não informado",
        "viagens": int(linha["viagens"] or 0),
        "total": round(float(linha["total"] or 0), 2),
    } for linha in linhas]

    return {
        "transportadores": transportadores,
        "viagens": sum(t["viagens"] for t in transportadores),
        "total": round(sum(t["total"] for t in transportadores), 2),
    }
