# Obra ERP · v2

Diário de obra, folha de mão de obra e caixa de canteiro — lançados do celular,
no pé da obra, e fechados em um PDF que pode ir direto para o cliente.

Feito para quem toca obra de verdade: cada tela responde a uma pergunta que
aparece no dia a dia ("o que foi feito hoje?", "quanto custou a semana?",
"o que vence essa semana?", "estamos dentro do orçamento?").

---

## O que mudou da v1 para a v2

| | v1 | v2 |
|---|---|---|
| Acesso | aberto, sem login | login com papéis (gestor, mestre, cliente) e escopo por obra |
| Anexos | públicos em `/static/uploads` | rota autenticada `/arquivos/<nome>`, fora do diretório público |
| Semanas | só numeradas | datas reais de calendário, dia útil, "hoje" e pendências |
| Relatório | `.txt` | PDF profissional com KPIs, prancha fotográfica e campo de assinatura (`.txt` e `.csv` continuam) |
| Fotos | não existia | fotos no RDO, por ambiente, com galeria da obra |
| Financeiro | despesa simples | vencimento, baixa de pagamento, edição, categoria, fornecedor, vínculo com etapa |
| Planejamento | não existia | EAP com pesos, avanço físico ponderado, previsto x realizado |
| Banco | SQLite, arquivo local | SQLite **ou** Postgres/Supabase, mesmo código |
| Anexos | pasta pública | disco ou Supabase Storage (bucket privado, URL assinada) |
| Upload | só câmera, sem compressão | câmera, galeria ou arquivos; comprimido no aparelho e no servidor |
| Visual | tema escuro "concreto + latão" | design claro contemporâneo, tema escuro automático, PWA |
| Metas | texto livre + "atingida" sim/não | meta nomeada, % diário, arrasto automático entre semanas |
| Terceirizados | não existia | contrato global, medição semanal por %, saldo devedor e controle de caixa |
| Profissionais | só salário por hora | contrato **fixo** (por hora) ou **por produção** (por tarefa medida) |
| Locação | despesa avulsa | aluguel recorrente com cronograma de renovações e alerta de vencimento |
| Nota fiscal | não registrada | número da NF na despesa, com trava contra lançamento em duplicidade |
| Relatório de pagamento | não existia | exportação .xlsx no modelo do escritório, uma aba por obra |
| Nota parcelada | não existia | valor total dividido em N contas a pagar, com vencimentos próprios |
| Frete | despesa avulsa | várias viagens do mesmo transportador, cada uma no seu dia |
| Arquivos | espalhados por lançamento | central da semana com filtro e download em .zip |
| Baixa de pagamento | uma conta por vez | baixa em lote da semana e quitação da folha ao fechar |
| Código | 1 arquivo de 659 linhas | pacote modular, blueprints, camada de serviços, migrações versionadas |
| Frontend | `innerHTML` sem escape (XSS) | ES modules, escape em todo dado do usuário, CSRF, PWA offline |

O banco da v1 é migrado automaticamente na primeira subida — nada é apagado.

---

## Rodando localmente

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py            # http://localhost:5000
```

Primeiro acesso: **gestor@obra.local** / **obra123** (troque em Ajustes).

---

## Papéis de acesso

| Papel | Enxerga | Pode lançar | Financeiro |
|---|---|---|---|
| **Gestor / Engenheiro** | tudo, todas as obras | tudo | sim |
| **Mestre de obra** | só as obras vinculadas | RDO, fotos, ponto, medições | **não** (bloqueado no backend, não só na tela) |
| **Cliente / Síndico** | só as obras vinculadas | nada (somente leitura) | vê o fechamento |

O bloqueio do mestre é feito no servidor: os endpoints financeiros devolvem
403 e o fechamento vem sem valores. Esconder botão no frontend não é segurança.

---

## Arquitetura

```
app.py                  → só instancia o create_app()
obra/
  config.py             → caminhos, limites, papéis, variáveis de ambiente
  db.py                 → schema, migrações versionadas, auditoria
  sql.py                → dialeto SQL (traduz SQLite -> Postgres) e schema PG
  storage.py            → anexos: disco local ou Supabase Storage
  utils.py              → validação de entrada, moeda pt-BR, anexos (Pillow)
  security.py           → sessão, CSRF, papéis, escopo por obra, rate-limit
  blueprints/           → paginas, auth, obras, diario, equipe, financeiro, relatorios
  services/
    fechamento.py       → consolidação da semana e dashboard
    relatorio.py        → exportação .txt e .csv
    pdf.py              → relatório em PDF (reportlab)
    ia.py               → resumo executivo (regras + Claude opcional)
static/
  css/style.css         → design system (tokens, componentes)
  js/                   → ES modules: api, estado, navegacao, util, views/
  js/sw.js              → service worker (casca offline)
templates/              → index.html (shell) e login.html
ferramentas/
  migrar_para_supabase.py → copia o SQLite para o Postgres + sobe anexos
```

**Por que assim:** cada blueprint mexe em um assunto, os cálculos de dinheiro
ficam num único lugar (`services/fechamento.py`) e as três exportações
(PDF, TXT, CSV) leem exatamente os mesmos números — não há chance de o PDF
divergir da tela.

---

---

## Banco de dados: SQLite ou Supabase

O app fala os dois com o **mesmo código**. Sem `DATABASE_URL`, roda em SQLite
(desenvolvimento). Com `DATABASE_URL` apontando para o Postgres do Supabase,
passa a falar Postgres — nenhum blueprint muda.

Isso funciona porque o SQL do app é escrito no dialeto do SQLite e traduzido na
saída por `obra/sql.py`: `?` vira `%s`, `INSERT OR IGNORE` vira
`ON CONFLICT DO NOTHING`, `COLLATE NOCASE` vira `LOWER()`, e todo `INSERT`
ganha `RETURNING id` para o `cursor.lastrowid` continuar funcionando. Um único
ponto para auditar, em vez de 40 consultas reescritas.

Passo a passo completo de conexão: **[SUPABASE.md](SUPABASE.md)**.
Deploy travado ou tela sem atualizar: **[DEPLOY.md](DEPLOY.md)**.

---

## Identidade visual

Design system em `static/css/style.css`, todo em variáveis CSS — trocar a
paleta inteira é editar o bloco `:root`.

- Primária índigo→violeta em gradiente, fundo névoa, cartões brancos.
- Raios de 14–24px, sombras difusas, pílulas de status coloridas.
- Navegação inferior com ícones e botão flutuante de lançamento rápido.
- Números em `tabular-nums` (alinham em coluna, como devem alinhar valores).
- **Tema escuro automático** por `prefers-color-scheme`, e
  `prefers-reduced-motion` respeitado.

---

## Metas da semana

Meta não é texto solto: tem nome, percentual e história.

- **Percentual atualizado por dia.** Cada marcação vira um registro, então o
  relatório mostra *onde a meta travou* (Seg 10% → Ter 30% → Qua 30% → Qui 60%),
  não só onde ela chegou. Uma marcação por dia; corrigir o valor de um dia
  anterior não altera o percentual atual.
- **Meta incompleta arrasta sozinha para a semana seguinte**, começando de
  onde parou e com um contador. A partir da 3ª semana arrastando, ela vira
  pendência no painel — que é o alerta mais honesto que um app de obra dá.
- **Meta concluída não arrasta e sai da lista ativa**, mas continua registrada
  na semana em que foi concluída. O pedido original era apagá-la; apagar
  destruiria a prova do que foi entregue, e o relatório do cliente depende
  disso.
- Tudo isso aparece no PDF e no `.txt`, com barra de progresso, avanço em
  pontos percentuais e o histórico diário.

Quem já usava a meta em texto não perde nada: na primeira subida da 2.2, cada
texto vira uma meta nomeada (100% se estava marcada como atingida).

---

## Compressão de anexos

Foto de celular chega com 4–12 MB; nota fiscal escaneada em PDF passa fácil
dos 5 MB. Num mês de obra são centenas de anexos, e isso pesa em armazenamento
e — pior — no tempo de upload do 4G do canteiro. A compressão acontece em duas
camadas:

- **No aparelho, antes de subir** (`static/js/compressao.js`): a imagem é
  redimensionada para 1600px e reencodada em JPEG via canvas. Uma foto de
  12 MB sai como ~300 KB, então o que levaria um minuto de upload leva
  segundos. Bônus no iPhone: fotos em HEIC saem daqui já convertidas em JPEG.
- **No servidor, antes de gravar** (`obra/services/compressao.py`): imagens
  passam pelo Pillow; PDFs vão para o pikepdf, que **reamostra as imagens
  embutidas** — é onde mora o peso de uma nota escaneada — com o pypdf como
  reserva em Python puro.

Três garantias que valem nas duas camadas:

1. **Nunca aumentar** — se o resultado ficar maior que o original (comum em
   arquivo já otimizado), fica o original.
2. **Nunca perder o anexo** — qualquer falha grava os bytes como vieram. Um
   PDF exótico não pode fazer o gestor perder o comprovante.
3. **Nunca ilegível** — os limites são conservadores; texto de nota fiscal
   continua legível.

O upload aceita **câmera, galeria ou gerenciador de arquivos** (dois inputs,
um com `capture` e outro sem — é o que faz o Android abrir a câmera direto num
caso e a galeria no outro). A tela mostra quanto foi economizado.

---

## Relatório de Lançamento por Categoria (.xlsx)

No fechamento da semana há o botão **Relatório de pagamento (.xlsx)**, que
gera a planilha no mesmo formato que já circula entre a obra e o financeiro —
mesmo cabeçalho, mesmas colunas (Fornecedor · Descrição · Data · DOC · Valor),
mesmas cores e a coluna de destino (A PAGAR / REEMBOLSO / PAGO) ao lado.

Seções, na ordem:

1. **NOTAS PARA REEMBOLSO SEMANA NN** — despesas marcadas como Reembolso.
2. **NOTAS ABERTAS A PAGAR `<SIGLA>`** — despesas Em Aberto.
3. **MEDIÇÃO `<SERVIÇO>`** — uma seção por terceirizado medido, com o
   percentual da semana, o acumulado e o número da medição (MED01, MED02...).
4. **FOLHA DE MÃO DE OBRA** — o ponto da semana, por profissional.
5. **NOTAS JÁ PAGAS** — listadas para conferência, **fora** do total.
6. **TOTAL A PAGAR - SEMANA NN**.

Dois cuidados que valem registrar:

- **Os totais são fórmulas**, não números calculados no Python. Quem receber a
  planilha corrige um valor e o total acerta sozinho, que é como o financeiro
  trabalha de fato.
- **Medição não é contada duas vezes.** Como a medição aprovada gera uma
  despesa real no caixa, ela apareceria tanto nas notas quanto na sua própria
  seção; a exportação identifica essas despesas-espelho e as remove das notas.

O arquivo sai com **uma aba por obra** que tenha semana no mesmo período (é
assim que ele circula hoje, I19 e O13 no mesmo arquivo). Com `?obra=1`, apenas
a obra atual. A **sigla** usada no nome da aba e no título da seção vem do
cadastro da obra (Ajustes → Dados da obra); se estiver vazia, é deduzida do
nome — o identificador do lote, como "LOTE K42" → "K42".

---

## Baixa de pagamento

**Todas as contas da semana de uma vez.** No Caixa, abaixo do total da semana,
o botão *Marcar todos os custos da semana como pagos* dá baixa nas contas em
aberto de uma vez — na sexta, quando o gestor paga o bolo de notas do
fornecedor, baixar quinze lançamentos um a um é o tipo de trabalho que faz o
app ser abandonado.

Reembolsos ficam **de fora por padrão**: são acerto com quem adiantou o
dinheiro, não pagamento a fornecedor, e costumam sair em outro momento. A
confirmação avisa quanto seria incluído e deixa você escolher.

**A folha é quitada ao fechar a semana.** A mão de obra é acertada no fim da
semana, então fechar a semana zera o valor pendente da folha — e só dela. As
contas de material, locação e medições de terceiros continuam em aberto até
serem pagas.

> **As horas nunca são apagadas.** "Zerar a folha" apagando o ponto mudaria o
> custo daquela semana retroativamente, e o relatório já entregue ao cliente
> passaria a mostrar outro número. O que se registra é o **pagamento**: o
> custo da semana, a folha por profissional e as horas continuam intactos no
> histórico; o que zera é o quanto ainda falta pagar.

Dá para quitar a folha sem fechar a semana, e para reabrir se o pagamento foi
marcado por engano. O KPI do fechamento passou a mostrar *falta pagar* em vez
do valor da folha — é a pergunta que o gestor faz na sexta.

---

## Frete com várias viagens

Frete de obra raramente é uma viagem só: o mesmo motoboy busca material na
segunda, na quarta e na sexta, e cobra o conjunto no fim da semana. Lançar isso
como uma despesa única de "3 fretes" esconde justamente o que interessa — em
que dias houve movimento.

Na categoria **Frete**, informe quem fez, quantas viagens e a data de cada uma.
O valor digitado é o de **cada** viagem (transportador cobra por corrida, não
um total a dividir — o oposto da nota parcelada). Cada viagem vira um
lançamento no dia em que aconteceu, então o custo cai na semana certa e o
relatório mostra "frete 2/3" com a data.

O fechamento, o PDF e o `.txt` trazem um resumo consolidado por transportador:
quantas viagens e quanto no total.

---

## Arquivos da semana

No fechamento há a seção **Arquivos da semana**, que reúne tudo que foi anexado
— notas fiscais e comprovantes do Caixa, fotos do Diário — com filtro por tipo
e link direto. Antes era preciso abrir lançamento por lançamento para achar uma
nota.

O botão **Baixar todos em um .zip** empacota o conjunto com **nomes legíveis
fora do app**: `notas-fiscais/2026-08-18_NF-2353_INOVACIVIL_R$120,00.pdf` em vez
do UUID interno. O pacote separa notas e fotos em pastas e inclui um
`_conteudo.txt` com o resumo — e, se algum arquivo não abrir, ele é listado ali
em vez de o pacote falhar.

O mestre de obra vê as fotos, não as notas fiscais.

---

## Nota parcelada

O fornecedor entrega R$ 5.000 em material e a nota é paga em 5x. No caixa isso
não é um lançamento de R$ 5.000: são **cinco contas a pagar de R$ 1.000**, cada
uma com sua data. Só assim o fluxo de caixa fica correto e cada vencimento cai
no alerta da semana certa.

Ao lançar a despesa, marque **Nota parcelada** e informe o número de parcelas e
o intervalo (mensal, quinzenal, semanal ou um intervalo livre em dias). O valor
digitado é sempre o **total da nota** — ele é dividido, e a despesa original
vira a primeira parcela.

- A divisão é exata em centavos: 100,00 em 3x sai 33,33 / 33,33 / **33,34**.
  Somar de volta dá exatamente o total.
- O mensal preserva o dia (comprou dia 10, paga todo dia 10), com meses curtos
  caindo no último dia válido.
- As parcelas ficam ligadas entre si, aparecem como "2/5" na lista e podem ser
  canceladas de uma vez — **parcela já paga não é apagada**, porque o dinheiro
  saiu e apagar faria o caixa mentir.
- A trava de nota duplicada não acusa as parcelas da mesma nota.

**Por que aqui as parcelas nascem todas de uma vez, diferente da locação.**
Aluguel pode ser interrompido (devolveu o andaime, para de pagar), então lá a
renovação só vira conta quando o gestor confirma. Nota parcelada já é dívida
assumida na assinatura: o material foi entregue e o valor não muda.

---

## Número da nota fiscal

Toda despesa aceita o **número da nota**, opcional. Ele aparece na lista de
lançamentos, no PDF (junto do fornecedor, que é como a contabilidade confere) e
como coluna própria no CSV.

O ganho real não é guardar o número: é o que ele permite barrar. Ao lançar uma
nota já registrada **para o mesmo fornecedor na mesma obra**, o app recusa e
mostra o lançamento existente — descrição, data e valor. Pagamento em
duplicidade é o erro financeiro mais caro e mais silencioso de uma obra, porque
os dois lançamentos parecem legítimos isoladamente.

A trava não decide sozinha: nota parcelada em duas competências é caso válido,
então o gestor confirma e segue. A comparação ignora espaços e maiúsculas, e o
mesmo número emitido por fornecedores diferentes (ou em outra obra) não trava
nada.

---

## Locação de equipamentos

Andaime, betoneira, container: custos que se repetem em intervalo fixo e que
somem do radar justamente por serem repetitivos. O gestor lembra do primeiro
pagamento e esquece do quinto — até o locador ligar cobrando.

Ao lançar uma despesa da categoria **Locação**, aparecem os campos de
**frequência** (diária, semanal, quinzenal ou mensal) e **duração**. O app monta
o cronograma inteiro e passa a avisar cada renovação, no painel e no Caixa.

- O mensal anda de mês em mês preservando o dia (alugou dia 5, paga todo dia 5),
  com fevereiro e meses curtos caindo no último dia válido.
- **Encerrar** (equipamento devolvido) cancela as renovações futuras; o que já
  foi pago continua no histórico. **Prorrogar** estende o cronograma.
- O painel mostra o **custo mensal comprometido**, normalizando as frequências
  para responder "quanto os equipamentos me custam por mês".

**Por que as renovações não viram despesa sozinhas.** Seria fácil gerar as
contas automaticamente na data. Mas equipamento se devolve antes do fim do
contrato o tempo todo, e conta lançada sozinha vira custo fantasma no relatório
que vai para o cliente — o pior tipo de erro, porque ninguém desconfia de um
número que apareceu direitinho. Então o app cobra e o gestor confirma em um
toque.

---

## Fixo x por produção

O cadastro do profissional tem **Tipo de contrato**:

- **Fixo** — pago por hora. O valor/hora é obrigatório: sem ele a folha da
  semana sairia zerada sem ninguém perceber.
- **Por produção** — pago por tarefa. O campo de valor/hora some da tela e é
  forçado a zero no servidor. O ponto dele pode continuar sendo lançado para
  controle de presença, mas **não gera custo de folha**.

Essa última regra é o ponto crítico: sem ela, o avulso seria cobrado duas
vezes — uma no ponto e outra na medição da tarefa.

A tarefa do avulso usa **o mesmo motor de medição dos terceirizados**: valor
combinado, percentual medido por semana, saldo e o toggle de caixa. Muda só
quem recebe, então não há uma segunda regra de negócio para manter. O contrato
carrega o CPF e a chave PIX do cadastro, e o app recusa criar tarefa por
empreitada para quem está como fixo (com a mensagem dizendo o que fazer).

No fechamento, a mão de obra por produção aparece **separada da folha por
hora** (indicador `mao_obra_producao`), respondendo "quanto gastei com gente
nesta semana?" sem misturar as duas naturezas de contratação.

---

## Terceirizados (contrato global + medição semanal)

Empreiteiro se contrata por valor fechado e se paga por medição: contrata-se a
pintura por R$ 50.000, mede-se 20% na semana, paga-se R$ 10.000.

- **Contrato global** com empresa, serviço, valor total e etapa do cronograma.
- **Uma medição por semana** — relançar corrige em vez de somar, que é o que
  o gestor espera quando digitou errado.
- O sistema calcula o valor da fatia, o **acumulado** e o **saldo a medir**,
  e barra qualquer lançamento que ultrapasse 100%, dizendo quanto ainda resta.
- **Toggle "entra no pagamento da semana"**: ligado, a medição vira uma
  despesa de verdade no Caixa (categoria Serviço, contrato como fornecedor),
  com vencimento e baixa de pagamento; desligado, registra só o avanço
  contratual. Desligar depois remove a despesa; religar recria.
- Contrato 100% medido se encerra sozinho; remover a medição que o fechou
  reabre o contrato.
- **Aditivo**: alterar o valor global recalcula as medições já lançadas. Não é
  permitido reduzir o contrato abaixo do que já foi medido.

**Por que a medição vira despesa em vez de ter caixa próprio:** assim ela
herda contas a pagar, vencimento, baixa, CSV, PDF e custo da semana — tudo que
já existe e já é testado. Um segundo sistema financeiro paralelo significaria
duas verdades sobre o mesmo dinheiro, e é por aí que relatório começa a
divergir de caixa. O custo entra **uma única vez** no fechamento.

O módulo inteiro é restrito ao gestor, pela mesma regra da aba Caixa: o mestre
não vê valor de contrato nem no fechamento.

---

## Regras de negócio que importam

- **Snapshot do valor da hora.** Ao lançar o ponto, o valor/hora vigente é
  gravado junto. Reajustar o salário de um profissional **não** reescreve as
  semanas já fechadas. (Regra herdada da v1 e preservada de propósito.)
- **Dia útil sem lançamento vira pendência**, aparece no painel, no fechamento
  e como marca vermelha na aba Diário.
- **Avanço físico** = média dos percentuais das etapas ponderada pelos pesos.
- **Farol da semana**: verde até 85% do previsto, amarelo até 100%, vermelho
  acima — no card do painel e no PDF.
- **Excluir obra não apaga nada**: arquiva. O histórico é o ativo do app.

---

## Resumo executivo por IA (opcional)

Sem chave de API, o resumo é gerado por regras a partir dos próprios números
(sempre funciona, inclusive offline no servidor). Com `ANTHROPIC_API_KEY`
definida, o texto passa a ser redigido pelo Claude, com fallback silencioso
para as regras se a chamada falhar — o fechamento nunca quebra por causa disso.

---

## Deploy no Render

1. **New → Web Service**, apontando para o repositório.
2. Build: `pip install -r requirements.txt` · Start: já vem no `Procfile`.
3. **Persistência** — escolha uma das duas:
   - *Supabase* (recomendado): defina `DATABASE_URL` e as variáveis de Storage.
     Nada fica no disco do Render, então o deploy pode ser efêmero.
   - *Disco*: adicione um disco em `/var/data` e aponte `DATA_DIR` para ele.
     Sem disco **e** sem Supabase, o banco e as fotos somem a cada deploy.
4. Variáveis de ambiente:

| Variável | Para quê |
|---|---|
| `DATA_DIR` | `/var/data` — banco e anexos no disco persistente (modo SQLite) |
| `DATABASE_URL` | connection string do Supabase; sem ela, usa SQLite |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` / `SUPABASE_BUCKET` | anexos no Supabase Storage |
| `SECRET_KEY` | assina a sessão (se ausente, é gerada e salva em `DATA_DIR`) |
| `ADMIN_EMAIL` / `ADMIN_SENHA` | credencial do primeiro gestor |
| `FORCE_HTTPS` | `1` em produção (cookie `Secure`) |
| `ANTHROPIC_API_KEY` | opcional, liga o resumo por IA |
| `MAX_UPLOAD_MB` | opcional, padrão 25 |

Migrando da v1: copie o `obra_erp.db` antigo para `DATA_DIR` e a pasta
`static/uploads` para junto — a v2 detecta e migra na primeira subida,
inclusive os anexos antigos.

---

## Verificação

Duas suítes foram executadas contra este código:

- **Ponta a ponta** (45+ asserções): login, CSRF, papéis, escopo por obra,
  snapshot do valor-hora, contas vencidas, avanço físico, PDF/TXT/CSV,
  anexo protegido contra acesso anônimo.
- **Migração v1 → v2**: banco antigo com 3 semanas, ponto, anexo legado e
  despesas — todos os dados preservados, datas retroagidas e encadeadas de 7
  em 7 dias, anexo legado ainda abrindo, migração idempotente.
- **Modo Postgres**: a mesma suíte rodando com o app configurado para
  Postgres, validando a tradução de dialeto, `RETURNING id`, o upsert do
  ponto e a geração do PDF. Rodou contra um driver que imita a API do psycopg
  sobre SQLite — **não substitui um teste contra o Postgres real**, que só
  acontece no primeiro deploy.
- **Ferramenta de migração**: cópia completa de um banco v1 para o "Postgres",
  com ids preservados e sequências reposicionadas.
- **Ciclo de vida das metas** (23 asserções): criação, progresso diário com
  correção, conclusão automática em 100%, arrasto só do que ficou pendente,
  contador de arrasto, meta crítica na 3ª semana, presença no PDF e no TXT, e
  permissões por papel — em SQLite e em Postgres.
- **Conversão de metas em texto** de um banco 2.1 para o novo formato.
- **Compressão** (21 asserções): ganho real em foto de celular e PDF
  escaneado, proporção e páginas preservadas, arquivo já otimizado que não
  cresce, arquivo corrompido devolvido byte a byte, e o app rodando **sem
  pikepdf/pypdf instalados** (simulando produção sem as bibliotecas).
- **Baixa de pagamento** (30 asserções): baixa em lote sem tocar em
  reembolsos, idempotência, quitação da folha ao fechar a semana, **horas e
  custo histórico preservados**, locações e contas novas não afetadas, semana
  seguinte começando limpa, reabertura e restrição por papel.
- **Frete e arquivos** (33 asserções): viagens lançadas cada uma no seu dia,
  valor por viagem, resumo por transportador, validações; e a central de
  arquivos com índice, filtro, ZIP com nomes legíveis, arquivo ausente sem
  derrubar o pacote, semana vazia e restrição por papel.
- **Variáveis de ambiente** (17 asserções): URL colada com colchetes, aspas,
  `<>`, espaços, quebra de linha, sem `https://` ou com `/storage/v1` junto —
  todas normalizadas; chave e bucket idem; `DATABASE_URL` também; e os avisos
  de configuração meia-boca.
- **Anexo indisponível** (8 asserções): as três causas (disco temporário,
  chave inválida, bucket errado) resultando em mensagem específica na tela e
  no cabeçalho da resposta, mais a conferência de anexos no diagnóstico.
- **Entrega de anexos** (20 asserções): foto e PDF servidos pelo app tanto do
  disco quanto de um Supabase Storage simulado, MIME correto, bytes íntegros,
  ausência de redirecionamento para outro domínio (que a CSP bloquearia),
  404 limpo para arquivo ausente e bloqueio de anônimo.
- **Nota parcelada** (24 asserções): divisão exata em centavos, os quatro
  intervalos, virada de mês, soma das parcelas igual ao total em contas a
  pagar, convivência com a trava de duplicidade, limites e cancelamento
  preservando a parcela paga.
- **Planilha de pagamento** (29 asserções): título, cabeçalho, larguras de
  coluna, formato de moeda, fonte e mesclagens comparados **contra o arquivo
  modelo real**; seções e siglas corretas; totais como fórmula, recalculados
  pelo LibreOffice sem erro; ausência de dupla contagem das medições; aba por
  obra; semana vazia; sigla deduzida; e restrição por papel.
- **Postgres desatualizado** (16 asserções): um banco criado numa versão
  anterior, sem as colunas novas, recebendo a versão atual — reproduz o erro
  `column does not exist` que quebrou a produção e confirma que a migração
  adiciona as colunas, preserva os dados antigos e é idempotente.
- **Nota fiscal** (20 asserções): gravação e retorno em todas as telas,
  duplicidade barrada por nota + fornecedor, confirmação explícita liberando o
  lançamento, isolamento entre obras e fornecedores, edição sem falso positivo
  contra si mesma, e presença no CSV (com colunas alinhadas), TXT e PDF.
- **Locação** (26 asserções): geração do cronograma nas quatro frequências,
  mensal preservando o dia (incluindo 31/01 → 28/02), alerta de vencidas e
  próximas, lançamento em um toque sem duplicar, encerramento cancelando
  futuras, prorrogação, custo mensal normalizado e restrição por papel —
  em SQLite e em Postgres.
- **Fixo x produção** (32 asserções): obrigatoriedade do valor/hora no fixo,
  valor zerado à força no avulso, tarefa por empreitada recusada para quem é
  fixo, ausência de cobrança dupla entre ponto e medição, separação da folha
  no fechamento, troca de tipo nos dois sentidos e histórico de ponto
  preservado.
- **Terceirizados** (40 asserções): cálculo da medição, saldo devedor, limite
  de 100%, correção da medição da semana, toggle de caixa nos dois sentidos,
  aditivo de contrato, exclusões em cascata, ausência de dupla contagem no
  custo da semana e restrição por papel.
- **Portabilidade do SQL** (`ferramentas/verificar_sql.py`): varre as 104
  consultas do pacote e recusa construções que o SQLite aceita e o Postgres
  não — alias de agregação em `HAVING`/`WHERE`, coluna fora do `GROUP BY`,
  `INSERT OR REPLACE`. Rode antes de cada deploy:

  ```bash
  python ferramentas/verificar_sql.py
  ```

  Ele existe porque o teste com driver simulado sobre SQLite tem um ponto
  cego exatamente nessa classe de erro — foi assim que um `HAVING horas > 0`
  chegou a produção na 2.2.0.

---

## O que ficou de fora (e por quê)

- **Cotação de fornecedores (2.5)** — depende de cadastro de fornecedor com
  histórico de preço; vale a pena só depois que houver volume de compras
  no app para comparar.
- **Geolocalização no ponto (3.4)** — tecnicamente simples, mas registrar
  localização de trabalhador tem implicação trabalhista e de LGPD; melhor
  decidir a política antes de coletar o dado.

Próximos passos naturais: cronograma em curva S com linha de base, medição
com boletim assinado pelo cliente e notificação de vencimento por WhatsApp.
