# -*- coding: utf-8 -*-
"""Exclusão de semana sem deixar o caixa de outras semanas inconsistente.

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

from ..db import executar, todos
from .anexos_despesa import apagar_arquivos_orfaos


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
