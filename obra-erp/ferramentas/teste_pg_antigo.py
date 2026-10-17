# -*- coding: utf-8 -*-
"""Banco Postgres ANTIGO recebendo uma versão nova do app.

Reproduz o erro de produção: o banco foi criado numa versão anterior, sem as
colunas adicionadas depois. Antes da correção, a migração só criava tabelas
novas e o app quebrava com "column ... does not exist".
"""
import os, sqlite3, sys
import os
import sys
import tempfile

# Caminho do projeto derivado da posição deste arquivo: assim a suíte roda em
# qualquer clone, sem depender de onde a pasta foi colocada.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

import psycopg_falso
BANCO = '/tmp/pg_antigo.db'
psycopg_falso.instalar(BANCO)

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

# --- 1. fabrica um banco "antigo": despesas sem numero_nota, funcionarios sem
#        tipo_contrato, contratos sem funcionario_id ---
if os.path.exists(BANCO):
    os.remove(BANCO)
c = sqlite3.connect(BANCO)
c.executescript("""
CREATE TABLE usuarios (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, email TEXT, senha_hash TEXT,
  papel TEXT DEFAULT 'gestor', ativo INTEGER DEFAULT 1, senha_provisoria INTEGER DEFAULT 0,
  ultimo_acesso TEXT, criado_em TEXT);
CREATE TABLE obras (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, cliente TEXT DEFAULT '',
  endereco TEXT DEFAULT '', orcamento_total REAL DEFAULT 0, data_inicio TEXT, previsao_fim TEXT,
  status TEXT DEFAULT 'ativa', arquivada INTEGER DEFAULT 0, criado_em TEXT, atualizado_em TEXT);
CREATE TABLE usuario_obras (usuario_id INTEGER, obra_id INTEGER, PRIMARY KEY (usuario_id, obra_id));
CREATE TABLE etapas (id INTEGER PRIMARY KEY AUTOINCREMENT, obra_id INTEGER, nome TEXT, ordem INTEGER DEFAULT 0,
  peso REAL DEFAULT 0, percentual REAL DEFAULT 0, custo_previsto REAL DEFAULT 0, criado_em TEXT);
CREATE TABLE semanas (id INTEGER PRIMARY KEY AUTOINCREMENT, obra_id INTEGER, numero INTEGER, data_inicio TEXT,
  meta TEXT DEFAULT '', meta_atingida INTEGER DEFAULT 0, orcamento_previsto REAL DEFAULT 0,
  fechada INTEGER DEFAULT 0, criado_em TEXT);
-- funcionarios SEM tipo_contrato/chave_pix
CREATE TABLE funcionarios (id INTEGER PRIMARY KEY AUTOINCREMENT, obra_id INTEGER, nome TEXT,
  funcao TEXT DEFAULT '', valor_hora REAL DEFAULT 0, documento TEXT DEFAULT '', telefone TEXT DEFAULT '',
  ativo INTEGER DEFAULT 1, criado_em TEXT);
CREATE TABLE dias (id INTEGER PRIMARY KEY AUTOINCREMENT, semana_id INTEGER, dia_semana TEXT, data TEXT,
  producao_dia TEXT DEFAULT '', clima TEXT DEFAULT '', observacoes TEXT DEFAULT '',
  assinado_por TEXT DEFAULT '', assinado_em TEXT, atualizado_em TEXT, UNIQUE (semana_id, dia_semana));
CREATE TABLE fotos (id INTEGER PRIMARY KEY AUTOINCREMENT, dia_id INTEGER, arquivo TEXT, nome_original TEXT,
  legenda TEXT DEFAULT '', ambiente TEXT DEFAULT '', largura INTEGER DEFAULT 0, altura INTEGER DEFAULT 0, criado_em TEXT);
CREATE TABLE horas (id INTEGER PRIMARY KEY AUTOINCREMENT, dia_id INTEGER, funcionario_id INTEGER,
  horas_trabalhadas REAL DEFAULT 0, valor_hora REAL DEFAULT 0, UNIQUE (dia_id, funcionario_id));
CREATE TABLE medicoes (id INTEGER PRIMARY KEY AUTOINCREMENT, dia_id INTEGER, etapa_id INTEGER, descricao TEXT,
  quantidade REAL DEFAULT 0, unidade TEXT DEFAULT 'm2', criado_em TEXT);
-- despesas SEM numero_nota
CREATE TABLE despesas (id INTEGER PRIMARY KEY AUTOINCREMENT, dia_id INTEGER, etapa_id INTEGER, categoria TEXT,
  descricao TEXT DEFAULT '', fornecedor TEXT DEFAULT '', valor REAL DEFAULT 0, status_pagamento TEXT,
  vencimento TEXT, pago_em TEXT, imagem_path TEXT DEFAULT '', nome_arquivo TEXT DEFAULT '',
  criado_por INTEGER, criado_em TEXT);
CREATE TABLE auditoria (id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER, usuario_nome TEXT,
  acao TEXT, entidade TEXT, entidade_id INTEGER, detalhe TEXT, criado_em TEXT);
-- contratos SEM funcionario_id
CREATE TABLE contratos (id INTEGER PRIMARY KEY AUTOINCREMENT, obra_id INTEGER, empresa TEXT, servico TEXT,
  etapa_id INTEGER, valor_total REAL DEFAULT 0, responsavel TEXT DEFAULT '', telefone TEXT DEFAULT '',
  observacoes TEXT DEFAULT '', status TEXT DEFAULT 'ativo', criado_em TEXT, atualizado_em TEXT);
CREATE TABLE contrato_medicoes (id INTEGER PRIMARY KEY AUTOINCREMENT, contrato_id INTEGER, semana_id INTEGER,
  percentual REAL DEFAULT 0, valor REAL DEFAULT 0, entra_no_caixa INTEGER DEFAULT 1, despesa_id INTEGER,
  observacao TEXT DEFAULT '', criado_por TEXT DEFAULT '', criado_em TEXT, UNIQUE (contrato_id, semana_id));
""")
ag = '2026-08-01T09:00:00'
c.execute("INSERT INTO obras (nome, criado_em) VALUES ('Obra em producao', ?)", (ag,))
c.execute("INSERT INTO semanas (obra_id,numero,data_inicio,criado_em) VALUES (1,1,'2026-08-03',?)", (ag,))
for d in ['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo']:
    c.execute("INSERT INTO dias (semana_id,dia_semana,data) VALUES (1,?,NULL)", (d,))
c.execute("INSERT INTO funcionarios (obra_id,nome,funcao,valor_hora,ativo,criado_em) VALUES (1,'Zé','Pedreiro',25,1,?)", (ag,))
c.execute("""INSERT INTO despesas (dia_id,categoria,descricao,fornecedor,valor,status_pagamento,criado_em)
             VALUES (1,'Material','Cimento antigo','Casa do Construtor',500,'Pago',?)""", (ag,))
c.execute("INSERT INTO contratos (obra_id,empresa,servico,valor_total,status,criado_em) VALUES (1,'Pinturas','Pintura',10000,'ativo',?)", (ag,))
c.commit(); c.close()

antes = sqlite3.connect(BANCO)
colunas_antes = {r[1] for r in antes.execute("PRAGMA table_info(despesas)")}
antes.close()
ok('numero_nota' not in colunas_antes, 'banco começa SEM a coluna numero_nota (como na produção)')

# --- 2. sobe o app novo sobre esse banco ---
os.environ['DATABASE_URL'] = 'postgresql://x:y@db.projeto.supabase.co:5432/postgres'
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))
from obra import create_app
app = create_app()
c2 = app.test_client()

depois = sqlite3.connect(BANCO)
def colunas(t): return {r[1] for r in depois.execute(f"PRAGMA table_info({t})")}
ok('numero_nota' in colunas('despesas'), 'migração adiciona numero_nota', colunas('despesas'))
ok('tipo_contrato' in colunas('funcionarios'), 'migração adiciona tipo_contrato', colunas('funcionarios'))
ok('chave_pix' in colunas('funcionarios'), 'migração adiciona chave_pix')
ok('funcionario_id' in colunas('contratos'), 'migração adiciona funcionario_id em contratos')
depois.close()

# --- 3. o endpoint que quebrou em produção ---
c2.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
H = {'X-CSRF-Token': c2.get('/api/sessao').get_json()['csrf']}
r = c2.get('/api/semanas/1/fechamento')
ok(r.status_code == 200, 'GET /api/semanas/1/fechamento volta a funcionar', r.status_code)
ok(r.get_json()['despesas'][0]['descricao'] == 'Cimento antigo', 'dados antigos preservados',
   r.get_json()['despesas'][0])
ok(r.get_json()['despesas'][0].get('numero_nota') in ('', None), 'despesa antiga fica com nota vazia',
   r.get_json()['despesas'][0].get('numero_nota'))

r = c2.get('/api/dias/1/despesas')
ok(r.status_code == 200, 'GET /api/dias/1/despesas volta a funcionar', r.status_code)
r = c2.get('/api/obras/1/financeiro')
ok(r.status_code == 200, 'painel financeiro funciona', r.status_code)
r = c2.get('/api/obras/1/dashboard')
ok(r.status_code == 200, 'dashboard funciona', r.status_code)
r = c2.get('/api/obras/1/funcionarios')
ok(r.status_code == 200 and r.get_json()[0]['nome'] == 'Zé',
   'funcionário antigo continua na equipe', r.get_json()[0] if r.status_code == 200 else r.status_code)

r = c2.post('/api/dias/1/despesas', data={'categoria':'Material','descricao':'Nova com nota',
    'valor':'100','status_pagamento':'Pago','numero_nota':'55555'},
    headers=H, content_type='multipart/form-data')
ok(r.status_code == 201, 'lançar despesa com nota no banco migrado', r.get_json())
r = c2.get('/api/semanas/1/relatorio.pdf')
ok(r.status_code == 200 and r.data[:4] == b'%PDF', 'PDF gera', r.status_code)

# --- 4. rodar de novo não quebra (idempotência) ---
app2 = create_app()
c3 = app2.test_client()
c3.post('/api/auth/login', json={'email':'gestor@obra.local','senha':'obra123'})
ok(c3.get('/api/semanas/1/fechamento').status_code == 200, 'segundo boot é idempotente')

print('\n' + ('POSTGRES ANTIGO OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
