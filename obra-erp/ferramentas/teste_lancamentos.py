# -*- coding: utf-8 -*-
"""Edição de lançamento, vários anexos por lançamento e destinatário do reembolso."""
import io
import os
import sys
import tempfile

# Caminho do projeto derivado da posição deste arquivo: assim a suíte roda em
# qualquer clone, sem depender de onde a pasta foi colocada.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
from obra import create_app
from obra import config

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
       b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
       b"\x00\x00IEND\xaeB`\x82")
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

def arquivo(nome, dados=PNG):
    return (io.BytesIO(dados), nome)

def no_disco(nome):
    return os.path.exists(os.path.join(config.UPLOAD_DIR, nome))

app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Obra Lançamentos'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio':'2026-08-17'}, headers=H).get_json()['id']
dias = c.get(f'/api/semanas/{S}/dias').get_json()
DIA, DIA2 = dias[0]['id'], dias[2]['id']

def lancar(**extra):
    dados = {'categoria':'Material','descricao':'Cimento','valor':'100','status_pagamento':'Pago'}
    dados.update(extra)
    return c.post(f'/api/dias/{DIA}/despesas', data=dados, headers=H,
                  content_type='multipart/form-data')

def da_lista(despesa_id, dia=DIA):
    return next(x for x in c.get(f'/api/dias/{dia}/despesas').get_json() if x['id'] == despesa_id)

print('== VÁRIOS ANEXOS NO MESMO LANÇAMENTO ==')
r = lancar(anexos=[arquivo('nota.png'), arquivo('pix.pdf', PDF), arquivo('material.png')])
ok(r.status_code == 201 and r.get_json().get('anexos') == 3, 'lança com 3 anexos', r.get_json())
D = r.get_json()['id']
x = da_lista(D)
ok([a['nome'] for a in x['anexos']] == ['nota.png', 'pix.pdf', 'material.png'],
   'lista devolve os 3 anexos na ordem', x['anexos'])
ok(x['anexos'][1]['eh_pdf'] and not x['anexos'][0]['eh_pdf'], 'marca qual é PDF')
ok(x['nome_arquivo'] == 'nota.png' and x['anexo_url'] == x['anexos'][0]['url'],
   'imagem_path continua espelhando o 1º anexo', (x['nome_arquivo'], x['anexo_url']))
ok(c.get(x['anexos'][1]['url']).status_code == 200, 'cada anexo abre pela rota autenticada')

r = lancar(imagem=arquivo('antigo.png'))
ok(r.status_code == 201 and da_lista(r.get_json()['id'])['anexos'][0]['nome'] == 'antigo.png',
   'envio de um anexo só (app antigo) continua funcionando', r.get_json())

r = lancar(anexos=[arquivo(f'a{i}.png') for i in range(6)])
ok(r.status_code == 400, 'mais de 5 anexos no lançamento é recusado', r.status_code)

print('== ADICIONAR E REMOVER ANEXO DEPOIS ==')
r = c.post(f'/api/despesas/{D}/anexos', data={'anexos':[arquivo('extra.png')]}, headers=H,
           content_type='multipart/form-data')
ok(r.status_code == 201 and len(r.get_json()['anexos']) == 4, 'acrescenta anexo a lançamento existente', r.get_json())
r = c.post(f'/api/despesas/{D}/anexos', data={'anexos':[arquivo('b.png'), arquivo('c.png')]}, headers=H,
           content_type='multipart/form-data')
ok(r.status_code == 400 and len(da_lista(D)['anexos']) == 4, 'passar do limite não grava nada', r.status_code)

primeiro = da_lista(D)['anexos'][0]
arquivo_primeiro = primeiro['url'].rsplit('/', 1)[1]
r = c.delete(f"/api/despesas/{D}/anexos/{primeiro['id']}", headers=H)
x = da_lista(D)
ok(r.status_code == 200 and len(x['anexos']) == 3, 'remove um anexo', x['anexos'])
ok(x['nome_arquivo'] == 'pix.pdf', 'removendo o 1º, o seguinte vira o principal', x['nome_arquivo'])
ok(not no_disco(arquivo_primeiro), 'arquivo removido sai do disco')
outro = lancar().get_json()['id']
ok(c.delete(f"/api/despesas/{outro}/anexos/{x['anexos'][0]['id']}", headers=H).status_code == 404,
   'não remove anexo de outro lançamento')

print('== EDITAR O LANÇAMENTO ==')
r = c.put(f'/api/despesas/{D}', json={'descricao':'Cimento CP-II', 'valor':150.5,
          'fornecedor':'Casa do Construtor', 'categoria':'Serviço'}, headers=H)
x = da_lista(D)
ok(r.status_code == 200 and (x['descricao'], x['valor'], x['fornecedor'], x['categoria'])
   == ('Cimento CP-II', 150.5, 'Casa do Construtor', 'Serviço'), 'edita descrição, valor, fornecedor e categoria', x)
r = c.put(f'/api/despesas/{D}', json={'dia_id': DIA2}, headers=H)
ok(r.status_code == 200 and any(y['id'] == D for y in c.get(f'/api/dias/{DIA2}/despesas').get_json()),
   'muda o lançamento de dia')
ok(len(da_lista(D, DIA2)['anexos']) == 3, 'anexos acompanham a troca de dia')

O2 = c.post('/api/obras', json={'nome':'Outra obra'}, headers=H).get_json()['id']
S2 = c.post(f'/api/obras/{O2}/semanas', json={'data_inicio':'2026-08-17'}, headers=H).get_json()['id']
dia_outra = c.get(f'/api/semanas/{S2}/dias').get_json()[0]['id']
r = c.put(f'/api/despesas/{D}', json={'dia_id': dia_outra}, headers=H)
ok(r.status_code == 400, 'não move lançamento para dia de outra obra', r.status_code)

print('== PARA QUEM É O REEMBOLSO ==')
r = lancar(status_pagamento='Reembolso')
R = r.get_json()['id']
ok(da_lista(R)['reembolsar_para'] == 'Léo', 'reembolso sem nome vai para Léo', da_lista(R)['reembolsar_para'])
r = lancar(status_pagamento='Reembolso', reembolsar_para='Marcos')
R2 = r.get_json()['id']
ok(da_lista(R2)['reembolsar_para'] == 'Marcos', 'aceita outro nome', da_lista(R2)['reembolsar_para'])
r = lancar(status_pagamento='Pago', reembolsar_para='Marcos')
ok(da_lista(r.get_json()['id'])['reembolsar_para'] == '', 'lançamento pago não guarda nome de reembolso')

c.put(f'/api/despesas/{R2}', json={'reembolsar_para':'Ana'}, headers=H)
ok(da_lista(R2)['reembolsar_para'] == 'Ana', 'edita só o nome do reembolso')
c.put(f'/api/despesas/{R2}', json={'status_pagamento':'Pago', 'reembolsar_para':'Ana'}, headers=H)
ok(da_lista(R2)['reembolsar_para'] == '', 'virou pago: nome do reembolso sai')
c.put(f'/api/despesas/{R2}', json={'status_pagamento':'Reembolso', 'reembolsar_para':''}, headers=H)
ok(da_lista(R2)['reembolsar_para'] == 'Léo', 'voltou a reembolso sem nome: Léo de novo')

fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
item = next(x for x in fe['despesas'] if x['id'] == R)
ok(item['reembolsar_para'] == 'Léo' and 'anexos' in item, 'fechamento traz reembolso e anexos', item)
csv = c.get(f'/api/semanas/{S}/despesas.csv').get_data(as_text=True)
ok('Reembolsar para' in csv and ';Léo;' in csv, 'CSV tem a coluna do reembolso')
ok(c.get('/api/inicio').get_json().get('reembolso_padrao') == 'Léo', 'app recebe o nome padrão no início')

print('== ARQUIVOS DA SEMANA E EXCLUSÃO ==')
indice = c.get(f'/api/semanas/{S}/arquivos').get_json()
do_D = [a for a in indice['arquivos'] if a.get('despesa_id') == D]
ok(len(do_D) == 3, 'arquivos da semana lista cada anexo do lançamento', len(do_D))
ok(c.get(f'/api/semanas/{S}/arquivos.zip').status_code == 200, 'zip da semana continua saindo')

arquivos_D = [a['url'].rsplit('/', 1)[1] for a in da_lista(D, DIA2)['anexos']]
ok(all(no_disco(a) for a in arquivos_D), 'anexos estão no disco antes de excluir')
c.delete(f'/api/despesas/{D}', headers=H)
ok(not any(no_disco(a) for a in arquivos_D), 'excluir o lançamento apaga todos os anexos')

print('== PARCELAS DIVIDEM OS ANEXOS DA NOTA ==')
# semanas dos vencimentos (20/08, 27/08 e 03/09) já existem: cada parcela cai na sua
for inicio in ('2026-08-24', '2026-08-31'):
    c.post(f'/api/obras/{O}/semanas', json={'data_inicio': inicio}, headers=H)
r = lancar(status_pagamento='Em Aberto', vencimento='2026-08-20', parcelas='3',
           parcela_frequencia='semanal', anexos=[arquivo('nf.pdf', PDF), arquivo('boleto.png')])
P = r.get_json()['id']
grupo = c.get(f'/api/despesas/{P}/parcelas', headers=H).get_json()
ok(len(grupo) == 3, '3 parcelas criadas', grupo)
outra_parcela = grupo[1]['id']
nomes = lambda anexos: [a['nome'] for a in anexos]
fe_todas = []
for s in c.get(f'/api/obras/{O}/semanas').get_json():
    fe_todas += c.get(f"/api/semanas/{s['id']}/fechamento").get_json()['despesas']
parcela2 = next((x for x in fe_todas if x['id'] == outra_parcela), None)
ok(parcela2 is not None and nomes(parcela2['anexos']) == ['nf.pdf', 'boleto.png'],
   'a 2ª parcela também mostra a nota e o boleto', parcela2 and parcela2.get('anexos'))
arquivo_nf = da_lista(P)['anexos'][0]['url'].rsplit('/', 1)[1]
c.delete(f'/api/despesas/{P}', headers=H)
ok(no_disco(arquivo_nf), 'excluir uma parcela NÃO apaga o arquivo que as outras usam')

print('== PERMISSÕES ==')
c.post('/api/usuarios', json={'nome':'Mestre','email':'ml@obra.local','senha':'mestre123',
                              'papel':'mestre','obras':[O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email':'ml@obra.local','senha':'mestre123'})
HM = {'X-CSRF-Token': cm.get('/api/sessao').get_json()['csrf']}
ok(cm.post(f'/api/despesas/{R}/anexos', data={'anexos':[arquivo('x.png')]}, headers=HM,
           content_type='multipart/form-data').status_code == 403, 'mestre não anexa em lançamento')
ok(cm.put(f'/api/despesas/{R}', json={'reembolsar_para':'Eu'}, headers=HM).status_code == 403,
   'mestre não edita lançamento')

print('\n' + ('LANÇAMENTOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
