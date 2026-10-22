# -*- coding: utf-8 -*-
"""Assistente de IA da obra — da fala (ou do arquivo) ao lançamento.

O gestor fala como falaria com um colega: "hoje o João e o Pedro fizeram 8
horas, concretamos a laje, comprei cimento na Casa do Construtor, nota 4521,
R$ 1.380, paguei do meu bolso". Anexa a foto da nota e a foto da laje. O
assistente devolve uma lista de **ações** já no formato do app:

* ``diario``           texto técnico do RDO (produção, clima, observações)
* ``ponto``            horas e justificativas por funcionário
* ``funcionario``      profissional novo que apareceu na fala
* ``despesa``          lançamento no caixa (com nota, vencimento, parcelas, anexos)
* ``editar_despesa``   correção de um lançamento já feito
* ``pagar_despesa``    baixa de uma conta em aberto
* ``excluir_despesa``  remoção (sempre pede um toque de confirmação na tela)
* ``foto``             foto do andamento anexada ao diário

Este módulo **não grava nada**. Ele monta o contexto que a IA precisa (equipe,
contas em aberto, lançamentos recentes), pede a resposta e passa um pente-fino
em tudo que volta: data inválida, funcionário que não existe, lançamento de
outra obra e permissão do usuário. A gravação é feita pela tela, pelos mesmos
endpoints do formulário — com as mesmas travas (nota duplicada, mestre sem
acesso ao caixa, auditoria).
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date, timedelta

from .. import config
from ..db import todos, um
from ..utils import data_iso, hoje_iso, numero, texto
from . import ia_provedores

TIPOS = ("diario", "ponto", "funcionario", "despesa", "editar_despesa", "pagar_despesa",
         "excluir_despesa", "foto")
TIPOS_FINANCEIROS = {"despesa", "editar_despesa", "pagar_despesa", "excluir_despesa"}
FREQUENCIAS = ("semanal", "quinzenal", "mensal")
MAX_HISTORICO = 8

_STR = {"type": "string"}
_NUM = {"type": "number"}
_INT = {"type": "integer"}

# Todo campo é obrigatório e "vazio" (texto "" / número 0) significa "não se
# aplica". Assim o mesmo esquema serve ao Gemini e ao modo estrito do Claude.
ESQUEMA = {
    "type": "object",
    "properties": {
        "resposta": _STR,
        "acoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": list(TIPOS)},
                    "data": _STR,
                    "resumo": _STR,
                    "despesa_id": _INT,
                    "producao_dia": _STR,
                    "clima": _STR,
                    "observacoes": _STR,
                    "registros": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"funcionario_id": _INT, "nome": _STR, "horas": _NUM,
                                           "justificativa": _STR},
                            "required": ["funcionario_id", "nome", "horas", "justificativa"],
                            "additionalProperties": False,
                        },
                    },
                    "categoria": _STR,
                    "descricao": _STR,
                    "fornecedor": _STR,
                    "valor": _NUM,
                    "status_pagamento": _STR,
                    "numero_nota": _STR,
                    "vencimento": _STR,
                    "reembolsar_para": _STR,
                    "parcelas": _INT,
                    "parcela_frequencia": _STR,
                    "anexos": {"type": "array", "items": _INT},
                    "legenda": _STR,
                    "ambiente": _STR,
                    "nome": _STR,
                    "funcao": _STR,
                },
                "required": ["tipo", "data", "resumo", "despesa_id", "producao_dia", "clima",
                             "observacoes", "registros", "categoria", "descricao", "fornecedor",
                             "valor", "status_pagamento", "numero_nota", "vencimento",
                             "reembolsar_para", "parcelas", "parcela_frequencia", "anexos",
                             "legenda", "ambiente", "nome", "funcao"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["resposta", "acoes"],
    "additionalProperties": False,
}

PROMPT_SISTEMA = """Você é o assistente de um aplicativo de gestão de obras (diário de obra, ponto da equipe e caixa).
O usuário fala de forma informal, como numa conversa, e anexa fotos e notas fiscais. Seu trabalho é
transformar isso em AÇÕES prontas para gravar no aplicativo. Responda SOMENTE com um objeto JSON no
formato descrito no fim.

REGRAS GERAIS
- Português do Brasil. Nunca invente valor, número de nota, fornecedor, data ou nome. Se algo
  obrigatório faltar e não estiver em nenhum anexo, deixe vazio (texto "" ou número 0) e pergunte
  na "resposta".
- Datas no formato AAAA-MM-DD. "Hoje" é a data de referência informada no contexto; "ontem",
  "segunda", "dia 12" etc. são relativos a ela. Sem data dita, use a data de referência.
- Use os IDs que aparecem no contexto (funcionários, lançamentos). Não crie IDs.
- Uma ação por coisa a gravar. Pode haver várias ações de tipos diferentes na mesma resposta.
- Em cada ação, "resumo" é uma frase curta dizendo o que será gravado.
- Campos que não se aplicam ao tipo da ação ficam vazios ("" / 0 / []).

DIÁRIO DE OBRA (tipo "diario")
- Redija em linguagem técnica de engenharia civil, impessoal (ex.: "Executada a concretagem da laje
  do 2º pavimento, volume aproximado de 12 m³, com lançamento por bomba e adensamento mecânico.").
- producao_dia: serviços executados, quantidades, locais/ambientes, frentes de trabalho.
- clima: condição do tempo (ex.: "Ensolarado pela manhã, chuva forte à tarde"), só se foi dito.
- observacoes: ocorrências, interferências, visitas, pendências, segurança, entregas de material.
- Se o diário do dia já tem texto no contexto, devolva o texto COMPLETO já mesclado com o novo,
  sem perder o que existia.
- Gere um "diario" sempre que o usuário relatar serviços executados ou ocorrências do dia.

PONTO (tipo "ponto")
- registros: um por pessoa citada, com funcionario_id do contexto e as horas do dia.
- Jornada completa = valor de "jornada_horas" do contexto. "Dia todo"/"o dia inteiro" = jornada;
  "meio período"/"meio turno" = metade. Faltou = 0 horas com justificativa.
- Abaixo da jornada, preencha "justificativa" com o motivo dito (ex.: "Consulta médica").
- "Todo mundo"/"a equipe toda" = todos os funcionários ativos do contexto.
- Pessoa que NÃO está na lista: use funcionario_id 0 e o nome em "nome", e crie também uma ação
  "funcionario" (nome e função) para cadastrá-la.

CAIXA (tipos de despesa) — somente se "pode_financeiro" for true no contexto
- despesa: categoria (uma de "categorias"), descricao objetiva do que foi comprado/contratado,
  fornecedor, valor TOTAL em reais, numero_nota (só os dígitos/identificador da NF), status_pagamento
  e vencimento.
- status_pagamento: "Pago" se já foi pago pela empresa/obra; "Reembolso" se alguém pagou do próprio
  bolso e precisa ser reembolsado (preencha reembolsar_para com o nome; se for o próprio usuário e não
  houver nome, use o "reembolso_padrao" do contexto); "Em Aberto" se ainda vai ser pago (boleto, a
  prazo, fatura) — nesse caso preencha vencimento se souber.
- Nota parcelada: valor = total da nota, parcelas = quantidade (2 a 36), parcela_frequencia =
  "mensal" | "quinzenal" | "semanal", vencimento = data da 1ª parcela.
- Lendo nota fiscal/cupom/boleto anexado: extraia valor total, número da nota, fornecedor (nome
  fantasia se houver, senão razão social), itens principais para a descrição, data de emissão
  (use como "data" se o usuário não disser outra) e vencimento/parcelas do boleto.
- Coloque em "anexos" os índices (começando em 0) dos arquivos que são o comprovante daquela despesa.
- Se a mesma nota (mesmo número e fornecedor) já está nos lançamentos do contexto, NÃO crie outra:
  use "editar_despesa" com o despesa_id e somente os campos que mudam (os demais vazios/0).
- Correções ("na verdade foram 1.500", "muda o fornecedor") de algo já lançado = "editar_despesa".
- "Paguei a nota X"/"quitei o boleto do Y" de uma conta em aberto = "pagar_despesa" com despesa_id;
  "data" = dia do pagamento.
- "excluir_despesa" somente quando o usuário pedir claramente para apagar um lançamento.

FOTOS (tipo "foto")
- Imagens da obra (serviço, ambiente, andamento) — não documentos — viram uma ação "foto" com os
  índices em "anexos", uma legenda técnica curta e o ambiente (ex.: "Fachada", "Suíte 1").

RESPOSTA
- "resposta": 1 a 4 frases ao usuário dizendo o que você preparou e perguntando o que faltou.
  Sem markdown.

FORMATO (JSON):
{"resposta": "...", "acoes": [{"tipo": "diario|ponto|funcionario|despesa|editar_despesa|pagar_despesa|excluir_despesa|foto",
 "data": "AAAA-MM-DD", "resumo": "...", "despesa_id": 0, "producao_dia": "", "clima": "", "observacoes": "",
 "registros": [{"funcionario_id": 0, "nome": "", "horas": 0, "justificativa": ""}], "categoria": "",
 "descricao": "", "fornecedor": "", "valor": 0, "status_pagamento": "", "numero_nota": "", "vencimento": "",
 "reembolsar_para": "", "parcelas": 0, "parcela_frequencia": "", "anexos": [], "legenda": "", "ambiente": "",
 "nome": "", "funcao": ""}]}
"""

PROMPT_RDO = """Você é engenheiro civil e redige o Relatório Diário de Obra (RDO).
Com base SOMENTE nos dados do dia informados, escreva em português do Brasil, linguagem técnica e
impessoal, sem inventar serviços que não estejam nos dados. Responda só com JSON:
{"producao_dia": "...", "clima": "...", "observacoes": "..."}
- producao_dia: serviços executados, efetivo presente, materiais recebidos, quantidades e locais.
- clima: mantenha o que já estiver informado; se não houver informação, "".
- observacoes: faltas e justificativas, ocorrências, pendências e pontos de atenção.
Se já houver texto nos campos, aproveite e melhore a redação sem perder informação."""

ESQUEMA_RDO = {
    "type": "object",
    "properties": {"producao_dia": _STR, "clima": _STR, "observacoes": _STR},
    "required": ["producao_dia", "clima", "observacoes"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------- utilidades

def _sem_acento(valor: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", valor or "")
                   if unicodedata.category(c) != "Mn").lower().strip()


def _data_ou(valor, padrao: str) -> str:
    try:
        return data_iso(valor, None) or padrao
    except Exception:  # noqa: BLE001 — data inventada pela IA vira a de referência
        return padrao


def _inteiro(valor) -> int:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return 0


def _valor(valor) -> float:
    try:
        return max(numero(valor, 0), 0.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _dia_semana(data_str: str) -> str:
    return config.DIAS_SEMANA[date.fromisoformat(data_str).weekday()]


# ---------------------------------------------------------------- contexto

def _funcionarios(obra_id: int) -> list[dict]:
    return [dict(r) for r in todos(
        """SELECT id, nome, funcao FROM funcionarios
            WHERE obra_id = ? AND ativo = 1 ORDER BY nome""", (obra_id,))]


def _dia_por_data(obra_id: int, data_str: str):
    return um("""SELECT d.id, d.producao_dia, d.clima, d.observacoes
                   FROM dias d JOIN semanas s ON s.id = d.semana_id
                  WHERE s.obra_id = ? AND d.data = ?""", (obra_id, data_str))


def _despesas_contexto(obra_id: int, referencia: str) -> tuple[list[dict], list[dict]]:
    inicio = (date.fromisoformat(referencia) - timedelta(days=21)).isoformat()
    recentes = [dict(r) for r in todos(
        """SELECT x.id, d.data, x.categoria, x.descricao, x.fornecedor, x.valor,
                  x.status_pagamento, x.numero_nota, x.vencimento
             FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND d.data >= ?
            ORDER BY d.data DESC, x.id DESC LIMIT 60""", (obra_id, inicio))]
    abertas = [dict(r) for r in todos(
        """SELECT x.id, d.data, x.descricao, x.fornecedor, x.valor, x.numero_nota, x.vencimento
             FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
            WHERE s.obra_id = ? AND x.status_pagamento = 'Em Aberto'
            ORDER BY COALESCE(x.vencimento, d.data) LIMIT 60""", (obra_id,))]
    return recentes, abertas


def montar_contexto(obra_id: int, referencia: str, pode_financeiro: bool, pode_cadastrar: bool) -> dict:
    obra = um("SELECT nome, cliente, endereco FROM obras WHERE id = ?", (obra_id,))
    funcionarios = _funcionarios(obra_id)
    dia = _dia_por_data(obra_id, referencia)

    ponto = {}
    if dia:
        for r in todos("""SELECT h.funcionario_id, h.horas_trabalhadas, h.justificativa
                            FROM horas h WHERE h.dia_id = ?""", (dia["id"],)):
            ponto[str(r["funcionario_id"])] = {"horas": r["horas_trabalhadas"],
                                               "justificativa": r["justificativa"] or ""}

    contexto = {
        "obra": dict(obra) if obra else {},
        "data_referencia": referencia,
        "dia_da_semana": _dia_semana(referencia),
        "hoje": hoje_iso(),
        "jornada_horas": config.JORNADA_HORAS,
        "funcionarios_ativos": funcionarios,
        "ponto_ja_lancado_no_dia": ponto,
        "diario_ja_escrito_no_dia": {
            "producao_dia": (dia["producao_dia"] if dia else "") or "",
            "clima": (dia["clima"] if dia else "") or "",
            "observacoes": (dia["observacoes"] if dia else "") or "",
        },
        "pode_financeiro": pode_financeiro,
        "pode_cadastrar_funcionario": pode_cadastrar,
    }
    if pode_financeiro:
        recentes, abertas = _despesas_contexto(obra_id, referencia)
        fornecedores = [r["fornecedor"] for r in todos(
            """SELECT DISTINCT x.fornecedor FROM despesas x
                 JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? AND x.fornecedor <> '' ORDER BY x.fornecedor LIMIT 80""",
            (obra_id,))]
        contexto.update({
            "categorias": config.CATEGORIAS_PADRAO,
            "status_pagamento": config.STATUS_PAGAMENTO,
            "reembolso_padrao": config.REEMBOLSO_PADRAO,
            "lancamentos_recentes": recentes,
            "contas_em_aberto": abertas,
            "fornecedores_conhecidos": fornecedores,
        })
    return contexto


# ---------------------------------------------------------------- pedido

def _texto_pedido(mensagem: str, contexto: dict, arquivos: list[dict], historico: list[dict]) -> str:
    partes = ["CONTEXTO DO APLICATIVO (JSON):", json.dumps(contexto, ensure_ascii=False, default=str)]
    if historico:
        partes.append("\nCONVERSA ANTERIOR (mais antiga primeiro):")
        for item in historico[-MAX_HISTORICO:]:
            quem = "Usuário" if item.get("papel") == "usuario" else "Assistente"
            partes.append(f"{quem}: {texto(item.get('texto'), 1500)}")
    if arquivos:
        partes.append("\nARQUIVOS ANEXADOS (na ordem dos índices):")
        for i, a in enumerate(arquivos):
            partes.append(f"[{i}] {a['nome']} ({a['mime']})")
    partes.append("\nMENSAGEM DO USUÁRIO:")
    partes.append(mensagem or "(sem texto — analise os anexos e proponha os lançamentos)")
    return "\n".join(partes)


def interpretar(obra_id: int, mensagem: str, referencia: str | None, arquivos: list[dict],
                historico: list[dict], pode_financeiro: bool, pode_cadastrar: bool) -> dict:
    referencia = _data_ou(referencia, hoje_iso())
    contexto = montar_contexto(obra_id, referencia, pode_financeiro, pode_cadastrar)

    bruto, fonte = ia_provedores.gerar(
        PROMPT_SISTEMA, _texto_pedido(mensagem, contexto, arquivos, historico),
        arquivos, ESQUEMA, max_tokens=8192)

    resultado = normalizar(bruto, obra_id, referencia, arquivos, pode_financeiro, pode_cadastrar)
    resultado["fonte"] = fonte
    resultado["data_referencia"] = referencia
    return resultado


# ---------------------------------------------------------------- pente-fino

def _casar_funcionario(nome: str, funcionarios: list[dict]) -> int:
    """Encontra o funcionário pelo nome falado ("o João" → "João Batista")."""
    alvo = _sem_acento(nome)
    if not alvo:
        return 0
    exatos = [f["id"] for f in funcionarios if _sem_acento(f["nome"]) == alvo]
    if len(exatos) == 1:
        return exatos[0]
    parciais = [f["id"] for f in funcionarios
                if _sem_acento(f["nome"]).split(" ")[0] == alvo.split(" ")[0]
                or _sem_acento(f["nome"]).startswith(alvo)]
    return parciais[0] if len(parciais) == 1 else 0


def _despesa_da_obra(obra_id: int, despesa_id: int):
    if not despesa_id:
        return None
    linha = um("""SELECT x.id, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                        x.numero_nota, x.vencimento, x.reembolsar_para, d.data
                   FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                  WHERE x.id = ? AND s.obra_id = ?""", (despesa_id, obra_id))
    return dict(linha) if linha else None


def _categoria(valor: str) -> str:
    alvo = _sem_acento(valor)
    for c in config.CATEGORIAS_PADRAO:
        if _sem_acento(c) == alvo:
            return c
    return "Outro" if alvo else "Material"


def _status(valor: str) -> str:
    alvo = _sem_acento(valor)
    for s in config.STATUS_PAGAMENTO:
        if _sem_acento(s) == alvo:
            return s
    return ""


def _indices(lista, arquivos: list[dict], somente_imagem: bool = False) -> list[int]:
    validos = []
    for bruto in lista or []:
        i = _inteiro(bruto)
        if 0 <= i < len(arquivos) and i not in validos:
            if somente_imagem and not arquivos[i]["mime"].startswith("image/"):
                continue
            validos.append(i)
    return validos


def normalizar(bruto, obra_id: int, referencia: str, arquivos: list[dict],
               pode_financeiro: bool, pode_cadastrar: bool) -> dict:
    """Confere cada ação devolvida pela IA contra os dados reais da obra."""
    if not isinstance(bruto, dict):
        bruto = {}
    funcionarios = _funcionarios(obra_id)
    ids_validos = {f["id"] for f in funcionarios}
    nomes = {f["id"]: f["nome"] for f in funcionarios}
    acoes, avisos = [], []

    for item in bruto.get("acoes") or []:
        if not isinstance(item, dict):
            continue
        tipo = str(item.get("tipo") or "").strip()
        if tipo not in TIPOS:
            continue
        if tipo in TIPOS_FINANCEIROS and not pode_financeiro:
            avisos.append("Lançamentos de caixa ficaram de fora: seu perfil não acessa o financeiro.")
            continue

        data_acao = _data_ou(item.get("data"), referencia)
        acao = {"tipo": tipo, "data": data_acao, "resumo": texto(item.get("resumo"), 240)}

        if tipo == "diario":
            acao.update({"producao_dia": texto(item.get("producao_dia"), 6000),
                         "clima": texto(item.get("clima"), 200),
                         "observacoes": texto(item.get("observacoes"), 4000)})
            if not (acao["producao_dia"] or acao["clima"] or acao["observacoes"]):
                continue

        elif tipo == "ponto":
            registros = []
            for r in item.get("registros") or []:
                if not isinstance(r, dict):
                    continue
                fid = _inteiro(r.get("funcionario_id"))
                nome = texto(r.get("nome"), 120)
                if fid not in ids_validos:
                    fid = _casar_funcionario(nome, funcionarios)
                if not fid and not (nome and pode_cadastrar):
                    if nome:
                        avisos.append(f"{nome} não está cadastrado na equipe e ficou fora do ponto.")
                    continue
                horas = min(max(_valor(r.get("horas")), 0), 24)
                registros.append({"funcionario_id": fid, "nome": nomes.get(fid, nome), "horas": horas,
                                  "justificativa": "" if horas >= config.JORNADA_HORAS
                                  else texto(r.get("justificativa"), 160)})
            if not registros:
                continue
            acao["registros"] = registros

        elif tipo == "funcionario":
            nome = texto(item.get("nome"), 120)
            if not nome:
                continue
            if not pode_cadastrar:
                avisos.append(f"Só o gestor cadastra profissionais — {nome} não foi incluído.")
                continue
            if _casar_funcionario(nome, funcionarios) and \
                    any(_sem_acento(f["nome"]) == _sem_acento(nome) for f in funcionarios):
                continue  # já existe: não duplica o cadastro
            acao.update({"nome": nome, "funcao": texto(item.get("funcao"), 80)})

        elif tipo == "despesa":
            status = _status(item.get("status_pagamento")) or "Em Aberto"
            parcelas = _inteiro(item.get("parcelas"))
            frequencia = str(item.get("parcela_frequencia") or "").strip().lower()
            acao.update({
                "categoria": _categoria(item.get("categoria")),
                "descricao": texto(item.get("descricao"), 200),
                "fornecedor": texto(item.get("fornecedor"), 120),
                "valor": _valor(item.get("valor")),
                "status_pagamento": status,
                "numero_nota": texto(item.get("numero_nota"), 40),
                "vencimento": _data_ou(item.get("vencimento"), ""),
                "reembolsar_para": (texto(item.get("reembolsar_para"), 80) or config.REEMBOLSO_PADRAO)
                if status == "Reembolso" else "",
                "parcelas": parcelas if 2 <= parcelas <= 36 else 0,
                "parcela_frequencia": frequencia if frequencia in FREQUENCIAS else "mensal",
                "anexos": _indices(item.get("anexos"), arquivos),
            })
            faltando = [nome for nome, campo in (("descrição", "descricao"), ("valor", "valor"))
                        if not acao[campo]]
            if faltando:
                acao["faltando"] = faltando

        elif tipo in ("editar_despesa", "pagar_despesa", "excluir_despesa"):
            atual = _despesa_da_obra(obra_id, _inteiro(item.get("despesa_id")))
            if not atual:
                avisos.append("Um lançamento citado não foi encontrado nesta obra.")
                continue
            acao["despesa_id"] = atual["id"]
            acao["despesa_atual"] = {k: atual[k] for k in
                                     ("descricao", "fornecedor", "valor", "status_pagamento",
                                      "numero_nota", "vencimento", "data")}
            if tipo == "editar_despesa":
                alteracoes = {}
                for campo, limite in (("descricao", 200), ("fornecedor", 120),
                                      ("numero_nota", 40), ("reembolsar_para", 80)):
                    valor = texto(item.get(campo), limite)
                    if valor and valor != atual.get(campo):
                        alteracoes[campo] = valor
                if texto(item.get("categoria"), 40):
                    alteracoes["categoria"] = _categoria(item.get("categoria"))
                if _valor(item.get("valor")):
                    alteracoes["valor"] = _valor(item.get("valor"))
                if _status(item.get("status_pagamento")):
                    alteracoes["status_pagamento"] = _status(item.get("status_pagamento"))
                if _data_ou(item.get("vencimento"), ""):
                    alteracoes["vencimento"] = _data_ou(item.get("vencimento"), "")
                anexos = _indices(item.get("anexos"), arquivos)
                if not alteracoes and not anexos:
                    continue
                acao["alteracoes"] = alteracoes
                acao["anexos"] = anexos
            elif tipo == "pagar_despesa":
                if atual["status_pagamento"] == "Pago":
                    avisos.append(f"\"{atual['descricao']}\" já estava paga.")
                    continue
                acao["pago_em"] = data_acao
            else:
                acao["requer_confirmacao"] = True

        elif tipo == "foto":
            indices = _indices(item.get("anexos"), arquivos, somente_imagem=True)
            if not indices:
                continue
            acao.update({"anexos": indices, "legenda": texto(item.get("legenda"), 160),
                         "ambiente": texto(item.get("ambiente"), 80)})

        acoes.append(acao)

    usados = {i for a in acoes for i in a.get("anexos", [])}
    for i, a in enumerate(arquivos):
        if i not in usados:
            avisos.append(f"O arquivo \"{a['nome']}\" não foi usado em nenhum lançamento.")

    return {"resposta": texto(bruto.get("resposta"), 1500)
            or ("Preparei os lançamentos abaixo." if acoes else "Não encontrei nada para lançar."),
            "acoes": acoes, "avisos": list(dict.fromkeys(avisos))}


# ---------------------------------------------------------------- RDO do dia

def redigir_rdo(dia_id: int, pode_financeiro: bool) -> dict:
    """Redige o RDO a partir do que já foi lançado no dia (ponto, fotos, caixa, notas)."""
    dia = um("""SELECT d.id, d.data, d.dia_semana, d.producao_dia, d.clima, d.observacoes,
                       o.nome AS obra
                  FROM dias d JOIN semanas s ON s.id = d.semana_id JOIN obras o ON o.id = s.obra_id
                 WHERE d.id = ?""", (dia_id,))
    ponto = [dict(r) for r in todos(
        """SELECT f.nome, f.funcao, h.horas_trabalhadas AS horas, h.justificativa
             FROM horas h JOIN funcionarios f ON f.id = h.funcionario_id
            WHERE h.dia_id = ? ORDER BY f.nome""", (dia_id,))]
    fotos = [dict(r) for r in todos(
        "SELECT legenda, ambiente FROM fotos WHERE dia_id = ? ORDER BY id", (dia_id,))]
    dados = {
        "obra": dia["obra"], "data": dia["data"], "dia_da_semana": dia["dia_semana"],
        "jornada_horas": config.JORNADA_HORAS,
        "texto_atual": {"producao_dia": dia["producao_dia"] or "", "clima": dia["clima"] or "",
                        "observacoes": dia["observacoes"] or ""},
        "ponto": ponto,
        "fotos": fotos,
    }
    if pode_financeiro:
        dados["compras_e_servicos"] = [dict(r) for r in todos(
            """SELECT categoria, descricao, fornecedor FROM despesas
                WHERE dia_id = ? ORDER BY id""", (dia_id,))]

    bruto, fonte = ia_provedores.gerar(
        PROMPT_RDO, "DADOS DO DIA (JSON):\n" + json.dumps(dados, ensure_ascii=False, default=str),
        [], ESQUEMA_RDO, max_tokens=4096)
    bruto = bruto if isinstance(bruto, dict) else {}
    return {"producao_dia": texto(bruto.get("producao_dia"), 6000),
            "clima": texto(bruto.get("clima"), 200),
            "observacoes": texto(bruto.get("observacoes"), 4000),
            "fonte": fonte}
