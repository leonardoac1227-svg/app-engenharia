# -*- coding: utf-8 -*-
"""Bot do Telegram — o assistente de IA no chat do celular.

Fluxo de uma mensagem:

1. **Arquivos** (foto, print, PDF de nota, comprovante de PIX) ficam guardados
   esperando a instrução. O bot avisa uma vez que recebeu.
2. **Instrução** (texto, legenda ou áudio) dispara o processamento, depois de
   alguns segundos — tempo de chegarem as outras fotos do mesmo envio.
3. Áudios são transcritos e a obra é identificada pela fala (ou fica a obra
   padrão escolhida com /obra).
4. O assistente monta as ações e o `Executor` grava tudo pelos endpoints do
   app, como o usuário vinculado ao chat.
5. O bot responde o que gravou. Exclusão e nota repetida esperam um toque em
   botão; despesa sem valor espera o valor na próxima mensagem.

Só conversas vinculadas a um usuário do app são atendidas (código gerado em
Ajustes → Telegram). O que o bot pode fazer é exatamente o que o perfil desse
usuário pode fazer na tela.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, data_br, hoje_iso, moeda, numero, texto
from . import assistente, ia_provedores, telegram_api
from .executor_acoes import ORDEM, ErroAcao, Executor

log = logging.getLogger(__name__)

EM_SEGUNDO_PLANO = True      # os testes desligam para rodar tudo na mesma thread
ATRASO_LOTE = 3.0            # segundos esperando o resto das fotos do mesmo envio
VALIDADE_ARQUIVO = timedelta(hours=6)
VALIDADE_CODIGO = timedelta(minutes=20)
OCUPADO_POR = timedelta(minutes=4)   # teto de um processamento (IA + gravação)
ESPERA_MAXIMA = 300                  # segundos esperando a mensagem anterior terminar
MAX_HISTORICO = 8
MAX_PENDENTES = 10
_vistos: dict[int, float] = {}
_trava_vistos = threading.Lock()

MIME_DOCUMENTO = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
TITULOS = {"diario": "Diário", "ponto": "Ponto", "funcionario": "Cadastro", "despesa": "Despesa",
           "editar_despesa": "Correção", "pagar_despesa": "Baixa", "excluir_despesa": "Exclusão",
           "foto": "Fotos"}

AJUDA = """Sou o assistente da obra. Pode mandar do jeito que falaria com um colega:

📄 Nota fiscal, cupom, boleto ou comprovante de PIX (foto, print ou PDF) — eu leio número, valor e fornecedor.
🎙 Áudio dizendo para qual obra é e o que foi comprado: "esse cimento é da obra do Alphaville, 20 sacos CP-II".
📷 Fotos da obra + "relatório fotográfico de ontem" — anexo no dia certo.
🗣 Áudio do fim do dia: "Ademir fez 27 metros de reboco de manhã e à tarde arestamento..." — monto o diário e o ponto.

Mande os arquivos primeiro e depois o áudio ou texto com a instrução.

/obra — escolher a obra padrão
/ajuda — esta mensagem"""


# ---------------------------------------------------------------- vínculo

def gerar_codigo(usuario_id: int) -> str:
    executar("DELETE FROM telegram_codigos WHERE usuario_id = ? OR expira_em < ?",
             (usuario_id, agora_iso()))
    codigo = secrets.token_hex(4).upper()
    executar("INSERT INTO telegram_codigos (codigo, usuario_id, expira_em) VALUES (?, ?, ?)",
             (codigo, usuario_id, (datetime.now() + VALIDADE_CODIGO).isoformat(timespec="seconds")))
    return codigo


def vinculo(chat_id: str):
    linha = um("""SELECT c.chat_id, c.usuario_id, c.obra_id, c.historico, c.pendentes,
                         u.nome, u.papel, u.ativo
                    FROM telegram_chats c JOIN usuarios u ON u.id = c.usuario_id
                   WHERE c.chat_id = ?""", (chat_id,))
    if not linha or not linha["ativo"]:
        return None
    dados = dict(linha)
    for campo in ("historico", "pendentes"):
        try:
            dados[campo] = json.loads(dados[campo] or "[]")
        except ValueError:
            dados[campo] = []
    return dados


def _salvar(chat_id: str, **campos) -> None:
    valores = [json.dumps(v, ensure_ascii=False, default=str) if k in ("historico", "pendentes") else v
               for k, v in campos.items()]
    executar(f"UPDATE telegram_chats SET {', '.join(f'{k} = ?' for k in campos)}, atualizado_em = ? "
             "WHERE chat_id = ?", valores + [agora_iso(), chat_id])


def _vincular(chat_id: str, codigo: str) -> str:
    linha = um("SELECT usuario_id, expira_em FROM telegram_codigos WHERE codigo = ?",
               (codigo.strip().upper(),))
    if not linha or linha["expira_em"] < agora_iso():
        return "Código inválido ou vencido. Gere outro no app em Ajustes → Bot do Telegram."
    executar("DELETE FROM telegram_codigos WHERE codigo = ?", (codigo.strip().upper(),))
    executar("DELETE FROM telegram_chats WHERE chat_id = ?", (chat_id,))
    executar("""INSERT INTO telegram_chats (chat_id, usuario_id, obra_id, historico, pendentes,
                                             vinculado_em, atualizado_em)
                VALUES (?, ?, NULL, '[]', '[]', ?, ?)""",
             (chat_id, linha["usuario_id"], agora_iso(), agora_iso()))
    usuario = um("SELECT nome FROM usuarios WHERE id = ?", (linha["usuario_id"],))
    return f"✅ Conectado como {usuario['nome']}.\n\n{AJUDA}"


def obras_do_usuario(usuario_id: int, papel: str) -> list[dict]:
    if papel == "gestor":
        linhas = todos("""SELECT id, nome, cliente, endereco FROM obras
                           WHERE arquivada = 0 ORDER BY nome""")
    else:
        linhas = todos("""SELECT o.id, o.nome, o.cliente, o.endereco FROM obras o
                            JOIN usuario_obras uo ON uo.obra_id = o.id
                           WHERE uo.usuario_id = ? AND o.arquivada = 0 ORDER BY o.nome""",
                       (usuario_id,))
    return [dict(l) for l in linhas]


# ---------------------------------------------------------------- entrada

def _em_segundo_plano(app, funcao, *args) -> None:
    def rodar():
        with app.app_context():
            try:
                funcao(app, *args)
            except Exception:  # noqa: BLE001
                log.exception("Telegram: falha ao processar")
    if EM_SEGUNDO_PLANO:
        threading.Thread(target=rodar, daemon=True).start()
    else:
        rodar()


def _repetido(update_id) -> bool:
    """O Telegram reenvia a entrega se a resposta demorar: processa uma vez só."""
    if update_id is None:
        return False
    agora = time.time()
    with _trava_vistos:
        for chave in [k for k, t in _vistos.items() if agora - t > 3600]:
            _vistos.pop(chave, None)
        if update_id in _vistos:
            return True
        _vistos[update_id] = agora
    return False


def _arquivo_da_mensagem(msg: dict):
    if msg.get("photo"):
        maior = max(msg["photo"], key=lambda p: p.get("file_size") or p.get("width", 0))
        return maior["file_id"], "foto", "image/jpeg", "foto.jpg"
    if msg.get("voice"):
        return msg["voice"]["file_id"], "audio", msg["voice"].get("mime_type") or "audio/ogg", "audio.ogg"
    if msg.get("audio"):
        a = msg["audio"]
        return a["file_id"], "audio", a.get("mime_type") or "audio/mpeg", a.get("file_name") or "audio.mp3"
    if msg.get("document"):
        d = msg["document"]
        mime = (d.get("mime_type") or "").lower()
        if mime in MIME_DOCUMENTO or mime in ia_provedores.MIME_AUDIO:
            tipo = "audio" if mime.startswith("audio/") else "documento"
            return d["file_id"], tipo, mime, d.get("file_name") or "arquivo"
        return None
    return None


def receber(app, update: dict) -> None:
    """Ponto de entrada do webhook. Precisa ser rápido: o pesado vai para outra thread."""
    if _repetido(update.get("update_id")):
        return
    if update.get("callback_query"):
        _em_segundo_plano(app, tratar_botao, update["callback_query"])
        return

    msg = update.get("message") or {}
    if (msg.get("chat") or {}).get("type") != "private":
        return
    chat_id = str(msg["chat"]["id"])
    texto_msg = (msg.get("text") or msg.get("caption") or "").strip()

    if texto_msg.startswith("/"):
        _em_segundo_plano(app, tratar_comando, chat_id, texto_msg)
        return

    v = vinculo(chat_id)
    if not v:
        telegram_api.enviar(chat_id, "Este bot é privado. No app, abra Ajustes → Bot do Telegram, "
                                     "toque em Conectar e envie aqui o código: /start CÓDIGO")
        return

    arquivo = _arquivo_da_mensagem(msg)
    if not arquivo and not texto_msg:
        telegram_api.enviar(chat_id, "Esse tipo de arquivo eu não leio. Mande foto, PDF ou áudio.")
        return
    if arquivo:
        file_id, tipo, mime, nome = arquivo
        executar("""INSERT INTO telegram_arquivos (chat_id, file_id, tipo, mime, nome, lote, criado_em)
                    VALUES (?, ?, ?, ?, ?, '', ?)""", (chat_id, file_id, tipo, mime, nome[:120], agora_iso()))

    if texto_msg or (arquivo and arquivo[1] == "audio"):
        _em_segundo_plano(app, processar, chat_id, texto_msg)
        return

    # Arquivo sem instrução: avisa uma vez por envio, não uma vez por foto.
    soltos = um("SELECT COUNT(*) AS n FROM telegram_arquivos WHERE chat_id = ? AND lote = ''",
                (chat_id,))["n"]
    if soltos == 1:
        telegram_api.enviar(chat_id, "📎 Recebido. Pode mandar mais arquivos; depois envie um áudio ou "
                                     "texto dizendo o que fazer (obra, dia, materiais...).")


# ---------------------------------------------------------------- comandos

def tratar_comando(app, chat_id: str, comando: str) -> None:
    nome, _, argumento = comando.partition(" ")
    nome = nome.split("@")[0].lower()
    argumento = argumento.strip()

    if nome == "/start":
        if argumento:
            telegram_api.enviar(chat_id, _vincular(chat_id, argumento))
        elif vinculo(chat_id):
            telegram_api.enviar(chat_id, AJUDA)
        else:
            telegram_api.enviar(chat_id, "Olá! Para conectar, abra o app em Ajustes → Bot do Telegram, "
                                         "toque em Conectar e envie aqui: /start CÓDIGO")
        return

    v = vinculo(chat_id)
    if not v:
        telegram_api.enviar(chat_id, "Conecte primeiro: /start CÓDIGO (gere o código em Ajustes no app).")
        return

    if nome == "/ajuda":
        telegram_api.enviar(chat_id, AJUDA)
    elif nome == "/obra":
        _comando_obra(app, chat_id, v, argumento)
    elif nome == "/desconectar":
        executar("DELETE FROM telegram_chats WHERE chat_id = ?", (chat_id,))
        telegram_api.enviar(chat_id, "Conversa desconectada do app.")
    else:
        telegram_api.enviar(chat_id, "Comando desconhecido. Use /ajuda.")


def _comando_obra(app, chat_id: str, v: dict, argumento: str) -> None:
    obras = obras_do_usuario(v["usuario_id"], v["papel"])
    if not obras:
        telegram_api.enviar(chat_id, "Você ainda não tem obras no app.")
        return
    escolhida = next((o for o in obras if str(o["id"]) == argumento), None)
    if not escolhida:
        atual = next((o["nome"] for o in obras if o["id"] == v["obra_id"]), "nenhuma")
        botoes = [[{"text": o["nome"][:60], "callback_data": f"obra:{o['id']}"}] for o in obras[:20]]
        telegram_api.enviar(chat_id, f"Obra padrão atual: {atual}.\nEscolha outra:", botoes)
        return
    _definir_obra(app, chat_id, v, escolhida)


def _definir_obra(app, chat_id: str, v: dict, obra: dict) -> None:
    aguardando = next((p for p in v["pendentes"] if p.get("motivo") == "obra"), None)
    pendentes = [p for p in v["pendentes"] if p.get("motivo") != "obra"]
    _salvar(chat_id, obra_id=obra["id"], pendentes=pendentes)
    telegram_api.enviar(chat_id, f"🏗 Obra padrão: {obra['nome']}.")
    if aguardando:
        processar(app, chat_id, aguardando.get("texto", ""), transcricao=aguardando.get("transcricao", ""),
                  esperar=False)


# ---------------------------------------------------------------- processamento

def _pegar_lote(chat_id: str) -> list[dict]:
    lote = uuid.uuid4().hex
    limite = (datetime.now() - VALIDADE_ARQUIVO).isoformat(timespec="seconds")
    executar("DELETE FROM telegram_arquivos WHERE chat_id = ? AND lote = '' AND criado_em < ?",
             (chat_id, limite))
    executar("UPDATE telegram_arquivos SET lote = ? WHERE chat_id = ? AND lote = ''", (lote, chat_id))
    return [dict(l) for l in todos(
        "SELECT id, file_id, tipo, mime, nome FROM telegram_arquivos WHERE lote = ? ORDER BY id", (lote,))]


def _devolver_lote(linhas: list[dict]) -> None:
    """Falhou? Os arquivos voltam para a fila: ninguém precisa reenviar a nota."""
    for linha in linhas:
        executar("UPDATE telegram_arquivos SET lote = '' WHERE id = ?", (linha["id"],))


def _encerrar_lote(linhas: list[dict]) -> None:
    for linha in linhas:
        executar("DELETE FROM telegram_arquivos WHERE id = ?", (linha["id"],))


def _valor_digitado(texto_msg: str):
    if not re.fullmatch(r"\s*(r\$)?\s*[\d.,]+\s*(reais)?\s*", texto_msg or "", re.I):
        return None
    try:
        valor = numero(re.sub(r"(?i)r\$|reais", "", texto_msg), 0)
    except ApiErro:
        return None
    return valor if valor > 0 else None


def _ocupar(chat_id: str) -> bool:
    """Uma mensagem por vez por conversa — vale entre os workers do gunicorn.

    Áudio seguido de texto viravam dois processamentos em paralelo: o primeiro
    levava os arquivos e o segundo rodava sem eles. Agora o segundo espera o
    primeiro terminar e, se ele falhar, encontra os arquivos de volta na fila.
    """
    agora = datetime.now()
    cur = executar("""UPDATE telegram_chats SET ocupado_ate = ?
                       WHERE chat_id = ? AND (ocupado_ate IS NULL OR ocupado_ate < ?)""",
                   ((agora + OCUPADO_POR).isoformat(timespec="seconds"), chat_id,
                    agora.isoformat(timespec="seconds")))
    return bool(cur.rowcount)


def _liberar(chat_id: str) -> None:
    executar("UPDATE telegram_chats SET ocupado_ate = NULL WHERE chat_id = ?", (chat_id,))


def processar(app, chat_id: str, texto_msg: str, transcricao: str = "", esperar: bool = True) -> None:
    if esperar and ATRASO_LOTE:
        time.sleep(ATRASO_LOTE)
    if not vinculo(chat_id):
        return

    inicio = time.monotonic()
    avisou_espera = False
    while not _ocupar(chat_id):
        if not avisou_espera:
            telegram_api.enviar(chat_id, "⏳ Recebido. Termino a mensagem anterior e já sigo com esta.")
            avisou_espera = True
        if time.monotonic() - inicio >= ESPERA_MAXIMA:
            telegram_api.enviar(chat_id, "⚠️ A mensagem anterior ainda está sendo processada. "
                                         "Mande esta de novo daqui a pouco.")
            return
        time.sleep(2)

    linhas = []
    try:
        v = vinculo(chat_id)
        # Resposta curta a uma despesa que ficou sem valor: completa e grava.
        valor = _valor_digitado(texto_msg)
        faltando = [p for p in v["pendentes"] if p.get("motivo") == "faltando"]
        if valor and faltando:
            pendente = faltando[-1]
            pendente["acao"]["valor"] = valor
            _executar_pendente(app, chat_id, v, pendente)
            return

        linhas = _pegar_lote(chat_id)
        if not avisou_espera:
            telegram_api.enviar(chat_id, "⏳ Recebido, analisando"
                                + (f" ({len(linhas)} arquivo(s))" if linhas else "") + "...")
        _processar_lote(app, chat_id, v, texto_msg, transcricao, linhas)
    except ia_provedores.IaIndisponivel as erro:
        _devolver_lote(linhas)
        telegram_api.enviar(chat_id, f"⚠️ {erro}")
    except ApiErro as erro:
        _devolver_lote(linhas)
        telegram_api.enviar(chat_id, f"⚠️ {erro.mensagem}")
    except Exception as erro:  # noqa: BLE001 — o usuário sempre fica sabendo
        _devolver_lote(linhas)
        log.exception("Telegram: falha no processamento do chat %s", chat_id)
        telegram_api.enviar(chat_id, "⚠️ Algo deu errado aqui "
                                     f"({type(erro).__name__}: {str(erro)[:120]}). Seus arquivos continuam "
                                     "guardados: tente mandar a instrução de novo.")
    finally:
        _liberar(chat_id)


def _processar_lote(app, chat_id: str, v: dict, texto_msg: str, transcricao: str,
                    linhas: list[dict]) -> None:
    from ..blueprints.assistente import _conferir_limite
    _conferir_limite(v["usuario_id"])

    obras = obras_do_usuario(v["usuario_id"], v["papel"])
    if not obras:
        telegram_api.enviar(chat_id, "Você ainda não tem obras no app.")
        return

    telegram_api.digitando(chat_id)
    arquivos, audios = [], []
    for linha in linhas:
        item = {"nome": linha["nome"], "mime": linha["mime"], "dados": telegram_api.baixar(linha["file_id"]),
                "file_id": linha["file_id"]}
        (audios if linha["tipo"] == "audio" else arquivos).append(item)

    obra_id = 0
    if audios or len(obras) > 1:
        ouvido = assistente.ouvir(audios, texto_msg, obras)
        transcricao = ouvido["transcricao"] or transcricao
        obra_id = ouvido["obra_id"]
    ids = {o["id"] for o in obras}
    if not obra_id:
        obra_id = v["obra_id"] if v["obra_id"] in ids else (obras[0]["id"] if len(obras) == 1 else 0)

    if not obra_id:
        _devolver_lote(linhas)
        pendentes = [p for p in v["pendentes"] if p.get("motivo") != "obra"]
        pendentes.append({"motivo": "obra", "texto": texto_msg, "transcricao": transcricao})
        _salvar(chat_id, pendentes=pendentes[-MAX_PENDENTES:])
        botoes = [[{"text": o["nome"][:60], "callback_data": f"obra:{o['id']}"}] for o in obras[:20]]
        cabeca = f"🎙 Entendi: {transcricao[:600]}\n\n" if transcricao else ""
        telegram_api.enviar(chat_id, cabeca + "Para qual obra é isso?", botoes)
        return

    obra = next(o for o in obras if o["id"] == obra_id)
    mensagem = "\n".join(t for t in (texto_msg, f"(áudio) {transcricao}" if transcricao else "") if t)
    pode_financeiro = v["papel"] == "gestor"
    resultado = assistente.interpretar(
        obra_id, mensagem, hoje_iso(), arquivos, v["historico"],
        pode_financeiro=pode_financeiro, pode_cadastrar=v["papel"] == "gestor")

    executor = Executor(app, v["usuario_id"], obra_id)
    linhas_resposta, pendentes, botoes = [], [p for p in v["pendentes"] if p.get("motivo") != "obra"], []
    for acao in sorted(resultado["acoes"], key=lambda a: ORDEM.index(a["tipo"])):
        if acao.get("requer_confirmacao") or acao.get("faltando"):
            pendente = _novo_pendente(acao, arquivos, "exclusao" if acao.get("requer_confirmacao") else "faltando")
            pendentes.append(pendente)
            if pendente["motivo"] == "exclusao":
                atual = acao.get("despesa_atual") or {}
                linhas_resposta.append(f"🗑 Excluir \"{atual.get('descricao')}\" ({moeda(atual.get('valor'))})? "
                                       "Confirme no botão.")
                botoes.append([{"text": "Confirmar exclusão", "callback_data": f"ok:{pendente['id']}"},
                               {"text": "Cancelar", "callback_data": f"no:{pendente['id']}"}])
            else:
                linhas_resposta.append(f"❓ {_descrever(acao)} — faltou {' e '.join(acao['faltando'])}. "
                                       "Responda só com o valor (ex.: 350,00).")
            continue
        try:
            executor.gravar(acao, arquivos)
            linhas_resposta.append("✅ " + _descrever(acao))
        except ErroAcao as erro:
            if erro.codigo == "nota_duplicada":
                pendente = _novo_pendente(acao, arquivos, "duplicada")
                pendentes.append(pendente)
                botoes.append([{"text": "Lançar mesmo assim", "callback_data": f"ok:{pendente['id']}"},
                               {"text": "Descartar", "callback_data": f"no:{pendente['id']}"}])
            linhas_resposta.append(f"⚠️ {TITULOS.get(acao['tipo'], acao['tipo'])}: {erro.mensagem}")

    _encerrar_lote(linhas)
    gravados = [l[2:] for l in linhas_resposta if l.startswith("✅")]
    historico = (v["historico"] + [
        {"papel": "usuario", "texto": mensagem or "(arquivos)"},
        {"papel": "assistente", "texto": resultado["resposta"]
         + (f" [gravado: {'; '.join(gravados)}]" if gravados else "")},
    ])[-MAX_HISTORICO:]
    _salvar(chat_id, obra_id=obra_id, historico=historico, pendentes=pendentes[-MAX_PENDENTES:])

    partes = [f"🏗 {obra['nome']}"]
    if transcricao:
        partes.append(f"🎙 Entendi: {transcricao[:700]}")
    partes.append(resultado["resposta"])
    if linhas_resposta:
        partes.append("\n".join(linhas_resposta))
    if resultado.get("avisos"):
        partes.append("\n".join(f"• {a}" for a in resultado["avisos"]))
    telegram_api.enviar(chat_id, "\n\n".join(partes), botoes or None)


def _novo_pendente(acao: dict, arquivos: list[dict], motivo: str) -> dict:
    """Guarda a ação para depois, com os arquivos referenciados pelo id do Telegram."""
    usados = sorted(set(acao.get("anexos") or []))
    copia = dict(acao)
    copia["anexos"] = list(range(len(usados)))
    return {"id": uuid.uuid4().hex[:10], "motivo": motivo, "acao": copia,
            "arquivos": [{k: arquivos[i][k] for k in ("file_id", "mime", "nome")}
                         for i in usados if i < len(arquivos)]}


def _executar_pendente(app, chat_id: str, v: dict, pendente: dict) -> None:
    acao = pendente["acao"]
    if pendente["motivo"] == "duplicada":
        acao["confirmar_duplicidade"] = True
    arquivos = [{**a, "dados": telegram_api.baixar(a["file_id"])} for a in pendente.get("arquivos", [])]
    obra_id = v["obra_id"]
    restantes = [p for p in v["pendentes"] if p.get("id") != pendente["id"]]
    try:
        Executor(app, v["usuario_id"], obra_id).gravar(acao, arquivos)
        telegram_api.enviar(chat_id, "✅ " + _descrever(acao))
    except ErroAcao as erro:
        telegram_api.enviar(chat_id, f"⚠️ {TITULOS.get(acao['tipo'], acao['tipo'])}: {erro.mensagem}")
    _salvar(chat_id, pendentes=restantes)


def tratar_botao(app, callback: dict) -> None:
    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
    dados = callback.get("data") or ""
    v = vinculo(chat_id)
    if not v:
        telegram_api.responder_botao(callback.get("id", ""), "Conversa não conectada.")
        return
    tipo, _, valor = dados.partition(":")

    if tipo == "obra":
        obra = next((o for o in obras_do_usuario(v["usuario_id"], v["papel"]) if str(o["id"]) == valor), None)
        telegram_api.responder_botao(callback.get("id", ""), obra["nome"] if obra else "Obra indisponível.")
        if obra:
            _definir_obra(app, chat_id, v, obra)
        return

    pendente = next((p for p in v["pendentes"] if p.get("id") == valor), None)
    if not pendente:
        telegram_api.responder_botao(callback.get("id", ""), "Isso já foi resolvido.")
        return
    telegram_api.responder_botao(callback.get("id", ""))
    if tipo == "no":
        _salvar(chat_id, pendentes=[p for p in v["pendentes"] if p.get("id") != valor])
        telegram_api.enviar(chat_id, "Descartado.")
    elif tipo == "ok":
        _executar_pendente(app, chat_id, v, pendente)


# ---------------------------------------------------------------- texto das respostas

def _descrever(a: dict) -> str:
    dia = data_br(a.get("data")) if a.get("data") else ""
    tipo = a["tipo"]
    if tipo == "diario":
        return f"Diário de {dia} atualizado"
    if tipo == "ponto":
        return f"Ponto de {dia}: " + ", ".join(f"{r['nome']} {r['horas']:g}h" for r in a["registros"])
    if tipo == "funcionario":
        return f"Cadastrado: {a['nome']}" + (f" ({a['funcao']})" if a.get("funcao") else "")
    if tipo == "foto":
        return f"{len(a['anexos'])} foto(s) no diário de {dia}" + (f" · {a['ambiente']}" if a.get("ambiente") else "")
    if tipo == "despesa":
        partes = [a.get("descricao") or "Despesa", moeda(a.get("valor"))]
        if a.get("fornecedor"):
            partes.append(a["fornecedor"])
        if a.get("numero_nota"):
            partes.append(f"nº {a['numero_nota']}")
        partes.append(a.get("status_pagamento") or "")
        if a.get("parcelas"):
            partes.append(f"{a['parcelas']}x")
        if a.get("anexos"):
            partes.append("📎")
        return "Despesa em " + dia + ": " + " · ".join(p for p in partes if p)
    atual = a.get("despesa_atual") or {}
    if tipo == "editar_despesa":
        mudou = ", ".join(f"{k.replace('_', ' ')} → {moeda(v) if k == 'valor' else v}"
                          for k, v in (a.get("alteracoes") or {}).items())
        return f"Corrigido \"{atual.get('descricao')}\": {mudou or 'anexo incluído'}"
    if tipo == "pagar_despesa":
        return f"Baixa de \"{atual.get('descricao')}\" ({moeda(atual.get('valor'))}) em {data_br(a.get('pago_em'))}"
    if tipo == "excluir_despesa":
        return f"Excluído \"{atual.get('descricao')}\""
    return texto(a.get("resumo"), 200)
