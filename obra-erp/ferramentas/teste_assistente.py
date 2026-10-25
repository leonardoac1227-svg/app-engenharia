# -*- coding: utf-8 -*-
"""Assistente de IA: catálogo de ações, conferência, gravação pela tela e provedores.

A IA é simulada: nenhum teste chama o Gemini ou o Claude de verdade.
"""
import io
import json
import os
import sys
import tempfile
import urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
from PIL import Image

from obra import __version__, config, create_app
from obra.services import acoes, ia_provedores

app = create_app(); c = app.test_client()
falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

def jpeg():
    b = io.BytesIO(); Image.new('RGB', (40, 30), (90, 90, 90)).save(b, 'JPEG'); return b.getvalue()
JPG = jpeg()
PDF = b"%PDF-1.4\n%fake\n"

c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome': 'Obra IA'}, headers=H).get_json()['id']
OUTRA = c.post('/api/obras', json={'nome': 'Outra'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio': '2026-08-03'}, headers=H).get_json()['id']
dias = c.get(f'/api/semanas/{S}/dias').get_json()
SEG = dias[0]['id']
JOAO = c.post(f'/api/obras/{O}/funcionarios', json={'nome': 'João Batista', 'funcao': 'Pedreiro'}, headers=H).get_json()['id']
PEDRO = c.post(f'/api/obras/{O}/funcionarios', json={'nome': 'Pedro Lima', 'funcao': 'Servente'}, headers=H).get_json()['id']

def lancar(dia, **campos):
    base = {'categoria': 'Material', 'descricao': 'Item', 'valor': '100', 'status_pagamento': 'Em Aberto'}
    base.update(campos)
    return c.post(f'/api/dias/{dia}/despesas', data=base, headers=H, content_type='multipart/form-data').get_json()['id']
AREIA = lancar(SEG, descricao='Areia', fornecedor='Areal', valor='300', numero_nota='77')
S2 = c.post(f'/api/obras/{OUTRA}/semanas', json={'data_inicio': '2026-08-03'}, headers=H).get_json()['id']
DESPESA_OUTRA = lancar(c.get(f'/api/semanas/{S2}/dias').get_json()[0]['id'], descricao='De outra obra')
LOC = c.post(f'/api/obras/{O}/locacoes', json={'equipamento': 'Andaime', 'fornecedor': 'Loca', 'frequencia': 'mensal',
                                               'valor_parcela': 500, 'ciclos': 3, 'data_inicio': '2026-08-03'},
             headers=H).get_json()['id']
PARCELA = next(p for p in c.get(f'/api/locacoes/{LOC}').get_json()['parcelas'] if p['status'] == 'prevista')['id']

# ------------------------------------------------------------------ IA simulada
chamadas = []
respostas = {}
def falso(nome):
    def chamar(sistema, texto, arquivos, esquema, max_tokens):
        chamadas.append({'provedor': nome, 'sistema': sistema, 'texto': texto, 'arquivos': arquivos})
        r = respostas.get(nome)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)
    return chamar
ia_provedores.CHAMADAS['gemini'] = falso('gemini')
ia_provedores.CHAMADAS['anthropic'] = falso('anthropic')

def A(tipo, **dados):
    return {'tipo': tipo, 'dados': dados}

PROPOSTA = {'transcricao': '', 'obra_id': 999, 'resposta': 'Preparei tudo.', 'acoes': [
    A('diario', data='2026-08-03', producao_dia='Executada a concretagem da laje.', clima='Sol'),
    A('ponto', data='2026-08-03', registros=[
        {'funcionario_id': JOAO, 'nome': 'João', 'horas': 8, 'justificativa': 'não devia ficar'},
        {'funcionario_id': 0, 'nome': 'pedro', 'horas': 4, 'justificativa': 'Consulta médica'},
        {'funcionario_id': 0, 'nome': 'Carlos Novo', 'horas': 8}]),
    A('funcionario', nome='Carlos Novo', funcao='Armador'),
    A('funcionario', nome='João Batista'),
    A('despesa', data='quinta passada', categoria='material', descricao='20 sacos de cimento',
      fornecedor='Casa do Construtor', valor='1.380,00', status_pagamento='reembolso', numero_nota='4521',
      anexos=[0, 0, 7, 2]),
    A('despesa', descricao='Sem valor', categoria='Ferragens'),
    A('despesa', categoria='Frete', descricao='Frete de areia', valor=120, status_pagamento='Pago',
      frete_transportador='Zé', frete_viagens=3, frete_datas=['2026-08-03', '2026-08-04', '2026-08-05']),
    A('editar_despesa', despesa_id=AREIA, alteracoes={'status_pagamento': 'Reembolso', 'fornecedor': 'Areal'}),
    A('editar_despesa', despesa_id=DESPESA_OUTRA, alteracoes={'valor': 10}),
    A('pagar_despesa', despesa_id=AREIA, pago_em='2026-08-05'),
    A('excluir_despesa', despesa_id=AREIA),
    A('foto', anexos=[1, 0], legenda='Laje', ambiente='2º pavimento'),
    A('semana', data='2026-08-04', meta='Concluir a laje', orcamento_previsto=8000),
    A('relatorio', formato='excel'),
    A('locacao_lancar', parcela_id=PARCELA, status_pagamento='Pago'),
    A('editar_funcionario', funcionario_id=PEDRO, alteracoes={'ativo': False}),
    A('pagar_semana', data='2026-08-03'),
    {'tipo': 'inventado', 'dados': {}},
]}

def perguntar(mensagem='hoje...', arquivos=(), cliente=None, cab=None, obra=None):
    dados = {'mensagem': mensagem, 'data_referencia': '2026-08-03',
             'historico': json.dumps([{'papel': 'usuario', 'texto': 'oi'}]),
             'arquivos': [(io.BytesIO(b), n) for n, b in arquivos]}
    return (cliente or c).post(f'/api/obras/{obra or O}/assistente', data=dados, headers=cab or H,
                               content_type='multipart/form-data')

def aplicar(lista, arquivos=(), cliente=None, cab=None):
    dados = {'acoes': json.dumps(lista), 'data_referencia': '2026-08-03',
             'arquivos': [(io.BytesIO(b), n) for n, b in arquivos]}
    return (cliente or c).post(f'/api/obras/{O}/assistente/aplicar', data=dados, headers=cab or H,
                               content_type='multipart/form-data')

print('== CATÁLOGO ==')
ok(len(acoes.CATALOGO) >= 19, 'catálogo cobre as funções do app', list(acoes.CATALOGO))
prompt_gestor = acoes.texto_para_prompt(True, True)
prompt_mestre = acoes.texto_para_prompt(False, False)
ok('despesa:' in prompt_gestor and 'despesa:' not in prompt_mestre and 'ponto:' in prompt_mestre,
   'prompt do mestre não oferece ações de caixa')

print('== DADOS DO LANÇAMENTO CHEGAM MESMO FORA DO LUGAR ==')
from obra.services import assistente as srv
props = srv.ESQUEMA['properties']['acoes']['items']['properties']['dados'].get('properties', {})
ok({'valor', 'descricao', 'fornecedor', 'numero_nota', 'anexos', 'registros', 'alteracoes'} <= set(props),
   'esquema enviado ao Gemini declara os campos de "dados" (sem isso ele devolvia {})', sorted(props))
ctx = acoes.Contexto(O, '2026-08-03', True, True, [{'nome': 'a.jpg', 'mime': 'image/jpeg'}])
conf, _ = acoes.normalizar([{'tipo': 'despesa', 'resumo': '2 baldes de aço para concretagem', 'dados': {},
                             'valor_total': '64,00', 'supplier': 'Almeida e Caires', 'nota': 'ORC 31797',
                             'status': 'pago'}], ctx)
d = conf[0]['dados']
ok(d['valor'] == 64 and d['fornecedor'] == 'Almeida e Caires' and d['numero_nota'] == 'ORC 31797'
   and d['status_pagamento'] == 'Pago' and d['descricao'] == '2 baldes de aço para concretagem'
   and 'faltando' not in d, 'campos soltos e apelidos viram o lançamento; resumo vira descrição', d)

print('== TODO ARQUIVO FICA SALVO NA DESPESA ==')
arqs = [{'nome': 'cupom.jpg', 'mime': 'image/jpeg'}, {'nome': 'laje.jpg', 'mime': 'image/jpeg'},
        {'nome': 'boleto.pdf', 'mime': 'application/pdf'}, {'nome': 'fala.ogg', 'mime': 'audio/ogg'}]
lista, _ = acoes.normalizar([A('despesa', descricao='Cimento', valor=100), A('foto', anexos=[1])],
                            acoes.Contexto(O, '2026-08-03', True, True, arqs))
srv._garantir_anexos(lista, arqs)
ok(lista[0]['dados']['anexos'] == [0, 2] and lista[1]['dados']['anexos'] == [1] and '📎' in lista[0]['descricao'],
   'despesa sem anexo recebe cupom e boleto (a foto da obra e o áudio ficam fora)', [a['dados'] for a in lista])
lista, _ = acoes.normalizar([A('despesa', descricao='A', valor=1), A('despesa', descricao='B', valor=2)],
                            acoes.Contexto(O, '2026-08-03', True, True, arqs[:2]))
srv._garantir_anexos(lista, arqs[:2])
ok([a['dados']['anexos'] for a in lista] == [[0], [1]], 'duas despesas e dois arquivos: um para cada')

print('== SEM CHAVE ==')
config.GEMINI_API_KEY = ''; config.ANTHROPIC_API_KEY = ''
r = perguntar()
ok(r.status_code == 503 and r.get_json()['codigo'] == 'ia_indisponivel', 'sem chave: 503 com explicação', r.get_json())

print('== INTERPRETAÇÃO (UMA CHAMADA) ==')
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = 'a'
respostas['gemini'] = '```json\n' + json.dumps(PROPOSTA) + '\n```'
n0 = len(chamadas)
r = perguntar(arquivos=[('nota.jpg', JPG), ('laje.png', b"\x89PNG\r\n\x1a\n" + b"\x00" * 40), ('nota.pdf', PDF)])
p = r.get_json()
ok(r.status_code == 200 and len(chamadas) == n0 + 1, 'uma única chamada à IA', len(chamadas) - n0)
ok(p['obra_id'] == O, 'obra inexistente vira a obra aberta', p['obra_id'])
ctx = chamadas[-1]['texto']
for trecho in ('João Batista', 'contas_em_aberto', 'locacoes_ativas', 'semana_atual', 'Usuário: oi'):
    ok(trecho in ctx, f'contexto leva {trecho}')
ok('pagar_semana' in chamadas[-1]['sistema'], 'prompt descreve o catálogo')
tipos = [a['tipo'] for a in p['acoes']]
ok(tipos.count('funcionario') == 1 and 'inventado' not in tipos, 'duplicado e inventado ficam fora', tipos)
por = {}
for a in p['acoes']:
    por.setdefault(a['tipo'], []).append(a)
ponto = {r['nome']: r for r in por['ponto'][0]['dados']['registros']}
ok(ponto['João Batista']['justificativa'] == '' and ponto['Pedro Lima']['funcionario_id'] == PEDRO
   and ponto['Carlos Novo']['funcionario_id'] == 0, 'ponto: nomes casados, novo marcado, jornada sem justificativa', ponto)
d1, d2, d3 = [a['dados'] for a in por['despesa']]
ok(d1['data'] == '2026-08-03' and d1['categoria'] == 'Material' and d1['status_pagamento'] == 'Reembolso'
   and d1['valor'] == 1380.0 and d1['reembolsar_para'] == config.REEMBOLSO_PADRAO and d1['anexos'] == [0, 2],
   'despesa normalizada (data, categoria, status, valor BR, anexos válidos)', d1)
ok(d2['categoria'] == 'Outro' and d2['faltando'] == ['valor'], 'falta de valor sinalizada', d2)
ok(d3['frete_viagens'] == 3 and len(d3['frete_datas']) == 3, 'frete com várias viagens', d3)
ed = por['editar_despesa']
ok(len(ed) == 1 and ed[0]['dados']['alteracoes'] == {'status_pagamento': 'Reembolso', 'reembolsar_para': config.REEMBOLSO_PADRAO},
   'editar: só o que muda; despesa de outra obra recusada', ed)
ok(por['excluir_despesa'][0]['confirmar'] and por['pagar_semana'][0]['confirmar'], 'exclusão e baixa em lote pedem confirmação')
ok(por['foto'][0]['dados']['anexos'] == [1, 0], 'foto: as duas imagens (o PDF fica fora)', por['foto'][0]['dados'])
ok(por['relatorio'][0]['dados']['formato'] == 'planilha', '"excel" vira planilha')
ok(por['semana'][0]['dados'] == {'data': '2026-08-04', 'meta': 'Concluir a laje', 'orcamento_previsto': 8000.0},
   'meta e orçamento da semana', por['semana'][0]['dados'])
ok(any('não foi encontrado' in a for a in p['avisos']), 'aviso de lançamento de outra obra', p['avisos'])

print('== GRAVAR PELA TELA (MESMO CAMINHO DO BOT) ==')
gravar = [a for a in p['acoes'] if a['tipo'] in ('diario', 'ponto', 'funcionario', 'despesa', 'editar_despesa',
                                                 'semana', 'relatorio', 'locacao_lancar', 'editar_funcionario', 'foto')
          and not a['dados'].get('faltando')]
r = aplicar([{'tipo': a['tipo'], 'dados': a['dados']} for a in gravar],
            arquivos=[('nota.jpg', JPG), ('laje.jpg', JPG), ('nota.pdf', PDF)])
res = r.get_json()['resultados']
erros = [(gravar[x['indice']]['tipo'], x.get('erro')) for x in res if not x['ok']]
ok(r.status_code == 200 and not erros, 'todas as ações gravadas', erros)
dia = c.get(f'/api/semanas/{S}/dias').get_json()[0]
ok(dia['producao_dia'].startswith('Executada') and dia['clima'] == 'Sol', 'diário gravado')
horas = c.get(f'/api/dias/{SEG}/horas').get_json()
ok(horas[str(JOAO)]['horas'] == 8 and horas[str(PEDRO)]['justificativa'] == 'Consulta médica' and len(horas) == 3,
   'ponto gravado, com o profissional novo cadastrado antes', horas)
func = c.get(f'/api/obras/{O}/funcionarios?inativos=1').get_json()
ok(any(f['nome'] == 'Carlos Novo' for f in func) and not next(f for f in func if f['id'] == PEDRO)['ativo'],
   'cadastro e desligamento', func)
desp = c.get(f'/api/dias/{SEG}/despesas').get_json()
cimento = next(x for x in desp if x['descricao'] == '20 sacos de cimento')
ok(cimento['status_pagamento'] == 'Reembolso' and len(cimento['anexos']) == 2, 'despesa com 2 anexos (foto e PDF)', cimento)
ok(next(x for x in desp if x['id'] == AREIA)['status_pagamento'] == 'Reembolso', 'status alterado para reembolso')
fretes = [x for d in c.get(f'/api/semanas/{S}/dias').get_json() for x in c.get(f"/api/dias/{d['id']}/despesas").get_json()
          if x['categoria'] == 'Frete']
ok(len(fretes) == 3, 'frete virou 3 lançamentos, um por dia', len(fretes))
semana = next(s for s in c.get(f'/api/obras/{O}/semanas').get_json() if s['id'] == S)
ok(semana['meta'] == 'Concluir a laje' and semana['orcamento_previsto'] == 8000, 'semana atualizada', semana)
link = next(x for x in res if gravar[x['indice']]['tipo'] == 'relatorio').get('link', '')
ok(link == f'/api/semanas/{S}/pagamentos.xlsx' and c.get(link).status_code == 200, 'relatório: link funcionando', link)
ok(next(p for p in c.get(f'/api/locacoes/{LOC}').get_json()['parcelas'] if p['id'] == PARCELA)['status'] == 'lancada',
   'renovação do aluguel lançada')
ok(dia['fotos'] == 2, 'fotos anexadas ao diário', dia['fotos'])

print('== CONFERÊNCIA NO SERVIDOR ==')
r = aplicar([{'tipo': 'editar_despesa', 'dados': {'despesa_id': DESPESA_OUTRA, 'alteracoes': {'valor': 1}}}]).get_json()
ok(not r['resultados'][0]['ok'], 'despesa de outra obra recusada mesmo vinda da tela', r)
r = aplicar([{'tipo': 'despesa', 'dados': {'descricao': 'Cimento', 'valor': 50, 'fornecedor': 'Casa do Construtor',
                                           'numero_nota': '4521'}}]).get_json()
ok(r['resultados'][0]['codigo'] == 'nota_duplicada', 'nota repetida é barrada', r)
r = aplicar([{'tipo': 'despesa', 'confirmar_duplicidade': True,
              'dados': {'descricao': 'Cimento', 'valor': 50, 'fornecedor': 'Casa do Construtor', 'numero_nota': '4521'}}]).get_json()
ok(r['resultados'][0]['ok'], '"lançar mesmo assim" passa', r)
r = aplicar([{'tipo': 'excluir_despesa', 'dados': {'despesa_id': AREIA}}]).get_json()
ok(r['resultados'][0]['ok'] and not any(x['id'] == AREIA for x in c.get(f'/api/dias/{SEG}/despesas').get_json()),
   'exclusão confirmada apaga', r)
r = aplicar([{'tipo': 'pagar_semana', 'dados': {'data': '2026-08-04'}}]).get_json()
ok(r['resultados'][0]['ok'] and not [x for x in c.get(f'/api/dias/{SEG}/despesas').get_json() if x['status_pagamento'] == 'Em Aberto'],
   'baixa de todas as contas da semana', r)

print('== BAIXA COM COMPROVANTE ==')
conta = lancar(SEG, descricao='Brita', fornecedor='Pedreira', valor='250')
respostas['gemini'] = {'resposta': 'Baixa feita.', 'acoes': [A('pagar_despesa', despesa_id=conta, pago_em='2026-08-03')]}
p2 = perguntar(arquivos=[('pix.jpg', JPG)]).get_json()
ok(p2['acoes'][0]['dados']['anexos'] == [0], 'comprovante de PIX vai junto com a baixa', p2['acoes'])
r = aplicar([{'tipo': a['tipo'], 'dados': a['dados']} for a in p2['acoes']], arquivos=[('pix.jpg', JPG)]).get_json()
brita = next(x for x in c.get(f'/api/dias/{SEG}/despesas').get_json() if x['id'] == conta)
ok(r['resultados'][0]['ok'] and brita['status_pagamento'] == 'Pago' and len(brita['anexos']) == 1,
   'baixa gravada com o comprovante anexado', brita)

print('== MESTRE DE OBRA ==')
c.post('/api/usuarios', json={'nome': 'Mestre', 'email': 'mestre@obra.local', 'senha': 'mestre123',
                              'papel': 'mestre', 'obras': [O]}, headers=H)
m = app.test_client()
m.post('/api/auth/login', json={'email': 'mestre@obra.local', 'senha': 'mestre123'})
HM = {'X-CSRF-Token': m.get('/api/sessao').get_json()['csrf']}
respostas['gemini'] = PROPOSTA
pm = perguntar(cliente=m, cab=HM).get_json()
tipos_m = {a['tipo'] for a in pm['acoes']}
ok(not tipos_m & {'despesa', 'editar_despesa', 'pagar_despesa', 'excluir_despesa', 'funcionario', 'pagar_semana'}
   and 'diario' in tipos_m, 'mestre: só o que o perfil permite', tipos_m)
ok('contas_em_aberto' not in chamadas[-1]['texto'], 'mestre: contexto sem dados financeiros')
r = aplicar([{'tipo': 'despesa', 'dados': {'descricao': 'x', 'valor': 1}}], cliente=m, cab=HM).get_json()
ok(not r['resultados'][0]['ok'], 'mestre: despesa recusada mesmo forçando pela API', r)

print('== RESERVA E FALHAS ==')
respostas['gemini'] = urllib.error.HTTPError('u', 429, 'quota', {}, None)
respostas['anthropic'] = {'resposta': 'ok', 'acoes': [A('diario', producao_dia='Alvenaria.')]}
r = perguntar()
ok(r.status_code == 200 and r.get_json()['fonte'] == 'anthropic', 'Gemini fora: Claude responde', r.get_json())
respostas['anthropic'] = RuntimeError('fora do ar')
r = perguntar()
ok(r.status_code == 503 and 'limite gratuito' in r.get_json()['erro'], 'os dois falham: 503 com o motivo', r.get_json())
respostas['gemini'] = PROPOSTA
r = perguntar(arquivos=[('virus.jpg', b'MZ\x90\x00exe')])
ok(r.status_code == 400, 'arquivo falso recusado')
r = perguntar(mensagem='')
ok(r.status_code == 400, 'mensagem vazia recusada')
respostas['gemini'] = PROPOSTA
config.ANTHROPIC_API_KEY = ''
r = perguntar(arquivos=[('fala.ogg', b'OggS\x00')])
ok(r.status_code == 200 and chamadas[-1]['arquivos'][0]['mime'] == 'audio/ogg', 'áudio pela tela vai ao Gemini')

print('== RDO, LIMITE E TESTE DA IA ==')
respostas['gemini'] = {'producao_dia': 'Concretagem.', 'clima': 'Sol', 'observacoes': ''}
r = c.post(f'/api/dias/{SEG}/rdo/redigir', json={}, headers=H)
ok(r.status_code == 200 and r.get_json()['producao_dia'] == 'Concretagem.', 'redigir RDO com IA', r.get_json())
limite = config.IA_LIMITE_HORA; config.IA_LIMITE_HORA = 1
ok(perguntar().status_code == 429, 'freio por usuário')
config.IA_LIMITE_HORA = limite
ok(m.post('/api/assistente/testar', headers=HM).status_code == 403, 'mestre não roda o teste da IA')

print('== PROVEDORES (HTTP SIMULADO) ==')
import obra.services.ia_provedores as ip
from importlib import reload
reload(ip)
class Resp:
    def __init__(self, d): self.d = d
    def read(self): return json.dumps(self.d).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False
def ok_json(texto): return Resp({'candidates': [{'content': {'parts': [{'text': texto}]}}]})
def http(codigo, msg=''):
    return urllib.error.HTTPError('u', codigo, 'x', {}, io.BytesIO(json.dumps({'error': {'message': msg}}).encode()))
pedidos = []
comportamento = {}
def urlopen_falso(req, timeout=0):
    corpo = json.loads(req.data)
    modelo = req.full_url.split('/models/')[1].split(':')[0]
    pedidos.append((modelo, corpo, timeout))
    r = comportamento.get(modelo)
    if callable(r):
        return r(corpo)
    if isinstance(r, Exception):
        raise r
    return ok_json('{"a": 1}')
ip.urllib.request.urlopen = urlopen_falso
config.IA_ORDEM = ['gemini']; config.GEMINI_API_KEY = 'g'
config.GEMINI_MODEL = 'lite'; config.GEMINI_MODELO_RESERVA = 'flash'

comportamento = {'lite': http(429, 'quota')}
res, fonte = ip.gerar('s', 't', [], {'type': 'object'})
ok(res == {'a': 1} and [p[0] for p in pedidos][-1] == 'flash', 'cota do modelo principal: reserva responde', [p[0] for p in pedidos])
pedidos.clear(); comportamento = {'lite': TimeoutError('timed out')}
ip.gerar('s', 't', [], {'type': 'object'}, prazo=100)
ok([p[0] for p in pedidos][-1] == 'flash' and pedidos[0][2] <= 60, 'falha de rede: reserva, dentro do prazo',
   [(p[0], round(p[2])) for p in pedidos])
pedidos.clear(); comportamento = {'lite': http(400, 'API key not valid')}
try:
    ip.gerar('s', 't', [], {'type': 'object'}); ok(False, 'chave inválida deveria falhar')
except ip.IaIndisponivel as erro:
    ok('GEMINI_API_KEY inválida' in str(erro) and [p[0] for p in pedidos] == ['lite'], 'chave inválida: não insiste no reserva', str(erro))
pedidos.clear()
def sem_thinking_level(corpo):
    if 'thinkingLevel' in (corpo['generationConfig'].get('thinkingConfig') or {}):
        raise http(400, 'Unknown name thinkingLevel')
    return ok_json('{"a": 2}')
comportamento = {'lite': sem_thinking_level}
res, _ = ip.gerar('s', 't', [], {'type': 'object'}, pensar='minimo')
ok(res == {'a': 2} and pedidos[-1][1]['generationConfig']['thinkingConfig'] == {'thinkingBudget': 0},
   'raciocínio: modelo sem thinkingLevel recebe thinkingBudget 0')
n = len(pedidos)
ip.gerar('s', 't', [], {'type': 'object'}, pensar='minimo')
ok(len(pedidos) == n + 1, 'parâmetro aceito fica guardado')
pedidos.clear(); comportamento = {}
ip.gerar('s', 't', [{'nome': 'f.png', 'mime': 'image/png', 'dados': b'x'}], None)
ok(pedidos[0][1]['contents'][0]['parts'][0]['inlineData']['mimeType'] == 'image/png', 'imagem vai em base64')
r = ip.testar()
ok([x['modelo'] for x in r] == ['lite', 'flash'] and all(x['ok'] for x in r), 'teste mede cada modelo', r)

import anthropic
capturado = {}
class ClienteFalso:
    def __init__(self, **kw): self.beta = self; self.messages = self
    def create(self, **kw):
        capturado.update(kw)
        class B: type = 'text'; text = '{"ok": true}'
        class R: stop_reason = 'end_turn'; content = [B()]
        return R()
anthropic.Anthropic = ClienteFalso
config.IA_ORDEM = ['anthropic']; config.ANTHROPIC_API_KEY = 'a'
res, fonte = ip.gerar('s', 't', [{'nome': 'n.pdf', 'mime': 'application/pdf', 'dados': PDF}], {'type': 'object'})
ok(res == {'ok': True} and capturado['model'] == 'claude-opus-5-5' and capturado['fallbacks'] == 'default',
   'Claude: modelo padrão com reserva do servidor')
ok('format' not in capturado['output_config'] and 'temperature' not in capturado
   and capturado['messages'][0]['content'][0]['type'] == 'document', 'Claude: JSON pelo prompt, PDF como documento')
try:
    ip.gerar('s', 't', [{'nome': 'a.ogg', 'mime': 'audio/ogg', 'dados': b'x'}], None); ok(False, 'áudio sem Gemini')
except ip.IaIndisponivel as erro:
    ok('GEMINI_API_KEY' in str(erro), 'áudio só com Gemini: explica')
ok('certificado' in ip.descrever_falha(urllib.error.URLError('[SSL: CERTIFICATE_VERIFY_FAILED]'))
   and 'DNS' in ip.descrever_falha(urllib.error.URLError('[Errno -2] Name or service not known')), 'motivos de rede')

print('== ARQUIVOS DA TELA COM VERSÃO NO ENDEREÇO ==')
pagina = c.get('/').get_data(as_text=True)
ok(f'/static/v{__version__}/js/app.js' in pagina and '/static/js/' not in pagina, 'página usa endereços com a versão')
r = c.get(f'/static/v{__version__}/js/views/ajustes.js')
ok(r.status_code == 200 and 'immutable' in r.headers.get('Cache-Control', ''), 'módulo servido com cache imutável')
sw = c.get('/sw.js').get_data(as_text=True)
ok(f"const VERSAO = '{__version__}'" in sw and 'no-cache' in c.get('/sw.js').headers['Cache-Control'],
   'service worker com a versão escrita')

print('\n' + ('ASSISTENTE OK' if not falhas else f'{len(falhas)} FALHA(S)'))
sys.exit(1 if falhas else 0)
