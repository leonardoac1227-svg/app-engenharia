# -*- coding: utf-8 -*-
"""Ocorrências do ponto: quem faltou, em que dia, e por quê.

O ponto é só de presença (não há mais valor/hora), mas o relatório de
pagamento precisa dizer quem não cumpriu a jornada. A regra, de segunda a
sexta, para cada dia que **teve ponto lançado** na obra:

- horas >= jornada (8 h)      → nada a dizer;
- metade da jornada (4 h)     → falta de meio turno;
- entre zero e a jornada      → horas a menos (atraso, saída antecipada);
- zero ou sem registro        → falta.

Dia útil já passado em que **ninguém** teve ponto lançado não vira falta de
todo mundo: é ponto esquecido, e aparece uma vez só, em `dias_sem_ponto`.
Sábado e domingo nunca geram falta — trabalho nesses dias é extra.

Funcionário só é cobrado a partir do dia em que foi cadastrado, e inativo sem
registro no dia não é cobrado (já saiu da obra).
"""

from __future__ import annotations

from .. import config
from ..db import todos
from ..utils import hoje_iso

ABREVIACAO = {"Segunda": "Seg", "Terça": "Ter", "Quarta": "Qua", "Quinta": "Qui",
              "Sexta": "Sex", "Sábado": "Sáb", "Domingo": "Dom"}


def _horas(valor: float) -> str:
    return f"{valor:g}".replace(".", ",") + " h"


def _data_curta(iso: str) -> str:
    return f"{iso[8:10]}/{iso[5:7]}" if iso and len(iso) >= 10 else ""


def classificar(horas: float, jornada: float | None = None) -> str | None:
    """'falta', 'meio_turno', 'parcial' ou None (jornada cumprida)."""
    jornada = jornada or config.JORNADA_HORAS
    if horas >= jornada:
        return None
    if horas <= 0:
        return "falta"
    if abs(horas - jornada / 2) < 0.01:
        return "meio_turno"
    return "parcial"


def descrever(ocorrencia: dict, jornada: float | None = None) -> str:
    """Texto curto: 'Qua 15/10: falta de meio turno (4 h) · atestado médico'."""
    jornada = jornada or config.JORNADA_HORAS
    tipo, horas = ocorrencia["tipo"], ocorrencia["horas"]
    if tipo == "falta":
        motivo = "falta"
    elif tipo == "meio_turno":
        motivo = f"falta de meio turno ({_horas(horas)})"
    else:
        motivo = f"{_horas(horas)} de {_horas(jornada)} (faltaram {_horas(round(jornada - horas, 2))})"
    dia = f"{ABREVIACAO.get(ocorrencia['dia_semana'], '')} {_data_curta(ocorrencia['data'])}".strip()
    justificativa = ocorrencia.get("justificativa") or "sem justificativa"
    return f"{dia}: {motivo} · {justificativa}"


def ocorrencias_da_semana(obra_id: int, dias: list[dict]) -> dict:
    """Ponto da equipe na semana, pronto para tela e relatórios.

    `dias` é a lista de `listar_dias()` (precisa de id, dia_semana e data).
    """
    jornada = config.JORNADA_HORAS
    hoje = hoje_iso()
    ids = [d["id"] for d in dias]
    vazio = {"jornada": jornada, "equipe": [], "dias_sem_ponto": [], "total_ocorrencias": 0}
    if not ids:
        return vazio

    marcas = ",".join("?" * len(ids))
    registros: dict[tuple[int, int], dict] = {}
    for linha in todos(
            f"""SELECT dia_id, funcionario_id, horas_trabalhadas, justificativa
                  FROM horas WHERE dia_id IN ({marcas})""", ids):
        registros[(linha["dia_id"], linha["funcionario_id"])] = {
            "horas": float(linha["horas_trabalhadas"] or 0),
            "justificativa": (linha["justificativa"] or "").strip(),
        }

    com_registro = {funcionario for (_, funcionario) in registros}
    funcionarios = [dict(f) for f in todos(
        """SELECT id, nome, funcao, ativo, criado_em FROM funcionarios
            WHERE obra_id = ? ORDER BY nome""", (obra_id,))
        if f["ativo"] or f["id"] in com_registro]

    dias_com_ponto = {dia_id for (dia_id, _) in registros}
    avaliados = [d for d in dias
                 if d["dia_semana"] in config.DIAS_UTEIS and d.get("data") and d["data"] <= hoje]

    equipe = []
    for f in funcionarios:
        desde = str(f.get("criado_em") or "")[:10]
        horas_semana, dias_trabalhados, ocorrencias = 0.0, 0, []
        for d in dias:
            registro = registros.get((d["id"], f["id"]))
            if registro and registro["horas"] > 0:
                horas_semana += registro["horas"]
                dias_trabalhados += 1

        for d in avaliados:
            if d["id"] not in dias_com_ponto:
                continue
            registro = registros.get((d["id"], f["id"]))
            if not registro and (not f["ativo"] or (desde and desde > d["data"])):
                continue
            horas = registro["horas"] if registro else 0.0
            tipo = classificar(horas, jornada)
            if not tipo:
                continue
            ocorrencia = {
                "dia_id": d["id"], "dia_semana": d["dia_semana"], "data": d["data"],
                "horas": horas, "tipo": tipo,
                "justificativa": registro["justificativa"] if registro else "",
            }
            ocorrencia["texto"] = descrever(ocorrencia, jornada)
            ocorrencias.append(ocorrencia)

        if horas_semana <= 0 and not ocorrencias:
            continue
        equipe.append({
            "id": f["id"], "nome": f["nome"], "funcao": f["funcao"] or "",
            "horas": round(horas_semana, 2), "dias_trabalhados": dias_trabalhados,
            "ocorrencias": ocorrencias,
            "sem_justificativa": sum(1 for o in ocorrencias if not o["justificativa"]),
        })

    return {
        "jornada": jornada,
        "equipe": equipe,
        "dias_sem_ponto": [
            {"dia_semana": d["dia_semana"], "data": d["data"]}
            for d in avaliados if d["id"] not in dias_com_ponto and d["data"] < hoje],
        "total_ocorrencias": sum(len(p["ocorrencias"]) for p in equipe),
    }
