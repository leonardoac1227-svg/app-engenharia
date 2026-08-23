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
```

**Por que assim:** cada blueprint mexe em um assunto, os cálculos de dinheiro
ficam num único lugar (`services/fechamento.py`) e as três exportações
(PDF, TXT, CSV) leem exatamente os mesmos números — não há chance de o PDF
divergir da tela.

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
3. **Disco persistente (obrigatório):** adicione um disco em `/var/data`.
   Sem ele, o SQLite e as fotos somem a cada deploy.
4. Variáveis de ambiente:

| Variável | Para quê |
|---|---|
| `DATA_DIR` | `/var/data` — banco e anexos no disco persistente |
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
  despesas — todos os dados preservados, datas retroagidas, anexo legado
  ainda abrindo, migração idempotente.

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
