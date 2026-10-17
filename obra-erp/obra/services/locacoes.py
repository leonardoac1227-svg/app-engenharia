# -*- coding: utf-8 -*-
"""Locação de equipamentos — aluguel recorrente com aviso de renovação.

Andaime, betoneira, container, bomba: são custos que se repetem em intervalo
fixo e que somem do radar justamente por serem repetitivos. O gestor lembra do
primeiro pagamento e esquece do quinto — até o locador ligar cobrando.

Como funciona: ao lançar uma despesa da categoria **Locação**, o gestor informa
a frequência (diária, semanal, quinzenal ou mensal) e por quantos ciclos. O app
monta o cronograma inteiro de renovações e passa a avisar quando cada uma
vence.

**Por que as parcelas não viram despesa sozinhas.** Seria fácil gerar as contas
automaticamente na data, e foi a primeira ideia. Mas equipamento se devolve
antes do fim do contrato o tempo todo, e conta lançada sozinha vira custo
fantasma no relatório que vai para o cliente — o pior tipo de erro, porque
ninguém desconfia de um número que apareceu direitinho. Então: o app cobra, o
gestor confirma em um toque, e quem devolveu o andaime encerra a locação e as
parcelas futuras somem.
"""

from __future__ import annotations

from datetime import date, timedelta

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, data_iso, hoje_iso, numero, texto

FREQUENCIAS = {
    "diaria": ("Diária", 1),
    "semanal": ("Semanal", 7),
    "quinzenal": ("Quinzenal", 15),
    "mensal": ("Mensal", 30),
}

MAXIMO_PARCELAS = 366   # trava contra "diária por 5 anos" digitado por engano
DIAS_DE_ALERTA = 7      # janela de "vence em breve" no painel

CATEGORIA = "Locação"


def _somar_ciclo(inicio: date, frequencia: str, ciclos: int) -> date:
    """Avança N ciclos a partir de uma data.

    O mensal anda de mês em mês (não de 30 em 30 dias), preservando o dia:
    quem alugou dia 5 paga todo dia 5. Fevereiro e meses de 31 dias caem no
    último dia válido.
    """
    if frequencia != "mensal":
        return inicio + timedelta(days=FREQUENCIAS[frequencia][1] * ciclos)

    mes = inicio.month - 1 + ciclos
    ano = inicio.year + mes // 12
    mes = mes % 12 + 1
    dia = min(inicio.day, [31, 29 if ano % 4 == 0 and (ano % 100 != 0 or ano % 400 == 0) else 28,
                           31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mes - 1])
    return date(ano, mes, dia)


def _rotulo_frequencia(chave: str) -> str:
    return FREQUENCIAS.get(chave, FREQUENCIAS["mensal"])[0]


# ---------------------------------------------------------------- criação

def criar_locacao(obra_id: int, dados: dict, despesa_id: int | None = None) -> int:
    """Cria a locação e o cronograma completo de renovações.

    Quando vem de uma despesa recém-lançada, a primeira parcela já nasce
    quitada e apontando para ela — o que o gestor acabou de pagar não pode
    virar cobrança de novo.
    """
    equipamento = texto(dados.get("equipamento"), 160)
    if not equipamento:
        raise ApiErro("Informe qual equipamento foi alugado.")

    frequencia = dados.get("frequencia")
    if frequencia not in FREQUENCIAS:
        raise ApiErro("Frequência inválida. Use diária, semanal, quinzenal ou mensal.")

    valor = numero(dados.get("valor_parcela"), 0, 0, campo="o valor da locação")
    if valor <= 0:
        raise ApiErro("A locação precisa de um valor por período maior que zero.")

    ciclos = int(numero(dados.get("ciclos"), 1, 1, MAXIMO_PARCELAS, campo="a duração"))
    inicio_texto = data_iso(dados.get("data_inicio"), hoje_iso(), "a data de início")
    inicio = date.fromisoformat(inicio_texto)
    fim = _somar_ciclo(inicio, frequencia, ciclos - 1)

    cur = executar(
        """INSERT INTO locacoes (obra_id, equipamento, fornecedor, etapa_id, valor_parcela,
                                 frequencia, data_inicio, ciclos, data_fim, status,
                                 observacoes, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ativa', ?, ?)""",
        (obra_id, equipamento, texto(dados.get("fornecedor"), 120),
         dados.get("etapa_id") or None, valor, frequencia, inicio_texto, ciclos,
         fim.isoformat(), texto(dados.get("observacoes"), 300), agora_iso()))
    locacao_id = cur.lastrowid

    for numero_parcela in range(1, ciclos + 1):
        vencimento = _somar_ciclo(inicio, frequencia, numero_parcela - 1)
        primeira = numero_parcela == 1 and despesa_id
        executar(
            """INSERT INTO locacao_parcelas (locacao_id, numero, vencimento, valor,
                                             despesa_id, status, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (locacao_id, numero_parcela, vencimento.isoformat(), valor,
             despesa_id if primeira else None,
             "lancada" if primeira else "prevista", agora_iso()))

    return locacao_id


# ---------------------------------------------------------------- leitura

def _situacao_parcela(parcela: dict, hoje: str) -> str:
    if parcela["status"] == "lancada":
        return "lancada"
    if parcela["status"] == "cancelada":
        return "cancelada"
    if parcela["vencimento"] < hoje:
        return "vencida"
    if parcela["vencimento"] <= (date.fromisoformat(hoje) + timedelta(days=DIAS_DE_ALERTA)).isoformat():
        return "proxima"
    return "futura"


def listar_locacoes(obra_id: int, incluir_encerradas: bool = True) -> list[dict]:
    filtro = "" if incluir_encerradas else " AND l.status = 'ativa'"
    hoje = hoje_iso()

    locacoes = [dict(linha) for linha in todos(
        f"""SELECT l.*, e.nome AS etapa_nome
              FROM locacoes l
              LEFT JOIN etapas e ON e.id = l.etapa_id
             WHERE l.obra_id = ?{filtro}
             ORDER BY l.status, l.data_inicio DESC, l.id DESC""", (obra_id,))]
    if not locacoes:
        return []

    # Todas as parcelas de todas as locações numa consulta só, em vez de uma
    # consulta por equipamento alugado.
    marcas = ", ".join("?" for _ in locacoes)
    por_locacao: dict[int, list[dict]] = {}
    for registro in todos(
        f"""SELECT * FROM locacao_parcelas
             WHERE locacao_id IN ({marcas})
             ORDER BY locacao_id, numero""",
        [l["id"] for l in locacoes],
    ):
        por_locacao.setdefault(registro["locacao_id"], []).append(dict(registro))

    saida = []
    for locacao in locacoes:
        parcelas = por_locacao.get(locacao["id"], [])

        for parcela in parcelas:
            parcela["situacao"] = _situacao_parcela(parcela, hoje)
            parcela["valor"] = round(float(parcela["valor"] or 0), 2)

        pagas = [p for p in parcelas if p["situacao"] == "lancada"]
        pendentes = [p for p in parcelas if p["situacao"] in ("vencida", "proxima", "futura")]
        vencidas = [p for p in parcelas if p["situacao"] == "vencida"]
        proximas = [p for p in parcelas if p["situacao"] == "proxima"]

        locacao["frequencia_label"] = _rotulo_frequencia(locacao["frequencia"])
        locacao["valor_parcela"] = round(float(locacao["valor_parcela"] or 0), 2)
        locacao["parcelas"] = parcelas
        locacao["total_previsto"] = round(locacao["valor_parcela"] * locacao["ciclos"], 2)
        locacao["total_lancado"] = round(sum(p["valor"] for p in pagas), 2)
        locacao["total_pendente"] = round(sum(p["valor"] for p in pendentes), 2)
        locacao["parcelas_pagas"] = len(pagas)
        locacao["vencidas"] = len(vencidas)
        locacao["proximas"] = len(proximas)
        locacao["proxima_parcela"] = next(
            (p for p in parcelas if p["situacao"] in ("vencida", "proxima", "futura")), None)
        saida.append(locacao)

    return saida


def alertas(obra_id: int) -> dict:
    """Renovações vencidas e próximas — é o que aparece no painel."""
    hoje = hoje_iso()
    limite = (date.fromisoformat(hoje) + timedelta(days=DIAS_DE_ALERTA)).isoformat()

    linhas = todos(
        """SELECT p.*, l.equipamento, l.fornecedor, l.frequencia, l.obra_id
             FROM locacao_parcelas p
             JOIN locacoes l ON l.id = p.locacao_id
            WHERE l.obra_id = ? AND l.status = 'ativa' AND p.status = 'prevista'
              AND p.vencimento <= ?
            ORDER BY p.vencimento""", (obra_id, limite))

    vencidas, proximas = [], []
    for linha in linhas:
        item = dict(linha)
        item["valor"] = round(float(item["valor"] or 0), 2)
        item["frequencia_label"] = _rotulo_frequencia(item["frequencia"])
        (vencidas if item["vencimento"] < hoje else proximas).append(item)

    return {
        "vencidas": vencidas,
        "proximas": proximas,
        "total_vencido": round(sum(p["valor"] for p in vencidas), 2),
        "total_proximo": round(sum(p["valor"] for p in proximas), 2),
        "quantidade": len(vencidas) + len(proximas),
    }


def painel_obra(obra_id: int) -> dict:
    locacoes = listar_locacoes(obra_id)
    ativas = [l for l in locacoes if l["status"] == "ativa"]

    # Quanto de aluguel a obra tem comprometido por mês, normalizando as
    # frequências — é o número que responde "quanto os equipamentos me custam".
    por_mes = 0.0
    for locacao in ativas:
        dias = FREQUENCIAS[locacao["frequencia"]][1]
        por_mes += locacao["valor_parcela"] * (30 / dias)

    return {
        "locacoes": locacoes,
        "ativas": len(ativas),
        "custo_mensal_estimado": round(por_mes, 2),
        "total_pendente": round(sum(l["total_pendente"] for l in ativas), 2),
        "total_lancado": round(sum(l["total_lancado"] for l in locacoes), 2),
        "alertas": alertas(obra_id),
    }


# ---------------------------------------------------------------- lançamento

def _dia_para_data(obra_id: int, data_alvo: str) -> int | None:
    """Encontra o dia do diário correspondente a uma data.

    A renovação pode cair numa semana que ainda não foi criada. Nesse caso
    usamos o dia mais próximo já existente, para a despesa não ficar órfã.
    """
    exato = um(
        """SELECT d.id FROM dias d
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.data = ?""", (obra_id, data_alvo))
    if exato:
        return exato["id"]

    anterior = um(
        """SELECT d.id FROM dias d
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.data <= ?
            ORDER BY d.data DESC LIMIT 1""", (obra_id, data_alvo))
    if anterior:
        return anterior["id"]

    proximo = um(
        """SELECT d.id FROM dias d
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ?
            ORDER BY d.data LIMIT 1""", (obra_id,))
    return proximo["id"] if proximo else None


def lancar_parcela(parcela_id: int, dados: dict, usuario_id: int | None = None) -> dict:
    """Transforma uma renovação prevista em despesa de verdade no caixa."""
    parcela = um(
        """SELECT p.*, l.obra_id, l.equipamento, l.fornecedor, l.etapa_id, l.frequencia
             FROM locacao_parcelas p JOIN locacoes l ON l.id = p.locacao_id
            WHERE p.id = ?""", (parcela_id,))
    if not parcela:
        raise ApiErro("Renovação não encontrada.", 404)
    if parcela["status"] == "lancada":
        raise ApiErro("Esta renovação já foi lançada no caixa.")

    dia_id = _dia_para_data(parcela["obra_id"], parcela["vencimento"])
    if not dia_id:
        raise ApiErro("Crie ao menos uma semana na obra antes de lançar a renovação.")

    status = dados.get("status_pagamento")
    status = status if status in ("Em Aberto", "Pago", "Reembolso") else "Em Aberto"
    valor = numero(dados.get("valor"), parcela["valor"], 0, campo="o valor")

    cur = executar(
        """INSERT INTO despesas (dia_id, etapa_id, categoria, descricao, fornecedor, valor,
                                 status_pagamento, vencimento, pago_em, criado_por, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (dia_id, parcela["etapa_id"], CATEGORIA,
         f"{parcela['equipamento']} · renovação {parcela['numero']}",
         parcela["fornecedor"], valor, status, parcela["vencimento"],
         hoje_iso() if status == "Pago" else None, usuario_id, agora_iso()))

    executar(
        "UPDATE locacao_parcelas SET despesa_id = ?, valor = ?, status = 'lancada' WHERE id = ?",
        (cur.lastrowid, valor, parcela_id))

    return {"parcela_id": parcela_id, "despesa_id": cur.lastrowid, "valor": valor}


def encerrar_locacao(locacao_id: int) -> int:
    """Equipamento devolvido: cancela as renovações que ainda não foram pagas."""
    locacao = um("SELECT * FROM locacoes WHERE id = ?", (locacao_id,))
    if not locacao:
        raise ApiErro("Locação não encontrada.", 404)

    pendentes = todos(
        "SELECT id FROM locacao_parcelas WHERE locacao_id = ? AND status = 'prevista'",
        (locacao_id,))
    executar(
        "UPDATE locacao_parcelas SET status = 'cancelada' WHERE locacao_id = ? AND status = 'prevista'",
        (locacao_id,))
    executar(
        "UPDATE locacoes SET status = 'encerrada', encerrado_em = ? WHERE id = ?",
        (hoje_iso(), locacao_id))
    return len(pendentes)


def prorrogar_locacao(locacao_id: int, ciclos_extras: int) -> int:
    """Estende o aluguel por mais N períodos, continuando o cronograma."""
    locacao = um("SELECT * FROM locacoes WHERE id = ?", (locacao_id,))
    if not locacao:
        raise ApiErro("Locação não encontrada.", 404)

    extras = int(numero(ciclos_extras, 1, 1, MAXIMO_PARCELAS, campo="a prorrogação"))
    ultimo = um("SELECT COALESCE(MAX(numero), 0) AS n FROM locacao_parcelas WHERE locacao_id = ?",
                (locacao_id,))["n"]
    inicio = date.fromisoformat(locacao["data_inicio"])

    for i in range(1, extras + 1):
        numero_parcela = ultimo + i
        vencimento = _somar_ciclo(inicio, locacao["frequencia"], numero_parcela - 1)
        executar(
            """INSERT INTO locacao_parcelas (locacao_id, numero, vencimento, valor, status, criado_em)
               VALUES (?, ?, ?, ?, 'prevista', ?)""",
            (locacao_id, numero_parcela, vencimento.isoformat(),
             locacao["valor_parcela"], agora_iso()))

    fim = _somar_ciclo(inicio, locacao["frequencia"], ultimo + extras - 1)
    executar(
        "UPDATE locacoes SET ciclos = ?, data_fim = ?, status = 'ativa', encerrado_em = NULL WHERE id = ?",
        (ultimo + extras, fim.isoformat(), locacao_id))
    return extras


def remover_locacao(locacao_id: int) -> None:
    """Só some do sistema quem nunca teve renovação lançada no caixa."""
    lancadas = um(
        "SELECT COUNT(*) AS total FROM locacao_parcelas WHERE locacao_id = ? AND status = 'lancada'",
        (locacao_id,))["total"]
    if lancadas:
        raise ApiErro(
            "Esta locação já tem renovações lançadas no caixa e não pode ser apagada. "
            "Encerre-a para parar as próximas cobranças.")
    executar("DELETE FROM locacoes WHERE id = ?", (locacao_id,))
