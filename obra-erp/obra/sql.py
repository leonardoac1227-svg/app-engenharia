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
    sigla TEXT DEFAULT '',
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
    tipo_contrato TEXT NOT NULL DEFAULT 'fixo',
    chave_pix TEXT DEFAULT '',
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
    numero_nota TEXT DEFAULT '',
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

CREATE TABLE IF NOT EXISTS metas (
    id BIGSERIAL PRIMARY KEY,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    semana_id BIGINT NOT NULL REFERENCES semanas (id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    descricao TEXT DEFAULT '',
    percentual DOUBLE PRECISION NOT NULL DEFAULT 0,
    percentual_inicial DOUBLE PRECISION NOT NULL DEFAULT 0,
    etapa_id BIGINT REFERENCES etapas (id) ON DELETE SET NULL,
    origem_meta_id BIGINT,
    semanas_arrastada INTEGER NOT NULL DEFAULT 0,
    concluida INTEGER NOT NULL DEFAULT 0,
    concluida_em TEXT,
    transferida INTEGER NOT NULL DEFAULT 0,
    ordem INTEGER NOT NULL DEFAULT 0,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS meta_progresso (
    id BIGSERIAL PRIMARY KEY,
    meta_id BIGINT NOT NULL REFERENCES metas (id) ON DELETE CASCADE,
    dia_id BIGINT NOT NULL REFERENCES dias (id) ON DELETE CASCADE,
    percentual DOUBLE PRECISION NOT NULL DEFAULT 0,
    observacao TEXT DEFAULT '',
    usuario_nome TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    UNIQUE (meta_id, dia_id)
);

CREATE INDEX IF NOT EXISTS idx_metas_semana ON metas (semana_id);
CREATE INDEX IF NOT EXISTS idx_progresso_meta ON meta_progresso (meta_id);
CREATE INDEX IF NOT EXISTS idx_progresso_dia ON meta_progresso (dia_id);

CREATE TABLE IF NOT EXISTS contratos (
    id BIGSERIAL PRIMARY KEY,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    funcionario_id BIGINT REFERENCES funcionarios (id) ON DELETE SET NULL,
    empresa TEXT NOT NULL,
    servico TEXT NOT NULL,
    etapa_id BIGINT REFERENCES etapas (id) ON DELETE SET NULL,
    valor_total DOUBLE PRECISION NOT NULL DEFAULT 0,
    responsavel TEXT DEFAULT '',
    telefone TEXT DEFAULT '',
    observacoes TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ativo',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT
);

CREATE TABLE IF NOT EXISTS contrato_medicoes (
    id BIGSERIAL PRIMARY KEY,
    contrato_id BIGINT NOT NULL REFERENCES contratos (id) ON DELETE CASCADE,
    semana_id BIGINT NOT NULL REFERENCES semanas (id) ON DELETE CASCADE,
    percentual DOUBLE PRECISION NOT NULL DEFAULT 0,
    valor DOUBLE PRECISION NOT NULL DEFAULT 0,
    entra_no_caixa INTEGER NOT NULL DEFAULT 1,
    despesa_id BIGINT REFERENCES despesas (id) ON DELETE SET NULL,
    observacao TEXT DEFAULT '',
    criado_por TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    UNIQUE (contrato_id, semana_id)
);

CREATE INDEX IF NOT EXISTS idx_contratos_obra ON contratos (obra_id);
CREATE INDEX IF NOT EXISTS idx_cmedicoes_contrato ON contrato_medicoes (contrato_id);
CREATE INDEX IF NOT EXISTS idx_cmedicoes_semana ON contrato_medicoes (semana_id);

CREATE TABLE IF NOT EXISTS locacoes (
    id BIGSERIAL PRIMARY KEY,
    obra_id BIGINT NOT NULL REFERENCES obras (id) ON DELETE CASCADE,
    equipamento TEXT NOT NULL,
    fornecedor TEXT DEFAULT '',
    etapa_id BIGINT REFERENCES etapas (id) ON DELETE SET NULL,
    valor_parcela DOUBLE PRECISION NOT NULL DEFAULT 0,
    frequencia TEXT NOT NULL DEFAULT 'mensal',
    data_inicio TEXT NOT NULL,
    ciclos INTEGER NOT NULL DEFAULT 1,
    data_fim TEXT,
    status TEXT NOT NULL DEFAULT 'ativa',
    observacoes TEXT DEFAULT '',
    criado_em TEXT NOT NULL,
    encerrado_em TEXT
);

CREATE TABLE IF NOT EXISTS locacao_parcelas (
    id BIGSERIAL PRIMARY KEY,
    locacao_id BIGINT NOT NULL REFERENCES locacoes (id) ON DELETE CASCADE,
    numero INTEGER NOT NULL,
    vencimento TEXT NOT NULL,
    valor DOUBLE PRECISION NOT NULL DEFAULT 0,
    despesa_id BIGINT REFERENCES despesas (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'prevista',
    criado_em TEXT NOT NULL,
    UNIQUE (locacao_id, numero)
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
ALTER TABLE metas          ENABLE ROW LEVEL SECURITY;
ALTER TABLE meta_progresso ENABLE ROW LEVEL SECURITY;
ALTER TABLE contratos         ENABLE ROW LEVEL SECURITY;
ALTER TABLE contrato_medicoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE locacoes          ENABLE ROW LEVEL SECURITY;
ALTER TABLE locacao_parcelas  ENABLE ROW LEVEL SECURITY;
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
    "fotos", "horas", "medicoes", "despesas", "auditoria", "metas", "meta_progresso",
    "contratos", "contrato_medicoes", "locacoes", "locacao_parcelas",
]


def tipo_para_postgres(definicao: str) -> str:
    """Converte a definição de coluna do dialeto SQLite para Postgres.

    As colunas novas são declaradas uma única vez (em `COLUNAS_EXTRAS`, no
    dialeto SQLite) e traduzidas aqui. Manter duas listas paralelas era o
    caminho para uma delas ficar para trás — foi exatamente o que aconteceu
    quando o Postgres passou meses sem receber as colunas novas.
    """
    texto_convertido = definicao
    texto_convertido = re.sub(r"\bREAL\b", "DOUBLE PRECISION", texto_convertido, flags=re.I)
    texto_convertido = re.sub(r"\bINTEGER\b", "BIGINT", texto_convertido, flags=re.I)

    # NOT NULL sem DEFAULT é impossível de adicionar a uma tabela que já tem
    # linhas — o Postgres não sabe o que gravar nelas. Numa migração de banco
    # existente, a coluna entra permitindo nulo; em banco novo, o CREATE TABLE
    # já aplicou a restrição correta.
    if "NOT NULL" in texto_convertido.upper() and "DEFAULT" not in texto_convertido.upper():
        texto_convertido = re.sub(r"\s*NOT NULL\s*", " ", texto_convertido, flags=re.I).strip()

    return texto_convertido


_RE_CREATE = re.compile(
    r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\((.*?)\n\);", re.I | re.S)

# Linhas que não são coluna e sim restrição de tabela.
_RESTRICOES = ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CHECK", "CONSTRAINT")


def colunas_do_schema(schema: str = SCHEMA_POSTGRES) -> list[tuple[str, str, str]]:
    """Extrai (tabela, coluna, definição) do próprio DDL do Postgres.

    Ler o schema em vez de manter uma segunda lista à mão elimina a classe de
    erro que derrubou a produção: uma coluna nova entrava no `CREATE TABLE` e
    ninguém lembrava de acrescentá-la à lista de migração, então bancos
    existentes ficavam sem ela.
    """
    encontradas: list[tuple[str, str, str]] = []

    for tabela, corpo in _RE_CREATE.findall(schema):
        profundidade = 0
        atual = ""
        partes = []
        for caractere in corpo:
            if caractere == "(":
                profundidade += 1
            elif caractere == ")":
                profundidade -= 1
            if caractere == "," and profundidade == 0:
                partes.append(atual)
                atual = ""
            else:
                atual += caractere
        partes.append(atual)

        for parte in partes:
            linha = " ".join(parte.split())
            if not linha or linha.upper().startswith(_RESTRICOES):
                continue
            nome, _, definicao = linha.partition(" ")
            if not definicao or "SERIAL" in definicao.upper():
                continue  # chave primária já existe em qualquer banco
            # A referência é removida de propósito: adicionar FK sobre linhas
            # antigas pode falhar, e o app não depende dela para funcionar.
            definicao = re.sub(r"\s+REFERENCES\s+.*$", "", definicao, flags=re.I).strip()
            encontradas.append((tabela, nome, definicao))

    return encontradas


def alteracoes_postgres(colunas=None) -> list[str]:
    """ALTER TABLE que faltam num banco Postgres já existente.

    `ADD COLUMN IF NOT EXISTS` deixa a operação idempotente: rodar a cada boot
    é seguro e barato, e um banco criado do zero não tem o que alterar.
    """
    alvo = colunas if colunas is not None else colunas_do_schema()
    return [
        f"ALTER TABLE IF EXISTS {tabela} "
        f"ADD COLUMN IF NOT EXISTS {coluna} {tipo_para_postgres(definicao)}"
        for tabela, coluna, definicao in alvo
    ]
