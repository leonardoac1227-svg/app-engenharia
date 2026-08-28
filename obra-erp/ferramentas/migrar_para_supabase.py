# -*- coding: utf-8 -*-
"""Copia um banco SQLite do Obra ERP para o Postgres do Supabase.

Uso:

    export DATABASE_URL='postgresql://postgres:SENHA@db.SEU-PROJETO.supabase.co:5432/postgres'
    export SUPABASE_URL='https://SEU-PROJETO.supabase.co'      # opcional (anexos)
    export SUPABASE_SERVICE_KEY='service_role...'              # opcional (anexos)
    python ferramentas/migrar_para_supabase.py dados/obra_erp.db

O que ele faz, nesta ordem:

1. Abre o SQLite e roda a migração local — assim um banco da v1 é corrigido
   (datas retroagidas, snapshot de valor/hora) **antes** de subir.
2. Cria o schema no Postgres, se ainda não existir.
3. Copia as tabelas na ordem das dependências, preservando os ids.
4. Reposiciona as sequências (senão o primeiro INSERT novo colide com um id
   existente).
5. Opcionalmente, sobe os anexos da pasta de uploads para o bucket.

Rode quantas vezes quiser: a cópia é feita em transação única e o script
recusa tabelas de destino que já tenham dados, para nunca duplicar.

IMPORTANTE: use a porta 5432 (conexão direta) para a migração. O pooler na
porta 6543 é modo transação e não suporta bem cargas em lote.
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ordem importa: uma tabela só entra depois daquelas de quem ela depende.
ORDEM = [
    "usuarios", "obras", "usuario_obras", "etapas", "semanas", "funcionarios",
    "dias", "fotos", "horas", "medicoes", "despesas", "auditoria",
    "metas", "meta_progresso", "contratos", "contrato_medicoes",
    "locacoes", "locacao_parcelas",
]

TABELAS_COM_ID = [t for t in ORDEM if t != "usuario_obras"]


def _conectar_postgres(url: str):
    try:
        import psycopg
        return psycopg.connect(url, autocommit=False)
    except ImportError:
        pass
    try:
        import psycopg2
        return psycopg2.connect(url)
    except ImportError:
        raise SystemExit("Driver ausente. Rode: pip install 'psycopg[binary]'")


def _primeiro(linha):
    """Primeiro valor da linha, seja ela tupla (psycopg padrão) ou dicionário."""
    if linha is None:
        return None
    if isinstance(linha, dict):
        return next(iter(linha.values()))
    return linha[0]


def _colunas_sqlite(cur, tabela: str) -> list[str]:
    return [linha[1] for linha in cur.execute(f"PRAGMA table_info({tabela})").fetchall()]


def _colunas_postgres(cur, tabela: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (tabela,))
    return {_primeiro(linha) for linha in cur.fetchall()}


def migrar_dados(caminho_sqlite: str, url_postgres: str) -> None:
    from obra import config
    from obra.sql import SCHEMA_POSTGRES

    origem = sqlite3.connect(caminho_sqlite)
    origem.row_factory = sqlite3.Row
    cur_origem = origem.cursor()

    destino = _conectar_postgres(url_postgres)
    cur_destino = destino.cursor()

    print("→ criando schema no Postgres (se necessário)")
    cur_destino.execute(SCHEMA_POSTGRES)
    destino.commit()

    total_geral = 0
    for tabela in ORDEM:
        existe = cur_origem.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tabela,)).fetchone()
        if not existe:
            print(f"   {tabela}: não existe na origem, pulando")
            continue

        cur_destino.execute(f"SELECT COUNT(*) AS total FROM {tabela}")
        if _primeiro(cur_destino.fetchone()):
            print(f"   {tabela}: destino já tem dados — pulando para não duplicar")
            continue

        colunas_origem = _colunas_sqlite(cur_origem, tabela)
        colunas_destino = _colunas_postgres(cur_destino, tabela)
        colunas = [c for c in colunas_origem if c in colunas_destino]
        if not colunas:
            continue

        linhas = cur_origem.execute(
            f"SELECT {', '.join(colunas)} FROM {tabela}").fetchall()
        if not linhas:
            print(f"   {tabela}: 0 registros")
            continue

        marcadores = ", ".join(["%s"] * len(colunas))
        sql = f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({marcadores})"
        for linha in linhas:
            cur_destino.execute(sql, tuple(linha[c] for c in colunas))

        total_geral += len(linhas)
        print(f"   {tabela}: {len(linhas)} registro(s)")

    print("→ reposicionando sequências")
    for tabela in TABELAS_COM_ID:
        cur_destino.execute(
            f"""SELECT setval(pg_get_serial_sequence('{tabela}', 'id'),
                              COALESCE((SELECT MAX(id) FROM {tabela}), 1), true)"""
        )

    destino.commit()
    destino.close()
    origem.close()
    print(f"✓ {total_geral} registro(s) migrados para o Supabase")


def enviar_anexos(pasta: str) -> int:
    from obra import config, storage
    if not storage.usando_supabase():
        print("→ SUPABASE_URL/SERVICE_KEY ausentes: anexos continuam no disco")
        return 0
    if not os.path.isdir(pasta):
        print(f"→ pasta de anexos não encontrada: {pasta}")
        return 0

    enviados = 0
    for nome in sorted(os.listdir(pasta)):
        caminho = os.path.join(pasta, nome)
        if not os.path.isfile(caminho):
            continue
        with open(caminho, "rb") as arquivo:
            try:
                storage.enviar(nome, arquivo.read())
                enviados += 1
            except storage.ErroStorage as erro:
                print(f"   falha em {nome}: {erro}")
    print(f"✓ {enviados} anexo(s) enviados para o bucket '{config.SUPABASE_BUCKET}'")
    return enviados


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Uso: python ferramentas/migrar_para_supabase.py <caminho/obra_erp.db>")

    caminho_sqlite = sys.argv[1]
    if not os.path.exists(caminho_sqlite):
        raise SystemExit(f"Banco não encontrado: {caminho_sqlite}")

    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url.startswith(("postgres://", "postgresql://")):
        raise SystemExit("Defina DATABASE_URL com a string de conexão do Supabase.")

    # Corrige o banco de origem antes de subir (importante para bancos da v1).
    print("→ normalizando o banco de origem (migração local)")
    os.environ["DB_PATH"] = caminho_sqlite
    os.environ.pop("DATABASE_URL", None)          # força o modo SQLite aqui
    import importlib
    from obra import config as config_mod
    importlib.reload(config_mod)
    from obra import db as db_mod
    importlib.reload(db_mod)
    db_mod.migrar()

    os.environ["DATABASE_URL"] = url              # devolve para o destino
    importlib.reload(config_mod)

    migrar_dados(caminho_sqlite, url)

    pasta_anexos = os.environ.get("UPLOADS_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(caminho_sqlite)), "uploads")
    enviar_anexos(pasta_anexos)

    print("\nPronto. Suba o app com DATABASE_URL apontando para o Supabase.")


if __name__ == "__main__":
    main()
