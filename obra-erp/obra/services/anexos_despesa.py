# -*- coding: utf-8 -*-
"""Anexos de um lançamento — nota, comprovante, foto do material.

Um lançamento pode ter vários arquivos: a nota fiscal, o comprovante do PIX e
a foto do material entregue. Eles moram em `despesa_anexos`; a coluna antiga
`despesas.imagem_path` continua espelhando o **primeiro** anexo, para que
relatórios, PDF e planilha que só conhecem um arquivo sigam funcionando.

O mesmo arquivo pode estar ligado a mais de um lançamento (as parcelas de uma
nota dividem o anexo da nota). Por isso um arquivo só é apagado do disco ou do
bucket quando nenhum lançamento aponta mais para ele.
"""

from __future__ import annotations

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, remover_anexo, salvar_anexo

LIMITE_POR_LANCAMENTO = 5


def _serializar(linha) -> dict:
    arquivo = linha["arquivo"]
    return {
        "id": linha["id"],
        "url": f"/arquivos/{arquivo}",
        "nome": linha["nome_original"] or arquivo,
        "eh_pdf": str(arquivo).lower().endswith(".pdf"),
    }


def anexos_por_despesa(ids) -> dict[int, list[dict]]:
    """{despesa_id: [anexos]} para uma lista de lançamentos, numa consulta só."""
    ids = [int(i) for i in ids if i]
    if not ids:
        return {}
    marcas = ", ".join("?" for _ in ids)
    resultado: dict[int, list[dict]] = {}
    for linha in todos(
            f"""SELECT id, despesa_id, arquivo, nome_original FROM despesa_anexos
                 WHERE despesa_id IN ({marcas}) ORDER BY despesa_id, id""", ids):
        resultado.setdefault(linha["despesa_id"], []).append(_serializar(linha))
    return resultado


def anexar_lista(itens: list[dict]) -> list[dict]:
    """Acrescenta a chave `anexos` em cada lançamento serializado."""
    mapa = anexos_por_despesa([item["id"] for item in itens])
    for item in itens:
        item["anexos"] = mapa.get(item["id"], [])
    return itens


def sincronizar_principal(despesa_id: int) -> None:
    """Mantém imagem_path/nome_arquivo iguais ao primeiro anexo."""
    primeiro = um("""SELECT arquivo, nome_original FROM despesa_anexos
                      WHERE despesa_id = ? ORDER BY id LIMIT 1""", (despesa_id,))
    executar("UPDATE despesas SET imagem_path = ?, nome_arquivo = ? WHERE id = ?",
             (primeiro["arquivo"] if primeiro else "",
              primeiro["nome_original"] if primeiro else "", despesa_id))


def registrar(despesa_id: int, arquivo: str, nome_original: str, tamanho: int = 0) -> None:
    """Liga um arquivo já gravado a um lançamento (usado ao copiar para parcelas)."""
    executar("""INSERT INTO despesa_anexos (despesa_id, arquivo, nome_original, tamanho, criado_em)
                VALUES (?, ?, ?, ?, ?)""",
             (despesa_id, arquivo, nome_original or "", int(tamanho or 0), agora_iso()))


def adicionar(despesa_id: int, arquivos) -> list[dict]:
    """Grava os uploads e liga ao lançamento. Devolve os metadados de cada um.

    Confere o limite **antes** de gravar qualquer arquivo: estourar no meio
    deixaria arquivos soltos no armazenamento sem lançamento nenhum.
    """
    arquivos = [a for a in arquivos if a and a.filename]
    if not arquivos:
        return []

    existentes = um("SELECT COUNT(*) AS total FROM despesa_anexos WHERE despesa_id = ?",
                    (despesa_id,))["total"]
    if existentes + len(arquivos) > LIMITE_POR_LANCAMENTO:
        raise ApiErro(f"Cada lançamento aceita até {LIMITE_POR_LANCAMENTO} anexos.")

    gravados = []
    for arquivo in arquivos:
        meta = salvar_anexo(arquivo)
        if not meta:
            continue
        registrar(despesa_id, meta["arquivo"], meta["nome_original"], meta["tamanho"])
        gravados.append(meta)

    sincronizar_principal(despesa_id)
    return gravados


def _ainda_em_uso(arquivo: str) -> bool:
    return bool(um("SELECT 1 AS sim FROM despesa_anexos WHERE arquivo = ? LIMIT 1", (arquivo,))
                or um("SELECT 1 AS sim FROM despesas WHERE imagem_path = ? LIMIT 1", (arquivo,)))


def apagar_arquivos_orfaos(arquivos) -> None:
    for arquivo in set(filter(None, arquivos)):
        if not _ainda_em_uso(arquivo):
            remover_anexo(arquivo)


def remover(despesa_id: int, anexo_id: int) -> None:
    anexo = um("SELECT arquivo FROM despesa_anexos WHERE id = ? AND despesa_id = ?",
               (anexo_id, despesa_id))
    if not anexo:
        raise ApiErro("Anexo não encontrado.", 404)
    executar("DELETE FROM despesa_anexos WHERE id = ?", (anexo_id,))
    sincronizar_principal(despesa_id)
    apagar_arquivos_orfaos([anexo["arquivo"]])


def arquivos_da_despesa(despesa_id: int) -> list[str]:
    """Todos os arquivos ligados ao lançamento (para limpar depois de excluí-lo)."""
    linhas = todos("SELECT arquivo FROM despesa_anexos WHERE despesa_id = ?", (despesa_id,))
    principal = um("SELECT imagem_path FROM despesas WHERE id = ?", (despesa_id,))
    arquivos = [linha["arquivo"] for linha in linhas]
    if principal and principal["imagem_path"]:
        arquivos.append(principal["imagem_path"])
    return arquivos
