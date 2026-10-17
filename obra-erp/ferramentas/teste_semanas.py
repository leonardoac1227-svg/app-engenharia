# -*- coding: utf-8 -*-
"""Semana sempre começa na segunda; exclusão de semana sem estragar outras semanas."""
import io
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
from obra import config

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")

def no_disco(nome):
    return os.path.exists(os.path.join(config.UPLOAD_DIR, nome))

app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Obra Semanas'}, headers=H).get_json()['id']

def nova_semana(data):
    return c.post(f'/api/obras/{O}/semanas', json={'data_inicio': data}, headers=H)

def dias(semana_id):
    return c.get(f'/api/semanas/{semana_id}/dias').get_json()

print('== SEMANA COMEÇA NA SEGUNDA ==')
r = nova_semana('2026-08-16')   # domingo
ok(r.status_code == 201 and r.get_json()['data_inicio'] == '2026-08-10',
   'domingo vira a segunda da mesma semana (seg 10 a dom 16)', r.get_json())
S1 = r.get_json()['id']
d1 = dias(S1)
ok(d1[0]['dia_semana'] == 'Segunda' and d1[0]['data'] == '2026-08-10', '"Segunda" cai numa segunda', d1[0])
ok(d1[-1]['dia_semana'] == 'Domingo' and d1[-1]['data'] == '2026-08-16', '"Domingo" cai num domingo', d1[-1])

r = nova_semana('2026-08-19')   # quarta
ok(r.get_json()['data_inicio'] == '2026-08-17', 'quarta vira a segunda da semana', r.get_json())
S2 = r.get_json()['id']
r = nova_semana('2026-08-24')   # segunda
ok(r.get_json()['data_inicio'] == '2026-08-24', 'segunda continua segunda', r.get_json())
S3 = r.get_json()['id']
r = c.post(f'/api/obras/{O}/semanas', json={}, headers=H)
ok(r.get_json()['data_inicio'] == '2026-08-31', 'sem data: segunda seguinte à última', r.get_json())
S4 = r.get_json()['id']
ok(nova_semana('2026-08-30').status_code == 409, 'domingo de uma semana existente não cria duplicada')

c.put(f'/api/semanas/{S4}', json={'data_inicio': '2026-09-03'}, headers=H)  # quinta
sem4 = next(s for s in c.get(f'/api/obras/{O}/semanas').get_json() if s['id'] == S4)
ok(sem4['data_inicio'] == '2026-08-31', 'editar a data também acerta para segunda', sem4)

print('== EXCLUIR SEMANA VAZIA ==')
r = c.delete(f'/api/semanas/{S4}', headers=H)
ok(r.status_code == 200 and all(s['id'] != S4 for s in c.get(f'/api/obras/{O}/semanas').get_json()),
   'semana vazia é excluída', r.get_json())
r = c.post(f'/api/obras/{O}/semanas', json={}, headers=H)
ok(r.get_json()['numero'] == 4, 'recriada, volta a ser a semana 4', r.get_json())
S4 = r.get_json()['id']

print('== EXCLUIR SEMANA COM DADOS ==')
dia2 = dias(S2)[0]['id']
f = c.post(f'/api/obras/{O}/funcionarios', json={'nome':'ZÉ'}, headers=H).get_json()['id']
c.put(f'/api/dias/{dia2}/horas', json={'registros':[{'funcionario_id': f, 'horas': 8}]}, headers=H)
foto = c.post(f'/api/dias/{dia2}/fotos', data={'fotos': [(io.BytesIO(PNG), 'obra.png')]}, headers=H,
              content_type='multipart/form-data').get_json()['fotos'][0]
arquivo_foto = foto['url'].rsplit('/', 1)[1] if 'url' in foto else foto['arquivo']
d = c.post(f'/api/dias/{dia2}/despesas', data={'categoria':'Material','descricao':'Cimento','valor':'500',
           'status_pagamento':'Pago','anexos':[(io.BytesIO(PNG), 'nf.png')]}, headers=H,
           content_type='multipart/form-data').get_json()['id']
anexo = c.get(f'/api/dias/{dia2}/despesas').get_json()[0]['anexos'][0]['url'].rsplit('/', 1)[1]

# Nota lançada na semana 1, em 3x semanal: a 2ª parcela vence na semana 2 e a 3ª na semana 3.
dia1 = d1[2]['id']
nota1 = c.post(f'/api/dias/{dia1}/despesas', data={'categoria':'Material','descricao':'Tijolo','valor':'300',
               'status_pagamento':'Em Aberto','vencimento':'2026-08-12','parcelas':'3',
               'parcela_frequencia':'semanal'}, headers=H, content_type='multipart/form-data').get_json()['id']
grupo1 = c.get(f'/api/despesas/{nota1}/parcelas', headers=H).get_json()
parcela_na_s2 = grupo1[1]['id']

# Nota lançada na semana 2, em 3x semanal: parcelas nas semanas 3 e 4; a da semana 3 já paga.
nota2 = c.post(f'/api/dias/{dia2}/despesas', data={'categoria':'Material','descricao':'Areia','valor':'600',
               'status_pagamento':'Em Aberto','vencimento':'2026-08-17','parcelas':'3',
               'parcela_frequencia':'semanal'}, headers=H, content_type='multipart/form-data').get_json()['id']
grupo2 = c.get(f'/api/despesas/{nota2}/parcelas', headers=H).get_json()
c.post(f"/api/despesas/{grupo2[1]['id']}/pagar", json={}, headers=H)

ok(no_disco(arquivo_foto) and no_disco(anexo), 'foto e anexo estão no disco')
r = c.delete(f'/api/semanas/{S2}', headers=H)
res = r.get_json()
ok(r.status_code == 200, 'semana com dados é excluída', res)
ok(res['despesas'] == 2 and res['fotos'] == 1, 'resumo conta lançamentos e fotos', res)
ok(not no_disco(arquivo_foto) and not no_disco(anexo), 'foto e anexo saem do disco')

restantes = c.get(f'/api/despesas/{nota1}/parcelas', headers=H).get_json()
ok([p['parcela_numero'] for p in restantes] == [1, 2, 3],
   'nota de outra semana mantém todas as parcelas', restantes)
ok(res['parcelas_reagendadas'] == 1, 'parcela que vencia na semana apagada foi reagendada', res)
reagendada = next(p for p in restantes if p['id'] == parcela_na_s2)
ok(reagendada['status_pagamento'] == 'Em Aberto' and reagendada['data'] == d1[2]['data'],
   'parcela reagendada continua em aberto, guardada no dia da nota', reagendada)

ok(res['parcelas_removidas'] == 1, 'parcela futura em aberto da nota apagada sai junto', res)
paga = c.get(f'/api/semanas/{S3}/fechamento').get_json()['despesas']
ok(any(x['id'] == grupo2[1]['id'] for x in paga), 'parcela já paga da nota apagada fica no caixa', paga)

r = nova_semana('2026-08-17')
S2b = r.get_json()['id']
fe = c.get(f'/api/semanas/{S2b}/fechamento').get_json()
ok(any(x['id'] == parcela_na_s2 for x in fe['despesas']),
   'recriando a semana, a parcela reagendada volta para ela', [x['descricao'] for x in fe['despesas']])

print('== PERMISSÕES ==')
c.post('/api/usuarios', json={'nome':'Mestre','email':'ms@obra.local','senha':'mestre123',
                              'papel':'mestre','obras':[O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email':'ms@obra.local','senha':'mestre123'})
HM = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
ok(cm.delete(f'/api/semanas/{S3}', headers=HM).status_code == 403, 'mestre não exclui semana')

print('\n' + ('SEMANAS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
