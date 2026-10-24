# -*- coding: utf-8 -*-
"""Grava no servidor as ações propostas pelo assistente (usado pelo bot do Telegram).

Na tela, quem grava é o navegador, chamando os endpoints de sempre. O bot não
tem navegador, então faz o mesmo por dentro: cada ação vira uma requisição
interna aos **mesmos endpoints**, autenticada como o usuário vinculado ao chat.

Por que não gravar direto no banco: lançamento de despesa tem regra demais
(nota em duplicidade, parcelamento, frete, locação, compressão de anexo,
auditoria, permissão do perfil). Repetir isso aqui seria abrir a porta para as
duas versões divergirem. Passando pelo endpoint, o bot obedece exatamente as
mesmas travas da tela — inclusive o bloqueio do caixa para o mestre de obra.
"""

from __future__ import annotations

import io
import secrets

from ..utils import hoje_iso

BASE_INTERNA = "https://bot.interno"
ORDEM = ["funcionario", "diario", "ponto", "foto", "despesa", "editar_despesa", "pagar_despesa",
         "excluir_despesa"]
EXTENSOES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic",
             "image/heif": "heif", "application/pdf": "pdf"}


class ErroAcao(Exception):
    def __init__(self, mensagem: str, codigo: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo


def _sem_acento(valor: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", valor or "")
                   if unicodedata.category(c) != "Mn").lower().strip()


def _nome_arquivo(arquivo: dict, indice: int) -> str:
    nome = arquivo.get("nome") or ""
    ext = EXTENSOES.get(arquivo.get("mime"), "")
    if not nome or "." not in nome:
        nome = f"telegram-{indice + 1}.{ext or 'bin'}"
    return nome


class Executor:
    def __init__(self, app, usuario_id: int, obra_id: int):
        self.obra_id = obra_id
        self.cliente = app.test_client()
        self.csrf = secrets.token_urlsafe(24)
        with self.cliente.session_transaction(base_url=BASE_INTERNA) as sessao:
            sessao["usuario_id"] = usuario_id
            sessao["csrf"] = self.csrf
        self.dias: dict[str, int] = {}
        self.novos: dict[str, int] = {}

    # ------------------------------------------------------------ http interno

    def _req(self, metodo: str, caminho: str, json=None, dados=None):
        parametros = {"method": metodo, "base_url": BASE_INTERNA,
                      "headers": {"X-CSRF-Token": self.csrf}}
        if dados is not None:
            parametros.update(data=dados, content_type="multipart/form-data")
        elif json is not None:
            parametros["json"] = json
        resposta = self.cliente.open(caminho, **parametros)
        corpo = resposta.get_json(silent=True) or {}
        if resposta.status_code >= 400:
            raise ErroAcao(corpo.get("erro") or f"Erro {resposta.status_code}.", corpo.get("codigo", ""))
        return corpo

    def dia(self, data: str | None) -> int:
        alvo = data or hoje_iso()
        if alvo not in self.dias:
            self.dias[alvo] = self._req("POST", f"/api/obras/{self.obra_id}/dia-por-data",
                                        json={"data": alvo})["dia_id"]
        return self.dias[alvo]

    @staticmethod
    def _arquivos(indices, arquivos: list[dict]):
        return [(io.BytesIO(arquivos[i]["dados"]), _nome_arquivo(arquivos[i], i))
                for i in indices or [] if 0 <= i < len(arquivos)]

    # ------------------------------------------------------------ ações

    def gravar(self, a: dict, arquivos: list[dict]) -> None:
        tipo = a["tipo"]

        if tipo == "funcionario":
            r = self._req("POST", f"/api/obras/{self.obra_id}/funcionarios",
                          json={"nome": a["nome"], "funcao": a.get("funcao", "")})
            self.novos[_sem_acento(a["nome"])] = r["id"]

        elif tipo == "diario":
            self._req("PUT", f"/api/dias/{self.dia(a['data'])}",
                      json={k: a.get(k, "") for k in ("producao_dia", "clima", "observacoes")})

        elif tipo == "ponto":
            registros = [{"funcionario_id": r["funcionario_id"] or self.novos.get(_sem_acento(r["nome"]), 0),
                          "horas": r["horas"], "justificativa": r.get("justificativa", "")}
                         for r in a["registros"]]
            registros = [r for r in registros if r["funcionario_id"]]
            if not registros:
                raise ErroAcao("Nenhum funcionário cadastrado para este ponto.")
            self._req("PUT", f"/api/dias/{self.dia(a['data'])}/horas", json={"registros": registros})

        elif tipo == "foto":
            dados = {"fotos": self._arquivos(a["anexos"], arquivos)}
            if a.get("legenda"):
                dados["legenda"] = a["legenda"]
            if a.get("ambiente"):
                dados["ambiente"] = a["ambiente"]
            self._req("POST", f"/api/dias/{self.dia(a['data'])}/fotos", dados=dados)

        elif tipo == "despesa":
            if not a.get("descricao") or not a.get("valor"):
                raise ErroAcao("Faltam descrição ou valor.", "faltando")
            dados = {k: str(a[k]) for k in ("categoria", "descricao", "fornecedor", "valor",
                                             "status_pagamento", "numero_nota", "vencimento",
                                             "reembolsar_para") if a.get(k)}
            if a.get("parcelas"):
                dados.update(parcelas=str(a["parcelas"]), parcela_frequencia=a["parcela_frequencia"])
            if a.get("confirmar_duplicidade"):
                dados["confirmar_duplicidade"] = "1"
            dados["anexos"] = self._arquivos(a.get("anexos"), arquivos)
            self._req("POST", f"/api/dias/{self.dia(a['data'])}/despesas", dados=dados)

        elif tipo == "editar_despesa":
            alteracoes = dict(a.get("alteracoes") or {})
            if alteracoes:
                if a.get("confirmar_duplicidade"):
                    alteracoes["confirmar_duplicidade"] = True
                self._req("PUT", f"/api/despesas/{a['despesa_id']}", json=alteracoes)
            anexos = self._arquivos(a.get("anexos"), arquivos)
            if anexos:
                self._req("POST", f"/api/despesas/{a['despesa_id']}/anexos", dados={"anexos": anexos})

        elif tipo == "pagar_despesa":
            self._req("POST", f"/api/despesas/{a['despesa_id']}/pagar",
                      json={"pago_em": a.get("pago_em") or hoje_iso()})

        elif tipo == "excluir_despesa":
            self._req("DELETE", f"/api/despesas/{a['despesa_id']}")
