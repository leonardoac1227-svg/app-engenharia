# -*- coding: utf-8 -*-
"""Criar obra pelo bot: o pedido, as perguntas, a criação e o que se pede para fazer nela."""
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
os.environ["TELEGRAM_BOT_TOKEN"] = "123:falso"

from obra import config, create_app
from obra.db import executar, todos, um
from obra.services import assistente, bot_telegram, ia_provedores, telegram_api
from obra.utils import hoje_iso

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

enviadas, vistos, fila = [], [], []
telegram_api.usuario_do_bot = lambda: 'b'
telegram_api.enviar = lambda chat, texto, botoes=None: enviadas.append((texto, botoes or []))
telegram_api.digitando = lambda c: None
telegram_api.responder_botao = lambda *a, **k: None
bot_telegram.EM_SEGUNDO_PLANO = False; bot_telegram.ATRASO_LOTE = 0; bot_telegram.ATRASO_FOTO = None
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''; config.IA_LIMITE_HORA = 999
def ia(sistema, texto, *a):
    vistos.append(texto); return json.dumps(fila.pop(0))
ia_provedores.CHAMADAS['gemini'] = ia
app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
I19 = c.post('/api/obras', json={'nome': 'I19'}, headers=H).get_json()['id']
n = [0]
def envia(corpo):
    n[0] += 1
    c.post('/telegram/webhook', json={'update_id': n[0], **corpo},
           headers={'X-Telegram-Bot-Api-Secret-Token': telegram_api.segredo_webhook()})
def diz(fala, *acoes, chat=7, obra_id=0):
    fila[:] = [{'obra_id': obra_id, 'resposta': 'Certo.', 'acoes': list(acoes)}]
    antes = len(enviadas)
    envia({'message': {'chat': {'id': chat, 'type': 'private'}, 'text': fala}})
    return '\n'.join(t for t, _ in enviadas[antes:])
def toca(prefixo, chat=7):
    dados = next(b['callback_data'] for linha in enviadas[-1][1] for b in linha if b['callback_data'].startswith(prefixo))
    envia({'callback_query': {'id': 'x', 'data': dados, 'message': {'chat': {'id': chat}}}})
    return enviadas[-1][0], dados
def nova(_id=0, **d): return {'tipo': 'criar_obra', 'dados': d, 'obra_id': _id}
def obra(nome):
    with app.app_context():
        r = um('SELECT * FROM obras WHERE nome = ? ORDER BY id DESC', (nome,)); return dict(r) if r else None
def quantas():
    with app.app_context(): return um('SELECT COUNT(*) AS n FROM obras')['n']
envia({'message': {'chat': {'id': 7, 'type': 'private'}, 'text': '/start ' + c.post('/api/telegram/codigo', headers=H).get_json()['codigo']}})

print('== O PEDIDO ==')
for fala in ('quero criar uma obra', 'Quero adicionar uma obra', 'entrou uma obra nova', 'quero cadastrar uma obra nova',
             'cadastra uma nova obra', 'pegamos outra obra nova', 'cria mais uma obra aí', 'abre uma obra pra mim'):
    q = quantas(); r = diz(fala)   # o modelo só respondeu em texto: o pedido vale assim mesmo
    ok('nome da obra' in r and 'data de início' in r and 'previsão de término' in r and quantas() == q, f'"{fala}" pergunta', r)
ok('orçamento' not in r.lower() and 'valor' not in r.lower(), 'não pergunta valores', r)
ok('Certo.' not in r, 'não repete a fala da IA', r)
toca('no:'); ok('Descartado' in enviadas[-1][0], 'dá para cancelar')
for fala in ('a nota da obra nova é essa', 'quem trabalhou na obra?', 'cadastra o Pedro na obra I19', 'obra parada hoje'):
    ok('Vamos cadastrar' not in diz(fala), f'"{fala}" não é obra nova')

print('== AOS POUCOS ==')
diz('entrou uma obra nova', nova())
r = diz('é a O20', nova(nome='O20'))
ok('Já anotei' in r and 'O20' in r and 'nome da obra' not in r.split('Me diga')[1], 'anota e pede só o que falta', r)
ok('Obra nova em cadastro' in vistos[-1], 'a IA sabe que há cadastro aberto')
r = diz('pregos no I19, 50 reais', {'tipo': 'despesa', 'dados': {'descricao': 'Pregos', 'valor': 50}}, obra_id=I19)
ok('Pregos' in r and not obra('O20'), 'outro assunto no meio é lançado e o cadastro espera', r)
r = diz('começa dia primeiro de dezembro e vai até junho, cliente João', nova(data_inicio='2026-12-01', previsao_fim='2027-06-30', cliente='João'))
o = obra('O20')
ok(o and o['data_inicio'] == '2026-12-01' and o['previsao_fim'] == '2027-06-30' and o['cliente'] == 'João' and o['status'] == 'ativa'
   and not o['orcamento_total'], 'cria com tudo que foi dito', (r, o))
ok('Obra criada: O20' in r and 'Ficou sem endereço' in r, 'diz o que criou e o que ficou sem', r)
q = quantas(); diz('e aí', ); ok(quantas() == q and 'em cadastro' not in vistos[-1], 'o cadastro encerrou')
r = diz('hoje no O20 choveu', {'tipo': 'diario', 'dados': {'clima': 'Chuva'}}, obra_id=o['id'])
ok('Diário' in r and 'O20' in r, 'a obra nova já recebe lançamento', r)

print('== DE UMA VEZ ==')
r = diz('cria a obra O21, começa hoje, termina em 8 meses, rua A 10', nova(nome='O21', data_inicio=hoje_iso(), previsao_fim='2027-12-31', endereco='Rua A, 10'))
ok(obra('O21') and 'Vamos cadastrar' not in r, 'com nome e datas não pergunta nada', r)
r = diz('cria a obra O22, não sei as datas, pode criar', nova(nome='O22', pode_criar=True))
ok(obra('O22')['data_inicio'] == hoje_iso() and not obra('O22')['previsao_fim'] and 'início ficou hoje' in r, '"pode criar": cria com o que tem', r)
r = diz('cria a obra O23', nova(nome='O23'))
t, dados = toca('cr:'); ok('Obra criada: O23' in t and obra('O23'), 'botão "Criar assim mesmo"', t)
q = quantas(); envia({'callback_query': {'id': 'x', 'data': dados, 'message': {'chat': {'id': 7}}}})
ok(quantas() == q, 'toque duplo não cria duas')
diz('nova obra', nova()); r = diz('deixa pra lá', nova(cancelar=True))
ok('cancelado' in r and quantas() == q, '"deixa pra lá" cancela', r)
r = diz('cria a obra i-19', nova(nome='i-19'))
ok('já existe' in r and quantas() == q, 'não repete obra que já existe', r)
diz('cria a O24', nova(nome='O24')); r = diz('de 10/12/2026 a 01/11/2026', nova(data_inicio='2026-12-10', previsao_fim='2026-11-01'))
ok(not obra('O24') and 'previsão de término' in r.split('Me diga')[1], 'término antes do início: pergunta de novo', r)
diz('termina em abril', nova(previsao_fim='2027-04-30')); ok(obra('O24')['previsao_fim'] == '2027-04-30', 'e cria com a data certa')
diz('obra nova O25', nova(nome='O25')); diz('cliente Ana', nova(cliente='Ana')); diz('rua B', nova(endereco='Rua B'))
ok(obra('O25') and obra('O25')['cliente'] == 'Ana', 'não fica perguntando sem fim: na terceira, cria')

print('== O QUE FAZER NELA ==')
r = diz('cria a obra O30 de dezembro a maio e cadastra o Pedro, armador, nela',
        nova(nome='O30', data_inicio='2026-12-01', previsao_fim='2027-05-31'), {'tipo': 'funcionario', 'dados': {'nome': 'Pedro', 'funcao': 'Armador'}})
def equipe(nome):
    with app.app_context(): return [x['nome'] for x in todos('SELECT nome FROM funcionarios WHERE obra_id = ?', (obra(nome)['id'],))]
ok(equipe('O30') == ['Pedro'], 'cadastra a pessoa na obra criada', (r, equipe('O30')))
diz('cria a O31 e cadastra o Zeca nela', nova(nome='O31'), {'tipo': 'funcionario', 'dados': {'nome': 'Zeca'}})
with app.app_context(): ok(not um("SELECT 1 AS x FROM funcionarios WHERE nome = 'Zeca'"), 'sem a obra, espera')
toca('cr:'); ok(equipe('O31') == ['Zeca'], 'criada, faz o que foi pedido antes', equipe('O31'))

print('== QUEM PODE ==')
c.post('/api/usuarios', json={'nome': 'M', 'email': 'on@obra.local', 'senha': 'mestre123', 'papel': 'mestre', 'obras': [I19]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email': 'on@obra.local', 'senha': 'mestre123'})
Hm = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
envia({'message': {'chat': {'id': 8, 'type': 'private'}, 'text': '/start ' + cm.post('/api/telegram/codigo', headers=Hm).get_json()['codigo']}})
q = quantas(); r = diz('quero criar uma obra X', nova(nome='X', pode_criar=True), chat=8)
ok(quantas() == q and 'permissão' in r, 'mestre não cria obra', r)
with app.app_context():
    executar('UPDATE obras SET arquivada = 1')
    from obra.db import get_db; get_db().commit()
r = diz('cria a obra Primeira', nova(nome='Primeira', pode_criar=True))
ok(obra('Primeira'), 'gestor sem nenhuma obra ativa também cria', r)

print('== PRONÚNCIA ==')
from obra.services import pronuncia
for f in ('Saint Remy', 'saint remi', 'São Remi', 'san remy', 'Sanremi', 'sant rémi', 'Saint-Rémy', 'Santo Remir', 'são rémi',
          'sem remi', 'cem remi', 'sen remy', 'San Hemi', 'saint rami', 'São Renê', 'San Remo', 'som remi', 'sanremy', 'Sain Remie',
          'Sonnet', 'sonete', 'soneto', 'Sonet', 'sonéti', 'soneti'):
    r = pronuncia.corrigir(f'pregos no {f}, 50 reais', ['I19', 'Saint Remy'])
    ok(r == 'pregos no Saint Remy, 50 reais', f'"{f}" é o Saint Remy', r)
for f in ('obra do Remi', 'obra Saint', 'obra só net'):
    ok('Saint Remy' in pronuncia.corrigir(f'ponto na {f} hoje', ['Saint Remy']), f'"{f}" é o Saint Remy')
for f in ('paguei só net esse mês', 'o Remi faltou hoje', 'são 50 reais de prego', 'sem remessa', 'santo antônio', 'sem reembolso',
          'cem remendos', 'são remédios', 'santa mônica', 'sem rampa', 'a senha', 'são ramais de água', 'sem rima'):
    ok(pronuncia.corrigir(f, ['Saint Remy', 'I19']) == f, f'"{f}" não vira obra', pronuncia.corrigir(f, ['Saint Remy']))
ok(pronuncia.corrigir('no soneto e no san remi', ['Saint Remy']) == 'no Saint Remy e no Saint Remy', 'as duas escritas são a mesma obra')
ok(pronuncia.corrigir('no soneto', ['I19']) == 'no soneto', 'sem a obra cadastrada, não mexe')
ok('san remi' in pronuncia.dica_de_audio(['Saint Remy']) and not pronuncia.dica_de_audio(['I19']), 'a transcrição sabe como se fala')
c.post('/api/obras', json={'nome': 'Saint Remy'}, headers=H)
r = diz('pregos no são rémi, 80 reais', {'tipo': 'despesa', 'dados': {'descricao': 'Pregos', 'valor': 80}})
ok('Saint Remy' in r and 'no Saint Remy' in vistos[-1], 'o bot lança no Saint Remy e a IA já lê o nome certo', r)
q = quantas(); r = diz('cria a obra san remi', nova(nome='san remi'))
ok('já existe' in r and quantas() == q, 'não cria um segundo Saint Remy', r)
r = diz('quero criar uma obra, o nome é Sonnet', nova(nome='Sonnet', pode_criar=True))
ok('Saint Remy já existe' in r and quantas() == q, 'nem uma obra "Sonnet"', r)
r = diz('areia no Sonnet, 90', {'tipo': 'despesa', 'dados': {'descricao': 'Areia', 'valor': 90}})
ok('Saint Remy' in r, '"Sonnet" lança na Saint Remy', r)

print('\n' + ('OBRA NOVA OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
