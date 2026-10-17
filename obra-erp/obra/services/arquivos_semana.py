# -*- coding: utf-8 -*-
"""Arquivos da semana — todos os anexos num lugar só.

Hoje a nota fiscal fica escondida dentro do lançamento e a foto dentro do dia.
Quando o contador pede "as notas da semana", alguém precisa abrir despesa por
despesa. Aqui elas são reunidas: um índice único, com filtro, e um ZIP para
enviar de uma vez.

Os arquivos do ZIP são **renomeados para serem entendíveis fora do app**. Um
anexo chamado `c1849f0204ec4fe3.pdf` não diz nada na pasta de downloads do
contador; `notas/2026-08-18_NF-2353_INOVACIVIL_R$120,00.pdf` diz tudo.
"""

from __future__ import annotations

import io
import os
import re
import zipfile

from ..db import todos
from .parcelamento import sql_fora_da_semana
from ..utils import data_br

PASTA_NOTAS = "notas-fiscais"
PASTA_FOTOS = "fotos"


def _limpo(texto: str, tamanho: int = 42) -> str:
    """Nome de arquivo seguro em qualquer sistema, sem perder a legibilidade."""
    sem_acento = (texto or "").strip()
    limpo = re.sub(r'[\\/:*?"<>|\n\r\t]+', "-", sem_acento)
    limpo = re.sub(r"\s+", " ", limpo).strip(" .-")
    return limpo[:tamanho] or "sem-nome"


def listar_arquivos(semana_id: int) -> dict:
    """Índice de tudo que foi anexado na semana."""
    # Um item por arquivo: lançamento com nota + comprovante aparece duas vezes.
    notas = [{
        "id": linha["anexo_id"],
        "despesa_id": linha["id"],
        "tipo": "nota",
        "url": f"/arquivos/{linha['arquivo']}",
        "arquivo": linha["arquivo"],
        "nome_original": linha["nome_original"] or linha["arquivo"],
        "titulo": linha["descricao"] or "Lançamento",
        "subtitulo": " · ".join(filter(None, [
            linha["fornecedor"] or "",
            f"NF {linha['numero_nota']}" if linha["numero_nota"] else "",
            linha["categoria"] or "",
        ])),
        "data": linha["data"],
        "dia_semana": linha["dia_semana"],
        "valor": round(float(linha["valor"] or 0), 2),
        "status_pagamento": linha["status_pagamento"],
        "numero_nota": linha["numero_nota"] or "",
        "eh_pdf": str(linha["arquivo"]).lower().endswith(".pdf"),
    } for linha in todos(
        f"""SELECT x.id, a.id AS anexo_id, x.descricao, x.fornecedor, x.categoria, x.valor,
                  x.numero_nota, x.status_pagamento, a.arquivo, a.nome_original,
                  d.data, d.dia_semana
             FROM despesa_anexos a
             JOIN despesas x ON x.id = a.despesa_id
             JOIN dias d ON d.id = x.dia_id
            WHERE d.semana_id = ? AND NOT {sql_fora_da_semana()}
            ORDER BY d.data, x.id, a.id""", (semana_id,))]

    fotos = [{
        "id": linha["id"],
        "tipo": "foto",
        "url": f"/arquivos/{linha['arquivo']}",
        "arquivo": linha["arquivo"],
        "nome_original": linha["nome_original"] or linha["arquivo"],
        "titulo": linha["legenda"] or linha["ambiente"] or "Foto do diário",
        "subtitulo": linha["ambiente"] or "",
        "data": linha["data"],
        "dia_semana": linha["dia_semana"],
        "valor": None,
        "eh_pdf": False,
    } for linha in todos(
        """SELECT f.id, f.arquivo, f.nome_original, f.legenda, f.ambiente,
                  d.data, d.dia_semana
             FROM fotos f JOIN dias d ON d.id = f.dia_id
            WHERE d.semana_id = ?
            ORDER BY d.data, f.id""", (semana_id,))]

    return {
        "arquivos": notas + fotos,
        "total": len(notas) + len(fotos),
        "notas": len(notas),
        "fotos": len(fotos),
        # Soma cada lançamento uma vez, mesmo que ele tenha vários arquivos.
        "valor_com_nota": round(sum({n["despesa_id"]: n["valor"] for n in notas}.values()), 2),
    }


def _nome_no_zip(item: dict) -> str:
    """Nome legível dentro do pacote, com a data na frente para ordenar sozinho."""
    extensao = os.path.splitext(item["arquivo"])[1] or ".dat"
    data = str(item.get("data") or "")[:10] or "sem-data"

    if item["tipo"] == "nota":
        partes = [data]
        if item.get("numero_nota"):
            partes.append(f"NF-{_limpo(item['numero_nota'], 20)}")
        partes.append(_limpo(item["subtitulo"].split(" · ")[0] or item["titulo"], 32))
        if item.get("valor"):
            partes.append(f"R${item['valor']:.2f}".replace(".", ","))
        return f"{PASTA_NOTAS}/{'_'.join(partes)}{extensao}"

    return f"{PASTA_FOTOS}/{data}_{_limpo(item['titulo'], 40)}{extensao}"


def gerar_zip(semana_id: int, dados_semana: dict, ler_bytes) -> tuple[bytes, dict]:
    """Empacota os anexos da semana. `ler_bytes(nome)` devolve o conteúdo.

    Arquivo que não abre não derruba o pacote: ele é anotado num relatório de
    texto incluído no próprio ZIP. Quem receber sabe exatamente o que faltou,
    em vez de descobrir a ausência conferindo lançamento por lançamento.
    """
    indice = listar_arquivos(semana_id)
    buffer = io.BytesIO()
    incluidos, ausentes, usados = 0, [], set()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        for item in indice["arquivos"]:
            conteudo = ler_bytes(item["arquivo"])
            if not conteudo:
                ausentes.append(item)
                continue

            nome = _nome_no_zip(item)
            if nome in usados:   # dois anexos podem gerar o mesmo nome
                raiz, extensao = os.path.splitext(nome)
                contador = 2
                while f"{raiz}_{contador}{extensao}" in usados:
                    contador += 1
                nome = f"{raiz}_{contador}{extensao}"
            usados.add(nome)

            pacote.writestr(nome, conteudo)
            incluidos += 1

        resumo = [
            f"ARQUIVOS DA SEMANA {dados_semana.get('semana_numero', '')}",
            f"Obra: {dados_semana.get('obra_nome', '')}",
            f"Período: {data_br(dados_semana.get('data_inicio'))} a "
            f"{data_br(dados_semana.get('data_fim'))}",
            "",
            f"Notas fiscais e comprovantes: {indice['notas']}",
            f"Fotos do diário: {indice['fotos']}",
            f"Arquivos incluídos neste pacote: {incluidos}",
        ]
        if ausentes:
            resumo += ["", f"NÃO INCLUÍDOS ({len(ausentes)}) — arquivo não encontrado:"]
            resumo += [f"  - [{i['tipo']}] {i['titulo']} ({data_br(i['data'])})" for i in ausentes]
        pacote.writestr("_conteudo.txt", "\n".join(resumo) + "\n")

    return buffer.getvalue(), {
        "incluidos": incluidos,
        "ausentes": len(ausentes),
        "total": indice["total"],
    }
