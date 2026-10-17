# -*- coding: utf-8 -*-
"""Orçamento de consultas por tela.

Cada ida ao banco é uma viagem de rede até o Supabase. Localmente uma consulta
a mais custa microssegundos e passa despercebida; em produção custa dezenas de
milissegundos. Este teste fixa um teto por endpoint para que um N+1 novo
apareça aqui, e não no celular de quem está no canteiro.

Os limites têm folga sobre o valor atual: o objetivo é pegar regressão grande
(uma consulta por linha), não engessar refatoração.
"""

import io
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

falhas = []


def ok(cond, msg, extra=""):
    print(("  OK  " if cond else " FALHA") + " " + msg + ("" if cond else f"  -> {extra}"))
    if not cond:
        falhas.append(msg)


from PIL import Image                     # noqa: E402
import obra.db as db                      # noqa: E402
from obra import create_app               # noqa: E402

CONTADOR = {"n": 0}
_executar_original = db.Conexao.execute


def _contando(self, sql, args=()):
    CONTADOR["n"] += 1
    return _executar_original(self, sql, args)


db.Conexao.execute = _contando

app = create_app()
c = app.test_client()
c.post("/api/auth/login", json={"email": "gestor@obra.local", "senha": "obra123"})
H = {"X-CSRF-Token": c.get("/api/sessao").get_json()["csrf"]}

# ---- massa parecida com uma obra de dois meses ----
O = c.post("/api/obras", json={"nome": "Desempenho", "cliente": "X"}, headers=H).get_json()["id"]
funcionarios = [c.post(f"/api/obras/{O}/funcionarios", json={"nome": f"P{i}"},
                       headers=H).get_json()["id"] for i in range(8)]


def _foto():
    buffer = io.BytesIO()
    Image.new("RGB", (700, 500), (120, 130, 120)).save(buffer, "JPEG")
    buffer.seek(0)
    return buffer


semanas = []
for s in range(8):
    S = c.post(f"/api/obras/{O}/semanas",
               json={"data_inicio": f"2026-0{6 + s // 4}-{(s % 4) * 7 + 1:02d}"},
               headers=H).get_json()["id"]
    semanas.append(S)
    dias = c.get(f"/api/semanas/{S}/dias").get_json()
    for d in dias[:5]:
        c.put(f"/api/dias/{d['id']}/horas",
              json={"registros": [{"funcionario_id": f, "horas": 8} for f in funcionarios]},
              headers=H)
        for k in range(3):
            c.post(f"/api/dias/{d['id']}/despesas",
                   data={"categoria": "Material", "descricao": f"C{k}", "fornecedor": f"F{k}",
                         "valor": "450", "status_pagamento": "Em Aberto",
                         "numero_nota": f"{s}{k}{d['id']}"},
                   headers=H, content_type="multipart/form-data")
    c.post(f"/api/dias/{dias[0]['id']}/fotos", data={"fotos": [_foto(), _foto()]},
           headers=H, content_type="multipart/form-data")

ULTIMA = semanas[-1]
print("Massa: 8 semanas · 120 despesas · 8 profissionais\n")


def medir(rotulo, url, teto):
    c.get(url)                      # aquecimento
    CONTADOR["n"] = 0
    resposta = c.get(url)
    consultas = CONTADOR["n"]
    ok(resposta.status_code == 200 and consultas <= teto,
       f"{rotulo}: {consultas} consultas (teto {teto})", consultas)
    return consultas


print("== ORÇAMENTO DE CONSULTAS ==")
medir("abertura do app", f"/api/inicio?obra={O}", 22)
medir("painel", f"/api/obras/{O}/dashboard", 36)
medir("fechamento", f"/api/semanas/{ULTIMA}/fechamento", 28)
medir("caixa", f"/api/obras/{O}/financeiro", 10)
medir("locações", f"/api/obras/{O}/locacoes", 8)
medir("dias da semana", f"/api/semanas/{ULTIMA}/dias", 14)
medir("relatório PDF", f"/api/semanas/{ULTIMA}/relatorio.pdf", 30)
medir("planilha de pagamento", f"/api/semanas/{ULTIMA}/pagamentos.xlsx", 32)

print("\n== NÃO CRESCE COM O VOLUME ==")
# O sinal de N+1 é a consulta que se multiplica com o número de linhas.
CONTADOR["n"] = 0
c.get(f"/api/semanas/{semanas[0]}/fechamento")
primeira_semana = CONTADOR["n"]
CONTADOR["n"] = 0
c.get(f"/api/semanas/{ULTIMA}/fechamento")
ultima_semana = CONTADOR["n"]
ok(abs(ultima_semana - primeira_semana) <= 2,
   f"fechamento da 1ª e da 8ª semana custam o mesmo ({primeira_semana} x {ultima_semana})",
   (primeira_semana, ultima_semana))

dia_ultima = c.get(f"/api/semanas/{ULTIMA}/dias").get_json()[1]["id"]
for k in range(10):
    c.post(f"/api/dias/{dia_ultima}/despesas",
           data={"categoria": "Material", "descricao": f"Extra {k}", "valor": "10",
                 "status_pagamento": "Em Aberto"},
           headers=H, content_type="multipart/form-data")
CONTADOR["n"] = 0
c.get(f"/api/semanas/{ULTIMA}/fechamento")
ok(CONTADOR["n"] <= ultima_semana + 2,
   f"mais despesas na semana não multiplicam consultas ({ultima_semana} -> {CONTADOR['n']})",
   CONTADOR["n"])

print("\n== COMPRESSÃO E CACHE ==")
sem_gzip = c.get("/static/css/style.css")
com_gzip = c.get("/static/css/style.css", headers={"Accept-Encoding": "gzip"})
ok(com_gzip.headers.get("Content-Encoding") == "gzip", "CSS é servido comprimido",
   com_gzip.headers.get("Content-Encoding"))
reducao = 1 - len(com_gzip.data) / max(len(sem_gzip.data), 1)
ok(reducao > 0.5, f"compressão reduz mais de 50% ({reducao * 100:.0f}%)", reducao)
ok("max-age=31536000" in (com_gzip.headers.get("Cache-Control") or ""),
   "estático versionado pode ficar em cache", com_gzip.headers.get("Cache-Control"))

resposta_json = c.get(f"/api/semanas/{ULTIMA}/fechamento", headers={"Accept-Encoding": "gzip"})
ok(resposta_json.headers.get("Content-Encoding") == "gzip", "JSON grande também é comprimido",
   resposta_json.headers.get("Content-Encoding"))

print("\n" + ("DESEMPENHO OK" if not falhas else f"{len(falhas)} FALHA(S): {falhas}"))
sys.exit(1 if falhas else 0)
