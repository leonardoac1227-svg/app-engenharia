# -*- coding: utf-8 -*-
"""Verificador de portabilidade do SQL (SQLite -> Postgres).

Existe porque testar o modo Postgres com um driver simulado sobre SQLite tem
um ponto cego grande: construções que o SQLite aceita e o Postgres recusa
passam batidas no teste e quebram só em produção. Foi assim que um
`HAVING horas > 0` (alias de agregação) chegou ao deploy.

Este script lê todas as strings SQL do pacote e sinaliza:

1. **Alias do SELECT usado em HAVING ou WHERE** — o Postgres só reconhece
   alias em `GROUP BY` e `ORDER BY`. Em `HAVING`/`WHERE` é preciso repetir a
   expressão.
2. **Coluna não agregada fora do GROUP BY** — o SQLite escolhe um valor
   qualquer; o Postgres recusa a consulta. (Colunas do mesmo alias cuja
   chave `id` está no GROUP BY são aceitas, como faz o Postgres.)
3. **`INSERT OR REPLACE`** e outras formas sem tradução na camada de dialeto.
4. **INSERT em tabela sem coluna `id`** que não está em `TABELAS_SEM_ID` — a
   camada de dialeto acrescentaria `RETURNING id` e o Postgres recusaria o
   INSERT. Foi assim que o código de conexão do Telegram quebrou no Supabase.

Uso:  python ferramentas/verificar_sql.py
Saída: código 1 se encontrar problema, 0 se estiver tudo portável.
"""

from __future__ import annotations

import ast
import os
import re
import sys

RAIZ = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "obra")
sys.path.insert(0, os.path.dirname(RAIZ))

from obra.sql import SCHEMA_POSTGRES, TABELAS_SEM_ID  # noqa: E402


def _tabelas_sem_id() -> set[str]:
    sem_id = set()
    for nome, corpo in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA_POSTGRES, re.S):
        if not re.search(r"^\s*id\s", corpo, re.M):
            sem_id.add(nome.lower())
    return sem_id


SEM_ID = _tabelas_sem_id()

FUNCOES_AGREGACAO = ("SUM(", "COUNT(", "AVG(", "MIN(", "MAX(", "GROUP_CONCAT(", "STRING_AGG(")

# Palavras que aparecem como "alias" mas são a própria sintaxe.
RESERVADAS = {
    "AND", "OR", "NOT", "NULL", "IS", "IN", "LIKE", "BETWEEN", "AS", "ON",
    "DESC", "ASC", "CASE", "WHEN", "THEN", "ELSE", "END", "SELECT", "FROM",
}


def _coletar_sql(caminho: str) -> list[tuple[int, str]]:
    """Extrai literais de texto que se parecem com SQL, com o número da linha."""
    with open(caminho, encoding="utf-8") as arquivo:
        arvore = ast.parse(arquivo.read(), filename=caminho)

    encontrados: list[tuple[int, str]] = []
    # partes de f-string aparecem também como Constant no walk; ignoramos as
    # que já foram consumidas pelo JoinedStr para não duplicar o relatório.
    dentro_de_fstring = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.JoinedStr):
            for parte in no.values:
                dentro_de_fstring.add(id(parte))

    for no in ast.walk(arvore):
        if id(no) in dentro_de_fstring:
            continue
        textos = []
        if isinstance(no, ast.Constant) and isinstance(no.value, str):
            textos.append(no.value)
        elif isinstance(no, ast.JoinedStr):  # f-strings
            partes = [p.value for p in no.values
                      if isinstance(p, ast.Constant) and isinstance(p.value, str)]
            textos.append(" ? ".join(partes))

        for texto in textos:
            if re.search(r"\bSELECT\b.+\bFROM\b|^\s*INSERT\s+(OR\s+IGNORE\s+)?INTO\b", texto, re.I | re.S):
                encontrados.append((no.lineno, " ".join(texto.split())))
    return encontrados


def _aliases_do_select(sql: str) -> list[str]:
    trecho = re.search(r"\bSELECT\b(.+?)\bFROM\b", sql, re.I | re.S)
    if not trecho:
        return []
    return [m.group(1) for m in re.finditer(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", trecho.group(1), re.I)]


def _colunas_selecionadas(sql: str) -> list[str]:
    """Colunas simples (sem agregação) na lista do SELECT."""
    trecho = re.search(r"\bSELECT\b(.+?)\bFROM\b", sql, re.I | re.S)
    if not trecho:
        return []

    colunas, profundidade, atual = [], 0, ""
    for caractere in trecho.group(1):
        if caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        if caractere == "," and profundidade == 0:
            colunas.append(atual)
            atual = ""
        else:
            atual += caractere
    colunas.append(atual)

    simples = []
    for coluna in colunas:
        limpa = coluna.strip()
        if not limpa or limpa == "*":
            continue
        if any(f in limpa.upper() for f in FUNCOES_AGREGACAO):
            continue
        expressao = re.split(r"\s+AS\s+", limpa, flags=re.I)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?", expressao):
            simples.append(expressao)
    return simples


def verificar(sql: str) -> list[str]:
    problemas = []
    alto = sql.upper()

    # 1) alias em HAVING / WHERE
    #
    # O trecho precisa parar em UNION também: sem isso, a cláusula WHERE do
    # primeiro SELECT "engolia" o segundo, e qualquer alias declarado lá
    # aparecia como se estivesse sendo usado no WHERE.
    for clausula in ("HAVING", "WHERE"):
        trecho = re.search(
            rf"\b{clausula}\b(.+?)(\bGROUP BY\b|\bORDER BY\b|\bLIMIT\b|\bUNION\b|$)",
            sql, re.I | re.S)
        if not trecho:
            continue
        corpo = trecho.group(1)
        for alias in _aliases_do_select(sql):
            if alias.upper() in RESERVADAS:
                continue
            # `d.semana_id` é a coluna real, não o alias: o lookbehind evita
            # acusar consultas em que o alias repete o nome da coluna.
            if re.search(rf"(?<![\w.]){re.escape(alias)}\b", corpo):
                problemas.append(
                    f"alias '{alias}' usado em {clausula} — o Postgres exige repetir a expressão")

    # 2) coluna não agregada fora do GROUP BY
    if "GROUP BY" in alto:
        trecho = re.search(r"\bGROUP BY\b(.+?)(\bHAVING\b|\bORDER BY\b|\bLIMIT\b|$)",
                           sql, re.I | re.S)
        agrupadas = {c.strip().lower() for c in (trecho.group(1) if trecho else "").split(",")}
        # o Postgres aceita colunas da tabela cuja chave primária está no GROUP BY
        prefixos_com_id = {c.split(".")[0] for c in agrupadas if c.endswith(".id")}

        for coluna in _colunas_selecionadas(sql):
            if coluna.lower() in agrupadas:
                continue
            if "." in coluna and coluna.split(".")[0] in prefixos_com_id:
                continue
            problemas.append(
                f"coluna '{coluna}' não está no GROUP BY nem é agregada — o Postgres recusa")

    # 3) formas sem tradução
    if "INSERT OR REPLACE" in alto:
        problemas.append("INSERT OR REPLACE não tem tradução — use ON CONFLICT DO UPDATE")

    # 4) INSERT em tabela sem id: o dialeto acrescentaria RETURNING id
    alvo = re.search(r"\bINSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)", sql, re.I)
    if alvo and alvo.group(1).lower() in SEM_ID and alvo.group(1).lower() not in TABELAS_SEM_ID:
        problemas.append(f"INSERT em '{alvo.group(1)}', que não tem coluna id — inclua a tabela em "
                         "TABELAS_SEM_ID (obra/sql.py), senão o Postgres recusa o RETURNING id")

    return problemas


def main() -> None:
    total_consultas = falhas = 0

    for pasta, _, arquivos in os.walk(RAIZ):
        if "__pycache__" in pasta:
            continue
        for nome in sorted(arquivos):
            if not nome.endswith(".py"):
                continue
            caminho = os.path.join(pasta, nome)
            for linha, sql in _coletar_sql(caminho):
                total_consultas += 1
                for problema in verificar(sql):
                    falhas += 1
                    relativo = os.path.relpath(caminho, os.path.dirname(RAIZ))
                    print(f" PROBLEMA {relativo}:{linha}\n          {problema}\n          {sql[:110]}...")

    print(f"\n{total_consultas} consulta(s) analisada(s) · "
          + ("nenhum problema de portabilidade" if not falhas else f"{falhas} problema(s)"))
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main()
