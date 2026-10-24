# -*- coding: utf-8 -*-
"""Cliente mínimo da API de bots do Telegram (biblioteca padrão, sem dependência).

Só o que o bot do app usa: receber arquivos, responder, mostrar botões e
apontar o webhook para o servidor.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from .. import config

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{metodo}"
ARQUIVO = "https://api.telegram.org/file/bot{token}/{caminho}"
TEMPO_LIMITE = 30
LIMITE_TEXTO = 4000  # o Telegram corta em 4096 caracteres
_eu: dict = {}


class ErroTelegram(Exception):
    pass


def configurado() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN)


def segredo_webhook() -> str:
    """Segredo que o Telegram devolve no cabeçalho de cada entrega.

    Sem ele, qualquer um que descobrisse a URL poderia se passar pelo Telegram.
    Derivado da SECRET_KEY quando não é definido à parte.
    """
    if config.TELEGRAM_WEBHOOK_SECRET:
        return config.TELEGRAM_WEBHOOK_SECRET
    chave = config.carregar_secret_key().encode()
    return hmac.new(chave, b"telegram-webhook", hashlib.sha256).hexdigest()[:48]


def chamar(metodo: str, **parametros):
    if not configurado():
        raise ErroTelegram("TELEGRAM_BOT_TOKEN não configurado.")
    req = urllib.request.Request(
        API.format(token=config.TELEGRAM_BOT_TOKEN, metodo=metodo),
        data=json.dumps(parametros).encode("utf-8"),
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        try:
            dados = json.loads(erro.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise ErroTelegram(f"Telegram respondeu HTTP {erro.code}.") from erro
    if not dados.get("ok"):
        raise ErroTelegram(dados.get("description") or "Falha na API do Telegram.")
    return dados.get("result")


def baixar(file_id: str) -> bytes:
    info = chamar("getFile", file_id=file_id)
    url = ARQUIVO.format(token=config.TELEGRAM_BOT_TOKEN, caminho=info["file_path"])
    with urllib.request.urlopen(url, timeout=60) as resposta:
        return resposta.read()


def enviar(chat_id, texto: str, botoes: list[list[dict]] | None = None) -> None:
    """Texto simples (sem markdown: nome de fornecedor com * ou _ quebraria a formatação)."""
    partes = [texto[i:i + LIMITE_TEXTO] for i in range(0, len(texto), LIMITE_TEXTO)] or [""]
    for i, parte in enumerate(partes):
        parametros = {"chat_id": chat_id, "text": parte, "disable_web_page_preview": True}
        if botoes and i == len(partes) - 1:
            parametros["reply_markup"] = {"inline_keyboard": botoes}
        try:
            chamar("sendMessage", **parametros)
        except Exception as erro:  # noqa: BLE001 — resposta perdida não pode derrubar o processamento
            log.warning("Telegram: falha ao responder %s: %s", chat_id, erro)


def digitando(chat_id) -> None:
    try:
        chamar("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:  # noqa: BLE001
        pass


def responder_botao(callback_id: str, texto: str = "") -> None:
    try:
        chamar("answerCallbackQuery", callback_query_id=callback_id, text=texto[:190])
    except Exception:  # noqa: BLE001
        pass


def usuario_do_bot() -> str:
    if "username" not in _eu:
        _eu["username"] = (chamar("getMe") or {}).get("username", "")
    return _eu["username"]


def ativar_webhook(url_base: str) -> str:
    url = url_base.rstrip("/") + "/telegram/webhook"
    chamar("setWebhook", url=url, secret_token=segredo_webhook(),
           allowed_updates=["message", "callback_query"], drop_pending_updates=False)
    chamar("setMyCommands", commands=[
        {"command": "obra", "description": "Escolher a obra padrão"},
        {"command": "ajuda", "description": "Como usar o bot"},
    ])
    return url


def estado_webhook() -> dict:
    return chamar("getWebhookInfo") or {}
