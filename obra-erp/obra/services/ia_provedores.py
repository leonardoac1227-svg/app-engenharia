# -*- coding: utf-8 -*-
"""Provedores de IA — Claude primeiro, Gemini de reserva, Grok para áudio.

Todo pedido de IA do app passa por aqui. Quem chama entrega um prompt de
sistema, o texto do usuário e, se houver, arquivos (foto, PDF de nota, áudio).

Como cada pedido é atendido:

1. **Claude** (`ANTHROPIC_MODEL`), se houver `ANTHROPIC_API_KEY`: texto, foto e PDF.
2. **Gemini, modelo principal** (`GEMINI_MODEL`, padrão Flash-Lite), se o Claude
   falhar ou não ler o formato (HEIC do iPhone).
3. **Gemini, modelo reserva** (`GEMINI_MODELO_RESERVA`, padrão Flash) se o
   principal demorar, estourar a cota (que é contada por modelo) ou falhar.

Áudio: o **Grok** (`XAI_API_KEY`) transcreve; sem chave ou se falhar, o Gemini.
A ordem muda com `IA_ORDEM`.

Tudo dentro de um **prazo total** por pedido: o bot do Telegram pode esperar
mais; a tela não pode passar do limite do gunicorn.

Nenhuma dependência é obrigatória: o Gemini é chamado por HTTP com a
biblioteca padrão e o SDK `anthropic` só é importado se a chave existir.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

from .. import config

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
TEMPO_MAXIMO_TENTATIVA = 45   # segundos por modelo
PRAZO_PADRAO = 100            # segundos por pedido (a tela: o gunicorn corta em 120)
TEMPO_CLAUDE_COM_RESERVA = 40  # o Gemini ainda precisa caber no prazo se o Claude travar
XAI_STT_URL = "https://api.x.ai/v1/stt"
XAI_STT_MODELO = "grok-voice-transcribe-2.0"
XAI_MAXIMO_TERMOS, XAI_TAMANHO_TERMO = 100, 50   # limites da API para "keyterm"
TEMPO_XAI = 20
ERROS_DE_REDE = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)
# Falhas em que vale tentar o outro modelo: o problema é daquele modelo, não da chave.
CODIGOS_TROCA_MODELO = {404, 408, 429, 500, 502, 503, 504}
# O Google recusa pedidos em picos de uso (503) e erros 5xx costumam passar em
# segundos: desistir na primeira perdia o lote inteiro de fotos.
ESPERAS_INSTAVEL = (2, 5)     # segundos antes de repetir o mesmo modelo
PRAZO_MINIMO_REPETIR = 15     # segundos que precisam sobrar para valer repetir
ESPERA_NOVA_RODADA = 10       # segundos antes de voltar ao modelo principal

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
        motivo = f"servidor do Google instável (HTTP {codigo})"
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


@contextlib.contextmanager
def somente(provedor: str):
    """Nesta thread, só este provedor responde (comparação: a reserva esconderia quem falhou)."""
    _local.somente = provedor
    try:
        yield
    finally:
        _local.__dict__.pop("somente", None)


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
    quedas_de_rede = 0
    esperas = list(ESPERAS_INSTAVEL)
    while True:
        inicio = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=tempo) as resposta:
                return json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as erro:
            falha = _falha_http(erro, modelo)
            log.warning("Gemini %s: HTTP %s: %s", modelo, falha.codigo, falha.detalhe[:300])
            if falha.codigo < 500 or not esperas or _restante() < PRAZO_MINIMO_REPETIR:
                raise falha from erro
            time.sleep(esperas.pop(0))
        except ERROS_DE_REDE as erro:
            gasto = time.monotonic() - inicio
            quedas_de_rede += 1
            # Queda rápida (conexão recusada, DNS momentâneo) merece outra
            # tentativa; tempo esgotado não — dobraria a espera.
            if quedas_de_rede == 2 or gasto > 10:
                raise FalhaProvedor("sem conexão: " + _motivo_rede(erro, gasto), rede=True) from erro
            log.warning("Gemini %s: falha de rede (%s), tentando de novo", modelo, erro)
            time.sleep(1.5)


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
    # Em pico de demanda os dois modelos recusam juntos e voltam em poucos
    # segundos: com prazo sobrando (no bot, sobra), uma nova rodada evita
    # devolver o trabalho ao usuário.
    for rodada in (1, 2):
        if rodada == 2:
            if _restante() < ESPERA_NOVA_RODADA + PRAZO_MINIMO_REPETIR:
                break
            time.sleep(ESPERA_NOVA_RODADA)
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
    raise FalhaProvedor("; ".join(dict.fromkeys(falhas)) or "prazo esgotado", rede=True)


# ---------------------------------------------------------------- Claude

_cliente_claude: tuple | None = None
_trava_cliente = threading.Lock()


def cliente_claude():
    """Um cliente para o processo inteiro.

    Cada cliente novo monta um contexto SSL próprio, e no servidor (Python 3.14)
    essa montagem dentro de uma thread travava o processo. `preparar()` cria o
    cliente na partida, fora das threads do bot.
    """
    global _cliente_claude
    import anthropic  # importado só aqui: sem chave, o app nem precisa do pacote
    marca = (config.ANTHROPIC_API_KEY, anthropic.Anthropic)
    with _trava_cliente:
        if _cliente_claude is None or _cliente_claude[0] != marca:
            _cliente_claude = (marca, anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=0))
        return _cliente_claude[1]


def preparar() -> None:
    if config.ANTHROPIC_API_KEY:
        try:
            cliente_claude()
        except Exception as erro:  # noqa: BLE001 — sem o Claude, o app sobe com o Gemini
            log.warning("Claude indisponível: %r", erro)


def le_todos(provedor: str, arquivos: list[dict]) -> bool:
    """O provedor lê todos os arquivos? O que ele ignorasse viraria resposta sem ter visto a nota."""
    aceitos = MIME_GEMINI if provedor == "gemini" else MIME_CLAUDE_IMAGEM | {"application/pdf"}
    return all(a["mime"] in aceitos for a in arquivos)


def _chamar_anthropic(sistema: str, texto: str, arquivos: list[dict], esquema: dict | None,
                      max_tokens: int) -> str:
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
        # As regras não mudam de uma mensagem para outra: do cache, saem mais rápido e mais barato.
        "system": [{"type": "text", "text": sistema, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": conteudo}],
        "output_config": {"effort": "low" if getattr(_local, "pensar", "baixo") == "minimo"
                          else config.ANTHROPIC_ESFORCO},
    }
    if config.ANTHROPIC_MODEL in MODELOS_COM_RESERVA:
        # Se um filtro de segurança recusar por engano, o próprio servidor
        # refaz o pedido num modelo adequado, na mesma chamada.
        parametros["betas"] = ["server-side-fallback-2026-07-01"]
        parametros["fallbacks"] = "default"

    reserva = "gemini" in provedores_configurados()
    parametros["timeout"] = max(min(_restante() * (0.6 if reserva else 1.0),
                                    TEMPO_CLAUDE_COM_RESERVA if reserva else 90), 10)
    inicio = time.monotonic()
    resposta = cliente_claude().beta.messages.create(**parametros)
    if resposta.stop_reason == "refusal":
        raise FalhaProvedor("o Claude recusou o pedido")
    if resposta.stop_reason == "max_tokens":
        raise FalhaProvedor("a resposta do Claude veio cortada")
    _local.modelo = config.ANTHROPIC_MODEL
    uso = getattr(resposta, "usage", None)
    log.info("Claude %s respondeu em %.1fs (tokens: %s novos, %s do cache, %s de saída)",
             config.ANTHROPIC_MODEL, time.monotonic() - inicio, getattr(uso, "input_tokens", "?"),
             getattr(uso, "cache_read_input_tokens", "?"), getattr(uso, "output_tokens", "?"))
    return "\n".join(b.text for b in resposta.content if b.type == "text").strip()


def transcrever_xai(arquivo: dict, termos: list[str], prazo: float) -> str:
    """Áudio em texto pelo Grok. `termos`: nomes próprios que ele deve reconhecer."""
    fronteira = uuid.uuid4().hex
    campos = [("model", XAI_STT_MODELO), ("language", "pt"), ("format", "true")]
    unicos = dict.fromkeys(t.strip() for t in termos if t and t.strip() and len(t.strip()) <= XAI_TAMANHO_TERMO)
    campos += [("keyterm", t) for t in list(unicos)[:XAI_MAXIMO_TERMOS]]
    corpo = b"".join(f'--{fronteira}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'
                     .encode("utf-8") for n, v in campos)
    extensao = arquivo["mime"].split("/")[-1].replace("x-", "").replace("mpeg", "mp3")
    # O arquivo tem de ser o último campo: o que vem depois dele é ignorado.
    corpo += (f'--{fronteira}\r\nContent-Disposition: form-data; name="file"; filename="audio.{extensao}"\r\n'
              f'Content-Type: {arquivo["mime"]}\r\n\r\n').encode("utf-8")
    corpo += arquivo["dados"] + f"\r\n--{fronteira}--\r\n".encode("ascii")
    req = urllib.request.Request(XAI_STT_URL, data=corpo, method="POST", headers={
        "authorization": f"Bearer {config.XAI_API_KEY}",
        "content-type": f"multipart/form-data; boundary={fronteira}"})
    inicio = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=max(min(prazo, TEMPO_XAI), 5)) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", "replace")[:200]
        motivo = {401: "chave do Grok inválida", 403: "chave do Grok sem permissão ou sem crédito",
                  429: "limite do Grok atingido"}.get(erro.code, f"HTTP {erro.code}")
        raise FalhaProvedor(motivo, erro.code, detalhe) from erro
    except (*ERROS_DE_REDE, ValueError) as erro:
        raise FalhaProvedor(f"sem resposta do Grok: {erro}", rede=True) from erro
    log.info("Grok transcreveu em %.1fs", time.monotonic() - inicio)
    return str((dados or {}).get("text") or "").strip()


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
    if getattr(_local, "somente", ""):
        provedores = [p for p in provedores if p == _local.somente]
    if not provedores:
        raise IaIndisponivel(
            "Assistente de IA desligado: configure GEMINI_API_KEY (gratuita) no servidor.")
    if any(a["mime"] in MIME_AUDIO for a in arquivos):
        # O Claude ignoraria o áudio e responderia sem ouvi-lo: melhor falhar.
        provedores = [p for p in provedores if p == "gemini"]
        if not provedores:
            raise IaIndisponivel("Não consegui transcrever o áudio: para isso é preciso a XAI_API_KEY "
                                 "ou a GEMINI_API_KEY. Mande a mensagem por texto.")
    # Quem não lê algum dos arquivos fica para o fim (só responde se não houver outro).
    provedores = sorted(provedores, key=lambda p: not le_todos(p, arquivos))

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


def _silencio() -> bytes:
    """Um segundo de áudio mudo: prova a chave do Grok sem gastar com fala."""
    import io
    import wave
    saida = io.BytesIO()
    with wave.open(saida, "wb") as som:
        som.setnchannels(1)
        som.setsampwidth(2)
        som.setframerate(16000)
        som.writeframes(b"\x00" * 32000)
    return saida.getvalue()


def testar() -> list[dict]:
    """Pedido mínimo a cada modelo configurado: diz se responde e em quanto tempo."""
    alvos = []
    if config.GEMINI_API_KEY and "gemini" in config.IA_ORDEM:
        alvos += [("Gemini", m) for m in modelos_gemini()]
    if config.ANTHROPIC_API_KEY and "anthropic" in config.IA_ORDEM:
        alvos.append(("Claude", config.ANTHROPIC_MODEL))
    if config.XAI_API_KEY:
        alvos.append(("Grok", XAI_STT_MODELO))

    resultados = []
    for nome, modelo in alvos:
        _local.pensar = "minimo"
        _local.limite = time.monotonic() + 40
        inicio = time.perf_counter()
        try:
            if nome == "Gemini":
                resposta = _gemini_modelo(modelo, "Responda só com a palavra: ok",
                                          [{"text": "teste de conexão"}], None, 256, 35)
            elif nome == "Grok":
                resposta = transcrever_xai({"mime": "audio/wav", "dados": _silencio()}, [], 10) or "ok"
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
