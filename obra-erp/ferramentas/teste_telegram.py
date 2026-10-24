# -*- coding: utf-8 -*-
"""Bot do Telegram: vínculo, envio de arquivos + instrução, tentativas, botões e relatórios.

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

def jpeg(cor):
    b = io.BytesIO(); Image.new('RGB', (40, 30), cor).save(b, 'JPEG'); return b.getvalue()
ARQUIVOS = {'nota1': jpeg((200, 200, 200)), 'foto1': jpeg((120, 90, 60)), 'pix1': jpeg((10, 200, 10)),
            'voz1': b'OggS\x00audio', 'pdf1': b'%PDF-1.4\n%nota\n', 'nova': jpeg((1, 2, 3))}

# ------------------------------------------------------------------ Telegram simulado
enviadas, documentos = [], []
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
telegram_api.enviar_arquivo = lambda chat, dados, nome, mime, legenda='': documentos.append((nome, mime, len(dados)))
ultima = lambda: enviadas[-1]['text'] if enviadas else ''
botoes = lambda: [b['callback_data'] for linha in (enviadas[-1].get('reply_markup') or {}).get('inline_keyboard', []) for b in linha]

# ------------------------------------------------------------------ IA simulada
chamadas, fila_ia = [], []
def ia_falsa(sistema, texto, arquivos, esquema, max_tokens):
    chamadas.append({'texto': texto, 'arquivos': arquivos})
    r = fila_ia.pop(0)
    if isinstance(r, Exception):
        raise r
    return json.dumps(r)
ia_provedores.CHAMADAS['gemini'] = ia_falsa
def resposta(obra_id=0, transcricao='', acoes=(), texto='Feito.'):
    return {'transcricao': transcricao, 'obra_id': obra_id, 'resposta': texto, 'acoes': list(acoes)}
A = lambda tipo, **dados: {'tipo': tipo, 'dados': dados}

# ------------------------------------------------------------------ dados
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
OA = c.post('/api/obras', json={'nome': 'O13'}, headers=H).get_json()['id']
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
            """SELECT x.id, x.descricao, x.valor, x.numero_nota, x.status_pagamento,
                      (SELECT COUNT(*) FROM despesa_anexos a WHERE a.despesa_id = x.id) AS anexos
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? ORDER BY x.id""", (obra,))]

def fila_do_chat(lote_vazio=True):
    from obra.db import um
    with app.app_context():
        return um("SELECT COUNT(*) AS n FROM telegram_arquivos WHERE chat_id = ? AND "
                  + ("lote = ''" if lote_vazio else "lote <> ''"), (str(CHAT),))['n']

print('== WEBHOOK E VÍNCULO ==')
ok(entregar({'text': 'oi'}, segredo='errado').status_code == 403, 'segredo errado: 403')
entregar({'text': 'oi'})
ok('privado' in ultima(), 'conversa não vinculada recebe instrução')
cod = c.post('/api/telegram/codigo', headers=H).get_json()
ok(cod['link'] == f"https://t.me/obra_teste_bot?start={cod['codigo']}", 'código com link direto')
entregar({'text': '/start ERRADO'})
ok('inválido' in ultima(), 'código errado recusado')
entregar({'text': f"/start {cod['codigo']}"})
ok('Conectado como' in ultima() and c.get('/api/telegram/status').get_json()['conectado'], 'conecta com o código')

print('== NOTA + ÁUDIO: UMA CHAMADA SÓ ==')
entregar({'photo': [{'file_id': 'x', 'file_size': 1}, {'file_id': 'nota1', 'file_size': 9}]})
ok('Recebido' in ultima(), 'foto sem instrução: avisa que recebeu')
n = len(enviadas)
entregar({'photo': [{'file_id': 'foto1'}]})
ok(len(enviadas) == n, 'segunda foto do mesmo envio não gera outro aviso')
fila_ia[:] = [resposta(OA, 'Obra O13, 2 baldes para a concretagem; a foto é da laje.', [
    A('despesa', categoria='Material', descricao='2 baldes de aço 10 L — concretagem', fornecedor='Almeida e Caires',
      valor=64, status_pagamento='Pago', numero_nota='ORC 31797', anexos=[0]),
    A('foto', anexos=[1], legenda='Laje', ambiente='2º pavimento')])]
n_ia = len(chamadas)
entregar({'voice': {'file_id': 'voz1', 'mime_type': 'audio/ogg'}})
ok(len(chamadas) == n_ia + 1, 'áudio, nota e foto numa chamada só à IA', len(chamadas) - n_ia)
ok([a['mime'] for a in chamadas[-1]['arquivos']] == ['image/jpeg', 'image/jpeg', 'audio/ogg'],
   'a IA recebe as fotos e o áudio', [a['mime'] for a in chamadas[-1]['arquivos']])
ok('O13' in chamadas[-1]['texto'] and 'Prédio Centro' in chamadas[-1]['texto'], 'contexto lista as obras')
textos = [m['text'] for m in enviadas[-3:]]
ok(any(t.startswith('⏳ Analisando 3 arquivo') for t in textos), 'avisa que está analisando', textos)
d = despesas(OA)
ok(len(d) == 1 and d[0]['valor'] == 64 and d[0]['numero_nota'] == 'ORC 31797' and d[0]['anexos'] == 1,
   'despesa gravada com o cupom anexado', d)
r = ultima()
ok('🏗 O13' in r and '🎙 Entendi' in r and '✅ Despesa' in r and '✅ 1 foto' in r, 'resposta resume o que foi feito', r)
ok(fila_do_chat() == 0 and fila_do_chat(False) == 0, 'arquivos processados saem da fila')

print('== FALHA DA IA: TENTATIVA PRESA, SEM MISTURAR ==')
entregar({'photo': [{'file_id': 'nota1'}]})
fila_ia[:] = [ia_provedores.urllib.error.HTTPError('u', 429, 'quota', {}, None)]
entregar({'text': 'obra O13, lança essa nota'})
ok('⚠️' in ultima() and 'limite gratuito' in ultima() and botoes()[0].startswith('re:'),
   'falha avisa com o motivo e o botão "Tentar de novo"', ultima())
botao_repetir = botoes()[0]
ok(fila_do_chat() == 0 and fila_do_chat(False) == 1, 'a nota fica presa à tentativa, fora da fila')
entregar({'photo': [{'file_id': 'nova'}]})
fila_ia[:] = [resposta(OA, acoes=[A('foto', anexos=[0])])]
entregar({'text': 'foto da obra de hoje'})
ok(len(chamadas[-1]['arquivos']) == 1 and chamadas[-1]['arquivos'][0]['dados'] == ARQUIVOS['nova'],
   'envio novo não leva a nota da tentativa que falhou', len(chamadas[-1]['arquivos']))
fila_ia[:] = [resposta(OA, acoes=[A('despesa', descricao='Tinta', valor=420, categoria='Material',
                                    fornecedor='Tintas BR', status_pagamento='Em Aberto', anexos=[0])])]
entregar(callback=botao_repetir)
ok(chamadas[-1]['arquivos'][0]['dados'] == ARQUIVOS['nota1'] and 'lança essa nota' in chamadas[-1]['texto'],
   '"Tentar de novo" usa a mesma nota e a mesma instrução')
ok(despesas(OA)[-1]['descricao'] == 'Tinta' and despesas(OA)[-1]['anexos'] == 1, 'segunda tentativa grava')

print('== NOTA REPETIDA, VALOR FALTANDO, CONFIRMAÇÕES ==')
entregar({'photo': [{'file_id': 'pix1'}]})
fila_ia[:] = [resposta(OA, acoes=[A('despesa', descricao='Baldes', valor=64, fornecedor='Almeida e Caires',
                                    numero_nota='ORC 31797', status_pagamento='Pago', anexos=[0])])]
entregar({'text': 'pix dos baldes'})
ok('já foi lançada' in ultima() and botoes()[0].startswith('ok:'), 'nota repetida: oferece "Lançar mesmo assim"')
n_desp = len(despesas(OA))
entregar(callback=botoes()[0])
ok(len(despesas(OA)) == n_desp + 1 and despesas(OA)[-1]['anexos'] == 1, 'botão lança, com o comprovante')
fila_ia[:] = [resposta(OA, acoes=[A('despesa', descricao='Frete da areia', categoria='Frete', status_pagamento='Pago')])]
entregar({'text': 'paguei o frete da areia'})
ok('faltou valor' in ultima(), 'pede o valor que faltou', ultima())
entregar({'text': 'R$ 350,00'})
ok(despesas(OA)[-1]['descricao'] == 'Frete da areia' and despesas(OA)[-1]['valor'] == 350, 'valor respondido grava')
alvo = despesas(OA)[-1]['id']
fila_ia[:] = [resposta(OA, acoes=[A('excluir_despesa', despesa_id=alvo)])]
entregar({'text': 'apaga o frete'})
ok(any(d['id'] == alvo for d in despesas(OA)) and 'confirme' in ultima(), 'exclusão espera o botão')
entregar(callback=botoes()[0])
ok(not any(d['id'] == alvo for d in despesas(OA)), 'botão confirma a exclusão')
fila_ia[:] = [resposta(OA, acoes=[A('editar_despesa', despesa_id=despesas(OA)[-1]['id'],
                                    alteracoes={'status_pagamento': 'Reembolso'})])]
entregar({'text': 'essa última passa para reembolso'})
ok(despesas(OA)[-1]['status_pagamento'] == 'Reembolso', 'troca de status pela conversa')

print('== RELATÓRIO DO DIA E PONTO POR ÁUDIO ==')
fila_ia[:] = [resposta(OA, 'Ademir fez 27 metros de reboco de manhã e à tarde arestamento.', [
    A('diario', producao_dia='Ademir: execução de 27 m² de reboco (manhã); arestamento (tarde).'),
    A('ponto', registros=[{'funcionario_id': ADEMIR, 'nome': 'Ademir', 'horas': 8}])])]
entregar({'voice': {'file_id': 'voz1'}})
dia = next(x for x in c.get(f'/api/obras/{OA}/contexto').get_json()['dias'] if x['data'] == hoje_iso())
ok('27 m² de reboco' in (dia['producao_dia'] or '') and dia['horas'] == 8, 'diário e ponto gravados', dia)

print('== OBRA NÃO IDENTIFICADA ==')
from obra.db import executar
with app.app_context():
    executar("UPDATE telegram_chats SET obra_id = NULL")
entregar({'document': {'file_id': 'pdf1', 'mime_type': 'application/pdf', 'file_name': 'nota.pdf'}})
fila_ia[:] = [resposta(0, acoes=[A('despesa', descricao='Tinta acrílica', categoria='Material', valor=420,
                                   fornecedor='Tintas BR', status_pagamento='Em Aberto', anexos=[0])])]
entregar({'text': 'nota da tinta'})
ok('Para qual obra' in ultima() and botoes()[0].startswith('ob:'), 'pergunta a obra com botões')
n_ia = len(chamadas)
entregar(callback=next(b for b in botoes() if b.endswith(f':{OB}')))
ok(len(chamadas) == n_ia and despesas(OB) and despesas(OB)[0]['anexos'] == 1,
   'escolhida a obra, grava sem nova ida à IA, com o PDF', despesas(OB))

print('== RELATÓRIOS COMO ARQUIVO E COMANDOS ==')
fila_ia[:] = [resposta(OA, acoes=[A('relatorio', formato='pdf')])]
entregar({'text': 'obra O13, me manda o relatório da semana'})
ok(documentos and documentos[-1][0].endswith('.pdf') and documentos[-1][2] > 500, 'relatório chega como PDF', documentos)
entregar({'text': '/relatorio planilha'})
ok(documentos[-1][0].endswith('.xlsx'), '/relatorio planilha manda o .xlsx', documentos[-1])
telegram_api.chamar = chamar_falso
original_testar = ia_provedores.testar
ia_provedores.testar = lambda: [{'provedor': 'Gemini', 'modelo': 'gemini-flash-lite-latest', 'ok': True, 'ms': 1800},
                                {'provedor': 'Gemini', 'modelo': 'gemini-flash-latest', 'ok': False, 'ms': 35000,
                                 'erro': 'sem conexão: tempo esgotado (35s sem resposta)'}]
entregar({'text': '/teste'})
ok('respondeu em 1.8s' in ultima() and 'tempo esgotado' in ultima(), '/teste mostra velocidade e falhas', ultima())
ia_provedores.testar = original_testar

print('== UMA MENSAGEM POR VEZ E ERROS ==')
from obra.db import um
with app.app_context():
    executar("UPDATE telegram_chats SET ocupado_ate = '2999-01-01T00:00:00' WHERE chat_id = ?", (str(CHAT),))
espera = bot_telegram.ESPERA_MAXIMA; bot_telegram.ESPERA_MAXIMA = 0
n = len(enviadas)
entregar({'text': 'outra coisa'})
textos = [m['text'] for m in enviadas[n:]]
ok(any('Termino a mensagem anterior' in t for t in textos) and 'ainda está sendo processada' in textos[-1],
   'com outra mensagem em andamento: espera e avisa', textos)
bot_telegram.ESPERA_MAXIMA = espera
with app.app_context():
    executar("UPDATE telegram_chats SET ocupado_ate = '2000-01-01T00:00:00' WHERE chat_id = ?", (str(CHAT),))
fila_ia[:] = [resposta(OA, texto='Nada a lançar.')]
entregar({'text': 'tudo certo'})
ok('Nada a lançar' in ultima(), 'trava vencida não bloqueia a conversa')
original = bot_telegram._processar_lote
bot_telegram._processar_lote = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('banco caiu'))
entregar({'text': 'teste'})
bot_telegram._processar_lote = original
ok('banco caiu' in ultima() and botoes()[0].startswith('re:'), 'erro inesperado chega com motivo e botão', ultima())
with app.app_context():
    ok(um("SELECT ocupado_ate FROM telegram_chats WHERE chat_id = ?", (str(CHAT),))['ocupado_ate'] is None,
       'conversa liberada depois da falha')
config.GEMINI_API_KEY = ''; config.ANTHROPIC_API_KEY = 'a'
entregar({'voice': {'file_id': 'voz1'}})
ok('GEMINI_API_KEY' in ultima(), 'áudio sem Gemini: explica', ultima())
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''
entregar({'document': {'file_id': 'z', 'mime_type': 'application/zip', 'file_name': 'a.zip'}})
ok('não leio' in ultima(), 'arquivo não suportado recusado')
n = len(enviadas)
c.post('/telegram/webhook', json={'update_id': 1, 'message': {'chat': {'id': -1, 'type': 'group'}, 'text': 'oi'}},
       headers={'X-Telegram-Bot-Api-Secret-Token': SEGREDO})
ok(len(enviadas) == n, 'mensagens de grupo ignoradas')

print('== MESTRE DE OBRA ==')
c.post('/api/usuarios', json={'nome': 'Mestre', 'email': 'mestre@obra.local', 'senha': 'mestre123',
                              'papel': 'mestre', 'obras': [OA]}, headers=H)
m = app.test_client()
m.post('/api/auth/login', json={'email': 'mestre@obra.local', 'senha': 'mestre123'})
HM = {'X-CSRF-Token': m.get('/api/sessao').get_json()['csrf']}
entregar({'text': f"/start {m.post('/api/telegram/codigo', headers=HM).get_json()['codigo']}"}, chat=777)
antes = len(despesas(OA))
fila_ia[:] = [resposta(OA, acoes=[A('despesa', descricao='Areia', valor=200)])]
entregar({'text': 'comprei areia'}, chat=777)
ok(len(despesas(OA)) == antes and 'contas_em_aberto' not in chamadas[-1]['texto'],
   'mestre: sem despesa e sem dados financeiros', ultima())

print('== DESCONECTAR ==')
ok(c.delete('/api/telegram/conexao', headers=H).status_code == 200, 'desconectar pelo app')
entregar({'text': 'oi'})
ok('privado' in ultima(), 'depois de desconectar, o bot não atende')

from obra.sql import para_postgres
ok('RETURNING' not in para_postgres("INSERT INTO telegram_codigos (codigo, usuario_id, expira_em) VALUES (?, ?, ?)"),
   'Postgres: código de conexão sem RETURNING id')

print('\n' + ('TELEGRAM OK' if not falhas else f'{len(falhas)} FALHA(S)'))
sys.exit(1 if falhas else 0)
