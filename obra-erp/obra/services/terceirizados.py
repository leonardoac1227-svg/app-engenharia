# -*- coding: utf-8 -*-
"""Terceirizados — contrato global fechado, pago por medição semanal.

Como funciona na obra: contrata-se a pintura por R$ 50.000 no total. Toda
semana mede-se quanto do serviço andou (em %) e paga-se a fatia
correspondente. O que importa controlar é: quanto já foi medido, quanto falta,
e se aquela medição entrou no caixa da semana.

Decisão de projeto: **a medição aprovada vira uma despesa de verdade**,
vinculada ao contrato. Assim ela herda contas a pagar, vencimento, baixa de
pagamento, exportação e custo da semana — tudo que já existe. Criar um segundo
sistema financeiro paralelo significaria manter duas verdades sobre o mesmo
dinheiro, e é assim que relatório começa a divergir de caixa.

O toggle "entra no pagamento da semana" decide exatamente isso: com ele
ligado, nasce a despesa; desligado, a medição registra apenas o avanço
contratual (útil quando o combinado é medir agora e pagar no mês seguinte).
"""

from __future__ import annotations

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, numero, texto

STATUS_CONTRATO = ("ativo", "concluido", "cancelado")
CATEGORIA_DESPESA = "Serviço"


# ---------------------------------------------------------------- leitura

def _contrato_completo(linha) -> dict:
    c = dict(linha)
    total = float(c.get("valor_total") or 0)
    medido_pct = round(float(c.pop("medido_percentual", 0) or 0), 2)
    medido_valor = round(float(c.pop("medido_valor", 0) or 0), 2)

    c["valor_total"] = round(total, 2)
    c["medido_percentual"] = medido_pct
    c["medido_valor"] = medido_valor
    c["saldo_percentual"] = round(max(100 - medido_pct, 0), 2)
    c["saldo_valor"] = round(max(total - medido_valor, 0), 2)
    c["pago"] = round(float(c.pop("pago_valor", 0) or 0), 2)
    c["a_pagar"] = round(max(medido_valor - c["pago"], 0), 2)
    c["concluido"] = medido_pct >= 100 or c.get("status") == "concluido"
    c["medicoes_count"] = int(c.pop("medicoes_count", 0) or 0)
    c["tipo"] = "avulso" if c.get("funcionario_id") else "empresa"
    return c


def listar_contratos(obra_id: int, incluir_encerrados: bool = True) -> list[dict]:
    filtro = "" if incluir_encerrados else " AND c.status = 'ativo'"
    linhas = todos(
        f"""SELECT c.*, e.nome AS etapa_nome,
                   f.documento AS funcionario_documento,
                   f.funcao AS funcionario_funcao,
                   f.chave_pix AS funcionario_pix,
                   COALESCE((SELECT SUM(m.percentual) FROM contrato_medicoes m
                              WHERE m.contrato_id = c.id), 0) AS medido_percentual,
                   COALESCE((SELECT SUM(m.valor) FROM contrato_medicoes m
                              WHERE m.contrato_id = c.id), 0) AS medido_valor,
                   COALESCE((SELECT COUNT(*) FROM contrato_medicoes m
                              WHERE m.contrato_id = c.id), 0) AS medicoes_count,
                   COALESCE((SELECT SUM(d.valor) FROM contrato_medicoes m
                              JOIN despesas d ON d.id = m.despesa_id
                             WHERE m.contrato_id = c.id AND d.status_pagamento = 'Pago'), 0) AS pago_valor
              FROM contratos c
              LEFT JOIN etapas e ON e.id = c.etapa_id
              LEFT JOIN funcionarios f ON f.id = c.funcionario_id
             WHERE c.obra_id = ?{filtro}
             ORDER BY c.status, c.empresa, c.id""",
        (obra_id,))
    return [_contrato_completo(linha) for linha in linhas]


def medicoes_do_contrato(contrato_id: int) -> list[dict]:
    linhas = todos(
        """SELECT m.*, s.numero AS semana_numero, s.data_inicio,
                  d.status_pagamento, d.vencimento
             FROM contrato_medicoes m
             JOIN semanas s ON s.id = m.semana_id
             LEFT JOIN despesas d ON d.id = m.despesa_id
            WHERE m.contrato_id = ?
            ORDER BY s.numero""",
        (contrato_id,))

    acumulado = 0.0
    saida = []
    for linha in linhas:
        item = dict(linha)
        acumulado += float(item["percentual"] or 0)
        item["percentual"] = round(float(item["percentual"] or 0), 2)
        item["valor"] = round(float(item["valor"] or 0), 2)
        item["acumulado"] = round(acumulado, 2)
        item["entra_no_caixa"] = bool(item["entra_no_caixa"])
        saida.append(item)
    return saida


def medicoes_da_semana(semana_id: int) -> list[dict]:
    """Medições lançadas na semana — alimenta fechamento e relatório."""
    linhas = todos(
        """SELECT m.*, c.empresa, c.servico, c.valor_total, c.funcionario_id,
                  e.nome AS etapa_nome, d.status_pagamento
             FROM contrato_medicoes m
             JOIN contratos c ON c.id = m.contrato_id
             LEFT JOIN etapas e ON e.id = c.etapa_id
             LEFT JOIN despesas d ON d.id = m.despesa_id
            WHERE m.semana_id = ?
            ORDER BY c.empresa""",
        (semana_id,))

    saida = []
    for linha in linhas:
        item = dict(linha)
        item["percentual"] = round(float(item["percentual"] or 0), 2)
        item["valor"] = round(float(item["valor"] or 0), 2)
        item["entra_no_caixa"] = bool(item["entra_no_caixa"])
        item["tipo"] = "avulso" if item.get("funcionario_id") else "empresa"
        acumulado = um(
            """SELECT COALESCE(SUM(m2.percentual), 0) AS total
                 FROM contrato_medicoes m2
                 JOIN semanas s2 ON s2.id = m2.semana_id
                WHERE m2.contrato_id = ?
                  AND s2.numero <= (SELECT numero FROM semanas WHERE id = ?)""",
            (item["contrato_id"], semana_id))
        item["acumulado"] = round(float(acumulado["total"] or 0), 2)
        saida.append(item)
    return saida


def resumo_semana(semana_id: int) -> dict:
    medicoes = medicoes_da_semana(semana_id)
    no_caixa = sum(m["valor"] for m in medicoes if m["entra_no_caixa"])
    fora = sum(m["valor"] for m in medicoes if not m["entra_no_caixa"])
    # Mão de obra avulsa é custo de pessoal, ainda que lançado como serviço:
    # separar permite responder "quanto gastei com gente nesta semana?".
    avulsos = sum(m["valor"] for m in medicoes if m["tipo"] == "avulso")
    return {
        "total": len(medicoes),
        "valor_no_caixa": round(no_caixa, 2),
        "valor_fora_do_caixa": round(fora, 2),
        "valor_medido": round(no_caixa + fora, 2),
        "valor_empresas": round((no_caixa + fora) - avulsos, 2),
        "valor_producao": round(avulsos, 2),
        "lista": medicoes,
    }


# ---------------------------------------------------------------- escrita

def criar_contrato(obra_id: int, dados: dict) -> int:
    """Cria um contrato de empreitada.

    Duas naturezas, mesmo motor de medição: uma **empresa** terceirizada
    (pintura, forro) ou um **profissional por produção** já cadastrado na
    equipe — o avulso pago por tarefa, não por hora. No segundo caso o nome do
    contratado vem do cadastro, para não haver duas grafias da mesma pessoa.
    """
    funcionario_id = dados.get("funcionario_id") or None
    servico = texto(dados.get("servico"), 160)

    if funcionario_id:
        profissional = um(
            "SELECT * FROM funcionarios WHERE id = ? AND obra_id = ?", (funcionario_id, obra_id))
        if not profissional:
            raise ApiErro("Profissional não encontrado nesta obra.")
        if profissional["tipo_contrato"] != "producao":
            raise ApiErro(
                f"{profissional['nome']} está cadastrado como contrato fixo (pago por hora). "
                "Mude o tipo para 'Por produção' antes de criar uma tarefa por empreitada.")
        empresa = profissional["nome"]
    else:
        empresa = texto(dados.get("empresa"), 160)

    if not empresa:
        raise ApiErro("Informe a empresa ou o prestador.")
    if not servico:
        raise ApiErro("Informe qual serviço foi contratado (ex.: pintura interna).")

    valor = numero(dados.get("valor_total"), 0, 0, campo="o valor do contrato")
    if valor <= 0:
        raise ApiErro("O contrato precisa de um valor global maior que zero.")

    cur = executar(
        """INSERT INTO contratos (obra_id, funcionario_id, empresa, servico, etapa_id, valor_total,
                                  responsavel, telefone, observacoes, status,
                                  criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ativo', ?, ?)""",
        (obra_id, funcionario_id, empresa, servico, dados.get("etapa_id") or None, valor,
         texto(dados.get("responsavel"), 120), texto(dados.get("telefone"), 40),
         texto(dados.get("observacoes"), 500), agora_iso(), agora_iso()))
    return cur.lastrowid


def atualizar_contrato(contrato_id: int, dados: dict) -> None:
    atual = um("SELECT * FROM contratos WHERE id = ?", (contrato_id,))
    if not atual:
        raise ApiErro("Contrato não encontrado.", 404)

    valor = numero(dados.get("valor_total"), atual["valor_total"], 0, campo="o valor do contrato")
    medido = um("""SELECT COALESCE(SUM(valor), 0) AS total FROM contrato_medicoes
                    WHERE contrato_id = ?""", (contrato_id,))["total"]
    if valor < float(medido or 0):
        raise ApiErro(
            "O novo valor do contrato é menor do que já foi medido. "
            "Ajuste as medições antes de reduzir o contrato.")

    status = dados.get("status") if dados.get("status") in STATUS_CONTRATO else atual["status"]

    executar(
        """UPDATE contratos SET empresa = ?, servico = ?, etapa_id = ?, valor_total = ?,
                                responsavel = ?, telefone = ?, observacoes = ?,
                                status = ?, atualizado_em = ?
            WHERE id = ?""",
        (texto(dados.get("empresa"), 160) or atual["empresa"],
         texto(dados.get("servico"), 160) or atual["servico"],
         dados.get("etapa_id") or None, valor,
         texto(dados.get("responsavel"), 120), texto(dados.get("telefone"), 40),
         texto(dados.get("observacoes"), 500), status, agora_iso(), contrato_id))

    # Recalcula os valores das medições: o percentual medido continua valendo,
    # mas o valor em reais acompanha o novo contrato (aditivo, por exemplo).
    if abs(valor - float(atual["valor_total"] or 0)) > 0.001:
        _recalcular_medicoes(contrato_id, valor)


def _recalcular_medicoes(contrato_id: int, valor_total: float) -> None:
    for medicao in todos("SELECT * FROM contrato_medicoes WHERE contrato_id = ?", (contrato_id,)):
        novo_valor = round(valor_total * float(medicao["percentual"]) / 100, 2)
        executar("UPDATE contrato_medicoes SET valor = ? WHERE id = ?", (novo_valor, medicao["id"]))
        if medicao["despesa_id"]:
            executar("UPDATE despesas SET valor = ? WHERE id = ?", (novo_valor, medicao["despesa_id"]))


def remover_contrato(contrato_id: int) -> None:
    """Só sai do sistema quem nunca foi medido — o resto é encerrado."""
    medido = um("SELECT COUNT(*) AS total FROM contrato_medicoes WHERE contrato_id = ?",
                (contrato_id,))["total"]
    if medido:
        raise ApiErro(
            "Este contrato já tem medições lançadas e não pode ser apagado. "
            "Marque-o como concluído ou cancelado para tirá-lo da lista ativa.")
    executar("DELETE FROM contratos WHERE id = ?", (contrato_id,))


def _dia_de_lancamento(semana_id: int) -> int | None:
    """Dia da semana onde a despesa da medição é registrada.

    Preferência pela sexta-feira: é quando a medição costuma ser fechada com o
    prestador. Se não existir, cai no último dia disponível da semana.
    """
    sexta = um("SELECT id FROM dias WHERE semana_id = ? AND dia_semana = 'Sexta'", (semana_id,))
    if sexta:
        return sexta["id"]
    ultimo = um("SELECT id FROM dias WHERE semana_id = ? ORDER BY data DESC, id DESC LIMIT 1",
                (semana_id,))
    return ultimo["id"] if ultimo else None


def registrar_medicao(contrato_id: int, semana_id: int, dados: dict, usuario_nome: str = "") -> dict:
    """Lança (ou corrige) a medição da semana e sincroniza a despesa do caixa."""
    contrato = um("SELECT * FROM contratos WHERE id = ?", (contrato_id,))
    if not contrato:
        raise ApiErro("Contrato não encontrado.", 404)

    percentual = numero(dados.get("percentual"), 0, 0, 100, campo="o percentual medido")
    if percentual <= 0:
        raise ApiErro("Informe o percentual executado nesta semana.")

    anterior = um("SELECT * FROM contrato_medicoes WHERE contrato_id = ? AND semana_id = ?",
                  (contrato_id, semana_id))
    ja_medido = float(um(
        """SELECT COALESCE(SUM(percentual), 0) AS total FROM contrato_medicoes
            WHERE contrato_id = ? AND semana_id != ?""",
        (contrato_id, semana_id))["total"] or 0)

    if ja_medido + percentual > 100.001:
        restante = round(100 - ja_medido, 2)
        raise ApiErro(
            f"Este contrato já está {round(ja_medido, 2):g}% medido. "
            f"Resta no máximo {restante:g}% para lançar.")

    valor = round(float(contrato["valor_total"] or 0) * percentual / 100, 2)
    entra_no_caixa = bool(dados.get("entra_no_caixa", True))
    observacao = texto(dados.get("observacao"), 300)

    if anterior:
        executar(
            """UPDATE contrato_medicoes SET percentual = ?, valor = ?, entra_no_caixa = ?,
                                            observacao = ?, criado_por = ?, criado_em = ?
                WHERE id = ?""",
            (percentual, valor, 1 if entra_no_caixa else 0, observacao, usuario_nome,
             agora_iso(), anterior["id"]))
        medicao_id = anterior["id"]
        despesa_id = anterior["despesa_id"]
    else:
        cur = executar(
            """INSERT INTO contrato_medicoes (contrato_id, semana_id, percentual, valor,
                                              entra_no_caixa, observacao, criado_por, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (contrato_id, semana_id, percentual, valor, 1 if entra_no_caixa else 0,
             observacao, usuario_nome, agora_iso()))
        medicao_id = cur.lastrowid
        despesa_id = None

    despesa_id = _sincronizar_despesa(
        contrato, medicao_id, semana_id, valor, percentual, entra_no_caixa, despesa_id,
        dados.get("vencimento"), dados.get("status_pagamento"))
    executar("UPDATE contrato_medicoes SET despesa_id = ? WHERE id = ?", (despesa_id, medicao_id))

    # Contrato 100% medido se encerra sozinho: some da lista ativa sem sumir do
    # histórico, e para de aparecer para novo lançamento.
    total_medido = float(um("SELECT COALESCE(SUM(percentual), 0) AS t FROM contrato_medicoes WHERE contrato_id = ?",
                            (contrato_id,))["t"] or 0)
    if total_medido >= 99.999 and contrato["status"] == "ativo":
        executar("UPDATE contratos SET status = 'concluido', atualizado_em = ? WHERE id = ?",
                 (agora_iso(), contrato_id))

    return {
        "id": medicao_id, "percentual": percentual, "valor": valor,
        "acumulado": round(total_medido, 2),
        "saldo_valor": round(max(float(contrato["valor_total"] or 0) - (valor + ja_medido * float(contrato["valor_total"] or 0) / 100), 0), 2),
        "entra_no_caixa": entra_no_caixa,
        "despesa_id": despesa_id,
        "contrato_concluido": total_medido >= 99.999,
    }


def _sincronizar_despesa(contrato, medicao_id, semana_id, valor, percentual,
                         entra_no_caixa, despesa_id, vencimento, status_pagamento) -> int | None:
    """Cria, atualiza ou remove a despesa espelho da medição."""
    if not entra_no_caixa:
        if despesa_id:
            executar("DELETE FROM despesas WHERE id = ?", (despesa_id,))
        return None

    descricao = f"Medição {percentual:g}% · {contrato['servico']}"
    status = status_pagamento if status_pagamento in ("Em Aberto", "Pago", "Reembolso") else "Em Aberto"

    if despesa_id and um("SELECT id FROM despesas WHERE id = ?", (despesa_id,)):
        executar(
            """UPDATE despesas SET descricao = ?, fornecedor = ?, valor = ?, etapa_id = ?
                WHERE id = ?""",
            (descricao, contrato["empresa"], valor, contrato["etapa_id"], despesa_id))
        return despesa_id

    dia_id = _dia_de_lancamento(semana_id)
    if not dia_id:
        raise ApiErro("A semana não tem dias cadastrados para lançar a medição.")

    cur = executar(
        """INSERT INTO despesas (dia_id, etapa_id, categoria, descricao, fornecedor, valor,
                                 status_pagamento, vencimento, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (dia_id, contrato["etapa_id"], CATEGORIA_DESPESA, descricao, contrato["empresa"],
         valor, status, vencimento or None, agora_iso()))
    return cur.lastrowid


def remover_medicao(medicao_id: int) -> None:
    medicao = um("SELECT * FROM contrato_medicoes WHERE id = ?", (medicao_id,))
    if not medicao:
        raise ApiErro("Medição não encontrada.", 404)
    if medicao["despesa_id"]:
        executar("DELETE FROM despesas WHERE id = ?", (medicao["despesa_id"],))
    executar("DELETE FROM contrato_medicoes WHERE id = ?", (medicao_id,))

    # Se o contrato tinha se encerrado por atingir 100%, ele volta a ficar ativo.
    executar(
        """UPDATE contratos SET status = 'ativo', atualizado_em = ?
            WHERE id = ? AND status = 'concluido'""",
        (agora_iso(), medicao["contrato_id"]))


def painel_obra(obra_id: int, semana_id: int | None = None) -> dict:
    """Números do topo da tela de terceirizados.

    Com `semana_id`, cada contrato informa se aquela semana já foi medida —
    é o que faz o botão dizer "corrigir" em vez de "medir" e evita o gestor
    lançar duas vezes achando que a primeira não salvou.
    """
    contratos = listar_contratos(obra_id)

    if semana_id:
        medicoes = {m["contrato_id"]: m for m in todos(
            """SELECT m.*, d.status_pagamento
                 FROM contrato_medicoes m
                 LEFT JOIN despesas d ON d.id = m.despesa_id
                WHERE m.semana_id = ?""", (semana_id,))}
        for contrato in contratos:
            atual = medicoes.get(contrato["id"])
            contrato["medicao_semana"] = {
                "id": atual["id"],
                "percentual": round(float(atual["percentual"] or 0), 2),
                "valor": round(float(atual["valor"] or 0), 2),
                "entra_no_caixa": bool(atual["entra_no_caixa"]),
                "status_pagamento": atual["status_pagamento"],
            } if atual else None
    ativos = [c for c in contratos if c["status"] == "ativo"]
    return {
        "contratos": contratos,
        "empresas": len([c for c in contratos if c["tipo"] == "empresa"]),
        "avulsos": len([c for c in contratos if c["tipo"] == "avulso"]),
        "total_producao": round(sum(c["medido_valor"] for c in contratos if c["tipo"] == "avulso"), 2),
        "total_contratado": round(sum(c["valor_total"] for c in contratos if c["status"] != "cancelado"), 2),
        "total_medido": round(sum(c["medido_valor"] for c in contratos), 2),
        "total_saldo": round(sum(c["saldo_valor"] for c in ativos), 2),
        "total_a_pagar": round(sum(c["a_pagar"] for c in contratos), 2),
        "ativos": len(ativos),
    }
