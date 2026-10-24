# -*- coding: utf-8 -*-
"""Assistente de IA da obra — da fala (ou do arquivo) ao lançamento.

O gestor fala como falaria com um colega e anexa fotos, notas, comprovantes
de PIX e áudios. Uma única chamada à IA:

1. transcreve os áudios;
2. descobre de qual obra se trata (pelo nome, cliente ou endereço citado);
3. lê os documentos e monta as **ações** do catálogo (`services/acoes.py`),
   que cobre tudo que o app faz: diário, ponto, equipe, fotos, despesas,
   frete, aluguel, semana, folha, relatórios e dados da obra;
4. responde perguntas ("quanto gastei essa semana?") com os dados do contexto.

Este módulo **não grava nada**: confere as ações contra os dados reais e o
perfil do usuário. A gravação passa pelo `Executor`, que usa os endpoints de
sempre — com as mesmas travas dos formulários.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from .. import config
from ..db import todos, um
from ..utils import hoje_iso, somar_dias, texto
from . import acoes as catalogo
from . import ia_provedores

MAX_HISTORICO = 8
MAX_OBRAS_DETALHADAS = 3

ESQUEMA = {
    "type": "object",
    "properties": {
        "transcricao": {"type": "string"},
        "obra_id": {"type": "integer"},
        "resposta": {"type": "string"},
        "acoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": list(catalogo.CATALOGO)},
                    "obra_id": {"type": "integer"},
                    "resumo": {"type": "string"},
                    "dados": {"type": "object"},
                },
                "required": ["tipo", "dados"],
            },
        },
    },
    "required": ["resposta", "acoes"],
}

PROMPT_BASE = """Você é o assistente de um aplicativo de gestão de obras e tem controle total sobre ele.
O usuário fala de forma informal (texto ou áudio) e manda fotos, notas fiscais, cupons, orçamentos,
boletos e comprovantes de PIX. Transforme isso em AÇÕES do aplicativo e responda SOMENTE com JSON.

REGRAS GERAIS
- Português do Brasil. Nunca invente valor, número de nota, fornecedor, data ou nome.
- Datas AAAA-MM-DD. "Hoje" = data_referencia do contexto; "ontem", "sábado", "dia 12" são relativos a ela.
  Sem data dita, use data_referencia.
- Use os IDs do contexto (obras, funcionários, lançamentos, locações). Não invente IDs.
- Uma ação por coisa a gravar; pode haver várias ações de tipos diferentes.
- Áudios: transcreva fielmente em "transcricao" (corrija só pontuação e termos de obra mal ouvidos)
  e trate o conteúdo como a mensagem do usuário. Áudio nunca vai em "anexos".
- "obra_id": a obra de que a mensagem trata (pelo nome, apelido, cliente ou endereço citado, ou o
  endereço impresso num documento). Se não der para saber e houver mais de uma obra, use 0.
- Perguntas ("quanto gastei?", "o que vence essa semana?", "quem faltou?"): responda em "resposta"
  com os números do contexto, sem criar ações.

DOCUMENTOS (nota, cupom, orçamento, recibo, boleto, PIX)
- O VALOR vem SEMPRE do documento: o TOTAL final a pagar (depois de descontos), nunca subtotal,
  preço unitário ou valor de um item. Leia com atenção foto torta, amassada ou com dedo na frente.
  Só deixe valor 0 se não houver documento ou o total estiver ilegível — e diga isso na resposta.
- Fornecedor: nome fantasia se houver, senão razão social. Número: o da NF; se não for nota fiscal
  (orçamento, pedido, "não é documento fiscal", recibo), o número dele com o tipo na frente
  (ex.: "ORC 31797"). PIX: o ID da transação (E2E) e o recebedor como fornecedor.
- Status pela forma de pagamento impressa: dinheiro, PIX, débito, crédito à vista = "Pago" (salvo
  se o usuário disser que pagou do bolso = "Reembolso"); boleto, a prazo, faturado = "Em Aberto".
- Data: a que o usuário disser; senão a de emissão do documento.
- Descrição: itens com quantidade e unidade + o que o usuário disse (finalidade).
- Mesma nota (número e fornecedor) já nos lançamentos do contexto, ou PIX de uma conta em aberto
  igual: NÃO crie outra — use editar_despesa (anexando o comprovante) ou pagar_despesa.

DIÁRIO DE OBRA
- Linguagem técnica de engenharia, impessoal ("Executada a concretagem da laje do 2º pavimento...").
- Relato por funcionário: escreva por pessoa/frente, com quantidade, unidade e turno
  ("Ademir: execução de 27 m² de reboco interno (manhã); arestamento de vãos (tarde)."). "Metros" de
  serviço de área (reboco, chapisco, alvenaria, contrapiso, pintura, revestimento, forro) = m².
- Se o diário do dia já tem texto, devolva o texto COMPLETO mesclado, sem perder o que existia.
- Relato do dia que cita quem trabalhou gera também o ponto.

FOTOS
- Imagens da obra (não documentos) viram "foto" no dia indicado ("relatório fotográfico de ontem").

AÇÕES DISPONÍVEIS (tipo: quando usar / campos de "dados")
{catalogo}

FORMATO (JSON):
{{"transcricao": "", "obra_id": 0, "resposta": "1 a 4 frases ao usuário, sem markdown",
 "acoes": [{{"tipo": "...", "obra_id": 0, "resumo": "frase curta", "dados": {{...}}}}]}}
Em "dados", inclua só os campos que se aplicam."""

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
    "properties": {"producao_dia": {"type": "string"}, "clima": {"type": "string"},
                   "observacoes": {"type": "string"}},
    "required": ["producao_dia", "clima", "observacoes"],
}


# ---------------------------------------------------------------- contexto

def _semana_da_data(obra_id: int, referencia: str):
    linha = um("""SELECT s.id, s.numero, s.data_inicio, s.meta, s.orcamento_previsto, s.fechada, s.folha_paga
                    FROM dias d JOIN semanas s ON s.id = d.semana_id
                   WHERE s.obra_id = ? AND d.data = ?""", (obra_id, referencia))
    if not linha:
        return None
    semana = dict(linha)
    semana["data_fim"] = somar_dias(semana["data_inicio"], 6) if semana["data_inicio"] else None
    return semana


def _contexto_obra(obra: dict, referencia: str, pode_financeiro: bool) -> dict:
    obra_id = obra["id"]
    ctx = {k: obra.get(k) for k in ("id", "nome", "cliente", "endereco")}
    ctx["funcionarios_ativos"] = [dict(r) for r in todos(
        "SELECT id, nome, funcao FROM funcionarios WHERE obra_id = ? AND ativo = 1 ORDER BY nome", (obra_id,))]

    dia = um("""SELECT d.id, d.producao_dia, d.clima, d.observacoes FROM dias d
                  JOIN semanas s ON s.id = d.semana_id WHERE s.obra_id = ? AND d.data = ?""",
             (obra_id, referencia))
    ctx["dia_referencia"] = None
    if dia:
        ctx["dia_referencia"] = {
            "diario_ja_escrito": {k: dia[k] or "" for k in ("producao_dia", "clima", "observacoes")},
            "ponto_ja_lancado": [dict(r) for r in todos(
                """SELECT f.nome, h.horas_trabalhadas AS horas, h.justificativa FROM horas h
                     JOIN funcionarios f ON f.id = h.funcionario_id WHERE h.dia_id = ?""", (dia["id"],))],
            "fotos": um("SELECT COUNT(*) AS n FROM fotos WHERE dia_id = ?", (dia["id"],))["n"],
        }

    semana = _semana_da_data(obra_id, referencia)
    ctx["semana_atual"] = semana
    if semana:
        horas = um("""SELECT COALESCE(SUM(h.horas_trabalhadas), 0) AS h FROM horas h
                        JOIN dias d ON d.id = h.dia_id WHERE d.semana_id = ?""", (semana["id"],))["h"]
        semana["horas_lancadas"] = round(float(horas or 0), 1)

    if pode_financeiro:
        inicio = (date.fromisoformat(referencia) - timedelta(days=21)).isoformat()
        ctx["lancamentos_recentes"] = [dict(r) for r in todos(
            """SELECT x.id, d.data, x.categoria, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                      x.numero_nota, x.vencimento
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? AND d.data >= ? ORDER BY d.data DESC, x.id DESC LIMIT 30""",
            (obra_id, inicio))]
        ctx["contas_em_aberto"] = [dict(r) for r in todos(
            """SELECT x.id, d.data, x.descricao, x.fornecedor, x.valor, x.numero_nota, x.vencimento
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? AND x.status_pagamento = 'Em Aberto'
                ORDER BY COALESCE(x.vencimento, d.data) LIMIT 30""", (obra_id,))]
        if semana:
            total = um("""SELECT COALESCE(SUM(x.valor), 0) AS t,
                                 COALESCE(SUM(CASE WHEN x.status_pagamento = 'Em Aberto' THEN x.valor ELSE 0 END), 0) AS a
                            FROM despesas x JOIN dias d ON d.id = x.dia_id WHERE d.semana_id = ?""", (semana["id"],))
            semana["gasto_da_semana"] = round(float(total["t"] or 0), 2)
            semana["em_aberto_da_semana"] = round(float(total["a"] or 0), 2)
        from .locacoes import listar_locacoes
        ctx["locacoes_ativas"] = [
            {"id": l["id"], "equipamento": l["equipamento"], "fornecedor": l["fornecedor"],
             "frequencia": l["frequencia"], "valor_parcela": l["valor_parcela"],
             "proxima_parcela": {k: l["proxima_parcela"][k] for k in ("id", "numero", "vencimento", "valor")}
             if l.get("proxima_parcela") else None}
            for l in listar_locacoes(obra_id, incluir_encerradas=False)]
    return ctx


def _obras_citadas(texto_msg: str, obras: list[dict]) -> list[int]:
    alvo = f" {catalogo.sem_acento(texto_msg)} "
    return [o["id"] for o in obras if len(catalogo.sem_acento(o["nome"])) >= 2
            and re.search(rf"(?<!\w){re.escape(catalogo.sem_acento(o['nome']))}(?!\w)", alvo)]


def montar_contexto(obras: list[dict], referencia: str, pode_financeiro: bool, pode_gestor: bool,
                    foco: list[int]) -> dict:
    detalhadas = (foco + [o["id"] for o in obras if o["id"] not in foco])[:MAX_OBRAS_DETALHADAS]
    return {
        "data_referencia": referencia,
        "dia_da_semana": catalogo.hoje_semana(referencia),
        "jornada_horas": config.JORNADA_HORAS,
        "pode_financeiro": pode_financeiro,
        "pode_gestor": pode_gestor,
        "categorias": config.CATEGORIAS_PADRAO,
        "reembolso_padrao": config.REEMBOLSO_PADRAO,
        "obras": [_contexto_obra(o, referencia, pode_financeiro) if o["id"] in detalhadas
                  else {k: o.get(k) for k in ("id", "nome", "cliente", "endereco")} for o in obras],
    }


def _texto_pedido(mensagem: str, contexto: dict, arquivos: list[dict], historico: list[dict]) -> str:
    partes = ["CONTEXTO (JSON):", json.dumps(contexto, ensure_ascii=False, default=str)]
    if historico:
        partes.append("\nCONVERSA ANTERIOR (mais antiga primeiro):")
        for item in historico[-MAX_HISTORICO:]:
            quem = "Usuário" if item.get("papel") == "usuario" else "Assistente"
            partes.append(f"{quem}: {texto(item.get('texto'), 1500)}")
    if arquivos:
        partes.append("\nARQUIVOS (índice: nome e tipo):")
        for i, a in enumerate(arquivos):
            partes.append(f"[{i}] {a['nome']} ({a['mime']})")
    partes.append("\nMENSAGEM DO USUÁRIO:")
    partes.append(mensagem or "(sem texto — ouça os áudios e analise os anexos)")
    return "\n".join(partes)


# ---------------------------------------------------------------- pedido

def interpretar(obras: list[dict], mensagem: str, referencia: str | None, arquivos: list[dict],
                historico: list[dict], pode_financeiro: bool, pode_gestor: bool,
                obra_padrao: int = 0, prazo: float = ia_provedores.PRAZO_PADRAO) -> dict:
    """Uma ida à IA: transcreve, identifica a obra e devolve as ações já conferidas.

    `obras`: as obras que o usuário pode usar (na tela, só a obra aberta).
    """
    referencia = catalogo.data_ou(referencia, hoje_iso())
    ids = {o["id"] for o in obras}
    foco = _obras_citadas(mensagem, obras) or ([obra_padrao] if obra_padrao in ids else [])
    contexto = montar_contexto(obras, referencia, pode_financeiro, pode_gestor, foco)
    sistema = PROMPT_BASE.replace("{catalogo}", catalogo.texto_para_prompt(pode_financeiro, pode_gestor)) \
                         .replace("{{", "{").replace("}}", "}")

    bruto, fonte = ia_provedores.gerar(
        sistema, _texto_pedido(mensagem, contexto, arquivos, historico), arquivos, ESQUEMA,
        max_tokens=8192, pensar="baixo", prazo=prazo)
    bruto = bruto if isinstance(bruto, dict) else {}

    obra_id = catalogo.inteiro(bruto.get("obra_id"))
    if obra_id not in ids:
        obra_id = foco[0] if len(foco) == 1 else (obras[0]["id"] if len(obras) == 1 else 0)

    acoes_brutas = [a for a in (bruto.get("acoes") or []) if isinstance(a, dict)]
    por_obra: dict[int, list] = {}
    for acao in acoes_brutas:
        alvo = catalogo.inteiro(acao.get("obra_id"))
        por_obra.setdefault(alvo if alvo in ids else obra_id, []).append(acao)

    resultado = {"transcricao": texto(bruto.get("transcricao"), 6000), "obra_id": obra_id,
                 "resposta": texto(bruto.get("resposta"), 2000), "acoes": [], "avisos": [],
                 "fonte": fonte, "modelo": ia_provedores.modelo_usado(), "data_referencia": referencia,
                 "precisa_obra": False, "acoes_sem_obra": []}
    for alvo, lista in por_obra.items():
        if not alvo:
            resultado["precisa_obra"] = True
            resultado["acoes_sem_obra"] = lista
            continue
        ctx = catalogo.Contexto(alvo, referencia, pode_financeiro, pode_gestor, arquivos)
        conferidas, avisos = catalogo.normalizar(lista, ctx)
        for a in conferidas:
            a["obra_id"] = alvo
        resultado["acoes"] += conferidas
        resultado["avisos"] += avisos

    usados = {i for a in resultado["acoes"] for i in a["dados"].get("anexos", [])}
    for i, a in enumerate(arquivos):
        if i not in usados and not a["mime"].startswith("audio/") and not resultado["precisa_obra"]:
            resultado["avisos"].append(f"O arquivo \"{a['nome']}\" não foi usado em nenhum lançamento.")
    if not resultado["resposta"]:
        resultado["resposta"] = "Preparei os lançamentos abaixo." if resultado["acoes"] else \
            "Não encontrei nada para lançar."
    return resultado


def reconferir(obra_id: int, acoes: list[dict], arquivos: list[dict], pode_financeiro: bool,
               pode_gestor: bool, referencia: str | None = None) -> tuple[list[dict], list[str]]:
    """Confere de novo ações que voltaram da tela (o navegador não é confiável)."""
    ctx = catalogo.Contexto(obra_id, catalogo.data_ou(referencia, hoje_iso()), pode_financeiro,
                            pode_gestor, arquivos)
    brutas = [{"tipo": a.get("tipo"), "dados": a.get("dados") or {}} for a in acoes if isinstance(a, dict)]
    conferidas, avisos = catalogo.normalizar(brutas, ctx)
    # Leva junto o "lançar mesmo assim" de cada uma, na mesma ordem.
    marcas = [a for a in acoes if isinstance(a, dict) and a.get("tipo") in catalogo.CATALOGO]
    for conferida, original in zip(conferidas, marcas):
        conferida["confirmar_duplicidade"] = bool(original.get("confirmar_duplicidade"))
        conferida["obra_id"] = obra_id
    return conferidas, avisos


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
        [], ESQUEMA_RDO, max_tokens=4096, pensar="minimo")
    bruto = bruto if isinstance(bruto, dict) else {}
    return {"producao_dia": texto(bruto.get("producao_dia"), 6000),
            "clima": texto(bruto.get("clima"), 200),
            "observacoes": texto(bruto.get("observacoes"), 4000),
            "fonte": fonte}
