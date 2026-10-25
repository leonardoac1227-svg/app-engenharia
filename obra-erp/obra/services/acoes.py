# -*- coding: utf-8 -*-
"""Catálogo de ações do assistente — tudo que a IA pode fazer no app.

Cada ação descreve, num lugar só:

* **o que a IA deve preencher** (`campos`, que vira texto do prompt);
* **quem pode** (`permissao`: lançar, financeiro ou gestor);
* **se precisa de confirmação** (exclusão, baixa em lote, fechar semana);
* **como conferir** o que a IA devolveu (`normalizar`), contra os dados reais;
* **como gravar** (`executar`), sempre pelos endpoints de sempre;
* **como contar** ao usuário o que foi feito (`descrever`).

O prompt, a tela do assistente e o bot do Telegram leem daqui. Para ensinar
uma função nova à IA, basta acrescentar uma entrada no CATALOGO.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .. import config
from ..db import todos, um
from ..utils import data_br, data_iso, moeda, numero, texto

FREQ_PARCELA = ("semanal", "quinzenal", "mensal")
FREQ_LOCACAO = ("diaria", "semanal", "quinzenal", "mensal")
FORMATOS_RELATORIO = {"pdf": "relatorio.pdf", "planilha": "pagamentos.xlsx", "csv": "despesas.csv",
                      "arquivos": "arquivos.zip", "texto": "relatorio.txt"}


class Pular(Exception):
    """A ação não pode ser feita; a mensagem vira aviso ao usuário."""


@dataclass
class Contexto:
    """O que a conferência das ações precisa saber."""
    obra_id: int
    referencia: str
    pode_financeiro: bool
    pode_gestor: bool
    arquivos: list = field(default_factory=list)
    _funcionarios: list | None = None

    @property
    def funcionarios(self) -> list[dict]:
        if self._funcionarios is None:
            self._funcionarios = [dict(r) for r in todos(
                "SELECT id, nome, funcao, ativo FROM funcionarios WHERE obra_id = ? ORDER BY nome",
                (self.obra_id,))]
        return self._funcionarios


# ---------------------------------------------------------------- conversões

def sem_acento(valor: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", valor or "")
                   if unicodedata.category(c) != "Mn").lower().strip()


def inteiro(valor) -> int:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return 0


def valor(bruto) -> float:
    try:
        return max(numero(bruto, 0), 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def data_ou(bruto, padrao: str) -> str:
    try:
        return data_iso(bruto, None) or padrao
    except Exception:  # noqa: BLE001 — data inventada pela IA vira a padrão
        return padrao


def categoria(bruto) -> str:
    alvo = sem_acento(str(bruto or ""))
    for c in config.CATEGORIAS_PADRAO:
        if sem_acento(c) == alvo:
            return c
    return "Outro" if alvo else "Material"


def status_pagamento(bruto) -> str:
    alvo = sem_acento(str(bruto or "")).replace("_", " ")
    for s in config.STATUS_PAGAMENTO:
        if sem_acento(s) == alvo:
            return s
    return {"aberto": "Em Aberto", "pendente": "Em Aberto", "pago": "Pago",
            "reembolsar": "Reembolso"}.get(alvo, "")


def indices(lista, arquivos: list, somente_imagem: bool = False) -> list[int]:
    validos = []
    for bruto in lista or []:
        i = inteiro(bruto)
        if 0 <= i < len(arquivos) and i not in validos:
            mime = arquivos[i].get("mime", "")
            if mime.startswith("audio/"):
                continue
            if somente_imagem and not mime.startswith("image/"):
                continue
            validos.append(i)
    return validos


def casar_funcionario(nome: str, funcionarios: list[dict]) -> int:
    """Encontra o funcionário pelo nome falado ("o João" → "João Batista")."""
    alvo = sem_acento(nome)
    if not alvo:
        return 0
    ativos = [f for f in funcionarios if f.get("ativo", 1)]
    exatos = [f["id"] for f in ativos if sem_acento(f["nome"]) == alvo]
    if len(exatos) == 1:
        return exatos[0]
    parciais = [f["id"] for f in ativos
                if sem_acento(f["nome"]).split(" ")[0] == alvo.split(" ")[0]
                or sem_acento(f["nome"]).startswith(alvo)]
    return parciais[0] if len(parciais) == 1 else 0


def _funcionario(d: dict, ctx: Contexto) -> dict:
    fid = inteiro(d.get("funcionario_id"))
    validos = {f["id"]: f for f in ctx.funcionarios}
    if fid not in validos:
        fid = casar_funcionario(texto(d.get("nome"), 120), ctx.funcionarios)
    if not fid:
        raise Pular(f"Funcionário \"{d.get('nome') or d.get('funcionario_id')}\" não encontrado na equipe.")
    return validos[fid]


def _despesa(d: dict, ctx: Contexto) -> dict:
    linha = um("""SELECT x.id, x.descricao, x.fornecedor, x.valor, x.status_pagamento, x.numero_nota,
                         x.vencimento, x.reembolsar_para, x.categoria, d.data
                    FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                   WHERE x.id = ? AND s.obra_id = ?""", (inteiro(d.get("despesa_id")), ctx.obra_id))
    if not linha:
        raise Pular("Um lançamento citado não foi encontrado nesta obra.")
    return dict(linha)


def _locacao(d: dict, ctx: Contexto) -> dict:
    linha = um("SELECT id, equipamento, fornecedor, status FROM locacoes WHERE id = ? AND obra_id = ?",
               (inteiro(d.get("locacao_id")), ctx.obra_id))
    if not linha:
        raise Pular("Locação não encontrada nesta obra.")
    return dict(linha)


# ---------------------------------------------------------------- definição

@dataclass
class TipoAcao:
    nome: str
    titulo: str
    campos: str                       # o que a IA preenche em "dados"
    normalizar: Callable              # (dados, ctx) -> dados conferidos
    executar: Callable                # (executor, dados, arquivos) -> dict | None
    descrever: Callable               # (dados) -> str
    permissao: str = "lancar"         # lancar | financeiro | gestor
    confirmar: bool = False           # pede um toque antes de gravar
    quando: str = ""                  # dica ao modelo de quando usar


CATALOGO: dict[str, TipoAcao] = {}


def _registrar(tipo: TipoAcao) -> None:
    CATALOGO[tipo.nome] = tipo


# ---------------------------------------------------------------- diário

def _n_diario(d, ctx):
    saida = {"data": data_ou(d.get("data"), ctx.referencia),
             "producao_dia": texto(d.get("producao_dia"), 6000),
             "clima": texto(d.get("clima"), 200),
             "observacoes": texto(d.get("observacoes"), 4000)}
    if not (saida["producao_dia"] or saida["clima"] or saida["observacoes"]):
        raise Pular("")
    return saida


_registrar(TipoAcao(
    "diario", "Diário de obra",
    "data, producao_dia (texto técnico COMPLETO do dia, já mesclado com o que existia), clima, observacoes",
    _n_diario,
    lambda ex, d, arq: ex.req("PUT", f"/api/dias/{ex.dia(d['data'])}",
                              json={k: d[k] for k in ("producao_dia", "clima", "observacoes")}),
    lambda d: f"Diário de {data_br(d['data'])} atualizado",
    quando="relato de serviços executados, clima ou ocorrências do dia"))


# ---------------------------------------------------------------- ponto

def _n_ponto(d, ctx):
    registros = []
    for r in d.get("registros") or []:
        if not isinstance(r, dict):
            continue
        nome = texto(r.get("nome"), 120)
        fid = inteiro(r.get("funcionario_id"))
        if fid not in {f["id"] for f in ctx.funcionarios}:
            fid = casar_funcionario(nome, ctx.funcionarios)
        if not fid and not (nome and ctx.pode_gestor):
            continue
        horas = min(max(valor(r.get("horas")), 0), 24)
        registros.append({"funcionario_id": fid,
                          "nome": next((f["nome"] for f in ctx.funcionarios if f["id"] == fid), nome),
                          "horas": horas,
                          "justificativa": "" if horas >= config.JORNADA_HORAS
                          else texto(r.get("justificativa"), 160)})
    if not registros:
        raise Pular("Ninguém da equipe foi reconhecido para o ponto.")
    return {"data": data_ou(d.get("data"), ctx.referencia), "registros": registros}


def _e_ponto(ex, d, arq):
    registros = [{"funcionario_id": r["funcionario_id"] or ex.novos.get(sem_acento(r["nome"]), 0),
                  "horas": r["horas"], "justificativa": r.get("justificativa", "")} for r in d["registros"]]
    registros = [r for r in registros if r["funcionario_id"]]
    if not registros:
        raise ex.Erro("Nenhum funcionário cadastrado para este ponto.")
    ex.req("PUT", f"/api/dias/{ex.dia(d['data'])}/horas", json={"registros": registros})


_registrar(TipoAcao(
    "ponto", "Ponto da equipe",
    "data, registros: [{funcionario_id, nome, horas, justificativa}] — um por pessoa citada. "
    "Jornada completa = jornada_horas; manhã e tarde (ou sem turno dito) = jornada; só um turno = metade; "
    "faltou = 0 horas com justificativa; abaixo da jornada, justificativa com o motivo dito",
    _n_ponto, _e_ponto,
    lambda d: f"Ponto de {data_br(d['data'])}: " + ", ".join(
        f"{r['nome']} {r['horas']:g}h" + (f" ({r['justificativa']})" if r.get("justificativa") else "")
        for r in d["registros"]),
    quando="horas trabalhadas, presença, faltas e atrasos; também quando o relato do dia cita quem trabalhou"))

_registrar(TipoAcao(
    "repetir_ponto", "Repetir ponto",
    "data — copia o ponto do último dia lançado",
    lambda d, ctx: {"data": data_ou(d.get("data"), ctx.referencia)},
    lambda ex, d, arq: ex.req("POST", f"/api/dias/{ex.dia(d['data'])}/horas/repetir"),
    lambda d: f"Ponto de {data_br(d['data'])} copiado do último dia",
    quando="\"mesma equipe de ontem\", \"repete o ponto\""))


# ---------------------------------------------------------------- equipe

def _n_funcionario(d, ctx):
    nome = texto(d.get("nome"), 120)
    if not nome:
        raise Pular("")
    if any(sem_acento(f["nome"]) == sem_acento(nome) and f.get("ativo", 1) for f in ctx.funcionarios):
        raise Pular("")  # já existe: não duplica
    return {"nome": nome, "funcao": texto(d.get("funcao"), 80), "telefone": texto(d.get("telefone"), 40),
            "documento": texto(d.get("documento"), 40), "chave_pix": texto(d.get("chave_pix"), 120)}


def _e_funcionario(ex, d, arq):
    r = ex.req("POST", f"/api/obras/{ex.obra_id}/funcionarios", json=d)
    ex.novos[sem_acento(d["nome"])] = r["id"]


_registrar(TipoAcao(
    "funcionario", "Novo profissional",
    "nome, funcao, telefone, documento, chave_pix",
    _n_funcionario, _e_funcionario,
    lambda d: f"Cadastrado: {d['nome']}" + (f" ({d['funcao']})" if d.get("funcao") else ""),
    permissao="gestor",
    quando="pessoa nova citada que não está na equipe (crie junto com o ponto dela)"))


def _n_editar_funcionario(d, ctx):
    f = _funcionario(d, ctx)
    # "nome" fora de "alteracoes" identifica a pessoa; dentro, é o nome novo.
    origem = d.get("alteracoes") if isinstance(d.get("alteracoes"), dict) else \
        {k: v for k, v in d.items() if k != "nome"}
    alteracoes = {}
    for campo, limite in (("nome", 120), ("funcao", 80), ("telefone", 40), ("documento", 40),
                          ("chave_pix", 120)):
        novo = texto(origem.get(campo), limite)
        if novo and novo != (f.get(campo) or ""):
            alteracoes[campo] = novo
    ativo = origem.get("ativo")
    if ativo in (False, 0, "0", "false", "nao", "não") and f.get("ativo", 1):
        alteracoes["ativo"] = False
    elif ativo in (True, 1, "1", "true", "sim") and not f.get("ativo", 1):
        alteracoes["ativo"] = True
    if not alteracoes:
        raise Pular("")
    return {"funcionario_id": f["id"], "nome_atual": f["nome"], "alteracoes": alteracoes}


_registrar(TipoAcao(
    "editar_funcionario", "Alterar profissional",
    "funcionario_id, alteracoes: {nome, funcao, telefone, documento, chave_pix, ativo (false = desligar)}",
    _n_editar_funcionario,
    lambda ex, d, arq: ex.req("PUT", f"/api/funcionarios/{d['funcionario_id']}", json=d["alteracoes"]),
    lambda d: f"{d['nome_atual']}: " + ("desligado da equipe" if d["alteracoes"].get("ativo") is False else
                                        ", ".join(f"{k.replace('_', ' ')} → {v}" for k, v in d["alteracoes"].items())),
    permissao="gestor",
    quando="mudar função, telefone, PIX, ou desligar alguém da equipe"))


# ---------------------------------------------------------------- fotos

def _n_foto(d, ctx):
    anexos = indices(d.get("anexos"), ctx.arquivos, somente_imagem=True)
    if not anexos:
        raise Pular("")
    return {"data": data_ou(d.get("data"), ctx.referencia), "anexos": anexos,
            "legenda": texto(d.get("legenda"), 160), "ambiente": texto(d.get("ambiente"), 80)}


def _e_foto(ex, d, arq):
    dados = {"fotos": ex.arquivos(d["anexos"], arq)}
    if d.get("legenda"):
        dados["legenda"] = d["legenda"]
    if d.get("ambiente"):
        dados["ambiente"] = d["ambiente"]
    ex.req("POST", f"/api/dias/{ex.dia(d['data'])}/fotos", dados=dados)


_registrar(TipoAcao(
    "foto", "Fotos do andamento",
    "data, anexos (índices das imagens da obra), legenda técnica curta, ambiente (ex.: Fachada, Suíte 1)",
    _n_foto, _e_foto,
    lambda d: f"{len(d['anexos'])} foto(s) no diário de {data_br(d['data'])}"
              + (f" · {d['ambiente']}" if d.get("ambiente") else ""),
    quando="imagens da obra (serviço, ambiente, andamento) — não documentos"))


# ---------------------------------------------------------------- despesas

def _n_despesa(d, ctx):
    status = status_pagamento(d.get("status_pagamento")) or "Em Aberto"
    parcelas = inteiro(d.get("parcelas"))
    frequencia = sem_acento(str(d.get("parcela_frequencia") or ""))
    saida = {
        "data": data_ou(d.get("data"), ctx.referencia),
        "categoria": categoria(d.get("categoria")),
        "descricao": texto(d.get("descricao"), 200),
        "fornecedor": texto(d.get("fornecedor"), 120),
        "valor": valor(d.get("valor")),
        "status_pagamento": status,
        "numero_nota": texto(d.get("numero_nota"), 40),
        "vencimento": data_ou(d.get("vencimento"), ""),
        "reembolsar_para": (texto(d.get("reembolsar_para"), 80) or config.REEMBOLSO_PADRAO)
        if status == "Reembolso" else "",
        "parcelas": parcelas if 2 <= parcelas <= 36 else 0,
        "parcela_frequencia": frequencia if frequencia in FREQ_PARCELA else "mensal",
        "anexos": indices(d.get("anexos"), ctx.arquivos),
    }
    viagens = inteiro(d.get("frete_viagens"))
    if saida["categoria"] == "Frete" and texto(d.get("frete_transportador"), 120) and viagens > 1:
        datas = [data_ou(x, saida["data"]) for x in (d.get("frete_datas") or [])][:viagens]
        saida.update(frete_transportador=texto(d.get("frete_transportador"), 120), frete_viagens=viagens,
                     frete_datas=datas or [saida["data"]])
        saida["parcelas"] = 0
    freq_loc = sem_acento(str(d.get("locacao_frequencia") or ""))
    if saida["categoria"] == "Locação" and freq_loc in FREQ_LOCACAO:
        saida.update(locacao_frequencia=freq_loc, locacao_ciclos=max(inteiro(d.get("locacao_ciclos")), 1),
                     equipamento=texto(d.get("equipamento"), 160) or saida["descricao"])
        saida["parcelas"] = 0
    faltando = [n for n, c in (("descrição", "descricao"), ("valor", "valor")) if not saida[c]]
    if faltando:
        saida["faltando"] = faltando
    return saida


def _e_despesa(ex, d, arq):
    if not d.get("descricao") or not d.get("valor"):
        raise ex.Erro("Faltam descrição ou valor.", "faltando")
    dados = {k: str(d[k]) for k in ("categoria", "descricao", "fornecedor", "valor", "status_pagamento",
                                     "numero_nota", "vencimento", "reembolsar_para") if d.get(k)}
    if d.get("parcelas"):
        dados.update(parcelas=str(d["parcelas"]), parcela_frequencia=d["parcela_frequencia"])
    if d.get("frete_viagens"):
        dados.update(frete_transportador=d["frete_transportador"], frete_viagens=str(d["frete_viagens"]),
                     frete_datas=d["frete_datas"])
    if d.get("locacao_frequencia"):
        dados.update(frequencia=d["locacao_frequencia"], ciclos=str(d["locacao_ciclos"]),
                     equipamento=d["equipamento"], data_locacao=d["data"])
    if d.get("confirmar_duplicidade"):
        dados["confirmar_duplicidade"] = "1"
    dados["anexos"] = ex.arquivos(d.get("anexos"), arq)
    ex.req("POST", f"/api/dias/{ex.dia(d['data'])}/despesas", dados=dados)


def _d_despesa(d):
    partes = [d.get("descricao") or "Despesa", moeda(d.get("valor"))]
    for chave, prefixo in (("fornecedor", ""), ("numero_nota", "nº ")):
        if d.get(chave):
            partes.append(prefixo + d[chave])
    partes.append(d.get("status_pagamento", ""))
    if d.get("parcelas"):
        partes.append(f"{d['parcelas']}x {d['parcela_frequencia']}")
    if d.get("frete_viagens"):
        partes.append(f"{d['frete_viagens']} viagens")
    if d.get("locacao_frequencia"):
        partes.append(f"aluguel {d['locacao_frequencia']} × {d['locacao_ciclos']}")
    if d.get("anexos"):
        partes.append("📎")
    return f"Despesa em {data_br(d['data'])}: " + " · ".join(p for p in partes if p)


_registrar(TipoAcao(
    "despesa", "Despesa",
    "data, categoria (uma de \"categorias\"), descricao (itens com quantidade + o que o usuário disse), "
    "fornecedor, valor (TOTAL final), status_pagamento (Pago | Em Aberto | Reembolso), numero_nota, "
    "vencimento, reembolsar_para, anexos (índices dos comprovantes), parcelas + parcela_frequencia "
    "(nota parcelada; valor = total), frete_transportador + frete_viagens + frete_datas (frete com várias "
    "viagens; valor = de CADA viagem), locacao_frequencia (diaria|semanal|quinzenal|mensal) + "
    "locacao_ciclos + equipamento (aluguel recorrente)",
    _n_despesa, _e_despesa, _d_despesa, permissao="financeiro",
    quando="compra, serviço, frete, aluguel, nota, cupom, recibo, comprovante de PIX"))


def _n_editar_despesa(d, ctx):
    atual = _despesa(d, ctx)
    origem = d.get("alteracoes") if isinstance(d.get("alteracoes"), dict) else d
    alteracoes = {}
    for campo, limite in (("descricao", 200), ("fornecedor", 120), ("numero_nota", 40),
                          ("reembolsar_para", 80)):
        novo = texto(origem.get(campo), limite)
        if novo and novo != atual.get(campo):
            alteracoes[campo] = novo
    if texto(origem.get("categoria"), 40) and categoria(origem.get("categoria")) != atual["categoria"]:
        alteracoes["categoria"] = categoria(origem.get("categoria"))
    if valor(origem.get("valor")) and valor(origem.get("valor")) != atual["valor"]:
        alteracoes["valor"] = valor(origem.get("valor"))
    novo_status = status_pagamento(origem.get("status_pagamento"))
    if novo_status and novo_status != atual["status_pagamento"]:
        alteracoes["status_pagamento"] = novo_status
        if novo_status == "Reembolso" and "reembolsar_para" not in alteracoes:
            alteracoes["reembolsar_para"] = atual.get("reembolsar_para") or config.REEMBOLSO_PADRAO
    if data_ou(origem.get("vencimento"), ""):
        alteracoes["vencimento"] = data_ou(origem.get("vencimento"), "")
    mover = data_ou(origem.get("mover_para_data") or d.get("mover_para_data")
                    or origem.get("data_lancamento"), "")
    anexos = indices(d.get("anexos") or origem.get("anexos"), ctx.arquivos)
    if not alteracoes and not anexos and not mover:
        raise Pular("")
    return {"despesa_id": atual["id"], "despesa_atual": atual, "alteracoes": alteracoes,
            "mover_para_data": mover if mover and mover != atual["data"] else "", "anexos": anexos}


def _e_editar_despesa(ex, d, arq):
    alteracoes = dict(d.get("alteracoes") or {})
    if d.get("mover_para_data"):
        alteracoes["dia_id"] = ex.dia(d["mover_para_data"])
    if alteracoes:
        if d.get("confirmar_duplicidade"):
            alteracoes["confirmar_duplicidade"] = True
        ex.req("PUT", f"/api/despesas/{d['despesa_id']}", json=alteracoes)
    anexos = ex.arquivos(d.get("anexos"), arq)
    if anexos:
        ex.req("POST", f"/api/despesas/{d['despesa_id']}/anexos", dados={"anexos": anexos})


def _d_editar_despesa(d):
    atual = d["despesa_atual"]
    mudou = [f"{k.replace('_', ' ')} → {moeda(v) if k == 'valor' else v}" for k, v in d["alteracoes"].items()]
    if d.get("mover_para_data"):
        mudou.append(f"movido para {data_br(d['mover_para_data'])}")
    if d.get("anexos"):
        mudou.append("comprovante anexado")
    return f"Corrigido \"{atual['descricao']}\": " + ", ".join(mudou)


_registrar(TipoAcao(
    "editar_despesa", "Corrigir lançamento",
    "despesa_id, alteracoes: {descricao, fornecedor, valor, categoria, status_pagamento, reembolsar_para, "
    "numero_nota, vencimento, mover_para_data} — só o que muda; anexos (comprovante a acrescentar)",
    _n_editar_despesa, _e_editar_despesa, _d_editar_despesa, permissao="financeiro",
    quando="correção de algo já lançado, mudar status (em aberto/pago/reembolso), mudar de dia, "
           "anexar comprovante a um lançamento existente, mesma nota já lançada"))


def _n_pagar(d, ctx):
    atual = _despesa(d, ctx)
    if atual["status_pagamento"] == "Pago":
        raise Pular(f"\"{atual['descricao']}\" já estava paga.")
    return {"despesa_id": atual["id"], "despesa_atual": atual,
            "pago_em": data_ou(d.get("pago_em") or d.get("data"), ctx.referencia),
            "anexos": indices(d.get("anexos"), ctx.arquivos)}


def _e_pagar(ex, d, arq):
    ex.req("POST", f"/api/despesas/{d['despesa_id']}/pagar", json={"pago_em": d["pago_em"]})
    anexos = ex.arquivos(d.get("anexos"), arq)
    if anexos:
        ex.req("POST", f"/api/despesas/{d['despesa_id']}/anexos", dados={"anexos": anexos})


_registrar(TipoAcao(
    "pagar_despesa", "Baixa de pagamento",
    "despesa_id (de contas_em_aberto), pago_em, anexos (comprovante)",
    _n_pagar, _e_pagar,
    lambda d: f"Baixa de \"{d['despesa_atual']['descricao']}\" ({moeda(d['despesa_atual']['valor'])}) "
              f"em {data_br(d['pago_em'])}" + (" · 📎" if d.get("anexos") else ""),
    permissao="financeiro",
    quando="\"paguei a nota X\", \"quitei o boleto\", comprovante de PIX de uma conta em aberto"))


def _n_excluir(d, ctx):
    atual = _despesa(d, ctx)
    return {"despesa_id": atual["id"], "despesa_atual": atual}


_registrar(TipoAcao(
    "excluir_despesa", "Excluir lançamento", "despesa_id",
    _n_excluir,
    lambda ex, d, arq: ex.req("DELETE", f"/api/despesas/{d['despesa_id']}"),
    lambda d: f"Excluir \"{d['despesa_atual']['descricao']}\" ({moeda(d['despesa_atual']['valor'])})",
    permissao="financeiro", confirmar=True,
    quando="somente quando o usuário pedir claramente para apagar"))


def _n_semana_data(d, ctx):
    return {"data": data_ou(d.get("data"), ctx.referencia),
            "incluir_reembolsos": bool(d.get("incluir_reembolsos"))}


_registrar(TipoAcao(
    "pagar_semana", "Baixa de todas as contas da semana",
    "data (qualquer dia da semana), incluir_reembolsos (true/false)",
    _n_semana_data,
    lambda ex, d, arq: ex.req("POST", f"/api/semanas/{ex.semana(d['data'])}/despesas/pagar-todas",
                              json={"incluir_reembolsos": d["incluir_reembolsos"]}),
    lambda d: f"Baixa das contas em aberto da semana de {data_br(d['data'])}"
              + (" (com reembolsos)" if d["incluir_reembolsos"] else ""),
    permissao="financeiro", confirmar=True,
    quando="\"paguei tudo da semana\", \"quita as notas da semana\""))


# ---------------------------------------------------------------- semana e folha

def _n_semana(d, ctx):
    saida = {"data": data_ou(d.get("data"), ctx.referencia)}
    if texto(d.get("meta"), 400):
        saida["meta"] = texto(d.get("meta"), 400)
    if valor(d.get("orcamento_previsto")) and ctx.pode_gestor:
        saida["orcamento_previsto"] = valor(d.get("orcamento_previsto"))
    if d.get("meta_atingida") in (True, False):
        saida["meta_atingida"] = d["meta_atingida"]
    if len(saida) == 1:
        raise Pular("")
    return saida


_registrar(TipoAcao(
    "semana", "Semana",
    "data (qualquer dia da semana), meta (texto), orcamento_previsto, meta_atingida (true/false)",
    _n_semana,
    lambda ex, d, arq: ex.req("PUT", f"/api/semanas/{ex.semana(d['data'])}",
                              json={k: v for k, v in d.items() if k != "data"}),
    lambda d: f"Semana de {data_br(d['data'])}: " + ", ".join(
        {"meta": f"meta \"{d.get('meta', '')}\"",
         "orcamento_previsto": f"orçamento {moeda(d.get('orcamento_previsto'))}",
         "meta_atingida": "meta atingida" if d.get("meta_atingida") else "meta não atingida"}[k]
        for k in d if k != "data"),
    quando="meta da semana, orçamento previsto da semana"))

_registrar(TipoAcao(
    "fechar_semana", "Fechar semana", "data (qualquer dia da semana)",
    lambda d, ctx: {"data": data_ou(d.get("data"), ctx.referencia)},
    lambda ex, d, arq: ex.req("PUT", f"/api/semanas/{ex.semana(d['data'])}", json={"fechada": True}),
    lambda d: f"Fechar a semana de {data_br(d['data'])} (a folha é quitada junto)",
    permissao="gestor", confirmar=True, quando="\"fecha a semana\""))

_registrar(TipoAcao(
    "pagar_folha", "Pagar folha da semana", "data (qualquer dia da semana)",
    lambda d, ctx: {"data": data_ou(d.get("data"), ctx.referencia)},
    lambda ex, d, arq: ex.req("POST", f"/api/semanas/{ex.semana(d['data'])}/folha/pagar"),
    lambda d: f"Folha da semana de {data_br(d['data'])} quitada",
    permissao="financeiro", confirmar=True, quando="\"paguei a folha\", \"paguei a equipe\""))


# ---------------------------------------------------------------- locações

def _n_loc_lancar(d, ctx):
    linha = um("""SELECT p.id, p.numero, p.vencimento, p.valor, p.status, l.equipamento
                    FROM locacao_parcelas p JOIN locacoes l ON l.id = p.locacao_id
                   WHERE p.id = ? AND l.obra_id = ?""", (inteiro(d.get("parcela_id")), ctx.obra_id))
    if not linha or linha["status"] == "lancada":
        raise Pular("Renovação de aluguel não encontrada ou já lançada.")
    return {"parcela_id": linha["id"], "equipamento": linha["equipamento"], "numero": linha["numero"],
            "vencimento": linha["vencimento"], "valor": valor(d.get("valor")) or float(linha["valor"] or 0),
            "status_pagamento": status_pagamento(d.get("status_pagamento")) or "Em Aberto"}


_registrar(TipoAcao(
    "locacao_lancar", "Lançar renovação de aluguel",
    "parcela_id (proxima_parcela de locacoes_ativas), valor, status_pagamento",
    _n_loc_lancar,
    lambda ex, d, arq: ex.req("POST", f"/api/locacoes/parcelas/{d['parcela_id']}/lancar",
                              json={"valor": d["valor"], "status_pagamento": d["status_pagamento"]}),
    lambda d: f"Aluguel {d['equipamento']} · renovação {d['numero']} · {moeda(d['valor'])} · {d['status_pagamento']}",
    permissao="financeiro", quando="\"renovou o aluguel do andaime\", \"paguei a locação\""))

_registrar(TipoAcao(
    "locacao_encerrar", "Encerrar aluguel", "locacao_id",
    lambda d, ctx: {"locacao_id": _locacao(d, ctx)["id"], "equipamento": _locacao(d, ctx)["equipamento"]},
    lambda ex, d, arq: ex.req("POST", f"/api/locacoes/{d['locacao_id']}/encerrar"),
    lambda d: f"Encerrar aluguel de {d['equipamento']} (cancela as renovações futuras)",
    permissao="financeiro", confirmar=True, quando="\"devolvi o andaime\", \"acabou o aluguel\""))

_registrar(TipoAcao(
    "locacao_prorrogar", "Prorrogar aluguel", "locacao_id, ciclos (quantos períodos a mais)",
    lambda d, ctx: {"locacao_id": _locacao(d, ctx)["id"], "equipamento": _locacao(d, ctx)["equipamento"],
                    "ciclos": max(inteiro(d.get("ciclos")), 1)},
    lambda ex, d, arq: ex.req("POST", f"/api/locacoes/{d['locacao_id']}/prorrogar", json={"ciclos": d["ciclos"]}),
    lambda d: f"Aluguel de {d['equipamento']} prorrogado por {d['ciclos']} período(s)",
    permissao="financeiro", quando="\"vou ficar mais um mês com a betoneira\""))


# ---------------------------------------------------------------- relatórios

def _n_relatorio(d, ctx):
    formato = sem_acento(str(d.get("formato") or "pdf"))
    formato = {"xlsx": "planilha", "excel": "planilha", "zip": "arquivos", "txt": "texto"}.get(formato, formato)
    if formato not in FORMATOS_RELATORIO:
        formato = "pdf"
    if formato in ("planilha", "csv") and not ctx.pode_financeiro:
        raise Pular("Seu perfil não acessa os relatórios financeiros.")
    return {"data": data_ou(d.get("data"), ctx.referencia), "formato": formato}


_registrar(TipoAcao(
    "relatorio", "Relatório da semana",
    "data (qualquer dia da semana), formato: pdf (relatório semanal com RDO e fotos) | planilha "
    "(pagamentos .xlsx) | csv | arquivos (zip com notas e fotos) | texto",
    _n_relatorio,
    lambda ex, d, arq: {"link": f"/api/semanas/{ex.semana(d['data'])}/{FORMATOS_RELATORIO[d['formato']]}"},
    lambda d: f"Relatório ({d['formato']}) da semana de {data_br(d['data'])}",
    quando="\"me manda o relatório\", \"gera o PDF da semana\", \"planilha de pagamentos\""))


# ---------------------------------------------------------------- obra

def _n_obra(d, ctx):
    origem = d.get("alteracoes") if isinstance(d.get("alteracoes"), dict) else d
    alteracoes = {}
    for campo, limite in (("nome", 140), ("cliente", 140), ("endereco", 220)):
        if texto(origem.get(campo), limite):
            alteracoes[campo] = texto(origem.get(campo), limite)
    if valor(origem.get("orcamento_total")):
        alteracoes["orcamento_total"] = valor(origem.get("orcamento_total"))
    for campo in ("data_inicio", "previsao_fim"):
        if data_ou(origem.get(campo), ""):
            alteracoes[campo] = data_ou(origem.get(campo), "")
    estado = sem_acento(str(origem.get("status") or ""))
    estado = {"paralisada": "pausada", "parada": "pausada", "concluída": "concluida"}.get(estado, estado)
    if estado in ("ativa", "pausada", "concluida"):
        alteracoes["status"] = estado
    if not alteracoes:
        raise Pular("")
    return {"alteracoes": alteracoes}


_registrar(TipoAcao(
    "editar_obra", "Dados da obra",
    "alteracoes: {nome, cliente, endereco, orcamento_total, data_inicio, previsao_fim, status (ativa|pausada|concluida)}",
    _n_obra,
    lambda ex, d, arq: ex.req("PUT", f"/api/obras/{ex.obra_id}", json=d["alteracoes"]),
    lambda d: "Obra atualizada: " + ", ".join(
        f"{k.replace('_', ' ')} → {moeda(v) if k == 'orcamento_total' else v}" for k, v in d["alteracoes"].items()),
    permissao="gestor", quando="orçamento total, previsão de término, obra parada ou concluída"))


# ---------------------------------------------------------------- esquema dos dados
#
# Os campos de "dados" precisam estar DECLARADOS no esquema enviado ao Gemini.
# Com saída estruturada ele só gera as propriedades listadas: um objeto sem
# propriedades voltava sempre vazio ({}), e o lançamento saía "R$ 0,00, faltou
# descrição e valor" mesmo com a IA tendo lido o total da nota.

_S, _N, _I, _B = {"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}
_ALTERACOES = {k: _S for k in ("descricao", "fornecedor", "categoria", "status_pagamento", "reembolsar_para",
                               "numero_nota", "vencimento", "mover_para_data", "nome", "funcao", "telefone",
                               "documento", "chave_pix", "cliente", "endereco", "data_inicio", "previsao_fim",
                               "status")}
_ALTERACOES.update(valor=_N, orcamento_total=_N, ativo=_B)
ESQUEMA_DADOS = {
    "type": "object",
    "properties": {
        **{k: _S for k in ("data", "producao_dia", "clima", "observacoes", "nome", "funcao", "telefone",
                           "documento", "chave_pix", "legenda", "ambiente", "categoria", "descricao",
                           "fornecedor", "status_pagamento", "numero_nota", "vencimento", "reembolsar_para",
                           "parcela_frequencia", "frete_transportador", "locacao_frequencia", "equipamento",
                           "pago_em", "meta", "formato", "mover_para_data")},
        **{k: _I for k in ("funcionario_id", "parcelas", "frete_viagens", "locacao_ciclos", "despesa_id",
                           "parcela_id", "locacao_id", "ciclos")},
        "valor": _N, "orcamento_previsto": _N,
        "ativo": _B, "incluir_reembolsos": _B, "meta_atingida": _B,
        "anexos": {"type": "array", "items": _I},
        "frete_datas": {"type": "array", "items": _S},
        "registros": {"type": "array", "items": {"type": "object", "properties": {
            "funcionario_id": _I, "nome": _S, "horas": _N, "justificativa": _S}}},
        "alteracoes": {"type": "object", "properties": _ALTERACOES},
    },
}

# Nomes que o modelo às vezes usa no lugar dos nossos.
APELIDOS = {"valor_total": "valor", "total": "valor", "value": "valor", "amount": "valor", "preco": "valor",
            "description": "descricao", "supplier": "fornecedor", "vendor": "fornecedor", "loja": "fornecedor",
            "nota": "numero_nota", "nf": "numero_nota", "numero": "numero_nota", "date": "data",
            "attachments": "anexos", "arquivos": "anexos"}
APELIDOS_CAIXA = {"status": "status_pagamento", "situacao": "status_pagamento"}


def _juntar_dados(item: dict, tipo: str) -> dict:
    """Junta "dados" com campos soltos no item e traduz apelidos (o que está em "dados" vale mais)."""
    soltos = {k: v for k, v in item.items() if k not in ("tipo", "resumo", "obra_id", "dados")}
    dados = {**soltos, **(item.get("dados") if isinstance(item.get("dados"), dict) else {})}
    apelidos = {**APELIDOS, **(APELIDOS_CAIXA if tipo in ("despesa", "locacao_lancar") else {})}
    for apelido, campo in apelidos.items():
        if apelido in dados and dados.get(campo) in (None, "", 0, []):
            dados[campo] = dados[apelido]
    return dados


# ---------------------------------------------------------------- uso

def permitido(tipo: TipoAcao, ctx: Contexto) -> bool:
    return (tipo.permissao == "lancar"
            or (tipo.permissao == "financeiro" and ctx.pode_financeiro)
            or (tipo.permissao == "gestor" and ctx.pode_gestor))


def texto_para_prompt(ctx_financeiro: bool, ctx_gestor: bool) -> str:
    linhas = []
    for t in CATALOGO.values():
        if t.permissao == "financeiro" and not ctx_financeiro:
            continue
        if t.permissao == "gestor" and not ctx_gestor:
            continue
        aviso = " [pede confirmação do usuário]" if t.confirmar else ""
        linhas.append(f"- {t.nome}: {t.quando}.{aviso}\n    dados: {t.campos}")
    return "\n".join(linhas)


def normalizar(bruto_acoes, ctx: Contexto) -> tuple[list[dict], list[str]]:
    """Confere cada ação contra os dados reais e o perfil. Devolve (ações, avisos)."""
    acoes, avisos = [], []
    for item in bruto_acoes or []:
        if not isinstance(item, dict):
            continue
        tipo = CATALOGO.get(str(item.get("tipo") or "").strip())
        if not tipo:
            continue
        if not permitido(tipo, ctx):
            avisos.append(f"{tipo.titulo}: seu perfil não tem permissão.")
            continue
        dados = _juntar_dados(item, tipo.nome)
        if tipo.nome == "despesa" and not texto(dados.get("descricao"), 200):
            # Sem descrição, aproveita o resumo da IA ou o fornecedor: só o valor é indispensável.
            dados["descricao"] = texto(item.get("resumo"), 200) or (
                f"Compra em {texto(dados.get('fornecedor'), 120)}" if texto(dados.get("fornecedor"), 120) else "")
        try:
            conferido = tipo.normalizar(dados, ctx)
        except Pular as aviso:
            if str(aviso):
                avisos.append(str(aviso))
            continue
        acoes.append({"tipo": tipo.nome, "titulo": tipo.titulo, "confirmar": tipo.confirmar,
                      "dados": conferido, "descricao": tipo.descrever(conferido),
                      "resumo": texto(item.get("resumo"), 240)})
    return acoes, list(dict.fromkeys(avisos))


def descrever(acao: dict) -> str:
    tipo = CATALOGO.get(acao.get("tipo"))
    try:
        return tipo.descrever(acao["dados"]) if tipo else acao.get("tipo", "")
    except Exception:  # noqa: BLE001
        return tipo.titulo if tipo else ""


def hoje_semana(data_str: str) -> str:
    return config.DIAS_SEMANA[date.fromisoformat(data_str).weekday()]
