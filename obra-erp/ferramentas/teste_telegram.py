# -*- coding: utf-8 -*-
"""Bot do Telegram: vínculo, lote de arquivos, áudio, gravação e botões.

Telegram e IA são simulados: nada sai para a internet.
"""
import io
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
os.environ["TELEGRAM_BOT_TOKEN"] = "123:falso"
from PIL import Image

from obra import config, create_app
from obra.services import bot_telegram, ia_provedores, telegram_api
from obra.utils import hoje_iso

bot_telegram.EM_SEGUNDO_PLANO = False
bot_telegram.ATRASO_LOTE = 0
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''

app = create_app(); c = app.test_client()
falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

def jpeg():
    b = io.BytesIO(); Image.new('RGB', (40, 30), (120, 90, 60)).save(b, 'JPEG'); return b.getvalue()

ARQUIVOS = {'nota1': jpeg(), 'foto1': jpeg(), 'pix1': jpeg(), 'voz1': b'OggS\x00audio',
            'pdf1': b'%PDF-1.4\n%nota\n'}

# ------------------------------------------------------------------ Telegram simulado
enviadas = []
def chamar_falso(metodo, **p):
    if metodo == 'sendMessage':
        enviadas.append(p)
    if metodo == 'getMe':
        return {'username': 'obra_teste_bot'}
    if metodo == 'getWebhookInfo':
        return {'url': 'https://app/telegram/webhook'}
    return True
telegram_api.chamar = chamar_falso
telegram_api.baixar = lambda file_id: ARQUIVOS[file_id]

def ultima():
    return enviadas[-1]['text'] if enviadas else ''

# ------------------------------------------------------------------ IA simulada
chamadas = []
fila_ia = []  # respostas na ordem; ouvir e interpretar
def ia_falsa(sistema, texto, arquivos, esquema, max_tokens):
    chamadas.append({'texto': texto, 'arquivos': arquivos, 'esquema': esquema})
    resposta = fila_ia.pop(0)
    if isinstance(resposta, Exception):
        raise resposta
    return json.dumps(resposta)
ia_provedores.CHAMADAS['gemini'] = ia_falsa

def acao(**k):
    base = {x: '' for x in ('data', 'resumo', 'producao_dia', 'clima', 'observacoes', 'categoria', 'descricao',
                            'fornecedor', 'status_pagamento', 'numero_nota', 'vencimento', 'reembolsar_para',
                            'parcela_frequencia', 'legenda', 'ambiente', 'nome', 'funcao')}
    base.update({'despesa_id': 0, 'valor': 0, 'parcelas': 0, 'registros': [], 'anexos': []})
    base.update(k); return base

# ------------------------------------------------------------------ dados
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
OA = c.post('/api/obras', json={'nome': 'Residência Alphaville'}, headers=H).get_json()['id']
OB = c.post('/api/obras', json={'nome': 'Prédio Centro'}, headers=H).get_json()['id']
ADEMIR = c.post(f'/api/obras/{OA}/funcionarios', json={'nome': 'Ademir Souza', 'funcao': 'Pedreiro'}, headers=H).get_json()['id']
SEGREDO = telegram_api.segredo_webhook()
CHAT = 555

def entregar(msg=None, callback=None, segredo=SEGREDO, chat=CHAT, uid=[1000]):
    uid[0] += 1
    update = {'update_id': uid[0]}
    if callback:
        update['callback_query'] = {'id': 'cb', 'data': callback, 'message': {'chat': {'id': chat}}}
    else:
        update['message'] = {'chat': {'id': chat, 'type': 'private'}, **msg}
    return c.post('/telegram/webhook', json=update, headers={'X-Telegram-Bot-Api-Secret-Token': segredo})

def despesas(obra):
    from obra.db import todos
    with app.app_context():
        return [dict(r) for r in todos(
            """SELECT x.id, x.descricao, x.valor, x.numero_nota, x.fornecedor, x.status_pagamento,
                      (SELECT COUNT(*) FROM despesa_anexos a WHERE a.despesa_id = x.id) AS anexos
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? ORDER BY x.id""", (obra,))]

print('== WEBHOOK E VÍNCULO ==')
ok(entregar({'text': 'oi'}, segredo='errado').status_code == 403, 'segredo errado: 403')
r = entregar({'text': 'oi'})
ok(r.status_code == 200 and 'privado' in ultima(), 'conversa não vinculada recebe instrução', ultima())
st = c.get('/api/telegram/status').get_json()
ok(st['configurado'] and st['bot'] == 'obra_teste_bot' and not st['conectado'], 'status antes de conectar', st)
cod = c.post('/api/telegram/codigo', headers=H).get_json()
ok(cod['link'] == f"https://t.me/obra_teste_bot?start={cod['codigo']}", 'código com link direto', cod)
entregar({'text': '/start NAOEXISTE'})
ok('inválido' in ultima(), 'código errado é recusado', ultima())
entregar({'text': f"/start {cod['codigo']}"})
ok('Conectado como' in ultima(), 'conecta com o código', ultima())
ok(c.get('/api/telegram/status').get_json()['conectado'], 'app mostra a conversa conectada')
entregar({'text': f"/start {cod['codigo']}"}, chat=999)
ok('inválido' in ultima(), 'código vale uma vez só', ultima())

from obra.sql import para_postgres
ok('RETURNING' not in para_postgres("INSERT INTO telegram_codigos (codigo, usuario_id, expira_em) VALUES (?, ?, ?)"),
   'Postgres: código de conexão sem RETURNING id (tabela não tem id)')

print('== NOTA + ÁUDIO (VÁRIAS OBRAS) ==')
n_antes = len(enviadas)
entregar({'photo': [{'file_id': 'x', 'file_size': 10}, {'file_id': 'nota1', 'file_size': 999}]})
ok('Recebido' in ultima(), 'foto sem instrução: avisa que recebeu')
entregar({'photo': [{'file_id': 'foto1', 'file_size': 999}]})
ok(len(enviadas) == n_antes + 1, 'segunda foto do mesmo envio não gera outro aviso', len(enviadas) - n_antes)
fila_ia[:] = [
    {'transcricao': 'Esse cimento é da obra do Alphaville, 20 sacos CP-II. E a foto é da laje de hoje.', 'obra_id': OA},
    {'resposta': 'Lancei a nota e a foto.', 'acoes': [
        acao(tipo='despesa', data=hoje_iso(), categoria='Material', descricao='20 sacos de cimento CP-II',
             fornecedor='Casa do Construtor', valor=1380, status_pagamento='Pago', numero_nota='4521', anexos=[0]),
        acao(tipo='foto', data=hoje_iso(), anexos=[1], legenda='Laje', ambiente='2º pavimento')]},
]
entregar({'voice': {'file_id': 'voz1', 'mime_type': 'audio/ogg'}})
ok(chamadas[-2]['arquivos'][0]['mime'] == 'audio/ogg', 'áudio vai para a transcrição')
ok('Residência Alphaville' in chamadas[-2]['texto'] and 'Prédio Centro' in chamadas[-2]['texto'],
   'transcrição recebe a lista de obras')
ok('(áudio) Esse cimento' in chamadas[-1]['texto'] and 'Ademir Souza' in chamadas[-1]['texto'],
   'pedido principal recebe a transcrição e o contexto da obra certa')
ok(all(a['mime'] != 'audio/ogg' for a in chamadas[-1]['arquivos']) and len(chamadas[-1]['arquivos']) == 2,
   'pedido principal recebe só nota e foto', [a['mime'] for a in chamadas[-1]['arquivos']])
d = despesas(OA)
ok(len(d) == 1 and d[0]['valor'] == 1380 and d[0]['numero_nota'] == '4521' and d[0]['anexos'] == 1,
   'despesa gravada com a nota anexada', d)
dias = c.get(f'/api/obras/{OA}/contexto').get_json()['dias']
ok(sum(x['fotos'] for x in dias) == 1, 'foto anexada ao diário de hoje', [x['fotos'] for x in dias])
resp = ultima()
ok('🏗 Residência Alphaville' in resp and '🎙 Entendi' in resp and '✅ Despesa' in resp and '✅ 1 foto' in resp,
   'resposta resume o que foi gravado', resp)
with app.app_context():
    from obra.db import um
    ok(um("SELECT COUNT(*) AS n FROM telegram_arquivos")['n'] == 0, 'lote processado sai da fila')

print('== PIX REPETIDO: BOTÃO "LANÇAR MESMO ASSIM" ==')
entregar({'photo': [{'file_id': 'pix1'}]})
fila_ia[:] = [
    {'transcricao': '', 'obra_id': 0},
    {'resposta': 'PIX lido.', 'acoes': [acao(tipo='despesa', data=hoje_iso(), categoria='Material',
        descricao='Cimento', fornecedor='Casa do Construtor', valor=1380, status_pagamento='Pago',
        numero_nota='4521', anexos=[0])]},
]
entregar({'text': 'pix do cimento'})
ok('já foi lançada' in ultima() and enviadas[-1].get('reply_markup'), 'nota repetida: avisa e oferece botão', ultima())
ok(chamadas[-2]['arquivos'] == [], 'sem áudio: transcrição só identifica a obra')
botao = enviadas[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
entregar(callback=botao)
d = despesas(OA)
ok(len(d) == 2 and d[1]['anexos'] == 1, 'botão lança mesmo assim, com o comprovante', d)
n_msgs = len(enviadas)
entregar(callback=botao)
ok(len(despesas(OA)) == 2 and len(enviadas) == n_msgs, 'segundo toque no botão não lança de novo', ultima())

print('== DESPESA SEM VALOR ==')
fila_ia[:] = [{'transcricao': '', 'obra_id': OA},
              {'resposta': 'Qual o valor?', 'acoes': [acao(tipo='despesa', descricao='Frete da areia',
               categoria='Frete', fornecedor='Zé do Caminhão', status_pagamento='Pago')]}]
entregar({'text': 'paguei o frete da areia pro Zé do Caminhão'})
ok('faltou valor' in ultima(), 'pede o valor que faltou', ultima())
entregar({'text': 'R$ 350,00'})
d = despesas(OA)
ok(d[-1]['descricao'] == 'Frete da areia' and d[-1]['valor'] == 350, 'valor respondido completa e grava', d[-1])

print('== EXCLUSÃO PEDE CONFIRMAÇÃO ==')
alvo = d[-1]['id']
fila_ia[:] = [{'transcricao': '', 'obra_id': OA},
              {'resposta': 'Confirma?', 'acoes': [acao(tipo='excluir_despesa', despesa_id=alvo)]}]
entregar({'text': 'apaga o frete da areia'})
ok(len(despesas(OA)) == 3 and 'Confirme' in ultima(), 'exclusão não acontece sem o botão')
cb = enviadas[-1]['reply_markup']['inline_keyboard'][0][0]['callback_data']
entregar(callback=cb)
ok(len(despesas(OA)) == 2 and '✅ Excluído' in ultima(), 'botão confirma a exclusão', ultima())

print('== RELATÓRIO DO DIA POR ÁUDIO ==')
fila_ia[:] = [
    {'transcricao': 'Ademir fez 27 metros de reboco de manhã e à tarde arestamento.', 'obra_id': OA},
    {'resposta': 'Diário e ponto lançados.', 'acoes': [
        acao(tipo='diario', data=hoje_iso(), producao_dia='Ademir: execução de 27 m² de reboco (manhã); arestamento (tarde).'),
        acao(tipo='ponto', data=hoje_iso(), registros=[{'funcionario_id': ADEMIR, 'nome': 'Ademir', 'horas': 8, 'justificativa': ''}])]},
]
entregar({'voice': {'file_id': 'voz1', 'mime_type': 'audio/ogg'}})
dia = next(x for x in c.get(f'/api/obras/{OA}/contexto').get_json()['dias'] if x['data'] == hoje_iso())
ok('27 m² de reboco' in (dia['producao_dia'] or '') and dia['horas'] == 8, 'diário e ponto gravados', dia)
ok('Ademir Souza 8h' in ultima(), 'resposta mostra o ponto', ultima())

print('== OBRA NÃO IDENTIFICADA ==')
with app.app_context():
    from obra.db import executar
    executar("UPDATE telegram_chats SET obra_id = NULL")
entregar({'document': {'file_id': 'pdf1', 'mime_type': 'application/pdf', 'file_name': 'nota.pdf'}})
fila_ia[:] = [{'transcricao': '', 'obra_id': 0}]
entregar({'text': 'nota da tinta'})
ok('Para qual obra' in ultima(), 'pergunta a obra com botões', ultima())
with app.app_context():
    ok(um("SELECT COUNT(*) AS n FROM telegram_arquivos WHERE lote = ''")['n'] == 1, 'arquivo continua guardado')
fila_ia[:] = [{'transcricao': '', 'obra_id': 0},
              {'resposta': 'Ok.', 'acoes': [acao(tipo='despesa', descricao='Tinta acrílica', categoria='Material',
               valor=420, status_pagamento='Em Aberto', fornecedor='Tintas BR', anexos=[0])]}]
entregar(callback=f'obra:{OB}')
d = despesas(OB)
ok(len(d) == 1 and d[0]['anexos'] == 1, 'escolhida a obra, processa a mensagem guardada com o PDF', d)
ok('nota da tinta' in chamadas[-1]['texto'], 'texto original reaproveitado')

print('== FALHAS ==')
entregar({'photo': [{'file_id': 'nota1'}]})
fila_ia[:] = [ia_provedores.urllib.error.HTTPError('u', 429, 'quota', {}, None)]
entregar({'text': 'lança isso'})
ok('⚠️' in ultima(), 'IA fora: avisa', ultima())
with app.app_context():
    ok(um("SELECT COUNT(*) AS n FROM telegram_arquivos WHERE lote = ''")['n'] == 1, 'IA fora: arquivo volta para a fila')
config.GEMINI_API_KEY = ''; config.ANTHROPIC_API_KEY = 'a'
entregar({'voice': {'file_id': 'voz1', 'mime_type': 'audio/ogg'}})
ok('GEMINI_API_KEY' in ultima(), 'áudio sem Gemini: explica', ultima())
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''
with app.app_context():
    executar("DELETE FROM telegram_arquivos")
entregar({'document': {'file_id': 'z', 'mime_type': 'application/zip', 'file_name': 'a.zip'}})
ok('não leio' in ultima(), 'arquivo não suportado é recusado', ultima())
n_msgs = len(enviadas)
c.post('/telegram/webhook', json={'update_id': 1, 'message': {'chat': {'id': -1, 'type': 'group'}, 'text': 'oi'}},
       headers={'X-Telegram-Bot-Api-Secret-Token': SEGREDO})
ok(len(enviadas) == n_msgs, 'mensagens de grupo são ignoradas')

print('== OBRA ESCRITA NA MENSAGEM ==')
n_chamadas = len(chamadas)
fila_ia[:] = [{'resposta': 'Ok.', 'acoes': []}]
entregar({'text': 'na obra prédio centro chegou o caminhão de areia'})
ok(len(chamadas) == n_chamadas + 1 and '🏗 Prédio Centro' in ultima(),
   'obra citada pelo nome: uma chamada à IA só (sem a etapa de identificar)', ultima())

print('== AVISO IMEDIATO E UMA MENSAGEM POR VEZ ==')
n_msgs = len(enviadas)
fila_ia[:] = [{'transcricao': '', 'obra_id': OA}, {'resposta': 'Nada a lançar.', 'acoes': []}]
entregar({'text': 'tudo certo hoje'})
textos = [m['text'] for m in enviadas[n_msgs:]]
ok(textos and textos[0].startswith('⏳ Recebido, analisando'), 'avisa que está analisando antes da resposta', textos)
with app.app_context():
    ok(um("SELECT ocupado_ate FROM telegram_chats WHERE chat_id = ?", (str(CHAT),))['ocupado_ate'] is None,
       'conversa liberada depois de processar')
    executar("UPDATE telegram_chats SET ocupado_ate = '2999-01-01T00:00:00' WHERE chat_id = ?", (str(CHAT),))
espera = bot_telegram.ESPERA_MAXIMA
bot_telegram.ESPERA_MAXIMA = 0
n_msgs = len(enviadas)
entregar({'text': 'outra coisa'})
textos = [m['text'] for m in enviadas[n_msgs:]]
ok(any('Termino a mensagem anterior' in t for t in textos) and 'ainda está sendo processada' in textos[-1],
   'com outra mensagem em andamento: espera e avisa', textos)
bot_telegram.ESPERA_MAXIMA = espera
with app.app_context():
    executar("UPDATE telegram_chats SET ocupado_ate = '2000-01-01T00:00:00' WHERE chat_id = ?", (str(CHAT),))
fila_ia[:] = [{'transcricao': '', 'obra_id': OA}, {'resposta': 'Ok.', 'acoes': []}]
entregar({'text': 'e agora?'})
ok(ultima().startswith('🏗'), 'trava vencida (processo morto) não bloqueia a conversa', ultima())

print('== ERRO INESPERADO SEMPRE AVISA ==')
original = bot_telegram._processar_lote
def explode(*a, **k): raise RuntimeError('banco caiu')
bot_telegram._processar_lote = explode
entregar({'text': 'teste'})
bot_telegram._processar_lote = original
ok('Algo deu errado' in ultima() and 'banco caiu' in ultima(), 'falha inesperada chega ao chat com o motivo', ultima())
with app.app_context():
    ok(um("SELECT ocupado_ate FROM telegram_chats WHERE chat_id = ?", (str(CHAT),))['ocupado_ate'] is None,
       'conversa liberada mesmo depois da falha')

print('== MESTRE DE OBRA ==')
c.post('/api/usuarios', json={'nome': 'Mestre', 'email': 'mestre@obra.local', 'senha': 'mestre123',
                              'papel': 'mestre', 'obras': [OA]}, headers=H)
m = app.test_client()
m.post('/api/auth/login', json={'email': 'mestre@obra.local', 'senha': 'mestre123'})
HM = {'X-CSRF-Token': m.get('/api/sessao').get_json()['csrf']}
cm = m.post('/api/telegram/codigo', headers=HM).get_json()['codigo']
entregar({'text': f'/start {cm}'}, chat=777)
antes = len(despesas(OA))
fila_ia[:] = [{'transcricao': 'comprei areia', 'obra_id': 0},
              {'resposta': 'ok', 'acoes': [acao(tipo='despesa', descricao='Areia', valor=200, categoria='Material')]}]
entregar({'voice': {'file_id': 'voz1'}}, chat=777)
ok(len(despesas(OA)) == antes, 'mestre: despesa não é lançada pelo bot', ultima())
ok('contas_em_aberto' not in chamadas[-1]['texto'], 'mestre: IA não recebe dados financeiros')

print('== DESCONECTAR ==')
ok(c.delete('/api/telegram/conexao', headers=H).status_code == 200, 'desconectar pelo app')
entregar({'text': 'oi'})
ok('privado' in ultima(), 'depois de desconectar, o bot não atende', ultima())

print('\n' + ('TELEGRAM OK' if not falhas else f'{len(falhas)} FALHA(S)'))
sys.exit(1 if falhas else 0)
