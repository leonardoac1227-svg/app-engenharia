# -*- coding: utf-8 -*-
"""Armazenamento de anexos — disco local ou Supabase Storage.

Fotos de canteiro e notas fiscais são o dado mais volumoso do app. No Render
sem disco persistente, tudo isso some a cada deploy; por isso, quando as
variáveis do Supabase estão presentes, os arquivos vão para um bucket.

Regra de ouro: **o bucket deve ser privado**. O app continua sendo o guardião
do acesso — a rota `/arquivos/<nome>` valida a sessão e só então gera uma URL
assinada de curta duração. Bucket público jogaria fora a correção de segurança
mais importante da v2 (anexos que eram acessíveis por link na v1).

Não há dependência nova: as chamadas usam `urllib` da biblioteca padrão.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request

from . import config

TEMPO_LIMITE = 20  # segundos


class ErroStorage(RuntimeError):
    pass


def usando_supabase() -> bool:
    return config.STORAGE == "supabase"


# ---------------------------------------------------------------- Supabase

def _url_base() -> str:
    return f"{config.SUPABASE_URL}/storage/v1"


def _cabecalhos(extra: dict | None = None) -> dict:
    cabecalhos = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "apikey": config.SUPABASE_SERVICE_KEY,
    }
    cabecalhos.update(extra or {})
    return cabecalhos


def _requisitar(metodo: str, caminho: str, dados=None, cabecalhos=None):
    requisicao = urllib.request.Request(
        f"{_url_base()}{caminho}", data=dados, method=metodo,
        headers=_cabecalhos(cabecalhos),
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=TEMPO_LIMITE) as resposta:
            corpo = resposta.read()
            return corpo
    except urllib.error.HTTPError as erro:
        detalhe = erro.read().decode("utf-8", "replace")[:300]
        raise ErroStorage(f"Supabase Storage {erro.code}: {detalhe}") from erro
    except urllib.error.URLError as erro:
        raise ErroStorage(f"Sem resposta do Supabase Storage: {erro.reason}") from erro


def enviar(nome: str, conteudo: bytes, tipo: str | None = None) -> None:
    """Sobe (ou substitui) um arquivo no bucket."""
    tipo = tipo or mimetypes.guess_type(nome)[0] or "application/octet-stream"
    _requisitar(
        "POST", f"/object/{config.SUPABASE_BUCKET}/{nome}", conteudo,
        {"Content-Type": tipo, "x-upsert": "true"},
    )


def url_assinada(nome: str, segundos: int | None = None) -> str | None:
    """Gera uma URL temporária de leitura. Devolve None se o arquivo sumiu."""
    validade = segundos or config.SUPABASE_URL_VALIDADE
    corpo = json.dumps({"expiresIn": validade}).encode("utf-8")
    try:
        resposta = _requisitar(
            "POST", f"/object/sign/{config.SUPABASE_BUCKET}/{nome}", corpo,
            {"Content-Type": "application/json"},
        )
    except ErroStorage:
        return None
    dados = json.loads(resposta.decode("utf-8"))
    caminho = dados.get("signedURL") or dados.get("signedUrl") or ""
    if not caminho:
        return None
    return f"{_url_base()}{caminho}" if caminho.startswith("/") else caminho


def baixar(nome: str) -> bytes | None:
    try:
        return _requisitar("GET", f"/object/{config.SUPABASE_BUCKET}/{nome}")
    except ErroStorage:
        return None


def apagar(nome: str) -> None:
    try:
        _requisitar("DELETE", f"/object/{config.SUPABASE_BUCKET}/{nome}")
    except ErroStorage:
        pass  # arquivo já removido não é motivo para derrubar a operação


# ---------------------------------------------------------------- disco local

def gravar_local(nome: str, conteudo: bytes) -> None:
    with open(os.path.join(config.UPLOAD_DIR, nome), "wb") as arquivo:
        arquivo.write(conteudo)


def caminho_local(nome: str) -> str | None:
    for pasta in (config.UPLOAD_DIR, config.LEGACY_UPLOAD_DIR):
        caminho = os.path.join(pasta, os.path.basename(nome))
        if os.path.exists(caminho):
            return caminho
    return None


# ---------------------------------------------------------------- fachada

def gravar(nome: str, conteudo: bytes, tipo: str | None = None) -> None:
    """Grava o anexo no destino configurado.

    No modo Supabase, se o envio falhar (queda de rede, chave errada), o
    arquivo cai no disco local em vez de o lançamento inteiro se perder — o
    gestor está no canteiro e não pode perder a nota fiscal por causa de 4G
    instável. A rota de leitura procura nos dois lugares.
    """
    if usando_supabase():
        try:
            enviar(nome, conteudo, tipo)
            return
        except ErroStorage:
            pass
    gravar_local(nome, conteudo)


def remover(nome: str) -> None:
    if not nome:
        return
    if usando_supabase():
        apagar(os.path.basename(nome))
    caminho = caminho_local(nome)
    if caminho:
        try:
            os.remove(caminho)
        except OSError:
            pass
