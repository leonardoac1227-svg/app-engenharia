# -*- coding: utf-8 -*-
"""Bot do Telegram — o assistente da obra no chat do celular.

Fluxo de uma mensagem:

1. **Arquivos** (foto, print, PDF de nota, comprovante de PIX, áudio) entram na
   fila da conversa. Foto sozinha recebe um aviso de "recebido".
2. **Instrução** (texto, legenda ou áudio) fecha o envio: depois de alguns
   segundos — tempo de chegarem as outras fotos — tudo vai junto para a IA
   (o áudio é transcrito e a obra e os documentos são lidos com ele).
   Se a instrução não vier em `ATRASO_FOTO` segundos, o arquivo é analisado
   sozinho. Sem saber a obra, o bot pergunta, e o áudio ou texto que chegar
   depois responde a mesma pergunta.
3. As ações voltam conferidas e o `Executor` grava pelos endpoints do app,
   como o usuário vinculado à conversa.
4. O bot responde o que gravou. Exclusão, baixa em lote e fechar semana pedem
   botão; nota repetida oferece "Lançar mesmo assim"; despesa sem valor espera
   o valor; relatório volta como arquivo.

Se a IA falhar, os arquivos ficam presos àquela tentativa, com o botão
"Tentar de novo" — nunca voltam para a fila, onde se misturariam com o
próximo envio (foi assim que uma nota antiga entrou num lançamento novo).

Só conversas vinculadas a um usuário do app são atendidas, e o bot pode
exatamente o que o perfil desse usuário pode na tela.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timedelta

from ..db import executar, liberar_leitura, todos, um
from ..utils import ApiErro, agora_iso, data_br, hoje_iso, moeda, numero, segunda_da_semana
from . import acoes as catalogo
from . import assistente, ia_provedores, telegram_api
from . import reembolso
from .executor_acoes import ErroAcao, Executor, ordenar

log = logging.getLogger(__name__)

EM_SEGUNDO_PLANO = True      # os testes desligam para rodar tudo na mesma thread
ATRASO_LOTE = 3.0            # segundos esperando o resto das fotos do mesmo envio
ATRASO_CURTO = 0.8           # mensagem sozinha: só o bastante para o encaminhado com comentário
ATRASO_FOTO = 30             # segundos esperando o áudio que explica a foto; None desliga
PRAZO_IA = 150              # segundos: no bot não há limite do gunicorn
VALIDADE_FILA = timedelta(hours=1)        # arquivo esperando instrução
VALIDADE_TENTATIVA = timedelta(hours=12)  # arquivo de tentativa que falhou (botão "tentar de novo")
VALIDADE_CODIGO = timedelta(minutes=20)
VALIDADE_CONVITE = timedelta(days=3)
VALIDADE_OBRA_NOVA = timedelta(hours=12)   # cadastro de obra parado no meio
OCUPADO_POR = timedelta(minutes=5)   # teto de um processamento (IA + gravação)
ESPERA_MAXIMA = 300                  # segundos esperando a mensagem anterior terminar
MAX_HISTORICO = 8
MAX_PENDENTES = 12
_vistos: dict[int, float] = {}
_trava_vistos = threading.Lock()

MIME_DOCUMENTO = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}

AJUDA = """Sou o assistente da obra e faço tudo que o app faz. Fale do jeito que falaria com um colega:

📄 Nota, cupom, orçamento, boleto ou comprovante de PIX (foto, print ou PDF) — leio número, total e fornecedor e lanço.
🎙 Áudio: "esse material é da obra O13, 2 baldes para a concretagem".
📷 Fotos + "relatório fotográfico de ontem" — anexo no dia certo.
🗣 Fim do dia: "Ademir fez 27 metros de reboco de manhã e à tarde arestamento; o Carlos faltou, atestado" — monto o diário e o ponto.
🕗 Ponto: "Ademir trabalhou o dia inteiro hoje", "Ademir e Ailton vieram", "a equipe inteira esteve na obra".
💰 "Essa nota está em aberto", "passa para reembolso", "paguei o boleto da areia", "frete de 3 viagens"...
✏️ Corrigir: "o valor da última nota é 150", "muda o fornecedor do #212", "esse lançamento é do O13".
👷 "Cadastra o Marcos, armador", "o João saiu da obra".
🏗 "Quero criar uma obra", "entrou uma obra nova": pergunto nome, início, término, cliente e endereço, e cadastro.
📊 "Me manda o relatório da semana", "quanto gastei essa semana?"

Pode mandar só a foto: eu analiso sozinho. Se quiser explicar, mande o áudio ou texto logo depois (ou na legenda).

/obra — obra padrão · /relatorio — PDF da semana · /teste — testar a IA · /comparar — comparar as IAs · /ajuda"""


# ---------------------------------------------------------------- vínculo

def gerar_codigo(usuario_id: int, convite: bool = False) -> str:
    """Código que liga a conversa à conta. O de convite vale dias e, por isso, é bem mais longo."""
    executar("DELETE FROM telegram_codigos WHERE usuario_id = ? OR expira_em < ?",
             (usuario_id, agora_iso()))
    codigo = secrets.token_hex(10 if convite else 4).upper()
    executar("INSERT INTO telegram_codigos (codigo, usuario_id, expira_em) VALUES (?, ?, ?)",
             (codigo, usuario_id, (datetime.now() + (VALIDADE_CONVITE if convite else VALIDADE_CODIGO))
              .isoformat(timespec="seconds")))
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
        return "Código inválido ou vencido. Peça outro a quem te convidou (ou gere no app em Ajustes → Bot do Telegram)."
    dono = um("SELECT ativo FROM usuarios WHERE id = ?", (linha["usuario_id"],))
    if not dono or not dono["ativo"]:
        return "Esta conta está desativada. Fale com o gestor."
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
        linhas = todos("SELECT id, nome, cliente, endereco FROM obras WHERE arquivada = 0 ORDER BY nome")
    else:
        linhas = todos("""SELECT o.id, o.nome, o.cliente, o.endereco FROM obras o
                            JOIN usuario_obras uo ON uo.obra_id = o.id
                           WHERE uo.usuario_id = ? AND o.arquivada = 0 ORDER BY o.nome""", (usuario_id,))
    return [dict(l) for l in linhas]


def _perfil(v: dict) -> tuple[bool, bool]:
    return v["papel"] == "gestor", v["papel"] == "gestor"


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

    if not vinculo(chat_id):
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
        # Renova a fila: o temporizador da foto não pode correr na frente da instrução.
        executar("UPDATE telegram_arquivos SET criado_em = ? WHERE chat_id = ? AND lote = ''",
                 (agora_iso(), chat_id))
        na_fila = um("SELECT COUNT(*) AS n FROM telegram_arquivos WHERE chat_id = ? AND lote = ''",
                     (chat_id,))["n"]
        curto = not msg.get("media_group_id") and na_fila <= 1
        _em_segundo_plano(app, processar, chat_id, texto_msg, "", True, curto)
        return

    # Arquivo sem instrução: avisa uma vez por envio, não uma vez por foto.
    soltos = um("SELECT COUNT(*) AS n FROM telegram_arquivos WHERE chat_id = ? AND lote = ''",
                (chat_id,))["n"]
    if soltos == 1:
        telegram_api.enviar(chat_id, "📎 Recebido. Já vou analisar. Se quiser explicar, mande agora "
                                     "um áudio ou texto (obra, dia, para que serve...).")
    if ATRASO_FOTO is not None:
        _em_segundo_plano(app, analisar_sem_instrucao, chat_id)


def analisar_sem_instrucao(app, chat_id: str) -> None:
    """A instrução não veio: analisa o que está na fila, uma vez só por envio."""
    time.sleep(ATRASO_FOTO or 0)
    ultimo = um("SELECT MAX(criado_em) AS m FROM telegram_arquivos WHERE chat_id = ? AND lote = ''",
                (chat_id,))["m"]
    limite = (datetime.now() - timedelta(seconds=(ATRASO_FOTO or 0) - 1)).isoformat(timespec="seconds")
    if not ultimo or ultimo > limite:
        return  # já foi levado por uma instrução, ou chegou arquivo mais novo com o próprio prazo
    lote = _pegar_fila(chat_id)
    if _arquivos_do_lote(lote):
        processar(app, chat_id, "", lote=lote, esperar=False)


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
    elif nome == "/teste":
        _comando_teste(chat_id)
    elif nome == "/comparar":
        _comando_comparar(chat_id, v)
    elif nome == "/relatorio":
        _comando_relatorio(app, chat_id, v, argumento)
    elif nome == "/desconectar":
        executar("DELETE FROM telegram_chats WHERE chat_id = ?", (chat_id,))
        telegram_api.enviar(chat_id, "Conversa desconectada do app.")
    else:
        telegram_api.enviar(chat_id, "Comando desconhecido. Use /ajuda.")


def _comando_teste(chat_id: str) -> None:
    telegram_api.enviar(chat_id, "⏳ Testando a IA...")
    resultados = ia_provedores.testar()
    if not resultados:
        telegram_api.enviar(chat_id, "⚠️ Nenhuma chave de IA configurada no servidor (GEMINI_API_KEY).")
        return
    linhas = [("✅" if r["ok"] else "❌") + f" {r['provedor']} · {r['modelo']}: "
              + (f"respondeu em {r['ms'] / 1000:.1f}s" if r["ok"] else r["erro"]) for r in resultados]
    telegram_api.enviar(chat_id, "Teste da IA:\n" + "\n".join(linhas))


def _comando_comparar(chat_id: str, v: dict) -> None:
    from . import comparar_ia
    if v["papel"] != "gestor":
        telegram_api.enviar(chat_id, "Somente o gestor compara as IAs.")
        return
    if len(ia_provedores.provedores_configurados()) < 2:
        telegram_api.enviar(chat_id, "Só há uma IA configurada. Salve a ANTHROPIC_API_KEY no servidor para comparar.")
        return
    telegram_api.enviar(chat_id, "⏳ Comparando as IAs com notas já lançadas. Leva alguns minutos e não grava nada.")
    obras = obras_do_usuario(v["usuario_id"], v["papel"])
    resultado = comparar_ia.resumo(comparar_ia.comparar(obras))
    log.info("Comparação das IAs: %s", resultado.replace("\n", " | "))
    telegram_api.enviar(chat_id, resultado)


def _comando_obra(app, chat_id: str, v: dict, argumento: str) -> None:
    obras = obras_do_usuario(v["usuario_id"], v["papel"])
    if not obras:
        telegram_api.enviar(chat_id, "Você ainda não tem obras no app.")
        return
    escolhida = next((o for o in obras if str(o["id"]) == argumento
                      or catalogo.sem_acento(o["nome"]) == catalogo.sem_acento(argumento)), None)
    if not escolhida:
        atual = next((o["nome"] for o in obras if o["id"] == v["obra_id"]), "nenhuma")
        botoes = [[{"text": o["nome"][:60], "callback_data": f"obra:{o['id']}"}] for o in obras[:20]]
        telegram_api.enviar(chat_id, f"Obra padrão atual: {atual}.\nEscolha outra:", botoes)
        return
    _salvar(chat_id, obra_id=escolhida["id"])
    telegram_api.enviar(chat_id, f"🏗 Obra padrão: {escolhida['nome']}.")


def _comando_relatorio(app, chat_id: str, v: dict, argumento: str) -> None:
    obras = obras_do_usuario(v["usuario_id"], v["papel"])
    obra_id = v["obra_id"] if any(o["id"] == v["obra_id"] for o in obras) else \
        (obras[0]["id"] if len(obras) == 1 else 0)
    if not obra_id:
        telegram_api.enviar(chat_id, "Escolha a obra primeiro com /obra.")
        return
    acao = {"tipo": "relatorio", "dados": {"data": hoje_iso(), "formato": argumento or "pdf"}}
    pf, pg = _perfil(v)
    conferidas, avisos = catalogo.normalizar([acao], catalogo.Contexto(obra_id, hoje_iso(), pf, pg))
    if not conferidas:
        telegram_api.enviar(chat_id, "⚠️ " + (avisos[0] if avisos else "Não foi possível gerar o relatório."))
        return
    try:
        _executar(app, chat_id, v, conferidas[0], [], obra_id)
    except ErroAcao as erro:
        telegram_api.enviar(chat_id, f"⚠️ {erro.mensagem}")


# ---------------------------------------------------------------- fila e trava

def _limpar_fila(chat_id: str) -> None:
    agora = datetime.now()
    executar("DELETE FROM telegram_arquivos WHERE chat_id = ? AND lote = '' AND criado_em < ?",
             (chat_id, (agora - VALIDADE_FILA).isoformat(timespec="seconds")))
    executar("DELETE FROM telegram_arquivos WHERE chat_id = ? AND lote <> '' AND criado_em < ?",
             (chat_id, (agora - VALIDADE_TENTATIVA).isoformat(timespec="seconds")))


def _pegar_fila(chat_id: str) -> str:
    _limpar_fila(chat_id)
    lote = uuid.uuid4().hex
    executar("UPDATE telegram_arquivos SET lote = ? WHERE chat_id = ? AND lote = ''", (lote, chat_id))
    return lote


def _arquivos_do_lote(lote: str) -> list[dict]:
    return [dict(l) for l in todos(
        "SELECT id, file_id, tipo, mime, nome FROM telegram_arquivos WHERE lote = ? ORDER BY id", (lote,))]


def _encerrar_lote(lote: str) -> None:
    executar("DELETE FROM telegram_arquivos WHERE lote = ?", (lote,))


def _ocupar(chat_id: str) -> bool:
    """Uma mensagem por vez por conversa — vale entre os workers do gunicorn."""
    agora = datetime.now()
    cur = executar("""UPDATE telegram_chats SET ocupado_ate = ?
                       WHERE chat_id = ? AND (ocupado_ate IS NULL OR ocupado_ate < ?)""",
                   ((agora + OCUPADO_POR).isoformat(timespec="seconds"), chat_id,
                    agora.isoformat(timespec="seconds")))
    return bool(cur.rowcount)


def _liberar(chat_id: str) -> None:
    executar("UPDATE telegram_chats SET ocupado_ate = NULL WHERE chat_id = ?", (chat_id,))


def _valor_digitado(texto_msg: str):
    if not re.fullmatch(r"\s*(r\$)?\s*[\d.,]+\s*(reais)?\s*", texto_msg or "", re.I):
        return None
    try:
        valor = numero(re.sub(r"(?i)r\$|reais", "", texto_msg), 0)
    except ApiErro:
        return None
    return valor if valor > 0 else None


def _novo_pendente(v: dict, motivo: str, **dados) -> dict:
    pendente = {"id": uuid.uuid4().hex[:10], "motivo": motivo, **dados}
    v["pendentes"] = (v["pendentes"] + [pendente])[-MAX_PENDENTES:]
    return pendente


def _arquivos_para_pendente(acao: dict, arquivos: list[dict]) -> tuple[dict, list[dict]]:
    """Guarda a ação para depois, com os arquivos referenciados pelo id do Telegram."""
    usados = sorted(set(acao["dados"].get("anexos") or []))
    copia = json.loads(json.dumps(acao, default=str))
    if usados:
        copia["dados"]["anexos"] = list(range(len(usados)))
    return copia, [{k: arquivos[i][k] for k in ("file_id", "mime", "nome")} for i in usados if i < len(arquivos)]


# ---------------------------------------------------------------- processamento

def processar(app, chat_id: str, texto_msg: str, lote: str = "", esperar: bool = True,
              curto: bool = False) -> None:
    log.info("Telegram %s: mensagem recebida, processando", chat_id)
    if esperar and ATRASO_LOTE:
        time.sleep(min(ATRASO_LOTE, ATRASO_CURTO) if curto else ATRASO_LOTE)
    if not vinculo(chat_id):
        return

    inicio = time.monotonic()
    avisou = False
    while not _ocupar(chat_id):
        if not avisou:
            telegram_api.enviar(chat_id, "⏳ Recebido. Termino a mensagem anterior e já sigo com esta.")
            avisou = True
        if time.monotonic() - inicio >= ESPERA_MAXIMA:
            telegram_api.enviar(chat_id, "⚠️ A mensagem anterior ainda está sendo processada. "
                                         "Mande esta de novo daqui a pouco.")
            return
        time.sleep(2)

    try:
        v = vinculo(chat_id)
        valor = _valor_digitado(texto_msg)
        faltando = [p for p in v["pendentes"] if p.get("motivo") == "faltando"]
        if valor and faltando and not lote:
            pendente = faltando[-1]
            pendente["acao"]["dados"]["valor"] = valor
            pendente["acao"]["dados"].pop("faltando", None)
            if not pendente["acao"]["dados"].get("descricao"):
                pendente["acao"]["dados"]["descricao"] = pendente["acao"].get("resumo") or "Despesa"
            _resolver_pendente(app, chat_id, v, pendente)
            return
        lote = lote or _pegar_fila(chat_id)
        _processar_lote(app, chat_id, v, texto_msg, lote, avisou)
    except Exception as erro:  # noqa: BLE001 — o usuário sempre fica sabendo
        esperado = isinstance(erro, (ia_provedores.IaIndisponivel, telegram_api.ErroTelegram, ApiErro))
        if esperado:
            log.warning("Telegram %s: %s", chat_id, erro)
        else:
            log.exception("Telegram: falha no processamento do chat %s", chat_id)
        motivo = erro.mensagem if isinstance(erro, ApiErro) else \
            (str(erro) if esperado else f"{type(erro).__name__}: {str(erro)[:150]}")
        v = vinculo(chat_id) or {"pendentes": []}
        tentativa = _novo_pendente(v, "repetir", lote=lote, texto=texto_msg)
        _salvar(chat_id, pendentes=v["pendentes"])
        n = len(_arquivos_do_lote(lote)) if lote else 0
        telegram_api.enviar(chat_id, f"⚠️ {motivo}" + (f"\n\n{n} arquivo(s) guardado(s) para esta tentativa." if n else ""),
                            [[{"text": "🔁 Tentar de novo", "callback_data": f"re:{tentativa['id']}"}]])
    finally:
        _liberar(chat_id)


def _processar_lote(app, chat_id: str, v: dict, texto_msg: str, lote: str, ja_avisou: bool) -> None:
    from ..blueprints.assistente import _conferir_limite
    _conferir_limite(v["usuario_id"])

    obras = obras_do_usuario(v["usuario_id"], v["papel"])
    if not obras and v["papel"] != "gestor":
        telegram_api.enviar(chat_id, "Você ainda não tem obras no app.")
        return

    linhas = _arquivos_do_lote(lote)
    liberar_leitura()   # downloads e IA podem levar minutos: não segura tabelas enquanto isso
    if not ja_avisou:
        telegram_api.enviar(chat_id, "⏳ Analisando" + (f" {len(linhas)} arquivo(s)" if linhas else "") + "...")
    telegram_api.digitando(chat_id)
    arquivos = [{"nome": l["nome"], "mime": l["mime"], "dados": telegram_api.baixar(l["file_id"]),
                 "file_id": l["file_id"]} for l in linhas]

    # Foto analisada sem instrução: não herda a obra da conversa anterior, porque
    # lançar a nota na obra errada é pior que perguntar.
    sem_instrucao = not texto_msg and not any(a["mime"].startswith("audio/") for a in arquivos)
    obra_conversa = 0 if sem_instrucao else (v["obra_id"] or 0)

    # Lançamento que ficou incompleto ou esperando a obra: a resposta do usuário
    # vale para ele, então os arquivos voltam para a análise (sem eles a IA
    # pedia de novo o valor que estava na foto).
    mensagem = texto_msg
    incompletos = [p for p in v["pendentes"] if p.get("motivo") in ("faltando", "obra")]
    if incompletos and not sem_instrucao:
        ja = {a["file_id"] for a in arquivos}
        for pendente in incompletos:
            for ref in pendente.get("arquivos", []):
                if ref["file_id"] not in ja:
                    arquivos.append({**ref, "dados": telegram_api.baixar(ref["file_id"])})
                    ja.add(ref["file_id"])
        faltando = [catalogo.descrever(p["acao"]) for p in incompletos if p["motivo"] == "faltando"]
        if faltando:
            mensagem += ("\n(Complete o lançamento que ficou incompleto — " + "; ".join(faltando)
                         + " — usando esta mensagem e os arquivos dele, reenviados aqui. Não duplique.)")
        if len(faltando) < len(incompletos):
            mensagem += ("\n(Estes arquivos reenviados esperavam a obra: lance-os conforme esta mensagem. "
                         "Não duplique.)")
        v["pendentes"] = [p for p in v["pendentes"] if p not in incompletos]
    else:
        incompletos = []

    rascunho = _rascunho_obra(v)
    if rascunho and not sem_instrucao:
        sabe = catalogo._d_criar_obra(rascunho["dados"])
        falta = ", ".join(r for c, r in catalogo.CAMPOS_OBRA_NOVA if not rascunho["dados"].get(c))
        mensagem += f"\n(Obra nova em cadastro — {sabe}. Perguntado: {falta}.)".replace("(sem nome)", "sem nome")

    pf, pg = _perfil(v)
    inicio = time.monotonic()
    resultado = assistente.interpretar(
        obras, mensagem, hoje_iso(), arquivos, v["historico"], pode_financeiro=pf, pode_gestor=pg,
        obra_padrao=obra_conversa, prazo=PRAZO_IA, reembolso=reembolso.padrao_de(v["usuario_id"]),
        separar_obra_nova=True)
    log.info("Telegram %s: IA em %.1fs (%s %s)", chat_id, time.monotonic() - inicio,
             resultado["fonte"], resultado["modelo"])

    if resultado["precisa_obra"]:
        acoes_guardadas = [{"bruta": a} for a in resultado["acoes_sem_obra"]]
        pendente = _novo_pendente(v, "obra", lote=lote, acoes=acoes_guardadas,
                                  arquivos=[{k: a[k] for k in ("file_id", "mime", "nome")} for a in arquivos],
                                  transcricao=resultado["transcricao"], resposta=resultado["resposta"])
        _salvar(chat_id, pendentes=v["pendentes"])
        botoes = [[{"text": o["nome"][:60], "callback_data": f"ob:{pendente['id']}:{o['id']}"}] for o in obras[:20]]
        cabeca = f"🎙 Entendi: {resultado['transcricao'][:700]}\n\n" if resultado["transcricao"] else ""
        telegram_api.enviar(chat_id, cabeca + "Para qual obra é isso?", botoes)
        return

    obra_id = resultado["obra_id"] or obra_conversa
    _finalizar(app, chat_id, v, resultado, arquivos, obra_id, texto_msg)
    _encerrar_lote(lote)
    for pendente in incompletos:
        if pendente.get("lote"):
            _encerrar_lote(pendente["lote"])


def _finalizar(app, chat_id: str, v: dict, resultado: dict, arquivos: list[dict], obra_id: int,
               texto_msg: str) -> None:
    """Grava as ações, guarda o que espera decisão e responde."""
    linhas, botoes = [], []
    depois = None   # (obra criada, o que a conversa pediu para fazer nela)
    for acao in ordenar(resultado["acoes"]):
        if acao["tipo"] == "criar_obra":
            linha, botoes_obra, depois = _cadastrar_obra(app, chat_id, v, acao["dados"],
                                                         resultado.get("acoes_da_obra_nova") or [], arquivos)
            linhas.append(linha)
            botoes += botoes_obra
            if not linha.startswith("✅"):
                resultado["resposta"] = ""   # a pergunta já está na linha
            continue
        alvo = acao.get("obra_id") or obra_id
        if acao["confirmar"] or acao["dados"].get("faltando"):
            copia, refs = _arquivos_para_pendente(acao, arquivos)
            motivo = "faltando" if acao["dados"].get("faltando") else "confirmar"
            pendente = _novo_pendente(v, motivo, acao=copia, arquivos=refs, obra_id=alvo)
            if motivo == "confirmar":
                linhas.append(f"❔ {acao['descricao']} — confirme no botão.")
                botoes.append([{"text": f"Confirmar: {acao['titulo']}"[:60], "callback_data": f"ok:{pendente['id']}"},
                               {"text": "Cancelar", "callback_data": f"no:{pendente['id']}"}])
            else:
                linhas.append(f"❓ {acao['descricao']} — faltou {' e '.join(acao['dados']['faltando'])}. "
                              "Responda só com o valor (ex.: 350,00).")
            continue
        try:
            criadas = _executar(app, chat_id, v, acao, arquivos, alvo)
            linhas.append("✅ " + acao["descricao"] + "".join(f" · #{i}" for i in criadas)
                          + _aviso_de_semana(acao))
        except ErroAcao as erro:
            if erro.codigo == "nota_duplicada":
                copia, refs = _arquivos_para_pendente(acao, arquivos)
                pendente = _novo_pendente(v, "duplicada", acao=copia, arquivos=refs, obra_id=alvo)
                botoes.append([{"text": "Lançar mesmo assim", "callback_data": f"ok:{pendente['id']}"},
                               {"text": "Descartar", "callback_data": f"no:{pendente['id']}"}])
            linhas.append(f"⚠️ {acao['titulo']}: {erro.mensagem}")

    gravados = [l[2:] for l in linhas if l.startswith("✅")]
    historico = (v["historico"] + [
        {"papel": "usuario", "texto": " ".join(t for t in (texto_msg, resultado["transcricao"]) if t)
         or "(arquivos)"},
        {"papel": "assistente", "texto": resultado["resposta"]
         + (f" [gravado: {'; '.join(gravados)}]" if gravados else "")},
    ])[-MAX_HISTORICO:]
    _salvar(chat_id, obra_id=obra_id or v["obra_id"], historico=historico, pendentes=v["pendentes"])

    obra = um("SELECT nome FROM obras WHERE id = ?", (obra_id,)) if obra_id else None
    partes = [f"🏗 {obra['nome']}"] if obra else []
    if resultado["transcricao"]:
        partes.append(f"🎙 Entendi: {resultado['transcricao'][:700]}")
    partes.append(resultado["resposta"])
    if linhas:
        partes.append("\n".join(linhas))
    if resultado.get("avisos"):
        partes.append("\n".join(f"• {a}" for a in resultado["avisos"]))
    telegram_api.enviar(chat_id, "\n\n".join(p for p in partes if p), botoes or None)
    _fazer_na_obra_nova(app, chat_id, v, depois)


# ---------------------------------------------------------------- obra nova

def _rascunho_obra(v: dict) -> dict | None:
    """Cadastro de obra que está sendo perguntado. Esquecido por um dia, não atrapalha outra conversa."""
    limite = (datetime.now() - VALIDADE_OBRA_NOVA).isoformat(timespec="seconds")
    return next((p for p in reversed(v["pendentes"])
                 if p.get("motivo") == "obra_nova" and p.get("criado_em", "") >= limite), None)


def _cadastrar_obra(app, chat_id: str, v: dict, dados: dict, acoes: list[dict], arquivos: list[dict]):
    """Junta o que já foi dito e cria a obra, ou pergunta o que falta. Devolve (linha, botões, depois)."""
    rascunho = _rascunho_obra(v) or {"dados": {}, "grupos": [], "perguntas": 0}
    v["pendentes"] = [p for p in v["pendentes"] if p.get("motivo") != "obra_nova"]
    if dados.get("cancelar"):
        return "❌ Cadastro da obra cancelado.", [], None
    juntos = {**rascunho["dados"], **{c: dados[c] for c, _ in catalogo.CAMPOS_OBRA_NOVA if dados.get(c)}}
    grupos = list(rascunho["grupos"])
    if acoes:
        grupos.append({"acoes": acoes, "arquivos": [{k: a[k] for k in ("file_id", "mime", "nome")} for a in arquivos]})

    avisos = ""
    inicio, fim = juntos.get("data_inicio"), juntos.get("previsao_fim")
    if inicio and fim and fim < inicio:
        juntos.pop("previsao_fim")
        avisos = "\n⚠️ O término ficou antes do início. Confirme a data de término."
    falta = [c for c in catalogo.OBRIGATORIOS_OBRA_NOVA if not juntos.get(c)]
    pode = dados.get("pode_criar") or rascunho["perguntas"] >= 2
    if juntos.get("nome") and (not falta or pode):
        return _criar_obra(app, v, juntos, grupos)

    pendente = _novo_pendente(v, "obra_nova", dados=juntos, grupos=grupos, perguntas=rascunho["perguntas"] + 1,
                              criado_em=agora_iso())
    ja = [f"• {rotulo}: {data_br(juntos[c]) if c in ('data_inicio', 'previsao_fim') else juntos[c]}"
          for c, rotulo in catalogo.CAMPOS_OBRA_NOVA if juntos.get(c)]
    pedir = [f"• {rotulo}" + ("" if c in catalogo.OBRIGATORIOS_OBRA_NOVA else " (se já tiver)")
             for c, rotulo in catalogo.CAMPOS_OBRA_NOVA if not juntos.get(c)]
    linha = "🏗 Vamos cadastrar a obra nova." + ("\nJá anotei:\n" + "\n".join(ja) if ja else "") + avisos \
        + "\nMe diga, numa mensagem só ou aos poucos:\n" + "\n".join(pedir)
    botoes = [{"text": "Cancelar", "callback_data": f"no:{pendente['id']}"}]
    if juntos.get("nome"):
        botoes.insert(0, {"text": "Criar assim mesmo", "callback_data": f"cr:{pendente['id']}"})
    return linha, [botoes], None


def _criar_obra(app, v: dict, dados: dict, grupos: list[dict]):
    sem_inicio = not dados.get("data_inicio")
    dados = {**dados, "data_inicio": dados.get("data_inicio") or hoje_iso()}
    if dados.get("previsao_fim") and dados["previsao_fim"] < dados["data_inicio"]:
        dados.pop("previsao_fim")
    try:
        nova = Executor(app, v["usuario_id"], 0).gravar({"tipo": "criar_obra", "dados": dados}, [])["obra_nova"]
    except ErroAcao as erro:
        return f"⚠️ Obra nova: {erro.mensagem}", [], None
    linha = "✅ " + catalogo._d_criar_obra(dados).replace("Obra nova:", "Obra criada:")
    if sem_inicio:
        linha += "\nSem data dita, o início ficou hoje."
    falta = [rotulo for c, rotulo in catalogo.CAMPOS_OBRA_NOVA if not dados.get(c)]
    if falta:
        linha += f"\nFicou sem {', '.join(falta)}. Quando tiver, é só me dizer."
    return linha, [], (nova, grupos)


def _fazer_na_obra_nova(app, chat_id: str, v: dict, depois) -> None:
    """"Cria a obra O20 e cadastra o Pedro nela": o resto do pedido, com a obra já criada."""
    for grupo in (depois[1] if depois else []):
        atual = vinculo(chat_id) or v
        _escolher_obra(app, chat_id, atual, {"acoes": [{"bruta": a} for a in grupo["acoes"]],
                                             "arquivos": grupo["arquivos"]}, depois[0])


def _aviso_de_semana(acao: dict) -> str:
    """Quem confere o lançamento olha a semana atual: avisa quando ele entrou em outra."""
    data = acao["dados"].get("data") if acao["tipo"] == "despesa" else ""
    if not data:
        return ""
    segunda = segunda_da_semana(date.fromisoformat(data))
    return "" if segunda == segunda_da_semana() else f"\n⚠️ Entrou na semana de {data_br(segunda)}, não na atual."


def _executar(app, chat_id: str, v: dict, acao: dict, arquivos: list[dict], obra_id: int) -> list[int]:
    executor = Executor(app, v["usuario_id"], obra_id)
    extra = executor.gravar(acao, arquivos)
    if extra.get("link"):
        dados, nome, mime = executor.baixar(extra["link"])
        telegram_api.enviar_arquivo(chat_id, dados, nome, mime, acao["descricao"])
    return executor.criadas


def _resolver_pendente(app, chat_id: str, v: dict, pendente: dict) -> None:
    acao = pendente["acao"]
    if pendente["motivo"] == "duplicada":
        acao["confirmar_duplicidade"] = True
    arquivos = [{**a, "dados": telegram_api.baixar(a["file_id"])} for a in pendente.get("arquivos", [])]
    v["pendentes"] = [p for p in v["pendentes"] if p.get("id") != pendente["id"]]
    try:
        _executar(app, chat_id, v, acao, arquivos, pendente.get("obra_id") or v["obra_id"])
        telegram_api.enviar(chat_id, "✅ " + catalogo.descrever(acao))
    except ErroAcao as erro:
        telegram_api.enviar(chat_id, f"⚠️ {acao.get('titulo', '')}: {erro.mensagem}")
    _salvar(chat_id, pendentes=v["pendentes"])


def _escolher_obra(app, chat_id: str, v: dict, pendente: dict, obra_id: int) -> None:
    """Obra escolhida no botão: confere as ações já interpretadas, sem nova ida à IA."""
    v["pendentes"] = [p for p in v["pendentes"] if p.get("id") != pendente["id"]]
    arquivos = [{**a, "dados": telegram_api.baixar(a["file_id"])} for a in pendente.get("arquivos", [])]
    pf, pg = _perfil(v)
    conferidas, avisos = catalogo.normalizar([a["bruta"] for a in pendente.get("acoes", [])],
                                             catalogo.Contexto(obra_id, hoje_iso(), pf, pg, arquivos,
                                                               reembolso=reembolso.padrao_de(v["usuario_id"])))
    resultado = {"acoes": conferidas, "avisos": avisos, "transcricao": "",
                 "resposta": pendente.get("resposta") or "Ok."}
    _finalizar(app, chat_id, v, resultado, arquivos, obra_id, pendente.get("transcricao", ""))
    if pendente.get("lote"):
        _encerrar_lote(pendente["lote"])


def tratar_botao(app, callback: dict) -> None:
    chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
    dados = callback.get("data") or ""
    v = vinculo(chat_id)
    if not v:
        telegram_api.responder_botao(callback.get("id", ""), "Conversa não conectada.")
        return
    tipo, _, resto = dados.partition(":")
    obras = obras_do_usuario(v["usuario_id"], v["papel"])

    if tipo == "obra":
        obra = next((o for o in obras if str(o["id"]) == resto), None)
        telegram_api.responder_botao(callback.get("id", ""), obra["nome"] if obra else "Obra indisponível.")
        if obra:
            _salvar(chat_id, obra_id=obra["id"])
            telegram_api.enviar(chat_id, f"🏗 Obra padrão: {obra['nome']}.")
        return

    pid, _, extra = resto.partition(":")
    pendente = next((p for p in v["pendentes"] if p.get("id") == pid), None)
    if not pendente:
        telegram_api.responder_botao(callback.get("id", ""), "Isso já foi resolvido.")
        return
    telegram_api.responder_botao(callback.get("id", ""))

    if tipo == "no":
        _salvar(chat_id, pendentes=[p for p in v["pendentes"] if p.get("id") != pid])
        if pendente.get("lote"):
            _encerrar_lote(pendente["lote"])
        telegram_api.enviar(chat_id, "Descartado.")
    elif tipo == "ok":
        if not _ocupar(chat_id):
            telegram_api.enviar(chat_id, "⏳ Ainda processando a mensagem anterior. Toque de novo em instantes.")
            return
        try:
            _resolver_pendente(app, chat_id, v, pendente)
        finally:
            _liberar(chat_id)
    elif tipo == "cr":
        if not _ocupar(chat_id):
            telegram_api.enviar(chat_id, "⏳ Ainda processando a mensagem anterior. Toque de novo em instantes.")
            return
        try:
            atual = vinculo(chat_id) or v
            pendente = next((p for p in atual["pendentes"] if p.get("id") == pid), None)
            if pendente:   # toque duplo não cria duas vezes
                linha, _, depois = _criar_obra(app, atual, pendente["dados"], pendente["grupos"])
                _salvar(chat_id, pendentes=[p for p in atual["pendentes"] if p.get("id") != pid],
                        historico=(atual["historico"] + [{"papel": "assistente", "texto": linha}])[-MAX_HISTORICO:])
                telegram_api.enviar(chat_id, linha)
                _fazer_na_obra_nova(app, chat_id, atual, depois)
        finally:
            _liberar(chat_id)
    elif tipo == "ob":
        obra = next((o for o in obras if str(o["id"]) == extra), None)
        if obra and _ocupar(chat_id):
            try:
                _salvar(chat_id, obra_id=obra["id"])
                _escolher_obra(app, chat_id, v, pendente, obra["id"])
            finally:
                _liberar(chat_id)
    elif tipo == "re":
        _salvar(chat_id, pendentes=[p for p in v["pendentes"] if p.get("id") != pid])
        processar(app, chat_id, pendente.get("texto", ""), lote=pendente.get("lote", ""), esperar=False)
