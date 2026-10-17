# -*- coding: utf-8 -*-
"""Camada de acesso a dados — SQLite (local) ou Postgres/Supabase (produção).

Três responsabilidades:

1. Conexão por requisição (`get_db`). Em SQLite, com chaves estrangeiras
   ligadas e WAL — WAL evita o clássico "database is locked" quando o gestor
   salva o ponto enquanto o mestre lança uma despesa. Em Postgres, uma conexão
   por requisição com linhas em formato de dicionário.
2. Tradução de dialeto (ver `obra/sql.py`): o app escreve SQL no dialeto do
   SQLite e a camada converte na saída quando o destino é o Postgres. Nenhum
   blueprint precisa saber em qual banco está rodando.
3. Migração idempotente do schema (`migrar`), executada no boot: subir uma
   versão nova por cima de um banco antigo nunca perde dados.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

from . import config
from .sql import RLS_POSTGRES, SCHEMA_POSTGRES, alteracoes_postgres, para_postgres

log = logging.getLogger("obra-erp")

SCHEMA_VERSION = 13

EH_POSTGRES = config.BACKEND == "postgres"

# Erros que indicam "a conexão não existe mais", e não "a consulta está errada".
_SINAIS_DE_CONEXAO_MORTA = (
    "connection is closed", "server closed the connection", "connection already closed",
    "terminating connection", "SSL connection has been closed", "consuming input failed",
    "no connection to the server", "EOF detected",
)


def _parece_conexao_morta(erro: Exception) -> bool:
    texto_erro = f"{type(erro).__name__}: {erro}".lower()
    if "interfaceerror" in texto_erro or "operationalerror" in texto_erro:
        return True
    return any(sinal.lower() in texto_erro for sinal in _SINAIS_DE_CONEXAO_MORTA)


# ---------------------------------------------------------------- driver PG

def _driver_postgres():
    """Importa psycopg 3 ou, na falta dele, psycopg2. Só é chamado se preciso."""
    try:
        import psycopg
        from psycopg.rows import dict_row
        return "psycopg3", psycopg, dict_row
    except ImportError:
        pass
    try:
        import psycopg2
        import psycopg2.extras
        return "psycopg2", psycopg2, psycopg2.extras.RealDictCursor
    except ImportError as erro:
        raise RuntimeError(
            "DATABASE_URL aponta para Postgres, mas o driver não está instalado. "
            "Rode: pip install 'psycopg[binary]'"
        ) from erro


def _conectar_postgres():
    """Importa psycopg 3 ou, na falta dele, psycopg2. Só é chamado se preciso."""
    variante, driver, fabrica_linha = _driver_postgres()
    if variante == "psycopg3":
        # Adicionado prepare_threshold=None para evitar erro de 'DuplicatePreparedStatement'
        # no pooler de conexões do Render/Supabase.
        return driver.connect(
            config.DATABASE_URL, 
            row_factory=fabrica_linha, 
            autocommit=False, 
            prepare_threshold=None
        )
    return driver.connect(config.DATABASE_URL, cursor_factory=fabrica_linha)


class _ResultadoPG:
    """Imita a interface do cursor do sqlite3 (fetchone/fetchall/lastrowid)."""

    def __init__(self, cursor, sql: str):
        self._cursor = cursor
        self.rowcount = cursor.rowcount
        self.lastrowid = None
        if "RETURNING" in sql.upper():
            linha = cursor.fetchone()
            if linha is not None:
                # dict_row/RealDictCursor devolvem dicionário
                self.lastrowid = linha["id"] if isinstance(linha, dict) else linha[0]
            cursor.close()
            self._cursor = None

    def fetchone(self):
        if self._cursor is None:
            return None
        linha = self._cursor.fetchone()
        self._cursor.close()
        self._cursor = None
        return linha

    def fetchall(self):
        if self._cursor is None:
            return []
        linhas = self._cursor.fetchall()
        self._cursor.close()
        self._cursor = None
        return linhas


class Conexao:
    """Fachada única sobre sqlite3.Connection e a conexão do psycopg."""

    def __init__(self, bruta, postgres: bool, reaproveitada: bool = False):
        self.bruta = bruta
        self.postgres = postgres
        self.reaproveitada = reaproveitada   # veio do pool
        self.quebrada = False                 # não deve voltar para o pool

    def execute(self, sql: str, args=()):
        if not self.postgres:
            return self.bruta.execute(sql, args)

        traduzido = para_postgres(sql)
        try:
            return self._executar_postgres(traduzido, args)
        except Exception as erro:
            # Conexão guardada no pool pode ter sido encerrada do outro lado
            # sem aviso. Nesse caso — e só nele — abrimos outra e repetimos.
            # Vale apenas para a primeira consulta da requisição: depois disso
            # já pode haver trabalho na transação, e repetir seria perigoso.
            if not (self.reaproveitada and _parece_conexao_morta(erro)):
                self.quebrada = True
                raise

            log.info("Conexão do pool estava morta; reconectando.")
            try:
                self.bruta.close()
            except Exception:
                pass
            self.bruta = _conectar_postgres()
            self.reaproveitada = False
            return self._executar_postgres(traduzido, args)

    def _executar_postgres(self, traduzido: str, args):
        cursor = self.bruta.cursor()
        cursor.execute(traduzido, tuple(args))
        self.reaproveitada = False   # a partir daqui a conexão já provou que vive
        return _ResultadoPG(cursor, traduzido)

    def commit(self):
        self.bruta.commit()

    def rollback(self):
        try:
            self.bruta.rollback()
        except Exception:
            pass

    def close(self):
        self.bruta.close()


# ---------------------------------------------------------------- pool
#
# Abrir uma conexão nova a cada requisição é o maior custo isolado do app em
# produção: o aperto de mão com o pooler do Supabase leva mais tempo que todas
# as consultas da tela somadas. Aqui as conexões ficam guardadas e são
# reaproveitadas entre requisições do mesmo processo.
#
# É um pool deliberadamente simples — sem fila de espera nem timeout global.
# Quando o limite é atingido, abre-se uma conexão avulsa que é fechada no fim:
# degrada em vez de travar, que é o comportamento certo para um app onde três
# pessoas mexem ao mesmo tempo.

_POOL: deque = deque()
_POOL_LOCK = threading.Lock()
POOL_MAX = int(config.variavel("DB_POOL_MAX", "4") or 4)
IDADE_MAXIMA = 120.0   # segundos ocioso; acima disso a conexão pode ter morrido


def _pegar_do_pool():
    agora = time.time()
    with _POOL_LOCK:
        while _POOL:
            conexao, guardada_em = _POOL.popleft()
            if agora - guardada_em < IDADE_MAXIMA:
                return conexao
            try:
                conexao.close()   # ociosa demais: o servidor pode já ter desligado
            except Exception:
                pass
    return None


def _devolver_ao_pool(conexao) -> bool:
    with _POOL_LOCK:
        if len(_POOL) >= POOL_MAX:
            return False
        _POOL.append((conexao, time.time()))
        return True


def _conexao_postgres_reaproveitada():
    """Conexão pronta para uso, do pool ou nova.

    A conexão guardada pode ter sido encerrada do outro lado sem aviso. Em vez
    de pagar um `SELECT 1` de verificação a cada requisição (que anularia boa
    parte do ganho), testamos na prática: se a primeira consulta falhar por
    conexão morta, `Conexao.execute` abre outra e repete uma única vez.
    """
    reaproveitada = _pegar_do_pool()
    if reaproveitada is not None:
        return reaproveitada, True
    return _conectar_postgres(), False


# ---------------------------------------------------------------- conexão

def get_db() -> Conexao:
    if "db" not in g:
        if EH_POSTGRES:
            bruta, do_pool = _conexao_postgres_reaproveitada()
            g.db = Conexao(bruta, True, reaproveitada=do_pool)
        else:
            bruta = sqlite3.connect(config.DB_PATH, timeout=15)
            bruta.row_factory = sqlite3.Row
            bruta.execute("PRAGMA foreign_keys = ON;")
            bruta.execute("PRAGMA busy_timeout = 8000;")
            g.db = Conexao(bruta, False)
    return g.db


def fechar_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is None:
        return

    if db.postgres:
        # Devolve ao pool em estado limpo. Se o pool estiver cheio — ou a
        # conexão tiver dado problema —, fecha de vez.
        db.rollback()
        if not db.quebrada and _devolver_ao_pool(db.bruta):
            return
        try:
            db.close()
        except Exception:
            pass
        return

    if db is not None:
        # No Postgres, uma transação abortada precisa de rollback antes de
        # devolver a conexão; no SQLite o rollback é inofensivo.
        db.rollback()
        db.close()


def um(sql: str, args=()):
    return get_db().execute(sql, args).fetchone()


def todos(sql: str, args=()):
    return get_db().execute(sql, args).fetchall()


def executar(sql: str, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur


# ---------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL DEFAULT 'gestor',
    ativo INTEGER NOT NULL DEFAULT 1,
    senha_provisoria INTEGER NOT NULL DEFAULT 0,
    ultimo_acesso TEXT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS obras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    cliente TEXT DEFAULT '',
    endereco TEXT DEFAULT '',
    orcamento_total REAL NOT NULL DEFAULT 0,
    data_inicio TEXT,
    previsao_fim TEXT,
    status TEXT NOT NULL DEFAULT 'ativa',
    sigla TEXT DEFAULT '',
    arquivada INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS usuario_obras (
    usuario_id INTEGER NOT NULL,
    obra_id INTEGER NOT NULL,
    PRIMARY KEY (usuario_id, obra_id),
    FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS etapas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    ordem INTEGER NOT NULL DEFAULT 0,
    peso REAL NOT NULL DEFAULT 0,
    percentual REAL NOT NULL DEFAULT 0,
    custo_previsto REAL NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS semanas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    numero INTEGER NOT NULL,
    data_inicio TEXT,
    meta TEXT DEFAULT '',
    meta_atingida INTEGER NOT NULL DEFAULT 0,
    orcamento_previsto REAL NOT NULL DEFAULT 0,
    fechada INTEGER NOT NULL DEFAULT 0,
    folha_paga INTEGER NOT NULL DEFAULT 0,
    folha_paga_em TEXT,
    folha_valor_pago REAL NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS funcionarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    funcao TEXT DEFAULT '',
    tipo_contrato TEXT NOT NULL DEFAULT 'fixo',
    chave_pix TEXT DEFAULT '',
    valor_hora REAL NOT NULL DEFAULT 0,
    documento TEXT DEFAULT '',
    telefone TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    semana_id INTEGER NOT NULL,
    dia_semana TEXT NOT NULL,
    data TEXT,
    producao_dia TEXT DEFAULT '',
    clima TEXT DEFAULT '',
    observacoes TEXT DEFAULT '',
    assinado_por TEXT DEFAULT '',
    assinado_em TEXT,
    atualizado_em TEXT,
    UNIQUE (semana_id, dia_semana),
    FOREIGN KEY (semana_id) REFERENCES semanas (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fotos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia_id INTEGER NOT NULL,
    arquivo TEXT NOT NULL,
    nome_original TEXT DEFAULT '',
    legenda TEXT DEFAULT '',
    ambiente TEXT DEFAULT '',
    largura INTEGER DEFAULT 0,
    altura INTEGER DEFAULT 0,
    criado_em TEXT NOT NULL,
    FOREIGN KEY (dia_id) REFERENCES dias (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS horas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia_id INTEGER NOT NULL,
    funcionario_id INTEGER NOT NULL,
    horas_trabalhadas REAL NOT NULL DEFAULT 0,
    valor_hora REAL NOT NULL DEFAULT 0,
    UNIQUE (dia_id, funcionario_id),
    FOREIGN KEY (dia_id) REFERENCES dias (id) ON DELETE CASCADE,
    FOREIGN KEY (funcionario_id) REFERENCES funcionarios (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS medicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia_id INTEGER NOT NULL,
    etapa_id INTEGER,
    descricao TEXT NOT NULL,
    quantidade REAL NOT NULL DEFAULT 0,
    unidade TEXT DEFAULT 'm²',
    criado_em TEXT NOT NULL,
    FOREIGN KEY (dia_id) REFERENCES dias (id) ON DELETE CASCADE,
    FOREIGN KEY (etapa_id) REFERENCES etapas (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS despesas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dia_id INTEGER NOT NULL,
    etapa_id INTEGER,
    categoria TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    fornecedor TEXT DEFAULT '',
    valor REAL NOT NULL DEFAULT 0,
    status_pagamento TEXT NOT NULL,
    numero_nota TEXT DEFAULT '',
    grupo_parcela TEXT DEFAULT '',
    grupo_tipo TEXT DEFAULT 'parcela',
    parcela_numero INTEGER DEFAULT 0,
    parcela_total INTEGER DEFAULT 0,
    vencimento TEXT,
    pago_em TEXT,
    imagem_path TEXT DEFAULT '',
    nome_arquivo TEXT DEFAULT '',
    reembolsar_para TEXT DEFAULT '',
    criado_por INTEGER,
    criado_em TEXT NOT NULL,
    FOREIGN KEY (dia_id) REFERENCES dias (id) ON DELETE CASCADE,
    FOREIGN KEY (etapa_id) REFERENCES etapas (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS despesa_anexos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    despesa_id INTEGER NOT NULL,
    arquivo TEXT NOT NULL,
    nome_original TEXT DEFAULT '',
    tamanho INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    FOREIGN KEY (despesa_id) REFERENCES despesas (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_despesa_anexos_despesa ON despesa_anexos (despesa_id);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER,
    usuario_nome TEXT DEFAULT '',
    acao TEXT NOT NULL,
    entidade TEXT DEFAULT '',
    entidade_id INTEGER,
    detalhe TEXT DEFAULT '',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    semana_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    percentual REAL NOT NULL DEFAULT 0,
    percentual_inicial REAL NOT NULL DEFAULT 0,
    etapa_id INTEGER,
    origem_meta_id INTEGER,
    semanas_arrastada INTEGER NOT NULL DEFAULT 0,
    concluida INTEGER NOT NULL DEFAULT 0,
    concluida_em TEXT,
    transferida INTEGER NOT NULL DEFAULT 0,
    ordem INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE,
    FOREIGN KEY (semana_id) REFERENCES semanas (id) ON DELETE CASCADE,
    FOREIGN KEY (etapa_id) REFERENCES etapas (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS meta_progresso (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meta_id INTEGER NOT NULL,
    dia_id INTEGER NOT NULL,
    percentual REAL NOT NULL DEFAULT 0,
    observacao TEXT DEFAULT '',
    usuario_nome TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    UNIQUE (meta_id, dia_id),
    FOREIGN KEY (meta_id) REFERENCES metas (id) ON DELETE CASCADE,
    FOREIGN KEY (dia_id) REFERENCES dias (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_metas_semana ON metas (semana_id);
CREATE INDEX IF NOT EXISTS idx_progresso_meta ON meta_progresso (meta_id);
CREATE INDEX IF NOT EXISTS idx_progresso_dia ON meta_progresso (dia_id);

CREATE TABLE IF NOT EXISTS contratos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    funcionario_id INTEGER,
    empresa TEXT NOT NULL,
    servico TEXT NOT NULL,
    etapa_id INTEGER,
    valor_total REAL NOT NULL DEFAULT 0,
    responsavel TEXT DEFAULT '',
    telefone TEXT DEFAULT '',
    observacoes TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ativo',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE,
    FOREIGN KEY (funcionario_id) REFERENCES funcionarios (id) ON DELETE SET NULL,
    FOREIGN KEY (etapa_id) REFERENCES etapas (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS contrato_medicoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contrato_id INTEGER NOT NULL,
    semana_id INTEGER NOT NULL,
    percentual REAL NOT NULL DEFAULT 0,
    valor REAL NOT NULL DEFAULT 0,
    entra_no_caixa INTEGER NOT NULL DEFAULT 1,
    despesa_id INTEGER,
    observacao TEXT DEFAULT '',
    criado_por TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    UNIQUE (contrato_id, semana_id),
    FOREIGN KEY (contrato_id) REFERENCES contratos (id) ON DELETE CASCADE,
    FOREIGN KEY (semana_id) REFERENCES semanas (id) ON DELETE CASCADE,
    FOREIGN KEY (despesa_id) REFERENCES despesas (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_contratos_obra ON contratos (obra_id);
CREATE INDEX IF NOT EXISTS idx_cmedicoes_contrato ON contrato_medicoes (contrato_id);
CREATE INDEX IF NOT EXISTS idx_cmedicoes_semana ON contrato_medicoes (semana_id);

CREATE TABLE IF NOT EXISTS locacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    equipamento TEXT NOT NULL,
    fornecedor TEXT DEFAULT '',
    etapa_id INTEGER,
    valor_parcela REAL NOT NULL DEFAULT 0,
    frequencia TEXT NOT NULL DEFAULT 'mensal',
    data_inicio TEXT NOT NULL,
    ciclos INTEGER NOT NULL DEFAULT 1,
    data_fim TEXT,
    status TEXT NOT NULL DEFAULT 'ativa',
    observacoes TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    encerrado_em TEXT,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE,
    FOREIGN KEY (etapa_id) REFERENCES etapas (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS locacao_parcelas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    locacao_id INTEGER NOT NULL,
    numero INTEGER NOT NULL,
    vencimento TEXT NOT NULL,
    valor REAL NOT NULL DEFAULT 0,
    despesa_id INTEGER,
    status TEXT NOT NULL DEFAULT 'prevista',
    criado_em TEXT NOT NULL,
    UNIQUE (locacao_id, numero),
    FOREIGN KEY (locacao_id) REFERENCES locacoes (id) ON DELETE CASCADE,
    FOREIGN KEY (despesa_id) REFERENCES despesas (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_locacoes_obra ON locacoes (obra_id);
CREATE INDEX IF NOT EXISTS idx_parcelas_locacao ON locacao_parcelas (locacao_id);
CREATE INDEX IF NOT EXISTS idx_parcelas_venc ON locacao_parcelas (vencimento);

CREATE INDEX IF NOT EXISTS idx_semanas_obra ON semanas (obra_id);
CREATE INDEX IF NOT EXISTS idx_dias_semana ON dias (semana_id);
CREATE INDEX IF NOT EXISTS idx_horas_dia ON horas (dia_id);
CREATE INDEX IF NOT EXISTS idx_despesas_dia ON despesas (dia_id);
CREATE INDEX IF NOT EXISTS idx_fotos_dia ON fotos (dia_id);
CREATE INDEX IF NOT EXISTS idx_func_obra ON funcionarios (obra_id);
CREATE INDEX IF NOT EXISTS idx_etapas_obra ON etapas (obra_id);
CREATE INDEX IF NOT EXISTS idx_medicoes_dia ON medicoes (dia_id);
"""

# Colunas acrescentadas depois da v1 do app. Bancos criados pelo SCHEMA acima
# já nascem com elas; bancos antigos recebem via ALTER TABLE.
COLUNAS_EXTRAS = [
    ("obras", "cliente", "TEXT DEFAULT ''"),
    ("obras", "endereco", "TEXT DEFAULT ''"),
    ("obras", "orcamento_total", "REAL NOT NULL DEFAULT 0"),
    ("obras", "data_inicio", "TEXT"),
    ("obras", "previsao_fim", "TEXT"),
    ("obras", "status", "TEXT NOT NULL DEFAULT 'ativa'"),
    ("obras", "arquivada", "INTEGER NOT NULL DEFAULT 0"),
    ("obras", "atualizado_em", "TEXT"),
    ("obras", "sigla", "TEXT DEFAULT ''"),
    ("semanas", "data_inicio", "TEXT"),
    ("semanas", "orcamento_previsto", "REAL NOT NULL DEFAULT 0"),
    ("semanas", "fechada", "INTEGER NOT NULL DEFAULT 0"),
    ("semanas", "folha_paga", "INTEGER NOT NULL DEFAULT 0"),
    ("semanas", "folha_paga_em", "TEXT"),
    ("semanas", "folha_valor_pago", "REAL NOT NULL DEFAULT 0"),
    ("dias", "data", "TEXT"),
    ("dias", "assinado_por", "TEXT DEFAULT ''"),
    ("dias", "assinado_em", "TEXT"),
    ("dias", "atualizado_em", "TEXT"),
    ("funcionarios", "documento", "TEXT DEFAULT ''"),
    ("funcionarios", "telefone", "TEXT DEFAULT ''"),
    ("funcionarios", "criado_em", "TEXT"),
    ("funcionarios", "tipo_contrato", "TEXT NOT NULL DEFAULT 'fixo'"),
    ("funcionarios", "chave_pix", "TEXT DEFAULT ''"),
    ("contratos", "funcionario_id", "INTEGER"),
    ("horas", "valor_hora", "REAL NOT NULL DEFAULT 0"),
    ("despesas", "etapa_id", "INTEGER"),
    ("despesas", "fornecedor", "TEXT DEFAULT ''"),
    ("despesas", "vencimento", "TEXT"),
    ("despesas", "pago_em", "TEXT"),
    ("despesas", "nome_arquivo", "TEXT DEFAULT ''"),
    ("despesas", "criado_por", "INTEGER"),
    ("despesas", "numero_nota", "TEXT DEFAULT ''"),
    ("despesas", "grupo_parcela", "TEXT DEFAULT ''"),
    ("despesas", "grupo_tipo", "TEXT DEFAULT 'parcela'"),
    ("despesas", "parcela_numero", "INTEGER DEFAULT 0"),
    ("despesas", "parcela_total", "INTEGER DEFAULT 0"),
    ("despesas", "reembolsar_para", "TEXT DEFAULT ''"),
]

# Até a v3.2 cada lançamento tinha um único anexo, guardado direto em
# despesas.imagem_path. Agora os anexos moram em despesa_anexos (vários por
# lançamento) e imagem_path segue espelhando o primeiro, para relatórios e
# telas antigas. Esta cópia roda a cada boot e é idempotente: só entra quem
# tem arquivo e ainda não tem nenhuma linha na tabela nova.
COPIAR_ANEXOS_ANTIGOS = """
INSERT INTO despesa_anexos (despesa_id, arquivo, nome_original, tamanho, criado_em)
SELECT x.id, x.imagem_path, COALESCE(x.nome_arquivo, ''), 0, x.criado_em
  FROM despesas x
 WHERE x.imagem_path IS NOT NULL AND x.imagem_path != ''
   AND NOT EXISTS (SELECT 1 FROM despesa_anexos a WHERE a.despesa_id = x.id)
"""


def _colunas(cur, tabela: str) -> set[str]:
    return {r[1] for r in cur.execute(f"PRAGMA table_info({tabela})").fetchall()}


def _segunda_feira(dt: datetime) -> str:
    return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")


def migrar() -> None:
    """Cria/atualiza o schema e garante o usuário gestor inicial."""
    if EH_POSTGRES:
        _migrar_postgres()
        return
    _migrar_sqlite()


def _migrar_postgres() -> None:
    """Schema no Postgres do Supabase.

    Não há backfill aqui de propósito: o backfill existe para consertar dados
    da v1, que só existem no arquivo SQLite. Quem vem da v1 roda antes o
    `ferramentas/migrar_para_supabase.py`, que importa o banco já corrigido.
    """
    conexao = _conectar_postgres()
    try:
        with conexao.cursor() as cur:
            cur.execute(SCHEMA_POSTGRES)
        conexao.commit()

        # Colunas novas em tabelas que já existem.
        #
        # `CREATE TABLE IF NOT EXISTS` cria o que falta, mas não toca em tabela
        # existente — então um banco criado numa versão anterior ficava sem as
        # colunas adicionadas depois, e o app quebrava com "column does not
        # exist" só em produção. Cada ALTER roda isolado: um que falhe (por
        # permissão, por exemplo) não pode abortar a transação e derrubar os
        # seguintes.
        faltando = []
        for comando in alteracoes_postgres(COLUNAS_EXTRAS):
            try:
                with conexao.cursor() as cur:
                    cur.execute(comando)
                conexao.commit()
            except Exception as erro:  # noqa: BLE001
                conexao.rollback()
                faltando.append(f"{comando} → {erro}")

        if faltando:
            log.warning("Colunas que não puderam ser criadas no Postgres:\n%s",
                        "\n".join(faltando))

        try:
            with conexao.cursor() as cur:
                cur.execute(COPIAR_ANEXOS_ANTIGOS)
            conexao.commit()
        except Exception as erro:  # noqa: BLE001
            conexao.rollback()
            log.warning("Não foi possível copiar os anexos antigos: %s", erro)

        # RLS é best-effort: em alguns papéis do Postgres o ALTER TABLE pode
        # não ser permitido, e isso não deve impedir o app de subir.
        try:
            with conexao.cursor() as cur:
                cur.execute(RLS_POSTGRES)
            conexao.commit()
        except Exception:
            conexao.rollback()

        with conexao.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM usuarios")
            linha = cur.fetchone()
            total = linha["total"] if isinstance(linha, dict) else linha[0]
            if not total:
                cur.execute(
                    """INSERT INTO usuarios (nome, email, senha_hash, papel, ativo,
                                             senha_provisoria, criado_em)
                       VALUES (%s, %s, %s, 'gestor', 1, %s, %s)""",
                    (
                        config.ADMIN_NOME,
                        config.ADMIN_EMAIL,
                        generate_password_hash(config.ADMIN_SENHA),
                        1 if config.ADMIN_SENHA == "obra123" else 0,
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
        conexao.commit()
    finally:
        conexao.close()


def _migrar_sqlite() -> None:
    conn = sqlite3.connect(config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.executescript(SCHEMA)
    conn.commit()

    for tabela, coluna, definicao in COLUNAS_EXTRAS:
        existe = cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabela,)
        ).fetchone()
        if not existe:
            continue
        if coluna not in _colunas(cur, tabela):
            # SQLite não aceita NOT NULL sem default em ALTER TABLE — todas as
            # definições acima já trazem DEFAULT justamente por isso.
            cur.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")
    conn.commit()

    versao_atual = cur.execute("PRAGMA user_version").fetchone()[0]

    _backfill(conn, cur)
    cur.execute(COPIAR_ANEXOS_ANTIGOS)
    conn.commit()
    if versao_atual < 4:
        _reparar_semanas_colapsadas(conn, cur)
    if versao_atual < 5:
        _converter_metas_texto(conn, cur)
    _seed_usuario_admin(conn, cur)

    cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
    conn.commit()
    conn.close()


def _backfill(conn, cur) -> None:
    """Preenche dados que a v1 não guardava, sem sobrescrever nada existente."""

    # 1) Snapshot do valor/hora em pontos antigos (mantém histórico estável).
    cur.execute(
        """UPDATE horas
              SET valor_hora = COALESCE(
                    (SELECT f.valor_hora FROM funcionarios f WHERE f.id = horas.funcionario_id), 0)
            WHERE valor_hora IS NULL OR valor_hora = 0"""
    )

    # 2) Semanas sem data de início.
    #
    #    A v1 não guardava data nenhuma, então precisamos inferir. Estimar cada
    #    semana isoladamente pelo seu `criado_em` produz lixo: se o gestor
    #    cadastrou as semanas 1, 2 e 3 na mesma tarde, as três cairiam na mesma
    #    segunda-feira. Como semanas de obra são consecutivas por definição,
    #    ancoramos em uma referência por obra e encadeamos de 7 em 7 dias
    #    usando o número da semana.
    _backfill_datas_semanas(conn, cur)

    # 3) Datas dos dias derivadas da data de início da semana.
    cur.execute(
        """UPDATE dias
              SET data = (
                SELECT date(s.data_inicio, '+' || (
                    CASE dias.dia_semana
                        WHEN 'Segunda' THEN 0 WHEN 'Terça' THEN 1 WHEN 'Quarta' THEN 2
                        WHEN 'Quinta' THEN 3 WHEN 'Sexta' THEN 4 WHEN 'Sábado' THEN 5
                        ELSE 6 END) || ' days')
                FROM semanas s WHERE s.id = dias.semana_id)
            WHERE (data IS NULL OR data = '')"""
    )

    # 4) Anexos antigos ficavam com o caminho público completo; hoje guardamos
    #    só o nome do arquivo e servimos por rota autenticada.
    cur.execute(
        """UPDATE despesas
              SET imagem_path = replace(imagem_path, '/static/uploads/', '')
            WHERE imagem_path LIKE '/static/uploads/%'"""
    )

    # 5) Despesas já pagas ganham data de pagamento (usa a data do lançamento).
    cur.execute(
        """UPDATE despesas SET pago_em = date(criado_em)
            WHERE status_pagamento = 'Pago' AND (pago_em IS NULL OR pago_em = '')"""
    )

    cur.execute("UPDATE obras SET criado_em = COALESCE(criado_em, datetime('now'))")
    cur.execute("UPDATE funcionarios SET criado_em = COALESCE(criado_em, datetime('now'))")
    conn.commit()


def _backfill_datas_semanas(conn, cur) -> None:
    """Dá data de início às semanas antigas, encadeadas de 7 em 7 dias.

    Âncora, por obra e nesta ordem de preferência:
      1. uma semana que já tenha data (respeita o que o usuário já corrigiu);
      2. a segunda-feira da semana em que a primeira semana foi criada.
    A partir da âncora, `data = âncora + 7 dias × (numero - numero_âncora)`.
    """
    obras_pendentes = cur.execute(
        """SELECT DISTINCT obra_id FROM semanas
            WHERE data_inicio IS NULL OR data_inicio = ''"""
    ).fetchall()

    for linha_obra in obras_pendentes:
        obra_id = linha_obra["obra_id"]
        semanas = cur.execute(
            "SELECT id, numero, data_inicio, criado_em FROM semanas WHERE obra_id = ? ORDER BY numero",
            (obra_id,),
        ).fetchall()
        if not semanas:
            continue

        ancora_numero = ancora_data = None
        for semana in semanas:
            if semana["data_inicio"]:
                try:
                    ancora_data = datetime.fromisoformat(semana["data_inicio"][:10])
                    ancora_numero = semana["numero"]
                    break
                except ValueError:
                    continue

        if ancora_data is None:
            primeira = semanas[0]
            try:
                base = datetime.fromisoformat(primeira["criado_em"])
            except (TypeError, ValueError):
                base = datetime.now()
            ancora_data = datetime.fromisoformat(_segunda_feira(base))
            ancora_numero = primeira["numero"]

        for semana in semanas:
            if semana["data_inicio"]:
                continue
            deslocamento = (semana["numero"] - ancora_numero) * 7
            data = (ancora_data + timedelta(days=deslocamento)).strftime("%Y-%m-%d")
            cur.execute("UPDATE semanas SET data_inicio = ? WHERE id = ?", (data, semana["id"]))

    conn.commit()


DESLOCAMENTO_DIA = {
    "Segunda": 0, "Terça": 1, "Quarta": 2, "Quinta": 3,
    "Sexta": 4, "Sábado": 5, "Domingo": 6,
}


def _reparar_semanas_colapsadas(conn, cur) -> None:
    """Conserta bancos onde várias semanas ficaram com a MESMA data de início.

    Versões anteriores estimavam a data de cada semana isoladamente pelo seu
    `criado_em`. Quem cadastrou as semanas todas de uma vez acabou com elas
    empilhadas na mesma segunda-feira, o que bagunça relatório, alerta de dia
    sem RDO e vencimento. Aqui detectamos a colisão e reencadeamos de 7 em 7
    dias a partir da semana de menor número, corrigindo também os dias.

    Roda uma única vez (controlado por PRAGMA user_version).
    """
    obras_afetadas = cur.execute(
        """SELECT obra_id FROM semanas
            WHERE data_inicio IS NOT NULL AND data_inicio != ''
            GROUP BY obra_id
           HAVING COUNT(*) > COUNT(DISTINCT data_inicio)"""
    ).fetchall()

    for linha_obra in obras_afetadas:
        semanas = cur.execute(
            """SELECT id, numero, data_inicio FROM semanas
                WHERE obra_id = ? ORDER BY numero""",
            (linha_obra["obra_id"],),
        ).fetchall()
        if len(semanas) < 2:
            continue

        try:
            ancora = datetime.fromisoformat(semanas[0]["data_inicio"][:10])
        except (TypeError, ValueError):
            continue
        numero_base = semanas[0]["numero"]

        for semana in semanas:
            data = (ancora + timedelta(days=(semana["numero"] - numero_base) * 7))
            cur.execute("UPDATE semanas SET data_inicio = ? WHERE id = ?",
                        (data.strftime("%Y-%m-%d"), semana["id"]))
            # os dias herdavam a data errada da semana: recalcula todos
            for dia in cur.execute(
                "SELECT id, dia_semana FROM dias WHERE semana_id = ?", (semana["id"],)
            ).fetchall():
                deslocamento = DESLOCAMENTO_DIA.get(dia["dia_semana"], 0)
                cur.execute(
                    "UPDATE dias SET data = ? WHERE id = ?",
                    ((data + timedelta(days=deslocamento)).strftime("%Y-%m-%d"), dia["id"]),
                )

    conn.commit()


def _converter_metas_texto(conn, cur) -> None:
    """Transforma a meta em texto livre (v2.0) em meta com nome e percentual.

    Até a 2.1 a semana tinha um campo `meta` solto e um "meta atingida"
    sim/não. Quem já usava o app não pode perder isso: cada texto vira uma
    meta de verdade, com 100% se estava marcada como atingida. O campo antigo
    continua no banco, intocado, para não quebrar relatórios já emitidos.
    """
    semanas = cur.execute(
        """SELECT s.id, s.obra_id, s.meta, s.meta_atingida, s.criado_em
             FROM semanas s
            WHERE s.meta IS NOT NULL AND TRIM(s.meta) != ''
              AND NOT EXISTS (SELECT 1 FROM metas m WHERE m.semana_id = s.id)"""
    ).fetchall()

    for semana in semanas:
        atingida = 1 if semana["meta_atingida"] else 0
        cur.execute(
            """INSERT INTO metas (obra_id, semana_id, nome, descricao, percentual,
                                  percentual_inicial, semanas_arrastada, concluida,
                                  concluida_em, transferida, ordem, criado_em)
               VALUES (?, ?, ?, '', ?, 0, 0, ?, ?, 0, 0, ?)""",
            (
                semana["obra_id"], semana["id"], semana["meta"][:200],
                100.0 if atingida else 0.0, atingida,
                semana["criado_em"] if atingida else None,
                semana["criado_em"] or datetime.now().isoformat(timespec="seconds"),
            ),
        )
    conn.commit()


def _seed_usuario_admin(conn, cur) -> None:
    total = cur.execute("SELECT COUNT(*) AS t FROM usuarios").fetchone()["t"]
    if total:
        return
    cur.execute(
        """INSERT INTO usuarios (nome, email, senha_hash, papel, ativo, senha_provisoria, criado_em)
           VALUES (?, ?, ?, 'gestor', 1, ?, ?)""",
        (
            config.ADMIN_NOME,
            config.ADMIN_EMAIL,
            generate_password_hash(config.ADMIN_SENHA),
            1 if config.ADMIN_SENHA == "obra123" else 0,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def registrar_auditoria(usuario, acao: str, entidade: str = "", entidade_id=None, detalhe: str = "") -> None:
    """Trilha de auditoria — quem lançou o quê e quando.

    Em obra isso não é burocracia: quando o cliente questiona um lançamento
    seis meses depois, é o que responde "foi o mestre, na terça, às 17h".
    """
    try:
        executar(
            """INSERT INTO auditoria (usuario_id, usuario_nome, acao, entidade, entidade_id, detalhe, criado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                (usuario or {}).get("id"),
                (usuario or {}).get("nome", ""),
                acao,
                entidade,
                entidade_id,
                detalhe[:400],
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
    except Exception:
        # A auditoria nunca pode derrubar a operação principal — mas no
        # Postgres um erro deixa a transação em estado abortado, e a próxima
        # consulta da mesma requisição falharia com uma mensagem sem relação
        # com a causa. O rollback devolve a conexão a um estado utilizável;
        # o lançamento em si já foi confirmado antes desta chamada.
        try:
            get_db().rollback()
        except Exception:
            pass
