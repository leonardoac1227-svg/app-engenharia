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

print('== RECIBO ANTIGO ENTRA NA SEMANA ATUAL ==')
import re

def lancamento(despesa_id):
    with app.app_context():
        from obra.db import um
        linha = um("""SELECT x.descricao, x.fornecedor, x.numero_nota, x.valor, d.data, s.obra_id
                        FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                       WHERE x.id = ?""", (despesa_id,))
    return dict(linha) if linha else None

def ultimo_id():
    return int(re.findall(r'#(\d+)', enviadas[-1]['text'])[-1])

BOMBA = {'tipo': 'despesa', 'dados': {
    'data': '2025-01-08', 'categoria': 'Material', 'descricao': 'Bomba para esvaziar a piscina - Recibo 1191',
    'fornecedor': 'CASA DO CONSTRUTOR - FEIRA DE SANTANA', 'valor': 129, 'status_pagamento': 'Em Aberto'}}
fila_ia[:] = [{'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [json.loads(json.dumps(BOMBA))]}]
entregar({'text': 'Lance na obra do I19, bomba para esvaziar piscina, nota em aberto'})
ID = ultimo_id()
d = lancamento(ID)
ok(d['data'] == hoje_iso(), 'sem data dita, entra hoje e não na data do recibo', d)
ok(d['numero_nota'] == 'REC 1191' and d['descricao'] == 'Bomba para esvaziar a piscina',
   'o número do recibo sai da descrição e vai para o campo da nota', d)
ok(d['fornecedor'] == 'Casa do Construtor', 'nome do fornecedor padronizado', d)
ok('⚠️' not in enviadas[-1]['text'], 'na semana atual não há aviso', enviadas[-1]['text'])

n = len(despesas(I19))
fila_ia[:] = [{'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [json.loads(json.dumps(BOMBA))]}]
entregar({'text': 'lança de novo esse recibo'})
ok(len(despesas(I19)) == n and 'já foi lançada' in enviadas[-1]['text'], 'o mesmo recibo não entra duas vezes', enviadas[-1]['text'])

fila_ia[:] = [{'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [
    {'tipo': 'despesa', 'dados': {'data': '2025-01-08', 'descricao': 'Lixa', 'valor': 9, 'fornecedor': 'Loja'}}]}]
entregar({'text': 'lixa, do dia 08/01/2025, no I19'})
ok(lancamento(ultimo_id())['data'] == '2025-01-08' and 'Entrou na semana de 06/01/2025' in enviadas[-1]['text'],
   'data dita é respeitada e o bot avisa que foi para outra semana', enviadas[-1]['text'])

print('== CORRIGIR LANÇAMENTO ==')
fila_ia[:] = [{'obra_id': O13, 'resposta': 'Corrigido.', 'acoes': [
    {'tipo': 'editar_despesa', 'obra_id': O13, 'dados': {'despesa_id': ID, 'alteracoes': {'valor': 150}}}]}]
entregar({'text': 'o valor daquela bomba é 150'})
ok(lancamento(ID)['valor'] == 150 and '✅ Corrigido' in enviadas[-1]['text'],
   'corrige mesmo com a conversa em outra obra', enviadas[-1]['text'])
fila_ia[:] = [{'obra_id': I19, 'resposta': 'Movido.', 'acoes': [
    {'tipo': 'editar_despesa', 'dados': {'despesa_id': ID, 'alteracoes': {'mover_para_obra': O13}}}]}]
entregar({'text': 'esse lançamento é do O13'})
ok(lancamento(ID)['obra_id'] == O13 and 'movido para a obra O13' in enviadas[-1]['text'],
   'lançamento muda de obra', enviadas[-1]['text'])
with app.app_context():
    from obra.db import um
    ok(pedidos and 'mover_para_obra' in pedidos[-1]['sistema'], 'a IA sabe mudar de obra')
    dia_o13 = um("""SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
                     WHERE s.obra_id = ? AND d.data = ?""", (O13, hoje_iso()))['id']
    dia_i19 = um("""SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
                     WHERE s.obra_id = ? AND d.data = ?""", (I19, hoje_iso()))['id']
parcelada = c.post(f'/api/dias/{dia_o13}/despesas', data={'categoria': 'Material', 'descricao': 'Tinta', 'valor': '300',
                   'status_pagamento': 'Em Aberto', 'vencimento': hoje_iso(), 'parcelas': '3', 'parcela_frequencia': 'semanal'},
                   headers=H, content_type='multipart/form-data').get_json()['id']
r = c.post(f'/api/despesas/{parcelada}/mover-obra', json={'dia_id': dia_i19}, headers=H)
ok(r.status_code == 400 and 'parcelas' in r.get_json()['erro'], 'parcela não muda de obra sozinha', r.get_json())

print('== STATUS DITO NA FALA ==')
from obra.services.assistente import status_dito
for fala, esperado in [
    ('Lance isso na obra 19, rolo de textura, nota em reembolso', ('Reembolso', '')),
    ('Broca 10mm no engenheiro pro O13 tipo reembolso', ('Reembolso', '')),
    ('paguei do meu bolso o diesel', ('Reembolso', '')),
    ('reembolso pro Luiz, digitalização dos projetos', ('Reembolso', 'Luiz')),
    ('reembolso para mim', ('Reembolso', '')),
    ('nota da Inova em aberto', ('Em Aberto', '')),
    ('essa já está paga', ('Pago', '')),
    ('não foi paga ainda', ('Em Aberto', '')),
    ('uma em aberto e a outra reembolso', ('', '')),
    ('nota do engenheiro', ('', '')),
    ('nota da tinta\n(Complete o lançamento que ficou incompleto — Despesa em 31/10/2026: Tinta · Em Aberto — '
     'usando esta mensagem e os arquivos dele, reenviados aqui. Não duplique.)', ('', '')),
]:
    ok(status_dito(fala) == esperado, f'status de "{fala[:45]}"', status_dito(fala))

fila_ia[:] = [{'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [{'tipo': 'despesa', 'dados': {
    'descricao': 'Rolo de textura para chapisco', 'fornecedor': 'PETROTINTAS E CONSTRUCOES LTDA', 'valor': 28,
    'status_pagamento': 'Em Aberto'}}]}]
entregar({'text': 'Lance isso na obra 19, rolo de textura pro chapisco, nota em reembolso'})
d = lancamento(ultimo_id())
with app.app_context():
    from obra.db import um
    st = um('SELECT status_pagamento, reembolsar_para FROM despesas WHERE id = ?', (ultimo_id(),))
ok(st['status_pagamento'] == 'Reembolso' and st['reembolsar_para'] == config.REEMBOLSO_PADRAO,
   'reembolso dito no fim vale mesmo com a IA dizendo "em aberto"', dict(st))
ok(d['obra_id'] == I19 and d['fornecedor'] == 'Petrotintas', '"obra 19" é o I19 e a razão social fica curta', d)

fila_ia[:] = [{'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [{'tipo': 'despesa', 'dados': {
    'descricao': 'Contrato 1009', 'fornecedor': 'LOCMAQ LOCACAO DE MAQUINAS E EQUIPAMENTOS LTDA', 'valor': 323,
    'data': '2099-11-20'}}]}]
entregar({'text': 'contrato da locmaq no I19'})
d = lancamento(ultimo_id())
with app.app_context():
    venc = um('SELECT vencimento FROM despesas WHERE id = ?', (ultimo_id(),))['vencimento']
ok(d['data'] == hoje_iso() and venc == '2099-11-20' and d['fornecedor'] == 'LOCMAQ',
   'data futura do documento vira vencimento, e o lançamento entra hoje', (d, venc))

print('== JEITO DE FALAR A OBRA ==')
from obra.services.assistente import _obras_citadas
obras_ = [{'id': I19, 'nome': 'I19'}, {'id': O13, 'nome': 'O13'}, {'id': T21, 'nome': 'T21'}]
for fala, esperado in [('lança na obra 19', [I19]), ('é do I-19', [I19]), ('para o i 19', [I19]),
                       ('obra do 13', [O13]), ('19 sacos de cimento', []), ('T21 e O13', [O13, T21])]:
    ok(sorted(_obras_citadas(fala, obras_)) == sorted(esperado), f'obra em "{fala}"', _obras_citadas(fala, obras_))

with app.app_context():
    ok('Rolo de textura' in pedidos[-1]['sistema'] or 'EXEMPLOS' in pedidos[-1]['sistema'],
       'a IA recebe os exemplos do jeito da empresa')
    ok('Nunca deduza "Pago"' in pedidos[-1]['sistema'], 'a regra antiga de "Pago" pelo cupom saiu')

print('== NOTA A PRAZO: CADA VENCIMENTO NA SUA SEMANA ==')
from datetime import date, timedelta
from obra.services.semanas import garantir_semanas_em_dia
from obra.services.parcelamento import dia_de_pagar
HOJE = date.fromisoformat(hoje_iso())
V1, V2 = (HOJE + timedelta(days=15)).isoformat(), (HOJE + timedelta(days=45)).isoformat()

def grupo_de(despesa_id):
    with app.app_context():
        from obra.db import todos, um
        grupo = um('SELECT grupo_parcela FROM despesas WHERE id = ?', (despesa_id,))['grupo_parcela']
        return [dict(r) for r in todos(
            """SELECT x.id, x.descricao, x.valor, x.vencimento, x.parcela_numero, x.parcela_total, d.data
                 FROM despesas x JOIN dias d ON d.id = x.dia_id
                WHERE x.grupo_parcela = ? ORDER BY x.parcela_numero""", (grupo,))] if grupo else []

def semana_atual_tem(despesa_id):
    semana = next(s for s in c.get(f'/api/obras/{I19}/semanas').get_json() if s['atual'])
    return any(x['id'] == despesa_id for x in c.get(f"/api/semanas/{semana['id']}/fechamento").get_json()['despesas'])

MARCON = lambda lista: {'obra_id': I19, 'resposta': 'Lançado.', 'acoes': [{'tipo': 'despesa', 'dados': {
    'descricao': 'Registros e tubos para hidráulica', 'fornecedor': 'MARCON MATERIAIS DE CONSTRUCAO LTDA',
    'numero_nota': '343245', 'valor': 289, 'status_pagamento': 'Em Aberto', 'parcelas_lista': lista}}]}
fila_ia[:] = [MARCON([{'vencimento': V2, 'valor': 139}, {'vencimento': V1, 'valor': 150}])]
entregar({'text': 'Nota da Marcon parcelada, registros e tubos pra hidráulica do I19'})
ID = ultimo_id()
g = grupo_de(ID)
ok([(p['vencimento'], p['valor'], p['parcela_numero']) for p in g] == [(V1, 150, 1), (V2, 139, 2)],
   'cada parcela com a data e o valor impressos, em ordem', g)
ok(g[0]['descricao'].endswith('(1/2)') and lancamento(ID)['fornecedor'] == 'Marcon', 'descrição e fornecedor no padrão', g[0])
ok(not semana_atual_tem(ID), 'a 1ª parcela não conta na semana atual: vence em outra', ID)
ok('2 vencimento(s)' in enviadas[-1]['text'], 'o bot mostra os vencimentos', enviadas[-1]['text'])
with app.test_request_context():
    garantir_semanas_em_dia(HOJE + timedelta(days=16))
g = grupo_de(ID)
ok(g[0]['data'] == dia_de_pagar(date.fromisoformat(V1)).isoformat() and g[1]['data'] != V2,
   'semana do pagamento criada: a parcela vai para a sexta antes do vencimento', g)

fila_ia[:] = [MARCON([{'vencimento': V1, 'valor': 289}])]
entregar({'text': 'nota a prazo da Marcon, conexões, I19'})
g = grupo_de(ultimo_id())
ok(len(g) == 1 and g[0]['vencimento'] == V1 and g[0]['descricao'] == 'Registros e tubos para hidráulica',
   'duplicata única: um lançamento no vencimento, sem "(1/1)"', g)

fila_ia[:] = [MARCON([{'vencimento': V1, 'valor': 289}])]
fila_ia[0]['acoes'][0]['dados']['numero_nota'] = '343246'
entregar({'text': 'nota da Marcon, conexões, I19'})
with app.app_context():
    from obra.db import um
    x = um('SELECT grupo_parcela, vencimento, valor FROM despesas WHERE id = ?', (ultimo_id(),))
ok(not x['grupo_parcela'] and x['vencimento'] == V1 and x['valor'] == 289,
   'sem falar em parcela: lança hoje e guarda só o vencimento', dict(x))

with app.app_context():
    dia_hoje = um("""SELECT d.id FROM dias d JOIN semanas s ON s.id = d.semana_id
                      WHERE s.obra_id = ? AND d.data = ?""", (I19, hoje_iso()))['id']
antes = len(despesas(I19))
r = c.post(f'/api/dias/{dia_hoje}/despesas', data={'categoria': 'Material', 'descricao': 'X', 'valor': '10',
           'parcelas_lista': '[{"vencimento": "2026-13-40", "valor": 10}]'}, headers=H, content_type='multipart/form-data')
ok(r.status_code == 400 and len(despesas(I19)) == antes, 'parcela inválida não deixa a nota gravada pela metade', r.get_json())

print('== PAGAMENTO NA SEXTA ANTES DO VENCIMENTO ==')
from obra.services.parcelamento import dia_de_pagar
for venc, sexta in [('2026-11-11', '2026-11-06'), ('2026-11-18', '2026-11-13'), ('2026-11-13', '2026-11-13'),
                    ('2026-11-15', '2026-11-13'), ('2026-11-16', '2026-11-13'), ('2026-11-14', '2026-11-13')]:
    ok(dia_de_pagar(date.fromisoformat(venc)).isoformat() == sexta, f'vence {venc} → paga {sexta}')

PROXIMA_SEG = HOJE + timedelta(days=7 - HOJE.weekday())
QUARTA_SEGUINTE = PROXIMA_SEG + timedelta(days=9)          # quarta da semana depois da próxima
SEXTA_ANTES = QUARTA_SEGUINTE - timedelta(days=5)          # sexta da próxima semana
fila_ia[:] = [MARCON([{'vencimento': QUARTA_SEGUINTE.isoformat(), 'valor': 289}])]
fila_ia[0]['acoes'][0]['dados']['numero_nota'] = '343300'
entregar({'text': 'nota da Marcon a prazo, I19'})
ID = ultimo_id()
ok(f'(paga {SEXTA_ANTES.strftime("%d/%m/%Y")})' in enviadas[-1]['text'], 'o bot diz em que sexta a conta é paga',
   enviadas[-1]['text'])
with app.test_request_context():
    garantir_semanas_em_dia(SEXTA_ANTES)
g = grupo_de(ID)
ok(g[0]['data'] == SEXTA_ANTES.isoformat() and g[0]['vencimento'] == QUARTA_SEGUINTE.isoformat(),
   'vence na quarta: entra na semana anterior, na sexta, e o vencimento fica o impresso', g)

QUARTA = (HOJE - timedelta(days=HOJE.weekday()) + timedelta(days=2))
dia_ultimo = next(x for x in c.get(f'/api/obras/{I19}/contexto').get_json()['dias'])['id']
r = c.post(f'/api/dias/{dia_hoje}/despesas', data={'categoria': 'Material', 'descricao': 'Tinta', 'valor': '300',
           'status_pagamento': 'Em Aberto', 'vencimento': hoje_iso(), 'parcelas': '2',
           'parcela_frequencia': 'semanal'}, headers=H, content_type='multipart/form-data').get_json()
segunda_parcela = r['parcelamento']['lancamentos'][1]
ok(segunda_parcela['pagar_em'] == dia_de_pagar(date.fromisoformat(segunda_parcela['vencimento'])).isoformat(),
   'parcelamento pelo formulário também paga na sexta', segunda_parcela)
c.put(f"/api/despesas/{segunda_parcela['id']}", json={'vencimento': QUARTA_SEGUINTE.isoformat()}, headers=H)
with app.app_context():
    x = um('SELECT pagar_em FROM despesas WHERE id = ?', (segunda_parcela['id'],))
ok(x['pagar_em'] == SEXTA_ANTES.isoformat(), 'mudar o vencimento move junto o dia de pagamento', dict(x))

print('\n' + ('PADRÕES OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
