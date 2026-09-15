# Conectando o Obra ERP ao Supabase

Este guia cobre os dois serviços do Supabase que o app usa:

- **Postgres** — substitui o arquivo SQLite (dados da obra).
- **Storage** — substitui a pasta de uploads (fotos e notas fiscais).

Os dois são opcionais e independentes: dá para usar só o banco, só o storage,
ou os dois. Sem nenhuma variável configurada, o app continua rodando em SQLite
com anexos no disco, exatamente como antes.

---

## O que eu preciso de você

Cinco valores, todos tirados do painel do Supabase:

| Valor | Onde encontrar |
|---|---|
| **Connection string (pooler)** | Project Settings → Database → Connection string → **Transaction pooler** (porta 6543) |
| **Connection string (direta)** | a mesma tela, aba **Direct connection** (porta 5432) — usada só na migração |
| **Project URL** | Project Settings → API → Project URL |
| **service_role key** | Project Settings → API → Project API keys → `service_role` |
| **Nome do bucket** | você escolhe ao criar (sugestão: `anexos`) |

> A `service_role` key ignora todas as regras de segurança do banco. Ela vive
> **só no servidor**, nas variáveis de ambiente. Nunca a coloque no frontend,
> em repositório público ou em print de tela.

---

## Passo 1 — Criar o projeto

1. Em <https://supabase.com>, crie um projeto.
2. Escolha a região mais próxima (para o Brasil, `South America (São Paulo)`
   dá a menor latência).
3. Guarde a **senha do banco** que ele pede na criação: ela faz parte da
   connection string e não é exibida de novo.

## Passo 2 — Criar o bucket de anexos

1. Menu **Storage** → **New bucket**.
2. Nome: `anexos`.
3. **Deixe "Public bucket" DESMARCADO.**

Isso é importante. Na v1 do app, qualquer pessoa com o link via a nota fiscal
da obra. A v2 corrigiu isso servindo anexo por rota autenticada — um bucket
público jogaria a correção fora. Com o bucket privado, o app valida a sessão e
só então gera uma URL assinada de curta duração (1 hora por padrão).

## Passo 3 — Instalar o driver

```bash
pip install -r requirements.txt      # já inclui psycopg[binary]
```

## Passo 4 — Definir as variáveis de ambiente

No Render (ou no seu `.env` local):

```bash
# Banco — use o POOLER (porta 6543) para o app
DATABASE_URL='postgresql://postgres.SEU-REF:SENHA@aws-0-sa-east-1.pooler.supabase.com:6543/postgres'

# Storage
SUPABASE_URL='https://SEU-PROJETO.supabase.co'
SUPABASE_SERVICE_KEY='eyJ...service_role...'
SUPABASE_BUCKET='anexos'

# Sessão e HTTPS
SECRET_KEY='uma-chave-longa-e-aleatoria'
FORCE_HTTPS=1
```

Ao subir, o app cria o schema sozinho e cadastra o gestor inicial
(`ADMIN_EMAIL` / `ADMIN_SENHA`, padrão `gestor@obra.local` / `obra123`).
Troque a senha no primeiro acesso.

### Por que pooler no app e conexão direta na migração

O pooler em modo transação aguenta muitas conexões curtas — o padrão de um app
web. A conexão direta (5432) é melhor para carga em lote, que é o caso da
migração. Usar o pooler na migração costuma dar timeout no meio da cópia.

## Passo 5 — Levar os dados que já existem

Se você já usa o app com SQLite:

```bash
export DATABASE_URL='postgresql://postgres:SENHA@db.SEU-PROJETO.supabase.co:5432/postgres'  # direta
export SUPABASE_URL='https://SEU-PROJETO.supabase.co'
export SUPABASE_SERVICE_KEY='eyJ...'
export SUPABASE_BUCKET='anexos'

python ferramentas/migrar_para_supabase.py dados/obra_erp.db
```

O script:

1. normaliza o banco de origem (importante se ele vem da v1: retroage datas,
   preenche o snapshot de valor/hora);
2. cria o schema no Postgres;
3. copia as tabelas na ordem das dependências, **preservando os ids**;
4. reposiciona as sequências — sem isso, o primeiro lançamento novo colidiria
   com um id já existente;
5. sobe os anexos da pasta `uploads` para o bucket.

Ele se recusa a escrever em tabela que já tenha dados, então rodar duas vezes
por engano não duplica nada. Para recomeçar do zero, apague as tabelas no
Supabase antes.

## Passo 6 — Conferir se está mesmo funcionando

Ter a variável definida não prova nada: a senha pode estar errada, o projeto
pausado, a rede bloqueada. Há três formas de checar, da mais fácil à mais
completa.

**1. Dentro do app (recomendado).** Entre como gestor, vá em **Ajustes → Onde
os dados estão sendo salvos** e toque em **Testar agora**. O app grava e lê de
verdade no banco e no bucket, e mostra:

- farol verde/vermelho para banco e anexos;
- se está em `Supabase` ou em `SQLite local` / `disco do servidor`;
- o host de destino (sem expor usuário e senha);
- o tempo de resposta e a contagem de obras, dias e lançamentos.

Se aparecer o aviso *"Você não está usando o Supabase"*, a `DATABASE_URL` não
chegou ao processo — reveja as variáveis no Render e refaça o deploy.

**2. Pelo navegador:**

```
https://seu-app.onrender.com/health
```

Deve trazer `"banco": "postgres"` e `"anexos": "supabase"`.

**3. No painel do Supabase:** **Table Editor** deve mostrar as tabelas com os
dados, e **Storage → anexos** deve listar os arquivos. Lance uma despesa com
foto no app e veja o arquivo aparecer no bucket — é a prova definitiva.

---

## Segurança: RLS

O app cria as tabelas com **Row Level Security ligada e nenhuma policy**. Isso
é proposital:

- todo acesso passa pelo backend, que usa a `service_role` key — e ela ignora
  RLS por definição;
- a chave pública (`anon`) do projeto, que qualquer um poderia descobrir, não
  consegue ler nem escrever **nada**.

Se um dia você quiser que um app cliente fale direto com o Supabase (sem passar
pelo backend), aí sim será preciso escrever policies por tabela. Enquanto o
Flask for a única porta, o modelo atual é o mais seguro.

---

## Problemas comuns

> **Ao colar as variáveis no Render, cole apenas o valor** — sem aspas, sem
> colchetes, sem `<>`. A documentação costuma mostrar `[SEU-PROJETO]` como
> marcador de posição, e os colchetes acabam indo junto. Desde a 2.8.3 o app
> remove esses caracteres sozinho e aponta o problema em Ajustes, mas o valor
> limpo continua sendo o certo.

| Sintoma | Causa provável |
|---|---|
| `DATABASE_URL aponta para Postgres, mas o driver não está instalado` | falta `pip install 'psycopg[binary]'` |
| `password authentication failed` | senha do banco errada na connection string, ou caracteres especiais não escapados na URL |
| Timeout durante a migração | está usando o pooler (6543); troque pela conexão direta (5432) |
| Fotos somem depois do deploy | `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` ausentes: os anexos caíram no disco efêmero |
| `Supabase Storage 404` ao abrir anexo | nome do bucket diferente de `SUPABASE_BUCKET` |
| `unknown url type: [https` | `SUPABASE_URL` colada com colchetes ou aspas |
| Anexos antigos abrem, novos não | upload falhando: o app grava no disco como reserva — confira a service key |

---

## Voltando para SQLite

Basta remover `DATABASE_URL` (e as variáveis de Storage). O app volta ao
arquivo local no boot seguinte. Os dois modos usam o mesmo código; nada no
app "sabe" em qual banco está rodando.
