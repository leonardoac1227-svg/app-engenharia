# -*- coding: utf-8 -*-
"""Dialeto SQL — deixa o mesmo código falar com SQLite e com Postgres.

O app inteiro escreve SQL no dialeto do SQLite (placeholders `?`,
`INSERT OR IGNORE`, `COLLATE NOCASE`, `cursor.lastrowid`). Em vez de reescrever
40 consultas espalhadas pelos blueprints, traduzimos na saída — um único ponto
para auditar, e o modo SQLite continua rodando sem nenhuma penalidade.

As diferenças tratadas são exatamente as que o app usa:

| SQLite                        | Postgres                                  |
|-------------------------------|-------------------------------------------|
| `?`                           | `%s`                                      |
| `INSERT OR IGNORE INTO`       | `INSERT INTO ... ON CONFLICT DO NOTHING`  |
| `x = ? COLLATE NOCASE`        | `LOWER(x) = LOWER(%s)`                    |
| `ORDER BY x COLLATE NOCASE`   | `ORDER BY LOWER(x)`                       |
| `cursor.lastrowid`            | `... RETURNING id` + fetch                |
"""

from __future__ import annotations

import re
from functools import lru_cache

# Tabelas sem coluna `id` — nelas não se acrescenta RETURNING id.
TABELAS_SEM_ID = {"usuario_obras"}

_RE_INSERT_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
_RE_COLLATE_IGUAL = re.compile(r"([\w.]+)\s*=\s*\?\s*COLLATE\s+NOCASE", re.I)
_RE_COLLATE_ORDEM = re.compile(r"([\w.]+)\s+COLLATE\s+NOCASE", re.I)
_RE_TABELA_INSERT = re.compile(r"\bINSERT\s+INTO\s+([\w.\"]+)", re.I)


@lru_cache(maxsize=512)
def para_postgres(sql: str) -> str:
    """Converte uma instrução escrita no dialeto SQLite para Postgres."""
    texto = sql.strip()

    # 1) INSERT OR IGNORE -> ON CONFLICT DO NOTHING
    tem_ignore = bool(_RE_INSERT_IGNORE.search(texto))
    if tem_ignore:
        texto = _RE_INSERT_IGNORE.sub("INSERT INTO", texto)

    # 2) comparação insensível a maiúsculas (e-mail) e ordenação
    texto = _RE_COLLATE_IGUAL.sub(r"LOWER(\1) = LOWER(?)", texto)
    texto = _RE_COLLATE_ORDEM.sub(r"LOWER(\1)", texto)

    eh_insert = texto.lstrip().upper().startswith("INSERT")

    if tem_ignore and "ON CONFLICT" not in texto.upper():
        texto = texto.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # 3) RETURNING id — é o que permite manter `cursor.lastrowid` no app
    if eh_insert and "RETURNING" not in texto.upper():
        alvo = _RE_TABELA_INSERT.search(texto)
        tabela = (alvo.group(1) if alvo else "").strip('"').split(".")[-1].lower()
        if tabela not in TABELAS_SEM_ID:
            texto = texto.rstrip().rstrip(";") + " RETURNING id"

    # 4) placeholders (por último, para não confundir as regras acima)
    texto = texto.replace("?", "%s")
    return texto


# ---------------------------------------------------------------- schema PG

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS usuarios (
    id BIGSERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    email TEXT NOT NULL,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL DEFAULT 'gestor',
    ativo INTEGER NOT NULL DEFAULT 1,
    senha_provisoria INTEGER NOT NULL DEFAULT 0,
    ultimo_acesso TEXT,
    criado_em TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios (LOWER(email));

CREATE TABLE IF NOT EXISTS obras (
    id BIGSERIAL PRIMARY KEY,
    nome TEXT NOT NULL,
    cliente TEXT DEFAULT '',
    endereco TEXT DEFAULT '',
    orcamento_total DOUBLE PRECISION NOT NULL DEFAULT 0,
    data_inicio TEXT,
    previsao_fim TEXT,
    status TEXT NOT NULL DEFAULT 'ativa',
    arquivada INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS usuario_obras (
    usuario_id BIGINT NOT NULL REFERENCES usuarios (id) ON DELETE CASCADE,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    PRIMARY KEY (usuario_id, obra_id)
);

CREATE TABLE IF NOT EXISTS etapas (
    id BIGSERIAL PRIMARY KEY,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    ordem INTEGER NOT NULL DEFAULT 0,
    peso DOUBLE PRECISION NOT NULL DEFAULT 0,
    percentual DOUBLE PRECISION NOT NULL DEFAULT 0,
    custo_previsto DOUBLE PRECISION NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS semanas (
    id BIGSERIAL PRIMARY KEY,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    numero INTEGER NOT NULL,
    data_inicio TEXT,
    meta TEXT DEFAULT '',
    meta_atingida INTEGER NOT NULL DEFAULT 0,
    orcamento_previsto DOUBLE PRECISION NOT NULL DEFAULT 0,
    fechada INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS funcionarios (
    id BIGSERIAL PRIMARY KEY,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    funcao TEXT DEFAULT '',
    valor_hora DOUBLE PRECISION NOT NULL DEFAULT 0,
    documento TEXT DEFAULT '',
    telefone TEXT DEFAULT '',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT
);

CREATE TABLE IF NOT EXISTS dias (
    id BIGSERIAL PRIMARY KEY,
    semana_id BIGINT NOT NULL REFERENCES semanas (id) ON DELETE CASCADE,
    dia_semana TEXT NOT NULL,
    data TEXT,
    producao_dia TEXT DEFAULT '',
    clima TEXT DEFAULT '',
    observacoes TEXT DEFAULT '',
    assinado_por TEXT DEFAULT '',
    assinado_em TEXT,
    atualizado_em TEXT,
    UNIQUE (semana_id, dia_semana)
);

CREATE TABLE IF NOT EXISTS fotos (
    id BIGSERIAL PRIMARY KEY,
    dia_id BIGINT NOT NULL REFERENCES dias (id) ON DELETE CASCADE,
    arquivo TEXT NOT NULL,
    nome_original TEXT DEFAULT '',
    legenda TEXT DEFAULT '',
    ambiente TEXT DEFAULT '',
    largura INTEGER DEFAULT 0,
    altura INTEGER DEFAULT 0,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS horas (
    id BIGSERIAL PRIMARY KEY,
    dia_id BIGINT NOT NULL REFERENCES dias (id) ON DELETE CASCADE,
    funcionario_id BIGINT NOT NULL REFERENCES funcionarios (id) ON DELETE CASCADE,
    horas_trabalhadas DOUBLE PRECISION NOT NULL DEFAULT 0,
    valor_hora DOUBLE PRECISION NOT NULL DEFAULT 0,
    UNIQUE (dia_id, funcionario_id)
);

CREATE TABLE IF NOT EXISTS medicoes (
    id BIGSERIAL PRIMARY KEY,
    dia_id BIGINT NOT NULL REFERENCES dias (id) ON DELETE CASCADE,
    etapa_id BIGINT REFERENCES etapas (id) ON DELETE SET NULL,
    descricao TEXT NOT NULL,
    quantidade DOUBLE PRECISION NOT NULL DEFAULT 0,
    unidade TEXT DEFAULT 'm²',
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS despesas (
    id BIGSERIAL PRIMARY KEY,
    dia_id BIGINT NOT NULL REFERENCES dias (id) ON DELETE CASCADE,
    etapa_id BIGINT REFERENCES etapas (id) ON DELETE SET NULL,
    categoria TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    fornecedor TEXT DEFAULT '',
    valor DOUBLE PRECISION NOT NULL DEFAULT 0,
    status_pagamento TEXT NOT NULL,
    vencimento TEXT,
    pago_em TEXT,
    imagem_path TEXT DEFAULT '',
    nome_arquivo TEXT DEFAULT '',
    criado_por BIGINT,
    criado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auditoria (
    id BIGSERIAL PRIMARY KEY,
    usuario_id BIGINT,
    usuario_nome TEXT DEFAULT '',
    acao TEXT NOT NULL,
    entidade TEXT DEFAULT '',
    entidade_id BIGINT,
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

# Todo o acesso passa pelo backend com a service key, nunca pelo navegador.
# Ligar RLS sem criar policies é o que garante que a chave pública (anon) do
# projeto não consiga ler nada — a service key ignora RLS por definição.
RLS_POSTGRES = """
ALTER TABLE usuarios      ENABLE ROW LEVEL SECURITY;
ALTER TABLE obras         ENABLE ROW LEVEL SECURITY;
ALTER TABLE usuario_obras ENABLE ROW LEVEL SECURITY;
ALTER TABLE etapas        ENABLE ROW LEVEL SECURITY;
ALTER TABLE semanas       ENABLE ROW LEVEL SECURITY;
ALTER TABLE funcionarios  ENABLE ROW LEVEL SECURITY;
ALTER TABLE dias          ENABLE ROW LEVEL SECURITY;
ALTER TABLE fotos         ENABLE ROW LEVEL SECURITY;
ALTER TABLE horas         ENABLE ROW LEVEL SECURITY;
ALTER TABLE medicoes      ENABLE ROW LEVEL SECURITY;
ALTER TABLE despesas      ENABLE ROW LEVEL SECURITY;
ALTER TABLE auditoria     ENABLE ROW LEVEL SECURITY;
"""

# As sequências precisam ser reposicionadas depois de uma importação com ids
# explícitos, senão o primeiro INSERT novo colide com um id já existente.
SEQUENCIAS = """
SELECT setval(pg_get_serial_sequence(t, 'id'),
              COALESCE((SELECT MAX(id) FROM usuarios), 1))
  FROM (VALUES ('usuarios')) AS x(t);
"""

TABELAS_COM_ID = [
    "usuarios", "obras", "etapas", "semanas", "funcionarios", "dias",
    "fotos", "horas", "medicoes", "despesas", "auditoria",
]
