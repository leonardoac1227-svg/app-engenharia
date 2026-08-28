# O deploy subiu mas a tela continua igual — o que checar

Diagnóstico em ordem: o primeiro item que falhar é a causa. Não pule etapas,
porque os sintomas de "cache velho" e "deploy não pegou" são idênticos.

## 1. Que versão está realmente no ar?

```
https://seu-app.onrender.com/health
```

Deve responder algo como:

```json
{"ok": true, "versao": "2.1.0", "banco": "postgres", "anexos": "supabase", "tema": "claro-2.1"}
```

- **Não abre / erro 502** → o deploy falhou. Vá ao item 2.
- **Abre com `"versao": "2.0.0"` ou sem o campo `versao`** → o Render ainda
  está servindo o build antigo. Item 2.
- **Abre com `"versao": "2.1.0"`** → o servidor está certo e o problema é
  cache no seu aparelho. Item 4.

Esse endpoint também confirma se o Supabase pegou: `"banco": "postgres"` e
`"anexos": "supabase"`. Se aparecer `"sqlite"`/`"disco"`, as variáveis de
ambiente não chegaram ao processo.

## 2. A estrutura do repositório está certa?

O ZIP contém uma pasta `obra-erp/` na raiz. Se você descompactou e subiu a
pasta inteira, o repositório ficou assim:

```
seu-repo/
└── obra-erp/
    ├── app.py
    ├── requirements.txt
    └── ...
```

O Render procura `requirements.txt` e `Procfile` **na raiz** e não vai achar.
Duas saídas:

- **Recomendada:** mova o conteúdo para a raiz, de modo que `app.py` fique em
  `seu-repo/app.py`.
- **Alternativa:** no Render, em *Settings → Root Directory*, escreva
  `obra-erp`.

Confirme na aba **Logs** do Render se o build terminou com sucesso e se o
último deploy é o de hoje. Um build que falha **mantém a versão anterior no
ar** — daí a impressão de que "nada mudou".

## 3. O Render pegou o commit?

Em *Events*, veja se há um deploy correspondente ao seu push. Se não houver,
clique em **Manual Deploy → Deploy latest commit**. Se o serviço estiver ligado
a outro branch, o push no `main` não dispara nada.

## 4. Cache do navegador e do PWA

Se o `/health` já mostra `2.1.0` mas a tela continua escura, o culpado é o
service worker antigo guardado no seu aparelho.

**No computador (Chrome/Edge):**
1. F12 → aba **Application** → **Service Workers** → **Unregister**.
2. Ainda em Application → **Storage** → **Clear site data**.
3. Recarregue com Ctrl+Shift+R.

**No celular:**
- Se você instalou o app na tela inicial, **desinstale o ícone** e adicione de
  novo — o service worker vive junto com o atalho.
- Ou abra pelo navegador em aba anônima para confirmar rapidamente.

**Teste rápido que isola o problema:** abra direto

```
https://seu-app.onrender.com/static/css/style.css
```

Se as primeiras linhas dizem `SISTEMA DE DESIGN "CANTEIRO CLARO"`, o servidor
está com o arquivo novo e o problema é só cache local. Se dizem
`concreto à noite, latão`, o servidor ainda está no build antigo — volte ao
item 2.

## Por que isso aconteceu

A versão anterior do service worker usava estratégia *cache-first* e o próprio
`sw.js` não mudava entre releases. Como o navegador só reinstala o service
worker quando esse arquivo muda em bytes, o CSS antigo ficava servido do cache
indefinidamente. Corrigido na 2.1.0: a estratégia passou a ser *network-first*
(quem está online recebe sempre o código novo; sem sinal, cai no cache) e o
nome do cache carrega um número de versão, que muda a cada release.
