# -*- coding: utf-8 -*-
"""Sessão, troca de senha e cadastro de usuários (gestor / mestre / cliente)."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..config import PAPEIS
from ..db import executar, registrar_auditoria, todos, um
from ..security import (bloqueado, iniciar_sessao, limpar_tentativas, login_obrigatorio,
                        papel_obrigatorio, registrar_tentativa, token_csrf, usuario_atual)
from ..utils import ApiErro, agora_iso, texto

bp = Blueprint("auth", __name__, url_prefix="/api")


def _usuario_publico(u: dict) -> dict:
    return {
        "id": u["id"], "nome": u["nome"], "email": u["email"], "papel": u["papel"],
        "papel_label": PAPEIS.get(u["papel"], u["papel"]),
        "senha_provisoria": bool(u.get("senha_provisoria")),
        "pode_financeiro": u["papel"] == "gestor",
        "pode_lancar": u["papel"] in ("gestor", "mestre"),
        "pode_administrar": u["papel"] == "gestor",
    }


@bp.get("/sessao")
def sessao():
    usuario = usuario_atual()
    if not usuario:
        return jsonify({"autenticado": False}), 200
    from .. import __version__
    return jsonify({
        "autenticado": True,
        "usuario": _usuario_publico(usuario),
        "csrf": token_csrf(),
        "versao": __version__,
    })


@bp.post("/auth/login")
def login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    if bloqueado(ip):
        raise ApiErro("Muitas tentativas. Aguarde alguns minutos e tente novamente.", 429)

    dados = request.get_json(silent=True) or {}
    email = texto(dados.get("email"), 160).lower()
    senha = str(dados.get("senha") or "")

    row = um("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE", (email,))
    if not row or not row["ativo"] or not check_password_hash(row["senha_hash"], senha):
        registrar_tentativa(ip)
        raise ApiErro("E-mail ou senha incorretos.", 401, "credenciais")

    limpar_tentativas(ip)
    iniciar_sessao(row)
    executar("UPDATE usuarios SET ultimo_acesso = ? WHERE id = ?", (agora_iso(), row["id"]))
    registrar_auditoria(dict(row), "login", "usuario", row["id"])
    return jsonify({"autenticado": True, "usuario": _usuario_publico(dict(row)), "csrf": token_csrf()})


@bp.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.post("/auth/senha")
@login_obrigatorio
def trocar_senha():
    usuario = usuario_atual()
    dados = request.get_json(silent=True) or {}
    atual = str(dados.get("senha_atual") or "")
    nova = str(dados.get("senha_nova") or "")

    if len(nova) < 6:
        raise ApiErro("A nova senha precisa ter pelo menos 6 caracteres.")

    row = um("SELECT senha_hash FROM usuarios WHERE id = ?", (usuario["id"],))
    if not check_password_hash(row["senha_hash"], atual):
        raise ApiErro("Senha atual incorreta.", 403)

    executar(
        "UPDATE usuarios SET senha_hash = ?, senha_provisoria = 0 WHERE id = ?",
        (generate_password_hash(nova), usuario["id"]),
    )
    registrar_auditoria(usuario, "trocou_senha", "usuario", usuario["id"])
    return jsonify({"ok": True})


# ---------------------------------------------------------------- usuários

@bp.get("/usuarios")
@papel_obrigatorio("gestor")
def listar_usuarios():
    rows = todos(
        """SELECT id, nome, email, papel, ativo, ultimo_acesso, criado_em
             FROM usuarios ORDER BY papel, nome""")
    lista = []
    for r in rows:
        item = dict(r)
        item["papel_label"] = PAPEIS.get(r["papel"], r["papel"])
        item["obras"] = [x["obra_id"] for x in todos(
            "SELECT obra_id FROM usuario_obras WHERE usuario_id = ?", (r["id"],))]
        lista.append(item)
    return jsonify(lista)


@bp.post("/usuarios")
@papel_obrigatorio("gestor")
def criar_usuario():
    dados = request.get_json(silent=True) or {}
    nome = texto(dados.get("nome"), 120, True, "o nome")
    email = texto(dados.get("email"), 160, True, "o e-mail").lower()
    senha = str(dados.get("senha") or "")
    papel = texto(dados.get("papel"), 20) or "mestre"

    if papel not in PAPEIS:
        raise ApiErro("Perfil inválido.")
    if "@" not in email or "." not in email:
        raise ApiErro("E-mail inválido.")
    if len(senha) < 6:
        raise ApiErro("A senha precisa ter pelo menos 6 caracteres.")
    if um("SELECT id FROM usuarios WHERE email = ? COLLATE NOCASE", (email,)):
        raise ApiErro("Já existe um usuário com este e-mail.", 409)

    cur = executar(
        """INSERT INTO usuarios (nome, email, senha_hash, papel, ativo, senha_provisoria, criado_em)
           VALUES (?, ?, ?, ?, 1, 1, ?)""",
        (nome, email, generate_password_hash(senha), papel, agora_iso()),
    )
    usuario_id = cur.lastrowid

    for obra_id in dados.get("obras", []) or []:
        executar("INSERT OR IGNORE INTO usuario_obras (usuario_id, obra_id) VALUES (?, ?)",
                 (usuario_id, int(obra_id)))

    registrar_auditoria(usuario_atual(), "criou_usuario", "usuario", usuario_id, f"{nome} ({papel})")
    return jsonify({"id": usuario_id}), 201


@bp.put("/usuarios/<int:usuario_id>")
@papel_obrigatorio("gestor")
def atualizar_usuario(usuario_id: int):
    dados = request.get_json(silent=True) or {}
    atual = usuario_atual()
    alvo = um("SELECT id, papel FROM usuarios WHERE id = ?", (usuario_id,))
    if not alvo:
        raise ApiErro("Usuário não encontrado.", 404)

    campos, valores = [], []
    if "nome" in dados:
        campos.append("nome = ?"); valores.append(texto(dados["nome"], 120, True, "o nome"))
    if "papel" in dados:
        papel = texto(dados["papel"], 20)
        if papel not in PAPEIS:
            raise ApiErro("Perfil inválido.")
        if usuario_id == atual["id"] and papel != "gestor":
            raise ApiErro("Você não pode remover o próprio acesso de gestor.")
        campos.append("papel = ?"); valores.append(papel)
    if "ativo" in dados:
        if usuario_id == atual["id"] and not dados["ativo"]:
            raise ApiErro("Você não pode desativar o próprio usuário.")
        campos.append("ativo = ?"); valores.append(1 if dados["ativo"] else 0)
    if "senha" in dados and dados["senha"]:
        if len(str(dados["senha"])) < 6:
            raise ApiErro("A senha precisa ter pelo menos 6 caracteres.")
        campos.append("senha_hash = ?"); valores.append(generate_password_hash(str(dados["senha"])))
        campos.append("senha_provisoria = ?"); valores.append(1)

    if campos:
        valores.append(usuario_id)
        executar(f"UPDATE usuarios SET {', '.join(campos)} WHERE id = ?", valores)

    if "obras" in dados:
        executar("DELETE FROM usuario_obras WHERE usuario_id = ?", (usuario_id,))
        for obra_id in dados["obras"] or []:
            executar("INSERT OR IGNORE INTO usuario_obras (usuario_id, obra_id) VALUES (?, ?)",
                     (usuario_id, int(obra_id)))

    registrar_auditoria(atual, "editou_usuario", "usuario", usuario_id)
    return jsonify({"ok": True})


@bp.delete("/usuarios/<int:usuario_id>")
@papel_obrigatorio("gestor")
def desativar_usuario(usuario_id: int):
    atual = usuario_atual()
    if usuario_id == atual["id"]:
        raise ApiErro("Você não pode remover o próprio usuário.")
    executar("UPDATE usuarios SET ativo = 0 WHERE id = ?", (usuario_id,))
    registrar_auditoria(atual, "desativou_usuario", "usuario", usuario_id)
    return jsonify({"ok": True})


@bp.get("/auditoria")
@papel_obrigatorio("gestor")
def listar_auditoria():
    limite = min(int(request.args.get("limite", 60)), 300)
    rows = todos(
        """SELECT id, usuario_nome, acao, entidade, entidade_id, detalhe, criado_em
             FROM auditoria ORDER BY id DESC LIMIT ?""", (limite,))
    return jsonify([dict(r) for r in rows])


@bp.get("/diagnostico")
@login_obrigatorio
@papel_obrigatorio("gestor")
def diagnostico():
    """Prova, com ida e volta real, onde os dados estão sendo gravados.

    Ver a variável de ambiente definida não significa que o banco responde:
    a senha pode estar errada, o projeto pausado, a rede bloqueada. Aqui a
    gente escreve e lê de verdade, mede o tempo e conta os registros — é a
    diferença entre "configurei" e "está funcionando".
    """
    import time

    from .. import __version__, config, storage

    resultado = {
        "versao": __version__,
        "banco": {"tipo": config.BACKEND, "ok": False},
        "anexos": {"tipo": "supabase" if storage.usando_supabase() else "disco", "ok": False},
    }

    # ---- banco: leitura + escrita reais ----
    inicio = time.perf_counter()
    try:
        um("SELECT 1 AS teste")
        contagens = {}
        for tabela in ("obras", "semanas", "dias", "despesas", "fotos", "usuarios"):
            linha = um(f"SELECT COUNT(*) AS total FROM {tabela}")
            contagens[tabela] = (dict(linha) if linha else {}).get("total", 0)

        # escrita: a própria auditoria serve de sonda, sem sujar dados de obra
        registrar_auditoria(usuario_atual(), "diagnostico", "sistema", None, "verificação de conexão")

        resultado["banco"].update({
            "ok": True,
            "latencia_ms": round((time.perf_counter() - inicio) * 1000, 1),
            "registros": contagens,
            "destino": _descricao_banco(config),
        })
    except Exception as erro:  # noqa: BLE001 — queremos mostrar qualquer falha
        resultado["banco"].update({"ok": False, "erro": str(erro)[:200]})

    # ---- anexos ----
    if storage.usando_supabase():
        inicio = time.perf_counter()
        nome_sonda = "_diagnostico.txt"
        try:
            storage.enviar(nome_sonda, b"obra-erp ok", "text/plain")
            conteudo = storage.baixar(nome_sonda)
            resultado["anexos"].update({
                "ok": conteudo == b"obra-erp ok",
                "latencia_ms": round((time.perf_counter() - inicio) * 1000, 1),
                "bucket": config.SUPABASE_BUCKET,
                "destino": config.SUPABASE_URL,
            })
        except Exception as erro:  # noqa: BLE001
            resultado["anexos"].update({
                "ok": False,
                "erro": str(erro)[:200],
                "destino": config.SUPABASE_URL,
                "bucket": config.SUPABASE_BUCKET,
            })
    else:
        import os
        resultado["anexos"].update({
            "ok": os.path.isdir(config.UPLOAD_DIR),
            "destino": config.UPLOAD_DIR,
            "aviso": "No Render sem disco persistente, anexos no disco somem a cada deploy.",
        })

    # ---- anexos registrados x anexos que abrem ----
    #
    # É a conferência que responde "por que a foto não aparece": o banco tem o
    # registro, mas o arquivo pode não estar em lugar nenhum.
    try:
        registrados = todos(
            """SELECT arquivo AS nome FROM fotos WHERE arquivo != ''
               UNION ALL
               SELECT imagem_path AS nome FROM despesas
                WHERE imagem_path IS NOT NULL AND imagem_path != ''""")
        nomes = [linha["nome"] for linha in registrados]

        # Amostra: conferir centenas de arquivos a cada clique deixaria o
        # diagnóstico lento sem acrescentar informação.
        amostra = nomes[:12]
        ausentes = []
        for nome in amostra:
            situacao = storage.verificar(nome)
            if not situacao["encontrado"]:
                ausentes.append({"arquivo": nome, "motivo": situacao["detalhe"]})

        resultado["anexos"]["registrados"] = len(nomes)
        resultado["anexos"]["conferidos"] = len(amostra)
        resultado["anexos"]["ausentes"] = len(ausentes)
        resultado["anexos"]["exemplos_ausentes"] = ausentes[:3]
    except Exception as erro:  # noqa: BLE001
        resultado["anexos"]["erro_conferencia"] = str(erro)[:200]

    # Problemas que dá para apontar sem tentar conexão nenhuma — variável
    # colada com aspas, chave no formato errado, metade da configuração.
    resultado["configuracao"] = config.problemas_de_configuracao()

    return jsonify(resultado)


def _descricao_banco(config) -> str:
    """Mostra para onde o banco aponta, sem vazar usuário e senha."""
    if config.BACKEND != "postgres":
        return config.DB_PATH
    url = config.DATABASE_URL
    try:
        depois_arroba = url.split("@", 1)[1]
        host = depois_arroba.split("/")[0]
        return host
    except (IndexError, AttributeError):
        return "postgres"
