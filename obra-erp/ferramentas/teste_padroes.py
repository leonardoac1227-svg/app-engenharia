# -*- coding: utf-8 -*-
"""Padrões de lançamento: fala curta vira despesa completa, sem pisar no que veio informado.

Telegram e IA são simulados: nada sai para a internet.
"""
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
os.environ["TELEGRAM_BOT_TOKEN"] = "123:falso"

from obra import config, create_app
from obra.services import acoes as catalogo
from obra.services import assistente, bot_telegram, ia_provedores, padroes, telegram_api
from obra.utils import hoje_iso

bot_telegram.EM_SEGUNDO_PLANO = False
bot_telegram.ATRASO_LOTE = 0
bot_telegram.ATRASO_FOTO = None
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''

app = create_app(); c = app.test_client()
falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
I19 = c.post('/api/obras', json={'nome': 'I19'}, headers=H).get_json()['id']
O13 = c.post('/api/obras', json={'nome': 'O13'}, headers=H).get_json()['id']
T21 = c.post('/api/obras', json={'nome': 'T21'}, headers=H).get_json()['id']

def conferir(obra, **dados):
    """Passa por onde a resposta da IA passa e devolve os dados da despesa."""
    with app.app_context():
        ctx = catalogo.Contexto(obra, hoje_iso(), True, True, [])
        acoes, _ = catalogo.normalizar([{'tipo': 'despesa', 'dados': dados}], ctx)
    return acoes[0]['dados']

def campos(d):
    return d['fornecedor'], d['valor'], d['status_pagamento'], d['categoria']

print('== ENTULHO ==')
for obra in (I19, O13, T21):
    d = conferir(obra, descricao='Caixa de entulho')
    ok(campos(d) == ('SOS Entulho', 1, 'Em Aberto', 'Material') and 'faltando' not in d, 'entulho em qualquer obra', d)
ok(conferir(I19, descricao='caçamba de entulho', fornecedor='sos')['fornecedor'] == 'SOS Entulho', 'nome do fornecedor padronizado')

print('== FRETE ==')
d = conferir(O13, descricao='Frete referente a pregos')
ok(campos(d) == ('Diego motoboy', 30, 'Em Aberto', 'Frete'), 'frete: Diego motoboy, R$ 30, em aberto', d)
d = conferir(I19, descricao='Frete da areia', fornecedor='Transportadora X', valor=220)
ok(campos(d)[:2] == ('Transportadora X', 220), 'frete de outro transportador fica como veio', d)

print('== AREIA ==')
d = conferir(I19, descricao='3m3 de areia para reboco')
ok(campos(d) == ('Hitalo', 1, 'Em Aberto', 'Material'), 'areia no I19: Hitalo, R$ 1', d)
ok(conferir(I19, descricao='3m3 de areia branca', fornecedor='Ítalo')['fornecedor'] == 'Hitalo', 'fala "Ítalo", lança Hitalo')
ok(conferir(I19, descricao='areia', fornecedor='Italy')['fornecedor'] == 'Hitalo', 'áudio que ouve "Italy" também')
for obra in (O13, T21):
    d = conferir(obra, descricao='Caçamba de areia para reboco')
    ok(d['fornecedor'] == 'Somar' and d['valor'] == 0 and d['faltando'] == ['valor'],
       'areia no O13/T21 sem nota: Somar, e pergunta o valor', d)
d = conferir(T21, descricao='Areia para reboco 1 caçamba', valor=480)
ok(campos(d)[:2] == ('Somar', 480), 'areia no T21 com nota: valor da nota', d)
ok(conferir(O13, descricao='Agregados brita e areia branca 5m cada', valor=1450)['fornecedor'] == 'Somar', 'agregados: Somar')

print('== CIMENTO ==')
d = conferir(I19, descricao='cimento')
ok(campos(d) == ('', 2200, 'Em Aberto', 'Material'), 'cimento: R$ 2.200, sem fornecedor', d)
ok(conferir(O13, descricao='50 sacos de cimento')['valor'] == 2200, 'cimento no O13')
d = conferir(T21, descricao='6 sacos de cimento vindo do O13')
ok(campos(d) == ('', 1, 'Em Aberto', 'Material'), 'cimento vindo de outra obra: R$ 1', d)
ok(conferir(T21, descricao='6 sacos de cimento do o13')['valor'] == 1, 'mesma regra escrito "do o13"')
d = conferir(I19, descricao='10 sacos de cimento')
ok(d['valor'] == 0 and d['faltando'] == ['valor'], 'quantidade fora do padrão pergunta o valor', d)

print('== NADA PISA NO QUE VEIO INFORMADO ==')
d = conferir(I19, descricao='Caixa de entulho', valor=350, status_pagamento='Pago')
ok(campos(d)[:3] == ('SOS Entulho', 350, 'Pago'), 'valor e status ditos valem mais que o padrão', d)
d = conferir(I19, descricao='cimento', valor=2500)
ok(d['valor'] == 2500, 'cimento com outro valor fica com o valor dito', d)
d = conferir(O13, descricao='Discos de corte', fornecedor='Inovacivil Materiais Especializados Ltda', valor=80)
ok(d['fornecedor'] == 'Inova civil' and d['status_pagamento'] == 'Em Aberto', 'razão social vira o nome de sempre', d)
d = conferir(O13, descricao='Broca 10mm', fornecedor='O ENGENHEIRO', valor=15)
ok(campos(d)[:3] == ('O engenheiro', 15, 'Reembolso'), 'loja de reembolso sem status dito', d)
d = conferir(O13, descricao='Broca', fornecedor='Renova', valor=15, status_pagamento='Em Aberto')
ok(d['status_pagamento'] == 'Em Aberto', 'status dito vale mais que o da loja', d)
d = conferir(O13, descricao='Prego', fornecedor='Loja Nova', valor=15)
ok(d['fornecedor'] == 'Loja Nova' and d['status_pagamento'] == 'Em Aberto', 'fornecedor desconhecido fica como veio', d)

print('== O QUE A IA RECEBE ==')
pedidos = []
def ia_falsa(sistema, texto, arquivos, esquema, max_tokens):
    pedidos.append({'sistema': sistema, 'texto': texto})
    if sistema.startswith('Transcreva'):
        return transcricoes.pop(0)
    return json.dumps(fila_ia.pop(0))
transcricoes, fila_ia = [], []
ia_provedores.CHAMADAS['gemini'] = ia_falsa

def lancar(dia_obra, **campos_despesa):
    dados = {'categoria': 'Material', 'status_pagamento': 'Em Aberto', **campos_despesa}
    with app.app_context():
        from obra.db import um
        dia = um("""SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
                     WHERE s.obra_id = ? AND d.data = ?""", (dia_obra, hoje_iso()))
    if not dia:
        c.post(f'/api/obras/{dia_obra}/semanas', json={}, headers=H)
        return lancar(dia_obra, **campos_despesa)
    r = c.post(f"/api/dias/{dia['id']}/despesas", data=dados, headers=H, content_type='multipart/form-data')
    ok(r.status_code == 201, 'lançamento de apoio', r.get_json())

for _ in range(3):
    lancar(O13, descricao='Rebolo', fornecedor='inova', valor='88,50')
lancar(O13, descricao='Item único', fornecedor='Loja', valor='12')
with app.app_context():
    rep = padroes.repetidos([O13], hoje_iso())
ok(len(rep) == 1 and rep[0]['fornecedor'] == 'Inova civil' and rep[0]['vezes'] == 3,
   'o que se repete é contado e o que aparece uma vez fica de fora', rep)

print('== ÁUDIO DE PONTA A PONTA ==')
enviadas = []
def chamar_falso(metodo, **p):
    if metodo == 'sendMessage':
        enviadas.append(p)
    return {'username': 'bot'} if metodo == 'getMe' else True
telegram_api.chamar = chamar_falso
telegram_api.baixar = lambda file_id: b'OggS\x00audio'
codigo = c.post('/api/telegram/codigo', headers=H).get_json()['codigo']
SEGREDO = telegram_api.segredo_webhook()
def entregar(msg, uid=[1]):
    uid[0] += 1
    return c.post('/telegram/webhook', json={'update_id': uid[0], 'message': {'chat': {'id': 9, 'type': 'private'}, **msg}},
                  headers={'X-Telegram-Bot-Api-Secret-Token': SEGREDO})
entregar({'text': f'/start {codigo}'})

def despesas(obra):
    with app.app_context():
        from obra.db import todos
        return [dict(r) for r in todos(
            """SELECT x.descricao, x.fornecedor, x.valor, x.status_pagamento FROM despesas x
                 JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? ORDER BY x.id""", (obra,))]

transcricoes[:] = ['Três metros de areia para o I19.']
fila_ia[:] = [{'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [
    {'tipo': 'despesa', 'dados': {'descricao': '3m3 de areia para reboco'}}]}]
entregar({'voice': {'file_id': 'v', 'mime_type': 'audio/ogg'}})
d = despesas(I19)
ok(d and d[-1]['fornecedor'] == 'Hitalo' and d[-1]['valor'] == 1 and d[-1]['status_pagamento'] == 'Em Aberto',
   'áudio de 3 metros de areia vira lançamento completo', (d, enviadas[-1]['text']))
ok('Hitalo' in pedidos[-1]['sistema'] and 'SOS Entulho' in pedidos[-1]['sistema'], 'a IA recebe as regras')
ok('padroes_recentes' in pedidos[-1]['texto'], 'a IA recebe o que mais se repetiu')

print('\n' + ('PADRÕES OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
