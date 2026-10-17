# -*- coding: utf-8 -*-
"""Variáveis de ambiente coladas com 'enfeites' não podem derrubar o storage."""
import importlib, os, sys
import os
import sys

# Caminho do projeto derivado da posição deste arquivo: assim a suíte roda em
# qualquer clone, sem depender de onde a pasta foi colocada.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))


falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

CHAVE = 'eyJhbGciOiJI.eyJyb2xlIjoic2Vydmlj.assinatura'

def recarregar(url=None, chave=None, bucket=None):
    for nome, valor in (('SUPABASE_URL', url), ('SUPABASE_SERVICE_KEY', chave),
                        ('SUPABASE_BUCKET', bucket)):
        if valor is None:
            os.environ.pop(nome, None)
        else:
            os.environ[nome] = valor
    from obra import config
    importlib.reload(config)
    return config

print('== O CASO REAL: URL COM COLCHETES ==')
cfg = recarregar('[https://projeto.supabase.co]', CHAVE)
ok(cfg.SUPABASE_URL == 'https://projeto.supabase.co', 'colchetes removidos', cfg.SUPABASE_URL)
ok(cfg.STORAGE == 'supabase', 'storage volta a ser o Supabase', cfg.STORAGE)

import urllib.request
try:
    urllib.request.urlopen(cfg.SUPABASE_URL + '/storage/v1/x', timeout=0.1)
except Exception as erro:
    ok('unknown url type' not in str(erro), 'a URL montada é válida para o urllib', erro)

print('== OUTROS ENGANOS COMUNS ==')
for bruto, esperado, descricao in [
    ('"https://projeto.supabase.co"', 'https://projeto.supabase.co', 'aspas duplas'),
    ("'https://projeto.supabase.co'", 'https://projeto.supabase.co', 'aspas simples'),
    ('  https://projeto.supabase.co/  ', 'https://projeto.supabase.co', 'espaços e barra final'),
    ('<https://projeto.supabase.co>', 'https://projeto.supabase.co', 'sinais de menor/maior'),
    ('projeto.supabase.co', 'https://projeto.supabase.co', 'sem o https://'),
    ('https://projeto.supabase.co/storage/v1', 'https://projeto.supabase.co', 'caminho colado junto'),
    ('https://projeto.supabase.co\n', 'https://projeto.supabase.co', 'quebra de linha no fim'),
]:
    cfg = recarregar(bruto, CHAVE)
    ok(cfg.SUPABASE_URL == esperado, descricao, (bruto, cfg.SUPABASE_URL))

print('== CHAVE E BUCKET ==')
cfg = recarregar('https://projeto.supabase.co', f'"{CHAVE}"', '"anexos"')
ok(cfg.SUPABASE_SERVICE_KEY == CHAVE, 'aspas removidas da chave', cfg.SUPABASE_SERVICE_KEY[:12])
ok(cfg.SUPABASE_BUCKET == 'anexos', 'aspas removidas do bucket', cfg.SUPABASE_BUCKET)
cfg = recarregar('https://projeto.supabase.co', CHAVE, '/anexos/')
ok(cfg.SUPABASE_BUCKET == 'anexos', 'barras removidas do bucket', cfg.SUPABASE_BUCKET)

print('== AVISOS DE CONFIGURAÇÃO ==')
cfg = recarregar('https://projeto.supabase.co', 'chave-sem-formato-jwt')
avisos = cfg.problemas_de_configuracao()
ok(any('JWT' in a for a in avisos), 'chave fora do formato é apontada', avisos)
cfg = recarregar('https://projeto.supabase.co', None)
ok(any('sem SUPABASE_SERVICE_KEY' in a for a in avisos + cfg.problemas_de_configuracao()),
   'metade da configuração é apontada', cfg.problemas_de_configuracao())
cfg = recarregar('https://projeto.supabase.co', CHAVE)
ok(cfg.problemas_de_configuracao() == [], 'configuração correta não gera aviso',
   cfg.problemas_de_configuracao())

print('== DATABASE_URL TAMBÉM ==')
os.environ['DATABASE_URL'] = '[postgresql://u:s@host:6543/postgres]'
from obra import config as c2
importlib.reload(c2)
ok(c2.DATABASE_URL == 'postgresql://u:s@host:6543/postgres', 'colchetes removidos do banco', c2.DATABASE_URL)
ok(c2.BACKEND == 'postgres', 'backend reconhecido mesmo com o engano', c2.BACKEND)
os.environ.pop('DATABASE_URL')

print('\n' + ('CONFIGURAÇÃO OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
