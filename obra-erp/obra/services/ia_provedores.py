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
import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

from .. import config

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
TEMPO_LIMITE = 75  # segundos — nota fiscal em PDF de várias páginas demora (e o gunicorn corta em 120)
ERROS_DE_REDE = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)

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


class FalhaProvedor(Exception):
    """Falha de um provedor, já com o motivo em português (vai para a tela)."""

    def __init__(self, motivo: str, codigo: int = 0, detalhe: str = ""):
        super().__init__(motivo)
        self.codigo = codigo
        self.detalhe = detalhe


def _motivo_rede(erro) -> str:
    razao = str(getattr(erro, "reason", erro) or erro)
    if isinstance(erro, TimeoutError) or "timed out" in razao.lower():
        return f"tempo esgotado ({TEMPO_LIMITE}s sem resposta)"
    if "CERTIFICATE" in razao.upper():
        return "erro de certificado SSL"
    if any(t in razao for t in ("Name or service", "getaddrinfo", "nodename", "Temporary failure in name")):
        return "endereço não encontrado (DNS)"
    return razao[:100] or type(erro).__name__


def _falha_http(erro: urllib.error.HTTPError) -> FalhaProvedor:
    """Traduz o erro HTTP do Google, lendo a mensagem que ele manda no corpo."""
    detalhe = ""
    try:
        detalhe = (json.loads(erro.read().decode("utf-8")).get("error") or {}).get("message", "")
    except Exception:  # noqa: BLE001 — corpo vazio ou não-JSON
        pass
    codigo = erro.code
    baixo = detalhe.lower()
    if "api key" in baixo or "api_key" in baixo:
        motivo = "GEMINI_API_KEY inválida — confira a chave no Render"
    elif codigo == 403:
        motivo = "chave sem permissão para a API do Gemini"
    elif codigo == 404:
        motivo = f"modelo \"{config.GEMINI_MODEL}\" não encontrado (ajuste GEMINI_MODEL)"
    elif codigo == 429:
        motivo = "limite gratuito atingido"
    else:
        motivo = f"HTTP {codigo}" + (f": {detalhe[:120]}" if detalhe else "")
    return FalhaProvedor(motivo, codigo, detalhe)


def descrever_falha(erro: Exception) -> str:
    if isinstance(erro, FalhaProvedor):
        return str(erro)
    if isinstance(erro, urllib.error.HTTPError):
        return _falha_http(erro).args[0]
    if isinstance(erro, json.JSONDecodeError):
        return "resposta ilegível"
    if isinstance(erro, ERROS_DE_REDE):
        return "sem conexão: " + _motivo_rede(erro)
    return f"indisponível ({type(erro).__name__}: {str(erro)[:100]})"


def provedores_configurados() -> list[str]:
    chaves = {"gemini": config.GEMINI_API_KEY, "anthropic": config.ANTHROPIC_API_KEY}
    return [p for p in config.IA_ORDEM if chaves.get(p)]


def disponivel() -> bool:
    return bool(provedores_configurados())


# ---------------------------------------------------------------- Gemini

# Quanto o modelo "pensa" antes de responder. Os Gemini 3 vêm com raciocínio
# alto ligado por padrão: ler um cupom levava mais de um minuto (e estourava o
# tempo limite). Ler nota, ouvir áudio e montar lançamentos não precisa disso.
# Cada geração do modelo aceita um parâmetro diferente (thinkingLevel no 3.x,
# thinkingBudget no 2.5); tentamos na ordem e guardamos o que o modelo aceitou.
PENSAMENTO = {
    "minimo": [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}, {"thinkingBudget": 0}, None],
    "baixo": [{"thinkingLevel": "low"}, {"thinkingBudget": 1024}, None],
}
_aceito: dict[tuple[str, str], int] = {}
_local = threading.local()


def _modo_pensamento() -> str:
    return getattr(_local, "pensar", "baixo")


def _chamar_gemini(sistema: str, texto: str, arquivos: list[dict], esquema: dict | None,
                   max_tokens: int) -> str:
    partes = [{"inlineData": {"mimeType": a["mime"],
                              "data": base64.b64encode(a["dados"]).decode("ascii")}}
              for a in arquivos if a["mime"] in MIME_GEMINI]
    partes.append({"text": texto})

    def enviar(dados: dict) -> dict:
        req = urllib.request.Request(
            GEMINI_URL.format(modelo=config.GEMINI_MODEL),
            data=json.dumps(dados).encode("utf-8"),
            headers={"content-type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY},
            method="POST")
        for tentativa in (1, 2):
            inicio = time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=TEMPO_LIMITE) as resposta:
                    return json.loads(resposta.read().decode("utf-8"))
            except urllib.error.HTTPError as erro:
                raise _falha_http(erro) from erro
            except ERROS_DE_REDE as erro:
                # Queda rápida (conexão recusada, DNS momentâneo) merece uma
                # segunda tentativa; tempo esgotado não — dobraria a espera.
                if tentativa == 2 or time.monotonic() - inicio > 10:
                    raise FalhaProvedor("sem conexão: " + _motivo_rede(erro)) from erro
                log.warning("Gemini: falha de rede (%s), tentando de novo", erro)
                time.sleep(1.5)
        raise FalhaProvedor("sem conexão")

    modo = _modo_pensamento()
    variantes = PENSAMENTO.get(modo, PENSAMENTO["baixo"])
    chave = (config.GEMINI_MODEL, modo)
    guardado = _aceito.get(chave, 0)
    # Tentativas em ordem: com esquema estruturado, cada forma de controlar o
    # raciocínio (começando pela que já deu certo); depois, sem o esquema.
    tentativas = [(True, i) for i in range(guardado, len(variantes))] if esquema is not None else []
    tentativas += [(False, i) for i in range(len(variantes))]

    for numero, (com_esquema, indice) in enumerate(tentativas):
        geracao = {"maxOutputTokens": max_tokens}
        if variantes[indice] is not None:
            geracao["thinkingConfig"] = variantes[indice]
        if esquema is not None:
            geracao["responseMimeType"] = "application/json"
            if com_esquema:
                geracao["responseJsonSchema"] = esquema
        corpo = {"systemInstruction": {"parts": [{"text": sistema}]},
                 "contents": [{"role": "user", "parts": partes}],
                 "generationConfig": geracao}
        try:
            dados = enviar(corpo)
            if com_esquema or esquema is None:
                _aceito[chave] = indice
            break
        except FalhaProvedor as erro:
            # 400 = o modelo não conhece algum parâmetro: tenta a próxima forma.
            if erro.codigo != 400 or "API_KEY" in erro.detalhe.upper().replace(" ", "_") \
                    or numero == len(tentativas) - 1:
                raise

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
        "output_config": {"effort": "low" if _modo_pensamento() == "minimo" else "medium"},
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
          esquema: dict | None = None, max_tokens: int = 8192,
          pensar: str = "baixo") -> tuple[object, str]:
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
    _local.pensar = pensar
    for provedor in provedores:
        try:
            bruto = CHAMADAS[provedor](sistema, texto, arquivos, esquema, max_tokens)
            if not bruto:
                raise ValueError("resposta vazia")
            return (_extrair_json(bruto) if esquema is not None else bruto), provedor
        except Exception as erro:  # noqa: BLE001 — qualquer falha passa para o próximo
            motivo = descrever_falha(erro)
            falhas.append(f"{NOMES[provedor]}: {motivo}")
            log.warning("IA %s falhou: %s (%r)", provedor, motivo, erro)

    raise IaIndisponivel("A IA não respondeu agora (" + "; ".join(falhas) + "). "
                         "Tente de novo em instantes.")


def testar() -> list[dict]:
    """Pedido mínimo a cada provedor configurado: mostra se responde e quanto demora."""
    resultados = []
    for provedor in provedores_configurados():
        inicio = time.perf_counter()
        try:
            _local.pensar = "minimo"
            resposta = CHAMADAS[provedor]("Responda só com a palavra: ok", "teste de conexão", [], None, 256)
            resultados.append({"provedor": NOMES[provedor], "ok": True,
                               "ms": round((time.perf_counter() - inicio) * 1000),
                               "modelo": config.GEMINI_MODEL if provedor == "gemini" else config.ANTHROPIC_MODEL,
                               "resposta": (resposta or "")[:40]})
        except Exception as erro:  # noqa: BLE001
            resultados.append({"provedor": NOMES[provedor], "ok": False,
                               "ms": round((time.perf_counter() - inicio) * 1000),
                               "erro": descrever_falha(erro)})
            log.warning("Teste da IA %s falhou: %r", provedor, erro)
    return resultados
