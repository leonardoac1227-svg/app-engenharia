# -*- coding: utf-8 -*-
"""Frete com várias viagens e a central de arquivos da semana."""
import io, os, sys, zipfile
os.environ['DATA_DIR'] = '/tmp/fa_dados'
sys.path.insert(0, '/home/claude/obra-erp')
from PIL import Image
from reportlab.pdfgen import canvas as pdfcanvas

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

from obra import create_app
app = create_app(); c = app.test_client()
c.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome':'Obra Frete'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio':'2026-08-17'}, headers=H).get_json()['id']
dias = c.get(f'/api/semanas/{S}/dias').get_json()
por_nome = {d['dia_semana']: d for d in dias}
DIA = dias[0]['id']

print('== FRETE COM 3 VIAGENS ==')
r = c.post(f'/api/dias/{DIA}/despesas', data={
    'categoria':'Frete', 'descricao':'Busca de material', 'valor':'45',
    'status_pagamento':'Em Aberto',
    'frete_transportador':'DIEGO MOTOBOY', 'frete_viagens':'3',
    'frete_datas': ['2026-08-17','2026-08-19','2026-08-21'],
}, headers=H, content_type='multipart/form-data')
ok(r.status_code == 201, 'lançar frete com 3 viagens', r.get_json())
f = r.get_json()['frete']
ok(f['viagens'] == 3 and f['valor_unitario'] == 45.0, 'valor é POR viagem', f)
ok(f['valor_total'] == 135.0, 'total = 3 × 45', f['valor_total'])
ok(f['datas'] == ['2026-08-17','2026-08-19','2026-08-21'], 'cada viagem com sua data', f['datas'])

print('== CADA VIAGEM NO DIA CERTO ==')
fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
fretes = [d for d in fe['despesas'] if d['categoria'] == 'Frete']
ok(len(fretes) == 3, 'três lançamentos', len(fretes))
por_dia = sorted(d['dia_semana'] for d in fretes)
ok(por_dia == ['Quarta','Segunda','Sexta'], 'lançados nos dias das viagens', por_dia)
ok(all(d['fornecedor'] == 'DIEGO MOTOBOY' for d in fretes), 'transportador em todos')
ok(any('(frete 2/3)' in d['descricao'] for d in fretes), 'viagens numeradas',
   [d['descricao'] for d in fretes])
ok(fe['financeiro']['despesas'] == 135.0, 'custo da semana soma as 3 viagens',
   fe['financeiro']['despesas'])

print('== RESUMO POR TRANSPORTADOR ==')
c.post(f"/api/dias/{por_nome['Terça']['id']}/despesas", data={
    'categoria':'Frete', 'descricao':'Areia', 'valor':'200', 'status_pagamento':'Pago',
    'frete_transportador':'TALES CAMINHÃO', 'frete_viagens':'1', 'frete_datas':['2026-08-18'],
}, headers=H, content_type='multipart/form-data')
fe = c.get(f'/api/semanas/{S}/fechamento').get_json()
ok(fe['fretes']['viagens'] == 4 and fe['fretes']['total'] == 335.0,
   'resumo consolida viagens e valor', fe['fretes'])
nomes = [t['transportador'] for t in fe['fretes']['transportadores']]
ok(nomes == ['TALES CAMINHÃO','DIEGO MOTOBOY'], 'agrupado por transportador, maior primeiro', nomes)

print('== VALIDAÇÕES ==')
r = c.post(f'/api/dias/{DIA}/despesas', data={
    'categoria':'Frete','descricao':'X','valor':'10','status_pagamento':'Pago',
    'frete_viagens':'2','frete_datas':['2026-08-17']}, headers=H, content_type='multipart/form-data')
ok(r.status_code == 400, 'frete sem transportador é recusado', r.status_code)
r = c.post(f'/api/dias/{DIA}/despesas', data={
    'categoria':'Frete','descricao':'X','valor':'10','status_pagamento':'Pago',
    'frete_transportador':'ZE','frete_viagens':'99','frete_datas':['2026-08-17']},
    headers=H, content_type='multipart/form-data')
ok(r.status_code == 400, 'quantidade absurda é recusada', r.status_code)
r = c.post(f'/api/dias/{DIA}/despesas', data={'categoria':'Frete','descricao':'Frete simples',
    'valor':'30','status_pagamento':'Pago'}, headers=H, content_type='multipart/form-data')
ok(r.status_code == 201, 'frete sem os campos novos continua sendo despesa comum', r.status_code)

print('== CENTRAL DE ARQUIVOS ==')
def foto():
    b = io.BytesIO(); Image.new('RGB',(1200,900),(130,140,120)).save(b,'JPEG'); b.seek(0); return b
def pdf(texto):
    b = io.BytesIO(); cv = pdfcanvas.Canvas(b); cv.drawString(80,700,texto); cv.save(); b.seek(0); return b

c.post(f'/api/dias/{DIA}/fotos', data={'fotos': (foto(),'muro.jpg'), 'legenda':'Muro lateral',
       'ambiente':'Fachada'}, headers=H, content_type='multipart/form-data')
c.post(f'/api/dias/{DIA}/despesas', data={'categoria':'Material','descricao':'Cimento CP-II',
    'fornecedor':'INOVACIVIL','valor':'2200','status_pagamento':'Em Aberto','numero_nota':'2353',
    'imagem': (pdf('NOTA 2353'),'nota-2353.pdf')}, headers=H, content_type='multipart/form-data')
c.post(f'/api/dias/{DIA}/despesas', data={'categoria':'Material','descricao':'Areia lavada',
    'fornecedor':'JL DEPOSITO','valor':'450','status_pagamento':'Pago','numero_nota':'8891',
    'imagem': (foto(),'recibo.jpg')}, headers=H, content_type='multipart/form-data')

indice = c.get(f'/api/semanas/{S}/arquivos').get_json()
ok(indice['total'] == 3, 'índice reúne notas e fotos', indice['total'])
ok(indice['notas'] == 2 and indice['fotos'] == 1, 'separados por tipo', (indice['notas'], indice['fotos']))
ok(indice['valor_com_nota'] == 2650.0, 'soma dos lançamentos com anexo', indice['valor_com_nota'])
nota = [a for a in indice['arquivos'] if a['tipo'] == 'nota'][0]
ok(nota['url'].startswith('/arquivos/'), 'URL do anexo pela rota autenticada', nota['url'])
ok(nota['numero_nota'] and nota['subtitulo'], 'traz fornecedor e nota para identificar', nota)
ok(any(a['eh_pdf'] for a in indice['arquivos']), 'marca quais são PDF (abre diferente)')

print('== PACOTE ZIP ==')
r = c.get(f'/api/semanas/{S}/arquivos.zip')
ok(r.status_code == 200 and r.headers['Content-Type'] == 'application/zip', 'ZIP gerado', r.status_code)
ok('ARQUIVOS_' in r.headers['Content-Disposition'], 'nome do pacote', r.headers['Content-Disposition'])
pacote = zipfile.ZipFile(io.BytesIO(r.data))
nomes = pacote.namelist()
ok(len([n for n in nomes if n.startswith('notas-fiscais/')]) == 2, 'notas em pasta própria', nomes)
ok(len([n for n in nomes if n.startswith('fotos/')]) == 1, 'fotos em pasta própria', nomes)
ok(any('NF-2353' in n and 'INOVACIVIL' in n for n in nomes),
   'nome legível: data, nota e fornecedor', nomes)
ok('_conteudo.txt' in nomes, 'resumo do pacote incluído')
resumo = pacote.read('_conteudo.txt').decode('utf-8')
ok('Obra Frete' in resumo and 'Notas fiscais' in resumo, 'resumo descreve o conteúdo', resumo[:120])
ok(pacote.read([n for n in nomes if n.endswith('.pdf')][0])[:4] == b'%PDF', 'PDF íntegro dentro do ZIP')

print('== ARQUIVO AUSENTE NÃO DERRUBA O PACOTE ==')
from obra import config
alvo = [a for a in indice['arquivos'] if a['eh_pdf']][0]['arquivo']
os.remove(os.path.join(config.UPLOAD_DIR, alvo))
r = c.get(f'/api/semanas/{S}/arquivos.zip')
ok(r.status_code == 200, 'ZIP continua sendo gerado', r.status_code)
pacote = zipfile.ZipFile(io.BytesIO(r.data))
resumo = pacote.read('_conteudo.txt').decode('utf-8')
ok('NÃO INCLUÍDOS' in resumo, 'o que faltou é listado no resumo', resumo[-200:])

print('== SEMANA SEM ANEXO ==')
S2 = c.post(f'/api/obras/{O}/semanas', json={'data_inicio':'2026-08-24'}, headers=H).get_json()['id']
ok(c.get(f'/api/semanas/{S2}/arquivos').get_json()['total'] == 0, 'índice vazio não quebra')
ok(c.get(f'/api/semanas/{S2}/arquivos.zip').status_code == 404, 'ZIP vazio avisa em vez de baixar nada')

print('== PERMISSÕES ==')
c.post('/api/usuarios', json={'nome':'Mestre','email':'mf@obra.local','senha':'mestre123',
                              'papel':'mestre','obras':[O]}, headers=H)
cm = app.test_client(); cm.post('/api/auth/login', json={'email':'mf@obra.local','senha':'mestre123'})
idx = cm.get(f'/api/semanas/{S}/arquivos').get_json()
ok(idx['notas'] == 0 and all(a['tipo'] == 'foto' for a in idx['arquivos']),
   'mestre vê fotos, não notas fiscais', idx)
ok(cm.get(f'/api/semanas/{S}/arquivos.zip').status_code == 403, 'mestre não baixa o pacote')

print('\n' + ('FRETE E ARQUIVOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
