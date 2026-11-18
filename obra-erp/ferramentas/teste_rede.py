# -*- coding: utf-8 -*-
"""DNS com prazo: resolução presa não trava o bot."""
import os
import socket
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

from obra import rede

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

ENDERECO_V4 = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('149.154.167.220', 443))]
ENDERECO_V6 = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0))]
pedidos = []
modo = {'v4': 'ok'}

def falso(host, port, family=0, type=0, proto=0, flags=0):
    pedidos.append(family)
    if family == socket.AF_INET:
        if modo['v4'] == 'trava':
            time.sleep(1.0)
            return ENDERECO_V4
        if modo['v4'] == 'sem':
            raise socket.gaierror(socket.EAI_NONAME, 'sem IPv4')
        return ENDERECO_V4
    return ENDERECO_V6

rede._original = falso
rede.PRAZO = 0.2
rede._ultimos.clear()

r = rede._resolver('api.telegram.org', 443)
ok(r == ENDERECO_V4 and pedidos == [socket.AF_INET], 'pede IPv4 primeiro', (r, pedidos))

modo['v4'] = 'trava'; pedidos.clear()
inicio = time.monotonic()
r = rede._resolver('api.telegram.org', 443)
ok(r == ENDERECO_V4 and time.monotonic() - inicio < 0.6, 'DNS preso: usa o último endereço em vez de travar',
   time.monotonic() - inicio)

try:
    rede._resolver('nunca-visto.example', 443)
    ok(False, 'DNS preso sem endereço conhecido avisa com erro')
except socket.gaierror:
    ok(True, 'DNS preso sem endereço conhecido avisa com erro')

modo['v4'] = 'sem'; pedidos.clear()
r = rede._resolver('db.supabase.example', 5432)
ok(r == ENDERECO_V6, 'nome só com IPv6 (banco) continua funcionando', (r, pedidos))

print('== UMA CONEXÃO SEGURA PARA TODAS AS CHAMADAS ==')
import ssl, urllib.request
rede._original = socket.getaddrinfo.__wrapped__ if hasattr(socket.getaddrinfo, '__wrapped__') else rede._original
criados = []
original_ssl = ssl.SSLContext.__new__
rede.instalar()
ok(urllib.request._opener is not None, 'opener global instalado')
handler = next(h for h in urllib.request._opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
ok(isinstance(handler._context, ssl.SSLContext), 'HTTPS usa o contexto criado na partida')
class Espiao(ssl.SSLContext):
    pass
ssl.SSLContext = Espiao
def conta(*a, **k):
    criados.append(1)
    return original_ssl(*a, **k)
Espiao.__new__ = conta
try:
    urllib.request.urlopen('https://127.0.0.1:9/', timeout=0.5)
except Exception:
    pass
ok(criados == [], 'urlopen não cria contexto SSL novo a cada chamada', len(criados))

print('\n' + ('REDE OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
