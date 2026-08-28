# -*- coding: utf-8 -*-
"""Entrega de anexos: disco e Supabase Storage (simulado), imagens e PDFs."""
import io, os, sys
os.environ['DATA_DIR'] = '/tmp/anx_dados'
sys.path.insert(0, '/home/claude/obra-erp')
from PIL import Image

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

# --- Supabase Storage simulado: guarda os bytes em memória ---
import obra.storage as storage_mod
BUCKET = {}

def enviar_falso(nome, conteudo, tipo=None):
    BUCKET[nome] = conteudo

def baixar_falso(nome):
    return BUCKET.get(nome)

def apagar_falso(nome):
    BUCKET.pop(nome, None)

from obra import create_app, config
app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Anexos'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={}, headers=H).get_json()['id']
DIA = c.get(f'/api/semanas/{S}/dias').get_json()[0]['id']

def foto():
    b = io.BytesIO(); Image.new('RGB',(2000,1500),(120,140,120)).save(b,'JPEG'); b.seek(0); return b

print('== MODO DISCO ==')
r = c.post(f'/api/dias/{DIA}/fotos', data={'fotos': (foto(), 'obra.jpg')},
           headers=H, content_type='multipart/form-data')
url = r.get_json()['fotos'][0]['url']
resp = c.get(url)
ok(resp.status_code == 200, 'foto abre do disco', resp.status_code)
ok(resp.headers['Content-Type'].startswith('image/jpeg'), 'MIME de imagem correto',
   resp.headers['Content-Type'])

print('== MODO SUPABASE (bucket simulado) ==')
config.STORAGE = 'supabase'
config.SUPABASE_URL = 'https://projeto.supabase.co'
config.SUPABASE_SERVICE_KEY = 'chave'
storage_mod.enviar = enviar_falso
storage_mod.baixar = baixar_falso
storage_mod.apagar = apagar_falso

r = c.post(f'/api/dias/{DIA}/fotos', data={'fotos': (foto(), 'canteiro.jpg')},
           headers=H, content_type='multipart/form-data')
ok(r.status_code == 201, 'upload aceito', r.status_code)
url_sb = r.get_json()['fotos'][0]['url']
nome = os.path.basename(url_sb)
ok(nome in BUCKET, 'arquivo foi mesmo para o bucket, não para o disco', list(BUCKET))
ok(not os.path.exists(os.path.join(config.UPLOAD_DIR, nome)), 'não ficou cópia no disco efêmero')

resp = c.get(url_sb)
ok(resp.status_code == 200, 'foto do bucket abre pelo app (200, não redirect)', resp.status_code)
ok(resp.status_code != 302, 'NÃO redireciona para outro domínio (a CSP bloquearia a imagem)')
ok(resp.headers['Content-Type'].startswith('image/jpeg'), 'MIME correto', resp.headers['Content-Type'])
ok(resp.data[:2] == b'\xff\xd8', 'bytes da imagem chegam íntegros', resp.data[:4])
ok('max-age' in resp.headers.get('Cache-Control',''), 'cacheável no aparelho',
   resp.headers.get('Cache-Control'))

print('== PDF DE DESPESA VIA BUCKET ==')
from reportlab.pdfgen import canvas as pdfcanvas
b = io.BytesIO(); cv = pdfcanvas.Canvas(b); cv.drawString(80, 700, 'NOTA FISCAL 12345'); cv.save(); b.seek(0)
r = c.post(f'/api/dias/{DIA}/despesas', data={'categoria':'Material','descricao':'Cimento',
    'valor':'500','status_pagamento':'Pago','imagem': (b, 'nota.pdf')},
    headers=H, content_type='multipart/form-data')
ok(r.status_code == 201, 'PDF anexado', r.status_code)
fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
url_pdf = fe['despesas'][0]['anexo_url']
resp = c.get(url_pdf)
ok(resp.status_code == 200, 'PDF abre pelo app', resp.status_code)
ok(resp.headers['Content-Type'] == 'application/pdf', 'MIME de PDF (abre no visualizador)',
   resp.headers['Content-Type'])
ok(resp.data[:4] == b'%PDF', 'bytes do PDF íntegros', resp.data[:8])

print('== CSP PERMITE A IMAGEM ==')
csp = c.get('/').headers.get('Content-Security-Policy', '')
ok("img-src 'self'" in csp, 'CSP restringe imagens à própria origem', csp[:60])
ok(url_sb.startswith('/arquivos/'), 'URL do anexo é da própria origem', url_sb)

print('== ARQUIVO AUSENTE ==')
BUCKET.clear()
ok(c.get(url_sb).status_code == 404, 'anexo inexistente responde 404 limpo, sem estourar erro 500')

print('== PDF DO RELATÓRIO COM FOTO NO BUCKET ==')
BUCKET[nome] = open(os.path.join('/tmp'), 'rb').read() if False else None
r2 = c.post(f'/api/dias/{DIA}/fotos', data={'fotos': (foto(), 'nova.jpg')},
            headers=H, content_type='multipart/form-data')
rel = c.get(f'/api/semanas/{S}/relatorio.pdf')
ok(rel.status_code == 200 and rel.data[:4] == b'%PDF',
   'relatório em PDF embute a foto vinda do bucket', rel.status_code)
ok(len(rel.data) > 12000, 'a foto realmente entrou no PDF (tamanho)', len(rel.data))

print('== SEGURANÇA ==')
anon = app.test_client()
ok(anon.get(url_sb).status_code in (302, 401), 'anônimo não acessa anexo', anon.get(url_sb).status_code)

print('\n' + ('ANEXOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
