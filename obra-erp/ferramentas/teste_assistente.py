# -*- coding: utf-8 -*-
"""Assistente de IA: provedores, reserva paga, pente-fino das ações e permissões.

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
from obra import config, create_app
from obra.services import ia_provedores

app = create_app(); c = app.test_client()
falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake\n"

c.post('/api/auth/login', json={'email': 'gestor@obra.local', 'senha': 'obra123'})
H = {'X-CSRF-Token': c.get('/api/sessao').get_json()['csrf']}
O = c.post('/api/obras', json={'nome': 'Obra IA'}, headers=H).get_json()['id']
S = c.post(f'/api/obras/{O}/semanas', json={'data_inicio': '2026-08-03'}, headers=H).get_json()['id']
dias = c.get(f'/api/semanas/{S}/dias').get_json()
SEG = dias[0]['id']
JOAO = c.post(f'/api/obras/{O}/funcionarios', json={'nome': 'João Batista', 'funcao': 'Pedreiro'}, headers=H).get_json()['id']
PEDRO = c.post(f'/api/obras/{O}/funcionarios', json={'nome': 'Pedro Lima', 'funcao': 'Servente'}, headers=H).get_json()['id']
r = c.post(f'/api/dias/{SEG}/despesas', data={'categoria': 'Material', 'descricao': 'Areia', 'fornecedor': 'Areal',
           'valor': '300', 'status_pagamento': 'Em Aberto', 'numero_nota': '77'}, headers=H,
           content_type='multipart/form-data')
AREIA = r.get_json()['id']

# ------------------------------------------------------------------ IA simulada
chamadas = []
respostas = {}

def falso(nome):
    def chamar(sistema, texto, arquivos, esquema, max_tokens):
        chamadas.append({'provedor': nome, 'texto': texto, 'arquivos': arquivos, 'esquema': esquema})
        resposta = respostas.get(nome)
        if isinstance(resposta, Exception):
            raise resposta
        return resposta if isinstance(resposta, str) else json.dumps(resposta)
    return chamar

ia_provedores.CHAMADAS['gemini'] = falso('gemini')
ia_provedores.CHAMADAS['anthropic'] = falso('anthropic')

def acao(**campos):
    base = {k: '' for k in ('data', 'resumo', 'producao_dia', 'clima', 'observacoes', 'categoria', 'descricao',
                            'fornecedor', 'status_pagamento', 'numero_nota', 'vencimento', 'reembolsar_para',
                            'parcela_frequencia', 'legenda', 'ambiente', 'nome', 'funcao')}
    base.update({'despesa_id': 0, 'valor': 0, 'parcelas': 0, 'registros': [], 'anexos': []})
    base.update(campos)
    return base

PROPOSTA = {
    'resposta': 'Preparei o diário, o ponto e a despesa.',
    'acoes': [
        acao(tipo='diario', data='2026-08-03', producao_dia='Executada a concretagem da laje.', clima='Ensolarado'),
        acao(tipo='ponto', data='2026-08-03', registros=[
            {'funcionario_id': JOAO, 'nome': 'João', 'horas': 8, 'justificativa': 'não devia ficar'},
            {'funcionario_id': 0, 'nome': 'pedro', 'horas': 4, 'justificativa': 'Consulta médica'},
            {'funcionario_id': 0, 'nome': 'Carlos Novo', 'horas': 8, 'justificativa': ''},
            {'funcionario_id': 999, 'nome': '', 'horas': 8, 'justificativa': ''}]),
        acao(tipo='funcionario', nome='Carlos Novo', funcao='Armador'),
        acao(tipo='funcionario', nome='João Batista', funcao='Pedreiro'),
        acao(tipo='despesa', data='data-invalida', categoria='material', descricao='20 sacos de cimento',
             fornecedor='Casa do Construtor', valor=1380, status_pagamento='reembolso', numero_nota='4521',
             anexos=[0, 0, 7], parcelas=1),
        acao(tipo='despesa', descricao='Sem valor', categoria='Ferragens', status_pagamento='???'),
        acao(tipo='pagar_despesa', despesa_id=AREIA, data='2026-08-05'),
        acao(tipo='editar_despesa', despesa_id=AREIA, valor=350, fornecedor='Areal'),
        acao(tipo='editar_despesa', despesa_id=999999, valor=10),
        acao(tipo='excluir_despesa', despesa_id=AREIA),
        acao(tipo='foto', anexos=[1, 0], legenda='Laje concretada', ambiente='2º pavimento'),
        acao(tipo='inventado'),
    ],
}

def perguntar(mensagem='hoje...', arquivos=(), cabecalhos=None, cliente=None, obra=O):
    dados = {'mensagem': mensagem, 'data_referencia': '2026-08-03',
             'historico': json.dumps([{'papel': 'usuario', 'texto': 'oi'}]),
             'arquivos': [(io.BytesIO(b), n) for n, b in arquivos]}
    return (cliente or c).post(f'/api/obras/{obra}/assistente', data=dados, headers=cabecalhos or H,
                               content_type='multipart/form-data')

print('== SEM CHAVE ==')
config.GEMINI_API_KEY = ''; config.ANTHROPIC_API_KEY = ''
ok(c.get('/api/assistente/status').get_json()['disponivel'] is False, 'status: desligado sem chaves')
r = perguntar()
ok(r.status_code == 503 and r.get_json()['codigo'] == 'ia_indisponivel', 'sem chave: 503 com explicação', r.get_json())

print('== GEMINI RESPONDE ==')
config.GEMINI_API_KEY = 'g'; config.ANTHROPIC_API_KEY = 'a'
ok(c.get('/api/assistente/status').get_json()['provedores'] == ['gemini', 'anthropic'],
   'ordem: Gemini primeiro, Claude reserva')
respostas['gemini'] = '```json\n' + json.dumps(PROPOSTA) + '\n```'
r = perguntar(arquivos=[('nota.pdf', PDF), ('laje.png', PNG)])
ok(r.status_code == 200, 'proposta devolvida', r.get_json())
p = r.get_json()
ok(p['fonte'] == 'gemini', 'resposta veio do Gemini', p.get('fonte'))
ok(chamadas[-1]['provedor'] == 'gemini' and len(chamadas[-1]['arquivos']) == 2, 'arquivos chegaram à IA')
ok('João Batista' in chamadas[-1]['texto'] and 'contas_em_aberto' in chamadas[-1]['texto'],
   'contexto leva equipe e contas em aberto')
ok('Usuário: oi' in chamadas[-1]['texto'], 'histórico da conversa vai junto')
tipos = [a['tipo'] for a in p['acoes']]
ok(tipos.count('funcionario') == 1, 'funcionário já cadastrado não é duplicado', tipos)
ok('inventado' not in tipos, 'tipo desconhecido é descartado', tipos)

ponto = next(a for a in p['acoes'] if a['tipo'] == 'ponto')
regs = {x['nome']: x for x in ponto['registros']}
ok(regs['João Batista']['justificativa'] == '', 'jornada completa não guarda justificativa', regs)
ok(regs['Pedro Lima']['funcionario_id'] == PEDRO, '"pedro" casa com Pedro Lima', regs.get('Pedro Lima'))
ok(regs['Carlos Novo']['funcionario_id'] == 0, 'pessoa nova fica com id 0 para cadastrar', regs)
ok(len(ponto['registros']) == 3, 'id inexistente sem nome é descartado', ponto['registros'])

desp = [a for a in p['acoes'] if a['tipo'] == 'despesa']
d1 = desp[0]
ok(d1['data'] == '2026-08-03', 'data inválida vira a data de referência', d1['data'])
ok(d1['categoria'] == 'Material' and d1['status_pagamento'] == 'Reembolso', 'categoria/status normalizados', d1)
ok(d1['reembolsar_para'] == config.REEMBOLSO_PADRAO, 'reembolso sem nome vai para o padrão', d1['reembolsar_para'])
ok(d1['anexos'] == [0] and d1['parcelas'] == 0, 'anexos sem repetição/fora do intervalo; 1 parcela = sem parcelamento', d1)
ok(desp[1]['categoria'] == 'Outro' and desp[1]['status_pagamento'] == 'Em Aberto'
   and desp[1]['faltando'] == ['valor'], 'categoria estranha = Outro; falta valor sinalizada', desp[1])

pagar = next(a for a in p['acoes'] if a['tipo'] == 'pagar_despesa')
ok(pagar['pago_em'] == '2026-08-05' and pagar['despesa_atual']['descricao'] == 'Areia', 'baixa aponta a conta certa', pagar)
editar = [a for a in p['acoes'] if a['tipo'] == 'editar_despesa']
ok(len(editar) == 1 and editar[0]['alteracoes'] == {'valor': 350.0},
   'edição: só o que muda; lançamento de outra obra/inexistente descartado', editar)
excluir = next(a for a in p['acoes'] if a['tipo'] == 'excluir_despesa')
ok(excluir['requer_confirmacao'] is True, 'exclusão sempre pede confirmação')
foto = next(a for a in p['acoes'] if a['tipo'] == 'foto')
ok(foto['anexos'] == [1], 'foto só aceita imagem (PDF fica fora)', foto['anexos'])
ok(any('não foi encontrado' in a for a in p['avisos']), 'aviso de lançamento não encontrado', p['avisos'])

print('== RESERVA PAGA (CLAUDE) ==')
respostas['gemini'] = urllib.error.HTTPError('u', 429, 'quota', {}, None)
respostas['anthropic'] = {'resposta': 'ok', 'acoes': [acao(tipo='diario', producao_dia='Alvenaria.')]}
r = perguntar()
ok(r.status_code == 200 and r.get_json()['fonte'] == 'anthropic', 'Gemini estourou a cota: Claude responde', r.get_json())
respostas['gemini'] = 'isto não é json'
r = perguntar()
ok(r.get_json()['fonte'] == 'anthropic', 'JSON ilegível do Gemini: Claude responde', r.get_json())
respostas['gemini'] = urllib.error.HTTPError('u', 429, 'quota', {}, None)
respostas['anthropic'] = RuntimeError('fora do ar')
r = perguntar()
ok(r.status_code == 503 and 'limite gratuito' in r.get_json()['erro'], 'os dois falham: 503 explicando', r.get_json())

print('== ARQUIVOS ==')
respostas['gemini'] = PROPOSTA
r = perguntar(arquivos=[('virus.jpg', b'MZ\x90\x00executavel')])
ok(r.status_code == 400, 'arquivo com conteúdo falso é recusado', r.get_json())
r = perguntar(arquivos=[('planilha.xlsx', b'PK\x03\x04')])
ok(r.status_code == 400, 'formato não suportado é recusado', r.get_json())
r = perguntar(mensagem='')
ok(r.status_code == 400, 'mensagem vazia sem anexos é recusada', r.get_json())

print('== GRAVAÇÃO: DIA POR DATA E RDO ==')
r = c.post(f'/api/obras/{O}/dia-por-data', json={'data': '2026-08-05'}, headers=H).get_json()
ok(r['dia_id'] == dias[2]['id'] and not r['semana_criada'], 'data existente devolve o dia certo', r)
r = c.post(f'/api/obras/{O}/dia-por-data', json={'data': '2026-08-13'}, headers=H).get_json()
ok(r['semana_criada'] is True, 'data sem semana: o gestor tem a semana criada', r)
semanas = c.get(f'/api/obras/{O}/semanas').get_json()
ok(any(s['data_inicio'] == '2026-08-10' for s in semanas), 'semana nova começa na segunda', semanas)
r2 = c.post(f'/api/obras/{O}/dia-por-data', json={'data': '2026-08-13'}, headers=H).get_json()
ok(r2['dia_id'] == r['dia_id'] and not r2['semana_criada'], 'segunda chamada não duplica a semana', r2)

r = c.put(f'/api/dias/{SEG}', json={'producao_dia': 'Executada a concretagem da laje.', 'clima': 'Sol',
                                    'observacoes': 'Visita do fiscal.'}, headers=H)
ok(r.status_code == 200, 'RDO salvo', r.get_json())
dia = c.get(f'/api/semanas/{S}/dias').get_json()[0]
ok(dia['producao_dia'].startswith('Executada') and dia['clima'] == 'Sol', 'RDO aparece nos dias da semana', dia)
r = c.put(f'/api/dias/{SEG}', json={}, headers=H)
ok(r.status_code == 400, 'RDO sem campos é recusado')
r = c.get(f'/api/semanas/{S}/relatorio.pdf')
ok(r.status_code == 200 and r.data[:4] == b'%PDF', 'PDF semanal gera com o RDO', r.status_code)

respostas['gemini'] = {'producao_dia': 'Concretagem da laje.', 'clima': 'Sol', 'observacoes': ''}
r = c.post(f'/api/dias/{SEG}/rdo/redigir', json={}, headers=H)
ok(r.status_code == 200 and r.get_json()['producao_dia'] == 'Concretagem da laje.', 'redigir RDO com IA', r.get_json())
ok('Visita do fiscal' in chamadas[-1]['texto'], 'redação parte do texto já escrito', chamadas[-1]['texto'][:200])

print('== MESTRE DE OBRA ==')
c.post('/api/usuarios', json={'nome': 'Mestre', 'email': 'mestre@obra.local', 'senha': 'mestre123',
                              'papel': 'mestre', 'obras': [O]}, headers=H)
m = app.test_client()
m.post('/api/auth/login', json={'email': 'mestre@obra.local', 'senha': 'mestre123'})
HM = {'X-CSRF-Token': m.get('/api/sessao').get_json()['csrf']}
respostas['gemini'] = PROPOSTA
r = perguntar(cliente=m, cabecalhos=HM)
pm = r.get_json()
tipos = [a['tipo'] for a in pm['acoes']]
ok(r.status_code == 200 and not set(tipos) & {'despesa', 'pagar_despesa', 'editar_despesa', 'excluir_despesa'},
   'mestre: ações de caixa ficam de fora', tipos)
ok('funcionario' not in tipos, 'mestre não cadastra profissional', tipos)
ok('contas_em_aberto' not in chamadas[-1]['texto'], 'mestre: contexto sem dados financeiros')
r = m.post(f'/api/obras/{O}/dia-por-data', json={'data': '2026-09-30'}, headers=HM)
ok(r.status_code == 404, 'mestre não cria semana', r.get_json())

print('== LIMITE POR HORA ==')
limite = config.IA_LIMITE_HORA
config.IA_LIMITE_HORA = 1
r = perguntar()
ok(r.status_code == 429 and r.get_json()['codigo'] == 'limite_ia', 'freio por usuário', r.status_code)
config.IA_LIMITE_HORA = limite

print('== PROVEDORES REAIS (HTTP SIMULADO) ==')
import obra.services.ia_provedores as ip
from importlib import reload
reload(ip)  # volta as chamadas reais
corpos = []
class Resp:
    def __init__(self, d): self.d = d
    def read(self): return json.dumps(self.d).encode()
    def __enter__(self): return self
    def __exit__(self, *a): return False
def urlopen_falso(req, timeout=0):
    corpo = json.loads(req.data)
    corpos.append(corpo)
    if 'responseJsonSchema' in corpo['generationConfig']:
        raise urllib.error.HTTPError(req.full_url, 400, 'schema', {}, None)
    return Resp({'candidates': [{'content': {'parts': [{'text': '{"a": 1}'}]}}]})
original = ip.urllib.request.urlopen
ip.urllib.request.urlopen = urlopen_falso
config.IA_ORDEM = ['gemini']
res, fonte = ip.gerar('sis', 'txt', [{'nome': 'n.png', 'mime': 'image/png', 'dados': PNG}], {'type': 'object'})
ip.urllib.request.urlopen = original
ok(res == {'a': 1} and fonte == 'gemini', 'Gemini: refaz sem esquema quando o modelo recusa', res)
ok(corpos[0]['contents'][0]['parts'][0]['inlineData']['mimeType'] == 'image/png', 'Gemini recebe a imagem em base64')

import anthropic
capturado = {}
class ClienteFalso:
    def __init__(self, **kw): capturado['cliente'] = kw; self.beta = self; self.messages = self
    def create(self, **kw):
        capturado['params'] = kw
        class B: type = 'text'; text = '{"ok": true}'
        class R: stop_reason = 'end_turn'; content = [B()]
        return R()
anthropic.Anthropic = ClienteFalso
config.IA_ORDEM = ['anthropic']
res, fonte = ip.gerar('sis', 'txt', [{'nome': 'n.pdf', 'mime': 'application/pdf', 'dados': PDF}], {'type': 'object'})
pp = capturado['params']
ok(res == {'ok': True} and fonte == 'anthropic', 'Claude: resposta JSON lida', res)
ok(pp['model'] == 'claude-opus-5-5' and pp['fallbacks'] == 'default', 'Claude: modelo padrão com reserva do servidor', pp.get('model'))
ok(pp['output_config']['format']['type'] == 'json_schema', 'Claude: saída estruturada pelo esquema')
ok(pp['messages'][0]['content'][0]['type'] == 'document', 'Claude: PDF vai como documento')
ok('temperature' not in pp, 'Claude: sem parâmetros removidos da API')

print('\n' + ('ASSISTENTE OK' if not falhas else f'{len(falhas)} FALHA(S)'))
sys.exit(1 if falhas else 0)
