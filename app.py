# -*- coding: utf-8 -*-
"""
OBRA ERP DE BOLSO — backend Flask + SQLite.

Servidor único responsável por:
- Servir a interface (HTML/CSS/JS puros, mobile-first) em `/`.
- Expor uma API REST em `/api/...` consumida pelo frontend via fetch().
- Persistir tudo em um arquivo SQLite local (`obra_erp.db`), simulando
  um banco relacional completo sem precisar de um servidor SQL externo.
"""

import os
import sqlite3
import uuid
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory, g, Response
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "obra_erp.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
ALLOWED_EXT = {"png", "jpg", "jpeg", "pdf", "webp"}

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB por upload


# --------------------------------------------------------------------------
# BANCO DE DADOS
# --------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON;")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS obras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS semanas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER NOT NULL,
            numero INTEGER NOT NULL,
            meta TEXT DEFAULT '',
            meta_atingida INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL,
            FOREIGN KEY (obra_id) REFERENCES obras (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            funcao TEXT DEFAULT '',
            valor_hora REAL NOT NULL DEFAULT 0,
            ativo INTEGER DEFAULT 1,
            FOREIGN KEY (obra_id) REFERENCES obras (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semana_id INTEGER NOT NULL,
            dia_semana TEXT NOT NULL,
            producao_dia TEXT DEFAULT '',
            clima TEXT DEFAULT '',
            observacoes TEXT DEFAULT '',
            UNIQUE(semana_id, dia_semana),
            FOREIGN KEY (semana_id) REFERENCES semanas (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS horas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dia_id INTEGER NOT NULL,
            funcionario_id INTEGER NOT NULL,
            horas_trabalhadas REAL DEFAULT 0,
            UNIQUE(dia_id, funcionario_id),
            FOREIGN KEY (dia_id) REFERENCES dias (id),
            FOREIGN KEY (funcionario_id) REFERENCES funcionarios (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS despesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dia_id INTEGER NOT NULL,
            categoria TEXT NOT NULL,
            descricao TEXT DEFAULT '',
            valor REAL NOT NULL DEFAULT 0,
            status_pagamento TEXT NOT NULL,
            imagem_path TEXT DEFAULT '',
            criado_em TEXT NOT NULL,
            FOREIGN KEY (dia_id) REFERENCES dias (id)
        );
    """)

    conn.commit()
    conn.close()


def moeda(v):
    v = v or 0
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------
# PÁGINA PRINCIPAL (SPA)
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(os.path.join(BASE_DIR, "templates"), "index.html")


# --------------------------------------------------------------------------
# API — OBRAS
# --------------------------------------------------------------------------

@app.route("/api/obras", methods=["GET"])
def api_list_obras():
    db = get_db()
    rows = db.execute("SELECT id, nome FROM obras ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/obras", methods=["POST"])
def api_create_obra():
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"erro": "Nome da obra é obrigatório."}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO obras (nome, criado_em) VALUES (?, ?)",
        (nome, datetime.now().isoformat()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "nome": nome}), 201


# --------------------------------------------------------------------------
# API — SEMANAS
# --------------------------------------------------------------------------

@app.route("/api/obras/<int:obra_id>/semanas", methods=["GET"])
def api_list_semanas(obra_id):
    db = get_db()
    rows = db.execute(
        "SELECT id, numero, meta, meta_atingida FROM semanas WHERE obra_id = ? ORDER BY numero",
        (obra_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/obras/<int:obra_id>/semanas", methods=["POST"])
def api_create_semana(obra_id):
    db = get_db()
    row = db.execute(
        "SELECT COALESCE(MAX(numero), 0) AS m FROM semanas WHERE obra_id = ?", (obra_id,)
    ).fetchone()
    proximo = row["m"] + 1
    cur = db.execute(
        "INSERT INTO semanas (obra_id, numero, meta, meta_atingida, criado_em) VALUES (?, ?, '', 0, ?)",
        (obra_id, proximo, datetime.now().isoformat()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid, "numero": proximo, "meta": "", "meta_atingida": 0}), 201


@app.route("/api/semanas/<int:semana_id>", methods=["PUT"])
def api_update_semana(semana_id):
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "UPDATE semanas SET meta = ?, meta_atingida = ? WHERE id = ?",
        (data.get("meta", ""), 1 if data.get("meta_atingida") else 0, semana_id),
    )
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — DIAS / RDO
# --------------------------------------------------------------------------

def _garantir_dias(db, semana_id):
    for d in DIAS_SEMANA:
        existe = db.execute(
            "SELECT id FROM dias WHERE semana_id = ? AND dia_semana = ?", (semana_id, d)
        ).fetchone()
        if not existe:
            db.execute(
                "INSERT INTO dias (semana_id, dia_semana) VALUES (?, ?)", (semana_id, d)
            )
    db.commit()


@app.route("/api/semanas/<int:semana_id>/dias", methods=["GET"])
def api_list_dias(semana_id):
    db = get_db()
    _garantir_dias(db, semana_id)
    rows = db.execute(
        "SELECT id, dia_semana, producao_dia, clima, observacoes FROM dias WHERE semana_id = ?",
        (semana_id,),
    ).fetchall()
    por_nome = {r["dia_semana"]: dict(r) for r in rows}
    return jsonify([por_nome[d] for d in DIAS_SEMANA])


@app.route("/api/dias/<int:dia_id>", methods=["PUT"])
def api_update_dia(dia_id):
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "UPDATE dias SET producao_dia = ?, clima = ?, observacoes = ? WHERE id = ?",
        (data.get("producao_dia", ""), data.get("clima", ""), data.get("observacoes", ""), dia_id),
    )
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — FUNCIONÁRIOS / HORAS (FOLHA)
# --------------------------------------------------------------------------

@app.route("/api/obras/<int:obra_id>/funcionarios", methods=["GET"])
def api_list_funcionarios(obra_id):
    db = get_db()
    rows = db.execute(
        "SELECT id, nome, funcao, valor_hora FROM funcionarios WHERE obra_id = ? AND ativo = 1 ORDER BY nome",
        (obra_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/obras/<int:obra_id>/funcionarios", methods=["POST"])
def api_create_funcionario(obra_id):
    data = request.get_json(force=True)
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"erro": "Nome é obrigatório."}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO funcionarios (obra_id, nome, funcao, valor_hora, ativo) VALUES (?, ?, ?, ?, 1)",
        (obra_id, nome, (data.get("funcao") or "").strip(), float(data.get("valor_hora") or 0)),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/funcionarios/<int:funcionario_id>", methods=["DELETE"])
def api_delete_funcionario(funcionario_id):
    db = get_db()
    db.execute("UPDATE funcionarios SET ativo = 0 WHERE id = ?", (funcionario_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/dias/<int:dia_id>/horas", methods=["GET"])
def api_get_horas(dia_id):
    db = get_db()
    rows = db.execute(
        "SELECT funcionario_id, horas_trabalhadas FROM horas WHERE dia_id = ?", (dia_id,)
    ).fetchall()
    return jsonify({str(r["funcionario_id"]): r["horas_trabalhadas"] for r in rows})


@app.route("/api/dias/<int:dia_id>/horas", methods=["PUT"])
def api_save_horas(dia_id):
    data = request.get_json(force=True)
    registros = data.get("registros", [])
    db = get_db()
    for reg in registros:
        db.execute(
            """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas)
               VALUES (?, ?, ?)
               ON CONFLICT(dia_id, funcionario_id)
               DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas""",
            (dia_id, reg["funcionario_id"], float(reg.get("horas", 0))),
        )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/dias/<int:dia_id>/totais", methods=["GET"])
def api_totais_dia(dia_id):
    db = get_db()
    folha = db.execute(
        """SELECT COALESCE(SUM(h.horas_trabalhadas * f.valor_hora), 0) AS t
           FROM horas h JOIN funcionarios f ON h.funcionario_id = f.id
           WHERE h.dia_id = ?""",
        (dia_id,),
    ).fetchone()["t"]
    despesas = db.execute(
        "SELECT COALESCE(SUM(valor), 0) AS t FROM despesas WHERE dia_id = ?", (dia_id,)
    ).fetchone()["t"]
    return jsonify({"folha": folha, "despesas": despesas})


# --------------------------------------------------------------------------
# API — DESPESAS (CAIXA E MATERIAIS)
# --------------------------------------------------------------------------

@app.route("/api/dias/<int:dia_id>/despesas", methods=["GET"])
def api_list_despesas(dia_id):
    db = get_db()
    rows = db.execute(
        """SELECT id, categoria, descricao, valor, status_pagamento, imagem_path
           FROM despesas WHERE dia_id = ? ORDER BY id DESC""",
        (dia_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/dias/<int:dia_id>/despesas", methods=["POST"])
def api_create_despesa(dia_id):
    categoria = request.form.get("categoria", "Material")
    descricao = request.form.get("descricao", "").strip()
    valor = float(request.form.get("valor", 0) or 0)
    status = request.form.get("status_pagamento", "Em Aberto")

    if not descricao or valor <= 0:
        return jsonify({"erro": "Descrição e valor são obrigatórios."}), 400

    imagem_path = ""
    arquivo = request.files.get("imagem")
    if arquivo and arquivo.filename:
        ext = arquivo.filename.rsplit(".", 1)[-1].lower()
        if ext in ALLOWED_EXT:
            nome_seguro = f"{uuid.uuid4().hex}.{ext}"
            arquivo.save(os.path.join(UPLOAD_DIR, nome_seguro))
            imagem_path = f"/static/uploads/{nome_seguro}"

    db = get_db()
    cur = db.execute(
        """INSERT INTO despesas (dia_id, categoria, descricao, valor, status_pagamento, imagem_path, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (dia_id, categoria, descricao, valor, status, imagem_path, datetime.now().isoformat()),
    )
    db.commit()
    return jsonify({"id": cur.lastrowid}), 201


@app.route("/api/despesas/<int:despesa_id>", methods=["DELETE"])
def api_delete_despesa(despesa_id):
    db = get_db()
    db.execute("DELETE FROM despesas WHERE id = ?", (despesa_id,))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# API — FECHAMENTO DA SEMANA
# --------------------------------------------------------------------------

def _dados_fechamento(db, semana_id):
    semana = db.execute(
        "SELECT id, obra_id, numero, meta, meta_atingida FROM semanas WHERE id = ?", (semana_id,)
    ).fetchone()
    if not semana:
        return None

    _garantir_dias(db, semana_id)
    dias_rows = db.execute(
        "SELECT id, dia_semana, producao_dia, clima, observacoes FROM dias WHERE semana_id = ?",
        (semana_id,),
    ).fetchall()
    por_nome = {r["dia_semana"]: dict(r) for r in dias_rows}
    dias_ordenados = [por_nome[d] for d in DIAS_SEMANA]
    dias_ids = [d["id"] for d in dias_ordenados]

    placeholders = ",".join("?" * len(dias_ids)) if dias_ids else "0"

    folha = db.execute(
        f"""SELECT COALESCE(SUM(h.horas_trabalhadas * f.valor_hora), 0) AS t
            FROM horas h JOIN funcionarios f ON h.funcionario_id = f.id
            WHERE h.dia_id IN ({placeholders})""",
        dias_ids,
    ).fetchone()["t"] if dias_ids else 0.0

    def soma_status(status):
        r = db.execute(
            f"""SELECT COALESCE(SUM(valor), 0) AS t FROM despesas
                WHERE dia_id IN ({placeholders}) AND status_pagamento = ?""",
            dias_ids + [status],
        ).fetchone()
        return r["t"] if r else 0.0

    reembolso = soma_status("Reembolso") if dias_ids else 0.0
    em_aberto = soma_status("Em Aberto") if dias_ids else 0.0
    pago = soma_status("Pago") if dias_ids else 0.0

    obra = db.execute("SELECT nome FROM obras WHERE id = ?", (semana["obra_id"],)).fetchone()

    return {
        "obra_nome": obra["nome"] if obra else "",
        "semana_numero": semana["numero"],
        "meta": semana["meta"],
        "meta_atingida": bool(semana["meta_atingida"]),
        "dias": dias_ordenados,
        "financeiro": {
            "folha": folha,
            "reembolso": reembolso,
            "em_aberto": em_aberto,
            "pago": pago,
            "terceiros": folha + em_aberto,
        },
    }


@app.route("/api/semanas/<int:semana_id>/fechamento", methods=["GET"])
def api_fechamento(semana_id):
    db = get_db()
    dados = _dados_fechamento(db, semana_id)
    if dados is None:
        return jsonify({"erro": "Semana não encontrada."}), 404
    return jsonify(dados)


@app.route("/api/semanas/<int:semana_id>/relatorio.txt", methods=["GET"])
def api_relatorio_txt(semana_id):
    db = get_db()
    d = _dados_fechamento(db, semana_id)
    if d is None:
        return jsonify({"erro": "Semana não encontrada."}), 404

    linhas = []
    linhas.append(f"RELATORIO SEMANAL — {d['obra_nome']} — Semana {d['semana_numero']}")
    linhas.append("=" * 56)
    linhas.append("")
    linhas.append("META DA SEMANA")
    linhas.append(f"- {d['meta'] or '(nao definida)'}")
    linhas.append(f"- Status: {'ATINGIDA' if d['meta_atingida'] else 'NAO ATINGIDA'}")
    linhas.append("")
    linhas.append("RELATORIO FISICO (RDO)")
    for dia in d["dias"]:
        if dia["producao_dia"] or dia["observacoes"] or (dia["clima"] and "Limpo" not in dia["clima"]):
            linhas.append(f"\n[{dia['dia_semana']}] Clima: {dia['clima'] or '-'}")
            if dia["producao_dia"]:
                linhas.append(f"  Producao: {dia['producao_dia']}")
            if dia["observacoes"]:
                linhas.append(f"  Observacoes: {dia['observacoes']}")
    linhas.append("")
    linhas.append("RELATORIO FINANCEIRO")
    f = d["financeiro"]
    linhas.append(f"- Total Folha de Pagamento: {moeda(f['folha'])}")
    linhas.append(f"- Transferir p/ empresa do gestor (Reembolso): {moeda(f['reembolso'])}")
    linhas.append(f"- Total a pagar a terceiros (Folha + Em Aberto): {moeda(f['terceiros'])}")
    linhas.append(f"- Total ja pago no periodo: {moeda(f['pago'])}")
    linhas.append("")
    linhas.append(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")

    conteudo = "\n".join(linhas)
    nome_arquivo = f"{d['obra_nome'].replace(' ', '_')}_semana_{d['semana_numero']}.txt"
    return Response(
        conteudo,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={nome_arquivo}"},
    )


# --------------------------------------------------------------------------
# BOOT
# --------------------------------------------------------------------------

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
