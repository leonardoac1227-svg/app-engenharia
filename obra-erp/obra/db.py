# -*- coding: utf-8 -*-
"""Camada de acesso a dados (SQLite).

Duas responsabilidades:

1. Conexão por requisição (`get_db`), com chaves estrangeiras ligadas e WAL
   habilitado — WAL evita o clássico "database is locked" quando o gestor
   salva o ponto enquanto o mestre lança uma despesa.
2. Migração idempotente do schema (`migrar`). O app roda a migração no boot,
   então subir uma versão nova por cima de um banco antigo nunca perde dados:
   tabelas e colunas que faltam são criadas, e valores legados recebem
   backfill (ex.: semanas antigas ganham uma data de início estimada).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from flask import g
from werkzeug.security import generate_password_hash

from . import config

SCHEMA_VERSION = 3


# ---------------------------------------------------------------- conexão

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(config.DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 8000;")
        g.db = conn
    return g.db


def fechar_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
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
    criado_em TEXT NOT NULL,
    FOREIGN KEY (obra_id) REFERENCES obras (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS funcionarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    obra_id INTEGER NOT NULL,
    nome TEXT NOT NULL,
    funcao TEXT DEFAULT '',
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
    vencimento TEXT,
    pago_em TEXT,
    imagem_path TEXT DEFAULT '',
    nome_arquivo TEXT DEFAULT '',
    criado_por INTEGER,
    criado_em TEXT NOT NULL,
    FOREIGN KEY (dia_id) REFERENCES dias (id) ON DELETE CASCADE,
    FOREIGN KEY (etapa_id) REFERENCES etapas (id) ON DELETE SET NULL
);

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
    ("semanas", "data_inicio", "TEXT"),
    ("semanas", "orcamento_previsto", "REAL NOT NULL DEFAULT 0"),
    ("semanas", "fechada", "INTEGER NOT NULL DEFAULT 0"),
    ("dias", "data", "TEXT"),
    ("dias", "assinado_por", "TEXT DEFAULT ''"),
    ("dias", "assinado_em", "TEXT"),
    ("dias", "atualizado_em", "TEXT"),
    ("funcionarios", "documento", "TEXT DEFAULT ''"),
    ("funcionarios", "telefone", "TEXT DEFAULT ''"),
    ("funcionarios", "criado_em", "TEXT"),
    ("horas", "valor_hora", "REAL NOT NULL DEFAULT 0"),
    ("despesas", "etapa_id", "INTEGER"),
    ("despesas", "fornecedor", "TEXT DEFAULT ''"),
    ("despesas", "vencimento", "TEXT"),
    ("despesas", "pago_em", "TEXT"),
    ("despesas", "nome_arquivo", "TEXT DEFAULT ''"),
    ("despesas", "criado_por", "INTEGER"),
]


def _colunas(cur, tabela: str) -> set[str]:
    return {r[1] for r in cur.execute(f"PRAGMA table_info({tabela})").fetchall()}


def _segunda_feira(dt: datetime) -> str:
    return (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")


def migrar() -> None:
    """Cria/atualiza o schema e garante o usuário gestor inicial."""
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

    _backfill(conn, cur)
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

    # 2) Semanas sem data de início: estima a segunda-feira da semana em que
    #    a semana foi criada. É uma aproximação, mas destrava relatórios,
    #    alertas de dia sem RDO e o fluxo de caixa por vencimento.
    faltando = cur.execute(
        "SELECT id, criado_em FROM semanas WHERE data_inicio IS NULL OR data_inicio = ''"
    ).fetchall()
    for row in faltando:
        try:
            base = datetime.fromisoformat(row["criado_em"])
        except (TypeError, ValueError):
            base = datetime.now()
        cur.execute("UPDATE semanas SET data_inicio = ? WHERE id = ?", (_segunda_feira(base), row["id"]))

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
    except sqlite3.Error:
        pass  # auditoria nunca pode derrubar a operação principal
