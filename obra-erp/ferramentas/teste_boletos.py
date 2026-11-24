# -*- coding: utf-8 -*-
"""Boleto com vencimento adiante: programado na sexta de pagamento, sem o gestor ditar o dia."""
import io
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="obra-teste-"))

from reportlab.pdfgen import canvas

from obra import create_app
from obra.services import acoes as catalogo
from obra.services import assistente, boleto

falhas = []
def ok(cond, msg, extra=''):
    print(('  OK  ' if cond else ' FALHA') + ' ' + msg + ('' if cond else f'  -> {extra}'))
    if not cond: falhas.append(msg)

app = create_app()
HOJE = '2026-11-24'
# Linhas fictícias, com o vencimento e o valor dos boletos que serviram de exemplo.
def linha_de(fator, centavos, livre):
    codigo = '9999' + f'{fator}{centavos:010d}' + livre
    campos = ['9999' + livre[:5], livre[5:15], livre[15:]]
    partes = [c + str(boleto._mod10(c)) for c in campos]
    return (' '.join(f'{c[:5]}.{c[5:]}' for c in partes)
            + f' {boleto._mod11(codigo)} {fator}{centavos:010d}')
L1127 = linha_de(1663, 43900, '1' * 25)
L971 = linha_de(1638, 39100, '2' * 25)
L_ANDAIME = linha_de(1638, 39100, '3' * 25)
L_ESCORA = linha_de(1637, 63500, '4' * 25)
ERRADA = L1127.replace(' 1663', ' 1693')

def pdf(*linhas):
    b = io.BytesIO(); cv = canvas.Canvas(b)
    for i, l in enumerate(linhas):
        cv.drawString(60, 700 - 20 * i, l)
    cv.save()
    return {'nome': 'boleto.pdf', 'mime': 'application/pdf', 'dados': b.getvalue()}

print('== LINHA DIGITÁVEL ==')
for linha, venc, valor in ((L1127, '2026-12-17', 439), (L971, '2026-11-22', 391),
                           (L_ANDAIME, '2026-11-22', 391), (L_ESCORA, '2026-11-21', 635)):
    b = boleto.ler_linha(linha, HOJE)
    ok(b and (b['vencimento'], b['valor']) == (venc, valor), f'{venc} R$ {valor}', b)
ok(boleto.ler_linha(ERRADA, HOJE) is None, 'linha com dígito errado é recusada')
ok(boleto.ler_linha('', HOJE) is None and boleto.ler_linha('123', HOJE) is None, 'sem linha')
ok(len(boleto.do_arquivo(pdf(L1127, L1127), HOJE)) == 1, 'a linha repetida no PDF conta uma vez')
ok(boleto.do_arquivo(pdf('Comprovante de pagamento', L1127), HOJE) == [], 'comprovante não é conta a pagar')
ok(boleto.do_arquivo({'mime': 'application/pdf', 'dados': b'lixo'}, HOJE) == [], 'PDF ilegível não derruba')

def passar(fala, arquivos, **dados):
    """Mesma sequência por que a resposta da IA passa."""
    base = {'descricao': 'Locação mensal andaimes', 'fornecedor': 'LOCMAQ', 'valor': 439, 'anexos': [0],
            'status_pagamento': 'Em Aberto', 'data': HOJE}
    acoes = [{'tipo': 'despesa', 'dados': {**base, **dados}}]
    assistente._parcelamento_so_se_dito(acoes, fala)
    assistente._lancar_na_data_certa(acoes, fala, HOJE)
    assistente._programar_boletos(acoes, fala, HOJE, arquivos)
    return acoes[0]['dados']
PROGRAMADO = [{'vencimento': '2026-12-17', 'valor': 439}]

print('== VENCIMENTO ADIANTE ==')
d = passar('obra o13, verifique a data de vencimento e lançe na planilha correspodente', [pdf(L1127)],
           vencimento='2026-12-17')
ok(d.get('parcelas_lista') == PROGRAMADO, 'a fala das 08:40: programado sem ditar o dia', d)
with app.app_context():
    ctx = catalogo.Contexto(1, HOJE, True, True, [pdf(L1127)])
    texto = catalogo._d_despesa(catalogo._n_despesa(d, ctx))
ok(texto.startswith('Despesa programada para 11/12/2026') and '17/12/2026' in texto,
   'o bot mostra a sexta 11/12 e o vencimento', texto)
d = passar('obra o13', [pdf(L1127)])
ok(d.get('parcelas_lista') == PROGRAMADO and d['vencimento'] == '2026-12-17', 'só o arquivo: a data vem do PDF', d)
d = passar('obra o13', [pdf(L1127)], vencimento='2026-11-23')
ok(d.get('parcelas_lista') == PROGRAMADO, 'IA leu a data do documento: vale a linha digitável', d)
d = passar('obra o13, lançar no dia 11/12/2026', [pdf(L1127)], data='2026-12-11', vencimento='2026-12-17')
ok(d.get('parcelas_lista') == PROGRAMADO, 'a fala das 08:44: mesma sexta, guarda o vencimento 17/12', d)
d = passar('lançar no dia 04/12', [pdf(L1127)], data='2026-12-04')
ok(d['parcelas_lista'][0]['vencimento'] == '2026-12-04', 'dia ditado em outra semana vale', d)
d = passar('locação de 17/11 até 17/12, obra o13', [pdf(L1127)])
ok(d.get('parcelas_lista') == PROGRAMADO, 'período citado não é dia de lançar', d)
d = passar('obra o13', [pdf(L1127)], valor=0)
ok(d['valor'] == 439, 'valor que a IA não leu vem do boleto', d)
d = passar('obra o13', [pdf(L1127)], locacao_frequencia='mensal')
ok('locacao_frequencia' not in d, 'boleto não abre aluguel recorrente', d)
foto = {'nome': 'b.jpg', 'mime': 'image/jpeg', 'dados': b'x'}
d = passar('obra o13', [foto], linha_digitavel=L1127)
ok(d.get('parcelas_lista') == PROGRAMADO, 'foto: linha copiada pela IA', d)
d = passar('obra o13', [foto], linha_digitavel=ERRADA)
ok('parcelas_lista' not in d, 'foto com a linha mal copiada não programa', d)

print('== VENCIDO, DESTA SEMANA E PAGO ==')
for linha, venc in ((L971, '2026-11-22'), (L_ESCORA, '2026-11-21')):
    d = passar('boleto da obra o13', [pdf(linha)], parcelas_lista=[{'vencimento': venc, 'valor': 391}])
    ok('parcelas_lista' not in d and d['data'] == HOJE and d['vencimento'] == venc,
       f'vencido em {venc}: fica em hoje, não vai para semana fechada', d)
d = passar('lance hoje, o13', [pdf(L1127)])
ok('parcelas_lista' not in d and d['vencimento'] == '2026-12-17', '"lance hoje" fica nesta semana', d)
d = passar('já paguei, o13', [pdf(L1127)], status_pagamento='Pago')
ok('parcelas_lista' not in d, 'pago não é programado', d)
d = passar('nota da Inova, o13', [pdf('NOTA FISCAL 2353')], vencimento='2026-12-17')
ok('parcelas_lista' not in d, 'nota sem boleto segue como antes', d)
lista = [{'vencimento': '2026-12-17', 'valor': 200}, {'vencimento': '2027-01-17', 'valor': 239}]
d = passar('parcelada em 2x', [pdf(L1127)], parcelas_lista=lista)
ok(d['parcelas_lista'] == lista, 'parcelamento dito não muda', d)

print('\n' + ('BOLETOS OK' if not falhas else f'{len(falhas)} FALHA(S): {falhas}'))
sys.exit(1 if falhas else 0)
