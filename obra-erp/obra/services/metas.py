# -*- coding: utf-8 -*-
"""Metas da semana — o compromisso do canteiro, acompanhado dia a dia.

Modelo em três ideias:

1. **Meta tem nome e percentual**, não é texto solto. "Reboco interno do 2º
   pavimento — 60%" diz muito mais que um parágrafo.
2. **O progresso é registrado por dia** (`meta_progresso`). Guardar só o número
   atual esconde onde a obra travou; guardando o histórico, o relatório mostra
   que a meta ficou parada de terça a quinta — que é exatamente a conversa
   que precisa acontecer com o cliente.
3. **Meta incompleta arrasta para a semana seguinte**, levando um contador.
   "3ª semana arrastando" é o alerta mais honesto que um app de obra dá.

Sobre não apagar meta concluída: o pedido original era excluí-la ao fim da
semana. Ela some da lista ativa (não arrasta, não aparece no dia a dia), mas
continua registrada na semana em que foi concluída — é a prova do que foi
entregue, e o relatório do cliente depende disso.
"""

from __future__ import annotations

from ..db import executar, todos, um
from ..utils import agora_iso, numero, texto

# Uma meta que já passou por tantas semanas deixou de ser meta: virou problema.
ARRASTO_CRITICO = 3


def _linha(meta) -> dict:
    d = dict(meta)
    d["percentual"] = round(float(d.get("percentual") or 0), 1)
    d["percentual_inicial"] = round(float(d.get("percentual_inicial") or 0), 1)
    d["concluida"] = bool(d.get("concluida"))
    d["transferida"] = bool(d.get("transferida"))
    d["avanco_semana"] = round(d["percentual"] - d["percentual_inicial"], 1)
    d["arrastada"] = d["semanas_arrastada"] > 0
    d["critica"] = d["semanas_arrastada"] >= ARRASTO_CRITICO and not d["concluida"]
    if d["concluida"]:
        d["situacao"] = "concluida"
    elif d["transferida"]:
        d["situacao"] = "transferida"
    elif d["percentual"] > 0:
        d["situacao"] = "andamento"
    else:
        d["situacao"] = "parada"
    return d


def listar_metas(semana_id: int, com_progresso: bool = False) -> list[dict]:
    metas = [
        _linha(m) for m in todos(
            """SELECT m.*, e.nome AS etapa_nome
                 FROM metas m
                 LEFT JOIN etapas e ON e.id = m.etapa_id
                WHERE m.semana_id = ?
                ORDER BY m.concluida, m.semanas_arrastada DESC, m.ordem, m.id""",
            (semana_id,),
        )
    ]
    if not (metas and com_progresso):
        return metas

    ids = ",".join(str(m["id"]) for m in metas)
    historico = todos(
        f"""SELECT p.meta_id, p.percentual, p.observacao, p.usuario_nome,
                   d.dia_semana, d.data
              FROM meta_progresso p
              JOIN dias d ON d.id = p.dia_id
             WHERE p.meta_id IN ({ids})
             ORDER BY d.data"""
    )
    por_meta: dict[int, list] = {}
    for linha in historico:
        por_meta.setdefault(linha["meta_id"], []).append(dict(linha))
    for meta in metas:
        meta["progresso"] = por_meta.get(meta["id"], [])
    return metas


def criar_meta(obra_id: int, semana_id: int, dados: dict) -> int:
    nome = texto(dados.get("nome"), 200)
    if not nome:
        from ..utils import ApiErro
        raise ApiErro("Dê um nome à meta (ex.: 'Reboco interno do 2º pavimento').")

    percentual = numero(dados.get("percentual"), 0, 0, 100, campo="o percentual")
    ordem = um("SELECT COALESCE(MAX(ordem), 0) + 1 AS proxima FROM metas WHERE semana_id = ?",
               (semana_id,))["proxima"]

    cur = executar(
        """INSERT INTO metas (obra_id, semana_id, nome, descricao, percentual,
                              percentual_inicial, etapa_id, semanas_arrastada,
                              concluida, transferida, ordem, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?, ?, ?)""",
        (obra_id, semana_id, nome, texto(dados.get("descricao"), 400), percentual,
         percentual, dados.get("etapa_id") or None,
         1 if percentual >= 100 else 0, ordem, agora_iso(), agora_iso()),
    )
    return cur.lastrowid


def registrar_progresso(meta_id: int, dia_id: int, dados: dict, usuario_nome: str = "") -> dict:
    """Atualiza o percentual de uma meta em um dia (uma marcação por dia)."""
    percentual = numero(dados.get("percentual"), 0, 0, 100, campo="o percentual")
    observacao = texto(dados.get("observacao"), 300)

    executar(
        """INSERT INTO meta_progresso (meta_id, dia_id, percentual, observacao, usuario_nome, criado_em)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (meta_id, dia_id) DO UPDATE SET
             percentual = excluded.percentual,
             observacao = excluded.observacao,
             usuario_nome = excluded.usuario_nome,
             criado_em = excluded.criado_em""",
        (meta_id, dia_id, percentual, observacao, usuario_nome, agora_iso()),
    )

    # O percentual da meta é sempre o da marcação mais recente no calendário —
    # e não o maior valor: se o mestre corrigiu para baixo, a correção vale.
    ultimo = um(
        """SELECT p.percentual
             FROM meta_progresso p JOIN dias d ON d.id = p.dia_id
            WHERE p.meta_id = ?
            ORDER BY d.data DESC, p.id DESC LIMIT 1""",
        (meta_id,))
    atual = float(ultimo["percentual"]) if ultimo else percentual

    concluida = 1 if atual >= 100 else 0
    data_conclusao = None
    if concluida:
        dia = um("SELECT data FROM dias WHERE id = ?", (dia_id,))
        data_conclusao = dia["data"] if dia else None

    executar(
        """UPDATE metas SET percentual = ?, concluida = ?, concluida_em = ?, atualizado_em = ?
            WHERE id = ?""",
        (atual, concluida, data_conclusao, agora_iso(), meta_id))

    return _linha(um("SELECT * FROM metas WHERE id = ?", (meta_id,)))


def arrastar_metas(obra_id: int, semana_origem_id: int, semana_destino_id: int) -> int:
    """Leva as metas não concluídas da semana anterior para a nova semana.

    A meta antiga não é movida: ela fica congelada na sua semana, marcada como
    transferida, para o relatório daquela semana continuar contando a verdade.
    Na semana nova nasce uma cópia ligada à anterior (`origem_meta_id`), com o
    percentual de onde parou e o contador de arrasto somado.
    """
    pendentes = todos(
        """SELECT * FROM metas
            WHERE semana_id = ? AND concluida = 0 AND transferida = 0
            ORDER BY ordem, id""",
        (semana_origem_id,))
    if not pendentes:
        return 0

    for meta in pendentes:
        executar(
            """INSERT INTO metas (obra_id, semana_id, nome, descricao, percentual,
                                  percentual_inicial, etapa_id, origem_meta_id,
                                  semanas_arrastada, concluida, transferida, ordem,
                                  criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
            (obra_id, semana_destino_id, meta["nome"], meta["descricao"],
             meta["percentual"], meta["percentual"], meta["etapa_id"], meta["id"],
             (meta["semanas_arrastada"] or 0) + 1, meta["ordem"], agora_iso(), agora_iso()),
        )
        executar("UPDATE metas SET transferida = 1, atualizado_em = ? WHERE id = ?",
                 (agora_iso(), meta["id"]))

    return len(pendentes)


def resumo_metas(semana_id: int, com_progresso: bool = False) -> dict:
    """Números das metas para KPIs, fechamento e relatório."""
    metas = listar_metas(semana_id, com_progresso=com_progresso)
    if not metas:
        return {"total": 0, "concluidas": 0, "arrastadas": 0, "criticas": 0,
                "percentual_medio": 0.0, "avanco_semana": 0.0, "lista": []}

    concluidas = sum(1 for m in metas if m["concluida"])
    return {
        "total": len(metas),
        "concluidas": concluidas,
        "arrastadas": sum(1 for m in metas if m["arrastada"] and not m["concluida"]),
        "criticas": sum(1 for m in metas if m["critica"]),
        "percentual_medio": round(sum(m["percentual"] for m in metas) / len(metas), 1),
        "avanco_semana": round(sum(m["avanco_semana"] for m in metas), 1),
        "lista": metas,
    }
