# -*- coding: utf-8 -*-
"""Bot do Telegram: webhook e vínculo da conversa com o usuário do app."""

from __future__ import annotations

import logging
import secrets
import time

from flask import Blueprint, current_app, jsonify, request

from .. import config
from ..db import executar, registrar_auditoria, um
from ..security import login_obrigatorio, papel_obrigatorio, usuario_atual
from ..services import bot_telegram, telegram_api
from ..utils import ApiErro

bp = Blueprint("telegram", __name__)
log = logging.getLogger(__name__)
ERRO_RECENTE = 15 * 60  # segundos: erro mais antigo que isso já foi superado


@bp.post("/telegram/webhook")
def webhook():
    """Entrega do Telegram. Fora de /api: não há sessão nem CSRF, há o segredo."""
    if not telegram_api.configurado():
        return jsonify({"ok": False}), 404
    enviado = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secrets.compare_digest(enviado, telegram_api.segredo_webhook()):
        return jsonify({"ok": False}), 403
    try:
        bot_telegram.receber(current_app._get_current_object(), request.get_json(silent=True) or {})
    except Exception:  # noqa: BLE001
        # Responder erro faria o Telegram reenviar a mesma mensagem sem parar.
        log.exception("Telegram: falha ao receber a atualização")
    return jsonify({"ok": True})


@bp.get("/api/telegram/status")
@login_obrigatorio
def status():
    usuario = usuario_atual()
    resposta = {"configurado": telegram_api.configurado(), "bot": "", "webhook": "",
                "conectado": bool(um("SELECT 1 AS x FROM telegram_chats WHERE usuario_id = ?",
                                     (usuario["id"],)))}
    if resposta["configurado"]:
        try:
            resposta["bot"] = telegram_api.usuario_do_bot()
            if usuario["papel"] == "gestor":
                info = telegram_api.estado_webhook()
                resposta["webhook"] = info.get("url", "")
                # O Telegram guarda o último erro mesmo depois de voltar a
                # entregar: só vale mostrar o que é recente.
                if time.time() - (info.get("last_error_date") or 0) < ERRO_RECENTE:
                    resposta["webhook_erro"] = info.get("last_error_message", "")
        except telegram_api.ErroTelegram as erro:
            resposta["erro"] = str(erro)
    return jsonify(resposta)


@bp.post("/api/telegram/codigo")
@login_obrigatorio
def gerar_codigo():
    usuario = usuario_atual()
    if usuario["papel"] == "cliente":
        raise ApiErro("O perfil de cliente não usa o bot.", 403)
    if not telegram_api.configurado():
        raise ApiErro("Bot desligado: configure TELEGRAM_BOT_TOKEN no servidor.", 400)
    codigo = bot_telegram.gerar_codigo(usuario["id"])
    try:
        nome_bot = telegram_api.usuario_do_bot()
    except telegram_api.ErroTelegram as erro:
        raise ApiErro(f"Telegram recusou o token: {erro}", 400)
    return jsonify({"codigo": codigo, "bot": nome_bot,
                    "link": f"https://t.me/{nome_bot}?start={codigo}" if nome_bot else ""})


@bp.delete("/api/telegram/conexao")
@login_obrigatorio
def desconectar():
    usuario = usuario_atual()
    executar("DELETE FROM telegram_chats WHERE usuario_id = ?", (usuario["id"],))
    registrar_auditoria(usuario, "desconectou_telegram", "usuario", usuario["id"])
    return jsonify({"ok": True})


@bp.post("/api/telegram/webhook")
@papel_obrigatorio("gestor")
def ativar_webhook():
    """Aponta o bot para este servidor (o app também faz isso sozinho ao subir)."""
    if not telegram_api.configurado():
        raise ApiErro("Configure TELEGRAM_BOT_TOKEN no servidor.", 400)
    base = config.PUBLIC_URL or request.url_root
    if not base.startswith("https://"):
        raise ApiErro("O Telegram só entrega em endereço https. Abra o app pelo endereço público.", 400)
    try:
        url = telegram_api.ativar_webhook(base)
    except telegram_api.ErroTelegram as erro:
        raise ApiErro(f"Telegram recusou: {erro}", 400)
    registrar_auditoria(usuario_atual(), "ativou_telegram", "sistema", None, url)
    return jsonify({"ok": True, "webhook": url})
