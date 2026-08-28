# -*- coding: utf-8 -*-
"""Diário de obra: semanas, RDO diário, fotos, medições e assinatura digital."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..db import executar, get_db, registrar_auditoria, todos, um
from ..security import (exigir_lancamento, garantir_acesso_obra, login_obrigatorio, obra_da_foto,
                        obra_da_meta, obra_da_semana, obra_do_dia, papel_obrigatorio, usuario_atual)
from ..services.fechamento import garantir_dias, listar_dias
from ..services.metas import arrastar_metas, criar_meta, listar_metas, registrar_progresso
from ..utils import (ApiErro, formatar_bytes, agora_iso, data_iso, hoje_iso, numero, remover_anexo, salvar_anexo,
                     segunda_da_semana, somar_dias, texto)

bp = Blueprint("diario", __name__, url_prefix="/api")



def _relatorio_compressao(originais: int, finais: int) -> dict:
    """Resumo do que a compressão economizou, para mostrar ao usuário."""
    economizado = max(originais - finais, 0)
    return {
        "original": formatar_bytes(originais),
        "final": formatar_bytes(finais),
        "economizado": formatar_bytes(economizado),
        "reducao_percentual": round((economizado / originais) * 100, 1) if originais else 0.0,
        "houve_ganho": economizado > 1024,
    }

MAX_FOTOS_POR_ENVIO = 10


# ---------------------------------------------------------------- semanas

@bp.get("/obras/<int:obra_id>/semanas")
@login_obrigatorio
def listar_semanas(obra_id: int):
    garantir_acesso_obra(obra_id)
    rows = todos(
        """SELECT id, numero, data_inicio, meta, meta_atingida, orcamento_previsto, fechada
             FROM semanas WHERE obra_id = ? ORDER BY numero""", (obra_id,))
    semanas = []
    for r in rows:
        item = dict(r)
        item["data_fim"] = somar_dias(r["data_inicio"], 6) if r["data_inicio"] else None
        item["atual"] = bool(r["data_inicio"] and r["data_inicio"] <= hoje_iso() <= item["data_fim"])
        semanas.append(item)
    return jsonify(semanas)


@bp.post("/obras/<int:obra_id>/semanas")
@papel_obrigatorio("gestor")
def criar_semana(obra_id: int):
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}

    ultima = um(
        "SELECT numero, data_inicio FROM semanas WHERE obra_id = ? ORDER BY numero DESC LIMIT 1",
        (obra_id,))
    numero_novo = (ultima["numero"] + 1) if ultima else 1
    # Por padrão a nova semana começa na segunda seguinte à última cadastrada.
    padrao = somar_dias(ultima["data_inicio"], 7) if (ultima and ultima["data_inicio"]) else segunda_da_semana()
    inicio = data_iso(dados.get("data_inicio"), padrao)

    if um("SELECT id FROM semanas WHERE obra_id = ? AND data_inicio = ?", (obra_id, inicio)):
        raise ApiErro("Já existe uma semana cadastrada começando nesta data.", 409)

    cur = executar(
        """INSERT INTO semanas (obra_id, numero, data_inicio, meta, meta_atingida,
                                orcamento_previsto, fechada, criado_em)
           VALUES (?, ?, ?, ?, 0, ?, 0, ?)""",
        (obra_id, numero_novo, inicio, texto(dados.get("meta"), 400),
         numero(dados.get("orcamento_previsto"), 0, 0, campo="o orçamento previsto"), agora_iso()),
    )
    semana_nova = cur.lastrowid
    garantir_dias(semana_nova)

    # Metas não concluídas da semana anterior vêm junto, com o contador de
    # arrasto somado. É automático de propósito: meta que "some" na virada da
    # semana é meta que ninguém cobra.
    arrastadas = 0
    if ultima:
        anterior = um("SELECT id FROM semanas WHERE obra_id = ? AND numero = ?",
                      (obra_id, ultima["numero"]))
        if anterior:
            arrastadas = arrastar_metas(obra_id, anterior["id"], semana_nova)

    registrar_auditoria(usuario_atual(), "criou_semana", "semana", semana_nova,
                        f"Semana {numero_novo}" + (f" · {arrastadas} meta(s) arrastada(s)" if arrastadas else ""))
    return jsonify({"id": semana_nova, "numero": numero_novo, "data_inicio": inicio,
                    "metas_arrastadas": arrastadas}), 201


@bp.put("/semanas/<int:semana_id>")
@login_obrigatorio
def atualizar_semana(semana_id: int):
    obra_da_semana(semana_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}

    campos, valores = [], []
    if "meta" in dados:
        campos.append("meta = ?"); valores.append(texto(dados["meta"], 400))
    if "meta_atingida" in dados:
        campos.append("meta_atingida = ?"); valores.append(1 if dados["meta_atingida"] else 0)
    if "fechada" in dados:
        campos.append("fechada = ?"); valores.append(1 if dados["fechada"] else 0)
    if "orcamento_previsto" in dados or "data_inicio" in dados:
        if usuario["papel"] != "gestor":
            raise ApiErro("Somente o gestor altera orçamento e datas da semana.", 403)
        if "orcamento_previsto" in dados:
            campos.append("orcamento_previsto = ?")
            valores.append(numero(dados["orcamento_previsto"], 0, 0, campo="o orçamento previsto"))
        if "data_inicio" in dados:
            campos.append("data_inicio = ?"); valores.append(data_iso(dados["data_inicio"], hoje_iso()))

    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(semana_id)
    executar(f"UPDATE semanas SET {', '.join(campos)} WHERE id = ?", valores)
    garantir_dias(semana_id)  # realinha as datas dos dias se o início mudou
    return jsonify({"ok": True})


@bp.delete("/semanas/<int:semana_id>")
@papel_obrigatorio("gestor")
def remover_semana(semana_id: int):
    obra_da_semana(semana_id)
    executar("DELETE FROM semanas WHERE id = ?", (semana_id,))
    registrar_auditoria(usuario_atual(), "excluiu_semana", "semana", semana_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- dias / RDO

@bp.get("/semanas/<int:semana_id>/dias")
@login_obrigatorio
def dias_da_semana(semana_id: int):
    obra_da_semana(semana_id)
    dias = listar_dias(semana_id)
    if usuario_atual()["papel"] == "mestre":
        for d in dias:
            d.pop("folha", None)
            d.pop("despesas", None)
    return jsonify(dias)


@bp.get("/dias/<int:dia_id>")
@login_obrigatorio
def detalhar_dia(dia_id: int):
    obra_do_dia(dia_id)
    row = um("SELECT * FROM dias WHERE id = ?", (dia_id,))
    dia = dict(row)
    dia["fotos"] = [
        {"id": f["id"], "url": f"/arquivos/{f['arquivo']}", "legenda": f["legenda"],
         "ambiente": f["ambiente"]}
        for f in todos("SELECT id, arquivo, legenda, ambiente FROM fotos WHERE dia_id = ? ORDER BY id",
                       (dia_id,))
    ]
    return jsonify(dia)


@bp.put("/dias/<int:dia_id>")
@login_obrigatorio
def salvar_rdo(dia_id: int):
    obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    executar(
        """UPDATE dias SET producao_dia = ?, clima = ?, observacoes = ?, atualizado_em = ?
            WHERE id = ?""",
        (texto(dados.get("producao_dia"), 4000), texto(dados.get("clima"), 60),
         texto(dados.get("observacoes"), 2000), agora_iso(), dia_id),
    )
    registrar_auditoria(usuario, "salvou_rdo", "dia", dia_id)
    return jsonify({"ok": True})


@bp.post("/dias/<int:dia_id>/assinatura")
@login_obrigatorio
def assinar_dia(dia_id: int):
    """Assinatura digital do RDO (carimbo de responsável + data/hora).

    Cumpre o papel de "documento formal" citado pela Resolução CONFEA
    1094/2017: quem assinou, quando, e sobre qual conteúdo.
    """
    obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    dia = um("SELECT producao_dia, assinado_em FROM dias WHERE id = ?", (dia_id,))
    if not (dia["producao_dia"] or "").strip():
        raise ApiErro("Preencha a produção do dia antes de assinar o RDO.")

    if request.args.get("remover") == "1":
        executar("UPDATE dias SET assinado_por = '', assinado_em = NULL WHERE id = ?", (dia_id,))
        return jsonify({"ok": True, "assinado": False})

    carimbo = agora_iso()
    executar("UPDATE dias SET assinado_por = ?, assinado_em = ? WHERE id = ?",
             (usuario["nome"], carimbo, dia_id))
    registrar_auditoria(usuario, "assinou_rdo", "dia", dia_id, carimbo)
    return jsonify({"ok": True, "assinado": True, "assinado_por": usuario["nome"], "assinado_em": carimbo})


@bp.get("/dias/<int:dia_id>/totais")
@login_obrigatorio
def totais_dia(dia_id: int):
    obra_do_dia(dia_id)
    folha = um("""SELECT COALESCE(SUM(horas_trabalhadas * valor_hora), 0) AS t,
                         COALESCE(SUM(horas_trabalhadas), 0) AS h
                    FROM horas WHERE dia_id = ?""", (dia_id,))
    despesas = um("SELECT COALESCE(SUM(valor), 0) AS t FROM despesas WHERE dia_id = ?", (dia_id,))
    resposta = {"horas": round(folha["h"], 2), "folha": round(folha["t"], 2),
                "despesas": round(despesas["t"], 2)}
    if usuario_atual()["papel"] == "mestre":
        resposta = {"horas": resposta["horas"]}
    return jsonify(resposta)


# ---------------------------------------------------------------- fotos

@bp.post("/dias/<int:dia_id>/fotos")
@login_obrigatorio
def enviar_fotos(dia_id: int):
    """Anexa fotos ao RDO — o recurso que dá valor probatório ao diário."""
    obra_do_dia(dia_id)
    usuario = exigir_lancamento()

    arquivos = request.files.getlist("fotos") or request.files.getlist("foto")
    arquivos = [a for a in arquivos if a and a.filename][:MAX_FOTOS_POR_ENVIO]
    if not arquivos:
        raise ApiErro("Selecione pelo menos uma foto.")

    legenda = texto(request.form.get("legenda"), 160)
    ambiente = texto(request.form.get("ambiente"), 80)

    db = get_db()
    criadas = []
    bytes_originais = bytes_finais = 0
    for arquivo in arquivos:
        meta = salvar_anexo(arquivo, apenas_imagem=True)
        if not meta:
            continue
        cur = db.execute(
            """INSERT INTO fotos (dia_id, arquivo, nome_original, legenda, ambiente,
                                  largura, altura, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (dia_id, meta["arquivo"], meta["nome_original"], legenda, ambiente,
             meta["largura"], meta["altura"], agora_iso()),
        )
        bytes_originais += meta["tamanho_original"]
        bytes_finais += meta["tamanho"]
        criadas.append({"id": cur.lastrowid, "url": f"/arquivos/{meta['arquivo']}",
                        "legenda": legenda, "ambiente": ambiente})
    db.commit()
    registrar_auditoria(usuario, "anexou_fotos", "dia", dia_id, f"{len(criadas)} foto(s)")
    return jsonify({
        "ok": True,
        "fotos": criadas,
        "compressao": _relatorio_compressao(bytes_originais, bytes_finais),
    }), 201


@bp.put("/fotos/<int:foto_id>")
@login_obrigatorio
def atualizar_foto(foto_id: int):
    obra_da_foto(foto_id)
    exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    executar("UPDATE fotos SET legenda = ?, ambiente = ? WHERE id = ?",
             (texto(dados.get("legenda"), 160), texto(dados.get("ambiente"), 80), foto_id))
    return jsonify({"ok": True})


@bp.delete("/fotos/<int:foto_id>")
@login_obrigatorio
def remover_foto(foto_id: int):
    obra_da_foto(foto_id)
    exigir_lancamento()
    row = um("SELECT arquivo FROM fotos WHERE id = ?", (foto_id,))
    executar("DELETE FROM fotos WHERE id = ?", (foto_id,))
    if row:
        remover_anexo(row["arquivo"])
    return jsonify({"ok": True})


@bp.get("/obras/<int:obra_id>/galeria")
@login_obrigatorio
def galeria(obra_id: int):
    """Álbum evolutivo da obra, agrupado por ambiente (suíte, fachada, etc.)."""
    garantir_acesso_obra(obra_id)
    ambiente = texto(request.args.get("ambiente"), 80)

    sql = """SELECT f.id, f.arquivo, f.legenda, f.ambiente, d.data, d.dia_semana, s.numero AS semana
               FROM fotos f
               JOIN dias d ON d.id = f.dia_id
               JOIN semanas s ON s.id = d.semana_id
              WHERE s.obra_id = ?"""
    args = [obra_id]
    if ambiente:
        sql += " AND f.ambiente = ?"; args.append(ambiente)
    sql += " ORDER BY d.data DESC, f.id DESC LIMIT 300"

    fotos = [{
        "id": r["id"], "url": f"/arquivos/{r['arquivo']}", "legenda": r["legenda"],
        "ambiente": r["ambiente"], "data": r["data"], "semana": r["semana"],
    } for r in todos(sql, args)]

    ambientes = [r["ambiente"] for r in todos(
        """SELECT DISTINCT f.ambiente FROM fotos f
             JOIN dias d ON d.id = f.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND f.ambiente <> '' ORDER BY f.ambiente""", (obra_id,))]

    return jsonify({"fotos": fotos, "ambientes": ambientes})


# ---------------------------------------------------------------- medições

@bp.get("/dias/<int:dia_id>/medicoes")
@login_obrigatorio
def listar_medicoes(dia_id: int):
    obra_do_dia(dia_id)
    rows = todos(
        """SELECT m.id, m.descricao, m.quantidade, m.unidade, m.etapa_id, e.nome AS etapa_nome
             FROM medicoes m LEFT JOIN etapas e ON e.id = m.etapa_id
            WHERE m.dia_id = ? ORDER BY m.id""", (dia_id,))
    return jsonify([dict(r) for r in rows])


@bp.post("/dias/<int:dia_id>/medicoes")
@login_obrigatorio
def criar_medicao(dia_id: int):
    obra_id = obra_do_dia(dia_id)
    exigir_lancamento()
    dados = request.get_json(silent=True) or {}

    etapa_id = dados.get("etapa_id")
    if etapa_id:
        etapa = um("SELECT obra_id FROM etapas WHERE id = ?", (etapa_id,))
        if not etapa or etapa["obra_id"] != obra_id:
            raise ApiErro("Etapa inválida para esta obra.")

    cur = executar(
        """INSERT INTO medicoes (dia_id, etapa_id, descricao, quantidade, unidade, criado_em)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (dia_id, etapa_id or None, texto(dados.get("descricao"), 160, True, "o serviço medido"),
         numero(dados.get("quantidade"), 0, 0, campo="a quantidade"),
         texto(dados.get("unidade"), 12) or "m²", agora_iso()),
    )
    return jsonify({"id": cur.lastrowid}), 201


@bp.delete("/medicoes/<int:medicao_id>")
@login_obrigatorio
def remover_medicao(medicao_id: int):
    row = um("SELECT dia_id FROM medicoes WHERE id = ?", (medicao_id,))
    if not row:
        raise ApiErro("Medição não encontrada.", 404)
    obra_do_dia(row["dia_id"])
    exigir_lancamento()
    executar("DELETE FROM medicoes WHERE id = ?", (medicao_id,))
    return jsonify({"ok": True})


# ---------------------------------------------------------------- metas

@bp.get("/semanas/<int:semana_id>/metas")
@login_obrigatorio
def listar_metas_semana(semana_id: int):
    obra_da_semana(semana_id)
    return jsonify(listar_metas(semana_id, com_progresso=True))


@bp.post("/semanas/<int:semana_id>/metas")
@login_obrigatorio
def criar_meta_semana(semana_id: int):
    exigir_lancamento()
    obra_id = obra_da_semana(semana_id)
    dados = request.get_json(silent=True) or {}
    meta_id = criar_meta(obra_id, semana_id, dados)
    registrar_auditoria(usuario_atual(), "criou_meta", "meta", meta_id, texto(dados.get("nome"), 120))
    return jsonify({"id": meta_id}), 201


@bp.put("/metas/<int:meta_id>")
@login_obrigatorio
def atualizar_meta(meta_id: int):
    exigir_lancamento()
    obra_da_meta(meta_id)
    dados = request.get_json(silent=True) or {}
    atual = um("SELECT * FROM metas WHERE id = ?", (meta_id,))

    nome = texto(dados.get("nome"), 200) or atual["nome"]
    percentual = numero(dados.get("percentual"), atual["percentual"], 0, 100, campo="o percentual")
    concluida = 1 if percentual >= 100 else 0

    executar(
        """UPDATE metas SET nome = ?, descricao = ?, percentual = ?, etapa_id = ?,
                            concluida = ?, concluida_em = ?, atualizado_em = ?
            WHERE id = ?""",
        (nome, texto(dados.get("descricao"), 400), percentual,
         dados.get("etapa_id") or None, concluida,
         atual["concluida_em"] or (hoje_iso() if concluida else None), agora_iso(), meta_id))
    registrar_auditoria(usuario_atual(), "editou_meta", "meta", meta_id, nome)
    return jsonify({"ok": True})


@bp.delete("/metas/<int:meta_id>")
@login_obrigatorio
def remover_meta(meta_id: int):
    exigir_lancamento()
    obra_da_meta(meta_id)
    meta = um("SELECT nome FROM metas WHERE id = ?", (meta_id,))
    executar("DELETE FROM metas WHERE id = ?", (meta_id,))
    registrar_auditoria(usuario_atual(), "removeu_meta", "meta", meta_id, meta["nome"] if meta else "")
    return jsonify({"ok": True})


@bp.get("/dias/<int:dia_id>/metas")
@login_obrigatorio
def metas_do_dia(dia_id: int):
    """Metas da semana com a marcação daquele dia — é a tela do canteiro."""
    obra_do_dia(dia_id)
    dia = um("SELECT semana_id FROM dias WHERE id = ?", (dia_id,))
    metas = listar_metas(dia["semana_id"], com_progresso=False)

    marcacoes = {
        linha["meta_id"]: dict(linha)
        for linha in todos(
            "SELECT meta_id, percentual, observacao FROM meta_progresso WHERE dia_id = ?", (dia_id,))
    }
    for meta in metas:
        marcado = marcacoes.get(meta["id"])
        meta["marcado_hoje"] = marcado is not None
        meta["percentual_dia"] = round(float(marcado["percentual"]), 1) if marcado else meta["percentual"]
        meta["observacao_dia"] = marcado["observacao"] if marcado else ""
    return jsonify(metas)


@bp.post("/dias/<int:dia_id>/metas/<int:meta_id>/progresso")
@login_obrigatorio
def progresso_meta(dia_id: int, meta_id: int):
    exigir_lancamento()
    obra_do_dia(dia_id)
    obra_da_meta(meta_id)
    dados = request.get_json(silent=True) or {}
    usuario = usuario_atual() or {}
    meta = registrar_progresso(meta_id, dia_id, dados, usuario.get("nome", ""))
    registrar_auditoria(usuario, "atualizou_meta", "meta", meta_id,
                        f"{meta['nome']} → {meta['percentual']:g}%")
    return jsonify(meta)
