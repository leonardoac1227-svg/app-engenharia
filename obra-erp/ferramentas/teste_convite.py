# -*- coding: utf-8 -*-
"""Convite para o bot (só o link) e reembolso de quem mandou. Nada sai para a internet."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
os.environ["TELEGRAM_BOT_TOKEN"] = "123:falso"

from obra import config, create_app
from obra.db import executar, todos, um
from obra.services import acoes as cat
from obra.services import assistente, bot_telegram, ia_provedores, reembolso, telegram_api

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

enviadas = []
telegram_api.usuario_do_bot = lambda: 'obra_teste_bot'
telegram_api.enviar = lambda chat, texto, botoes=None: enviadas.append((str(chat), texto))
telegram_api.digitando = lambda c: None
bot_telegram.EM_SEGUNDO_PLANO = False; bot_telegram.ATRASO_LOTE = 0; bot_telegram.ATRASO_FOTO = None
app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome': 'O13'}, headers=H).get_json()['id']
uid = [1]
def manda(chat, texto):
    uid[0] += 1
    c.post('/telegram/webhook', json={'update_id': uid[0], 'message': {'chat': {'id': chat, 'type': 'private'}, 'text': texto}},
           headers={'X-Telegram-Bot-Api-Secret-Token': telegram_api.segredo_webhook()})
    return enviadas[-1][1] if enviadas else ''

print('== CONVITE ==')
r = c.post('/api/usuarios/convite', json={'nome': 'Caio', 'papel': 'gestor'}, headers=H)
cv = r.get_json(); CAIO = cv['id']
ok(r.status_code == 201 and cv['link'] == f"https://t.me/obra_teste_bot?start={cv['codigo']}" and cv['link'] in cv['mensagem'],
   'a mensagem já vem com o link', cv)
ok(len(cv['codigo']) == 20, 'código longo: vale dias')
u = next(x for x in c.get('/api/usuarios').get_json() if x['id'] == CAIO)
ok(u['papel'] == 'gestor' and u['convite'] and not u['no_telegram'], 'conta criada, ainda não entrou', u)
ok('Conectado como Caio' in manda(900, f"/start {cv['codigo']}"), 'o link conecta de primeira', enviadas[-1])
ok(next(x for x in c.get('/api/usuarios').get_json() if x['id'] == CAIO)['no_telegram'], 'a tela mostra conectado')
ok('inválido' in manda(901, f"/start {cv['codigo']}"), 'o mesmo link não serve para outra pessoa')
ok('Conectado como Caio' in manda(900, '/start ' + c.post(f'/api/usuarios/{CAIO}/convite', json={}, headers=H).get_json()['codigo'].lower()),
   'link novo, até em minúsculas')
ok(c.post('/api/usuarios/1/convite', json={}, headers=H).status_code == 400, 'não gera link para conta que tem senha')
ok(c.post('/api/usuarios/convite', json={'nome': 'X', 'papel': 'cliente'}, headers=H).status_code == 400, 'cliente não usa o bot')
ok(c.post('/api/usuarios/convite', json={'papel': 'gestor'}, headers=H).status_code == 400, 'sem nome, recusa')
cm = app.test_client()
c.post('/api/usuarios', json={'nome': 'M', 'email': 'cv@obra.local', 'senha': 'mestre123', 'papel': 'mestre', 'obras': [O]}, headers=H)
cm.post('/api/auth/login', json={'email': 'cv@obra.local', 'senha': 'mestre123'})
Hm = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
ok(cm.post('/api/usuarios/convite', json={'nome': 'Y', 'papel': 'gestor'}, headers=Hm).status_code == 403, 'só o gestor convida')
novo = c.post('/api/usuarios/convite', json={'nome': 'Sai', 'papel': 'gestor'}, headers=H).get_json()
c.delete(f"/api/usuarios/{novo['id']}", headers=H)
ok('desativada' in manda(902, f"/start {novo['codigo']}"), 'conta removida: o link não entra')
with app.app_context():
    executar("UPDATE telegram_codigos SET expira_em = '2020-01-01T00:00:00'")
    v = um('SELECT usuario_id FROM telegram_chats WHERE chat_id = ?', ('900',))
ok(v['usuario_id'] == CAIO, 'a conversa é do Caio')
codigo = c.post('/api/telegram/codigo', headers=H).get_json()['codigo']
ok(len(codigo) == 8 and 'Conectado como' in manda(555, f'/start {codigo}'), 'o jeito antigo continua funcionando')

print('== REEMBOLSO DE QUEM MANDOU ==')
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''
fila = []
ia_provedores.CHAMADAS['gemini'] = lambda *a: __import__('json').dumps(fila.pop(0))
def lanca(chat, fala, **dados):
    fila[:] = [{'obra_id': O, 'resposta': 'ok', 'acoes': [{'tipo': 'despesa', 'dados': {
        'descricao': 'Pregos', 'valor': 10, 'fornecedor': 'Loja X', **dados}}]}]
    manda(chat, fala)
    with app.app_context():
        return dict(um('SELECT reembolsar_para, status_pagamento FROM despesas ORDER BY id DESC LIMIT 1'))
LEO = config.REEMBOLSO_PADRAO
for chat, fala, dados, esperado in (
        (900, 'pregos pra O13, reembolso', {}, 'Caio'),
        (900, 'pregos pra O13, eu paguei', {}, 'Caio'),
        (900, 'pregos O13 reembolso Léo', {}, 'Léo'),
        (900, 'pregos O13 reembolso leo', {}, 'Léo'),
        (900, 'pregos O13, reembolso do Léo', {}, 'Léo'),
        (900, 'pregos O13 reembolso', {'reembolsar_para': 'Léo'}, 'Caio'),
        (900, 'pregos O13 reembolso projeto', {}, 'Progetto'),
        (555, 'pregos pra O13, reembolso', {}, LEO),
        (555, 'pregos O13 reembolso', {'reembolsar_para': 'Caio'}, LEO),
        (555, 'pregos O13 reembolso Caio', {}, 'Caio'),
        (555, 'pregos O13 reembolso projeto', {}, 'Progetto'),
        (555, 'reembolso projeto na obra O13, pregos', {}, 'Progetto'),
        (555, 'O13 reembolso, projeto elétrico da sala', {}, LEO),
        (900, 'pregos O13 reembolso progetto', {}, 'Progetto'),
        (900, 'pregos O13 reembolso do projeto', {}, 'Progetto'),
        (900, 'pregos O13 reembolso projeto', {'reembolsar_para': 'Projeto'}, 'Progetto'),
        (555, 'O13 reembolso cimento', {}, LEO),
        (555, 'reembolso pro Luiz, O13', {}, 'Luiz')):
    d = lanca(chat, fala, **dados)
    ok(d['status_pagamento'] == 'Reembolso' and d['reembolsar_para'] == esperado,
       f"{'Caio' if chat == 900 else 'Léo '}: \"{fala}\" → {esperado}", d)
ok(lanca(900, 'broca no engenheiro, O13', fornecedor='O engenheiro')['reembolsar_para'] == 'Caio', 'loja de reembolso: também de quem mandou')
with app.app_context():
    ctx = cat.Contexto(O, '2026-11-24', True, True, [], reembolso=reembolso.padrao_de(CAIO))
    i = um("INSERT INTO despesas (dia_id, categoria, descricao, valor, status_pagamento, criado_em) "
           "SELECT id, 'Material', 'x', 1, 'Em Aberto', '' FROM dias LIMIT 1 RETURNING id")['id']
    from obra.db import get_db; get_db().commit()
    a, _ = cat.normalizar([{'tipo': 'editar_despesa', 'dados': {'despesa_id': i, 'alteracoes': {'status_pagamento': 'Reembolso'}}}], ctx)
ok(a[0]['dados']['alteracoes']['reembolsar_para'] == 'Caio', '"passa para reembolso" na conta do Caio', a)
ok(c.get('/api/inicio').get_json()['reembolso_padrao'] == LEO, 'a tela do gestor continua sugerindo o Léo')

print('\n' + ('CONVITE OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
