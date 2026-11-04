# -*- coding: utf-8 -*-
"""Criação automática de semanas e exclusão sem estragar o caixa de outras semanas.

Toda obra ativa ganha sozinha a semana que começou: quem abre o app na segunda
já encontra a semana certa, sem passar obra por obra. Não há agendador porque
o Render adormece o serviço sem uso e um horário marcado poderia nunca rodar.
A conferência é feita uma vez por dia, na primeira requisição que chega, e
completa também as semanas que ficaram para trás.

`semanas_automaticas` registra o que já foi criado por obra e data. Com dois
workers, só quem conseguir gravar o registro cria a semana, e uma semana
excluída pelo gestor não volta sozinha na conferência seguinte.

Apagar a semana leva junto tudo que foi lançado nela (dias, ponto, fotos,
despesas, medições), pelo ON DELETE CASCADE do banco. O cuidado está no que
**cruza** semanas:

- Parcela de nota lançada em outra semana, que já tinha sido movida para um
  dia desta (vence aqui), não pode sumir: a dívida continua. Ela volta a ficar
  "agendada" no dia do lançamento da nota, como antes desta semana existir, e
  reaparece sozinha se a semana for recriada (`alocar_parcelas_agendadas`).
- Nota lançada nesta semana leva junto as parcelas futuras ainda não pagas.
  Parcela já paga fica: o dinheiro saiu, e apagá-la faria o caixa mentir
  (mesma regra de `parcelamento.remover_grupo`).
- Renovação de aluguel lançada nesta semana volta a ficar pendente, para o
  painel continuar avisando.
- Fotos e anexos só saem do armazenamento depois do banco, e só os que
  nenhum outro registro usa.
"""

from __future__ import annotations

import logging
import threading
from datetime import date

from ..db import executar, get_db, registrar_auditoria, todos, um
from ..utils import agora_iso, hoje_iso, segunda_da_semana, somar_dias
from .anexos_despesa import apagar_arquivos_orfaos
from .fechamento import garantir_dias
from .parcelamento import alocar_parcelas_agendadas

log = logging.getLogger("obra-erp")

USUARIO_AUTOMATICO = {"id": None, "nome": "Automático"}

_trava_conferencia = threading.Lock()
_conferido_em = None


def criar_semana_em(obra_id: int, inicio: str, meta: str = "", orcamento: float = 0,
                    usuario=None) -> dict:
    """Cria a semana que começa em `inicio` (uma segunda-feira) com os seus 7 dias."""
    ultima = um("SELECT numero FROM semanas WHERE obra_id = ? ORDER BY numero DESC LIMIT 1", (obra_id,))
    numero_novo = (ultima["numero"] + 1) if ultima else 1
    cur = executar(
        """INSERT INTO semanas (obra_id, numero, data_inicio, meta, meta_atingida,
                                orcamento_previsto, fechada, criado_em)
           VALUES (?, ?, ?, ?, 0, ?, 0, ?)""",
        (obra_id, numero_novo, inicio, meta, orcamento, agora_iso()),
    )
    semana_nova = cur.lastrowid
    garantir_dias(semana_nova)
    # Parcelas que vencem nesta semana estavam agendadas: passam a contar aqui.
    parcelas = alocar_parcelas_agendadas(obra_id)

    registrar_auditoria(usuario, "criou_semana", "semana", semana_nova,
                        f"Semana {numero_novo}" + (f" · {parcelas} parcela(s) prevista(s)" if parcelas else ""))
    return {"id": semana_nova, "numero": numero_novo, "data_inicio": inicio,
            "parcelas_previstas": parcelas}


def semanas_que_faltam(ultima: str | None, inicio_obra: str | None, hoje: date) -> list[str]:
    """Segundas-feiras a criar para a obra chegar até a semana de `hoje`.

    Obra sem nenhuma semana começa na semana atual, e não na data de início:
    semanas antigas sem nada lançado só encheriam o painel de pendências. Obra
    que ainda vai começar espera a semana dela.
    """
    atual = segunda_da_semana(hoje)
    if ultima:
        proxima = somar_dias(segunda_da_semana(date.fromisoformat(ultima)), 7)
    elif inicio_obra and inicio_obra > somar_dias(atual, 6):
        return []
    else:
        proxima = atual

    faltam = []
    while proxima <= atual:
        faltam.append(proxima)
        proxima = somar_dias(proxima, 7)
    return faltam


def _criar_semana_automatica(obra_id: int, inicio: str) -> bool:
    reservada = executar(
        "INSERT OR IGNORE INTO semanas_automaticas (obra_id, data_inicio, criado_em) VALUES (?, ?, ?)",
        (obra_id, inicio, agora_iso()))
    if not reservada.rowcount:
        return False
    if um("SELECT id FROM semanas WHERE obra_id = ? AND data_inicio = ?", (obra_id, inicio)):
        return False
    try:
        criar_semana_em(obra_id, inicio, usuario=USUARIO_AUTOMATICO)
    except Exception:
        # Sem a semana, a reserva não pode ficar: a próxima conferência tenta de novo.
        get_db().rollback()
        executar("DELETE FROM semanas_automaticas WHERE obra_id = ? AND data_inicio = ?",
                 (obra_id, inicio))
        raise
    return True


def garantir_semanas_em_dia(hoje: date | None = None) -> int:
    """Cria as semanas que faltam nas obras ativas. Devolve quantas foram criadas."""
    hoje = hoje or date.today()
    obras = todos(
        """SELECT o.id, o.data_inicio,
                  (SELECT MAX(s.data_inicio) FROM semanas s WHERE s.obra_id = o.id) AS ultima
             FROM obras o WHERE o.arquivada = 0 AND o.status = 'ativa'""")
    criadas = 0
    for obra in obras:
        try:
            for inicio in semanas_que_faltam(obra["ultima"], obra["data_inicio"], hoje):
                criadas += 1 if _criar_semana_automatica(obra["id"], inicio) else 0
        except Exception:  # noqa: BLE001 — uma obra com problema não trava as outras
            get_db().rollback()
            log.exception("Falha ao criar semanas automáticas da obra %s.", obra["id"])
    return criadas


def conferir_semanas_do_dia() -> None:
    """Roda `garantir_semanas_em_dia` uma vez por dia em cada processo."""
    global _conferido_em
    hoje = hoje_iso()
    if _conferido_em == hoje:
        return
    with _trava_conferencia:
        if _conferido_em == hoje:
            return
        try:
            criadas = garantir_semanas_em_dia()
        except Exception:  # noqa: BLE001 — não pode derrubar a requisição do usuário
            get_db().rollback()
            log.exception("Falha na conferência diária das semanas.")
            return
        _conferido_em = hoje
    if criadas:
        log.info("%d semana(s) criada(s) automaticamente.", criadas)


def _marcas(ids) -> str:
    return ", ".join("?" for _ in ids)


def excluir_semana(semana_id: int) -> dict:
    dias = [linha["id"] for linha in todos("SELECT id FROM dias WHERE semana_id = ?", (semana_id,))]
    resumo = {"despesas": 0, "fotos": 0, "parcelas_reagendadas": 0, "parcelas_removidas": 0}
    if not dias:
        executar("DELETE FROM semanas WHERE id = ?", (semana_id,))
        return resumo
    em_dias = _marcas(dias)

    # 1) Parcelas de notas de outras semanas que vencem aqui: voltam para o dia
    #    em que a nota foi lançada.
    de_fora = todos(
        f"""SELECT x.id, origem.dia_id AS dia_origem
              FROM despesas x
              JOIN despesas origem ON origem.grupo_parcela = x.grupo_parcela
                                  AND origem.parcela_numero = 1
             WHERE x.dia_id IN ({em_dias}) AND x.grupo_tipo = 'parcela'
               AND x.parcela_numero > 1 AND x.grupo_parcela != ''
               AND origem.dia_id NOT IN ({em_dias})""", [*dias, *dias])
    for linha in de_fora:
        executar("UPDATE despesas SET dia_id = ? WHERE id = ?", (linha["dia_origem"], linha["id"]))
    resumo["parcelas_reagendadas"] = len(de_fora)

    # 2) Notas lançadas aqui: as parcelas em aberto de outras semanas vão junto.
    orfas = todos(
        f"""SELECT x.id FROM despesas x
             WHERE x.dia_id NOT IN ({em_dias}) AND x.grupo_tipo = 'parcela'
               AND x.status_pagamento != 'Pago' AND x.grupo_parcela != ''
               AND x.grupo_parcela IN (SELECT o.grupo_parcela FROM despesas o
                                        WHERE o.dia_id IN ({em_dias}) AND o.parcela_numero = 1
                                          AND o.grupo_parcela != '')""", [*dias, *dias])
    ids_orfas = [linha["id"] for linha in orfas]

    # 3) Tudo que vai ser apagado: despesas desta semana + parcelas órfãs.
    despesas = [linha["id"] for linha in todos(
        f"SELECT id FROM despesas WHERE dia_id IN ({em_dias})", dias)] + ids_orfas
    resumo["despesas"] = len(despesas) - len(ids_orfas)
    resumo["parcelas_removidas"] = len(ids_orfas)

    arquivos = [linha["arquivo"] for linha in todos(
        f"SELECT arquivo FROM fotos WHERE dia_id IN ({em_dias})", dias)]
    resumo["fotos"] = len(arquivos)

    if despesas:
        em_despesas = _marcas(despesas)
        arquivos += [linha["arquivo"] for linha in todos(
            f"SELECT arquivo FROM despesa_anexos WHERE despesa_id IN ({em_despesas})", despesas)]
        arquivos += [linha["imagem_path"] for linha in todos(
            f"SELECT imagem_path FROM despesas WHERE id IN ({em_despesas})", despesas)]
        executar(f"""UPDATE locacao_parcelas SET status = 'prevista', despesa_id = NULL
                      WHERE despesa_id IN ({em_despesas})""", despesas)
        # Explícito em vez de confiar só no CASCADE: o banco de produção pode ter
        # nascido numa versão antiga, sem as chaves estrangeiras atuais.
        executar(f"DELETE FROM despesa_anexos WHERE despesa_id IN ({em_despesas})", despesas)
        executar(f"DELETE FROM despesas WHERE id IN ({em_despesas})", despesas)

    executar(f"DELETE FROM fotos WHERE dia_id IN ({em_dias})", dias)
    executar("DELETE FROM semanas WHERE id = ?", (semana_id,))
    # Tabelas ligadas aos dias por chave estrangeira com CASCADE; o DELETE
    # explícito cobre bancos antigos em que a chave não existe.
    for tabela in ("horas", "medicoes", "meta_progresso"):
        executar(f"DELETE FROM {tabela} WHERE dia_id IN ({em_dias})", dias)
    executar(f"DELETE FROM dias WHERE id IN ({em_dias})", dias)
    for tabela in ("metas", "contrato_medicoes"):
        executar(f"DELETE FROM {tabela} WHERE semana_id = ?", (semana_id,))

    apagar_arquivos_orfaos(arquivos)
    return resumo
