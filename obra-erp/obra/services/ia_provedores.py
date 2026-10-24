# -*- coding: utf-8 -*-
"""Provedores de IA — Gemini (gratuito) primeiro, Claude (pago) como reserva opcional.

Todo pedido de IA do app passa por aqui. Quem chama entrega um prompt de
sistema, o texto do usuário e, se houver, arquivos (foto, PDF de nota, áudio).

Como cada pedido é atendido:

1. **Gemini, modelo principal** (`GEMINI_MODEL`, padrão Flash-Lite: o mais
   rápido da camada gratuita, bom em ler cupom e ouvir áudio).
2. **Gemini, modelo reserva** (`GEMINI_MODELO_RESERVA`, padrão Flash) se o
   principal demorar, estourar a cota (que é contada por modelo) ou falhar.
3. **Claude**, só se houver `ANTHROPIC_API_KEY` (pago; não ouve áudio).

Tudo dentro de um **prazo total** por pedido: o bot do Telegram pode esperar
mais; a tela não pode passar do limite do gunicorn.

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
TEMPO_MAXIMO_TENTATIVA = 70   # segundos por modelo
PRAZO_PADRAO = 100            # segundos por pedido (a tela: o gunicorn corta em 120)
ERROS_DE_REDE = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)
# Falhas em que vale tentar o outro modelo: o problema é daquele modelo, não da chave.
CODIGOS_TROCA_MODELO = {404, 408, 429, 500, 502, 503, 504}

# Formatos que cada provedor lê. O que ficar fora é ignorado, não quebra o pedido.
MIME_AUDIO = {"audio/ogg", "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/aac",
              "audio/flac", "audio/mp4", "audio/m4a", "audio/x-m4a", "audio/webm"}
MIME_GEMINI = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
               "application/pdf"} | MIME_AUDIO
MIME_CLAUDE_IMAGEM = {"image/jpeg", "image/png", "image/gif", "image/webp"}

# Modelos do Claude que aceitam a reserva automática do servidor quando um
# filtro de segurança recusa o pedido (parâmetro `fallbacks`).
MODELOS_COM_RESERVA = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}

# Quanto o modelo "pensa" antes de responder. Os Gemini 3 vêm com raciocínio
# alto por padrão — ler um cupom passava de um minuto. Cada geração aceita um
# parâmetro diferente (thinkingLevel no 3.x, thinkingBudget no 2.5): tentamos
# na ordem e guardamos o que cada modelo aceitou.
PENSAMENTO = {
    "minimo": [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}, {"thinkingBudget": 0}, None],
    "baixo": [{"thinkingLevel": "low"}, {"thinkingBudget": 1024}, None],
}
_aceito: dict[tuple[str, str], int] = {}
_local = threading.local()


class IaIndisponivel(Exception):
    """Nenhum provedor respondeu. A mensagem já vem pronta para o usuário."""


class FalhaProvedor(Exception):
    """Falha de um provedor, já com o motivo em português (vai para a tela)."""

    def __init__(self, motivo: str, codigo: int = 0, detalhe: str = "", rede: bool = False):
        super().__init__(motivo)
        self.codigo = codigo
        self.detalhe = detalhe
        self.rede = rede


# ---------------------------------------------------------------- motivos

def _motivo_rede(erro, segundos: float = 0) -> str:
    razao = str(getattr(erro, "reason", erro) or erro)
    if isinstance(erro, TimeoutError) or "timed out" in razao.lower():
        return f"tempo esgotado ({round(segundos) or TEMPO_MAXIMO_TENTATIVA}s sem resposta)"
    if "CERTIFICATE" in razao.upper():
        return "erro de certificado SSL"
    if any(t in razao for t in ("Name or service", "getaddrinfo", "nodename", "Temporary failure in name")):
        return "endereço não encontrado (DNS)"
    return razao[:100] or type(erro).__name__


def _falha_http(erro: urllib.error.HTTPError, modelo: str = "") -> FalhaProvedor:
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
        motivo = f"modelo \"{modelo or config.GEMINI_MODEL}\" não encontrado"
    elif codigo == 429:
        motivo = "limite gratuito atingido"
    elif codigo >= 500:
        motivo = "servidor do Google instável"
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


def modelos_gemini() -> list[str]:
    modelos = [config.GEMINI_MODEL]
    if config.GEMINI_MODELO_RESERVA and config.GEMINI_MODELO_RESERVA != config.GEMINI_MODEL:
        modelos.append(config.GEMINI_MODELO_RESERVA)
    return modelos


def _restante() -> float:
    return getattr(_local, "limite", time.monotonic() + PRAZO_PADRAO) - time.monotonic()


# ---------------------------------------------------------------- Gemini

def _gemini_enviar(modelo: str, corpo: dict, tempo: float) -> dict:
    req = urllib.request.Request(
        GEMINI_URL.format(modelo=modelo), data=json.dumps(corpo).encode("utf-8"),
        headers={"content-type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY},
        method="POST")
    for tentativa in (1, 2):
        inicio = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=tempo) as resposta:
                return json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as erro:
            raise _falha_http(erro, modelo) from erro
        except ERROS_DE_REDE as erro:
            gasto = time.monotonic() - inicio
            # Queda rápida (conexão recusada, DNS momentâneo) merece outra
            # tentativa; tempo esgotado não — dobraria a espera.
            if tentativa == 2 or gasto > 10:
                raise FalhaProvedor("sem conexão: " + _motivo_rede(erro, gasto), rede=True) from erro
            log.warning("Gemini %s: falha de rede (%s), tentando de novo", modelo, erro)
            time.sleep(1.5)
    raise FalhaProvedor("sem conexão", rede=True)


def _gemini_modelo(modelo: str, sistema: str, partes: list, esquema: dict | None, max_tokens: int,
                   tempo: float) -> str:
    modo = getattr(_local, "pensar", "baixo")
    variantes = PENSAMENTO.get(modo, PENSAMENTO["baixo"])
    chave = (modelo, modo)
    guardado = _aceito.get(chave, 0)
    # Com esquema estruturado, cada forma de controlar o raciocínio (começando
    # pela que já deu certo); depois, sem o esquema (o prompt descreve o JSON).
    tentativas = [(True, i) for i in range(guardado, len(variantes))] if esquema is not None else []
    tentativas += [(False, i) for i in range(len(variantes))]
    inicio = time.monotonic()

    for numero, (com_esquema, indice) in enumerate(tentativas):
        geracao = {"maxOutputTokens": max_tokens}
        if variantes[indice] is not None:
            geracao["thinkingConfig"] = variantes[indice]
        if esquema is not None:
            geracao["responseMimeType"] = "application/json"
            if com_esquema:
                geracao["responseJsonSchema"] = esquema
        corpo = {"systemInstruction": {"parts": [{"text": sistema}]},
                 "contents": [{"role": "user", "parts": partes}], "generationConfig": geracao}
        try:
            dados = _gemini_enviar(modelo, corpo, max(tempo - (time.monotonic() - inicio), 5))
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
        raise FalhaProvedor(f"o Gemini não respondeu ({motivo})")
    partes_resposta = (candidatos[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in partes_resposta if not p.get("thought")).strip()


def _chamar_gemini(sistema: str, texto: str, arquivos: list[dict], esquema: dict | None,
                   max_tokens: int) -> str:
    partes = [{"inlineData": {"mimeType": a["mime"], "data": base64.b64encode(a["dados"]).decode("ascii")}}
              for a in arquivos if a["mime"] in MIME_GEMINI]
    partes.append({"text": texto})

    modelos = modelos_gemini()
    falhas = []
    for i, modelo in enumerate(modelos):
        restante = _restante()
        if restante < 8:
            break
        # O principal fica com parte do prazo; o resto é do reserva.
        tempo = min(TEMPO_MAXIMO_TENTATIVA, restante * (0.6 if i + 1 < len(modelos) else 1.0))
        inicio = time.monotonic()
        try:
            texto_resposta = _gemini_modelo(modelo, sistema, partes, esquema, max_tokens, tempo)
            _local.modelo = modelo
            log.info("Gemini %s respondeu em %.1fs", modelo, time.monotonic() - inicio)
            return texto_resposta
        except FalhaProvedor as erro:
            log.warning("Gemini %s falhou em %.1fs: %s", modelo, time.monotonic() - inicio, erro)
            falhas.append(f"{modelo}: {erro}")
            if not (erro.rede or erro.codigo in CODIGOS_TROCA_MODELO or not erro.codigo):
                raise
    raise FalhaProvedor("; ".join(falhas) or "prazo esgotado", rede=True)


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

    # Sem saída estruturada: o esquema do assistente tem objetos livres
    # ("dados" de cada ação), que o modo estrito não aceita. O prompt
    # descreve o JSON e a resposta é conferida do mesmo jeito.
    parametros = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max(max_tokens, 16000),
        "system": sistema,
        "messages": [{"role": "user", "content": conteudo}],
        "output_config": {"effort": "low" if getattr(_local, "pensar", "baixo") == "minimo" else "medium"},
    }
    if config.ANTHROPIC_MODEL in MODELOS_COM_RESERVA:
        # Se um filtro de segurança recusar por engano, o próprio servidor
        # refaz o pedido num modelo adequado, na mesma chamada.
        parametros["betas"] = ["server-side-fallback-2026-07-01"]
        parametros["fallbacks"] = "default"

    cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                                  timeout=max(min(_restante(), TEMPO_MAXIMO_TENTATIVA), 10), max_retries=1)
    resposta = cliente.beta.messages.create(**parametros)
    if resposta.stop_reason == "refusal":
        raise FalhaProvedor("o Claude recusou o pedido")
    _local.modelo = config.ANTHROPIC_MODEL
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
          pensar: str = "baixo", prazo: float = PRAZO_PADRAO) -> tuple[object, str]:
    """Pede uma resposta ao primeiro provedor que funcionar, dentro do prazo.

    `arquivos`: [{"nome", "mime", "dados": bytes}]. Com `esquema`, a resposta
    volta já convertida de JSON; sem ele, volta o texto. Devolve
    (resposta, nome_do_provedor).
    """
    arquivos = arquivos or []
    provedores = provedores_configurados()
    if not provedores:
        raise IaIndisponivel(
            "Assistente de IA desligado: configure GEMINI_API_KEY (gratuita) no servidor.")
    if any(a["mime"] in MIME_AUDIO for a in arquivos):
        # O Claude ignoraria o áudio e responderia sem ouvi-lo: melhor falhar.
        provedores = [p for p in provedores if p == "gemini"]
        if not provedores:
            raise IaIndisponivel("Para entender áudio é preciso a GEMINI_API_KEY. "
                                 "Mande a mensagem por texto.")

    _local.pensar = pensar
    _local.limite = time.monotonic() + prazo
    falhas = []
    try:
        for provedor in provedores:
            if _restante() < 8:
                break
            try:
                bruto = CHAMADAS[provedor](sistema, texto, arquivos, esquema, max_tokens)
                if not bruto:
                    raise FalhaProvedor("resposta vazia")
                return (_extrair_json(bruto) if esquema is not None else bruto), provedor
            except Exception as erro:  # noqa: BLE001 — qualquer falha passa para o próximo
                motivo = descrever_falha(erro)
                falhas.append(f"{NOMES[provedor]}: {motivo}")
                log.warning("IA %s falhou: %s (%r)", provedor, motivo, erro)
    finally:
        _local.__dict__.pop("limite", None)

    raise IaIndisponivel("A IA não respondeu agora (" + ("; ".join(falhas) or "prazo esgotado")
                         + "). Tente de novo em instantes.")


def modelo_usado() -> str:
    return getattr(_local, "modelo", "")


def testar() -> list[dict]:
    """Pedido mínimo a cada modelo configurado: diz se responde e em quanto tempo."""
    alvos = []
    if config.GEMINI_API_KEY and "gemini" in config.IA_ORDEM:
        alvos += [("Gemini", m) for m in modelos_gemini()]
    if config.ANTHROPIC_API_KEY and "anthropic" in config.IA_ORDEM:
        alvos.append(("Claude", config.ANTHROPIC_MODEL))

    resultados = []
    for nome, modelo in alvos:
        _local.pensar = "minimo"
        _local.limite = time.monotonic() + 40
        inicio = time.perf_counter()
        try:
            if nome == "Gemini":
                resposta = _gemini_modelo(modelo, "Responda só com a palavra: ok",
                                          [{"text": "teste de conexão"}], None, 256, 35)
            else:
                resposta = _chamar_anthropic("Responda só com a palavra: ok", "teste de conexão", [], None, 256)
            resultados.append({"provedor": nome, "modelo": modelo, "ok": True,
                               "ms": round((time.perf_counter() - inicio) * 1000), "resposta": resposta[:40]})
        except Exception as erro:  # noqa: BLE001
            resultados.append({"provedor": nome, "modelo": modelo, "ok": False,
                               "ms": round((time.perf_counter() - inicio) * 1000),
                               "erro": descrever_falha(erro)})
            log.warning("Teste da IA %s (%s) falhou: %r", nome, modelo, erro)
        finally:
            _local.__dict__.pop("limite", None)
    return resultados
