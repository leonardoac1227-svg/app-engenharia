# -*- coding: utf-8 -*-
"""Ponto em vários dias: "de segunda a quinta" lança um ponto por dia."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

from obra.services.assistente import PROMPT_BASE, _ponto_nos_dias_ditos, dias_ditos

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

QUARTA = '2026-11-18'
SEG_A_QUI = ['2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19']
SEG_A_SEX = SEG_A_QUI + ['2026-11-20']

print('== DIAS NA FALA ==')
casos = [
    ("lance 8horas para todo o efetivo do i19 de sengunda a quinta", SEG_A_QUI),
    ("ance 8horas para todo o efetivo do o13 para todos os dias menos sexta feira", SEG_A_QUI),
    ("segunda, terça e quarta o Ademir trabalhou", SEG_A_QUI[:3]),
    ("a semana toda 8 horas pra equipe", SEG_A_SEX),
    ("de segunda-feira até sexta-feira", SEG_A_SEX),
    ("ponto de terça a sábado menos quinta", ['2026-11-17', '2026-11-18', '2026-11-20', '2026-11-21']),
    ("Ademir trabalhou hoje", []),
    ("na segunda o Ademir faltou", []),
    ("o quintal foi limpo na terça", []),
]
for fala, esperado in casos:
    ok(dias_ditos(fala, QUARTA) == esperado, f'"{fala[:50]}"', dias_ditos(fala, QUARTA))

print('== UM PONTO POR DIA ==')
acoes = [{'tipo': 'ponto', 'dados': {'data': '2026-11-16', 'equipe_inteira': True, 'horas': 8}}]
_ponto_nos_dias_ditos(acoes, casos[0][0], QUARTA)
ok([a['dados']['data'] for a in acoes] == SEG_A_QUI, 'IA devolveu só segunda: vira segunda a quinta',
   [a['dados']['data'] for a in acoes])
ok(all(a['dados']['equipe_inteira'] and a['dados']['horas'] == 8 for a in acoes), 'cada dia com o mesmo ponto')

acoes = [{'tipo': 'ponto', 'dados': {'data': QUARTA, 'equipe_inteira': True, 'horas': 8}},
         {'tipo': 'diario', 'dados': {'data': QUARTA, 'producao_dia': 'x'}}]
_ponto_nos_dias_ditos(acoes, casos[1][0], QUARTA)
ok([a['tipo'] for a in acoes] == ['ponto'] * 4 + ['diario'], 'outras ações ficam como estão', acoes)

acoes = [{'tipo': 'ponto', 'dados': {'data': '2026-11-16', 'registros': [{'nome': 'Ademir', 'horas': 8}]}},
         {'tipo': 'ponto', 'dados': {'data': '2026-11-17', 'registros': [{'nome': 'Ademir', 'horas': 4}]}}]
antes = [dict(a['dados']) for a in acoes]
_ponto_nos_dias_ditos(acoes, "segunda e terça o Ademir", QUARTA)
ok([a['dados'] for a in acoes] == antes, 'pontos diferentes por dia: não mexe')

acoes = [{'tipo': 'ponto', 'dados': {'data': QUARTA, 'equipe_inteira': True}}]
_ponto_nos_dias_ditos(acoes, "a equipe inteira veio hoje", QUARTA)
ok(len(acoes) == 1, 'um dia só: não mexe')

ok('UMA AÇÃO PONTO POR DIA' in PROMPT_BASE, 'regra no prompt da IA')

print('\n' + ('PONTO EM VÁRIOS DIAS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
