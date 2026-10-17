# -*- coding: utf-8 -*-
"""Obra ERP — ERP de bolso para gestão de obras.

Application factory: monta a aplicação Flask com configuração de sessão,
proteção CSRF, cabeçalhos de segurança, tratamento uniforme de erros e o
registro dos blueprints da API.
"""

from __future__ import annotations

import gzip
import logging
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from . import config
from .db import fechar_db, migrar
from .security import validar_csrf
from .utils import ApiErro

__version__ = "3.1.1"

# Rotas que podem ser chamadas sem token CSRF (ainda não existe sessão).
ROTAS_SEM_CSRF = {"/api/auth/login"}


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=os.path.join(config.BASE_DIR, "static"),
        template_folder=os.path.join(config.BASE_DIR, "templates"),
    )

    # O Render entrega o tráfego por trás de um proxy. Sem isto, todo request
    # chega com o IP interno do proxy, e o app precisaria confiar no cabeçalho
    # X-Forwarded-For cru — que o cliente escolhe. Era assim que o freio de
    # força bruta podia ser anulado: bastava mandar um IP diferente a cada
    # tentativa. Com x_for=1 confiamos em exatamente um salto (o do Render).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.config.update(
        SECRET_KEY=config.carregar_secret_key(),
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Em produção (Render) o tráfego é HTTPS: o cookie não deve trafegar em claro.
        SESSION_COOKIE_SECURE=config.FORCE_HTTPS,
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,  # 30 dias no celular do canteiro
        JSON_SORT_KEYS=False,
        MAX_FORM_MEMORY_SIZE=config.MAX_UPLOAD_MB * 1024 * 1024,
        # Os estáticos carregam ?v=<versão> na URL, então o conteúdo de uma
        # URL nunca muda: pode ficar um ano no cache do aparelho. Revalidar a
        # cada carga custava doze requisições 304 em toda abertura do app —
        # no 4G do canteiro, mais caro que o download em si.
        SEND_FILE_MAX_AGE_DEFAULT=31_536_000,
    )
    app.json.ensure_ascii = False

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [obra-erp] %(message)s",
    )

    migrar()

    app.teardown_appcontext(fechar_db)

    # Parcelas gravadas por versões anteriores numa semana que não é a do
    # vencimento vão para o dia certo. Sem nada fora do lugar, não muda nada.
    try:
        from .services.parcelamento import alocar_parcelas_agendadas
        with app.app_context():
            movidas = alocar_parcelas_agendadas()
        if movidas:
            logging.getLogger(__name__).info(
                "%d parcela(s) movida(s) para a semana do vencimento.", movidas)
    except Exception:  # noqa: BLE001 — não pode impedir o app de subir
        logging.getLogger(__name__).exception("Falha ao realocar parcelas.")

    # ------------------------------------------------------------ middlewares

    @app.before_request
    def _protecao_csrf():
        caminho = request.path
        if caminho.startswith("/api") and caminho not in ROTAS_SEM_CSRF:
            validar_csrf()

    @app.after_request
    def _comprimir(resposta):
        """Compacta a resposta em gzip quando compensa.

        JSON de fechamento e os arquivos de CSS/JS encolhem cerca de 75%.
        No 4G do canteiro isso é a diferença mais sentida pelo usuário — mais
        que qualquer otimização de consulta. Conteúdo já comprimido (JPEG,
        PDF, ZIP) é deixado em paz: recomprimir só gastaria CPU.
        """
        if resposta.status_code >= 300:
            return resposta
        if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
            return resposta
        if resposta.headers.get("Content-Encoding"):
            return resposta

        tipo = (resposta.mimetype or "").lower()
        compensa = tipo.startswith("text/") or tipo in (
            "application/json", "application/javascript", "application/manifest+json",
            "image/svg+xml", "application/xml",
        )
        tamanho = resposta.content_length or 0
        if not compensa or tamanho < 1024 or tamanho > 2 * 1024 * 1024:
            return resposta

        # CSS e JS chegam aqui em modo "passthrough" (o arquivo é enviado
        # direto do disco, sem passar pela memória). Para comprimir é preciso
        # desligar isso — vale a pena: são os maiores downloads do app.
        resposta.direct_passthrough = False

        dados = gzip.compress(resposta.get_data(), compresslevel=6)
        resposta.set_data(dados)
        resposta.headers["Content-Encoding"] = "gzip"
        resposta.headers["Content-Length"] = len(dados)
        resposta.headers.add("Vary", "Accept-Encoding")
        return resposta

    @app.after_request
    def _cabecalhos_seguranca(resposta):
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        resposta.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        if config.FORCE_HTTPS:
            # Um ano, com subdomínios: depois da primeira visita o navegador
            # recusa falar HTTP com este domínio, fechando a janela em que
            # alguém na mesma rede poderia interceptar a sessão.
            resposta.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
    from .blueprints.contexto import bp as bp_contexto
    from .blueprints.diario import bp as bp_diario
    from .blueprints.equipe import bp as bp_equipe
    from .blueprints.financeiro import bp as bp_financeiro
    from .blueprints.obras import bp as bp_obras
    from .blueprints.paginas import bp as bp_paginas
    from .blueprints.relatorios import bp as bp_relatorios
    from .blueprints.locacoes import bp as bp_locacoes

    for blueprint in (bp_paginas, bp_auth, bp_obras, bp_diario, bp_equipe, bp_financeiro,
                      bp_relatorios, bp_locacoes, bp_contexto):
        app.register_blueprint(blueprint)

    @app.context_processor
    def _versao():
        return {"versao_app": __version__}

    app.logger.info("Obra ERP %s pronto · dados em %s", __version__, config.DATA_DIR)

    from . import storage
    if storage.usando_supabase():
        app.logger.info("Anexos: Supabase Storage · %s · bucket '%s'",
                        config.SUPABASE_URL, config.SUPABASE_BUCKET)
    else:
        # Sem bucket, o anexo vive no disco do servidor. No Render sem disco
        # persistente isso significa perder fotos e notas a cada deploy — e o
        # sintoma só aparece dias depois, quando alguém tenta abrir o arquivo.
        app.logger.warning(
            "Anexos: disco local (%s). Sem SUPABASE_URL/SUPABASE_SERVICE_KEY, "
            "fotos e notas fiscais são APAGADAS a cada novo deploy no Render. "
            "Veja SUPABASE.md.", config.UPLOAD_DIR)

    for aviso in config.problemas_de_configuracao():
        app.logger.warning("Configuração: %s", aviso)
    return app
