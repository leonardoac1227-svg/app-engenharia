# -*- coding: utf-8 -*-
"""Diário de obra: semanas, dias e fotos do andamento."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request

from ..db import executar, get_db, registrar_auditoria, todos, um
from ..security import (exigir_financeiro, exigir_lancamento, garantir_acesso_obra,
                        login_obrigatorio, obra_da_foto,
                        obra_da_semana, obra_do_dia, papel_obrigatorio, usuario_atual)
from ..services.fechamento import garantir_dias, listar_dias
from ..services.parcelamento import alocar_parcelas_agendadas, sql_fora_da_semana
from ..services.semanas import excluir_semana
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


def _segunda_feira(valor, padrao):
    """Semana de obra vai de segunda a domingo: qualquer data vira a segunda dela.

    Sem isso, uma semana começando num domingo deixava o dia chamado
    "Segunda" com a data do domingo, e o diário inteiro ficava defasado.
    Domingo pertence à semana que começou na segunda anterior.
    """
    escolhida = data_iso(valor, padrao, "início da semana")
    return segunda_da_semana(date.fromisoformat(escolhida))


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
    # Por padrão a nova semana começa na segunda seguinte à última cadastrada.
    padrao = somar_dias(ultima["data_inicio"], 7) if (ultima and ultima["data_inicio"]) else segunda_da_semana()
    inicio = _segunda_feira(dados.get("data_inicio"), padrao)

    if um("SELECT id FROM semanas WHERE obra_id = ? AND data_inicio = ?", (obra_id, inicio)):
        raise ApiErro("Já existe uma semana cadastrada começando nesta data.", 409)

    criada = criar_semana_em(
        obra_id, inicio, texto(dados.get("meta"), 400),
        numero(dados.get("orcamento_previsto"), 0, 0, campo="o orçamento previsto"))
    return jsonify(criada), 201


def criar_semana_em(obra_id: int, inicio: str, meta: str = "", orcamento: float = 0) -> dict:
    """Cria a semana que começa em `inicio` (uma segunda-feira) com os seus 7 dias."""
    ultima = um("SELECT numero FROM semanas WHERE obra_id = ? ORDER BY numero DESC LIMIT 1", (obra_id,))
    numero_novo = (ultima["numero"] + 1) if ultima else 1
    cur = executar(
        """INSERT INTO semanas (obra_id, numero, data_inicio, meta, meta_atingida,
                                orcamento_previsto, fechada, criado_em)
           VALUES (?, ?, ?, ?, 0, ?, 0, ?)""",
        (obra_id, numero_novo, inicio, meta, orcamento, agora_iso()),
    )
    semana_nova = cur.lastrowid
    garantir_dias(semana_nova)
    # Parcelas que vencem nesta semana estavam agendadas: passam a contar aqui.
    parcelas = alocar_parcelas_agendadas(obra_id)

    registrar_auditoria(usuario_atual(), "criou_semana", "semana", semana_nova,
                        f"Semana {numero_novo}" + (f" · {parcelas} parcela(s) prevista(s)" if parcelas else ""))
    return {"id": semana_nova, "numero": numero_novo, "data_inicio": inicio,
            "parcelas_previstas": parcelas}


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
    quitar_folha = False
    if "fechada" in dados:
        fechando = 1 if dados["fechada"] else 0
        campos.append("fechada = ?"); valores.append(fechando)
        # Fechar a semana quita a folha: a mão de obra é acertada no fim da
        # semana, e o que fica pendente para a semana seguinte são as contas
        # de material, locação e terceiros — não o pagamento da equipe.
        quitar_folha = bool(fechando) and usuario["papel"] == "gestor"
    if "orcamento_previsto" in dados or "data_inicio" in dados:
        if usuario["papel"] != "gestor":
            raise ApiErro("Somente o gestor altera orçamento e datas da semana.", 403)
        if "orcamento_previsto" in dados:
            campos.append("orcamento_previsto = ?")
            valores.append(numero(dados["orcamento_previsto"], 0, 0, campo="o orçamento previsto"))
        if "data_inicio" in dados:
            campos.append("data_inicio = ?")
            valores.append(_segunda_feira(dados["data_inicio"], segunda_da_semana()))

    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(semana_id)
    executar(f"UPDATE semanas SET {', '.join(campos)} WHERE id = ?", valores)
    garantir_dias(semana_id)  # realinha as datas dos dias se o início mudou
    if "data_inicio" in dados:
        alocar_parcelas_agendadas(obra_da_semana(semana_id))

    resposta = {"ok": True}
    if quitar_folha:
        resposta["folha"] = quitar_folha_da_semana(semana_id, usuario)
    return jsonify(resposta)


@bp.delete("/semanas/<int:semana_id>")
@papel_obrigatorio("gestor")
def remover_semana(semana_id: int):
    obra_da_semana(semana_id)
    semana = um("SELECT numero, data_inicio FROM semanas WHERE id = ?", (semana_id,))
    resumo = excluir_semana(semana_id)
    registrar_auditoria(
        usuario_atual(), "excluiu_semana", "semana", semana_id,
        f"Semana {semana['numero']} ({semana['data_inicio']}) · {resumo['despesas']} lançamento(s)"
        f" · {resumo['fotos']} foto(s)")
    return jsonify({"ok": True, **resumo})


# ---------------------------------------------------------------- dias

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
def atualizar_dia(dia_id: int):
    """Relatório diário (RDO): serviços executados, clima e observações."""
    obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    limites = {"producao_dia": 6000, "clima": 200, "observacoes": 4000}
    campos = [c for c in limites if c in dados]
    if not campos:
        raise ApiErro("Nada para atualizar.")
    executar(
        f"UPDATE dias SET {', '.join(f'{c} = ?' for c in campos)}, atualizado_em = ? WHERE id = ?",
        [texto(dados[c], limites[c]) for c in campos] + [agora_iso(), dia_id])
    registrar_auditoria(usuario, "salvou_rdo", "dia", dia_id, ", ".join(campos))
    return jsonify({"ok": True})


@bp.get("/dias/<int:dia_id>/totais")
@login_obrigatorio
def totais_dia(dia_id: int):
    obra_do_dia(dia_id)
    folha = um("""SELECT COALESCE(SUM(horas_trabalhadas * valor_hora), 0) AS t,
                         COALESCE(SUM(horas_trabalhadas), 0) AS h
                    FROM horas WHERE dia_id = ?""", (dia_id,))
    despesas = um(f"""SELECT COALESCE(SUM(x.valor), 0) AS t
                         FROM despesas x JOIN dias d ON d.id = x.dia_id
                        WHERE x.dia_id = ? AND NOT {sql_fora_da_semana()}""", (dia_id,))
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


# ---------------------------------------------------------------- folha da semana

def quitar_folha_da_semana(semana_id: int, usuario: dict) -> dict:
    """Marca a folha da semana como paga.

    **As horas lançadas não são apagadas.** Zerar a folha apagando o ponto
    mudaria o custo daquela semana retroativamente, e o relatório já entregue
    ao cliente passaria a mostrar outro número. O que se registra aqui é o
    pagamento: o histórico continua inteiro, e o que zera é o valor pendente.

    Nada além da folha é tocado — contas de material, locação e medições de
    terceiros seguem em aberto até serem pagas uma a uma.
    """
    total = um(
        """SELECT COALESCE(SUM(h.horas_trabalhadas * h.valor_hora), 0) AS total
             FROM horas h JOIN dias d ON d.id = h.dia_id
            WHERE d.semana_id = ?""", (semana_id,))["total"]
    total = round(float(total or 0), 2)

    executar(
        """UPDATE semanas SET folha_paga = 1, folha_paga_em = ?, folha_valor_pago = ?
            WHERE id = ?""",
        (hoje_iso(), total, semana_id))
    registrar_auditoria(usuario, "quitou_folha", "semana", semana_id, f"R$ {total:.2f}")
    return {"paga": True, "valor": total, "data": hoje_iso()}


@bp.post("/semanas/<int:semana_id>/folha/pagar")
@login_obrigatorio
def pagar_folha(semana_id: int):
    """Quita a folha sem precisar fechar a semana."""
    usuario = exigir_financeiro()
    obra_da_semana(semana_id)
    return jsonify(quitar_folha_da_semana(semana_id, usuario)), 200


@bp.delete("/semanas/<int:semana_id>/folha/pagar")
@login_obrigatorio
def reabrir_folha(semana_id: int):
    """Desfaz a quitação (pagamento lançado por engano)."""
    usuario = exigir_financeiro()
    obra_da_semana(semana_id)
    executar(
        "UPDATE semanas SET folha_paga = 0, folha_paga_em = NULL, folha_valor_pago = 0 WHERE id = ?",
        (semana_id,))
    registrar_auditoria(usuario, "reabriu_folha", "semana", semana_id, "")
    return jsonify({"paga": False})
