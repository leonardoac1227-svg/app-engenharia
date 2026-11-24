# -*- coding: utf-8 -*-
"""Equipe: cadastro de funcionários e lançamento de ponto.

O ponto controla a presença; o custo da mão de obra que vai na planilha sai
dele pelo vínculo de cada um, fichado ou avulso (ver `services/mao_de_obra.py`).

Avulso: quem passa uma ou duas diárias numa obra que não é a dele. O ponto
fica na obra onde ele trabalhou, sem mudar a equipe de nenhuma das duas. Se
ele é de outra obra, o registro aponta para o cadastro de lá; se não tem
cadastro, nasce um cadastro marcado como avulso e inativo, que não aparece
nas listas da equipe nem conta falta nos outros dias.
Abaixo da jornada (8 h), o ponto guarda a justificativa da falta; uma falta
justificada fica registrada mesmo com zero hora (ver `services/ponto.py`).
Pontos antigos guardam o valor/hora da época (`horas.valor_hora`) e esse valor
é preservado ao reeditar o dia, para não mudar fechamentos já entregues.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..db import executar, get_db, registrar_auditoria, todos, um
from ..security import (exigir_lancamento, garantir_acesso_obra, login_obrigatorio,
                        obra_do_funcionario, obra_do_dia, obras_permitidas, papel_obrigatorio,
                        usuario_atual)
from ..services.mao_de_obra import TABELA, VINCULOS, condicoes
from ..utils import ApiErro, agora_iso, data_iso, hoje_iso, numero, texto

bp = Blueprint("equipe", __name__, url_prefix="/api")



@bp.get("/obras/<int:obra_id>/funcionarios")
@login_obrigatorio
def listar(obra_id: int):
    garantir_acesso_obra(obra_id)
    incluir_inativos = request.args.get("inativos") == "1"
    sql = """SELECT f.id, f.nome, f.funcao, f.documento, f.telefone, f.ativo, f.chave_pix,
                    f.vinculo, f.classe, f.diaria, f.avulso
               FROM funcionarios f WHERE f.obra_id = ?"""
    if not incluir_inativos:
        sql += " AND f.ativo = 1"
    sql += " ORDER BY f.ativo DESC, f.nome COLLATE NOCASE"
    linhas = [dict(linha) for linha in todos(sql, (obra_id,))]
    # Quanto cada um ganha é só do gestor: mestre e cliente também abrem esta lista.
    gestor = usuario_atual()["papel"] == "gestor"
    for linha in linhas:
        cond = condicoes(linha)
        for campo in ("vinculo", "classe", "diaria", "avulso"):
            linha.pop(campo) if not gestor or campo == "avulso" else None
        if gestor:
            linha["vinculo"] = cond["vinculo"]
            linha["definido"] = cond["definido"]
            if cond["vinculo"] == "avulso":
                linha["diaria"] = cond["liquido"]
    return jsonify(linhas)


def _vinculo(valor) -> str:
    v = texto(valor, 20).lower()
    if v and v not in VINCULOS:
        raise ApiErro("Vínculo inválido: use fichado ou avulso.")
    return v


def _classe(valor) -> str:
    v = texto(valor, 30).lower()
    if v and v not in TABELA:
        raise ApiErro("Classe inválida.")
    return v


def _diaria(valor) -> float:
    return numero(valor, 0, 0, 5000, "a diária")


@bp.get("/mao-de-obra/tabela")
@papel_obrigatorio("gestor")
def tabela_mao_de_obra():
    return jsonify([{"id": k, **v} for k, v in TABELA.items()])


@bp.post("/funcionarios/<int:funcionario_id>/transferir")
@papel_obrigatorio("gestor")
def transferir(funcionario_id: int):
    """Muda a pessoa de obra de vez.

    Sai da equipe antiga com o histórico intacto e entra na nova com os mesmos
    dados. `desde` evita que ela apareça com falta, ou no "efetivo todo", em dias
    de antes da mudança.
    """
    origem_id = obra_do_funcionario(funcionario_id)
    dados = request.get_json(silent=True) or {}
    destino = int(numero(dados.get("obra_id"), 0, 0, campo="a obra"))
    obra = um("SELECT id, nome FROM obras WHERE id = ? AND arquivada = 0", (destino,))
    if not obra:
        raise ApiErro("Obra de destino não encontrada.", 404)
    garantir_acesso_obra(destino)
    if destino == origem_id:
        raise ApiErro("A pessoa já é desta obra.")
    desde = data_iso(dados.get("data"), hoje_iso(), "a data")
    f = dict(um("SELECT * FROM funcionarios WHERE id = ?", (funcionario_id,)))
    if not f["ativo"]:
        raise ApiErro(f"{f['nome']} não está na equipe ativa desta obra.")
    antigo = next((dict(x) for x in todos("SELECT id, nome, ativo FROM funcionarios WHERE obra_id = ?", (destino,))
                   if _sem_acento(x["nome"]) == _sem_acento(f["nome"])), None)
    if antigo and antigo["ativo"]:
        raise ApiErro(f"{f['nome']} já está na equipe de {obra['nome']}.", 409)

    campos = ("funcao", "chave_pix", "documento", "telefone", "vinculo", "classe", "diaria")
    db = get_db()
    if antigo:
        novo_id = antigo["id"]
        db.execute(f"UPDATE funcionarios SET ativo = 1, avulso = 0, desde = ?, "
                   f"{', '.join(c + ' = ?' for c in campos)} WHERE id = ?",
                   [desde, *[f[c] for c in campos], novo_id])
    else:
        novo_id = db.execute(
            f"""INSERT INTO funcionarios (obra_id, nome, tipo_contrato, valor_hora, ativo, avulso, desde, criado_em,
                                          {', '.join(campos)})
                VALUES (?, ?, 'fixo', 0, 1, 0, ?, ?, {', '.join('?' for _ in campos)})""",
            [destino, f["nome"], desde, agora_iso(), *[f[c] for c in campos]]).lastrowid
    db.execute("UPDATE funcionarios SET ativo = 0 WHERE id = ?", (funcionario_id,))
    db.commit()
    registrar_auditoria(usuario_atual(), "transferiu_funcionario", "funcionario", funcionario_id,
                        f"{f['nome']} → {obra['nome']}")
    return jsonify({"ok": True, "id": novo_id})


@bp.post("/obras/<int:obra_id>/funcionarios")
@papel_obrigatorio("gestor")
def criar(obra_id: int):
    garantir_acesso_obra(obra_id)
    dados = request.get_json(silent=True) or {}
    nome = texto(dados.get("nome"), 120, True, "o nome do funcionário")

    cur = executar(
        """INSERT INTO funcionarios (obra_id, nome, funcao, tipo_contrato, chave_pix, valor_hora,
                                     documento, telefone, ativo, vinculo, classe, diaria, desde,
                                     criado_em)
           VALUES (?, ?, ?, 'fixo', ?, 0, ?, ?, 1, ?, ?, ?, ?, ?)""",
        (obra_id, nome, texto(dados.get("funcao"), 80),
         texto(dados.get("chave_pix"), 120),
         texto(dados.get("documento"), 40), texto(dados.get("telefone"), 40),
         _vinculo(dados.get("vinculo")), _classe(dados.get("classe")), _diaria(dados.get("diaria")),
         data_iso(dados.get("desde"), None, "a data de entrada") or "", agora_iso()),
    )
    registrar_auditoria(usuario_atual(), "criou_funcionario", "funcionario", cur.lastrowid, nome)
    return jsonify({"id": cur.lastrowid}), 201


@bp.put("/funcionarios/<int:funcionario_id>")
@papel_obrigatorio("gestor")
def atualizar(funcionario_id: int):
    obra_do_funcionario(funcionario_id)
    dados = request.get_json(silent=True) or {}
    mapa = {
        "nome": lambda v: texto(v, 120, True, "o nome do funcionário"),
        "funcao": lambda v: texto(v, 80),
        "chave_pix": lambda v: texto(v, 120),
        "documento": lambda v: texto(v, 40),
        "telefone": lambda v: texto(v, 40),
        "ativo": lambda v: 1 if v else 0,
        "vinculo": _vinculo,
        "classe": _classe,
        "diaria": _diaria,
    }
    campos, valores = [], []
    for chave, conversor in mapa.items():
        if chave in dados:
            campos.append(f"{chave} = ?"); valores.append(conversor(dados[chave]))
    if not campos:
        raise ApiErro("Nada para atualizar.")
    valores.append(funcionario_id)
    executar(f"UPDATE funcionarios SET {', '.join(campos)} WHERE id = ?", valores)
    registrar_auditoria(usuario_atual(), "editou_funcionario", "funcionario", funcionario_id)
    return jsonify({"ok": True})


@bp.delete("/funcionarios/<int:funcionario_id>")
@papel_obrigatorio("gestor")
def desativar(funcionario_id: int):
    """Soft-delete: preserva o histórico de horas das semanas já lançadas."""
    obra_do_funcionario(funcionario_id)
    executar("UPDATE funcionarios SET ativo = 0 WHERE id = ?", (funcionario_id,))
    registrar_auditoria(usuario_atual(), "desativou_funcionario", "funcionario", funcionario_id)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- ponto

@bp.get("/dias/<int:dia_id>/horas")
@login_obrigatorio
def obter_horas(dia_id: int):
    obra_do_dia(dia_id)
    rows = todos("SELECT funcionario_id, horas_trabalhadas, justificativa FROM horas WHERE dia_id = ?",
                 (dia_id,))
    return jsonify({str(r["funcionario_id"]): {"horas": r["horas_trabalhadas"],
                                              "justificativa": r["justificativa"] or ""}
                    for r in rows})


@bp.put("/dias/<int:dia_id>/horas")
@login_obrigatorio
def salvar_horas(dia_id: int):
    obra_id = obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    registros = dados.get("registros", [])
    if not isinstance(registros, list):
        raise ApiErro("Formato de registros inválido.")

    validos = {r["id"] for r in todos("SELECT id FROM funcionarios WHERE obra_id = ?", (obra_id,))}
    # Avulsos já lançados neste dia podem ter as horas corrigidas ou ser removidos pela mesma tela.
    validos |= {r["funcionario_id"] for r in todos("SELECT funcionario_id FROM horas WHERE dia_id = ?", (dia_id,))}
    db = get_db()
    total = 0.0
    for reg in registros:
        funcionario_id = int(reg.get("funcionario_id") or 0)
        if funcionario_id not in validos:
            continue
        horas = numero(reg.get("horas"), 0, 0, 24, "as horas")
        # Jornada cumprida não tem o que justificar.
        justificativa = "" if horas >= config.JORNADA_HORAS else texto(reg.get("justificativa"), 160)
        if horas <= 0 and not justificativa:
            db.execute("DELETE FROM horas WHERE dia_id = ? AND funcionario_id = ?", (dia_id, funcionario_id))
            continue
        # Ponto novo entra com valor/hora zero (só presença); o de um dia
        # antigo mantém o valor gravado na época. Falta justificada fica com
        # zero hora, para o motivo não se perder.
        db.execute(
            """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas, valor_hora, justificativa)
               VALUES (?, ?, ?, 0, ?)
               ON CONFLICT(dia_id, funcionario_id)
               DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas,
                             justificativa = excluded.justificativa""",
            (dia_id, funcionario_id, horas, justificativa),
        )
        total += horas
    db.commit()
    registrar_auditoria(usuario, "salvou_ponto", "dia", dia_id, f"{total:g} h")
    return jsonify({"ok": True, "total_horas": round(total, 2)})


def _sem_acento(valor: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", valor or "")
                   if unicodedata.category(c) != "Mn").lower().strip()


def _achar_avulso(obra_id: int, nome: str) -> dict | None:
    """Mesmo nome já cadastrado: primeiro em outra obra (é dela que ele veio), depois avulso desta."""
    alvo = _sem_acento(nome)
    permitidas = obras_permitidas()
    candidatos = [dict(r) for r in todos(
        """SELECT id, obra_id, nome, avulso, ativo FROM funcionarios
            WHERE (obra_id <> ? AND ativo = 1) OR (obra_id = ? AND avulso = 1)""", (obra_id, obra_id))
        if permitidas is None or r["obra_id"] in permitidas or r["obra_id"] == obra_id]
    exatos = [c for c in candidatos if _sem_acento(c["nome"]) == alvo]
    if len(exatos) == 1:
        return exatos[0]
    primeiro_nome = [c for c in candidatos if _sem_acento(c["nome"]).split(" ")[0] == alvo.split(" ")[0]]
    return primeiro_nome[0] if len(primeiro_nome) == 1 and " " not in alvo else None


@bp.post("/dias/<int:dia_id>/horas/avulso")
@login_obrigatorio
def lancar_avulso(dia_id: int):
    """Diária de quem não é desta obra: de outra obra (pelo id ou pelo nome) ou sem cadastro nenhum."""
    obra_id = obra_do_dia(dia_id)
    usuario = exigir_lancamento()
    dados = request.get_json(silent=True) or {}
    horas = numero(dados.get("horas"), config.JORNADA_HORAS, 0, 24, "as horas")
    justificativa = "" if horas >= config.JORNADA_HORAS else texto(dados.get("justificativa"), 160)

    funcionario_id = int(numero(dados.get("funcionario_id"), 0, 0, campo="o funcionário"))
    if funcionario_id:
        origem = um("SELECT obra_id FROM funcionarios WHERE id = ?", (funcionario_id,))
        if not origem:
            raise ApiErro("Funcionário não encontrado.", 404)
        garantir_acesso_obra(origem["obra_id"])
    else:
        nome = texto(dados.get("nome"), 120, True, "o nome do avulso")
        achado = _achar_avulso(obra_id, nome)
        if achado:
            funcionario_id = achado["id"]
        else:
            funcionario_id = executar(
                """INSERT INTO funcionarios (obra_id, nome, funcao, tipo_contrato, chave_pix, valor_hora,
                                             documento, telefone, ativo, avulso, criado_em)
                   VALUES (?, ?, ?, 'fixo', '', 0, '', '', 0, 1, ?)""",
                (obra_id, nome, texto(dados.get("funcao"), 80) or "Avulso", agora_iso())).lastrowid

    executar(
        """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas, valor_hora, justificativa)
           VALUES (?, ?, ?, 0, ?)
           ON CONFLICT(dia_id, funcionario_id)
           DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas, justificativa = excluded.justificativa""",
        (dia_id, funcionario_id, horas, justificativa))
    registrar_auditoria(usuario, "lancou_avulso", "dia", dia_id, f"funcionário {funcionario_id} · {horas:g} h")
    return jsonify({"ok": True, "funcionario_id": funcionario_id}), 201


@bp.get("/dias/<int:dia_id>/avulsos")
@login_obrigatorio
def listar_avulsos(dia_id: int):
    """Quem tem ponto neste dia sem ser da equipe desta obra."""
    obra_id = obra_do_dia(dia_id)
    return jsonify([dict(r) for r in todos(
        """SELECT f.id AS funcionario_id, f.nome, f.funcao, h.horas_trabalhadas AS horas, h.justificativa,
                  CASE WHEN f.obra_id = ? THEN '' ELSE o.nome END AS obra_origem
             FROM horas h JOIN funcionarios f ON f.id = h.funcionario_id JOIN obras o ON o.id = f.obra_id
            WHERE h.dia_id = ? AND (f.obra_id <> ? OR f.avulso = 1)
            ORDER BY f.nome""", (obra_id, dia_id, obra_id))])


@bp.post("/dias/<int:dia_id>/horas/repetir")
@login_obrigatorio
def repetir_ponto(dia_id: int):
    """Copia o ponto do último dia lançado — economiza minutos todo dia.

    Na prática, a equipe do canteiro é quase sempre a mesma; digitar as mesmas
    8 horas para 12 pessoas de novo é o tipo de atrito que faz o gestor
    abandonar o app.
    """
    obra_id = obra_do_dia(dia_id)
    usuario = exigir_lancamento()

    referencia = um(
        """SELECT h.dia_id FROM horas h
             JOIN dias d ON d.id = h.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.id <> ? AND h.horas_trabalhadas > 0
              AND d.data < (SELECT data FROM dias WHERE id = ?)
            ORDER BY d.data DESC LIMIT 1""",
        (obra_id, dia_id, dia_id),
    )
    if not referencia:
        raise ApiErro("Não há nenhum ponto anterior para copiar.", 404)

    origem = todos(
        """SELECT h.funcionario_id, h.horas_trabalhadas
             FROM horas h JOIN funcionarios f ON f.id = h.funcionario_id
            WHERE h.dia_id = ? AND f.ativo = 1 AND f.obra_id = ? AND h.horas_trabalhadas > 0""",
        (referencia["dia_id"], obra_id))
    if not origem:
        raise ApiErro("O último ponto lançado não tem funcionários ativos.", 404)

    db = get_db()
    for r in origem:
        db.execute(
            """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas, valor_hora, justificativa)
               VALUES (?, ?, ?, 0, '')
               ON CONFLICT(dia_id, funcionario_id)
               DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas,
                             justificativa = ''""",
            (dia_id, r["funcionario_id"], r["horas_trabalhadas"]),
        )
    db.commit()
    registrar_auditoria(usuario, "repetiu_ponto", "dia", dia_id, f"{len(origem)} registro(s)")
    return jsonify({"ok": True, "copiados": len(origem)})
