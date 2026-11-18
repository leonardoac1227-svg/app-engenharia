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

import logging
import os
import re
import threading
from datetime import datetime

from ..db import executar, liberar_leitura, registrar_auditoria, todos
from ..utils import bytes_anexo
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
_trava = threading.Lock()
_estado: dict = {"rodando": False}


def estado() -> dict:
    return dict(_estado)


def _numero_limpo(bruto) -> str:
    texto = re.sub(r"(?i)\b(pedido|ped|n[º°o])\b\.?:?", "", str(bruto or "")).strip(" :#.-")
    texto = re.sub(r"\s+", "", texto)
    # Pedido de verdade tem dígito; "-", "N/A" e "sem pedido" ficam de fora.
    return texto[:30] if re.search(r"\d", texto) else ""


def _candidatas() -> list[dict]:
    """Lançamentos com nota anexada que ainda não estão com o pedido."""
    linhas = todos(
        """SELECT x.id, x.fornecedor, x.descricao, x.valor, x.numero_nota, d.data, a.arquivo
             FROM despesas x
             JOIN despesa_anexos a ON a.despesa_id = x.id
             JOIN dias d ON d.id = x.dia_id
            WHERE COALESCE(x.numero_nota, '') NOT LIKE 'PED %'
            ORDER BY x.id DESC, a.id""")
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
    resposta, _ = ia_provedores.gerar(SISTEMA, "Qual o número do pedido neste documento?",
                                      arquivos, ESQUEMA, max_tokens=256, pensar="baixo", prazo=60)
    return _numero_limpo((resposta or {}).get("pedido") if isinstance(resposta, dict) else "")


def _rodar(aplicar: bool, usuario: dict) -> None:
    candidatas = _candidatas()
    liberar_leitura()
    _estado.update(total=len(candidatas))

    for item in candidatas:
        try:
            pedido = _ler_pedido(item)
        except Exception as erro:  # noqa: BLE001 — uma nota ruim não para as outras
            _estado["erros"].append({"despesa_id": item["despesa_id"], "fornecedor": item["fornecedor"],
                                     "erro": str(erro)[:160]})
            log.warning("Pedido da despesa #%s: %s", item["despesa_id"], erro)
        else:
            if pedido:
                depois = f"PED {pedido}"
                if aplicar:
                    executar("UPDATE despesas SET numero_nota = ? WHERE id = ?", (depois, item["despesa_id"]))
                _estado["achados"].append({k: item[k] for k in
                                           ("despesa_id", "fornecedor", "descricao", "valor", "data", "antes")}
                                          | {"depois": depois})
            else:
                _estado["sem_pedido"] += 1
        _estado["feitos"] += 1

    if aplicar:
        registrar_auditoria(usuario, "releu_pedidos", "despesa", None,
                            f"{len(_estado['achados'])} lançamento(s) com pedido gravado")


def iniciar(app, aplicar: bool, usuario: dict) -> dict:
    """Começa a releitura. Só uma por vez."""
    with _trava:
        if _estado.get("rodando"):
            return estado()
        _estado.clear()
        _estado.update(rodando=True, aplicar=aplicar, total=0, feitos=0, sem_pedido=0,
                       achados=[], erros=[], inicio=datetime.now().isoformat(timespec="seconds"),
                       fim=None, falha="")

    def tarefa():
        with app.app_context():
            try:
                _rodar(aplicar, usuario)
            except Exception as erro:  # noqa: BLE001
                log.exception("Releitura de pedidos falhou")
                _estado["falha"] = str(erro)[:200]
            finally:
                _estado.update(rodando=False, fim=datetime.now().isoformat(timespec="seconds"))

    if EM_SEGUNDO_PLANO:
        threading.Thread(target=tarefa, daemon=True).start()
    else:
        tarefa()
    return estado()
