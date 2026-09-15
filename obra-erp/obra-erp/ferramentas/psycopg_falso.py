# -*- coding: utf-8 -*-
"""Driver de teste: fala a API do psycopg 3, mas por baixo é SQLite.

Serve para exercitar o caminho "Postgres" do app sem um servidor Postgres:
valida a tradução de dialeto (`?` -> `%s`, RETURNING id, ON CONFLICT,
LOWER()), o pool de conexões, o wrapper de cursor e as linhas em formato de
dicionário.

NÃO substitui um teste contra o Postgres real — tipos e travas de
concorrência são diferentes.
"""

import re
import sqlite3
import sys
import types

CAMINHO = {"db": ":memory:"}


def _pg_para_sqlite(sql: str) -> str:
    s = sql
    s = re.sub(r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", s, flags=re.I)
    s = re.sub(r"\bBIGSERIAL\b", "INTEGER", s, flags=re.I)
    s = re.sub(r"\bBIGINT\b", "INTEGER", s, flags=re.I)
    s = re.sub(r"\bDOUBLE PRECISION\b", "REAL", s, flags=re.I)
    s = s.replace("%s", "?")
    return s


class _Cursor:
    def __init__(self, conexao):
        self._conn = conexao
        self._cur = conexao.cursor()
        self.rowcount = -1
        self._retorno = None

    def execute(self, sql, args=()):
        alto = sql.upper()
        if "ENABLE ROW LEVEL SECURITY" in alto:
            self.rowcount = 0
            return self

        # information_schema.columns -> PRAGMA table_info
        if "INFORMATION_SCHEMA.COLUMNS" in alto:
            tabela = args[0]
            linhas = self._conn.execute(f"PRAGMA table_info({tabela})").fetchall()
            self._retorno = [{"column_name": linha[1]} for linha in linhas]
            self.rowcount = len(self._retorno)
            return self

        # ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS: emulado via PRAGMA
        alteracao = re.match(
            r"ALTER TABLE (?:IF EXISTS )?(\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)\s+(.*)$",
            sql.strip(), re.I | re.S)
        if alteracao:
            tabela, coluna, definicao = alteracao.groups()
            existe = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
            if existe:
                colunas = {r[1] for r in self._conn.execute(f"PRAGMA table_info({tabela})").fetchall()}
                if coluna not in colunas:
                    self._conn.execute(
                        f"ALTER TABLE {tabela} ADD COLUMN {coluna} {_pg_para_sqlite(definicao)}")
            self.rowcount = 0
            return self

        if "SETVAL(" in alto:
            self._retorno = [{"setval": 0}]
            self.rowcount = 1
            return self

        convertido = _pg_para_sqlite(sql)
        if convertido.count(";") > 1:
            self._cur.executescript(convertido)
            self.rowcount = 0
            return self

        retornando = re.search(r"\bRETURNING\s+id\s*$", convertido, re.I)
        if retornando:
            convertido = convertido[:retornando.start()].rstrip()

        self._cur.execute(convertido, tuple(args))
        self.rowcount = self._cur.rowcount
        if retornando:
            self._retorno = [{"id": self._cur.lastrowid}] if self._cur.rowcount else []
        return self

    def fetchone(self):
        if self._retorno is not None:
            return self._retorno.pop(0) if self._retorno else None
        linha = self._cur.fetchone()
        return dict(linha) if linha is not None else None

    def fetchall(self):
        if self._retorno is not None:
            saida, self._retorno = self._retorno, []
            return saida
        return [dict(r) for r in self._cur.fetchall()]

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _Conexao:
    def __init__(self, caminho):
        self._conn = sqlite3.connect(caminho, timeout=15, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self.fechada = False

    def cursor(self):
        if self.fechada:
            raise RuntimeError("connection is closed")
        return _Cursor(self._conn)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self.fechada = True
        self._conn.close()


def connect(_url, row_factory=None, autocommit=False, **_kw):
    return _Conexao(CAMINHO["db"])


def instalar(caminho_sqlite: str):
    """Registra este módulo como `psycopg` antes de o app importar o driver."""
    CAMINHO["db"] = caminho_sqlite
    modulo = types.ModuleType("psycopg")
    modulo.connect = connect
    linhas = types.ModuleType("psycopg.rows")
    linhas.dict_row = "dict_row"
    modulo.rows = linhas
    sys.modules["psycopg"] = modulo
    sys.modules["psycopg.rows"] = linhas
