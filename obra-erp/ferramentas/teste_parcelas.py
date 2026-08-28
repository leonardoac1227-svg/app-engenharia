# -*- coding: utf-8 -*-
"""Nota parcelada: divisão exata, vencimentos, caixa e cancelamento."""
import os, sys
from datetime import date
os.environ['DATA_DIR'] = '/tmp/parc_dados'
sys.path.insert(0, '/home/claude/obra-erp')
from obra import create_app

app = create_app(); c = app.test_client()
falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Obra Parcelas'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio':'2026-08-03'}, headers=H).get_json()['id']
dias = c.get(f'/api/semanas/{S}/dias').get_json()
DIA = dias[0]['id']

def lancar(**extra):
    base = {'categoria':'Material','descricao':'Material de acabamento','fornecedor':'DEPOSITO BAHIA',
            'valor':'5000','status_pagamento':'Em Aberto','numero_nota':'88123',
            'vencimento':'2026-09-10'}
    base.update(extra)
    return c.post(f'/api/dias/{DIA}/despesas', data=base, headers=H, content_type='multipart/form-data')

print('== PARCELAMENTO EM 5x ==')
r = lancar(parcelas='5', parcela_frequencia='mensal')
ok(r.status_code == 201, 'lançar nota parcelada', r.get_json())
p = r.get_json()['parcelamento']
ok(p['parcelas'] == 5 and p['valor_parcela'] == 1000.0, '5 parcelas de R$ 1.000', p)
ok(p['valor_total'] == 5000.0, 'valor informado é o TOTAL da nota', p['valor_total'])
vencs = [x['vencimento'] for x in p['lancamentos']]
ok(vencs == ['2026-09-10','2026-10-10','2026-11-10','2026-12-10','2027-01-10'],
   'mensal preserva o dia 10', vencs)

print('== CAIXA ==')
fin = c.get(f'/api/obras/{O}/financeiro').get_json()
ok(fin['total_em_aberto'] == 5000.0, 'as 5 parcelas somam o total da nota em contas a pagar',
   fin['total_em_aberto'])
lista = c.get(f'/api/dias/{DIA}/despesas').get_json()
primeira = [x for x in lista if x.get('parcela_numero') == 1][0]
ok(primeira['valor'] == 1000.0, 'a despesa original virou a 1ª parcela', primeira['valor'])
ok(primeira['descricao'].endswith('(1/5)'), 'descrição numerada', primeira['descricao'])
ok(primeira['numero_nota'] == '88123', 'a nota é a mesma em todas', primeira['numero_nota'])

parcelas = c.get(f"/api/despesas/{primeira['id']}/parcelas").get_json()
ok(len(parcelas) == 5, 'as 5 parcelas ficam ligadas pelo grupo', len(parcelas))
ok(round(sum(x['valor'] for x in parcelas), 2) == 5000.0, 'soma das parcelas = total da nota',
   sum(x['valor'] for x in parcelas))

print('== DIVISÃO COM CENTAVOS ==')
r = lancar(descricao='Tinta', valor='100', parcelas='3', parcela_frequencia='mensal', numero_nota='777')
p = r.get_json()['parcelamento']
valores = [x['valor'] for x in p['lancamentos']]
ok(valores == [33.33, 33.33, 33.34], 'sobra de centavo vai para a última parcela', valores)
ok(round(sum(valores), 2) == 100.0, 'soma bate com o total exato', sum(valores))

print('== OUTROS INTERVALOS ==')
r = lancar(descricao='Areia', valor='600', parcelas='3', parcela_frequencia='quinzenal',
           vencimento='2026-09-01', numero_nota='901')
vencs = [x['vencimento'] for x in r.get_json()['parcelamento']['lancamentos']]
ok(vencs == ['2026-09-01','2026-09-16','2026-10-01'], 'quinzenal anda 15 dias', vencs)

r = lancar(descricao='Brita', valor='400', parcelas='4', parcela_frequencia='semanal',
           vencimento='2026-09-07', numero_nota='902')
vencs = [x['vencimento'] for x in r.get_json()['parcelamento']['lancamentos']]
ok(vencs == ['2026-09-07','2026-09-14','2026-09-21','2026-09-28'], 'semanal anda 7 dias', vencs)

r = lancar(descricao='Cal', valor='900', parcelas='3', parcela_frequencia='dias',
           parcela_intervalo='45', vencimento='2026-09-01', numero_nota='903')
vencs = [x['vencimento'] for x in r.get_json()['parcelamento']['lancamentos']]
ok(vencs == ['2026-09-01','2026-10-16','2026-11-30'], 'intervalo livre em dias', vencs)

print('== VIRADA DE MÊS ==')
r = lancar(descricao='Bloco', valor='3000', parcelas='4', parcela_frequencia='mensal',
           vencimento='2026-01-31', numero_nota='904')
vencs = [x['vencimento'] for x in r.get_json()['parcelamento']['lancamentos']]
ok(vencs == ['2026-01-31','2026-02-28','2026-03-31','2026-04-30'],
   'meses curtos caem no último dia válido', vencs)

print('== NOTA DUPLICADA x PARCELA ==')
r = lancar(descricao='Outra compra', valor='200', numero_nota='88123', parcelas='')
ok(r.status_code == 201, 'a trava de duplicidade não acusa a própria nota parcelada', r.get_json())

print('== LIMITES ==')
ok(lancar(descricao='X', parcelas='1', numero_nota='910').status_code == 201,
   '1 parcela = lançamento normal, sem parcelamento')
r = lancar(descricao='Y', parcelas='99', numero_nota='911')
ok(r.status_code == 400, 'número absurdo de parcelas é recusado', r.status_code)
r = lancar(descricao='Z', parcelas='3', parcela_frequencia='anual', numero_nota='912')
ok(r.status_code == 400, 'intervalo inválido é recusado', r.status_code)

print('== CANCELAMENTO ==')
alvo = parcelas[0]['id']
c.post(f"/api/despesas/{parcelas[1]['id']}/pagar", json={}, headers=H)
r = c.delete(f'/api/despesas/{alvo}/parcelas', headers=H)
ok(r.status_code == 200 and r.get_json()['removidas'] == 4,
   'cancelar remove as 4 em aberto e mantém a paga', r.get_json())
restantes = c.get(f"/api/despesas/{parcelas[1]['id']}/parcelas").get_json()
ok(len(restantes) == 1 and restantes[0]['status_pagamento'] == 'Pago',
   'a parcela já paga continua no caixa', restantes)

print('== RELATÓRIOS ==')
fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
ok(any('(2/3)' in x['descricao'] for x in fe['despesas']), 'parcelas aparecem no fechamento',
   [x['descricao'] for x in fe['despesas']][:4])
r = c.get(f'/api/semanas/{S}/relatorio.pdf')
ok(r.status_code == 200 and r.data[:4] == b'%PDF', 'PDF gera', r.status_code)
r = c.get(f'/api/semanas/{S}/pagamentos.xlsx')
ok(r.status_code == 200, 'planilha de pagamento gera', r.status_code)

print('== PERMISSÃO ==')
c.post('/api/usuarios', json={'nome':'Mestre','email':'mp2@obra.local','senha':'mestre123',
                              'papel':'mestre','obras':[O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email':'mp2@obra.local','senha':'mestre123'})
HM = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
ok(cm.delete(f"/api/despesas/{parcelas[1]['id']}/parcelas", headers=HM).status_code == 403,
   'mestre não cancela parcelamento')

print('\n' + ('PARCELAMENTO OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
