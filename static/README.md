# 🏗️ Obra ERP — ERP de Bolso para Gestão de Obras (Flask + HTML/CSS/JS)

App mobile-first para lançar RDO, folha de pagamento e caixa/materiais
direto do canteiro, em poucos segundos. Reescrito sem Streamlit: backend
em **Flask (Python)** com API REST e frontend em **HTML/CSS/JS puros**,
com identidade visual própria (concreto + latão, estética de "diário de
canteiro/prancha técnica").

## Estrutura do projeto

```
app.py                  → backend Flask (API REST + SQLite)
templates/index.html    → página única (SPA)
static/css/style.css    → identidade visual (dark mode premium)
static/js/app.js        → toda a lógica de interação (fetch na API)
static/uploads/         → notas fiscais/comprovantes enviados
requirements.txt
runtime.txt              → fixa Python 3.11 no Render
Procfile                 → comando de start no Render
```

## Rodar localmente

```bash
pip install -r requirements.txt
python app.py
```

Abra `http://localhost:5000` no celular (mesma rede) ou use o modo
"device toolbar" do Chrome DevTools para ver a experiência mobile.

## Banco de dados (SQLite — `obra_erp.db`, criado automaticamente)

| Tabela         | Descrição                                            |
|----------------|-------------------------------------------------------|
| `obras`        | Centros de custo (uma obra = dados 100% isolados)     |
| `semanas`      | Semanas de cada obra, com meta e status de conclusão   |
| `dias`         | Um registro por dia da semana (Seg a Dom)              |
| `funcionarios` | Equipe cadastrada por obra, com valor/hora             |
| `horas`        | Horas trabalhadas por funcionário em cada dia          |
| `despesas`     | Lançamentos de caixa (material, serviço, locação...)   |

Nenhum servidor SQL externo é necessário.

## API REST (resumo)

- `GET/POST /api/obras`
- `GET/POST /api/obras/<id>/semanas`
- `PUT /api/semanas/<id>` (meta / meta_atingida)
- `GET /api/semanas/<id>/dias` (garante os 7 dias)
- `PUT /api/dias/<id>` (RDO)
- `GET/POST /api/obras/<id>/funcionarios`, `DELETE /api/funcionarios/<id>`
- `GET/PUT /api/dias/<id>/horas`
- `GET /api/dias/<id>/totais`
- `GET/POST /api/dias/<id>/despesas` (POST é multipart, aceita anexo), `DELETE /api/despesas/<id>`
- `GET /api/semanas/<id>/fechamento` (resumo físico + financeiro em JSON)
- `GET /api/semanas/<id>/relatorio.txt` (download do relatório)

## Deploy no Render

1. Suba o projeto inteiro (`app.py`, `templates/`, `static/`,
   `requirements.txt`, `runtime.txt`, `Procfile`) em um repositório Git.
2. No Render, crie um **Web Service** apontando para o repositório.
3. Configure:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT`
     (já está definido no `Procfile`, então o Render costuma detectar
     automaticamente)

### ⚠️ Persistência dos dados no Render

O plano gratuito usa filesystem **efêmero**: `obra_erp.db` e os arquivos
em `static/uploads/` são apagados a cada novo deploy/reinício. Para uso
real, adicione um **Persistent Disk** do Render montado na pasta do
projeto, ou migre para um banco gerenciado (Postgres do Render) trocando
apenas a camada de conexão em `app.py` — as rotas e o frontend não
precisam mudar.

## Próximos passos sugeridos

- OCR automático das notas fiscais/PIX enviadas em "Caixa e Materiais"
  (o campo `imagem_path` já está pronto para isso).
- Autenticação por obra/usuário.
- Exportar o fechamento semanal também em PDF.
