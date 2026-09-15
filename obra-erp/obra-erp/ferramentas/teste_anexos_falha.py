# -*- coding: utf-8 -*-
"""Anexo que não existe: a resposta precisa dizer POR QUE, não só 404."""
import io, os, sys
import os
import sys
import tempfile

# Caminho do projeto derivado da posição deste arquivo: assim a suíte roda em
# qualquer clone, sem depender de onde a pasta foi colocada.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
from PIL import Image

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

import obra.storage as storage_mod
from obra import create_app, config
app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Anexos'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={}, headers=H).get_json()['id']
DIA = c.get(f'/api/semanas/{S}/dias').get_json()[0]['id']

b = io.BytesIO(); Image.new('RGB',(900,700),(100,120,100)).save(b,'JPEG'); b.seek(0)
r = c.post(f'/api/dias/{DIA}/fotos', data={'fotos': (b, 'x.jpg')}, headers=H,
           content_type='multipart/form-data')
url = r.get_json()['fotos'][0]['url']
nome = os.path.basename(url)

print('== DISCO EFÊMERO: ARQUIVO APAGADO NO DEPLOY ==')
os.remove(os.path.join(config.UPLOAD_DIR, nome))
resp = c.get(url)
ok(resp.status_code == 404, 'responde 404', resp.status_code)
corpo = resp.data.decode('utf-8')
ok('Anexo indisponível' in corpo, 'página explica em português, não "Not Found" cru')
ok('temporário' in corpo or 'Supabase' in corpo, 'aponta a causa (disco temporário)', corpo[:200])
ok('X-Motivo' in resp.headers, 'motivo também vai no cabeçalho, para o log', resp.headers.get('X-Motivo'))

print('== BUCKET COM CHAVE ERRADA ==')
config.STORAGE = 'supabase'
config.SUPABASE_URL = 'https://projeto.supabase.co'
config.SUPABASE_SERVICE_KEY = 'chave-errada'
config.SUPABASE_BUCKET = 'anexos'
def falha(*a, **k):
    raise storage_mod.ErroStorage('Supabase Storage 400: {"message":"Invalid JWT"}')
storage_mod._requisitar = falha
resp = c.get(url)
motivo = resp.headers.get('X-Motivo', '')
ok('Invalid JWT' in motivo or '400' in motivo,
   'erro real do Supabase aparece no motivo, em vez de sumir', motivo)

print('== DIAGNÓSTICO DE ANEXOS ==')
diag = c.get('/api/diagnostico').get_json()
ok('registrados' in diag['anexos'], 'diagnóstico conta os anexos registrados', diag['anexos'])
ok(diag['anexos']['ausentes'] >= 1, 'aponta quantos não abrem', diag['anexos'])
ok(diag['anexos']['exemplos_ausentes'][0]['motivo'], 'traz o motivo de cada um',
   diag['anexos']['exemplos_ausentes'][0])

print('\n' + ('DIAGNÓSTICO DE ANEXOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
