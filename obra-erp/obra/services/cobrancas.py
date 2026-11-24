# -*- coding: utf-8 -*-
"""Cobrança que se repete todo mês até alguém dizer que acabou.

"Esse boleto vai se repetir todo mês": o vencimento cai sempre no mesmo dia do
mês e, como qualquer boleto, é pago na sexta até o vencimento. Cada cobrança
tem um número (COB 3), que é como se encerra: "a cobrança 3 não vai ter mais".

O lançamento de cada mês só nasce quando a semana em que ele é pago existe. Até
lá a cobrança é só uma previsão na aba Futuros: criar meses à frente inflaria o
total da obra com contas que podem nunca chegar.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, timedelta

from ..db import executar, todos, um
from ..utils import ApiErro, agora_iso, data_iso, hoje_iso
from .parcelamento import dia_de_pagar, sql_fora_da_semana

log = logging.getLogger("obra-erp")
MESES_PREVISTOS = 6


def proximo_vencimento(depois_de: date, dia: int) -> date:
    """O mesmo dia no mês seguinte ao de `depois_de` (31 vira o último dia do mês curto)."""
    ano, mes = depois_de.year + depois_de.month // 12, depois_de.month % 12 + 1
    ultimo = ((date(ano + mes // 12, mes % 12 + 1, 1)) - timedelta(days=1)).day
    return date(ano, mes, min(dia, ultimo))


def sem_periodo(descricao: str) -> str:
    """"Locação Mensal (22/10 a 21/11) 50 Escoras" → sem o período, que é só daquele mês."""
    limpo = re.sub(r"\s*\([^)]*\d{1,2}/\d{1,2}[^)]*\)\s*", " ", descricao or "").strip()
    return limpo or descricao or "Cobrança"


def _obra_da_despesa(despesa_id: int) -> dict | None:
    linha = um("""SELECT x.*, s.obra_id FROM despesas x JOIN dias d ON d.id = x.dia_id
                    JOIN semanas s ON s.id = d.semana_id WHERE x.id = ?""", (despesa_id,))
    return dict(linha) if linha else None


def repetir(despesa_id: int, vencimento: str | None = None) -> dict:
    """Passa a repetir um lançamento todo mês. O mês dele já está lançado: começa no seguinte."""
    x = _obra_da_despesa(despesa_id)
    if not x:
        raise ApiErro("Lançamento não encontrado.", 404)
    if x.get("cobranca_id"):
        ativa = um("SELECT status FROM cobrancas WHERE id = ?", (x["cobranca_id"],))
        if ativa and ativa["status"] == "ativa":
            raise ApiErro(f"Este lançamento já se repete: é a cobrança {x['cobranca_id']}.", 409)
    venc = data_iso(vencimento, None, "o vencimento") or str(x["vencimento"] or "")[:10]
    if not venc:
        raise ApiErro("Para repetir todo mês, diga em que dia o boleto vence.")
    # Marcado num lançamento antigo: os meses que já passaram não viram conta de uma vez.
    dia, marca = date.fromisoformat(venc).day, date.fromisoformat(venc)
    hoje = date.fromisoformat(hoje_iso())
    while dia_de_pagar(proximo_vencimento(marca, dia)) < hoje - timedelta(days=hoje.weekday()):
        marca = proximo_vencimento(marca, dia)
    cobranca_id = executar(
        """INSERT INTO cobrancas (obra_id, descricao, fornecedor, categoria, valor, dia, lancado_ate, status,
                                  despesa_id, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'ativa', ?, ?)""",
        (x["obra_id"], sem_periodo(x["descricao"]), x["fornecedor"] or "", x["categoria"], x["valor"],
         dia, marca.isoformat(), despesa_id, agora_iso())).lastrowid
    executar("UPDATE despesas SET cobranca_id = ?, vencimento = COALESCE(NULLIF(vencimento, ''), ?) WHERE id = ?",
             (cobranca_id, venc, despesa_id))
    gerar(x["obra_id"])
    return um_por_id(cobranca_id)


def um_por_id(cobranca_id: int) -> dict:
    linha = um("SELECT * FROM cobrancas WHERE id = ?", (cobranca_id,))
    if not linha:
        raise ApiErro("Cobrança não encontrada.", 404)
    c = dict(linha)
    venc = proximo_vencimento(date.fromisoformat(c["lancado_ate"]), c["dia"])
    c.update(proximo=venc.isoformat(), pagar_em=dia_de_pagar(venc).isoformat())
    return c


def listar(obra_id: int, encerradas: bool = False) -> list[dict]:
    filtro = "" if encerradas else " AND status = 'ativa'"
    return [um_por_id(l["id"]) for l in todos(
        f"SELECT id FROM cobrancas WHERE obra_id = ?{filtro} ORDER BY status, id", (obra_id,))]


def encerrar(cobranca_id: int) -> dict:
    """Não vem mais. O que já foi lançado fica: só os próximos meses deixam de aparecer."""
    c = um_por_id(cobranca_id)
    if c["status"] != "ativa":
        raise ApiErro(f"A cobrança {cobranca_id} já está encerrada.")
    executar("UPDATE cobrancas SET status = 'encerrada', encerrada_em = ? WHERE id = ?", (hoje_iso(), cobranca_id))
    # O que já nasceu para os próximos dias não é apagado sozinho, mas o gestor precisa saber.
    c["em_aberto"] = [l["id"] for l in todos(
        """SELECT id FROM despesas WHERE cobranca_id = ? AND status_pagamento = 'Em Aberto'
            AND COALESCE(pagar_em, vencimento) > ? ORDER BY vencimento""", (cobranca_id, hoje_iso()))]
    return c


def _criar_lancamento(c: dict, vencimento: date, dia_id: int, pagar: date) -> int:
    return executar(
        """INSERT INTO despesas (dia_id, categoria, descricao, fornecedor, valor, status_pagamento, numero_nota,
                                 grupo_parcela, grupo_tipo, parcela_numero, parcela_total, vencimento, pagar_em,
                                 vence_na_semana, imagem_path, nome_arquivo, cobranca_id, criado_em)
           VALUES (?, ?, ?, ?, ?, 'Em Aberto', '', ?, 'parcela', 1, 1, ?, ?, 1, '', '', ?, ?)""",
        (dia_id, c["categoria"], c["descricao"], c["fornecedor"], c["valor"], uuid.uuid4().hex,
         vencimento.isoformat(), pagar.isoformat(), c["id"], agora_iso())).lastrowid


def gerar(obra_id: int | None = None) -> int:
    """Lança as cobranças cuja semana de pagamento já existe. Devolve quantas."""
    filtro = "" if obra_id is None else " AND obra_id = ?"
    criadas = 0
    for linha in todos(f"SELECT * FROM cobrancas WHERE status = 'ativa'{filtro}",
                       () if obra_id is None else (obra_id,)):
        c = dict(linha)
        for _ in range(36):
            venc = proximo_vencimento(date.fromisoformat(c["lancado_ate"]), c["dia"])
            pagar = dia_de_pagar(venc)
            dia = um("""SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
                         WHERE s.obra_id = ? AND d.data = ?""", (c["obra_id"], pagar.isoformat()))
            if not dia and pagar.isoformat() < hoje_iso():
                # A sexta já passou e a semana dela nunca existiu: entra hoje, para não sumir.
                dia = um("""SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
                             WHERE s.obra_id = ? AND d.data = ?""", (c["obra_id"], hoje_iso()))
            if not dia:
                break
            # A marca é trocada junto com o teste do valor antigo: duas chamadas ao mesmo
            # tempo não lançam o mesmo mês duas vezes.
            marcou = executar("UPDATE cobrancas SET lancado_ate = ? WHERE id = ? AND lancado_ate = ?",
                              (venc.isoformat(), c["id"], c["lancado_ate"]))
            if not marcou.rowcount:
                break
            _criar_lancamento(c, venc, dia["id"], pagar)
            c["lancado_ate"] = venc.isoformat()
            criadas += 1
    if criadas:
        log.info("Cobranças mensais: %s lançamento(s) criado(s)", criadas)
    return criadas


def do_mes(obra_id: int, fornecedor: str, valor: float, vencimento: str) -> dict:
    """O boleto do mês de uma cobrança que já se repete?

    {"livre": cobrança ainda sem o lançamento deste mês} ou
    {"lancado": despesa que a cobrança já gerou}; {} se não é de nenhuma.
    """
    from .acoes import sem_acento
    alvo = date.fromisoformat(vencimento)
    lancado = None
    for linha in todos("SELECT * FROM cobrancas WHERE obra_id = ? AND status = 'ativa' ORDER BY id", (obra_id,)):
        c = dict(linha)
        if sem_acento(c["fornecedor"]) != sem_acento(fornecedor) or abs(float(c["valor"]) - valor) > 0.01:
            continue
        esperado = proximo_vencimento((alvo.replace(day=1) - timedelta(days=1)), c["dia"])
        if abs((esperado - alvo).days) > 3:
            continue
        existente = um("SELECT id, descricao FROM despesas WHERE cobranca_id = ? AND substr(vencimento, 1, 7) = ?",
                       (c["id"], vencimento[:7]))
        if not existente and c["lancado_ate"][:7] < vencimento[:7]:
            return {"livre": c}
        if existente and not lancado:
            lancado = {"id": existente["id"], "cobranca": c["id"]}
    return {"lancado": lancado} if lancado else {}


def vincular(cobranca_id: int, despesa_id: int, vencimento: str) -> None:
    """O boleto chegou antes: este lançamento é o do mês, e a cobrança não gera outro."""
    executar("UPDATE despesas SET cobranca_id = ? WHERE id = ?", (cobranca_id, despesa_id))
    executar("UPDATE cobrancas SET lancado_ate = ? WHERE id = ? AND lancado_ate < ?",
             (vencimento, cobranca_id, vencimento))


def futuros(obra_id: int) -> dict:
    """Tudo o que está lançado ou previsto para semanas que ainda não existem."""
    itens = []
    for r in todos(
            f"""SELECT x.id, x.categoria, x.descricao, x.fornecedor, x.valor, x.status_pagamento, x.numero_nota,
                       x.vencimento, x.pagar_em, x.parcela_numero, x.parcela_total, x.cobranca_id, x.dia_id,
                       x.grupo_tipo, x.pago_em, x.imagem_path, x.nome_arquivo, x.reembolsar_para, d.data
                  FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                 WHERE s.obra_id = ? AND {sql_fora_da_semana()}""", (obra_id,)):
        itens.append({**dict(r), "tipo": "lancado", "valor": round(float(r["valor"] or 0), 2),
                      "pagar_em": str(r["pagar_em"] or r["vencimento"])[:10]})
    ativas = listar(obra_id)
    for c in ativas:
        marca = date.fromisoformat(c["lancado_ate"])
        for _ in range(MESES_PREVISTOS):
            marca = proximo_vencimento(marca, c["dia"])
            itens.append({"tipo": "previsto", "cobranca_id": c["id"], "categoria": c["categoria"],
                          "descricao": c["descricao"], "fornecedor": c["fornecedor"],
                          "valor": round(float(c["valor"]), 2), "vencimento": marca.isoformat(),
                          "pagar_em": dia_de_pagar(marca).isoformat()})
    itens.sort(key=lambda i: (i["pagar_em"], i["vencimento"] or ""))
    semanas: dict[str, dict] = {}
    for i in itens:
        dia = date.fromisoformat(i["pagar_em"])
        segunda = dia - timedelta(days=dia.weekday())
        s = semanas.setdefault(segunda.isoformat(), {
            "inicio": segunda.isoformat(), "fim": (segunda + timedelta(days=6)).isoformat(),
            "itens": [], "total": 0.0})
        s["itens"].append(i)
        s["total"] = round(s["total"] + i["valor"], 2)
    lancados = [i for i in itens if i["tipo"] == "lancado"]
    return {"semanas": list(semanas.values()), "cobrancas": ativas,
            "encerradas": [c for c in listar(obra_id, True) if c["status"] != "ativa"],
            "total_lancado": round(sum(i["valor"] for i in lancados), 2), "lancados": len(lancados),
            "por_mes": round(sum(float(c["valor"]) for c in ativas), 2)}
