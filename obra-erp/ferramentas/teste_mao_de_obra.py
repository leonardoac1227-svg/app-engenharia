# -*- coding: utf-8 -*-
"""Mão de obra na planilha, fichado e avulso, transferência de obra e ponto "até hoje"."""
import io
import os
import sys
import tempfile
from datetime import date, timedelta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

from openpyxl import load_workbook

from obra import create_app
from obra.db import executar, todos, um
from obra.services import acoes as cat
from obra.services import assistente, mao_de_obra
from obra.services.executor_acoes import Executor, ordenar
from obra.services.fechamento import listar_dias

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
I19 = c.post('/api/obras', json={'nome': 'I19'}, headers=H).get_json()['id']
T21 = c.post('/api/obras', json={'nome': 'T21'}, headers=H).get_json()['id']
SEGUNDA = date(2026, 8, 17)
S = c.post(f'/api/obras/{I19}/semanas', json={'data_inicio': SEGUNDA.isoformat()}, headers=H).get_json()['id']
ST = c.post(f'/api/obras/{T21}/semanas', json={'data_inicio': SEGUNDA.isoformat()}, headers=H).get_json()['id']
DIAS = [d['id'] for d in c.get(f'/api/semanas/{S}/dias').get_json()]
DIAST = [d['id'] for d in c.get(f'/api/semanas/{ST}/dias').get_json()]

def novo(obra, nome, **k):
    i = c.post(f'/api/obras/{obra}/funcionarios', json={'nome': nome, **k}, headers=H).get_json()['id']
    with app.app_context():
        executar("UPDATE funcionarios SET criado_em = '2026-01-01' WHERE id = ?", (i,))
    return i
def ponto(dia, **h):
    c.put(f'/api/dias/{dia}/horas', json={'registros': [
        {'funcionario_id': int(k[1:]), 'horas': v[0] if isinstance(v, tuple) else v,
         'justificativa': v[1] if isinstance(v, tuple) else ''} for k, v in h.items()]}, headers=H)
def custo(obra=None, semana=None):
    with app.app_context():
        r = mao_de_obra.calcular(obra or I19, listar_dias(semana or S, False))
    return {p['nome']: p for p in r['pessoas']}, r

ADEMIR = novo(I19, 'Ademir', funcao='Pedreiro')
CARLOS = novo(I19, 'Carlos', funcao='Servente', classe='servente_comum', vinculo='fichado')
ZE = novo(I19, 'Seu jose(manteiga)', funcao='Ajudante', vinculo='avulso', diaria=100)
COSME = novo(I19, 'Cosme', funcao='Ajudante', vinculo='avulso')
BETO = novo(I19, 'Beto', funcao='Servente')

print('== VALORES DA TABELA ==')
for i, d in enumerate(DIAS[:5]):
    ponto(d, **{f'f{ADEMIR}': 8, f'f{CARLOS}': 8, f'f{ZE}': 8, f'f{COSME}': 8, f'f{BETO}': 8})
p, r = custo()
ok(p['Ademir']['valor'] == 1387.40 and p['Ademir']['diarias'] == 5, 'pedreiro, semana cheia: R$ 1.387,40', p['Ademir'])
ok(p['Carlos']['valor'] == round(5 * 180.89, 2), 'servente comum: R$ 180,89 por dia', p['Carlos'])
ok(p['Seu jose(manteiga)']['valor'] == 500, 'avulso: diária combinada, sem encargos', p['Seu jose(manteiga)'])
ok(not p['Cosme']['definido'] and not p['Beto']['definido'] and set(r['a_definir']) == {'Beto', 'Cosme'},
   'sem valor definido não é adivinhado ("servente" sozinho também não)', r['a_definir'])
ok(r['total'] == round(1387.40 + 904.45 + 500, 2), 'o total só soma quem tem valor', r['total'])

print('== MEIA DIÁRIA E FALTAS ==')
ponto(DIAS[0], **{f'f{ADEMIR}': 4})
ok(custo()[0]['Ademir']['valor'] == round(1387.40 - 130.81 / 2, 2), 'meia diária: desconta metade do líquido, encargos cheios')
ponto(DIAS[0], **{f'f{ADEMIR}': 0})
x = custo()[0]['Ademir']
ok(x['valor'] == round(1387.40 - 130.81, 2) and x['faltas'] == 1, 'falta inteira: só os encargos', x)
ponto(DIAS[0], **{f'f{ADEMIR}': (0, 'Atestado médico')})
x = custo()[0]['Ademir']
ok(x['valor'] == 1387.40 and x['justificadas'] == 1, 'falta justificada: diária cheia', x)
ponto(DIAS[0], **{f'f{ZE}': 4}); ponto(DIAS[1], **{f'f{ZE}': 0})
x = custo()[0]['Seu jose(manteiga)']
ok(x['valor'] == 350 and x['faltas'] == 0, 'avulso: meia diária é metade, falta é zero', x)
c.post(f'/api/dias/{DIAST[2]}/horas/avulso', json={'funcionario_id': CARLOS, 'horas': 8}, headers=H)
ponto(DIAS[2], **{f'f{CARLOS}': 0})
ok(custo()[0]['Carlos']['faltas'] == 0 and custo(T21, ST)[0]['Carlos']['valor'] == 180.89,
   'emprestado: cobrado só na obra onde trabalhou, sem falta na dele', custo()[0]['Carlos'])
ponto(DIAS[5], **{f'f{ADEMIR}': 4})
ok(custo()[0]['Ademir']['valor'] == round(1387.40 + 130.81 / 2 + 146.67, 2), 'sábado trabalhado entra')
ponto(DIAS[5], **{f'f{ADEMIR}': 0})
ok(custo()[0]['Ademir']['valor'] == 1387.40, 'sábado sem ponto não é falta')

print('== PLANILHA ==')
c.post(f'/api/dias/{DIAS[0]}/despesas', data={'categoria': 'Material', 'descricao': 'Cimento', 'valor': '100',
       'status_pagamento': 'Em Aberto'}, headers=H, content_type='multipart/form-data')
def abrir():
    r = c.get(f'/api/semanas/{S}/pagamentos.xlsx?obra=1')
    ws = load_workbook(io.BytesIO(r.data)).active
    return ws, [[ws.cell(l, k).value for k in range(1, 8)] for l in range(1, ws.max_row + 1)]
ws, linhas = abrir()
acha = lambda t: next(i for i, l in enumerate(linhas) if str(l[0] or '').startswith(t))
ok(acha('NOTAS ABERTAS') < acha('TOTAL A PAGAR') < acha('MÃO DE OBRA I19') < acha('TOTAL MÃO DE OBRA') < acha('PONTO:'),
   'materiais, mão de obra, total embaixo e ponto resumido')
l = linhas[acha('ADEMIR')]
ok(l[1] == 'PEDREIRO' and l[2] == '4 DIÁRIAS + 1 JUSTIFICADA' and l[4] == 1387.4, 'nome, função, dias e valor', l)
ok(linhas[acha('SEU JOSE')][1] == 'AVULSO' and linhas[acha('COSME')][4] == 'A DEFINIR', 'avulso e valor a definir')
ok(linhas[acha('TOTAL MÃO DE OBRA')][3] == f'=SUM(E{acha("ADEMIR") + 1}:E{acha("TOTAL MÃO DE OBRA")})', 'total por fórmula')
ok('E' in linhas[acha('TOTAL A PAGAR')][3] and linhas[acha('TOTAL A PAGAR')][3].count('+') == 1,
   'TOTAL A PAGAR continua só com materiais', linhas[acha('TOTAL A PAGAR')][3])
ok(all(l[6] is None for l in linhas) and sum(str(l[0] or '').startswith('PONTO') for l in linhas) == 1,
   'ponto numa linha só, sem a coluna lateral')
ok(ws.print_area.endswith(f'$F${acha("PONTO:") + 1}'), 'a impressão pega tudo', ws.print_area)

print('== QUEM VÊ OS VALORES ==')
fe = str(c.get(f'/api/semanas/{S}/fechamento').get_json())
ok('mao_de_obra' not in fe, 'o fechamento (que o cliente abre) não traz custo por pessoa')
c.post('/api/usuarios', json={'nome': 'M', 'email': 'mo@obra.local', 'senha': 'mestre123', 'papel': 'mestre',
                              'obras': [I19]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email': 'mo@obra.local', 'senha': 'mestre123'})
ok('diaria' not in str(cm.get(f'/api/obras/{I19}/funcionarios').get_json()), 'mestre não vê a diária')
eu = next(f for f in c.get(f'/api/obras/{I19}/funcionarios').get_json() if f['id'] == ZE)
ok(eu['vinculo'] == 'avulso' and eu['diaria'] == 100 and 'avulso' not in eu, 'gestor vê o vínculo e a diária', eu)
ok(c.put(f'/api/funcionarios/{ZE}', json={'vinculo': 'x'}, headers=H).status_code == 400, 'vínculo inválido é recusado')

print('== TRANSFERÊNCIA ==')
HOJE = '2026-08-18'
with app.app_context():
    ids = {I19, T21}
    T = lambda **d: {'tipo': 'transferir_funcionario', 'dados': d}
    for nome in ('Seu Zé', 'Zé', 'Manteiga', 'seu José'):
        ok(assistente._obra_do_lancamento(T(nome=nome, mover_para_obra=T21), ids) == I19, f'"{nome}" é achado na obra antiga')
    ok(assistente._obra_do_lancamento(T(nome='Fulano', mover_para_obra=T21), ids) == 0, 'desconhecido não é transferido')
    ctx = cat.Contexto(I19, HOJE, True, True, [])
    prontas, avisos = cat.normalizar([
        {'tipo': 'ponto', 'dados': {'data': HOJE, 'equipe_inteira': True}},
        {'tipo': 'funcionario', 'dados': {'nome': 'Pedro Novo', 'funcao': 'Ajudante', 'diaria': 120}},
        T(nome='Seu Zé', mover_para_obra=T21)], ctx)
    ok(ZE not in [r['funcionario_id'] for r in prontas[0]['dados']['registros']], 'quem sai não entra no efetivo da mesma fala')
    ex = Executor(app, um("SELECT id FROM usuarios WHERE papel = 'gestor'")['id'], I19)
    for a in ordenar(prontas): ex.gravar(a, [])
equipe = lambda o: {f['nome']: f for f in c.get(f'/api/obras/{o}/funcionarios').get_json()}
ok('Seu jose(manteiga)' not in equipe(I19) and equipe(T21)['Seu jose(manteiga)']['diaria'] == 100,
   'saiu do I19 e entrou no T21 com a mesma diária')
ok(equipe(I19)['Pedro Novo']['vinculo'] == 'avulso' and equipe(I19)['Pedro Novo']['diaria'] == 120, 'ajudante novo no lugar dele')
ok(custo()[0]['Seu jose(manteiga)']['valor'] == 350, 'o que ele trabalhou no I19 continua lá')
with app.app_context():
    ctx = cat.Contexto(T21, HOJE, True, True, [])
    antes = cat._equipe_inteira({'data': '2026-08-17'}, ctx); depois = cat._equipe_inteira({'data': HOJE}, ctx)
ok(not antes and [x['nome'] for x in depois] == ['Seu jose(manteiga)'], 'não entra no efetivo de antes da mudança', (antes, depois))
NOVO_ZE = equipe(T21)['Seu jose(manteiga)']['id']
ok(c.post(f'/api/funcionarios/{NOVO_ZE}/transferir', json={'obra_id': T21}, headers=H).status_code == 400, 'mesma obra: recusa')
ok(c.post(f'/api/funcionarios/{NOVO_ZE}/transferir', json={'obra_id': I19}, headers=H).status_code == 200
   and equipe(I19)['Seu jose(manteiga)']['id'] == ZE, 'volta para a obra antiga no mesmo cadastro')
ok(cm.post(f'/api/funcionarios/{ZE}/transferir', json={'obra_id': T21}).status_code == 403, 'mestre não transfere')

print('== VÍNCULO PELA FALA ==')
with app.app_context():
    ctx = cat.Contexto(I19, HOJE, True, True, [])
    a, _ = cat.normalizar([{'tipo': 'editar_funcionario', 'dados': {'nome': 'Cosme', 'alteracoes': {'diaria': 140}}}], ctx)
    ok(a[0]['dados']['alteracoes']['diaria'] == 140, '"a diária do Cosme é 140"', a)
    a, _ = cat.normalizar([{'tipo': 'editar_funcionario', 'dados': {'nome': 'Beto', 'alteracoes': {'classe': 'servente_pratico'}}}], ctx)
    ok(a[0]['dados']['alteracoes'] == {'classe': 'servente_pratico'}, '"o Beto é servente prático"', a)
    a, _ = cat.normalizar([{'tipo': 'ponto', 'dados': {'data': HOJE, 'equipe_inteira': True, 'horas': 0, 'justificativa': 'Chuva'}}], ctx)
    ok(all(r['horas'] == 0 and r['justificativa'] == 'Chuva' for r in a[0]['dados']['registros']), 'choveu, ninguém trabalhou')
    executar("INSERT INTO funcionarios (obra_id, nome, ativo) VALUES (?, 'José Carlos', 1)", (I19,))
    ctx = cat.Contexto(I19, HOJE, True, True, [])
    ok(cat.casar_funcionario('Zé', ctx.funcionarios) == 0, 'dois Josés: não escolhe por conta própria')

print('== PONTO ATÉ HOJE ==')
d = lambda *n: [(SEGUNDA + timedelta(days=i)).isoformat() for i in n]
TER, QUI, DOM = d(1)[0], d(3)[0], d(6)[0]
for fala, ref, esperado in (
        ('até hoje ninguém faltou na obra 19', TER, d(0, 1)),
        ('até o dia de hoje, ninguém faltou no I19', TER, d(0, 1)),
        ('essa semana toda até hoje veio todo mundo', QUI, d(0, 1, 2, 3)),
        ('até ontem ninguém faltou', QUI, d(0, 1, 2)),
        ('de terça até hoje veio todo mundo', QUI, d(1, 2, 3)),
        ('desde terça ninguém faltou', QUI, d(1, 2, 3)),
        ('até quarta veio todo mundo', QUI, d(0, 1, 2)),
        ('ontem e hoje veio todo mundo', QUI, d(2, 3)),
        ('até hoje ninguém faltou', DOM, d(0, 1, 2, 3, 4)),
        ('até hoje ninguém faltou', d(0)[0], []),
        ('8 horas de segunda a quinta', TER, d(0, 1, 2, 3)),
        ('ninguém faltou hoje', TER, [])):
    ok(assistente.dias_ditos(fala, ref) == esperado, f'"{fala}" ({ref[8:]})', assistente.dias_ditos(fala, ref))

print('\n' + ('MÃO DE OBRA OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
