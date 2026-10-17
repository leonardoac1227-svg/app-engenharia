# -*- coding: utf-8 -*-
"""Caixa da obra: lançamentos, anexos, contas a pagar e fluxo de caixa.

Todo este módulo é exclusivo do papel *gestor* — o mestre de obra lança RDO e
ponto, mas não vê nem movimenta dinheiro.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..config import CATEGORIAS_PADRAO, REEMBOLSO_PADRAO, STATUS_PAGAMENTO
from ..db import executar, registrar_auditoria, todos, um
from ..security import (exigir_financeiro, garantir_acesso_obra, login_obrigatorio,
                        obra_da_despesa, obra_do_dia)
from ..services import anexos_despesa
from ..services.locacoes import criar_locacao
from ..services.fretes import gerar_fretes, listar_grupo as listar_fretes
from ..services.fretes import validar as validar_frete
from ..services.parcelamento import gerar_parcelas, listar_grupo, remover_grupo, sql_fora_da_semana
from ..services.parcelamento import validar as validar_parcelamento
from ..utils import (ApiErro, agora_iso, data_br, data_iso, formatar_bytes, hoje_iso, moeda,
                     numero, somar_dias, texto)

bp = Blueprint("financeiro", __name__, url_prefix="/api")



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


def _serializar(r) -> dict:
    item = dict(r)
    item["anexo_url"] = f"/arquivos/{r['imagem_path']}" if r["imagem_path"] else ""
    item["vencida"] = bool(
        r["status_pagamento"] == "Em Aberto" and r["vencimento"] and r["vencimento"] < hoje_iso())
    return item



def _nota_ja_lancada(obra_id: int, numero_nota: str, fornecedor: str, ignorar_id: int | None = None):
    """Procura uma despesa com a mesma nota do mesmo fornecedor nesta obra.

    Nota fiscal repetida quase sempre significa pagamento em duplicidade — o
    erro financeiro mais caro e mais silencioso de uma obra, porque os dois
    lançamentos parecem legítimos isoladamente. Aqui a checagem é por nota +
    fornecedor: o mesmo número emitido por fornecedores diferentes é comum e
    não deve travar nada.
    """
    if not numero_nota:
        return None

    condicao = " AND x.id != ?" if ignorar_id else ""
    parametros = [obra_id, numero_nota.strip().lower(), (fornecedor or "").strip().lower()]
    if ignorar_id:
        parametros.append(ignorar_id)

    return um(
        f"""SELECT x.id, x.descricao, x.valor, x.numero_nota, x.fornecedor,
                   x.grupo_parcela, d.data
              FROM despesas x
              JOIN dias d ON d.id = x.dia_id
              JOIN semanas s ON s.id = d.semana_id
             WHERE s.obra_id = ?
               AND LOWER(TRIM(x.numero_nota)) = ?
               AND LOWER(TRIM(COALESCE(x.fornecedor, ''))) = ?{condicao}
             ORDER BY x.id DESC LIMIT 1""",
        parametros)


def _barrar_nota_duplicada(obra_id, numero_nota, fornecedor, confirmado, ignorar_id=None):
    """Bloqueia o lançamento repetido, mas deixa o gestor confirmar.

    Existe caso legítimo (nota parcelada lançada em duas competências), então
    o app informa o que já existe e permite seguir — não decide sozinho.
    """
    if confirmado:
        return
    existente = _nota_ja_lancada(obra_id, numero_nota, fornecedor, ignorar_id)
    if not existente:
        return
    if existente["grupo_parcela"]:
        # Parcela da mesma nota não é lançamento repetido: é a segunda via do
        # mesmo compromisso, com outro vencimento.
        return
    raise ApiErro(
        f"A nota {numero_nota} de {existente['fornecedor'] or 'mesmo fornecedor'} já foi lançada "
        f"em {data_br(existente['data'])}: {existente['descricao']} · {moeda(existente['valor'])}.",
        409, "nota_duplicada")


def _quem_recebe_reembolso(status: str, nome) -> str:
    """Nome de quem adiantou o dinheiro. Só existe em lançamento de reembolso."""
    if status != "Reembolso":
        return ""
    return texto(nome, 80) or REEMBOLSO_PADRAO


@bp.get("/dias/<int:dia_id>/despesas")
def listar(dia_id: int):
    exigir_financeiro()
    obra_do_dia(dia_id)
    rows = todos(
        f"""SELECT x.id, x.categoria, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                  x.numero_nota, x.grupo_parcela, x.grupo_tipo, x.parcela_numero, x.parcela_total,
                  x.vencimento, x.pago_em, x.imagem_path, x.nome_arquivo, x.reembolsar_para,
                  x.dia_id
             FROM despesas x JOIN dias d ON d.id = x.dia_id
            WHERE x.dia_id = ? AND NOT {sql_fora_da_semana()} ORDER BY x.id DESC""", (dia_id,))
    return jsonify(anexos_despesa.anexar_lista([_serializar(r) for r in rows]))


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

    vencimento = data_iso(request.form.get("vencimento"), None, "vencimento")
    pago_em = hoje_iso() if status == "Pago" else None
    reembolsar_para = _quem_recebe_reembolso(status, request.form.get("reembolsar_para"))

    fornecedor = texto(request.form.get("fornecedor"), 120)
    numero_nota = texto(request.form.get("numero_nota"), 40)
    _barrar_nota_duplicada(obra_id, numero_nota, fornecedor,
                           request.form.get("confirmar_duplicidade") in ("1", "true", "True"))

    # Vários arquivos vêm em "anexos"; "imagem"/"anexo" é o envio de um só,
    # das versões anteriores do app (ainda abertas em algum celular).
    uploads = [*request.files.getlist("anexos"),
               request.files.get("imagem") or request.files.get("anexo")]
    uploads = [u for u in uploads if u and u.filename]
    if len(uploads) > anexos_despesa.LIMITE_POR_LANCAMENTO:
        raise ApiErro(f"Cada lançamento aceita até {anexos_despesa.LIMITE_POR_LANCAMENTO} anexos.")

    cur = executar(
        """INSERT INTO despesas (dia_id, categoria, descricao, fornecedor, valor,
                                 status_pagamento, numero_nota, vencimento, pago_em, imagem_path,
                                 nome_arquivo, reembolsar_para, criado_por, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?, ?)""",
        (dia_id, categoria, descricao,
         fornecedor, valor, status, numero_nota, vencimento, pago_em, reembolsar_para,
         usuario["id"], agora_iso()),
    )
    gravados = anexos_despesa.adicionar(cur.lastrowid, uploads)
    registrar_auditoria(usuario, "lancou_despesa", "despesa", cur.lastrowid,
                        f"{descricao} · {valor:.2f} · {status}")
    frete = validar_frete(request.form) if categoria == "Frete" else None
    if frete:
        resposta_frete = gerar_fretes(cur.lastrowid, obra_id, frete, usuario["id"])
        registrar_auditoria(usuario, "lancou_frete", "despesa", cur.lastrowid,
                            f"{frete['transportador']} · {frete['viagens']} viagem(ns)")

    parcelamento = None if frete else validar_parcelamento(request.form)
    if parcelamento:
        resposta_parcelas = gerar_parcelas(cur.lastrowid, obra_id, parcelamento, usuario["id"])
        registrar_auditoria(usuario, "parcelou_nota", "despesa", cur.lastrowid,
                            f"{resposta_parcelas['parcelas']}x de "
                            f"{moeda(resposta_parcelas['valor_parcela'])}")

    resposta = {"id": cur.lastrowid}
    if frete:
        resposta["frete"] = resposta_frete
    if parcelamento:
        resposta["parcelamento"] = resposta_parcelas
    if gravados:
        resposta["compressao"] = _relatorio_compressao(
            sum(m["tamanho_original"] for m in gravados), sum(m["tamanho"] for m in gravados))
        resposta["anexos"] = len(gravados)

    # Locação com recorrência: a despesa recém-lançada é a 1ª parcela, e o
    # cronograma das renovações seguintes nasce junto.
    if categoria == "Locação" and request.form.get("frequencia"):
        locacao_id = criar_locacao(obra_id, {
            "equipamento": request.form.get("equipamento") or descricao,
            "fornecedor": request.form.get("fornecedor"),
            "valor_parcela": valor,
            "frequencia": request.form.get("frequencia"),
            "ciclos": request.form.get("ciclos"),
            "data_inicio": request.form.get("data_locacao") or vencimento or hoje_iso(),
            "observacoes": request.form.get("observacoes_locacao"),
        }, despesa_id=cur.lastrowid)
        resposta["locacao_id"] = locacao_id
        registrar_auditoria(usuario, "criou_locacao", "locacao", locacao_id, descricao)

    return jsonify(resposta), 201


@bp.put("/despesas/<int:despesa_id>")
def atualizar(despesa_id: int):
    usuario = exigir_financeiro()
    obra_id = obra_da_despesa(despesa_id)
    dados = request.get_json(silent=True) or {}

    campos, valores = [], []
    if "dia_id" in dados:
        # Lançou no dia errado: só pode ir para outro dia da mesma obra.
        novo_dia = int(numero(dados["dia_id"], 0, 1, campo="o dia"))
        if obra_do_dia(novo_dia) != obra_id:
            raise ApiErro("O dia escolhido é de outra obra.")
        campos.append("dia_id = ?"); valores.append(novo_dia)
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
    if "status_pagamento" in dados:
        status = texto(dados["status_pagamento"], 20)
        if status not in STATUS_PAGAMENTO:
            raise ApiErro("Status de pagamento inválido.")
        campos.append("status_pagamento = ?"); valores.append(status)
        campos.append("pago_em = ?")
        valores.append(data_iso(dados.get("pago_em"), hoje_iso()) if status == "Pago" else None)
        campos.append("reembolsar_para = ?")
        valores.append(_quem_recebe_reembolso(status, dados.get("reembolsar_para")))
    elif "reembolsar_para" in dados:
        atual = um("SELECT status_pagamento FROM despesas WHERE id = ?", (despesa_id,))
        campos.append("reembolsar_para = ?")
        valores.append(_quem_recebe_reembolso(atual["status_pagamento"] if atual else "",
                                              dados.get("reembolsar_para")))

    if "numero_nota" in dados:
        numero_nota = texto(dados["numero_nota"], 40)
        atual = um("""SELECT x.fornecedor, s.obra_id AS obra_id
                        FROM despesas x JOIN dias d ON d.id = x.dia_id
                        JOIN semanas s ON s.id = d.semana_id WHERE x.id = ?""", (despesa_id,))
        fornecedor = dados.get("fornecedor", atual["fornecedor"] if atual else "")
        _barrar_nota_duplicada(atual["obra_id"] if atual else 0, numero_nota, fornecedor,
                               bool(dados.get("confirmar_duplicidade")), ignorar_id=despesa_id)
        campos.append("numero_nota = ?"); valores.append(numero_nota)

    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(despesa_id)
    executar(f"UPDATE despesas SET {', '.join(campos)} WHERE id = ?", valores)
    registrar_auditoria(usuario, "editou_despesa", "despesa", despesa_id)
    return jsonify({"ok": True})


@bp.post("/despesas/<int:despesa_id>/anexos")
def adicionar_anexos(despesa_id: int):
    """Acrescenta arquivos a um lançamento que já existe."""
    usuario = exigir_financeiro()
    obra_da_despesa(despesa_id)
    uploads = request.files.getlist("anexos") or [request.files.get("anexo")]
    gravados = anexos_despesa.adicionar(despesa_id, uploads)
    if not gravados:
        raise ApiErro("Nenhum arquivo recebido.")
    registrar_auditoria(usuario, "anexou_despesa", "despesa", despesa_id,
                        ", ".join(m["nome_original"] for m in gravados))
    return jsonify({
        "anexos": anexos_despesa.anexos_por_despesa([despesa_id]).get(despesa_id, []),
        "compressao": _relatorio_compressao(sum(m["tamanho_original"] for m in gravados),
                                            sum(m["tamanho"] for m in gravados)),
    }), 201


@bp.delete("/despesas/<int:despesa_id>/anexos/<int:anexo_id>")
def remover_anexo_da_despesa(despesa_id: int, anexo_id: int):
    usuario = exigir_financeiro()
    obra_da_despesa(despesa_id)
    anexos_despesa.remover(despesa_id, anexo_id)
    registrar_auditoria(usuario, "removeu_anexo", "despesa", despesa_id, str(anexo_id))
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
    row = um("SELECT descricao FROM despesas WHERE id = ?", (despesa_id,))
    arquivos = anexos_despesa.arquivos_da_despesa(despesa_id)
    executar("DELETE FROM despesa_anexos WHERE despesa_id = ?", (despesa_id,))
    executar("DELETE FROM despesas WHERE id = ?", (despesa_id,))
    # Parcelas da mesma nota dividem o arquivo: só some quando ninguém mais usa.
    anexos_despesa.apagar_arquivos_orfaos(arquivos)
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
                  x.vencimento, x.pago_em, x.imagem_path, x.nome_arquivo, x.reembolsar_para,
                  d.data, s.numero AS semana
             FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND x.status_pagamento = 'Em Aberto'
            ORDER BY (x.vencimento IS NULL), x.vencimento, x.id""", (obra_id,))]
    anexos_despesa.anexar_lista(abertas)

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


@bp.get("/despesas/<int:despesa_id>/parcelas")
@login_obrigatorio
def parcelas_da_despesa(despesa_id: int):
    """Todas as parcelas da mesma nota, para conferência antes de cancelar."""
    exigir_financeiro()
    obra_da_despesa(despesa_id)
    despesa = um("SELECT grupo_parcela FROM despesas WHERE id = ?", (despesa_id,))
    if not despesa or not despesa["grupo_parcela"]:
        return jsonify([])
    return jsonify(listar_grupo(despesa["grupo_parcela"]))


@bp.delete("/despesas/<int:despesa_id>/parcelas")
@login_obrigatorio
def cancelar_parcelamento(despesa_id: int):
    """Cancela as parcelas em aberto. As já pagas continuam no caixa."""
    usuario = exigir_financeiro()
    obra_da_despesa(despesa_id)
    despesa = um("SELECT grupo_parcela FROM despesas WHERE id = ?", (despesa_id,))
    if not despesa or not despesa["grupo_parcela"]:
        raise ApiErro("Este lançamento não faz parte de um parcelamento.")

    removidas = remover_grupo(despesa["grupo_parcela"])
    registrar_auditoria(usuario, "cancelou_parcelamento", "despesa", despesa_id,
                        f"{removidas} parcela(s) em aberto removida(s)")
    return jsonify({"ok": True, "removidas": removidas})


@bp.post("/semanas/<int:semana_id>/despesas/pagar-todas")
@login_obrigatorio
def pagar_todas_da_semana(semana_id: int):
    """Baixa de uma vez as contas em aberto da semana.

    Na sexta o gestor paga o bolo de notas do fornecedor e precisa refletir
    isso no app — dar baixa em quinze lançamentos, um a um, é o tipo de
    trabalho que faz o app ser abandonado.

    Só entram as despesas **em aberto daquela semana**. Reembolsos ficam de
    fora por padrão: eles são acerto com quem adiantou o dinheiro, não
    pagamento a fornecedor, e costumam sair em outro momento — mas quem
    quiser incluí-los manda `incluir_reembolsos`.
    """
    from ..security import obra_da_semana

    usuario = exigir_financeiro()
    obra_da_semana(semana_id)
    dados = request.get_json(silent=True) or {}

    status_alvo = ["Em Aberto"]
    if dados.get("incluir_reembolsos"):
        status_alvo.append("Reembolso")

    marcas = ", ".join("?" for _ in status_alvo)
    # Parcela agendada para uma semana futura não é conta desta semana.
    pendentes = todos(
        f"""SELECT x.id, x.valor FROM despesas x
              JOIN dias d ON d.id = x.dia_id
             WHERE d.semana_id = ? AND x.status_pagamento IN ({marcas})
               AND NOT {sql_fora_da_semana()}""",
        [semana_id, *status_alvo])

    if not pendentes:
        return jsonify({"quitadas": 0, "total": 0.0,
                        "mensagem": "Nenhuma conta em aberto nesta semana."})

    hoje = hoje_iso()
    executar(
        f"""UPDATE despesas SET status_pagamento = 'Pago', pago_em = ?
             WHERE id IN (SELECT x.id FROM despesas x JOIN dias d ON d.id = x.dia_id
                           WHERE d.semana_id = ? AND x.status_pagamento IN ({marcas})
                             AND NOT {sql_fora_da_semana()})""",
        [hoje, semana_id, *status_alvo])

    total = round(sum(float(p["valor"] or 0) for p in pendentes), 2)
    registrar_auditoria(usuario, "pagou_todas_despesas", "semana", semana_id,
                        f"{len(pendentes)} conta(s) · {moeda(total)}")

    return jsonify({"quitadas": len(pendentes), "total": total, "pago_em": hoje})
