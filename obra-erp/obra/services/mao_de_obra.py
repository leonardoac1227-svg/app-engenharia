# -*- coding: utf-8 -*-
"""Custo da mão de obra na semana, cobrado do cliente na planilha de pagamento.

Dois vínculos:

- **Fichado**: líquido + encargos por dia útil, pela tabela de `TABELA`.
  Meia diária (ou atraso) desconta só o líquido, na proporção das horas; os
  encargos do dia são cobrados inteiros. Falta o dia todo sem justificativa:
  só os encargos. Falta o dia todo com justificativa: diária cheia.
- **Avulso**: a diária combinada com a pessoa, na proporção das horas. Não tem
  encargos e não recebe falta.

Quem não tem valor definido aparece como "a definir" e fica fora do total: o
app não adivinha o que vai ser cobrado.
"""

from __future__ import annotations

from .. import config
from ..db import todos
from ..utils import hoje_iso

# Custos por dia útil (20 dias), tabela de março de 2026.
TABELA = {
    "pedreiro": {"nome": "Pedreiro / Carpinteiro", "liquido": 130.81, "encargos": 146.67},
    "servente_pratico": {"nome": "Servente prático", "liquido": 90.00, "encargos": 97.94},
    "servente_comum": {"nome": "Servente comum", "liquido": 86.89, "encargos": 94.00},
}
VINCULOS = ("fichado", "avulso")


def _chave(texto) -> str:
    from .acoes import sem_acento
    return sem_acento(str(texto or ""))


def classe_da_funcao(funcao) -> str:
    """Só quando a função não deixa dúvida: "servente" sozinho pode ser prático ou comum."""
    chave = _chave(funcao)
    if "pedreiro" in chave or "carpinteiro" in chave:
        return "pedreiro"
    if "servente" in chave or "ajudante" in chave:
        if "pratico" in chave:
            return "servente_pratico"
        if "comum" in chave:
            return "servente_comum"
    return ""


def condicoes(f: dict) -> dict:
    """{"vinculo", "liquido", "encargos", "definido"} de uma pessoa."""
    vinculo = f.get("vinculo") or ("avulso" if f.get("avulso") else "fichado")
    if vinculo == "avulso":
        diaria = float(f.get("diaria") or 0)
        return {"vinculo": "avulso", "liquido": diaria, "encargos": 0.0, "definido": diaria > 0}
    valores = TABELA.get(f.get("classe") or classe_da_funcao(f.get("funcao")))
    if not valores:
        return {"vinculo": "fichado", "liquido": 0.0, "encargos": 0.0, "definido": False}
    return {"vinculo": "fichado", "liquido": valores["liquido"], "encargos": valores["encargos"], "definido": True}


def em_outra_obra(funcionario_ids: list[int], datas: list[str], obra_id: int) -> set[tuple[int, str]]:
    """(funcionário, data) com ponto em outra obra: emprestado no dia não faltou na obra dele."""
    if not funcionario_ids or not datas:
        return set()
    return {(r["funcionario_id"], str(r["data"])[:10]) for r in todos(
        f"""SELECT h.funcionario_id, d.data FROM horas h JOIN dias d ON d.id = h.dia_id
              JOIN semanas s ON s.id = d.semana_id
             WHERE s.obra_id <> ? AND h.horas_trabalhadas > 0
               AND h.funcionario_id IN ({', '.join('?' for _ in funcionario_ids)})
               AND d.data IN ({', '.join('?' for _ in datas)})""", [obra_id, *funcionario_ids, *datas])}


def calcular(obra_id: int, dias: list[dict]) -> dict:
    """{"pessoas": [...], "total", "a_definir": [nomes]}. `dias`: lista de `listar_dias()`."""
    ids = [d["id"] for d in dias]
    if not ids:
        return {"pessoas": [], "total": 0.0, "a_definir": []}
    registros = {(r["dia_id"], r["funcionario_id"]): dict(r) for r in todos(
        f"""SELECT dia_id, funcionario_id, horas_trabalhadas, justificativa FROM horas
             WHERE dia_id IN ({', '.join('?' for _ in ids)})""", ids)}
    com_registro = {f for (_, f) in registros}
    com_ponto = {d for (d, _) in registros}

    pessoas = {f["id"]: dict(f) for f in todos(
        "SELECT * FROM funcionarios WHERE obra_id = ? AND ativo = 1 AND COALESCE(avulso, 0) = 0", (obra_id,))}
    faltam = [i for i in com_registro if i not in pessoas]
    if faltam:
        pessoas.update({f["id"]: dict(f) for f in todos(
            f"SELECT * FROM funcionarios WHERE id IN ({', '.join('?' for _ in faltam)})", faltam)})
    fora = em_outra_obra(list(pessoas), [d["data"] for d in dias if d.get("data")], obra_id)

    hoje = hoje_iso()
    jornada = config.JORNADA_HORAS
    saida, a_definir = [], []
    for f in sorted(pessoas.values(), key=lambda p: _chave(p["nome"])):
        cond = condicoes(f)
        fichado = cond["vinculo"] == "fichado"
        desde = max(str(f.get("criado_em") or "")[:10], str(f.get("desde") or "")[:10])
        diarias, faltas, justificadas, valor = 0.0, 0, 0, 0.0
        for d in dias:
            util = d["dia_semana"] in config.DIAS_UTEIS
            reg = registros.get((d["id"], f["id"]))
            horas = float(reg["horas_trabalhadas"] or 0) if reg else 0.0
            if horas > 0:
                fracao = min(horas / jornada, 1.0)
                diarias += fracao
                valor += cond["liquido"] * fracao + cond["encargos"]
                continue
            if not fichado or not util:
                continue
            if reg and (reg["justificativa"] or "").strip():
                justificadas += 1
                valor += cond["liquido"] + cond["encargos"]
            elif (d["id"] in com_ponto and d.get("data") and d["data"] <= hoje
                  and (reg or (f["ativo"] and f["obra_id"] == obra_id and desde <= d["data"]))
                  and (f["id"], d["data"]) not in fora):
                faltas += 1
                valor += cond["encargos"]
        if not (diarias or faltas or justificadas):
            continue
        if not cond["definido"]:
            a_definir.append(f["nome"])
        saida.append({"nome": f["nome"], "funcao": f["funcao"] or "", "vinculo": cond["vinculo"],
                      "diarias": round(diarias, 2), "faltas": faltas, "justificadas": justificadas,
                      "definido": cond["definido"], "valor": round(valor, 2) if cond["definido"] else 0.0})
    return {"pessoas": saida, "total": round(sum(p["valor"] for p in saida), 2), "a_definir": a_definir}


def resumo_dos_dias(pessoa: dict) -> str:
    """"4,5 diárias + 1 falta"."""
    def numero(n):
        return f"{n:g}".replace(".", ",")
    partes = [f"{numero(pessoa['diarias'])} diária" + ("" if pessoa["diarias"] == 1 else "s")]
    if pessoa["faltas"]:
        partes.append(f"{pessoa['faltas']} falta" + ("s" if pessoa["faltas"] > 1 else ""))
    if pessoa["justificadas"]:
        partes.append(f"{pessoa['justificadas']} justificada" + ("s" if pessoa["justificadas"] > 1 else ""))
    return " + ".join(partes)
