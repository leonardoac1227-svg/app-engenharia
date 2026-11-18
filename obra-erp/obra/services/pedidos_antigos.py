# -*- coding: utf-8 -*-
"""Releitura das notas já enviadas para achar o NÚMERO DO PEDIDO.

A regra nova (o pedido vai no lugar da NF, como "PED 48213") só vale para o
que chega daqui para a frente. Os lançamentos antigos têm o PDF ou a foto da
nota anexados: a IA relê cada um e procura o pedido impresso.

Roda em segundo plano (são dezenas de notas, cada uma leva alguns segundos e
o Render corta a requisição em 30 s). Primeiro em modo **conferência**, que
só lista o que mudaria; depois, com `aplicar`, grava.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta

from .. import config
from ..db import executar, liberar_leitura, registrar_auditoria, todos
from ..utils import bytes_anexo, hoje_iso
from . import ia_provedores

log = logging.getLogger("obra-erp")

MIME_POR_EXTENSAO = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".png": "image/png", ".webp": "image/webp"}

SISTEMA = """Você lê notas fiscais, cupons e pedidos de lojas de material de construção.
Procure em TODA a página o NÚMERO DO PEDIDO impresso: "Pedido", "Nº do pedido", "Ped.",
"Pedido de venda", "Nº pedido cliente", "Pedido/Orçamento" — no cabeçalho, no corpo ou nas
informações complementares / dados adicionais.
NÃO é pedido: número da NF, série, chave de acesso, protocolo, CNPJ, CPF, código de produto, NCM,
CFOP, código de barras, número do caixa ou do cupom (COO/CCF).
Responda só o número do pedido, sem o texto "pedido". Sem pedido impresso, responda pedido vazio.
Não invente."""

ESQUEMA = {"type": "object", "properties": {"pedido": {"type": "string"}}}

EM_SEGUNDO_PLANO = True      # os testes desligam para rodar tudo na mesma thread
# O Gemini gratuito aceita poucas leituras por minuto: no ritmo cheio a cota
# estoura na 20ª nota (HTTP 429). Uma nota a cada PAUSA segundos fica abaixo.
PAUSA = 7
ESPERA_COTA = 65             # cota por minuto estourada: espera ela renovar
TENTATIVAS = 3
MAX_FALHAS_SEGUIDAS = 4      # depois disso a cota do dia acabou: para e guarda o que já leu
_trava = threading.Lock()
_estado: dict = {"rodando": False}


def _arquivo() -> str:
    return os.path.join(config.DATA_DIR, "pedidos_antigos.json")


def _salvar() -> None:
    """Progresso em disco: um reinício do servidor não perde as notas já lidas."""
    try:
        with open(_arquivo(), "w", encoding="utf-8") as saida:
            json.dump(_estado, saida, ensure_ascii=False)
    except OSError as erro:
        log.warning("Pedidos: não foi possível salvar o progresso: %s", erro)


def estado() -> dict:
    if not _estado.get("inicio"):
        try:
            with open(_arquivo(), encoding="utf-8") as entrada:
                _estado.update(json.load(entrada))
        except (OSError, ValueError):
            pass
        if _estado.get("rodando"):
            # Estava rodando quando o servidor reiniciou: a thread morreu junto.
            _estado.update(rodando=False, falha="Interrompida (o servidor reiniciou). "
                                                 "Toque em Conferir: continua de onde parou.")
    return dict(_estado)


def _numero_limpo(bruto) -> str:
    # "PED3308", "Pedido nº 48213", "Ped.: 5549", "Nº 77" → só o número.
    texto = re.sub(r"(?i)^\s*(pedido|ped)?\.?\s*(de\s+venda\s*)?(n[º°o]?\.?)?\s*[:#\-]?\s*", "",
                   str(bruto or "")).strip(" :#.-")
    texto = re.sub(r"\s+", "", texto)
    # Pedido de verdade tem dígito; "-", "N/A" e "sem pedido" ficam de fora.
    return texto[:30] if re.search(r"\d", texto) else ""


def inicio_semana_passada(hoje: str | None = None) -> str:
    """Segunda-feira da semana passada: o padrão é reler só a semana passada e a atual."""
    dia = date.fromisoformat((hoje or hoje_iso())[:10])
    return (dia - timedelta(days=dia.weekday() + 7)).isoformat()


def _candidatas(desde: str) -> list[dict]:
    """Lançamentos com nota anexada, a partir de `desde`, que ainda não estão com o pedido."""
    linhas = todos(
        """SELECT x.id, x.fornecedor, x.descricao, x.valor, x.numero_nota, d.data, a.arquivo
             FROM despesas x
             JOIN despesa_anexos a ON a.despesa_id = x.id
             JOIN dias d ON d.id = x.dia_id
            WHERE COALESCE(x.numero_nota, '') NOT LIKE ? AND d.data >= ?
            ORDER BY x.id DESC, a.id""", ("PED %", desde))  # "%" no texto do SQL quebra o psycopg
    por_despesa: dict[int, dict] = {}
    for linha in linhas:
        extensao = os.path.splitext(str(linha["arquivo"]))[1].lower()
        if extensao not in MIME_POR_EXTENSAO:
            continue
        item = por_despesa.setdefault(linha["id"], {
            "despesa_id": linha["id"], "fornecedor": linha["fornecedor"] or "",
            "descricao": linha["descricao"] or "", "valor": float(linha["valor"] or 0),
            "data": str(linha["data"] or "")[:10], "antes": linha["numero_nota"] or "",
            "arquivos": []})
        if len(item["arquivos"]) < 3:
            item["arquivos"].append((linha["arquivo"], MIME_POR_EXTENSAO[extensao]))
    return list(por_despesa.values())


def _ler_pedido(item: dict) -> str:
    arquivos = []
    for nome, mime in item["arquivos"]:
        dados = bytes_anexo(nome)
        if dados:
            arquivos.append({"nome": os.path.basename(nome), "mime": mime, "dados": dados})
    if not arquivos:
        raise ValueError("arquivo da nota não encontrado")
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            resposta, _ = ia_provedores.gerar(SISTEMA, "Qual o número do pedido neste documento?",
                                              arquivos, ESQUEMA, max_tokens=256, pensar="baixo", prazo=45)
            break
        except ia_provedores.IaIndisponivel:
            if tentativa == TENTATIVAS:
                raise
            time.sleep(ESPERA_COTA)
    return _numero_limpo((resposta or {}).get("pedido") if isinstance(resposta, dict) else "")


def _rodar(aplicar: bool, usuario: dict) -> None:
    candidatas = _candidatas(_estado["desde"])
    liberar_leitura()
    feitos = set(_estado["processados"])
    _estado.update(total=len(candidatas))
    _salvar()
    falhas_seguidas = 0

    for item in candidatas:
        if item["despesa_id"] in feitos:
            continue
        try:
            pedido = _ler_pedido(item)
        except ia_provedores.IaIndisponivel as erro:
            falhas_seguidas += 1
            log.warning("Pedido da despesa #%s: %s", item["despesa_id"], erro)
            if falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                _estado["falha"] = ("A IA parou de responder (cota gratuita do Gemini esgotada). "
                                    "Toque em Conferir mais tarde: continua de onde parou.")
                _salvar()
                return
            continue      # não marca como feita: entra na próxima rodada
        except Exception as erro:  # noqa: BLE001 — uma nota ruim não para as outras
            _estado["erros"].append({"despesa_id": item["despesa_id"], "fornecedor": item["fornecedor"],
                                     "erro": str(erro)[:160]})
            log.warning("Pedido da despesa #%s: %s", item["despesa_id"], erro)
        else:
            falhas_seguidas = 0
            if pedido:
                depois = f"PED {pedido}"
                if aplicar:
                    executar("UPDATE despesas SET numero_nota = ? WHERE id = ?", (depois, item["despesa_id"]))
                _estado["achados"].append({k: item[k] for k in
                                           ("despesa_id", "fornecedor", "descricao", "valor", "data", "antes")}
                                          | {"depois": depois})
            else:
                _estado["sem_pedido"] += 1
        _estado["processados"].append(item["despesa_id"])
        _estado["feitos"] = len(_estado["processados"])
        _salvar()
        time.sleep(PAUSA)

    if aplicar:
        registrar_auditoria(usuario, "releu_pedidos", "despesa", None,
                            f"{len(_estado['achados'])} lançamento(s) com pedido gravado")


def _gravar_conferidos(usuario: dict) -> None:
    """Grava exatamente a lista da última conferência, sem reler as notas.

    O que vai para o banco é o que o gestor viu e aprovou — e leva segundos,
    não os minutos de uma nova leitura pela IA (que ainda poderia variar).
    """
    for item in _estado["achados"]:
        executar("UPDATE despesas SET numero_nota = ? WHERE id = ? AND COALESCE(numero_nota, '') = ?",
                 (item["depois"], item["despesa_id"], item["antes"]))
    registrar_auditoria(usuario, "releu_pedidos", "despesa", None,
                        f"{len(_estado['achados'])} lançamento(s) com pedido gravado")


def iniciar(app, aplicar: bool, usuario: dict, desde: str | None = None) -> dict:
    """Começa a releitura. Só uma por vez.

    - `aplicar` logo depois de uma conferência concluída grava a lista conferida.
    - Conferência interrompida (cota, reinício): Conferir de novo continua de onde parou.
    - Sem conferência antes, `aplicar` relê e grava de uma vez.
    - `desde`: data inicial (padrão: segunda-feira da semana passada).
    """
    desde = desde or inicio_semana_passada()
    with _trava:
        anterior = estado()
        if anterior.get("rodando"):
            return anterior
        concluida = anterior.get("fim") and not anterior.get("falha")
        if aplicar and anterior.get("aplicar") is False and concluida and anterior.get("desde") == desde:
            _gravar_conferidos(usuario)
            _estado["aplicar"] = True
            _salvar()
            return estado()

        retomar = (not aplicar and anterior.get("aplicar") is False and not concluida
                   and anterior.get("processados") and anterior.get("desde") == desde)
        if retomar:
            # Notas que deram erro entram de novo; as lidas ficam como estão.
            com_erro = {e["despesa_id"] for e in anterior.get("erros") or []}
            _estado.update(rodando=True, fim=None, falha="", erros=[],
                           processados=[i for i in anterior["processados"] if i not in com_erro])
        else:
            _estado.clear()
            _estado.update(rodando=True, aplicar=aplicar, total=0, feitos=0, sem_pedido=0,
                           achados=[], erros=[], processados=[], desde=desde,
                           inicio=datetime.now().isoformat(timespec="seconds"), fim=None, falha="")
        _estado["feitos"] = len(_estado["processados"])
        _salvar()

    def tarefa():
        with app.app_context():
            try:
                _rodar(aplicar, usuario)
            except Exception as erro:  # noqa: BLE001
                log.exception("Releitura de pedidos falhou")
                _estado["falha"] = str(erro)[:200]
            finally:
                _estado.update(rodando=False, fim=datetime.now().isoformat(timespec="seconds"))
                _salvar()

    if EM_SEGUNDO_PLANO:
        threading.Thread(target=tarefa, daemon=True).start()
    else:
        tarefa()
    return estado()
