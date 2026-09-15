# -*- coding: utf-8 -*-
"""Baixa em lote das contas e quitação da folha ao fechar a semana."""
import os, sys
import os
import sys
import tempfile

# Caminho do projeto derivado da posição deste arquivo: assim a suíte roda em
# qualquer clone, sem depender de onde a pasta foi colocada.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
from obra import create_app

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Obra Pagamentos'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio':'2026-08-17'}, headers=H).get_json()['id']
dias = c.get(f'/api/semanas/{S}/dias').get_json()
DIA = dias[0]['id']

def desp(desc, valor, status):
    return c.post(f'/api/dias/{DIA}/despesas', data={'categoria':'Material','descricao':desc,
        'valor':str(valor),'status_pagamento':status}, headers=H,
        content_type='multipart/form-data')

desp('Cimento', 1000, 'Em Aberto')
desp('Areia', 450, 'Em Aberto')
desp('Combustível', 120, 'Reembolso')
desp('Brita', 300, 'Pago')

f1 = c.post(f'/api/obras/{O}/funcionarios', json={'nome':'SERGIO','valor_hora':28}, headers=H).get_json()['id']
f2 = c.post(f'/api/obras/{O}/funcionarios', json={'nome':'WENDEL','valor_hora':20}, headers=H).get_json()['id']
for d in dias[:5]:
    c.put(f"/api/dias/{d['id']}/horas", json={'registros':[
        {'funcionario_id':f1,'horas':8},{'funcionario_id':f2,'horas':8}]}, headers=H)

print('== SITUAÇÃO INICIAL ==')
fin = c.get(f'/api/semanas/{S}/fechamento').get_json()['financeiro']
ok(fin['folha'] == 1920.0, 'folha da semana (5 dias × 2 pessoas)', fin['folha'])
ok(fin['em_aberto'] == 1450.0, 'contas em aberto', fin['em_aberto'])
ok(fin['a_pagar'] == 1920.0 + 1450.0 + 120.0, 'a pagar = folha + aberto + reembolso', fin['a_pagar'])
custo_original = fin['custo_semana']

print('== BAIXA EM LOTE ==')
r = c.post(f'/api/semanas/{S}/despesas/pagar-todas', json={}, headers=H)
ok(r.status_code == 200 and r.get_json()['quitadas'] == 2, 'baixa as 2 contas em aberto', r.get_json())
ok(r.get_json()['total'] == 1450.0, 'total baixado', r.get_json()['total'])

fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
fin = fe['financeiro']
ok(fin['em_aberto'] == 0, 'nada mais em aberto', fin['em_aberto'])
ok(fin['reembolso'] == 120.0, 'reembolso NÃO foi baixado junto', fin['reembolso'])
ok(fin['pago'] == 1750.0, 'total pago acumulado (300 + 1450)', fin['pago'])
ok(fin['custo_semana'] == custo_original, 'custo da semana não muda ao dar baixa', fin['custo_semana'])
pagas = [d for d in fe['despesas'] if d['status_pagamento'] == 'Pago']
ok(all(d['pago_em'] for d in pagas), 'data de pagamento registrada em cada uma',
   [d['pago_em'] for d in pagas][:2])

print('== REEMBOLSO SÓ SE PEDIR ==')
r = c.post(f'/api/semanas/{S}/despesas/pagar-todas', json={'incluir_reembolsos': True}, headers=H)
ok(r.get_json()['quitadas'] == 1 and r.get_json()['total'] == 120.0,
   'com a opção, o reembolso é baixado', r.get_json())
r = c.post(f'/api/semanas/{S}/despesas/pagar-todas', json={}, headers=H)
ok(r.get_json()['quitadas'] == 0, 'rodar de novo não faz nada (idempotente)', r.get_json())

print('== FOLHA AO FECHAR A SEMANA ==')
fin = c.get(f'/api/semanas/{S}/fechamento').get_json()['financeiro']
ok(fin['a_pagar'] == 1920.0, 'só a folha continua pendente', fin['a_pagar'])

r = c.put(f'/api/semanas/{S}', json={'fechada': True}, headers=H)
ok(r.get_json().get('folha', {}).get('paga'), 'fechar a semana quita a folha', r.get_json())
ok(r.get_json()['folha']['valor'] == 1920.0, 'valor quitado é o da folha', r.get_json()['folha'])

fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
ok(fe['folha_paga'] and fe['folha_paga_em'], 'estado registrado com data', fe['folha_paga_em'])
ok(fe['financeiro']['a_pagar'] == 0, 'a pagar zerou', fe['financeiro']['a_pagar'])

print('== O HISTÓRICO NÃO PODE MUDAR ==')
ok(fe['financeiro']['folha'] == 1920.0, 'folha continua no relatório', fe['financeiro']['folha'])
ok(fe['financeiro']['custo_semana'] == custo_original, 'custo da semana intacto',
   (fe['financeiro']['custo_semana'], custo_original))
ok(len(fe['folha_detalhada']) == 2, 'folha por profissional preservada', len(fe['folha_detalhada']))
horas = c.get(f"/api/dias/{DIA}/horas").get_json()
ok(len(horas) == 2, 'as horas lançadas NÃO foram apagadas', horas)
tot = c.get(f'/api/dias/{DIA}/totais').get_json()
ok(tot['folha'] == 384.0, 'total do dia continua o mesmo', tot)

print('== NÃO AFETA OS OUTROS CUSTOS ==')
r = c.post(f'/api/obras/{O}/locacoes', json={'equipamento':'Andaime','valor_parcela':391,
    'frequencia':'mensal','ciclos':2,'data_inicio':'2026-08-18'}, headers=H)
LOC = r.get_json()['id']
al = c.get(f'/api/obras/{O}/locacoes/alertas').get_json()
pendentes_antes = al['quantidade']
c.put(f'/api/semanas/{S}', json={'fechada': False}, headers=H)
c.put(f'/api/semanas/{S}', json={'fechada': True}, headers=H)
al = c.get(f'/api/obras/{O}/locacoes/alertas').get_json()
ok(al['quantidade'] == pendentes_antes, 'renovações de locação seguem pendentes', al['quantidade'])
det = c.get(f'/api/locacoes/{LOC}').get_json()
ok(det['total_lancado'] == 0, 'nada foi pago automaticamente na locação', det['total_lancado'])

desp('Nova compra depois de fechar', 700, 'Em Aberto')
fin = c.get(f'/api/semanas/{S}/fechamento').get_json()['financeiro']
ok(fin['a_pagar'] == 700.0, 'conta nova continua entrando no a pagar', fin['a_pagar'])

print('== SEMANA SEGUINTE COMEÇA LIMPA ==')
S2 = c.post(f'/api/obras/{O}/semanas', json={'data_inicio':'2026-08-24'}, headers=H).get_json()['id']
fe2 = c.get(f'/api/semanas/{S2}/fechamento').get_json()
ok(fe2['financeiro']['folha'] == 0, 'folha da nova semana começa em zero', fe2['financeiro']['folha'])
ok(not fe2['folha_paga'], 'nova semana nasce com folha em aberto', fe2['folha_paga'])

print('== REABRIR A FOLHA ==')
r = c.delete(f'/api/semanas/{S}/folha/pagar', headers=H)
ok(r.status_code == 200, 'desfazer a quitação', r.get_json())
fin = c.get(f'/api/semanas/{S}/fechamento').get_json()['financeiro']
ok(fin['a_pagar'] == 700.0 + 1920.0, 'folha volta a contar como pendente', fin['a_pagar'])

print('== QUITAR SEM FECHAR ==')
r = c.post(f'/api/semanas/{S2}/folha/pagar', json={}, headers=H)
ok(r.status_code == 200 and r.get_json()['paga'], 'dá para quitar a folha sem fechar a semana', r.get_json())
ok(not c.get(f'/api/semanas/{S2}/fechamento').get_json()['fechada'], 'semana continua aberta')

print('== PERMISSÕES ==')
c.post('/api/usuarios', json={'nome':'Mestre','email':'mg@obra.local','senha':'mestre123',
                              'papel':'mestre','obras':[O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email':'mg@obra.local','senha':'mestre123'})
HM = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
ok(cm.post(f'/api/semanas/{S}/despesas/pagar-todas', json={}, headers=HM).status_code == 403,
   'mestre não dá baixa em contas')
ok(cm.post(f'/api/semanas/{S}/folha/pagar', json={}, headers=HM).status_code == 403,
   'mestre não quita a folha')
r = cm.put(f'/api/semanas/{S}', json={'fechada': True}, headers=HM)
ok('folha' not in (r.get_json() or {}), 'semana fechada por mestre não quita folha', r.get_json())

print('\n' + ('PAGAMENTOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
