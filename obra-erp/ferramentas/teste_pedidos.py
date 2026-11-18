# -*- coding: utf-8 -*-
"""Número do pedido no lugar da NF: regra da IA e releitura das notas antigas.

A IA é simulada: nenhum teste chama o Gemini ou o Claude de verdade.
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
from PIL import Image

from obra import config, create_app
from obra.services import assistente, ia_provedores, padroes, pedidos_antigos

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome': 'Obra Pedido'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio': '2026-08-03'}, headers=H).get_json()['id']
DIA = c.get(f'/api/semanas/{S}/dias').get_json()[0]['id']

print('== REGRA PARA A IA ==')
ok('NÚMERO DO PEDIDO' in assistente.PROMPT_BASE and '"PED 48213"' in assistente.PROMPT_BASE,
   'prompt manda usar o pedido no lugar da NF')
ok('PED 48213' in padroes.exemplos_para_prompt(True), 'exemplo de nota com pedido')
d = padroes.completar({'descricao': 'Aço CA-50 pedido 48213', 'fornecedor': 'Somar'}, 'O13')
ok(d['numero_nota'] == 'PED 48213' and 'pedido' not in d['descricao'].lower(),
   'pedido escrito na descrição vai para o número', d)

from obra.utils import rotulo_documento
ok(rotulo_documento('2353') == 'NF 2353' and rotulo_documento('PED 48213') == 'PED 48213',
   'relatórios não escrevem "NF PED"')

print('== RELEITURA DAS NOTAS ANTIGAS ==')
def jpeg():
    b = io.BytesIO(); Image.new('RGB', (40, 30), (90, 90, 90)).save(b, 'JPEG'); b.seek(0); return b

def lancar(fornecedor, nota, arquivo=True):
    dados = {'categoria': 'Material', 'descricao': 'Material', 'valor': '100',
             'status_pagamento': 'Em Aberto', 'fornecedor': fornecedor, 'numero_nota': nota}
    if arquivo:
        dados['imagem'] = (jpeg(), f'{fornecedor}.jpg')
    return c.post(f'/api/dias/{DIA}/despesas', data=dados, headers=H,
                  content_type='multipart/form-data').get_json()['id']

COM = lancar('Somar', '5521')
SEM = lancar('Inova civil', '2353')
SEM_ARQUIVO = lancar('Marcon', '343245', arquivo=False)

# A IA simulada acha pedido só na primeira nota que lê em cada rodada.
lidas = []
def falso_por_fornecedor(sistema, texto, arquivos, esquema, max_tokens):
    lidas.append(arquivos[0]['nome'])
    return json.dumps({'pedido': 'Pedido nº 48213' if len(lidas) == 1 else ''})

ia_provedores.CHAMADAS['gemini'] = falso_por_fornecedor
config.IA_ORDEM = ['gemini']; config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = ''
pedidos_antigos.EM_SEGUNDO_PLANO = False

r = c.post('/api/pedidos-antigos', json={'aplicar': False}, headers=H)
e = r.get_json()
ok(r.status_code == 200 and not e['rodando'], 'conferência roda até o fim', e)
ok(e['total'] == 2, 'só lançamentos com nota anexada entram', e['total'])
ok(len(e['achados']) == 1 and e['achados'][0]['depois'] == 'PED 48213', 'pedido achado e limpo', e['achados'])
ok(e['achados'][0]['despesa_id'] == SEM, 'mais recente primeiro (ordem por id decrescente)', e['achados'])
ok(e['sem_pedido'] == 1, 'nota sem pedido conta à parte', e)
fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
ok({x['numero_nota'] for x in fe['despesas']} == {'5521', '2353', '343245'}, 'conferir NÃO grava nada')

lidas.clear()
e = c.post('/api/pedidos-antigos', json={'aplicar': True}, headers=H).get_json()
fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
numeros = {x['id']: x['numero_nota'] for x in fe['despesas']}
ok(numeros[SEM] == 'PED 48213' and numeros[COM] == '5521' and numeros[SEM_ARQUIVO] == '343245',
   'gravar troca só onde achou pedido', numeros)
ok(c.get('/api/pedidos-antigos').get_json()['aplicar'] is True, 'estado mostra o resultado')

lidas.clear()
e = c.post('/api/pedidos-antigos', json={'aplicar': False}, headers=H).get_json()
ok(e['total'] == 1, 'quem já tem PED não é relido', e['total'])

print('== PERMISSÕES ==')
c.post('/api/usuarios', json={'nome': 'Mestre', 'email': 'mp@obra.local', 'senha': 'mestre123',
                              'papel': 'mestre', 'obras': [O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email': 'mp@obra.local', 'senha': 'mestre123'})
Hm = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
ok(cm.post('/api/pedidos-antigos', json={}, headers=Hm).status_code == 403, 'mestre não relê notas')

config.GEMINI_API_KEY = ''
ok(c.post('/api/pedidos-antigos', json={}, headers=H).status_code == 400, 'sem IA: avisa em vez de rodar')

print('\n' + ('PEDIDOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
