# -*- coding: utf-8 -*-
"""Segurança: cada teste aqui corresponde a uma falha real que foi corrigida.

As três primeiras foram demonstradas antes da correção: dava para anular o
freio de força bruta trocando um cabeçalho, descobrir quais e-mails existem
pelo tempo de resposta, e derrubar o processo enchendo a memória do contador
de tentativas.
"""

import io
import os
import sys
import tempfile
import statistics
import time

# Caminho do projeto derivado da posição deste arquivo: assim a suíte roda em
# qualquer clone, sem depender de onde a pasta foi colocada.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "ferramentas"))

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

falhas = []


def ok(cond, msg, extra=""):
    print(("  OK  " if cond else " FALHA") + " " + msg + ("" if cond else f"  -> {extra}"))
    if not cond:
        falhas.append(msg)


from obra import config, create_app          # noqa: E402
import obra.security as seguranca            # noqa: E402

app = create_app()


print("== FORÇA BRUTA: CABEÇALHO FORJADO NÃO ESCAPA DO LIMITE ==")
cliente = app.test_client()
bloqueadas = 0
for i in range(25):
    resposta = cliente.post("/api/auth/login",
                            json={"email": "gestor@obra.local", "senha": f"errada{i}"},
                            headers={"X-Forwarded-For": f"10.0.0.{i}"})
    if resposta.status_code == 429:
        bloqueadas += 1
ok(bloqueadas > 0, f"variar X-Forwarded-For não anula o freio ({bloqueadas} bloqueadas de 25)",
   bloqueadas)

print("== FORÇA BRUTA: LIMITE TAMBÉM POR CONTA ==")
seguranca._TENTATIVAS.clear()
outro = app.test_client()
bloqueadas = 0
for i in range(20):
    # cada tentativa "de um IP diferente", como numa botnet
    ambiente = {"REMOTE_ADDR": f"192.168.5.{i}"}
    resposta = outro.post("/api/auth/login", json={"email": "gestor@obra.local", "senha": "x"},
                          environ_base=ambiente)
    if resposta.status_code == 429:
        bloqueadas += 1
ok(bloqueadas > 0, f"ataque distribuído na mesma conta é barrado ({bloqueadas} de 20)", bloqueadas)

print("== ENUMERAÇÃO DE USUÁRIO POR TEMPO ==")
seguranca._TENTATIVAS.clear()


def cronometrar(email, repeticoes=6):
    tempos = []
    for i in range(repeticoes):
        seguranca._TENTATIVAS.clear()
        inicio = time.perf_counter()
        app.test_client().post("/api/auth/login", json={"email": email, "senha": "qualquer"},
                               environ_base={"REMOTE_ADDR": f"172.30.0.{i}"})
        tempos.append((time.perf_counter() - inicio) * 1000)
    return statistics.median(tempos)


existente = cronometrar("gestor@obra.local")
inexistente = cronometrar("naoexiste@obra.local")
razao = max(existente, inexistente) / max(min(existente, inexistente), 0.001)
ok(razao < 2.0,
   f"tempo de resposta não revela se o e-mail existe (existente {existente:.0f}ms, "
   f"inexistente {inexistente:.0f}ms, razão {razao:.1f}x)", razao)

print("== MEMÓRIA DO CONTADOR DE TENTATIVAS ==")
seguranca._TENTATIVAS.clear()
cliente3 = app.test_client()
for i in range(120):
    cliente3.post("/api/auth/login", json={"email": f"u{i}@x.com", "senha": "y"},
                  environ_base={"REMOTE_ADDR": f"10.{i // 256}.{i % 256}.1"})
ok(len(seguranca._TENTATIVAS) <= seguranca.MAX_CHAVES,
   f"tabela de tentativas tem teto ({len(seguranca._TENTATIVAS)} chaves)",
   len(seguranca._TENTATIVAS))

seguranca._TENTATIVAS.clear()
seguranca.registrar_tentativa("1.2.3.4", "a@b.com")
antigo = time.time() - seguranca.JANELA_SEGUNDOS - 1
seguranca._TENTATIVAS["ip:velho"] = [antigo]
seguranca.registrar_tentativa("5.6.7.8", "c@d.com")
ok("ip:velho" not in seguranca._TENTATIVAS, "entradas vencidas são removidas",
   list(seguranca._TENTATIVAS))

print("== ARQUIVO COM EXTENSÃO FALSA ==")
seguranca._TENTATIVAS.clear()
c = app.test_client()
c.post("/api/auth/login", json={"email": "gestor@obra.local", "senha": "obra123"})
H = {"X-CSRF-Token": c.get("/api/sessao").get_json()["csrf"]}
O = c.post("/api/obras", json={"nome": "Segurança"}, headers=H).get_json()["id"]
S = c.post(f"/api/obras/{O}/semanas", json={}, headers=H).get_json()["id"]
DIA = c.get(f"/api/semanas/{S}/dias").get_json()[0]["id"]

executavel = io.BytesIO(b"MZ\x90\x00\x03" + b"\x00" * 400)   # cabeçalho de .exe
r = c.post(f"/api/dias/{DIA}/despesas",
           data={"categoria": "Material", "descricao": "x", "valor": "10",
                 "status_pagamento": "Pago", "imagem": (executavel, "nota.pdf")},
           headers=H, content_type="multipart/form-data")
ok(r.status_code == 400, "executável renomeado para .pdf é recusado", r.status_code)

texto_qualquer = io.BytesIO(b"<html><script>alert(1)</script></html>")
r = c.post(f"/api/dias/{DIA}/fotos", data={"fotos": (texto_qualquer, "foto.jpg")},
           headers=H, content_type="multipart/form-data")
ok(r.status_code == 400, "HTML renomeado para .jpg é recusado", r.status_code)

from PIL import Image                                  # noqa: E402
buffer = io.BytesIO()
Image.new("RGB", (900, 700), (100, 120, 100)).save(buffer, "JPEG")
buffer.seek(0)
r = c.post(f"/api/dias/{DIA}/fotos", data={"fotos": (buffer, "real.jpg")},
           headers=H, content_type="multipart/form-data")
ok(r.status_code == 201, "imagem de verdade continua sendo aceita", r.status_code)

print("== BOMBA DE DESCOMPRESSÃO ==")
ok(Image.MAX_IMAGE_PIXELS is not None and Image.MAX_IMAGE_PIXELS <= 40_000_000,
   f"limite de pixels reduzido ({Image.MAX_IMAGE_PIXELS:,} px)", Image.MAX_IMAGE_PIXELS)

print("== CABEÇALHOS DE SEGURANÇA ==")
cabecalhos = c.get("/login").headers
for nome in ["Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options",
             "Referrer-Policy", "Permissions-Policy", "Cross-Origin-Opener-Policy"]:
    ok(nome in cabecalhos, f"{nome} presente", "ausente")

config.FORCE_HTTPS = True
try:
    with_https = create_app().test_client().get("/login")
    ok("Strict-Transport-Security" in with_https.headers,
       "HSTS presente quando FORCE_HTTPS=1", dict(with_https.headers))
finally:
    config.FORCE_HTTPS = False

print("== PATH TRAVERSAL NO ANEXO ==")
for tentativa in ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "....//....//etc/passwd"]:
    r = c.get(f"/arquivos/{tentativa}")
    ok(r.status_code in (400, 403, 404), f"caminho '{tentativa[:24]}' não escapa da pasta",
       r.status_code)

print("== SESSÃO ==")
anonimo = app.test_client()
ok(anonimo.get(f"/api/obras/{O}/dashboard").status_code == 401, "sem sessão não lê dados")
ok(anonimo.post("/api/obras", json={"nome": "x"}).status_code in (401, 403),
   "sem sessão não escreve")
r = c.post("/api/obras", json={"nome": "sem csrf"})
ok(r.status_code == 403, "escrita sem token CSRF é recusada", r.status_code)

cookie_sessao = [c for c in app.test_client().get("/login").headers.getlist("Set-Cookie")]
print("== SENHA ==")
r = c.post("/api/auth/senha", json={"senha_atual": "obra123", "senha_nova": "123"}, headers=H)
ok(r.status_code == 400, "senha curta é recusada", r.status_code)
r = c.post("/api/auth/senha", json={"senha_atual": "errada", "senha_nova": "outrasenha123"},
           headers=H)
ok(r.status_code in (400, 401, 403), "troca de senha exige a senha atual", r.status_code)

print("== SEGREDOS NÃO VAZAM ==")
diagnostico = c.get("/api/diagnostico").get_json()
texto_diagnostico = str(diagnostico)
ok("senha_hash" not in texto_diagnostico, "hash de senha não aparece no diagnóstico")
sessao = c.get("/api/sessao").get_json()
ok("senha_hash" not in str(sessao), "hash de senha não aparece na sessão")
ok(config.SUPABASE_SERVICE_KEY == "" or config.SUPABASE_SERVICE_KEY not in texto_diagnostico,
   "chave do Supabase não é devolvida ao navegador")

print("\n" + ("SEGURANÇA OK" if not falhas else f"{len(falhas)} FALHA(S): {falhas}"))
sys.exit(1 if falhas else 0)
