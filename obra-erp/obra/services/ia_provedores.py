# -*- coding: utf-8 -*-
"""Provedores de IA — Gemini (gratuito) primeiro, Claude (pago) como reserva.

Todo pedido de IA do app passa por aqui. Quem chama entrega um prompt de
sistema, o texto do usuário e, se houver, arquivos (foto da obra, nota fiscal
em foto ou PDF). Este módulo tenta os provedores na ordem de `IA_ORDEM` e
devolve o primeiro que responder.

Por que dois provedores:

* **Gemini** tem camada gratuita com leitura de imagem e PDF — cobre o uso de
  uma obra sem custo. Mas a cota gratuita tem limite por minuto e por dia, e
  um 429 no meio do lançamento não pode travar o gestor.
* **Claude** entra quando o Gemini falha (limite, fora do ar, resposta
  ilegível) ou quando não há chave do Gemini. É pago por uso, então só é
  acionado quando precisa.

Nenhuma dependência é obrigatória: o Gemini é chamado por HTTP com a
biblioteca padrão e o SDK `anthropic` só é importado se a chave existir.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request

from .. import config

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
TEMPO_LIMITE = 90  # segundos — nota fiscal em PDF de várias páginas demora

# Formatos que cada provedor lê. O que ficar fora é avisado, não quebra o pedido.
# Áudio (nota de voz do Telegram, gravação do celular) só o Gemini entende.
MIME_AUDIO = {"audio/ogg", "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/aac",
              "audio/flac", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/webm"}
MIME_GEMINI = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
               "application/pdf"} | MIME_AUDIO
MIME_CLAUDE_IMAGEM = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Modelos do Claude que aceitam a reserva automática do servidor quando um
# filtro de segurança recusa o pedido (parâmetro `fallbacks`).
MODELOS_COM_RESERVA = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class IaIndisponivel(Exception):
    """Nenhum provedor respondeu. A mensagem já vem pronta para o usuário."""


def provedores_configurados() -> list[str]:
    chaves = {"gemini": config.GEMINI_API_KEY, "anthropic": config.ANTHROPIC_API_KEY}
    return [p for p in config.IA_ORDEM if chaves.get(p)]


def disponivel() -> bool:
    return bool(provedores_configurados())


# ---------------------------------------------------------------- Gemini

def _chamar_gemini(sistema: str, texto: str, arquivos: list[dict], esquema: dict | None,
                   max_tokens: int) -> str:
    partes = [{"inlineData": {"mimeType": a["mime"],
                              "data": base64.b64encode(a["dados"]).decode("ascii")}}
              for a in arquivos if a["mime"] in MIME_GEMINI]
    partes.append({"text": texto})

    geracao = {"maxOutputTokens": max_tokens}
    if esquema is not None:
        geracao["responseMimeType"] = "application/json"
        geracao["responseJsonSchema"] = esquema

    corpo = {
        "systemInstruction": {"parts": [{"text": sistema}]},
        "contents": [{"role": "user", "parts": partes}],
        "generationConfig": geracao,
    }

    def enviar(dados: dict) -> dict:
        req = urllib.request.Request(
            GEMINI_URL.format(modelo=config.GEMINI_MODEL),
            data=json.dumps(dados).encode("utf-8"),
            headers={"content-type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY},
            method="POST")
        with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as resposta:
            return json.loads(resposta.read().decode("utf-8"))

    try:
        dados = enviar(corpo)
    except urllib.error.HTTPError as erro:
        # Versões do modelo que não conhecem o esquema estruturado devolvem 400:
        # o prompt já descreve o formato, então basta pedir JSON sem o esquema.
        if erro.code != 400 or "responseJsonSchema" not in geracao:
            raise
        geracao.pop("responseJsonSchema")
        dados = enviar(corpo)

    candidatos = dados.get("candidates") or []
    if not candidatos:
        motivo = (dados.get("promptFeedback") or {}).get("blockReason") or "sem resposta"
        raise ValueError(f"Gemini não respondeu ({motivo}).")
    partes_resposta = (candidatos[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in partes_resposta if not p.get("thought")).strip()


# ---------------------------------------------------------------- Claude

def _chamar_anthropic(sistema: str, texto: str, arquivos: list[dict], esquema: dict | None,
                      max_tokens: int) -> str:
    import anthropic  # importado só aqui: sem chave, o app nem precisa do pacote

    conteudo = []
    for a in arquivos:
        dados = base64.standard_b64encode(a["dados"]).decode("ascii")
        if a["mime"] == "application/pdf":
            conteudo.append({"type": "document",
                             "source": {"type": "base64", "media_type": "application/pdf", "data": dados}})
        elif a["mime"] in MIME_CLAUDE_IMAGEM:
            conteudo.append({"type": "image",
                             "source": {"type": "base64", "media_type": a["mime"], "data": dados}})
    conteudo.append({"type": "text", "text": texto})

    parametros = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max(max_tokens, 16000),
        "system": sistema,
        "messages": [{"role": "user", "content": conteudo}],
        "output_config": {"effort": "medium"},
    }
    if esquema is not None:
        parametros["output_config"]["format"] = {"type": "json_schema", "schema": esquema}
    if config.ANTHROPIC_MODEL in MODELOS_COM_RESERVA:
        # Se um filtro de segurança recusar por engano, o próprio servidor
        # refaz o pedido num modelo adequado, na mesma chamada.
        parametros["betas"] = ["server-side-fallback-2026-07-01"]
        parametros["fallbacks"] = "default"

    cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=TEMPO_LIMITE, max_retries=1)
    resposta = cliente.beta.messages.create(**parametros)
    if resposta.stop_reason == "refusal":
        raise ValueError("Claude recusou o pedido.")
    return "\n".join(b.text for b in resposta.content if b.type == "text").strip()


CHAMADAS = {"gemini": _chamar_gemini, "anthropic": _chamar_anthropic}
NOMES = {"gemini": "Gemini", "anthropic": "Claude"}


# ---------------------------------------------------------------- interface

def _extrair_json(texto: str):
    """Aceita JSON puro ou dentro de bloco ```json — modelos às vezes embrulham."""
    limpo = texto.strip()
    cerca = re.search(r"```(?:json)?\s*(.*?)```", limpo, re.S)
    if cerca:
        limpo = cerca.group(1).strip()
    if not limpo.startswith(("{", "[")):
        inicio = limpo.find("{")
        fim = limpo.rfind("}")
        if inicio >= 0 and fim > inicio:
            limpo = limpo[inicio:fim + 1]
    return json.loads(limpo)


def gerar(sistema: str, texto: str, arquivos: list[dict] | None = None,
          esquema: dict | None = None, max_tokens: int = 8192) -> tuple[object, str]:
    """Pede uma resposta ao primeiro provedor que funcionar.

    `arquivos`: [{"nome", "mime", "dados": bytes}]. Com `esquema`, a resposta
    volta já convertida de JSON; sem ele, volta o texto. Devolve
    (resposta, nome_do_provedor).
    """
    arquivos = arquivos or []
    provedores = provedores_configurados()
    if not provedores:
        raise IaIndisponivel(
            "Assistente de IA desligado: configure GEMINI_API_KEY (gratuita) ou "
            "ANTHROPIC_API_KEY no servidor.")
    if any(a["mime"] in MIME_AUDIO for a in arquivos):
        # O Claude ignoraria o áudio e responderia sem ouvi-lo: melhor falhar.
        provedores = [p for p in provedores if p == "gemini"]
        if not provedores:
            raise IaIndisponivel("Para entender áudio é preciso a GEMINI_API_KEY. "
                                 "Mande a mensagem por texto.")

    falhas = []
    for provedor in provedores:
        try:
            bruto = CHAMADAS[provedor](sistema, texto, arquivos, esquema, max_tokens)
            if not bruto:
                raise ValueError("resposta vazia")
            return (_extrair_json(bruto) if esquema is not None else bruto), provedor
        except urllib.error.HTTPError as erro:
            detalhe = f"HTTP {erro.code}"
            if erro.code == 429:
                detalhe = "limite gratuito atingido"
            falhas.append(f"{NOMES[provedor]}: {detalhe}")
            log.warning("IA %s falhou: %s", provedor, detalhe)
        except json.JSONDecodeError as erro:
            falhas.append(f"{NOMES[provedor]}: resposta ilegível")
            log.warning("IA %s devolveu JSON inválido: %s", provedor, erro)
        except (urllib.error.URLError, TimeoutError) as erro:
            falhas.append(f"{NOMES[provedor]}: sem conexão")
            log.warning("IA %s inacessível: %s", provedor, erro)
        except Exception as erro:  # noqa: BLE001 — qualquer falha passa para o próximo
            falhas.append(f"{NOMES[provedor]}: indisponível")
            log.warning("IA %s falhou: %s", provedor, erro)

    raise IaIndisponivel("A IA não respondeu agora (" + "; ".join(falhas) + "). "
                         "Tente de novo em instantes ou lance pelo formulário.")
