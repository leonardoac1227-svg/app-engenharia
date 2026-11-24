# -*- coding: utf-8 -*-
"""Cobrança mensal, nota faturada e lançamentos futuros."""
import os
import sys
import tempfile
from datetime import date, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

from obra import create_app
from obra.db import todos, um
from obra.services import acoes as cat
from obra.services import assistente, cobrancas
from obra.services.executor_acoes import Executor, ordenar
from obra.services.parcelamento import alocar_parcelas_agendadas, dia_de_pagar

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome': 'O13'}, headers=H).get_json()['id']
HOJE = date.today()
SEG = HOJE - timedelta(days=HOJE.weekday())
HI = HOJE.isoformat()
def semana(inicio):
    return c.post(f'/api/obras/{O}/semanas', json={'data_inicio': inicio.isoformat()}, headers=H).get_json()['id']
sems = c.get(f'/api/obras/{O}/semanas').get_json()
S = next((s['id'] for s in sems if s['data_inicio'] == SEG.isoformat()), None) or semana(SEG)
DIA = next(d['id'] for d in c.get(f'/api/semanas/{S}/dias').get_json() if d['data'] == HI)
def lancar(valor, venc='', forn='LOCMAQ', desc='Locação mensal', **k):
    return c.post(f'/api/dias/{DIA}/despesas', data={'categoria': 'Locação', 'descricao': desc, 'valor': str(valor),
        'status_pagamento': 'Em Aberto', 'fornecedor': forn, 'vencimento': venc, **k}, headers=H,
        content_type='multipart/form-data')
def da_cobranca(i):
    with app.app_context():
        return [dict(r) for r in todos("""SELECT x.*, d.data FROM despesas x JOIN dias d ON d.id = x.dia_id
                                           WHERE x.cobranca_id = ? ORDER BY x.vencimento""", (i,))]
futuros = lambda: c.get(f'/api/obras/{O}/futuros').get_json()

print('== MESMO DIA TODO MÊS ==')
pv = cobrancas.proximo_vencimento
ok(pv(date(2026, 11, 22), 22) == date(2026, 12, 22) and pv(date(2026, 12, 22), 22) == date(2027, 1, 22), 'dia 22, com virada de ano')
ok(pv(date(2027, 1, 31), 31) == date(2027, 2, 28) and pv(date(2027, 2, 28), 31) == date(2027, 3, 31), 'dia 31: mês curto não muda os seguintes')
ok(dia_de_pagar(date(2027, 1, 22)) == date(2027, 1, 22) and dia_de_pagar(date(2026, 12, 23)) == date(2026, 12, 18),
   'cai na sexta: a própria; cai na quarta: a sexta anterior')

ok(cobrancas.sem_periodo('Locação Mensal (22/10 a 21/11) 50 Escoras Metálicas') == 'Locação Mensal 50 Escoras Metálicas'
   and cobrancas.sem_periodo('Locação Mensal Andaimes (17/11/2026 até 17/12/2026)') == 'Locação Mensal Andaimes'
   and cobrancas.sem_periodo('Andaimes (16 peças)') == 'Andaimes (16 peças)', 'o período de um mês não vai para os outros')

print('== CRIAR, PREVER E GERAR ==')
A = lancar(391).get_json()['id']
ok(c.post(f'/api/despesas/{A}/repetir', json={}, headers=H).status_code == 400, 'sem vencimento, pede o dia')
antes = c.get(f'/api/obras/{O}/financeiro').get_json()['total_em_aberto']
r = c.post(f'/api/despesas/{A}/repetir', json={'vencimento': HI}, headers=H)
C1 = r.get_json()['id']
ok(r.status_code == 201 and r.get_json()['dia'] == HOJE.day, 'cobrança numerada', r.get_json())
ok(c.post(f'/api/despesas/{A}/repetir', json={'vencimento': HI}, headers=H).status_code == 409, 'não repete duas vezes o mesmo')
f = futuros()
prev = [i for s in f['semanas'] for i in s['itens'] if i['cobranca_id'] == C1]
ok(len(prev) == 6 and all(i['tipo'] == 'previsto' for i in prev), 'seis meses previstos na aba', prev)
ok(all(i['pagar_em'] == dia_de_pagar(date.fromisoformat(i['vencimento'])).isoformat() for i in prev), 'na sexta até o vencimento')
ok(c.get(f'/api/obras/{O}/financeiro').get_json()['total_em_aberto'] == antes and len(da_cobranca(C1)) == 1,
   'previsão não entra nas contas da obra')
prox = date.fromisoformat(prev[0]['pagar_em'])
semana(prox - timedelta(days=prox.weekday()))
d = da_cobranca(C1)
ok(len(d) == 2 and d[1]['data'] == prox.isoformat() and d[1]['vencimento'] == prev[0]['vencimento'] and d[1]['valor'] == 391,
   'criada a semana, o lançamento nasce na sexta certa', d[1:])
with app.app_context():
    cobrancas.gerar(O); alocar_parcelas_agendadas(O)
ok(len(da_cobranca(C1)) == 2, 'rodar de novo não duplica')
c.delete(f'/api/despesas/{d[1]["id"]}', headers=H)
with app.app_context(): cobrancas.gerar(O)
ok(len(da_cobranca(C1)) == 1, 'apagado pelo gestor não volta sozinho')

V = lancar(70, '2025-01-10', forn='Velho').get_json()['id']
CV = c.post(f'/api/despesas/{V}/repetir', json={}, headers=H).get_json()['id']
ok(all(x['id'] == V or x['vencimento'] > (SEG - timedelta(days=7)).isoformat() for x in da_cobranca(CV))
   and len(da_cobranca(CV)) <= 3, 'marcar um lançamento antigo não cria os meses passados', da_cobranca(CV))
c.post(f'/api/cobrancas/{CV}/encerrar', json={}, headers=H)

print('== BOLETO DO MÊS QUE CHEGA DEPOIS ==')
V2 = pv(date.fromisoformat(prev[0]['vencimento']), HOJE.day).isoformat()
r = lancar(391, V2)
ok(r.get_json().get('cobranca_id') == C1, 'o boleto do mês seguinte é ligado à cobrança', r.get_json())
ok([i['vencimento'] for s in futuros()['semanas'] for i in s['itens'] if i['cobranca_id'] == C1 and i['tipo'] == 'previsto'][0] > V2,
   'e a previsão daquele mês some')
r = lancar(391, V2)
ok(r.status_code == 409 and 'cobrança mensal' in r.get_json()['erro'], 'o mesmo boleto de novo é barrado', r.get_json())
ok(lancar(391, V2, confirmar_duplicidade='1').status_code == 201, 'mas o gestor pode confirmar')
ok('cobranca_id' not in lancar(500, V2).get_json(), 'outro valor não é da cobrança')

print('== ENCERRAR ==')
B = lancar(635, HI, desc='Escoras').get_json()['id']
C2 = c.post(f'/api/despesas/{B}/repetir', json={}, headers=H).get_json()['id']
ok(C2 != C1 and len(c.post(f'/api/cobrancas/{C2}/encerrar', json={}, headers=H).get_json()['em_aberto']) == 1,
   'cada uma com o seu número, e avisa o que ficou em aberto')
f = futuros()
ok(not any(i['cobranca_id'] == C2 and i['tipo'] == 'previsto' for s in f['semanas'] for i in s['itens']) and
   C2 in [x['id'] for x in f['encerradas']] and C1 in [x['id'] for x in f['cobrancas']], 'só a encerrada para')
ok(len(da_cobranca(C2)) == 2, 'o que já foi lançado continua (a semana do mês seguinte já existia)')
ok(c.post(f'/api/cobrancas/{C2}/encerrar', json={}, headers=H).status_code == 400, 'encerrar duas vezes avisa')
c.post('/api/usuarios', json={'nome': 'M', 'email': 'cb@obra.local', 'senha': 'mestre123', 'papel': 'mestre', 'obras': [O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email': 'cb@obra.local', 'senha': 'mestre123'})
ok(cm.get(f'/api/obras/{O}/futuros').status_code == 403, 'mestre não vê os futuros')

print('== PELO BOT ==')
D = lancar(439, desc='Andaimes').get_json()['id']
with app.app_context():
    ctx = cat.Contexto(O, HI, True, True, [])
    a, av = cat.normalizar([{'tipo': 'repetir_cobranca', 'dados': {'despesa_id': D}}], ctx)
    ok(not a and 'que dia ele vence' in av[0], 'sem vencimento, o bot pergunta', (a, av))
    a, _ = cat.normalizar([{'tipo': 'repetir_cobranca', 'dados': {'despesa_id': D, 'vencimento': HI}},
                           {'tipo': 'encerrar_cobranca', 'dados': {'cobranca_id': C1}},
                           {'tipo': 'encerrar_cobranca', 'dados': {'cobranca_id': 999}},
                           {'tipo': 'despesa', 'dados': {'descricao': 'Internet', 'fornecedor': 'Vivo', 'valor': 80,
                            'vencimento': HI, 'status_pagamento': 'Em Aberto', 'repete_mensal': True,
                            'categoria': 'Locação', 'locacao_frequencia': 'mensal'}}], ctx)
    ok([x['tipo'] for x in a] == ['repetir_cobranca', 'encerrar_cobranca', 'despesa'], 'cobrança que não existe é ignorada')
    ok('repete todo mês' in a[2]['descricao'] and 'locacao_frequencia' not in a[2]['dados'], 'repete, sem abrir aluguel junto', a[2])
    ex = Executor(app, um("SELECT id FROM usuarios WHERE papel = 'gestor'")['id'], O)
    for x in ordenar(a): ex.gravar(x, [])
    ativas = {x['descricao']: x for x in cobrancas.listar(O)}
    ok(set(ativas) == {'Andaimes', 'Internet'}, 'o bot repete, lança repetindo e encerra', ativas)
    ctx = assistente.montar_contexto([{'id': O, 'nome': 'O13'}], HI, True, True, [O])['obras'][0]
    ok({x['id'] for x in ctx['cobrancas_mensais']} == {x['id'] for x in ativas.values()}, 'a IA conhece os números')
    ok(assistente._obra_do_lancamento({'tipo': 'encerrar_cobranca', 'dados': {'cobranca_id': ativas['Internet']['id']}}, {O}) == O,
       'acha a obra pela cobrança')

print('== NOTA FATURADA ==')
def passar(fala='nota da O13', **d):
    acoes = [{'tipo': 'despesa', 'dados': {'descricao': 'Aço', 'valor': 100, 'data': HI, 'status_pagamento': 'Em Aberto', **d}}]
    assistente._parcelamento_so_se_dito(acoes, fala)
    assistente._lancar_na_data_certa(acoes, fala, HI)
    assistente._programar_faturadas(acoes, fala, HI)
    return acoes[0]['dados']
mais = lambda n: (HOJE + timedelta(days=n)).isoformat()
d = passar(faturada=True, condicao_pagamento='30/60', data_documento=HI)
ok(d['parcelas_lista'] == [{'vencimento': mais(30), 'valor': 50}, {'vencimento': mais(60), 'valor': 50}], '30/60 a partir da emissão', d)
d = passar(faturada=True, condicao_pagamento='30/60/90 DDL', data_documento=HI)
ok([p['valor'] for p in d['parcelas_lista']] == [33.33, 33.33, 33.34], 'centavos fecham com a nota', d)
d = passar(faturada=True, condicao_pagamento='30 DDL', data_documento='2019-01-01')
ok(d['parcelas_lista'][0]['vencimento'] == mais(30), 'emissão absurda: conta de hoje', d)
imp = [{'vencimento': mais(28), 'valor': 60}, {'vencimento': mais(56), 'valor': 40}]
ok(passar(faturada=True, parcelas_lista=imp, condicao_pagamento='30/60')['parcelas_lista'] == imp, 'data impressa vale mais que o prazo')
ok('parcelas_lista' not in passar(faturada=True, parcelas_lista=[{'vencimento': mais(28), 'valor': 90}]), 'soma que não fecha não é usada')
ok('parcelas_lista' not in passar(parcelas_lista=imp), 'sem ser faturada nem falar em parcela: como antes')
for cond in ('à vista', '2x', 'ENTRADA + 30', '60/30', '', None):
    ok('parcelas_lista' not in passar(faturada=True, condicao_pagamento=cond), f'condição {cond!r} não inventa data')
ok('parcelas_lista' not in passar('lance nesta semana', faturada=True, condicao_pagamento='30/60'), '"nesta semana" vale')
ok('parcelas_lista' not in passar(faturada=True, condicao_pagamento='30', status_pagamento='Pago'), 'paga não é programada')
d = passar(faturada=True, parcelas_lista=[{'vencimento': mais(-5), 'valor': 100}])
ok('parcelas_lista' not in d and d['data'] == HI, 'vencida fica em hoje', d)
for nome in ('Inova civil', 'INOVACIVIL LTDA', 'inova'):
    d = passar(faturada=True, fornecedor=nome, condicao_pagamento='30/60')
    ok('parcelas_lista' not in d and d['data'] == HI, f'{nome}: sempre na semana em que é enviada', d)
d = passar(faturada=True, fornecedor='Inova civil', parcelas_lista=imp)
ok('parcelas_lista' not in d and d['vencimento'] == mais(28), 'Inova com duplicata impressa: hoje, guardando o vencimento', d)
d = passar('boleto da Inova, veja o vencimento', faturada=True, fornecedor='Inova civil', parcelas_lista=imp)
ok('parcelas_lista' not in d, 'falar em boleto ou vencimento não tira a Inova da semana', d)
d = passar('nota da Inova parcelada', faturada=True, fornecedor='Inova civil', parcelas_lista=imp)
ok(d.get('parcelas_lista') == imp, 'mas se pedir parcelada, vale o pedido', d)
INOVA = dict(fornecedor='Inova civil', faturada=True)
d = passar(f'nota da Inova, lança no dia {HOJE + timedelta(days=20):%d/%m}', data=mais(20), **INOVA)
ok(d['parcelas_lista'] == [{'vencimento': mais(20), 'valor': 100}], 'Inova com o dia ditado: vai para esse dia', d)
d = passar(f'nota da Inova, lança no dia {HOJE + timedelta(days=20):%d/%m}', data=mais(20), parcelas_lista=imp, **INOVA)
ok(d['parcelas_lista'][0]['vencimento'] == mais(20), 'o dia ditado vale mais que o impresso', d)
for fala in ('nota da Inova, segue a data impressa', 'Inova, siga o vencimento da nota', 'Inova, lança conforme a data do boleto',
             'Inova, respeita as datas que estão na nota', 'verifique a data de vencimento e lance na planilha correspodente'):
    ok(passar(fala, parcelas_lista=imp, **INOVA).get('parcelas_lista') == imp, f'"{fala}" usa as datas impressas', passar(fala, parcelas_lista=imp, **INOVA))
    d = passar(fala, fornecedor='Inova civil', vencimento=mais(28))
    ok(d.get('parcelas_lista') == [{'vencimento': mais(28), 'valor': 100}], '   e também só com o vencimento', d)
d = passar('Inova, segue a data da nota', condicao_pagamento='30/60', data_documento=HI, **INOVA)
ok(len(d['parcelas_lista']) == 2, 'e com prazo escrito "30/60"', d)
for fala in ('nota da Inova pra laje do O13', 'Inova, boleto em anexo', 'Inova, lance hoje', 'nota da Inova com vencimento'):
    ok('parcelas_lista' not in passar(fala, parcelas_lista=imp, **INOVA), f'"{fala}": na semana do envio')
from obra.services import boleto
original = boleto.do_arquivo
boleto.do_arquivo = lambda a, h: [{'linha': '1', 'vencimento': mais(28), 'valor': 100}]
def com_boleto(fala, forn):
    acoes = [{'tipo': 'despesa', 'dados': {'descricao': 'x', 'valor': 100, 'data': HI, 'fornecedor': forn, 'anexos': [0],
                                           'status_pagamento': 'Em Aberto'}}]
    assistente._programar_boletos(acoes, fala, HI, [{'nome': 'b.pdf', 'mime': 'application/pdf', 'dados': b'x'}])
    return acoes[0]['dados']
ok('parcelas_lista' not in com_boleto('boleto da Inova', 'Inova civil'), 'boleto da Inova: na semana do envio')
ok(com_boleto('boleto da Inova, segue a data impressa', 'Inova civil').get('parcelas_lista'), 'boleto da Inova com pedido: programado')
ok(com_boleto('boleto', 'LOCMAQ').get('parcelas_lista'), 'boleto dos outros continua programado')
boleto.do_arquivo = original
d = passar(faturada=True, fornecedor='Somar', condicao_pagamento='30/60', data_documento=HI)
ok(len(d['parcelas_lista']) == 2, 'os outros fornecedores seguem a nota')
import json
r = lancar(100, forn='Somar', desc='Aço', parcelas_lista=json.dumps([{'vencimento': mais(-20), 'valor': 50}, {'vencimento': mais(40), 'valor': 50}]))
with app.app_context():
    datas = [x['data'] for x in todos("""SELECT d.data FROM despesas x JOIN dias d ON d.id = x.dia_id
        WHERE x.grupo_parcela = (SELECT grupo_parcela FROM despesas WHERE id = ?) ORDER BY x.parcela_numero""", (r.get_json()['id'],))]
ok(datas[0] == HI, 'parcela já vencida entra hoje, não numa semana fechada', datas)
lan = [i for s in futuros()['semanas'] for i in s['itens'] if i['tipo'] == 'lancado' and i['descricao'].startswith('Aço')]
ok(len(lan) == 1 and lan[0]['vencimento'] == mais(40), 'a futura aparece na aba para conferir', lan)

print('\n' + ('COBRANÇAS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
