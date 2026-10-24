# -*- coding: utf-8 -*-
"""Grava as ações do assistente pelos mesmos endpoints da tela.

Cada ação vira uma requisição interna aos **endpoints de sempre**, autenticada
como o usuário que pediu. Assim o assistente (na tela ou no Telegram) obedece
exatamente as mesmas regras dos formulários: nota em duplicidade,
parcelamento, frete, locação, compressão de anexo, auditoria e permissão do
perfil (o mestre de obra continua sem acesso ao caixa).

Repetir essas regras aqui seria abrir a porta para as duas versões
divergirem; passar pelo endpoint garante uma só.
"""

from __future__ import annotations

import io
import secrets

from ..utils import hoje_iso
from .acoes import CATALOGO

BASE_INTERNA = "https://assistente.interno"
ORDEM = ["editar_obra", "funcionario", "editar_funcionario", "semana", "diario", "repetir_ponto", "ponto",
         "foto", "despesa", "editar_despesa", "pagar_despesa", "locacao_lancar", "locacao_prorrogar",
         "locacao_encerrar", "excluir_despesa", "pagar_semana", "pagar_folha", "fechar_semana", "relatorio"]
EXTENSOES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic",
             "image/heif": "heif", "application/pdf": "pdf"}


class ErroAcao(Exception):
    def __init__(self, mensagem: str, codigo: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo


def ordenar(acoes: list[dict]) -> list[dict]:
    """Cadastro antes do ponto, lançamento antes da baixa, relatório por último."""
    return sorted(acoes, key=lambda a: ORDEM.index(a["tipo"]) if a["tipo"] in ORDEM else len(ORDEM))


def _nome_arquivo(arquivo: dict, indice: int) -> str:
    nome = arquivo.get("nome") or ""
    if not nome or "." not in nome:
        nome = f"anexo-{indice + 1}.{EXTENSOES.get(arquivo.get('mime'), 'bin')}"
    return nome


class Executor:
    Erro = ErroAcao

    def __init__(self, app, usuario_id: int, obra_id: int):
        self.obra_id = obra_id
        self.cliente = app.test_client()
        self.csrf = secrets.token_urlsafe(24)
        with self.cliente.session_transaction(base_url=BASE_INTERNA) as sessao:
            sessao["usuario_id"] = usuario_id
            sessao["csrf"] = self.csrf
        self.dias: dict[str, int] = {}
        self.semanas: dict[str, int] = {}
        self.novos: dict[str, int] = {}

    # ------------------------------------------------------------ http interno

    def req(self, metodo: str, caminho: str, json=None, dados=None):
        parametros = {"method": metodo, "base_url": BASE_INTERNA, "headers": {"X-CSRF-Token": self.csrf}}
        if dados is not None:
            parametros.update(data=dados, content_type="multipart/form-data")
        elif json is not None:
            parametros["json"] = json
        resposta = self.cliente.open(caminho, **parametros)
        corpo = resposta.get_json(silent=True) or {}
        if resposta.status_code >= 400:
            raise ErroAcao(corpo.get("erro") or f"Erro {resposta.status_code}.", corpo.get("codigo", ""))
        return corpo

    def baixar(self, caminho: str) -> tuple[bytes, str, str]:
        """Arquivo gerado pelo app (PDF, planilha...) para mandar no Telegram."""
        resposta = self.cliente.get(caminho, base_url=BASE_INTERNA)
        if resposta.status_code >= 400:
            corpo = resposta.get_json(silent=True) or {}
            raise ErroAcao(corpo.get("erro") or f"Erro {resposta.status_code} ao gerar o arquivo.")
        disposicao = resposta.headers.get("Content-Disposition", "")
        nome = disposicao.split("filename=")[-1].strip('"; ') if "filename=" in disposicao else caminho.rsplit("/", 1)[-1]
        return resposta.data, nome, resposta.mimetype or "application/octet-stream"

    def dia(self, data: str | None) -> int:
        alvo = data or hoje_iso()
        if alvo not in self.dias:
            r = self.req("POST", f"/api/obras/{self.obra_id}/dia-por-data", json={"data": alvo})
            self.dias[alvo] = r["dia_id"]
            self.semanas[alvo] = r["semana_id"]
        return self.dias[alvo]

    def semana(self, data: str | None) -> int:
        alvo = data or hoje_iso()
        if alvo not in self.semanas:
            self.dia(alvo)
        return self.semanas[alvo]

    @staticmethod
    def arquivos(indices, arquivos: list[dict]):
        return [(io.BytesIO(arquivos[i]["dados"]), _nome_arquivo(arquivos[i], i))
                for i in indices or [] if 0 <= i < len(arquivos)]

    # ------------------------------------------------------------ ações

    def gravar(self, acao: dict, arquivos: list[dict]) -> dict:
        tipo = CATALOGO[acao["tipo"]]
        dados = dict(acao["dados"])
        if acao.get("confirmar_duplicidade"):
            dados["confirmar_duplicidade"] = True
        return tipo.executar(self, dados, arquivos) or {}
