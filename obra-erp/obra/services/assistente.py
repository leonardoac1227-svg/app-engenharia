# -*- coding: utf-8 -*-
"""Assistente de IA da obra — da fala (ou do arquivo) ao lançamento.

O gestor fala como falaria com um colega e anexa fotos, notas, comprovantes
de PIX e áudios. Os áudios são transcritos antes (`transcrever`), porque é pelo
que foi dito que se sabe de qual obra e de qual equipe o contexto precisa. Com
a mensagem em texto, uma chamada à IA:

1. descobre de qual obra se trata (pelo nome, cliente, endereço ou equipe citada);
2. lê os documentos e monta as **ações** do catálogo (`services/acoes.py`),
   que cobre tudo que o app faz: diário, ponto, equipe, fotos, despesas,
   frete, aluguel, semana, folha, relatórios e dados da obra;
3. responde perguntas ("quanto gastei essa semana?") com os dados do contexto.

Este módulo **não grava nada**: confere as ações contra os dados reais e o
perfil do usuário. A gravação passa pelo `Executor`, que usa os endpoints de
sempre — com as mesmas travas dos formulários.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
import time

from .. import config
from ..db import liberar_leitura, todos, um
from ..utils import ApiErro, hoje_iso, somar_dias, texto
from . import acoes as catalogo
from . import boleto, ia_provedores, padroes
from .parcelamento import dia_de_pagar, dividir_valor

log = logging.getLogger(__name__)

MAX_HISTORICO = 8
MAX_OBRAS_DETALHADAS = 3
_FALA_DE_DATA = re.compile(r"\b(hoje|ontem|anteontem|segunda|terca|quarta|quinta|sexta|sabado|domingo"
                           r"|semana passada|mes passado|dia \d{1,2}|\d{1,2}[/.-]\d{1,2})\b")
_NOTA_DO_BOT = re.compile(r"\n\((Complete o lançamento|Estes arquivos reenviados).*?\)(?=\n|$)", re.S)
# Ordem importa só para leitura: com mais de um status na fala, nenhum é aplicado.
_FALA_DE_STATUS = (
    ("Reembolso", r"\breembols\w*|\bdo (meu )?bolso\b|\beu (que )?paguei\b|\badiantei\b"),
    ("Em Aberto", r"\bem aberto\b|\ba pagar\b|\bfaturad\w*|\bnao (foi )?pag[oa]\b|\bfiado\b"),
    ("Pago", r"(?<!nao )(?<!nao foi )\b(ja )?(pago|paga|quitad[oa])\b"),
)
_FALA_DE_PARCELA = re.compile(r"\bparcel\w*|\b\d{1,2}\s*x\b|\b\d{1,2} vezes\b|\ba prazo\b|\bvencimentos?\b"
                              r"|\bduplicatas?\b|\bboletos?\b")
_REEMBOLSO_PARA = re.compile(r"reembols\w*\s+(?:para|pro|pra|do|da|de|ao)\s+(?:o\s+|a\s+)?([A-Za-zÀ-ÿ]{3,})", re.I)
_NAO_SAO_NOMES = {"mim", "meu", "minha", "obra", "nota", "loja", "fornecedor", "material", "cupom"}

ESQUEMA = {
    "type": "object",
    "properties": {
        "obra_id": {"type": "integer"},
        "resposta": {"type": "string"},
        "acoes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": list(catalogo.CATALOGO)},
                    "obra_id": {"type": "integer"},
                    "resumo": {"type": "string"},
                    "dados": catalogo.ESQUEMA_DADOS,
                },
                "required": ["tipo", "dados"],
            },
        },
    },
    "required": ["resposta", "acoes"],
}

PROMPT_BASE = """Você é o assistente de um aplicativo de gestão de obras e tem controle total sobre ele.
O usuário fala de forma informal (texto ou áudio) e manda fotos, notas fiscais, cupons, orçamentos,
boletos e comprovantes de PIX. Transforme isso em AÇÕES do aplicativo e responda SOMENTE com JSON.

REGRAS GERAIS
- Português do Brasil. Nunca invente valor, número de nota, fornecedor, data ou nome.
- Datas AAAA-MM-DD. "Hoje" = data_referencia do contexto; "ontem", "sábado", "dia 12" são relativos a ela.
  Sem data dita, use data_referencia.
- Use os IDs do contexto (obras, funcionários, lançamentos, locações). Não invente IDs.
- Uma ação por coisa a gravar; pode haver várias ações de tipos diferentes.
- Preencha SEMPRE os campos dentro de "dados" (valor, descricao, fornecedor, numero_nota, anexos...).
  O que você escreve em "resposta" não é gravado: só "dados" vira lançamento.
- Só escreva os campos que têm valor: campo vazio, zero ou que não se aplica fica fora do JSON.
  "resposta": uma frase curta. Não repita nela o que já está em "dados".
- Tabelas do contexto: "colunas" dá o nome de cada posição das "linhas". contas_em_aberto = "outras" mais
  os lançamentos de lancamentos_recentes cujos ids estão em "ids_que_estao_em_lancamentos_recentes".
- FALA SOLTA: o usuário fala andando na obra, sem caprichar. Entenda a intenção e complete o resto:
  - Ignore "é...", "tipo", "né", "aí", repetição e frase pela metade. Se ele se corrige ("no O13, não,
    no I19"), vale a última versão.
  - Ordem, gíria e erro de grafia ou de transcrição não importam ("ó treze" = O13, "ítalo" = Hitalo,
    "inova" = Inova civil).
  - Com nota junto, basta dizer a obra e para que serve: valor, fornecedor, número do pedido, itens e
    vencimento você tira do documento. "Comprei um material pra laje do O13" + nota = descrição com os
    itens da nota e "para laje".
  - Sem nota, use os PADRÕES DA EMPRESA para o que faltar; o que não der para saber fica de fora (o bot
    pergunta). Nunca deixe de lançar por a frase estar incompleta.
- Áudios chegam já transcritos na mensagem do usuário: trate a transcrição como a própria mensagem.
  Áudio nunca vai em "anexos".
- Execute: gere as ações do que foi dito ou do que está no documento. Não peça por escrito o que dá
  para ler do arquivo ou deduzir do contexto.
- "obra_id": a obra de que a mensagem trata (pelo nome, apelido, cliente ou endereço citado, pela
  equipe citada, ou pelo endereço impresso num documento). Se não der para saber e houver mais de uma
  obra, use 0.
- Arquivo sem nenhuma instrução: nota, cupom, boleto ou comprovante vira despesa com o que você leu;
  imagem da obra vira "foto" do dia de referência.
- Perguntas ("quanto gastei?", "o que vence essa semana?", "quem faltou?"): responda em "resposta"
  com os números do contexto, sem criar ações.

DOCUMENTOS (nota, cupom, orçamento, recibo, boleto, PIX)
- O VALOR vem SEMPRE do documento: o TOTAL final a pagar (depois de descontos), nunca subtotal,
  preço unitário ou valor de um item. Leia com atenção foto torta, amassada ou com dedo na frente.
  Só deixe valor 0 se não houver documento ou o total estiver ilegível — e diga isso na resposta.
- Fornecedor: só o que o usuário disser ou o que estiver impresso no documento. Sem documento e sem
  fornecedor dito, deixe vazio: não copie de "padroes_recentes" nem de lançamentos parecidos.
- Nome do fornecedor: o nome curto pelo qual a loja é conhecida ("Inova civil", "O engenheiro", "Posto Sossego"),
  com maiúscula só no começo. Sem "LTDA", CNPJ, cidade ou filial.
- NÚMERO (numero_nota), nesta ordem:
  1. Se o documento tiver NÚMERO DO PEDIDO impresso ("Pedido", "Nº do pedido", "Ped.", "Pedido de venda",
     "Nº pedido cliente"), use o PEDIDO, no lugar da NF: "PED 48213". Número de contrato ou de boleto
     não é pedido. O NOME DO ARQUIVO também vale ("PED 3248 OZIRLEI.pdf" → "PED 3248"). Vale também para nota fiscal que
     traga o pedido no cabeçalho ou nas informações complementares. Procure em toda a página.
  2. Sem pedido: o número da NF.
  3. Outro documento (orçamento, "não é documento fiscal", recibo): o número com o tipo na frente
     ("ORC 31797", "REC 1191").
  PIX: o ID da transação (E2E) e o recebedor como fornecedor.
- STATUS — como esta empresa lança (vem do histórico real, não da forma de pagamento impressa):
  1. O que o usuário disser vale sempre, em qualquer parte da mensagem, inclusive no fim:
     "reembolso", "paguei do bolso", "eu paguei" = Reembolso; "em aberto", "a pagar", "faturado" =
     Em Aberto; "pago", "já paguei a loja", "quitado" = Pago.
  2. Sem nada dito: nota de fornecedor com conta (Inova civil, Somar, Central da construção, Alta tensão,
     locadoras, Diego motoboy, SOS Entulho, Hitalo) = Em Aberto; compra de balcão paga na hora (cupom,
     dinheiro, PIX, cartão) em loja pequena, posto, papelaria = Reembolso.
  3. "Pago" só quando o usuário disser. Nunca deduza "Pago" pelo cupom.
- Reembolso: "reembolsar_para" é quem pagou. Sem nome dito, deixe vazio (o app usa o padrão, Léo).
  "Reembolso do Luiz", "o Luiz pagou" = reembolsar_para "Luiz".
- Data do lançamento: a que o usuário disser; sem data dita, data_referencia (hoje), mesmo que o documento
  seja de outro dia: o lançamento entra na semana em que foi enviado. A data do documento não vira "data".
  Vencimento de boleto ou contrato vai em "vencimento", nunca em "data".
- BOLETO: copie "Data de Vencimento" em "vencimento" e a linha digitável (47 números) em "linha_digitavel".
  Não confunda com "Data do Documento", "Data Processamento" nem com o período da locação ("17/11 até 17/12").
  Fornecedor = Beneficiário (nunca o banco nem o Pagador); valor = "Valor do Documento", sem multa nem juros.
  Descrição pelo que está em "Instruções" ("Locação mensal de 50 escoras metálicas").
  Boleto que vence depois desta semana é programado pelo app na sexta de pagamento até o vencimento
  (vence quinta 17/12 → planilha da sexta 11/12), mesmo sem o usuário pedir. Boleto já vencido ou que se
  paga nesta semana entra hoje. Não preencha parcelas_lista nem locacao_frequencia só por causa do boleto.
  Se o usuário disser o dia ("lançar no dia 11/12"), esse dia vai em "data" e o impresso em "vencimento".
- Número do documento sempre em numero_nota, nunca na descrição. Recibo: "REC 1191".
- NOTA FATURADA, lida do documento: se ele mostrar venda a prazo — "faturado", "a prazo", "condição de
  pagamento 30/60", "30 DDL", "28/56 dias", boleto a vencer ou quadro DUPLICATAS/FATURA com data depois da
  emissão — marque faturada true, MESMO que o usuário não diga nada, e preencha:
  - parcelas_lista com cada vencimento e valor, se o documento imprime as datas;
  - senão, condicao_pagamento só com os prazos ("30/60") e data_documento com a emissão: o app calcula as datas.
  "À vista", "dinheiro", "PIX", "cartão", "pago", duplicata no próprio dia da emissão: faturada false. Não
  deduza prazo pelo fornecedor: só o que está escrito. Inova civil entra sempre na semana em que é enviada (o app
  cuida disso), mesmo faturada, salvo se o usuário pedir para parcelar ou programar.
- COBRANÇA MENSAL: "esse boleto vai se repetir todo mês", "vem todo dia 22", "é mensal até acabar a obra", "todo
  mês tem esse": a despesa com repete_mensal true (e vencimento). Se o lançamento JÁ existe ("os boletos da
  Locmaq que mandei hoje se repetem"), um repetir_cobranca por lançamento citado. Com número de vezes ("em 3x")
  é parcelamento, não cobrança mensal. Boleto de "locação mensal" sem o usuário dizer que repete: não repete.
  - "A cobrança 3 não vai ter mais", "COB 3 acabou", "devolvi as escoras, não vem mais boleto":
    encerrar_cobranca com o número (ou a única de cobrancas_mensais que corresponde; na dúvida, pergunte qual).
  - Boleto novo de uma cobrança que já está em cobrancas_mensais: despesa normal, sem repete_mensal. O app liga
    ao mês certo e não duplica.
- NOTA A PRAZO / PARCELADA (quando o usuário disser "parcelada", "em 3x", "a prazo", "tem boleto",
  "vencimentos", "parcelas"): leia o quadro "PARCELAS", "DUPLICATAS" ou "FATURA" do documento e preencha
  parcelas_lista com cada vencimento e valor exatamente como impressos, na ordem (mesmo que seja uma
  parcela só). valor = total da nota. Copie o vencimento como impresso: o app põe cada parcela sozinho na
  semana em que ela é paga (a sexta antes do vencimento). Sem quadro
  impresso, use parcelas + parcela_frequencia ditos pelo usuário.
- Descrição no estilo da empresa: curta, o que foi comprado + para quê. Quantidade quando ajuda; sem marca,
  código, medida técnica de catálogo ou preço. Muitos itens: os 2 ou 3 principais e "materiais diversos".
  Bom: "6kg de pregos para execução de vigas e pilares", "Discos de corte para telas da laje",
  "Espaçadores para assentamento de porcelanato da piscina", "Conta da internet referente ao mês de outubro".
  Ruim: "5 un de Disco corte aço 115,0X1,6X22,23 DCV Vonder, 1 un de Broca SDS plus 8,0x100x160".
- Todo arquivo que não for foto da obra (nota, cupom, orçamento, boleto, comprovante) vai em "anexos" da
  despesa a que se refere — o usuário quer o arquivo salvo junto com o lançamento.
- Mesma nota (número e fornecedor) já nos lançamentos do contexto, ou PIX de uma conta em aberto
  igual: NÃO crie outra — use editar_despesa (anexando o comprovante) ou pagar_despesa.

CORRIGIR O QUE JÁ FOI LANÇADO
- "Corrige", "muda", "troca", "na verdade", "está errado", "esse lançamento", "a última nota", "a nota do
  Inova", "passa para reembolso", "é da outra obra": use editar_despesa com o despesa_id. NUNCA crie uma
  despesa nova para corrigir outra.
- "lancamentos_recentes" vem do mais novo para o mais antigo, com os lançamentos da semana passada também:
  "o último" é o primeiro da lista. A conversa anterior mostra "#id" do que foi gravado: "o #212" é esse id.
- Lançado na obra errada: alteracoes.mover_para_obra com o id da obra certa. Dia errado: mover_para_data.
- Mais de um lançamento pode ser o citado e nada os diferencia: não altere; pergunte qual em "resposta",
  listando-os com o #id.
- Só use excluir_despesa quando o usuário pedir para apagar ou excluir.

PADRÕES DA EMPRESA (fala curta já é o lançamento completo: preencha "dados" por estas regras)
{padroes}
- Valor que o usuário disser ou que estiver no documento vale mais que o valor do padrão. Usar o valor
  do padrão não é inventar. Status dito pelo usuário vale mais que o status do padrão.

EXEMPLOS (fala → ações). Siga o mesmo raciocínio em falas parecidas.
{exemplos}
- "padroes_recentes" do contexto mostra o que mais se repete: siga a mesma escrita de descrição,
  fornecedor, categoria e status em lançamentos parecidos, mas só use o valor dali se a fala não trouxer
  valor e ele for sempre o mesmo.

DIÁRIO DE OBRA
- Linguagem técnica de engenharia, impessoal ("Executada a concretagem da laje do 2º pavimento...").
- Relato por funcionário: escreva por pessoa/frente, com quantidade, unidade e turno
  ("Ademir: execução de 27 m² de reboco interno (manhã); arestamento de vãos (tarde)."). "Metros" de
  serviço de área (reboco, chapisco, alvenaria, contrapiso, pintura, revestimento, forro) = m².
- Se o diário do dia já tem texto, devolva o texto COMPLETO mesclado, sem perder o que existia.
- Relato do dia que cita quem trabalhou gera também o ponto.

PONTO
- "Ademir trabalhou o dia inteiro hoje", "Ademir e Ailton vieram": uma ação ponto com um registro por
  pessoa, com jornada_horas, na data dita (sem data, data_referencia).
- VÁRIOS DIAS: "de segunda a quinta", "todos os dias menos sexta", "segunda, terça e quarta", "a semana
  toda": UMA AÇÃO PONTO POR DIA citado, todas iguais, cada uma com a sua data (dias da semana de
  data_referencia). "Todos os dias" / "a semana toda" = segunda a sexta. Nunca lance só o primeiro dia.
  Ex.: "8 horas para todo o efetivo do I19 de segunda a quinta" → 4 ações ponto {equipe_inteira true,
  horas 8}, datas de segunda, terça, quarta e quinta.
- ATÉ HOJE: "até hoje ninguém faltou no I19", "tá todo mundo vindo", "essa semana veio todo mundo até agora",
  "ponto em dia até hoje", "pode botar o ponto do pessoal até hoje": equipe_inteira em CADA dia útil da
  segunda-feira desta semana até hoje (na terça = segunda e terça). "Até ontem" não inclui hoje; "desde
  quarta" começa na quarta. Nunca lance dia futuro nessas falas.
- Com exceção em um dia ("até hoje só o Carlos faltou, na segunda"): uma ação por dia; só na segunda o
  Carlos vai em registros com horas 0. Sem motivo dito, justificativa vazia: nunca invente motivo.
- "Meia diária", "só veio de manhã", "saiu no almoço" = metade da jornada. "Chegou 2 horas atrasado" = jornada
  menos 2. "Faltou mas trouxe atestado" = horas 0, justificativa "Atestado médico". "Vai ficar afastado até
  sexta" = horas 0 com o motivo em cada dia até sexta.
- "Choveu, ninguém trabalhou hoje": equipe_inteira true, horas 0, justificativa "Chuva". "Choveu, o pessoal
  foi embora meio-dia": horas = metade, justificativa "Chuva".
- "Feriado, não teve obra": não lance ponto; registre no diário (observacoes).
- "Mesma equipe de ontem": repetir_ponto. "Sábado vieram só o Ademir e o Cosme": registros só deles no sábado.
- "O ponto do João de ontem tá errado, ele veio": ponto de ontem só com o João e a jornada (corrige só ele).
- "A equipe inteira", "todo mundo", "a turma toda": equipe_inteira = true, sem listar ninguém. Quem
  fugiu da regra ("menos o Carlos, que faltou") vai em registros com as horas dele.
- Nome parecido com o de alguém da equipe é essa pessoa: o áudio costuma errar a grafia.

EQUIPE
- "Foi demitido", "mandei embora", "pediu as contas", "saiu da obra", "não trabalha mais": editar_funcionario
  com alteracoes.ativo = false. Ele sai das listas da obra e o histórico de ponto continua.
  Quem saiu fica fora de "equipe inteira". Só registre ponto para ele se a fala disser que trabalhou
  naquele dia ("trabalhou de manhã e foi mandado embora").
- "Voltou", "foi readmitido": editar_funcionario com alteracoes.ativo = true.
- Pessoa nova citada com função ("entrou o Marcos, armador"): funcionario. Mudou função, telefone ou PIX:
  editar_funcionario só com o que mudou.
- TRANSFERÊNCIA: mudou de obra DE VEZ ("Seu Zé foi transferido pro T21", "passei o Zé pra lá", "agora o Zé é
  do T21", "o Zé saiu do I19 e foi pro T21", "troca o Zé de obra", "o Zé tá no T21 a partir de hoje"):
  transferir_funcionario com obra_id = obra ANTIGA e mover_para_obra = id da NOVA. Não é demissão: não use
  editar_funcionario ativo false. Se a fala só disser a obra nova, a antiga é a que tem a pessoa na equipe.
  - "... e chegou um ajudante novo, o Pedro", "no lugar dele entrou o Pedro": além da transferência, uma ação
    funcionario (Pedro, função dita) na obra que ficou sem ele.
  - "Troca o Zé pelo Pedro, o Pedro do T21 vem pro I19": duas transferências.
  - Emprestado por um dia ("o Zé foi ajudar no T21 hoje", "amanhã ele volta") NÃO é transferência: é diária
    avulsa no T21. Na dúvida entre as duas, sem "de vez", "agora é", "transferido": trate como emprestado e
    pergunte em "resposta".
  - Ponto de dias em que ele ainda estava na obra antiga fica na antiga: registros com o nome dele.
- PESSOA NOVA ("chegou o Pedro", "entrou um pedreiro novo, o Marcos", "tá começando hoje o João, servente
  prático, fichado", "contratei o Bruno, avulso, diária de 120, pix 75 9...", "amanhã chega um ajudante, o Léo"):
  funcionario com TUDO que foi dito: nome, funcao, data (dia em que chega), vinculo, classe, diaria, telefone,
  documento (CPF), chave_pix. O que não foi dito fica de fora: o app avisa o que falta.
  - "De carteira", "registrado", "fichado" = vinculo fichado. "Por diária", "por fora", "diarista", "avulso" =
    vinculo avulso. "Servente prático" ou "comum" também define a classe.
  - Avulso pedreiro ou carpinteiro sem diária dita: deixe diaria de fora (o app usa o padrão).
  - Sem nome ("chegou um ajudante novo"): não cadastre; pergunte o nome em "resposta".
  - Chegou e já trabalhou ("chegou o Pedro e trabalhou hoje"): funcionario + ponto dele na data.
  - Resposta que completa o cadastro de antes ("ele é avulso, 120", "é fichado, servente comum", "o pix dele
    é..."): editar_funcionario da pessoa da conversa anterior. Nunca cadastre a mesma pessoa duas vezes.
  - Voltou quem já foi da obra: editar_funcionario ativo true. Só uma ou duas diárias: é diária avulsa no ponto.
- FICHADO E AVULSO: "o Cosme é fichado", "seu José é avulso, a diária dele é 100", "aumenta a diária do Cosme
  pra 140", "o Pedro é servente prático": editar_funcionario (ou funcionario, se é novo) com vinculo, classe
  ou diaria. classe só para fichado. Nunca invente vínculo, classe nem diária.
- Apelido e tratamento: "Seu Zé", "Zé", "Manteiga" e "Seu jose(manteiga)" são a mesma pessoa. Se dois da
  equipe puderem ser o citado, não grave; pergunte qual.
- DIÁRIA EM OUTRA OBRA: quem passou uma ou duas diárias nesta obra sem ser dela ("o Zé do O13 veio ajudar hoje",
  "um avulso, o Bira, fez uma diária", "o Ademir passou o dia no T21"): registro no ponto da obra onde
  trabalhou com avulso = true e o nome. NÃO crie funcionario nem editar_funcionario: ele não entra na
  equipe desta obra e continua na equipe da obra dele.
- Exemplo: "o efetivo todo compareceu hoje, o Carlos foi demitido" → editar_funcionario {nome "Carlos",
  alteracoes {ativo false}} + ponto {equipe_inteira true}.

FOTOS
- Imagens da obra (não documentos) viram "foto" no dia indicado ("relatório fotográfico de ontem").

AÇÕES DISPONÍVEIS (tipo: quando usar / campos de "dados")
{catalogo}

FORMATO (JSON):
{{"obra_id": 0, "resposta": "1 a 4 frases ao usuário, sem markdown",
 "acoes": [{{"tipo": "...", "obra_id": 0, "resumo": "frase curta", "dados": {{...}}}}]}}
Em "dados", inclua só os campos que se aplicam."""

PROMPT_TRANSCRICAO = """Transcreva fielmente, em português do Brasil, o que é dito nestes áudios de um
gestor de obra. Corrija só a pontuação e termos de obra mal ouvidos. Responda somente com a transcrição,
sem comentários. Sem fala compreensível, responda apenas: INAUDIVEL
Nomes que podem aparecer (use esta grafia): {nomes}"""

PROMPT_RDO = """Você é engenheiro civil e redige o Relatório Diário de Obra (RDO).
Com base SOMENTE nos dados do dia informados, escreva em português do Brasil, linguagem técnica e
impessoal, sem inventar serviços que não estejam nos dados. Responda só com JSON:
{"producao_dia": "...", "clima": "...", "observacoes": "..."}
- producao_dia: serviços executados, efetivo presente, materiais recebidos, quantidades e locais.
- clima: mantenha o que já estiver informado; se não houver informação, "".
- observacoes: faltas e justificativas, ocorrências, pendências e pontos de atenção.
Se já houver texto nos campos, aproveite e melhore a redação sem perder informação."""

ESQUEMA_RDO = {
    "type": "object",
    "properties": {"producao_dia": {"type": "string"}, "clima": {"type": "string"},
                   "observacoes": {"type": "string"}},
    "required": ["producao_dia", "clima", "observacoes"],
}


# ---------------------------------------------------------------- contexto

def _semana_da_data(obra_id: int, referencia: str):
    linha = um("""SELECT s.id, s.numero, s.data_inicio, s.meta, s.orcamento_previsto, s.fechada, s.folha_paga
                    FROM dias d JOIN semanas s ON s.id = d.semana_id
                   WHERE s.obra_id = ? AND d.data = ?""", (obra_id, referencia))
    if not linha:
        return None
    semana = dict(linha)
    semana["data_fim"] = somar_dias(semana["data_inicio"], 6) if semana["data_inicio"] else None
    return semana


def _tabela(linhas: list[dict]) -> dict:
    """Lista de registros como tabela: o nome de cada campo vai uma vez só, não em toda linha."""
    if not linhas:
        return {"colunas": [], "linhas": []}
    colunas = list(linhas[0])
    return {"colunas": colunas, "linhas": [[l[c] if l[c] is not None else "" for c in colunas] for l in linhas]}


def _contexto_obra(obra: dict, referencia: str, pode_financeiro: bool) -> dict:
    obra_id = obra["id"]
    ctx = {k: obra.get(k) for k in ("id", "nome", "cliente", "endereco")}
    ctx["funcionarios_ativos"] = [dict(r) for r in todos(
        "SELECT id, nome, funcao FROM funcionarios WHERE obra_id = ? AND ativo = 1 ORDER BY nome", (obra_id,))]

    dia = um("""SELECT d.id, d.producao_dia, d.clima, d.observacoes FROM dias d
                  JOIN semanas s ON s.id = d.semana_id WHERE s.obra_id = ? AND d.data = ?""",
             (obra_id, referencia))
    ctx["dia_referencia"] = None
    if dia:
        ctx["dia_referencia"] = {
            "diario_ja_escrito": {k: dia[k] or "" for k in ("producao_dia", "clima", "observacoes")},
            "ponto_ja_lancado": [dict(r) for r in todos(
                """SELECT f.nome, h.horas_trabalhadas AS horas, h.justificativa FROM horas h
                     JOIN funcionarios f ON f.id = h.funcionario_id WHERE h.dia_id = ?""", (dia["id"],))],
            "fotos": um("SELECT COUNT(*) AS n FROM fotos WHERE dia_id = ?", (dia["id"],))["n"],
        }

    semana = _semana_da_data(obra_id, referencia)
    ctx["semana_atual"] = semana
    if semana:
        horas = um("""SELECT COALESCE(SUM(h.horas_trabalhadas), 0) AS h FROM horas h
                        JOIN dias d ON d.id = h.dia_id WHERE d.semana_id = ?""", (semana["id"],))["h"]
        semana["horas_lancadas"] = round(float(horas or 0), 1)

    if pode_financeiro:
        # Ordem de gravação, não de data: "o último lançamento" é o último que entrou.
        recentes = [dict(r) for r in todos(
            """SELECT x.id, d.data, x.categoria, x.descricao, x.fornecedor, x.valor, x.status_pagamento,
                      x.numero_nota, x.vencimento
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? ORDER BY x.id DESC LIMIT 30""", (obra_id,))]
        abertas = [dict(r) for r in todos(
            """SELECT x.id, d.data, x.descricao, x.fornecedor, x.valor, x.numero_nota, x.vencimento
                 FROM despesas x JOIN dias d ON d.id = x.dia_id JOIN semanas s ON s.id = d.semana_id
                WHERE s.obra_id = ? AND x.status_pagamento = 'Em Aberto'
                ORDER BY COALESCE(x.vencimento, d.data) LIMIT 30""", (obra_id,))]
        # A conta em aberto que já está nos recentes não vai escrita duas vezes: só o id.
        ja = {r["id"] for r in recentes}
        ctx["lancamentos_recentes"] = _tabela(recentes)
        ctx["contas_em_aberto"] = {
            "ids_que_estao_em_lancamentos_recentes": [a["id"] for a in abertas if a["id"] in ja],
            "outras": _tabela([a for a in abertas if a["id"] not in ja])}
        from .cobrancas import listar as listar_cobrancas
        ctx["cobrancas_mensais"] = [{k: c[k] for k in ("id", "descricao", "fornecedor", "valor", "dia", "proximo")}
                                    for c in listar_cobrancas(obra_id)]
        if semana:
            total = um("""SELECT COALESCE(SUM(x.valor), 0) AS t,
                                 COALESCE(SUM(CASE WHEN x.status_pagamento = 'Em Aberto' THEN x.valor ELSE 0 END), 0) AS a
                            FROM despesas x JOIN dias d ON d.id = x.dia_id WHERE d.semana_id = ?""", (semana["id"],))
            semana["gasto_da_semana"] = round(float(total["t"] or 0), 2)
            semana["em_aberto_da_semana"] = round(float(total["a"] or 0), 2)
        from .locacoes import listar_locacoes
        ctx["locacoes_ativas"] = [
            {"id": l["id"], "equipamento": l["equipamento"], "fornecedor": l["fornecedor"],
             "frequencia": l["frequencia"], "valor_parcela": l["valor_parcela"],
             "proxima_parcela": {k: l["proxima_parcela"][k] for k in ("id", "numero", "vencimento", "valor")}
             if l.get("proxima_parcela") else None}
            for l in listar_locacoes(obra_id, incluir_encerradas=False)]
    return ctx


def _jeito_de_falar(nome: str) -> str:
    """Como a obra aparece na fala: "I19" também é "I-19", "i 19" e "obra 19"."""
    chave = catalogo.sem_acento(nome)
    sigla = re.fullmatch(r"([a-z]{1,3})\s*-?\s*(\d{1,4})", chave)
    if not sigla:
        return rf"(?<!\w){re.escape(chave)}(?!\w)"
    letras, numero = sigla.groups()
    return rf"(?<!\w)(?:{letras}\s*-?\s*{numero}|obras?\s+(?:d[oa]\s+)?{numero})(?!\d)"


def _obras_citadas(texto_msg: str, obras: list[dict]) -> list[int]:
    alvo = f" {catalogo.sem_acento(fala_do_usuario(texto_msg))} "
    return [o["id"] for o in obras if len(catalogo.sem_acento(o["nome"])) >= 2
            and re.search(_jeito_de_falar(o["nome"]), alvo)]


def _equipes(obras: list[dict]) -> dict[int, list[str]]:
    """Nomes da equipe ativa de cada obra."""
    ids = [o["id"] for o in obras]
    equipes: dict[int, list[str]] = {i: [] for i in ids}
    if ids:
        for linha in todos(f"""SELECT obra_id, nome FROM funcionarios
                                WHERE ativo = 1 AND obra_id IN ({', '.join('?' for _ in ids)})
                                ORDER BY nome""", ids):
            equipes[linha["obra_id"]].append(linha["nome"])
    return equipes


def montar_contexto(obras: list[dict], referencia: str, pode_financeiro: bool, pode_gestor: bool,
                    foco: list[int]) -> dict:
    detalhadas = (foco + [o["id"] for o in obras if o["id"] not in foco])[:MAX_OBRAS_DETALHADAS]
    equipes = _equipes([o for o in obras if o["id"] not in detalhadas])
    return {
        "padroes_recentes": padroes.repetidos([o["id"] for o in obras], referencia) if pode_financeiro else [],
        "data_referencia": referencia,
        "dia_da_semana": catalogo.hoje_semana(referencia),
        "jornada_horas": config.JORNADA_HORAS,
        "pode_financeiro": pode_financeiro,
        "pode_gestor": pode_gestor,
        "categorias": config.CATEGORIAS_PADRAO,
        "reembolso_padrao": config.REEMBOLSO_PADRAO,
        "obras": [_contexto_obra(o, referencia, pode_financeiro) if o["id"] in detalhadas
                  else {**{k: o.get(k) for k in ("id", "nome", "cliente", "endereco")},
                        "equipe": equipes[o["id"]]} for o in obras],
    }


def eh_audio(arquivo: dict) -> bool:
    return arquivo["mime"].startswith("audio/")


def transcrever(arquivos: list[dict], obras: list[dict], prazo: float) -> str:
    """Áudios em texto. A grafia das obras e da equipe vai junto: é onde o áudio mais erra."""
    audios = [a for a in arquivos if eh_audio(a)]
    if not audios:
        return ""
    nomes = [o["nome"] for o in obras] + [n for equipe in _equipes(obras).values() for n in equipe]
    liberar_leitura()
    inicio = time.monotonic()
    if config.XAI_API_KEY:
        termos = list(dict.fromkeys(nomes + [oficial for _, oficial in padroes.FORNECEDORES]))
        try:
            partes = [ia_provedores.transcrever_xai(a, termos, prazo - (time.monotonic() - inicio))
                      for a in audios]
            if all(partes):
                return texto("\n".join(partes), 6000)
            log.warning("Grok não ouviu fala no áudio; tentando o Gemini")
        except ia_provedores.FalhaProvedor as erro:
            log.warning("Grok falhou (%s); tentando o Gemini", erro)
    bruto, _ = ia_provedores.gerar(
        PROMPT_TRANSCRICAO.replace("{nomes}", ", ".join(dict.fromkeys(nomes))),
        "Transcreva os áudios.", audios, None, max_tokens=4096, pensar="minimo",
        prazo=max(prazo - (time.monotonic() - inicio), 15))
    transcricao = texto(bruto, 6000)
    if not transcricao or catalogo.sem_acento(transcricao).strip(" .") == "inaudivel":
        raise ApiErro("Não consegui entender o áudio. Grave de novo, mais perto do microfone, "
                      "ou mande por texto.", 422, "audio_inaudivel")
    return transcricao


def fala_do_usuario(mensagem: str) -> str:
    """Só o que o usuário disse: o bot acrescenta notas próprias, como "(Complete o lançamento...)",
    que citam data e status de lançamentos antigos e enganariam as regras abaixo."""
    return _NOTA_DO_BOT.sub("", mensagem or "")


def _data_foi_dita(mensagem: str) -> bool:
    return bool(_FALA_DE_DATA.search(catalogo.sem_acento(fala_do_usuario(mensagem))))


def _despesas(acoes: list[dict]) -> list[dict]:
    lista = []
    for acao in acoes:
        if acao.get("tipo") == "despesa":
            if not isinstance(acao.get("dados"), dict):
                acao["dados"] = {}
            lista.append(acao["dados"])
    return lista


def _lancar_na_data_certa(acoes: list[dict], mensagem: str, referencia: str) -> None:
    """Sem data dita, a despesa entra hoje, ainda que o modelo copie a data do documento.

    Com a data do recibo ela caía na semana anterior, fora da tela que o gestor
    estava olhando, e parecia que o bot não tinha lançado nada. Data futura é
    vencimento lido do documento, nunca o dia do lançamento.
    """
    dita = _data_foi_dita(mensagem)
    for dados in _despesas(acoes):
        data = catalogo.data_ou(dados.get("data"), "")
        if data > referencia:
            dados.setdefault("vencimento", data)
            if dita and (not dados.get("parcelas_lista") or dados.get("faturada") is True):
                dados["_dia_dito"] = True   # o dia que o gestor ditou vale mais que o impresso
                # "Programe para o dia 20" é conta a pagar nesse dia: entra na
                # semana em que é paga, sem criar semana futura antes da hora.
                dados["parcelas_lista"] = [{"vencimento": data, "valor": dados.get("valor")}]
        if not dita or data > referencia:
            dados["data"] = referencia


def _prazos(condicao) -> list[int]:
    """"30/60/90 dias", "28 DDL", "30 e 60" → [30, 60, 90]. "À vista" ou "2x" → []."""
    if re.search(r"entrada|sinal|\bato\b|vista|%", catalogo.sem_acento(str(condicao or ""))):
        return []   # parte paga na hora: dividir tudo pelos prazos cobraria errado
    dias = [int(n) for n in re.findall(r"\d+", str(condicao or ""))]
    if not dias or len(dias) > 12 or any(not 5 <= n <= 365 for n in dias) or dias != sorted(set(dias)):
        return []
    return dias


# Pedido para usar a data que está no papel ("segue a data impressa", "na planilha correspondente").
_SEGUIR_IMPRESSA = re.compile(
    r"\b(segu\w+|sig\w+|respeit\w+|conforme|de acordo com|usa\w*|use)\b.{0,30}\b(data|datas|vencimentos?)\b"
    r"|\bdatas? (que (esta|ta|tem) )?(impressas?|na nota|da nota|no boleto|do boleto|do vencimento)\b"
    r"|\bno (dia do )?vencimento\b|\b(semana|planilha|data) corresp\w*")
# Com a Inova, só programa se o gestor pedir a programação; "boleto" ou "vencimento" não bastam.
_PEDIU_PRAZO = re.compile(r"\bparcel\w*|\b\d{1,2}\s*x\b|\b\d{1,2} vezes\b|\ba prazo\b|\bprogram\w*|"
                          + _SEGUIR_IMPRESSA.pattern)


def _programar_faturadas(acoes: list[dict], mensagem: str, referencia: str) -> None:
    """Nota faturada vai para as semanas em que é paga, mesmo sem o gestor falar em parcela.

    As datas impressas valem; sem elas, contam-se os prazos a partir da emissão.
    Nota com tudo a pagar nesta semana, ou vencida, fica em hoje.
    """
    fala = catalogo.sem_acento(fala_do_usuario(mensagem))
    hoje = date.fromisoformat(referencia[:10])
    domingo = hoje + timedelta(days=6 - hoje.weekday())
    for dados in _despesas(acoes):
        if dados.pop("_dia_dito", False):
            continue
        if _SEGUIR_IMPRESSA.search(fala) and not dados.get("parcelas_lista"):
            impresso = catalogo.data_ou(dados.get("vencimento"), "")
            if impresso > referencia and not _prazos(dados.get("condicao_pagamento")):
                dados["parcelas_lista"] = [{"vencimento": impresso, "valor": dados.get("valor")}]
            dados["faturada"] = True
        if dados.get("faturada") is not True:
            continue
        if padroes.sempre_na_semana(dados.get("fornecedor")) and not _PEDIU_PRAZO.search(fala):
            datas = [catalogo.data_ou(p.get("vencimento"), "") for p in dados.pop("parcelas_lista", None) or []
                     if isinstance(p, dict)]
            if any(datas) and not dados.get("vencimento"):
                dados["vencimento"] = min(d for d in datas if d)
            continue
        lista = [p for p in dados.get("parcelas_lista") or [] if isinstance(p, dict)
                 and catalogo.data_ou(p.get("vencimento"), "") and catalogo.valor(p.get("valor")) > 0]
        total = catalogo.valor(dados.get("valor"))
        if lista and total > 0 and abs(sum(catalogo.valor(p["valor"]) for p in lista) - total) > 0.05:
            lista = []   # a soma não fecha com a nota: alguma parcela foi lida errada
        prazos = [] if lista else _prazos(dados.get("condicao_pagamento"))
        if prazos and total > 0:
            emissao = date.fromisoformat(catalogo.data_ou(dados.get("data_documento"), referencia))
            if not 0 <= (hoje - emissao).days <= 60:
                emissao = hoje   # emissão ilegível ou do ano errado
            lista = [{"vencimento": (emissao + timedelta(days=n)).isoformat(), "valor": v}
                     for n, v in zip(prazos, dividir_valor(total, len(prazos)))]
        dados.pop("parcelas_lista", None)
        if not lista:
            continue
        dados["vencimento"] = min(p["vencimento"] for p in lista)
        pago = catalogo.status_pagamento(dados.get("status_pagamento")) in ("Pago", "Reembolso")
        if pago or _FICA_NA_SEMANA.search(fala):
            continue
        if any(dia_de_pagar(date.fromisoformat(p["vencimento"])) > domingo for p in lista):
            dados["data"] = referencia
            dados["parcelas_lista"] = lista
            dados.pop("parcelas", None)


_FICA_NA_SEMANA = re.compile(r"\b(hoje|ontem|anteontem|essa semana|esta semana|nessa semana|nesta semana"
                             r"|semana atual)\b")


def _programar_boletos(acoes: list[dict], mensagem: str, referencia: str, arquivos: list[dict]) -> None:
    """Boleto que vence adiante entra na semana em que é pago (a sexta até o vencimento).

    Antes só ia para a semana certa quando o gestor ditava o dia. Boleto vencido,
    ou pago nesta semana, fica em hoje: pela sexta anterior ele cairia numa
    semana já fechada e sumiria da planilha.
    """
    despesas = _despesas(acoes)
    fala = catalogo.sem_acento(fala_do_usuario(mensagem))
    dita = _data_foi_dita(mensagem)
    hoje = date.fromisoformat(referencia[:10])
    domingo = hoje + timedelta(days=6 - hoje.weekday())
    lidos: dict[int, list[dict]] = {}

    for dados in despesas:
        indices = catalogo.indices(dados.get("anexos"), arquivos)
        if not indices and len(despesas) == 1 and len(arquivos) == 1:
            indices = [0]
        boletos = []
        for i in indices:
            if i not in lidos:
                lidos[i] = boleto.do_arquivo(arquivos[i], referencia)
            boletos += lidos[i]
        if not boletos:
            da_ia = boleto.ler_linha(dados.get("linha_digitavel"), referencia)
            boletos = [da_ia] if da_ia else []
        boletos = list({b["linha"]: b for b in boletos}.values())
        # Vários boletos num lançamento só (carnê): fica com a leitura da IA, que sabe o que foi pedido.
        if len(boletos) != 1 or not boletos[0]["vencimento"]:
            continue
        if catalogo.status_pagamento(dados.get("status_pagamento")) in ("Pago", "Reembolso"):
            continue
        lista = dados.get("parcelas_lista")
        lista = lista if isinstance(lista, list) else []
        if len(lista) > 1:
            continue

        impresso = boletos[0]["vencimento"]
        if padroes.sempre_na_semana(dados.get("fornecedor")) and not _PEDIU_PRAZO.search(fala) \
                and not _data_foi_dita(mensagem):
            dados["vencimento"] = impresso
            dados.pop("parcelas_lista", None)
            continue
        if catalogo.valor(dados.get("valor")) <= 0:
            dados["valor"] = boletos[0]["valor"]
        programado = [{"vencimento": impresso, "valor": dados.get("valor")}]
        sexta = dia_de_pagar(date.fromisoformat(impresso))
        dados["vencimento"] = impresso
        # Uma cobrança não abre aluguel recorrente: as renovações duplicariam os próximos boletos.
        dados.pop("locacao_frequencia", None)

        if dita and lista and isinstance(lista[0], dict):
            dia = catalogo.data_ou(lista[0].get("vencimento"), "")
            if dia > referencia and dia != impresso:
                # O gestor ditou o dia de pagar: vale o dele. Sendo a mesma sexta que o
                # app escolheria, guarda-se o vencimento impresso.
                if dia_de_pagar(date.fromisoformat(dia)) == sexta:
                    dados["parcelas_lista"] = programado
                continue
        dados.pop("parcelas_lista", None)
        data = catalogo.data_ou(dados.get("data"), referencia)
        if _FICA_NA_SEMANA.search(fala) or (dita and data < referencia):
            continue
        if sexta > domingo:
            dados["data"] = referencia
            dados["parcelas_lista"] = programado


# Dias da semana na fala (sem acento). "sengunda" é erro comum de digitação/áudio.
_DIAS_DA_SEMANA = (r"se?n?gunda", r"terca", r"quarta", r"quinta", r"sexta", r"sabado", r"domingo")
_DIA = r"(se?n?gunda|terca|quarta|quinta|sexta|sabado|domingo)(?:[\s-]*feira)?"
_INTERVALO_DE_DIAS = re.compile(rf"\b{_DIA}\s+(?:a|ate|até)\s+(?:a\s+|o\s+)?{_DIA}\b")
_EXCETO_DIA = re.compile(rf"\b(?:menos|exceto|tirando|fora|sem)\s+(?:a\s+|o\s+|na\s+|no\s+)?{_DIA}\b")
_ATE = re.compile(rf"\b(?:(?:de|desde)\s+(?:a\s+)?{_DIA}\s+)?ate\s+(?:o\s+dia\s+de\s+|o\s+dia\s+|a\s+)?"
                  rf"(hoje|agora|ontem|{_DIA[1:_DIA.index(')')]})\b(?:[\s-]*feira)?")
_DESDE = re.compile(rf"\bdesde\s+(?:a\s+)?{_DIA}\b")
_SEMANA_TODA = re.compile(r"\b(todos os dias|todo dia|todos dias|a semana toda|semana inteira|"
                          r"toda a semana|a semana inteira)\b")


def _indice_do_dia(nome: str) -> int:
    return next(i for i, padrao in enumerate(_DIAS_DA_SEMANA) if re.fullmatch(padrao, nome))


def dias_ditos(mensagem: str, referencia: str) -> list[str]:
    """Datas da semana de `referencia` que a fala pede ("de segunda a quinta",
    "todos os dias menos sexta", "segunda, terça e quarta").

    Só devolve algo quando a fala cita DOIS ou mais dias: um dia só a IA já resolve.
    "Todos os dias" é a semana de trabalho (segunda a sexta).
    """
    fala = catalogo.sem_acento(fala_do_usuario(mensagem)).lower()
    dias: set[int] = set()
    for inicio, fim in _INTERVALO_DE_DIAS.findall(fala):
        a, b = _indice_do_dia(inicio), _indice_do_dia(fim)
        dias |= set(range(a, b + 1)) if a <= b else set()
    sem_intervalo = _INTERVALO_DE_DIAS.sub(" ", fala)
    # "Até hoje ninguém faltou", "desde terça": da segunda (ou do dia dito) até o dia citado,
    # só dias úteis. Trecho tirado da fala para o dia dele não contar como dia solto.
    hoje_i = date.fromisoformat(referencia[:10]).weekday()
    limitado = False
    ate = _ATE.search(sem_intervalo)
    if ate:
        inicio = _indice_do_dia(ate.group(1)) if ate.group(1) else 0
        fim = {"hoje": hoje_i, "agora": hoje_i, "ontem": hoje_i - 1}.get(ate.group(2))
        fim = _indice_do_dia(ate.group(2)) if fim is None else fim
        dias |= set(range(inicio, min(fim, 4) + 1))
        limitado = True
        sem_intervalo = _ATE.sub(" ", sem_intervalo)
    elif _DESDE.search(sem_intervalo):
        dias |= set(range(_indice_do_dia(_DESDE.search(sem_intervalo).group(1)), min(hoje_i, 4) + 1))
        limitado = True
        sem_intervalo = _DESDE.sub(" ", sem_intervalo)
    elif re.search(r"\bontem e hoje\b|\bhoje e ontem\b", sem_intervalo) and hoje_i > 0:
        dias |= {hoje_i - 1, hoje_i}
    excetos = {_indice_do_dia(d) for d in _EXCETO_DIA.findall(sem_intervalo)}
    if _SEMANA_TODA.search(fala) and not limitado:
        dias |= set(range(0, 5))
    # Dias soltos ("segunda, terça e quarta"), fora os que são exceção.
    soltos = {_indice_do_dia(d) for d in re.findall(rf"\b{_DIA}\b", _EXCETO_DIA.sub(" ", sem_intervalo))}
    if len(soltos) >= 2 or dias:
        dias |= soltos
    dias -= excetos
    if len(dias) < 2:
        return []
    segunda = date.fromisoformat(referencia[:10]) - timedelta(days=date.fromisoformat(referencia[:10]).weekday())
    return [(segunda + timedelta(days=i)).isoformat() for i in sorted(dias)]


def _ponto_nos_dias_ditos(acoes: list[dict], mensagem: str, referencia: str) -> None:
    """"8 horas para o efetivo de segunda a quinta" = um ponto por dia, não só o primeiro.

    A IA às vezes devolve um ponto só (o da segunda, ou o de hoje). Quando a fala
    cita vários dias e os pontos devolvidos são o mesmo lançamento (mesmas
    pessoas e horas), o ponto é repetido em cada dia pedido.
    """
    dias = dias_ditos(mensagem, referencia)
    pontos = [a for a in acoes if a.get("tipo") == "ponto" and isinstance(a.get("dados"), dict)]
    if not dias or not pontos:
        return

    def sem_data(acao):
        return json.dumps({k: v for k, v in acao["dados"].items() if k != "data"}, sort_keys=True,
                          ensure_ascii=False, default=str), acao.get("obra_id")

    if len({sem_data(a) for a in pontos}) != 1:
        return    # pontos diferentes por dia: a IA já separou, não mexe
    modelo = pontos[0]
    posicao = acoes.index(modelo)
    for acao in pontos:
        acoes.remove(acao)
    for i, dia in enumerate(dias):
        acoes.insert(posicao + i, {**modelo, "dados": {**modelo["dados"], "data": dia}})


def status_dito(mensagem: str) -> tuple[str, str]:
    """Status que o usuário falou, onde quer que esteja na mensagem, e para quem é o reembolso.

    Só vale se a fala trouxer um status só: "uma em aberto e outra reembolso" fica com a IA.
    """
    fala = catalogo.sem_acento(fala_do_usuario(mensagem))
    achados = [status for status, padrao in _FALA_DE_STATUS if re.search(padrao, fala)]
    if len(achados) != 1:
        return "", ""
    quem = ""
    if achados[0] == "Reembolso":
        nome = _REEMBOLSO_PARA.search(fala_do_usuario(mensagem))
        if nome and catalogo.sem_acento(nome.group(1)) not in _NAO_SAO_NOMES:
            quem = nome.group(1).capitalize()
    return achados[0], quem


def _parcelamento_so_se_dito(acoes: list[dict], mensagem: str) -> None:
    """Toda nota a prazo imprime o quadro de duplicatas, mas a empresa lança a maioria no dia da compra.

    O lançamento vai para as semanas dos vencimentos quando o gestor fala em
    parcela ou quando o documento mostra que a nota é faturada; senão o primeiro
    vencimento fica guardado como "vencimento".
    """
    fala = catalogo.sem_acento(fala_do_usuario(mensagem))
    if _FALA_DE_PARCELA.search(fala) or _SEGUIR_IMPRESSA.search(fala):
        return
    for dados in _despesas(acoes):
        if dados.get("faturada") is True:
            continue
        lista = dados.pop("parcelas_lista", None)
        if isinstance(lista, list) and lista and isinstance(lista[0], dict) and not dados.get("vencimento"):
            dados["vencimento"] = lista[0].get("vencimento")


def _aplicar_status_dito(acoes: list[dict], mensagem: str) -> None:
    status, quem = status_dito(mensagem)
    if not status:
        return
    for dados in _despesas(acoes):
        dados["status_pagamento"] = status
        if quem:
            dados["reembolsar_para"] = quem


def _obra_do_lancamento(acao: dict, ids: set[int]) -> int:
    """Correção vale na obra do lançamento citado, seja qual for a obra da conversa."""
    if acao.get("tipo") == "transferir_funcionario":
        return _obra_de_quem_sai(acao.get("dados") or {}, ids)
    if acao.get("tipo") == "encerrar_cobranca":
        c = um("SELECT obra_id FROM cobrancas WHERE id = ?",
               (catalogo.inteiro((acao.get("dados") or {}).get("cobranca_id")),))
        return c["obra_id"] if c and c["obra_id"] in ids else 0
    if acao.get("tipo") not in ("editar_despesa", "pagar_despesa", "excluir_despesa", "repetir_cobranca"):
        return 0
    despesa_id = catalogo.inteiro((acao.get("dados") or {}).get("despesa_id"))
    linha = um("""SELECT s.obra_id FROM despesas x JOIN dias d ON d.id = x.dia_id
                    JOIN semanas s ON s.id = d.semana_id WHERE x.id = ?""", (despesa_id,))
    return linha["obra_id"] if linha and linha["obra_id"] in ids else 0


def _obra_de_quem_sai(dados: dict, ids: set[int]) -> int:
    """Transferência vale na obra onde a pessoa está, ainda que a fala só diga a nova."""
    destino = catalogo.inteiro(dados.get("mover_para_obra"))
    equipe = [dict(r) for r in todos(
        "SELECT id, obra_id, nome, ativo FROM funcionarios WHERE ativo = 1 AND COALESCE(avulso, 0) = 0")
        if r["obra_id"] in ids and r["obra_id"] != destino]
    fid = catalogo.inteiro(dados.get("funcionario_id"))
    if fid not in {f["id"] for f in equipe}:
        fid = catalogo.casar_funcionario(texto(dados.get("nome"), 120), equipe)
    return next((f["obra_id"] for f in equipe if f["id"] == fid), 0)


def _obra_da_equipe(acoes: list[dict], obras: list[dict], referencia: str) -> int:
    """Sem obra dita: a única obra em que todos os nomes do ponto trabalham."""
    nomes = {texto(r.get("nome"), 120) for a in acoes if a.get("tipo") == "ponto"
             for r in (a.get("dados") or {}).get("registros") or [] if isinstance(r, dict)} - {""}
    if not nomes:
        return 0
    candidatas = [o["id"] for o in obras
                  if all(catalogo.casar_funcionario(
                      n, catalogo.Contexto(o["id"], referencia, False, False).funcionarios) for n in nomes)]
    return candidatas[0] if len(candidatas) == 1 else 0


def _texto_pedido(mensagem: str, contexto: dict, arquivos: list[dict], historico: list[dict]) -> str:
    partes = ["CONTEXTO (JSON):", json.dumps(contexto, ensure_ascii=False, default=str, separators=(",", ":"))]
    if historico:
        partes.append("\nCONVERSA ANTERIOR (mais antiga primeiro):")
        for item in historico[-MAX_HISTORICO:]:
            quem = "Usuário" if item.get("papel") == "usuario" else "Assistente"
            partes.append(f"{quem}: {texto(item.get('texto'), 1500)}")
    if arquivos:
        partes.append("\nARQUIVOS (índice: nome e tipo):")
        for i, a in enumerate(arquivos):
            partes.append(f"[{i}] {a['nome']} ({a['mime']})"
                          + (" — já transcrito na mensagem" if eh_audio(a) else ""))
    partes.append("\nMENSAGEM DO USUÁRIO:")
    partes.append(mensagem or "(sem instrução — analise os anexos)")
    return "\n".join(partes)


# ---------------------------------------------------------------- pedido

def interpretar(obras: list[dict], mensagem: str, referencia: str | None, arquivos: list[dict],
                historico: list[dict], pode_financeiro: bool, pode_gestor: bool,
                obra_padrao: int = 0, prazo: float = ia_provedores.PRAZO_PADRAO) -> dict:
    """Transcreve os áudios, identifica a obra e devolve as ações já conferidas.

    `obras`: as obras que o usuário pode usar (na tela, só a obra aberta).
    """
    referencia = catalogo.data_ou(referencia, hoje_iso())
    ids = {o["id"] for o in obras}
    inicio = time.monotonic()
    transcricao = transcrever(arquivos, obras, prazo / 2)
    mensagem = "\n".join(p for p in (mensagem, transcricao) if p)
    foco = _obras_citadas(mensagem, obras) or ([obra_padrao] if obra_padrao in ids else [])
    contexto = montar_contexto(obras, referencia, pode_financeiro, pode_gestor, foco)
    sistema = PROMPT_BASE.replace("{catalogo}", catalogo.texto_para_prompt(pode_financeiro, pode_gestor)) \
                         .replace("{padroes}", padroes.texto_para_prompt() if pode_financeiro else "(não se aplica)") \
                         .replace("{exemplos}", padroes.exemplos_para_prompt(pode_financeiro)) \
                         .replace("{{", "{").replace("}}", "}")

    pedido = _texto_pedido(mensagem, contexto, arquivos, historico)
    liberar_leitura()
    bruto, fonte = ia_provedores.gerar(
        sistema, pedido,
        [a for a in arquivos if not eh_audio(a)], ESQUEMA,
        max_tokens=8192, pensar="baixo", prazo=prazo - (time.monotonic() - inicio))
    bruto = bruto if isinstance(bruto, dict) else {}
    acoes_brutas = [a for a in (bruto.get("acoes") or []) if isinstance(a, dict)]
    _aplicar_status_dito(acoes_brutas, mensagem)
    _parcelamento_so_se_dito(acoes_brutas, mensagem)
    _lancar_na_data_certa(acoes_brutas, mensagem, referencia)
    _programar_faturadas(acoes_brutas, mensagem, referencia)
    _programar_boletos(acoes_brutas, mensagem, referencia, arquivos)
    _ponto_nos_dias_ditos(acoes_brutas, mensagem, referencia)

    obra_id = catalogo.inteiro(bruto.get("obra_id"))
    if obra_id not in ids:
        obra_id = _obra_da_equipe(acoes_brutas, obras, referencia) or (
            foco[0] if len(foco) == 1 else (obras[0]["id"] if len(obras) == 1 else 0))

    por_obra: dict[int, list] = {}
    for acao in acoes_brutas:
        alvo = _obra_do_lancamento(acao, ids) or catalogo.inteiro(acao.get("obra_id"))
        por_obra.setdefault(alvo if alvo in ids else obra_id, []).append(acao)

    resultado = {"transcricao": transcricao, "obra_id": obra_id,
                 "resposta": texto(bruto.get("resposta"), 2000), "acoes": [], "avisos": [],
                 "fonte": fonte, "modelo": ia_provedores.modelo_usado(), "data_referencia": referencia,
                 "precisa_obra": False, "acoes_sem_obra": []}
    for alvo, lista in por_obra.items():
        if not alvo:
            resultado["precisa_obra"] = True
            resultado["acoes_sem_obra"] = lista
            continue
        ctx = catalogo.Contexto(alvo, referencia, pode_financeiro, pode_gestor, arquivos)
        conferidas, avisos = catalogo.normalizar(lista, ctx)
        for a in conferidas:
            a["obra_id"] = alvo
        resultado["acoes"] += conferidas
        resultado["avisos"] += avisos

    _garantir_anexos(resultado["acoes"], arquivos)
    usados = {i for a in resultado["acoes"] for i in a["dados"].get("anexos", [])}
    for i, a in enumerate(arquivos):
        if i not in usados and not a["mime"].startswith("audio/") and not resultado["precisa_obra"]:
            resultado["avisos"].append(f"O arquivo \"{a['nome']}\" não foi usado em nenhum lançamento.")
    if not resultado["resposta"]:
        resultado["resposta"] = "Preparei os lançamentos abaixo." if resultado["acoes"] else \
            "Não encontrei nada para lançar."
    return resultado


def _garantir_anexos(acoes: list[dict], arquivos: list[dict]) -> None:
    """Todo arquivo enviado fica salvo junto com o lançamento.

    O modelo às vezes lê a nota mas esquece de citá-la em "anexos". Arquivo que
    não virou foto da obra nem comprovante de outra ação vai para a despesa
    (ou baixa/correção) que ficou sem anexo: uma só leva todos; várias, um para
    cada na ordem, quando a conta fecha; senão, todos na primeira.
    """
    usados = {i for a in acoes for i in a["dados"].get("anexos", [])}
    soltos = [i for i, a in enumerate(arquivos) if i not in usados and not a["mime"].startswith("audio/")]
    candidatas = [a for a in acoes if a["tipo"] in ("despesa", "editar_despesa", "pagar_despesa")
                  and not a["dados"].get("anexos")]
    if not soltos or not candidatas:
        return
    if len(candidatas) > 1 and len(soltos) == len(candidatas):
        partes = [[i] for i in soltos]
    else:
        partes = [soltos] + [[] for _ in candidatas[1:]]
    for acao, indices in zip(candidatas, partes):
        if indices:
            acao["dados"]["anexos"] = indices
            acao["descricao"] = catalogo.descrever(acao)


def reconferir(obra_id: int, acoes: list[dict], arquivos: list[dict], pode_financeiro: bool,
               pode_gestor: bool, referencia: str | None = None) -> tuple[list[dict], list[str]]:
    """Confere de novo ações que voltaram da tela (o navegador não é confiável)."""
    ctx = catalogo.Contexto(obra_id, catalogo.data_ou(referencia, hoje_iso()), pode_financeiro,
                            pode_gestor, arquivos)
    brutas = [{"tipo": a.get("tipo"), "dados": a.get("dados") or {}} for a in acoes if isinstance(a, dict)]
    conferidas, avisos = catalogo.normalizar(brutas, ctx)
    # Leva junto o "lançar mesmo assim" de cada uma, na mesma ordem.
    marcas = [a for a in acoes if isinstance(a, dict) and a.get("tipo") in catalogo.CATALOGO]
    for conferida, original in zip(conferidas, marcas):
        conferida["confirmar_duplicidade"] = bool(original.get("confirmar_duplicidade"))
        conferida["obra_id"] = obra_id
    return conferidas, avisos


# ---------------------------------------------------------------- RDO do dia

def redigir_rdo(dia_id: int, pode_financeiro: bool) -> dict:
    """Redige o RDO a partir do que já foi lançado no dia (ponto, fotos, caixa, notas)."""
    dia = um("""SELECT d.id, d.data, d.dia_semana, d.producao_dia, d.clima, d.observacoes,
                       o.nome AS obra
                  FROM dias d JOIN semanas s ON s.id = d.semana_id JOIN obras o ON o.id = s.obra_id
                 WHERE d.id = ?""", (dia_id,))
    ponto = [dict(r) for r in todos(
        """SELECT f.nome, f.funcao, h.horas_trabalhadas AS horas, h.justificativa
             FROM horas h JOIN funcionarios f ON f.id = h.funcionario_id
            WHERE h.dia_id = ? ORDER BY f.nome""", (dia_id,))]
    fotos = [dict(r) for r in todos(
        "SELECT legenda, ambiente FROM fotos WHERE dia_id = ? ORDER BY id", (dia_id,))]
    dados = {
        "obra": dia["obra"], "data": dia["data"], "dia_da_semana": dia["dia_semana"],
        "jornada_horas": config.JORNADA_HORAS,
        "texto_atual": {"producao_dia": dia["producao_dia"] or "", "clima": dia["clima"] or "",
                        "observacoes": dia["observacoes"] or ""},
        "ponto": ponto,
        "fotos": fotos,
    }
    if pode_financeiro:
        dados["compras_e_servicos"] = [dict(r) for r in todos(
            """SELECT categoria, descricao, fornecedor FROM despesas
                WHERE dia_id = ? ORDER BY id""", (dia_id,))]

    bruto, fonte = ia_provedores.gerar(
        PROMPT_RDO, "DADOS DO DIA (JSON):\n" + json.dumps(dados, ensure_ascii=False, default=str),
        [], ESQUEMA_RDO, max_tokens=4096, pensar="minimo")
    bruto = bruto if isinstance(bruto, dict) else {}
    return {"producao_dia": texto(bruto.get("producao_dia"), 6000),
            "clima": texto(bruto.get("clima"), 200),
            "observacoes": texto(bruto.get("observacoes"), 4000),
            "fonte": fonte}
