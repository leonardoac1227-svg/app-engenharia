# -*- coding: utf-8 -*-
"""Páginas HTML, entrega de anexos e arquivos de PWA."""

from __future__ import annotations

import io
import logging
import mimetypes
import os

from flask import (Blueprint, abort, make_response, redirect, render_template, send_file,
                   send_from_directory, url_for)

from .. import config
from ..security import login_obrigatorio, obras_permitidas, usuario_atual
from .. import storage
from ..utils import caminho_anexo

log = logging.getLogger("obra-erp")

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

    O conteúdo é **entregue pelo próprio app**, nunca por redirecionamento
    para o Supabase. A versão anterior redirecionava para a URL assinada do
    bucket e as fotos apareciam quebradas: a política de segurança do app
    (`img-src 'self'`) bloqueia imagem vinda de outro domínio, e o navegador
    descartava a resposta em silêncio. Servir aqui resolve isso, mantém a URL
    do anexo estável e evita que a URL assinada circule por aí.
    """
    if obras_permitidas() == []:
        abort(403)

    nome_base = os.path.basename(nome)
    tipo = mimetypes.guess_type(nome_base)[0] or "application/octet-stream"

    caminho = caminho_anexo(nome)
    if caminho:
        return send_file(caminho, mimetype=tipo, conditional=True, max_age=86400)

    if storage.usando_supabase():
        conteudo = storage.baixar(nome_base)
        if conteudo:
            resposta = send_file(io.BytesIO(conteudo), mimetype=tipo,
                                 download_name=nome_base, conditional=True)
            # O nome do arquivo é um UUID: o conteúdo nunca muda, então pode
            # ficar no cache do aparelho e poupar o 4G do canteiro.
            resposta.headers["Cache-Control"] = "private, max-age=86400"
            return resposta

    # Chegou aqui: o arquivo não está em lugar nenhum. Em vez de um "Not Found"
    # seco, explicamos a causa provável — as três possíveis têm soluções bem
    # diferentes, e a mensagem economiza uma investigação inteira.
    situacao = storage.verificar(nome_base)
    log.warning("Anexo indisponível (%s): %s · %s", situacao["origem"], nome_base,
                situacao["detalhe"])

    resposta = make_response(render_template("anexo_indisponivel.html",
                                             arquivo=nome_base,
                                             origem=situacao["origem"],
                                             detalhe=situacao["detalhe"]), 404)
    resposta.headers["X-Motivo"] = situacao["detalhe"][:180]
    return resposta


@bp.route("/static/v<versao>/<path:arquivo>")
def estatico_versionado(versao: str, arquivo: str):
    """Estáticos com a versão no caminho.

    Os módulos JS se importam por caminho relativo ("./views/ajustes.js").
    Com a versão só na query do app.js (?v=), os outros módulos tinham sempre o
    mesmo endereço e ficavam um ano no cache do aparelho: um deploy novo
    chegava pela metade (foi assim que o botão "Testar IA" não aparecia). Com a
    versão no caminho, os imports relativos herdam a versão e cada deploy tem
    endereços novos — e o cache longo continua valendo para o resto.
    """
    resposta = send_from_directory(os.path.join(config.BASE_DIR, "static"), arquivo, max_age=31_536_000)
    resposta.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resposta


@bp.route("/sw.js")
def service_worker():
    """Servido da raiz para que o escopo do PWA cubra o app inteiro.

    A versão do app é escrita dentro do arquivo: muda os bytes do sw.js a cada
    deploy (o navegador reinstala) e aponta o pré-cache para os endereços novos.
    """
    from flask import Response
    from .. import __version__
    with open(os.path.join(config.BASE_DIR, "static", "js", "sw.js"), encoding="utf-8") as arquivo:
        conteudo = arquivo.read().replace("__VERSAO__", __version__)
    return Response(conteudo, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


@bp.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(os.path.join(config.BASE_DIR, "static"), "manifest.webmanifest",
                               mimetype="application/manifest+json")


@bp.route("/health")
def health():
    """Liveness para o Render — e diagnóstico de deploy.

    Responde qual versão está realmente no ar e em qual banco/armazenamento
    ela está falando. É a forma mais rápida de descobrir se um deploy pegou:
    se a versão aqui não mudou, o problema é de deploy, não de cache.
    """
    from .. import __version__
    from .. import storage

    return {
        "ok": True,
        "servico": "obra-erp",
        "versao": __version__,
        "banco": config.BACKEND,
        "anexos": "supabase" if storage.usando_supabase() else "disco",
        "tema": "claro-2.2",
        "ia": _provedores_ia(),
        "telegram": bool(config.TELEGRAM_BOT_TOKEN),
    }


def _provedores_ia() -> list[str]:
    from ..services.ia_provedores import provedores_configurados
    return provedores_configurados()
