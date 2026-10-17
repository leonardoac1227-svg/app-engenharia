# -*- coding: utf-8 -*-
"""Regras de consolidação: é aqui que os lançamentos soltos viram informação
de gestão (fechamento semanal e dashboard da obra).

Manter isso fora das rotas permite que a API JSON, o relatório .txt, o PDF e o
resumo por IA leiam exatamente os mesmos números — sem risco de o PDF divergir
da tela, que é o erro clássico desse tipo de app.
"""

from __future__ import annotations

from datetime import date

from ..config import DIAS_SEMANA, DIAS_UTEIS
from ..db import get_db, todos, um
from ..utils import ApiErro, agora_iso, data_br, hoje_iso, somar_dias
from .fretes import resumo_semana as resumo_fretes
from .locacoes import alertas as alertas_locacoes

INDICE_DIA = {nome: i for i, nome in enumerate(DIAS_SEMANA)}


# ---------------------------------------------------------------- dias

def garantir_dias(semana_id: int) -> None:
    """Cria os 7 dias da semana (se faltarem) já com a data de calendário."""
    semana = um("SELECT id, data_inicio FROM semanas WHERE id = ?", (semana_id,))
    if not semana:
        raise ApiErro("Semana não encontrada.", 404)
    inicio = semana["data_inicio"] or hoje_iso()

    db = get_db()
    existentes = {r["dia_semana"]: r for r in todos(
        "SELECT id, dia_semana, data FROM dias WHERE semana_id = ?", (semana_id,))}

    for nome in DIAS_SEMANA:
        data_dia = somar_dias(inicio, INDICE_DIA[nome])
        if nome not in existentes:
            db.execute(
                "INSERT INTO dias (semana_id, dia_semana, data) VALUES (?, ?, ?)",
                (semana_id, nome, data_dia),
            )
        elif existentes[nome]["data"] != data_dia:
            # a data de início da semana pode ter sido corrigida pelo gestor
            db.execute("UPDATE dias SET data = ? WHERE id = ?", (data_dia, existentes[nome]["id"]))
    db.commit()


def _totais_por_dia(dias_ids: list[int]) -> dict[int, dict]:
    """Números de cada dia da semana, em três consultas.

    Eram seis: a tabela `horas` era varrida três vezes (folha, horas, efetivo)
    e a de `despesas`, duas. Como todos os agregados saem da mesma varredura,
    juntá-los não custa nada ao banco e economiza metade das idas — o que pesa
    quando cada ida atravessa a internet até o Supabase.
    """
    if not dias_ids:
        return {}
    marcas = ",".join("?" * len(dias_ids))

    mao_de_obra = {r["dia_id"]: r for r in todos(
        f"""SELECT dia_id,
                   COALESCE(SUM(horas_trabalhadas * valor_hora), 0) AS folha,
                   COALESCE(SUM(horas_trabalhadas), 0) AS horas,
                   COUNT(CASE WHEN horas_trabalhadas > 0 THEN 1 END) AS efetivo
              FROM horas WHERE dia_id IN ({marcas}) GROUP BY dia_id""", dias_ids)}

    gastos = {r["dia_id"]: r for r in todos(
        f"""SELECT dia_id, COALESCE(SUM(valor), 0) AS total, COUNT(*) AS quantidade
              FROM despesas WHERE dia_id IN ({marcas}) GROUP BY dia_id""", dias_ids)}

    fotos = {r["dia_id"]: r["t"] for r in todos(
        f"SELECT dia_id, COUNT(*) AS t FROM fotos WHERE dia_id IN ({marcas}) GROUP BY dia_id",
        dias_ids)}

    folha = {i: linha["folha"] for i, linha in mao_de_obra.items()}
    horas = {i: linha["horas"] for i, linha in mao_de_obra.items()}
    efetivo = {i: linha["efetivo"] for i, linha in mao_de_obra.items()}
    despesas = {i: linha["total"] for i, linha in gastos.items()}
    qtd_desp = {i: linha["quantidade"] for i, linha in gastos.items()}

    return {
        i: {
            "folha": round(folha.get(i, 0) or 0, 2),
            "horas": round(horas.get(i, 0) or 0, 2),
            "efetivo": efetivo.get(i, 0) or 0,
            "despesas": round(despesas.get(i, 0) or 0, 2),
            "qtd_despesas": qtd_desp.get(i, 0) or 0,
            "fotos": fotos.get(i, 0) or 0,
        }
        for i in dias_ids
    }


def listar_dias(semana_id: int, com_fotos: bool = True) -> list[dict]:
    """Dias da semana com os indicadores que alimentam os alertas da interface."""
    garantir_dias(semana_id)
    rows = todos(
        """SELECT id, dia_semana, data, atualizado_em
             FROM dias WHERE semana_id = ?""",
        (semana_id,),
    )
    por_nome = {r["dia_semana"]: dict(r) for r in rows}
    dias = [por_nome[n] for n in DIAS_SEMANA if n in por_nome]
    ids = [d["id"] for d in dias]
    totais = _totais_por_dia(ids)

    fotos_por_dia: dict[int, list] = {}
    if com_fotos and ids:
        marcas = ",".join("?" * len(ids))
        for f in todos(
            f"""SELECT id, dia_id, arquivo, legenda, ambiente, nome_original
                  FROM fotos WHERE dia_id IN ({marcas}) ORDER BY id""", ids):
            fotos_por_dia.setdefault(f["dia_id"], []).append({
                "id": f["id"], "url": f"/arquivos/{f['arquivo']}", "legenda": f["legenda"],
                "ambiente": f["ambiente"], "nome_original": f["nome_original"],
            })

    hoje = hoje_iso()
    for d in dias:
        t = totais.get(d["id"], {})
        d.update(t)
        d["fotos_lista"] = fotos_por_dia.get(d["id"], [])
        d["tem_ponto"] = bool(t.get("horas"))
        d["passado"] = bool(d["data"] and d["data"] < hoje)
        d["hoje"] = d["data"] == hoje
        d["util"] = d["dia_semana"] in DIAS_UTEIS
        # Alerta 1.5: dia útil que já passou e não tem NENHUM registro.
        d["pendente"] = bool(
            d["passado"] and d["util"]
            and not t.get("fotos") and not d["tem_ponto"] and not t.get("qtd_despesas")
        )
    return dias


# ---------------------------------------------------------------- fechamento

def dados_fechamento(semana_id: int) -> dict:
    semana = um(
        """SELECT id, obra_id, numero, data_inicio, orcamento_previsto,
                  fechada, folha_paga, folha_paga_em
             FROM semanas WHERE id = ?""",
        (semana_id,),
    )
    if not semana:
        raise ApiErro("Semana não encontrada.", 404)

    obra = um(
        "SELECT id, nome, cliente, endereco, orcamento_total, data_inicio, previsao_fim FROM obras WHERE id = ?",
        (semana["obra_id"],),
    )

    dias = listar_dias(semana_id)
    ids = [d["id"] for d in dias]
    marcas = ",".join("?" * len(ids)) if ids else "0"

    folha = um(
        f"SELECT COALESCE(SUM(horas_trabalhadas * valor_hora), 0) AS t FROM horas WHERE dia_id IN ({marcas})",
        ids,
    )["t"] if ids else 0.0

    # Os três status saem da mesma varredura: antes eram três consultas
    # idênticas mudando só o filtro.
    por_status = {"Reembolso": 0.0, "Em Aberto": 0.0, "Pago": 0.0}
    if ids:
        for registro in todos(
            f"""SELECT status_pagamento, COALESCE(SUM(valor), 0) AS t
                  FROM despesas WHERE dia_id IN ({marcas})
                 GROUP BY status_pagamento""", ids):
            por_status[registro["status_pagamento"]] = round(float(registro["t"] or 0), 2)

    reembolso = por_status.get("Reembolso", 0.0)
    em_aberto = por_status.get("Em Aberto", 0.0)
    pago = por_status.get("Pago", 0.0)
    total_despesas = round(reembolso + em_aberto + pago, 2)
    folha = round(folha or 0, 2)
    custo_semana = round(folha + total_despesas, 2)

    previsto = round(semana["orcamento_previsto"] or 0, 2)
    percentual_orcamento = round((custo_semana / previsto) * 100, 1) if previsto > 0 else None
    if percentual_orcamento is None:
        farol = "sem_meta"
    elif percentual_orcamento <= 85:
        farol = "verde"
    elif percentual_orcamento <= 100:
        farol = "amarelo"
    else:
        farol = "vermelho"

    despesas = []
    if ids:
        nome_por_id = {d["id"]: d["dia_semana"] for d in dias}
        data_por_id = {d["id"]: d["data"] for d in dias}
        for r in todos(
            f"""SELECT x.id, x.dia_id, x.categoria, x.descricao, x.fornecedor, x.valor,
                       x.status_pagamento, x.numero_nota, x.vencimento, x.pago_em, x.imagem_path,
                       x.nome_arquivo
                  FROM despesas x
                 WHERE x.dia_id IN ({marcas}) ORDER BY x.dia_id, x.id""", ids):
            item = dict(r)
            item["dia_semana"] = nome_por_id.get(r["dia_id"], "")
            item["data"] = data_por_id.get(r["dia_id"], "")
            item["anexo_url"] = f"/arquivos/{r['imagem_path']}" if r["imagem_path"] else ""
            despesas.append(item)

    por_categoria = {}
    for d in despesas:
        por_categoria[d["categoria"]] = round(por_categoria.get(d["categoria"], 0) + d["valor"], 2)

    folha_detalhada = []
    if ids:
        for r in todos(
            f"""SELECT f.id, f.nome, f.funcao,
                       COALESCE(SUM(h.horas_trabalhadas), 0) AS horas,
                       COALESCE(SUM(h.horas_trabalhadas * h.valor_hora), 0) AS total
                  FROM horas h JOIN funcionarios f ON f.id = h.funcionario_id
                 WHERE h.dia_id IN ({marcas})
                 GROUP BY f.id, f.nome, f.funcao
                HAVING SUM(h.horas_trabalhadas) > 0
                 ORDER BY SUM(h.horas_trabalhadas) DESC""", ids):
            folha_detalhada.append({
                "id": r["id"], "nome": r["nome"], "funcao": r["funcao"],
                "horas": round(r["horas"], 2), "total": round(r["total"], 2),
            })

    fotos = [f for d in dias for f in d["fotos_lista"]]
    pendentes = [d["dia_semana"] for d in dias if d["pendente"]]

    data_fim = somar_dias(semana["data_inicio"], 6) if semana["data_inicio"] else None
    total_horas = round(sum(d.get("horas", 0) for d in dias), 2)
    dias_trabalhados = len([d for d in dias if d.get("horas")])

    fretes = resumo_fretes(semana["id"])

    return {
        "obra": dict(obra) if obra else {},
        "obra_nome": obra["nome"] if obra else "",
        "semana_id": semana["id"],
        "semana_numero": semana["numero"],
        "data_inicio": semana["data_inicio"],
        "data_fim": data_fim,
        "fretes": fretes,
        "folha_paga": bool(semana["folha_paga"]),
        "folha_paga_em": semana["folha_paga_em"],
        "fechada": bool(semana["fechada"]),
        "dias": dias,
        "dias_pendentes": pendentes,
        "despesas": despesas,
        "despesas_por_categoria": por_categoria,
        "folha_detalhada": folha_detalhada,
        "fotos": fotos,
        "financeiro": {
            "folha_paga": bool(semana["folha_paga"]),
            # Falta pagar = tudo que a semana custou, menos o que já saiu.
            # A folha quitada sai da conta; o custo total não muda.
            "a_pagar": round((0 if semana["folha_paga"] else folha) + em_aberto + reembolso, 2),
            "folha": folha,
            "reembolso": reembolso,
            "em_aberto": em_aberto,
            "pago": pago,
            "despesas": total_despesas,
            "terceiros": round(folha + em_aberto, 2),
            "custo_semana": custo_semana,
            "orcamento_previsto": previsto,
            "saldo": round(previsto - custo_semana, 2) if previsto else None,
            "percentual_orcamento": percentual_orcamento,
            "farol": farol,
        },
        "indicadores": {
            "total_horas": total_horas,
            "dias_trabalhados": dias_trabalhados,
            "efetivo_medio": round(
                sum(d.get("efetivo", 0) for d in dias) / dias_trabalhados, 1
            ) if dias_trabalhados else 0,
            "total_fotos": len(fotos),
            "custo_medio_dia": round(custo_semana / dias_trabalhados, 2) if dias_trabalhados else 0,
        },
    }


# ---------------------------------------------------------------- dashboard

def dashboard_obra(obra_id: int) -> dict:
    """Resumo executivo da obra — a "foto em 3 segundos" da tela inicial."""
    obra = um(
        """SELECT id, nome, cliente, endereco, orcamento_total, data_inicio,
                  previsao_fim, status FROM obras WHERE id = ?""",
        (obra_id,),
    )
    if not obra:
        raise ApiErro("Obra não encontrada.", 404)

    semanas = todos(
        """SELECT id, numero, data_inicio, orcamento_previsto, fechada
             FROM semanas WHERE obra_id = ? ORDER BY numero""",
        (obra_id,),
    )
    semana_ids = [s["id"] for s in semanas]

    gasto_total = 0.0
    folha_total = 0.0
    despesas_total = 0.0
    serie = []
    a_pagar = 0.0
    reembolso_total = 0.0

    if semana_ids:
        marcas = ",".join("?" * len(semana_ids))
        folha_por_semana = {r["semana_id"]: r["t"] for r in todos(
            f"""SELECT d.semana_id AS semana_id,
                       COALESCE(SUM(h.horas_trabalhadas * h.valor_hora), 0) AS t
                  FROM horas h JOIN dias d ON d.id = h.dia_id
                 WHERE d.semana_id IN ({marcas}) GROUP BY d.semana_id""", semana_ids)}
        # Total, em aberto e reembolso de cada semana numa varredura só —
        # eram três consultas iguais mudando apenas o filtro de status.
        desp_por_semana, aberto_por_semana, reemb_por_semana = {}, {}, {}
        for registro in todos(
            f"""SELECT d.semana_id AS semana_id,
                       COALESCE(SUM(x.valor), 0) AS total,
                       COALESCE(SUM(CASE WHEN x.status_pagamento = 'Em Aberto'
                                         THEN x.valor ELSE 0 END), 0) AS aberto,
                       COALESCE(SUM(CASE WHEN x.status_pagamento = 'Reembolso'
                                         THEN x.valor ELSE 0 END), 0) AS reembolso
                  FROM despesas x JOIN dias d ON d.id = x.dia_id
                 WHERE d.semana_id IN ({marcas}) GROUP BY d.semana_id""", semana_ids):
            desp_por_semana[registro["semana_id"]] = registro["total"]
            aberto_por_semana[registro["semana_id"]] = registro["aberto"]
            reemb_por_semana[registro["semana_id"]] = registro["reembolso"]

        for s in semanas:
            f = round(folha_por_semana.get(s["id"], 0) or 0, 2)
            dsp = round(desp_por_semana.get(s["id"], 0) or 0, 2)
            custo = round(f + dsp, 2)
            folha_total += f
            despesas_total += dsp
            gasto_total += custo
            a_pagar += round(aberto_por_semana.get(s["id"], 0) or 0, 2)
            reembolso_total += round(reemb_por_semana.get(s["id"], 0) or 0, 2)
            serie.append({
                "semana_id": s["id"],
                "numero": s["numero"],
                "data_inicio": s["data_inicio"],
                "custo": custo,
                "previsto": round(s["orcamento_previsto"] or 0, 2),
            })

    gasto_total = round(gasto_total, 2)
    orcamento = round(obra["orcamento_total"] or 0, 2)
    percentual_orcamento = round((gasto_total / orcamento) * 100, 1) if orcamento > 0 else None

    semana_atual = semanas[-1] if semanas else None
    resumo_semana = dados_fechamento(semana_atual["id"]) if semana_atual else None

    ultimas_despesas = [dict(r) for r in todos(
        """SELECT x.id, x.descricao, x.categoria, x.valor, x.status_pagamento, d.data
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? ORDER BY x.id DESC LIMIT 5""", (obra_id,))]

    hoje = hoje_iso()
    vencendo = [dict(r) for r in todos(
        """SELECT x.id, x.descricao, x.fornecedor, x.valor, x.vencimento, x.status_pagamento
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND x.status_pagamento = 'Em Aberto'
              AND x.vencimento IS NOT NULL AND x.vencimento <> ''
            ORDER BY x.vencimento LIMIT 8""", (obra_id,))]
    for v in vencendo:
        v["vencida"] = bool(v["vencimento"] and v["vencimento"] < hoje)

    pendencias = []
    if resumo_semana:
        for nome in resumo_semana["dias_pendentes"]:
            pendencias.append(f"{nome} sem nenhum lançamento")
    # Renovação de aluguel vencida entra como pendência do painel: é o custo
    # que mais escapa, justamente por ser repetitivo.
    alertas_obra = alertas_locacoes(obra_id)
    for parcela in alertas_obra["vencidas"]:
        pendencias.append(
            f"Renovação vencida: {parcela['equipamento']} ({data_br(parcela['vencimento'])})")
    if alertas_obra["proximas"]:
        pendencias.append(
            f"{len(alertas_obra['proximas'])} renovação(ões) de aluguel vencendo nos próximos dias")

    if orcamento == 0:
        pendencias.append("Obra sem orçamento total informado")

    equipe = um(
        "SELECT COUNT(*) AS t FROM funcionarios WHERE obra_id = ? AND ativo = 1", (obra_id,)
    )["t"]
    total_fotos = um(
        """SELECT COUNT(*) AS t FROM fotos f
             JOIN dias d ON d.id = f.dia_id
             JOIN semanas s ON s.id = d.semana_id WHERE s.obra_id = ?""", (obra_id,)
    )["t"]

    dias_de_obra = None
    if obra["data_inicio"]:
        try:
            dias_de_obra = (date.today() - date.fromisoformat(obra["data_inicio"])).days
        except ValueError:
            dias_de_obra = None

    return {
        "obra": dict(obra),
        "gerado_em": agora_iso(),
        "totais": {
            "gasto_total": gasto_total,
            "folha_total": round(folha_total, 2),
            "despesas_total": round(despesas_total, 2),
            "orcamento_total": orcamento,
            "saldo": round(orcamento - gasto_total, 2) if orcamento else None,
            "percentual_orcamento": percentual_orcamento,
            "a_pagar": round(a_pagar, 2),
            "reembolso": round(reembolso_total, 2),
            "equipe_ativa": equipe,
            "total_fotos": total_fotos,
            "total_semanas": len(semanas),
            "dias_de_obra": dias_de_obra,
        },
        "serie_semanal": serie,
        "semana_atual": {
            "id": resumo_semana["semana_id"],
            "numero": resumo_semana["semana_numero"],
            "financeiro": resumo_semana["financeiro"],
            "indicadores": resumo_semana["indicadores"],
            "dias_pendentes": resumo_semana["dias_pendentes"],
            "data_inicio": resumo_semana["data_inicio"],
            "data_fim": resumo_semana["data_fim"],
        } if resumo_semana else None,
        "ultimas_despesas": ultimas_despesas,
        "locacoes": alertas_obra,
        "contas_a_vencer": vencendo,
        "pendencias": pendencias,
    }
