# -*- coding: utf-8 -*-
"""Páginas HTML, entrega de anexos e arquivos de PWA."""

from __future__ import annotations

import io
import os

from flask import Blueprint, abort, redirect, render_template, send_file, send_from_directory, url_for

from .. import config
from ..security import login_obrigatorio, obras_permitidas, usuario_atual
from .. import storage
from ..utils import bytes_anexo, caminho_anexo

bp = Blueprint("paginas", __name__)


@bp.route("/")
def index():
    if not usuario_atual():
        return redirect(url_for("paginas.login"))
    return render_template("index.html")


@bp.route("/login")
def login():
    if usuario_atual():
        return redirect(url_for("paginas.index"))
    return render_template("login.html")


@bp.route("/arquivos/<path:nome>")
@login_obrigatorio
def arquivo(nome: str):
    """Serve notas fiscais e fotos apenas para quem tem sessão ativa.

    Na v1 os anexos ficavam em /static/uploads — ou seja, qualquer pessoa com
    o link via a nota fiscal da obra. Aqui o acesso passa pela sessão.
    """
    if obras_permitidas() == []:
        abort(403)

    caminho = caminho_anexo(nome)
    if caminho:
        return send_file(caminho, conditional=True, max_age=86400)

    if storage.usando_supabase():
        # O bucket é privado: geramos uma URL de leitura válida por pouco tempo
        # e redirecionamos. A checagem de sessão acima continua sendo a porta.
        assinada = storage.url_assinada(os.path.basename(nome))
        if assinada:
            return redirect(assinada)
        conteudo = bytes_anexo(nome)
        if conteudo:
            return send_file(io.BytesIO(conteudo), download_name=os.path.basename(nome),
                             max_age=0)
    abort(404)


@bp.route("/sw.js")
def service_worker():
    """Servido da raiz para que o escopo do PWA cubra o app inteiro."""
    resposta = send_from_directory(os.path.join(config.BASE_DIR, "static", "js"), "sw.js")
    resposta.headers["Cache-Control"] = "no-cache"
    resposta.headers["Content-Type"] = "application/javascript"
    return resposta


@bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(os.path.join(config.BASE_DIR, "static"), "manifest.webmanifest",
                               mimetype="application/manifest+json")


@bp.route("/health")
def health():
    """Endpoint de liveness para o Render (e para monitoramento externo)."""
    return {"ok": True, "servico": "obra-erp"}
