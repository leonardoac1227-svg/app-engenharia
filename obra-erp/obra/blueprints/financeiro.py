# -*- coding: utf-8 -*-
"""Caixa da obra: lançamentos, anexos, contas a pagar e fluxo de caixa.

Todo este módulo é exclusivo do papel *gestor* — o mestre de obra lança RDO e
ponto, mas não vê nem movimenta dinheiro.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..config import CATEGORIAS_PADRAO, STATUS_PAGAMENTO
from ..db import executar, registrar_auditoria, todos, um
from ..security import (exigir_financeiro, garantir_acesso_obra, obra_da_despesa, obra_do_dia)
from ..utils import (ApiErro, agora_iso, data_iso, hoje_iso, numero, remover_anexo, salvar_anexo,
                     somar_dias, texto)

bp = Blueprint("financeiro", __name__, url_prefix="/api")


def _serializar(r) -> dict:
    item = dict(r)
    item["anexo_url"] = f"/arquivos/{r['imagem_path']}" if r["imagem_path"] else ""
    item["vencida"] = bool(
        r["status_pagamento"] == "Em Aberto" and r["vencimento"] and r["vencimento"] < hoje_iso())
    return item


@bp.get("/dias/<int:dia_id>/despesas")
def listar(dia_id: int):
    exigir_financeiro()
    obra_do_dia(dia_id)
    rows = todos(
        """SELECT x.id, x.categoria, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                  x.vencimento, x.pago_em, x.imagem_path, x.nome_arquivo, x.etapa_id,
                  e.nome AS etapa_nome
             FROM despesas x LEFT JOIN etapas e ON e.id = x.etapa_id
            WHERE x.dia_id = ? ORDER BY x.id DESC""", (dia_id,))
    return jsonify([_serializar(r) for r in rows])


@bp.post("/dias/<int:dia_id>/despesas")
def criar(dia_id: int):
    usuario = exigir_financeiro()
    obra_id = obra_do_dia(dia_id)

    categoria = texto(request.form.get("categoria"), 40) or "Material"
    descricao = texto(request.form.get("descricao"), 200, True, "a descrição")
    valor = numero(request.form.get("valor"), 0, 0.01, campo="o valor")
    status = texto(request.form.get("status_pagamento"), 20) or "Em Aberto"
    if status not in STATUS_PAGAMENTO:
        raise ApiErro("Status de pagamento inválido.")

    etapa_id = request.form.get("etapa_id") or None
    if etapa_id:
        etapa = um("SELECT obra_id FROM etapas WHERE id = ?", (etapa_id,))
        if not etapa or etapa["obra_id"] != obra_id:
            raise ApiErro("Etapa inválida para esta obra.")

    vencimento = data_iso(request.form.get("vencimento"), None, "vencimento")
    pago_em = hoje_iso() if status == "Pago" else None

    meta = salvar_anexo(request.files.get("imagem") or request.files.get("anexo"))

    cur = executar(
        """INSERT INTO despesas (dia_id, etapa_id, categoria, descricao, fornecedor, valor,
                                 status_pagamento, vencimento, pago_em, imagem_path,
                                 nome_arquivo, criado_por, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (dia_id, etapa_id or None, categoria, descricao,
         texto(request.form.get("fornecedor"), 120), valor, status, vencimento, pago_em,
         meta["arquivo"] if meta else "", meta["nome_original"] if meta else "",
         usuario["id"], agora_iso()),
    )
    registrar_auditoria(usuario, "lancou_despesa", "despesa", cur.lastrowid,
                        f"{descricao} · {valor:.2f} · {status}")
    return jsonify({"id": cur.lastrowid}), 201


@bp.put("/despesas/<int:despesa_id>")
def atualizar(despesa_id: int):
    usuario = exigir_financeiro()
    obra_da_despesa(despesa_id)
    dados = request.get_json(silent=True) or {}

    campos, valores = [], []
    if "categoria" in dados:
        campos.append("categoria = ?"); valores.append(texto(dados["categoria"], 40) or "Material")
    if "descricao" in dados:
        campos.append("descricao = ?"); valores.append(texto(dados["descricao"], 200, True, "a descrição"))
    if "fornecedor" in dados:
        campos.append("fornecedor = ?"); valores.append(texto(dados["fornecedor"], 120))
    if "valor" in dados:
        campos.append("valor = ?"); valores.append(numero(dados["valor"], 0, 0.01, campo="o valor"))
    if "vencimento" in dados:
        campos.append("vencimento = ?"); valores.append(data_iso(dados["vencimento"], None, "vencimento"))
    if "etapa_id" in dados:
        campos.append("etapa_id = ?"); valores.append(dados["etapa_id"] or None)
    if "status_pagamento" in dados:
        status = texto(dados["status_pagamento"], 20)
        if status not in STATUS_PAGAMENTO:
            raise ApiErro("Status de pagamento inválido.")
        campos.append("status_pagamento = ?"); valores.append(status)
        campos.append("pago_em = ?")
        valores.append(data_iso(dados.get("pago_em"), hoje_iso()) if status == "Pago" else None)

    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(despesa_id)
    executar(f"UPDATE despesas SET {', '.join(campos)} WHERE id = ?", valores)
    registrar_auditoria(usuario, "editou_despesa", "despesa", despesa_id)
    return jsonify({"ok": True})


@bp.post("/despesas/<int:despesa_id>/pagar")
def pagar(despesa_id: int):
    """Baixa rápida de uma conta a pagar (um toque na lista de vencimentos)."""
    usuario = exigir_financeiro()
    obra_da_despesa(despesa_id)
    dados = request.get_json(silent=True) or {}
    data_pagamento = data_iso(dados.get("pago_em"), hoje_iso(), "data de pagamento")
    executar("UPDATE despesas SET status_pagamento = 'Pago', pago_em = ? WHERE id = ?",
             (data_pagamento, despesa_id))
    registrar_auditoria(usuario, "baixou_despesa", "despesa", despesa_id, data_pagamento)
    return jsonify({"ok": True, "pago_em": data_pagamento})


@bp.delete("/despesas/<int:despesa_id>")
def remover(despesa_id: int):
    usuario = exigir_financeiro()
    obra_da_despesa(despesa_id)
    row = um("SELECT imagem_path, descricao FROM despesas WHERE id = ?", (despesa_id,))
    executar("DELETE FROM despesas WHERE id = ?", (despesa_id,))
    if row and row["imagem_path"]:
        remover_anexo(row["imagem_path"])
    registrar_auditoria(usuario, "excluiu_despesa", "despesa", despesa_id,
                        row["descricao"] if row else "")
    return jsonify({"ok": True})


@bp.get("/obras/<int:obra_id>/financeiro")
def painel_financeiro(obra_id: int):
    """Fluxo de caixa da obra: vencidas, a vencer, por categoria e por semana."""
    exigir_financeiro()
    garantir_acesso_obra(obra_id)

    hoje = hoje_iso()
    limite = somar_dias(hoje, 7)

    abertas = [_serializar(r) for r in todos(
        """SELECT x.id, x.categoria, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                  x.vencimento, x.pago_em, x.imagem_path, x.nome_arquivo, d.data, s.numero AS semana
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND x.status_pagamento = 'Em Aberto'
            ORDER BY (x.vencimento IS NULL), x.vencimento, x.id""", (obra_id,))]

    vencidas = [x for x in abertas if x["vencida"]]
    proximas = [x for x in abertas if x["vencimento"] and hoje <= x["vencimento"] <= limite]
    sem_data = [x for x in abertas if not x["vencimento"]]

    por_categoria = [dict(r) for r in todos(
        """SELECT x.categoria, COALESCE(SUM(x.valor), 0) AS total, COUNT(*) AS itens
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? GROUP BY x.categoria ORDER BY total DESC""", (obra_id,))]

    por_status = {r["status_pagamento"]: round(r["t"], 2) for r in todos(
        """SELECT x.status_pagamento, COALESCE(SUM(x.valor), 0) AS t
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? GROUP BY x.status_pagamento""", (obra_id,))}

    maiores_fornecedores = [dict(r) for r in todos(
        """SELECT COALESCE(NULLIF(x.fornecedor, ''), '(sem fornecedor)') AS fornecedor,
                  COALESCE(SUM(x.valor), 0) AS total, COUNT(*) AS itens
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? GROUP BY fornecedor ORDER BY total DESC LIMIT 6""", (obra_id,))]

    return jsonify({
        "hoje": hoje,
        "vencidas": vencidas,
        "proximas": proximas,
        "sem_vencimento": sem_data,
        "total_vencido": round(sum(x["valor"] for x in vencidas), 2),
        "total_proximas": round(sum(x["valor"] for x in proximas), 2),
        "total_em_aberto": round(sum(x["valor"] for x in abertas), 2),
        "por_categoria": por_categoria,
        "por_status": por_status,
        "maiores_fornecedores": maiores_fornecedores,
        "categorias_sugeridas": CATEGORIAS_PADRAO,
    })
