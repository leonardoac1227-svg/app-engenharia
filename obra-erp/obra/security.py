# -*- coding: utf-8 -*-
"""Autenticação, papéis e controle de acesso.

Regras de papel (o "Portal do Cliente" dos softwares do setor):

- **gestor**  — acesso total: financeiro, equipe, obras, usuários.
- **mestre**  — lança RDO, fotos e ponto. Não vê nem lança financeiro.
- **cliente** — só leitura: avanço, fotos e fechamento da(s) obra(s) dele.

Nenhuma dessas regras é aplicada só no frontend: toda rota valida papel *e*
escopo (a qual obra o registro pertence), porque esconder um botão não é
controle de acesso.
"""

from __future__ import annotations

import secrets
import time
from functools import wraps

from flask import g, request, session

from .db import um
from .utils import ApiErro

PODE_FINANCEIRO = {"gestor"}
PODE_LANCAR = {"gestor", "mestre"}
METODOS_INSEGUROS = {"POST", "PUT", "PATCH", "DELETE"}

# Freio simples contra força bruta no login (por IP, em memória do processo).
_TENTATIVAS: dict[str, list[float]] = {}
MAX_TENTATIVAS = 8
JANELA_SEGUNDOS = 300


def registrar_tentativa(ip: str) -> None:
    agora = time.time()
    tentativas = [t for t in _TENTATIVAS.get(ip, []) if agora - t < JANELA_SEGUNDOS]
    tentativas.append(agora)
    _TENTATIVAS[ip] = tentativas


def limpar_tentativas(ip: str) -> None:
    _TENTATIVAS.pop(ip, None)


def bloqueado(ip: str) -> bool:
    agora = time.time()
    tentativas = [t for t in _TENTATIVAS.get(ip, []) if agora - t < JANELA_SEGUNDOS]
    _TENTATIVAS[ip] = tentativas
    return len(tentativas) >= MAX_TENTATIVAS


# ---------------------------------------------------------------- sessão

def usuario_atual() -> dict | None:
    if "usuario" in g:
        return g.usuario
    uid = session.get("usuario_id")
    g.usuario = None
    if uid:
        row = um("SELECT id, nome, email, papel, ativo, senha_provisoria FROM usuarios WHERE id = ?", (uid,))
        if row and row["ativo"]:
            g.usuario = dict(row)
        else:
            session.clear()
    return g.usuario


def iniciar_sessao(usuario_row) -> None:
    session.clear()
    session["usuario_id"] = usuario_row["id"]
    session["csrf"] = secrets.token_urlsafe(24)
    session.permanent = True


def token_csrf() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


def validar_csrf() -> None:
    if request.method not in METODOS_INSEGUROS:
        return
    enviado = request.headers.get("X-CSRF-Token", "")
    esperado = session.get("csrf", "")
    if not esperado or not secrets.compare_digest(enviado, esperado):
        raise ApiErro("Sessão expirada. Recarregue a página e entre novamente.", 403, "csrf")


# ---------------------------------------------------------------- decorators

def login_obrigatorio(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not usuario_atual():
            raise ApiErro("Faça login para continuar.", 401, "sem_sessao")
        return fn(*args, **kwargs)
    return wrapper


def papel_obrigatorio(*papeis):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            usuario = usuario_atual()
            if not usuario:
                raise ApiErro("Faça login para continuar.", 401, "sem_sessao")
            if usuario["papel"] not in papeis:
                raise ApiErro("Seu perfil não tem permissão para esta ação.", 403, "sem_permissao")
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def exigir_financeiro() -> dict:
    usuario = usuario_atual()
    if not usuario or usuario["papel"] not in PODE_FINANCEIRO:
        raise ApiErro("Somente o gestor acessa o módulo financeiro.", 403, "sem_permissao")
    return usuario


def exigir_lancamento() -> dict:
    usuario = usuario_atual()
    if not usuario or usuario["papel"] not in PODE_LANCAR:
        raise ApiErro("Seu perfil é somente leitura.", 403, "somente_leitura")
    return usuario


# ---------------------------------------------------------------- escopo

def obras_permitidas() -> list[int] | None:
    """IDs de obra que o usuário pode ver. `None` significa "todas" (gestor)."""
    usuario = usuario_atual()
    if not usuario:
        return []
    if usuario["papel"] == "gestor":
        return None
    from .db import todos
    rows = todos("SELECT obra_id FROM usuario_obras WHERE usuario_id = ?", (usuario["id"],))
    return [r["obra_id"] for r in rows]


def garantir_acesso_obra(obra_id: int) -> int:
    obra = um("SELECT id FROM obras WHERE id = ?", (obra_id,))
    if not obra:
        raise ApiErro("Obra não encontrada.", 404)
    permitidas = obras_permitidas()
    if permitidas is not None and obra_id not in permitidas:
        raise ApiErro("Você não tem acesso a esta obra.", 403, "sem_permissao")
    return obra_id


def obra_da_semana(semana_id: int) -> int:
    row = um("SELECT obra_id FROM semanas WHERE id = ?", (semana_id,))
    if not row:
        raise ApiErro("Semana não encontrada.", 404)
    return garantir_acesso_obra(row["obra_id"])


def obra_do_dia(dia_id: int) -> int:
    row = um(
        "SELECT s.obra_id AS obra_id FROM dias d JOIN semanas s ON s.id = d.semana_id WHERE d.id = ?",
        (dia_id,),
    )
    if not row:
        raise ApiErro("Dia não encontrado.", 404)
    return garantir_acesso_obra(row["obra_id"])


def obra_da_despesa(despesa_id: int) -> int:
    row = um(
        """SELECT s.obra_id AS obra_id FROM despesas x
             JOIN dias d ON d.id = x.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE x.id = ?""",
        (despesa_id,),
    )
    if not row:
        raise ApiErro("Lançamento não encontrado.", 404)
    return garantir_acesso_obra(row["obra_id"])


def obra_da_foto(foto_id: int) -> int:
    row = um(
        """SELECT s.obra_id AS obra_id FROM fotos f
             JOIN dias d ON d.id = f.dia_id
             JOIN semanas s ON s.id = d.semana_id
            WHERE f.id = ?""",
        (foto_id,),
    )
    if not row:
        raise ApiErro("Foto não encontrada.", 404)
    return garantir_acesso_obra(row["obra_id"])


def obra_do_funcionario(funcionario_id: int) -> int:
    row = um("SELECT obra_id FROM funcionarios WHERE id = ?", (funcionario_id,))
    if not row:
        raise ApiErro("Funcionário não encontrado.", 404)
    return garantir_acesso_obra(row["obra_id"])


def obra_da_etapa(etapa_id: int) -> int:
    row = um("SELECT obra_id FROM etapas WHERE id = ?", (etapa_id,))
    if not row:
        raise ApiErro("Etapa não encontrada.", 404)
    return garantir_acesso_obra(row["obra_id"])
