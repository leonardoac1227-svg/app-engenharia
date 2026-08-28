# -*- coding: utf-8 -*-
"""Nota parcelada — uma compra, vários vencimentos.

O fornecedor entrega R$ 5.000 em material e a nota é paga em 5x. No caixa,
isso não é um lançamento de R$ 5.000: são **cinco contas a pagar** de R$ 1.000,
cada uma com sua data. Só assim o fluxo de caixa da obra fica correto e cada
vencimento aparece no alerta na semana certa.

Por isso as parcelas nascem todas de uma vez, como despesas de verdade — e não
como um "plano" a ser confirmado depois. A diferença para a locação de
equipamento é proposital: aluguel pode ser interrompido (devolveu o andaime,
para de pagar), então lá as renovações só viram conta quando o gestor confirma.
Nota parcelada já é dívida assumida na assinatura; o valor total não muda
porque o material já foi entregue.

As parcelas ficam ligadas por um `grupo_parcela`, o que permite mostrar "2/5"
na lista e apagar o parcelamento inteiro de uma vez.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, data_iso, numero, texto

FREQUENCIAS = {
    "semanal": ("Semanal", 7),
    "quinzenal": ("Quinzenal", 15),
    "mensal": ("Mensal", 30),
    "dias": ("Intervalo em dias", None),
}

MAXIMO_PARCELAS = 36
MINIMO_PARCELAS = 2


def _proxima_data(inicio: date, frequencia: str, indice: int, intervalo_dias: int) -> date:
    """Data da parcela `indice` (0 = primeira).

    O mensal anda de mês em mês preservando o dia — quem comprou dia 10 paga
    todo dia 10 —, com fevereiro e meses curtos caindo no último dia válido.
    """
    if frequencia != "mensal":
        return inicio + timedelta(days=intervalo_dias * indice)

    mes = inicio.month - 1 + indice
    ano = inicio.year + mes // 12
    mes = mes % 12 + 1
    bissexto = ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0)
    ultimo_dia = [31, 29 if bissexto else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1]
    return date(ano, mes, min(inicio.day, ultimo_dia))


def dividir_valor(total: float, parcelas: int) -> list[float]:
    """Divide o total em centavos exatos.

    A sobra da divisão vai para a **última** parcela: 100,00 em 3x sai
    33,33 / 33,33 / 33,34. Somar de volta tem que dar exatamente o total —
    centavo perdido em parcelamento é diferença que ninguém acha depois.
    """
    centavos = round(float(total) * 100)
    base = centavos // parcelas
    valores = [base] * parcelas
    valores[-1] += centavos - base * parcelas
    return [v / 100 for v in valores]


def _dia_para_data(obra_id: int, data_alvo: str) -> int | None:
    """Dia do diário mais próximo de uma data (a semana pode não existir ainda)."""
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


def validar(dados: dict) -> tuple[int, str, int] | None:
    """Lê os campos de parcelamento. Devolve None quando não há parcelamento."""
    bruto = dados.get("parcelas")
    if not bruto or str(bruto).strip() in ("", "0", "1"):
        return None

    parcelas = int(numero(bruto, 0, 0, MAXIMO_PARCELAS, campo="o número de parcelas"))
    if parcelas < MINIMO_PARCELAS:
        raise ApiErro(f"Parcelamento precisa de pelo menos {MINIMO_PARCELAS} parcelas.")

    frequencia = dados.get("parcela_frequencia") or "mensal"
    if frequencia not in FREQUENCIAS:
        raise ApiErro("Intervalo inválido. Use semanal, quinzenal, mensal ou dias.")

    if frequencia == "dias":
        intervalo = int(numero(dados.get("parcela_intervalo"), 30, 1, 365,
                               campo="o intervalo entre parcelas"))
    else:
        intervalo = FREQUENCIAS[frequencia][1]

    return parcelas, frequencia, intervalo


def gerar_parcelas(despesa_id: int, obra_id: int, config_parcelas: tuple[int, str, int],
                   usuario_id: int | None = None) -> dict:
    """Converte uma despesa recém-lançada na 1ª parcela e cria as demais.

    O valor informado é sempre o **total da nota**: ele é dividido, e a despesa
    original passa a valer a primeira parcela.
    """
    parcelas, frequencia, intervalo = config_parcelas

    despesa = um("SELECT * FROM despesas WHERE id = ?", (despesa_id,))
    if not despesa:
        raise ApiErro("Despesa não encontrada.", 404)

    total = float(despesa["valor"] or 0)
    valores = dividir_valor(total, parcelas)
    grupo = uuid.uuid4().hex

    primeiro_vencimento = despesa["vencimento"] or data_iso(None, None) or agora_iso()[:10]
    inicio = date.fromisoformat(str(primeiro_vencimento)[:10])
    descricao_base = despesa["descricao"] or ""

    executar(
        """UPDATE despesas SET valor = ?, descricao = ?, vencimento = ?,
                               grupo_parcela = ?, parcela_numero = 1, parcela_total = ?
            WHERE id = ?""",
        (valores[0], f"{descricao_base} (1/{parcelas})", inicio.isoformat(),
         grupo, parcelas, despesa_id))

    criadas = [{"id": despesa_id, "numero": 1, "valor": valores[0],
                "vencimento": inicio.isoformat()}]

    for indice in range(1, parcelas):
        vencimento = _proxima_data(inicio, frequencia, indice, intervalo)
        dia_id = _dia_para_data(obra_id, vencimento.isoformat()) or despesa["dia_id"]

        cur = executar(
            """INSERT INTO despesas (dia_id, etapa_id, categoria, descricao, fornecedor, valor,
                                     status_pagamento, numero_nota, grupo_parcela, parcela_numero,
                                     parcela_total, vencimento, imagem_path, nome_arquivo,
                                     criado_por, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, 'Em Aberto', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dia_id, despesa["etapa_id"], despesa["categoria"],
             f"{descricao_base} ({indice + 1}/{parcelas})", despesa["fornecedor"],
             valores[indice], despesa["numero_nota"], grupo, indice + 1, parcelas,
             vencimento.isoformat(), despesa["imagem_path"], despesa["nome_arquivo"],
             usuario_id, agora_iso()))

        criadas.append({"id": cur.lastrowid, "numero": indice + 1,
                        "valor": valores[indice], "vencimento": vencimento.isoformat()})

    return {
        "grupo": grupo,
        "parcelas": parcelas,
        "frequencia": frequencia,
        "valor_total": round(total, 2),
        "valor_parcela": valores[0],
        "primeiro_vencimento": inicio.isoformat(),
        "ultimo_vencimento": criadas[-1]["vencimento"],
        "lancamentos": criadas,
    }


def listar_grupo(grupo: str) -> list[dict]:
    return [dict(linha) for linha in todos(
        """SELECT x.id, x.descricao, x.valor, x.vencimento, x.status_pagamento,
                  x.parcela_numero, x.parcela_total, d.data
             FROM despesas x JOIN dias d ON d.id = x.dia_id
            WHERE x.grupo_parcela = ? ORDER BY x.parcela_numero""", (grupo,))]


def remover_grupo(grupo: str, manter_pagas: bool = True) -> int:
    """Cancela um parcelamento.

    Parcela já paga não é apagada por padrão: o dinheiro saiu, e apagar o
    lançamento faria o caixa mentir. Só as futuras somem.
    """
    condicao = " AND status_pagamento != 'Pago'" if manter_pagas else ""
    alvos = todos(f"SELECT id FROM despesas WHERE grupo_parcela = ?{condicao}", (grupo,))
    executar(f"DELETE FROM despesas WHERE grupo_parcela = ?{condicao}", (grupo,))
    return len(alvos)
