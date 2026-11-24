# -*- coding: utf-8 -*-
"""Claude na frente, Gemini de reserva e Grok para áudio. Nada sai para a internet."""
import io
import json
import os
import sys
import tempfile
import urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

from obra import config, create_app
from obra.services import assistente, bot_telegram
from obra.services import ia_provedores as ip

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

app = create_app()
ok(config.IA_ORDEM == ['anthropic', 'gemini'], 'ordem padrão: Claude, depois Gemini', config.IA_ORDEM)

import anthropic
criados, pedidos, estado = [], [], {'stop': 'end_turn', 'erro': None}
class ClienteFalso:
    def __init__(self, **kw): criados.append(kw); self.beta = self; self.messages = self
    def create(self, **kw):
        pedidos.append(kw)
        if estado['erro']: raise estado['erro']
        class B: type = 'text'; text = '{"de": "claude"}'
        class R: stop_reason = estado['stop']; content = [B()]
        return R()
anthropic.Anthropic = ClienteFalso
gemini = []
def gemini_falso(sistema, texto, arquivos, esquema, max_tokens):
    gemini.append(arquivos)
    return 'transcrição do gemini' if esquema is None else '{"de": "gemini"}'
ip.CHAMADAS['gemini'] = gemini_falso
config.ANTHROPIC_API_KEY = 'a'; config.GEMINI_API_KEY = 'g'
PDF = {'nome': 'n.pdf', 'mime': 'application/pdf', 'dados': b'%PDF'}
HEIC = {'nome': 'n.heic', 'mime': 'image/heic', 'dados': b'x'}

print('== CLAUDE NA FRENTE ==')
res, fonte = ip.gerar('regras', 'oi', [PDF], {'type': 'object'})
ok(fonte == 'anthropic' and res == {'de': 'claude'} and not gemini, 'texto e PDF vão para o Claude', (fonte, res))
p = pedidos[-1]
ok(p['system'][0]['cache_control'] == {'type': 'ephemeral'} and p['output_config'] == {'effort': 'low'},
   'regras no cache e esforço baixo', p.get('output_config'))
ok(p['timeout'] <= ip.TEMPO_CLAUDE_COM_RESERVA, 'sobra prazo para a reserva', p['timeout'])
ip.gerar('regras', 'oi', [], {'type': 'object'})
ok(len(criados) == 1 and criados[0]['max_retries'] == 0, 'um cliente só, reaproveitado', len(criados))
estado['erro'] = RuntimeError('fora do ar')
res, fonte = ip.gerar('r', 't', [PDF], {'type': 'object'})
ok(fonte == 'gemini', 'Claude falhou: Gemini assume', fonte)
estado['erro'] = None; estado['stop'] = 'max_tokens'
ok(ip.gerar('r', 't', [], {'type': 'object'})[1] == 'gemini', 'resposta cortada não é aproveitada')
estado['stop'] = 'end_turn'
ok(ip.gerar('r', 't', [HEIC], {'type': 'object'})[1] == 'gemini', 'foto que o Claude não lê vai para o Gemini')
config.ANTHROPIC_API_KEY = ''
ok(ip.gerar('r', 't', [PDF], {'type': 'object'})[1] == 'gemini', 'sem chave do Claude: tudo como hoje')
config.ANTHROPIC_API_KEY = 'a'

print('== ÁUDIO PELO GROK ==')
enviados, grok = [], {'resposta': {'text': 'Oito horas no O13.'}}
def abrir(req, timeout=None):
    enviados.append((req, timeout))
    if isinstance(grok['resposta'], Exception): raise grok['resposta']
    return io.BytesIO(json.dumps(grok['resposta']).encode())
ip.urllib.request.urlopen = abrir
OGG = {'nome': 'a.ogg', 'mime': 'audio/ogg', 'dados': b'OGGDADOS'}
OBRAS = [{'id': 0, 'nome': 'O13'}]
def ouvir(*audios):
    with app.app_context():
        return assistente.transcrever(list(audios), OBRAS, 60)
gemini.clear(); config.XAI_API_KEY = 'x'
ok(ouvir(OGG) == 'Oito horas no O13.' and not gemini, 'o Grok transcreve')
req, tempo = enviados[-1]
corpo = req.data
ok(req.full_url == 'https://api.x.ai/v1/stt' and req.get_header('Authorization') == 'Bearer x' and tempo <= 20,
   'endereço, chave e prazo')
ok(corpo.rindex(b'name="file"') > corpo.rindex(b'name="keyterm"') and b'OGGDADOS' in corpo
   and b'name="language"\r\n\r\npt' in corpo, 'arquivo por último, em português')
ok(b'\r\nO13\r\n' in corpo and b'LOCMAQ' in corpo, 'obras e fornecedores vão como vocabulário')
ip.transcrever_xai(OGG, [f'nome {i}' for i in range(150)] + ['z' * 51], 10)
ok(enviados[-1][0].data.count(b'name="keyterm"') == 100 and b'z' * 51 not in enviados[-1][0].data, 'respeita os limites')
for caso, r in (('limite', urllib.error.HTTPError('u', 429, 'x', {}, io.BytesIO(b'quota'))),
                ('chave inválida', urllib.error.HTTPError('u', 401, 'x', {}, io.BytesIO(b''))),
                ('sem rede', urllib.error.URLError('queda')), ('áudio mudo', {'text': ''})):
    gemini.clear(); grok['resposta'] = r
    ok(ouvir(OGG) == 'transcrição do gemini' and len(gemini) == 1, f'Grok com {caso}: Gemini assume')
config.XAI_API_KEY = ''; enviados.clear(); gemini.clear()
ok(ouvir(OGG) == 'transcrição do gemini' and not enviados, 'sem chave do Grok: tudo como hoje')
config.GEMINI_API_KEY = ''
try:
    ouvir(OGG); ok(False, 'áudio sem Grok nem Gemini')
except ip.IaIndisponivel as erro:
    ok('XAI_API_KEY' in str(erro), 'sem Grok nem Gemini: explica')

print('== COMPARAÇÃO DAS IAS ==')
from obra.services import comparar_ia
c = app.test_client()
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome': 'O13'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio': '2026-08-03'}, headers=H).get_json()['id']
DIA = c.get(f'/api/semanas/{S}/dias').get_json()[0]['id']
from reportlab.pdfgen import canvas
def lancar(status='Pago', fornecedor='Somar', nota=True):
    d = {'categoria': 'Material', 'descricao': 'Aço', 'valor': '100', 'status_pagamento': status, 'fornecedor': fornecedor}
    if nota:
        b = io.BytesIO(); cv = canvas.Canvas(b); cv.drawString(60, 700, 'NOTA'); cv.save(); b.seek(0)
        d['anexos'] = (b, 'n.pdf')
    return c.post(f'/api/dias/{DIA}/despesas', data=d, headers=H, content_type='multipart/form-data').get_json()['id']
ANTIGAS = [lancar() for _ in range(2)]
lancar(status='Em Aberto'); lancar(nota=False)
for _ in range(30): lancar(nota=False)
config.ANTHROPIC_API_KEY = 'a'; config.GEMINI_API_KEY = 'g'
def resposta_ia(valor, fornecedor):
    return json.dumps({'obra_id': O, 'resposta': 'ok', 'acoes': [{'tipo': 'despesa', 'dados': {
        'descricao': 'Aço', 'valor': valor, 'fornecedor': fornecedor, 'anexos': [0]}}]})
def claude_le(*a): return resposta_ia(100, 'SOMAR')
def gemini_le(*a): raise ip.FalhaProvedor('503', 503)
original = dict(ip.CHAMADAS)
ip.CHAMADAS.update(anthropic=claude_le, gemini=gemini_le)
with app.app_context():
    obras = [{'id': O, 'nome': 'O13', 'cliente': '', 'endereco': ''}]
    ids = [n['id'] for n in comparar_ia._notas(obras, 5)]
    antes = len(c.get(f'/api/semanas/{S}/fechamento').get_json()['despesas'])
    r = comparar_ia.comparar(obras)
ok(sorted(ids) == ANTIGAS, 'só notas antigas, pagas e com um arquivo', (ids, ANTIGAS))
ok(all(x['valor'] and x['fornecedor'] for x in r['resultados']['anthropic']), 'acerto conferido com o lançado', r)
ok(all(x['falhou'] for x in r['resultados']['gemini']), 'a reserva não esconde quem falhou', r)
ok(len(c.get(f'/api/semanas/{S}/fechamento').get_json()['despesas']) == antes, 'comparar não grava nada')
texto = comparar_ia.resumo(r)
ok('valor certo: 2/2' in texto and 'não respondeu' in texto, 'resumo mostra os dois', texto)
ip.CHAMADAS.update(gemini=lambda *a: resposta_ia(90, 'Outra'))
with app.app_context():
    r = comparar_ia.comparar(obras, 1)
ok(not r['resultados']['gemini'][0]['valor'] and not r['resultados']['gemini'][0]['fornecedor'], 'erro conta como erro', r)
ok(ip.gerar('r', 't', [], {'type': 'object'})[1] == 'anthropic', 'depois da comparação, tudo volta ao normal')
ip.CHAMADAS.update(original)

print('== ESPERA DO BOT ==')
dormidas, chamados = [], []
bot_telegram.time.sleep = dormidas.append
bot_telegram.vinculo = lambda chat: None
with app.app_context():
    bot_telegram.processar(app, '1', 'oi', '', True, True)
    bot_telegram.processar(app, '1', 'oi', '', True, False)
ok(dormidas == [0.8, 3.0], 'mensagem sozinha espera 0,8 s; álbum, 3 s', dormidas)
bot_telegram.vinculo = lambda chat: {'ok': 1}
bot_telegram._em_segundo_plano = lambda a, f, *args: chamados.append(args)
bot_telegram.telegram_api.enviar = lambda *a, **k: None
def chega(uid, **msg):
    with app.app_context():
        bot_telegram.receber(app, {'update_id': uid, 'message': {'chat': {'id': 9, 'type': 'private'}, **msg}})
    return chamados[-1][-1]
ok(chega(1, text='oi') is True, 'texto: espera curta')
ok(chega(2, caption='nota', photo=[{'file_id': 'a'}]) is True, 'uma foto com legenda: espera curta')
ok(chega(3, caption='notas', photo=[{'file_id': 'b'}], media_group_id='g') is False, 'álbum: espera completa')

print('\n' + ('PROVEDORES OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
