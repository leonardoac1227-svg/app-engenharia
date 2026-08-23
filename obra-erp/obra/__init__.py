# -*- coding: utf-8 -*-
"""Obra ERP — ERP de bolso para gestão de obras.

Application factory: monta a aplicação Flask com configuração de sessão,
proteção CSRF, cabeçalhos de segurança, tratamento uniforme de erros e o
registro dos blueprints da API.
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from . import config
from .db import fechar_db, migrar
from .security import validar_csrf
from .utils import ApiErro

__version__ = "2.0.0"

# Rotas que podem ser chamadas sem token CSRF (ainda não existe sessão).
ROTAS_SEM_CSRF = {"/api/auth/login"}


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=os.path.join(config.BASE_DIR, "static"),
        template_folder=os.path.join(config.BASE_DIR, "templates"),
    )

    app.config.update(
        SECRET_KEY=config.carregar_secret_key(),
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Em produção (Render) o tráfego é HTTPS: o cookie não deve trafegar em claro.
        SESSION_COOKIE_SECURE=os.environ.get("FORCE_HTTPS", "0") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 dias no celular do canteiro
        JSON_SORT_KEYS=False,
        MAX_FORM_MEMORY_SIZE=config.MAX_UPLOAD_MB * 1024 * 1024,
    )
    app.json.ensure_ascii = False

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [obra-erp] %(message)s",
    )

    migrar()

    app.teardown_appcontext(fechar_db)

    # ------------------------------------------------------------ middlewares

    @app.before_request
    def _protecao_csrf():
        caminho = request.path
        if caminho.startswith("/api") and caminho not in ROTAS_SEM_CSRF:
            validar_csrf()

    @app.after_request
    def _cabecalhos_seguranca(resposta):
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        # CSP enxuta: nada de script externo; só as fontes do Google e imagens
        # próprias (inclusive as fotos em base64 vindas do cache do PWA).
        resposta.headers.setdefault("Content-Security-Policy", (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "form-action 'self'; frame-ancestors 'self'; base-uri 'self'"
        ))
        resposta.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(self)")
        if request.path.startswith("/api"):
            resposta.headers.setdefault("Cache-Control", "no-store")
        return resposta

    # ------------------------------------------------------------ erros

    @app.errorhandler(ApiErro)
    def _erro_negocio(erro: ApiErro):
        return jsonify({"erro": erro.mensagem, "codigo": erro.codigo}), erro.status

    @app.errorhandler(RequestEntityTooLarge)
    def _erro_tamanho(_erro):
        return jsonify({
            "erro": f"Arquivo muito grande. O limite é {config.MAX_UPLOAD_MB} MB por envio.",
            "codigo": "arquivo_grande",
        }), 413

    @app.errorhandler(HTTPException)
    def _erro_http(erro: HTTPException):
        if request.path.startswith("/api") or request.accept_mimetypes.best == "application/json":
            return jsonify({"erro": erro.description, "codigo": f"http_{erro.code}"}), erro.code
        return erro

    @app.errorhandler(Exception)
    def _erro_inesperado(erro: Exception):
        app.logger.exception("Falha não tratada em %s", request.path)
        if request.path.startswith("/api"):
            return jsonify({
                "erro": "Erro interno no servidor. Tente novamente em instantes.",
                "codigo": "erro_interno",
            }), 500
        raise erro

    # ------------------------------------------------------------ blueprints

    from .blueprints.auth import bp as bp_auth
    from .blueprints.diario import bp as bp_diario
    from .blueprints.equipe import bp as bp_equipe
    from .blueprints.financeiro import bp as bp_financeiro
    from .blueprints.obras import bp as bp_obras
    from .blueprints.paginas import bp as bp_paginas
    from .blueprints.relatorios import bp as bp_relatorios

    for blueprint in (bp_paginas, bp_auth, bp_obras, bp_diario, bp_equipe, bp_financeiro, bp_relatorios):
        app.register_blueprint(blueprint)

    @app.context_processor
    def _versao():
        return {"versao_app": __version__}

    app.logger.info("Obra ERP %s pronto · dados em %s", __version__, config.DATA_DIR)
    return app
