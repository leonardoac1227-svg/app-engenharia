# Auditoria de segurança e desempenho — v3.0.0

Revisão de ponta a ponta do aplicativo. Cada item abaixo foi **medido antes de
ser corrigido**: sem número, otimização é chute e correção de segurança é fé.

Para reproduzir: `python ferramentas/rodar_testes.py`

---

## Segurança

### 1. Freio de força bruta podia ser anulado por um cabeçalho

O app lia `X-Forwarded-For` direto da requisição para identificar quem estava
tentando entrar. Esse cabeçalho é escolhido pelo cliente.

**Medido antes:** 40 tentativas de senha variando o cabeçalho → **0 bloqueadas**.
O limite existia e não valia nada.

**Corrigido:** `ProxyFix` com `x_for=1` — o app confia em exatamente um salto
(o proxy do Render) e ignora o que o cliente mandar antes dele. O limite passou
a contar também **por conta**, o que pega o ataque distribuído por muitos IPs.

### 2. Dava para descobrir quais e-mails existem pelo tempo de resposta

Login com e-mail inexistente respondia sem verificar senha nenhuma.

**Medido antes:** e-mail válido 68 ms · inexistente 0,3 ms — **274x de
diferença**. Um atacante mapeia as contas antes de tentar qualquer senha.

**Corrigido:** a verificação roda sempre, contra um hash descartável quando o
e-mail não existe. **Razão caiu para 1,0x.**

### 3. Contador de tentativas crescia sem limite

Cada origem diferente criava uma entrada permanente na memória.

**Medido antes:** 500 tentativas → 500 chaves que nunca saíam. Ataque
sustentado derruba o processo por consumo de RAM antes de descobrir senha.

**Corrigido:** entradas vencidas são removidas a cada registro e a tabela tem
teto rígido.

### 4. Extensão de arquivo era aceita sem conferir o conteúdo

**Corrigido:** a assinatura real do arquivo (*magic number*) é conferida contra
a extensão declarada. Executável renomeado para `.pdf` é recusado.

### 5. Bomba de descompressão em imagem

Um PNG de poucos KB pode declarar 60.000 × 60.000 pixels e consumir gigabytes
ao ser aberto.

**Corrigido:** teto de 40 MP no Pillow — cobre qualquer celular com folga
(um iPhone tira 12 MP) e recusa o resto.

### 6. Faltava HSTS

**Corrigido:** `Strict-Transport-Security` de um ano quando `FORCE_HTTPS=1`,
mais `Cross-Origin-Opener-Policy`.

**Cobertura:** `ferramentas/teste_seguranca.py` — 28 asserções, incluindo
tentativa de *path traversal* no anexo, acesso sem sessão, escrita sem CSRF e
conferência de que hash de senha e chave do Supabase não chegam ao navegador.

---

## Desempenho

O custo estava escondido: localmente tudo responde em 3 ms. Simulando a
latência real até o Supabase (12 ms por consulta), o painel levava **577 ms** e
o fechamento **441 ms** — só de ida e volta ao banco.

### O que foi feito

| | Antes | Depois |
|---|---|---|
| Consultas no painel | 47 | 31 |
| Consultas no fechamento | 36 | 22 |
| Requisições ao abrir o app | 7 (4 em fila) | 1 |
| Conexões ao Postgres | 1 por requisição | pool (25 requisições = 1 conexão) |
| Download do app | 118 KB | 32 KB (gzip, 73% menor) |
| Estáticos | revalidavam a cada carga | cache de 1 ano (URLs versionadas) |

**N+1 eliminados.** As medições de terceirizados faziam duas consultas por
linha; as locações, uma por equipamento. Agora vêm em consulta única e a soma é
feita em memória.

**Agregados unificados.** A tabela `horas` era varrida três vezes (folha, horas,
efetivo) e `despesas`, duas — tudo saía da mesma varredura.

**Pool de conexões.** O aperto de mão com o pooler do Supabase custava mais que
todas as consultas da tela somadas. O pool reconecta sozinho quando o servidor
derruba uma conexão ociosa, sem erro para o usuário.

**Abertura em uma requisição.** `/api/inicio` traz sessão, obras e o contexto da
obra. No 4G do canteiro (300 ms por ida), 1,2 s de espera viraram 300 ms.

**Cache curto no navegador.** Trocar de aba refazia todas as consultas. Agora a
resposta vale 12 segundos, e **qualquer gravação limpa o cache** — melhor
refazer uma consulta à toa que mostrar saldo velho depois de lançar despesa.

**Cobertura:** `ferramentas/teste_desempenho.py` fixa um teto de consultas por
tela e verifica que o custo **não cresce com o volume** — que é a assinatura de
um N+1 novo.

---

## Ferramentas de verificação

| Comando | O que faz |
|---|---|
| `python ferramentas/rodar_testes.py` | roda as 9 suítes, cada uma em banco limpo |
| `python ferramentas/verificar_sql.py` | recusa SQL que só funciona em SQLite |
| `node --experimental-vm-modules ferramentas/verificar_js.mjs` | sintaxe e importações do frontend |
| `python ferramentas/auditar_anexos.py` | confere anexos registrados x disponíveis |
