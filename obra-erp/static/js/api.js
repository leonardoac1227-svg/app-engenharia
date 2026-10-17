/* ==========================================================================
   Cliente da API — um único ponto de saída para o backend.
   Cuida do token CSRF, de sessão expirada e da mensagem de erro amigável.
   ========================================================================== */

import { toast } from './util.js';

let csrf = '';

export function definirCsrf(token) { csrf = token || ''; }
export function obterCsrf() { return csrf; }

export class ErroApi extends Error {
  constructor(mensagem, status, codigo) {
    super(mensagem);
    this.status = status;
    this.codigo = codigo;
  }
}

/* --------------------------------------------------------------------------
   Cache de leitura: mostra na hora, confere por trás.

   Trocar de aba refazia todas as consultas da tela. No wifi do escritório
   ninguém nota; no 4G do canteiro é um segundo de esqueleto para ver dado que
   acabou de chegar.

   Agora um GET responde do cache sempre que houver cópia recente:
     - até VALIDADE, a cópia é usada sem tocar a rede;
     - até VIDA_MAXIMA, a cópia aparece na hora e a consulta é refeita em
       segundo plano; se o servidor devolver algo diferente, quem se
       inscreveu em aoAtualizarEmSegundoPlano() redesenha a tela.

   **Qualquer gravação limpa o cache inteiro** e invalida as consultas que
   estavam a caminho: o que o próprio usuário lançou aparece sempre, nunca um
   saldo de antes da despesa. O que fica para a conferência de fundo é só a
   alteração feita por outra pessoa, em outro aparelho.
   -------------------------------------------------------------------------- */

const CACHE = new Map();
const EM_ANDAMENTO = new Map();
const VALIDADE = 15_000;          // ms — cópia usada sem consultar
const VIDA_MAXIMA = 10 * 60_000;  // ms — cópia mostrada enquanto confere
let geracao = 0;
let ouvinteAtualizacao = null;

export function limparCache() {
  CACHE.clear();
  EM_ANDAMENTO.clear();
  geracao += 1;
}

/** Chamado quando a conferência de fundo traz dado diferente do mostrado. */
export function aoAtualizarEmSegundoPlano(fn) { ouvinteAtualizacao = fn; }

async function buscarJson(caminho, init, silencioso) {
  let resposta;
  try {
    resposta = await fetch(caminho, init);
  } catch (e) {
    const msg = navigator.onLine
      ? 'Não foi possível falar com o servidor.'
      : 'Você está sem conexão. O lançamento não foi salvo.';
    if (!silencioso) toast('⚠ ' + msg, 'erro');
    throw new ErroApi(msg, 0, 'rede');
  }

  if (resposta.status === 401) {
    window.location.href = '/login';
    throw new ErroApi('Sessão expirada.', 401, 'sem_sessao');
  }

  if (!resposta.ok) {
    let mensagem = 'Não foi possível concluir a operação.';
    let codigo = '';
    try {
      const corpo = await resposta.json();
      mensagem = corpo.erro || mensagem;
      codigo = corpo.codigo || '';
    } catch (_) { /* resposta sem JSON */ }
    if (codigo === 'csrf') {
      // token perdido (servidor reiniciado): recarrega para renovar a sessão
      window.location.reload();
    }
    if (!silencioso) toast('⚠ ' + mensagem, 'erro');
    throw new ErroApi(mensagem, resposta.status, codigo);
  }

  if (resposta.status === 204) return { dados: null };

  const tipo = resposta.headers.get('content-type') || '';
  if (!tipo.includes('application/json')) return { dados: resposta };

  const texto = await resposta.text();
  return { dados: JSON.parse(texto), texto };
}

/** GET que passa pelo cache. Duas telas pedindo o mesmo dado = uma viagem. */
function buscarComCache(caminho, init, silencioso) {
  const pendente = EM_ANDAMENTO.get(caminho);
  if (pendente) return pendente;

  const minhaGeracao = geracao;
  const promessa = buscarJson(caminho, init, silencioso)
    .then((r) => {
      if (r.texto !== undefined && minhaGeracao === geracao) {
        CACHE.set(caminho, { texto: r.texto, em: Date.now() });
      }
      return r;
    })
    .finally(() => { if (EM_ANDAMENTO.get(caminho) === promessa) EM_ANDAMENTO.delete(caminho); });
  EM_ANDAMENTO.set(caminho, promessa);
  return promessa;
}

function conferirPorTras(caminho, init, textoMostrado) {
  if (EM_ANDAMENTO.has(caminho)) return;
  buscarComCache(caminho, init, true)
    .then((r) => {
      if (r.texto !== undefined && r.texto !== textoMostrado && ouvinteAtualizacao) {
        ouvinteAtualizacao(caminho);
      }
    })
    .catch(() => { /* sem rede: segue com a cópia */ });
}

async function requisicao(caminho, opcoes = {}) {
  const { silencioso = false, semCache = false, ...init } = opcoes;
  init.headers = init.headers || {};
  if (!['GET', 'HEAD'].includes((init.method || 'GET').toUpperCase())) {
    init.headers['X-CSRF-Token'] = csrf;
  }
  init.credentials = 'same-origin';

  const metodo = (init.method || 'GET').toUpperCase();

  if (metodo !== 'GET') {
    // Escreveu: tudo que estava guardado pode ter ficado velho.
    limparCache();
    return (await buscarJson(caminho, init, silencioso)).dados;
  }

  if (!semCache) {
    const guardado = CACHE.get(caminho);
    const idade = guardado ? Date.now() - guardado.em : Infinity;
    if (idade <= VIDA_MAXIMA) {
      if (idade > VALIDADE) conferirPorTras(caminho, init, guardado.texto);
      // Cópia nova a cada leitura: quem recebe pode alterar o objeto à vontade.
      return JSON.parse(guardado.texto);
    }
    if (guardado) CACHE.delete(caminho);
  }

  const r = await buscarComCache(caminho, init, silencioso);
  // Quem divide a viagem com outra chamada recebe a sua própria cópia.
  return r.texto !== undefined ? JSON.parse(r.texto) : r.dados;
}

/** Busca em segundo plano o que a próxima tela vai pedir. Não avisa erro. */
export function preaquecer(caminhos) {
  caminhos.forEach((caminho) => {
    const guardado = CACHE.get(caminho);
    if (guardado && Date.now() - guardado.em <= VALIDADE) return;
    buscarComCache(caminho, { method: 'GET', headers: {}, credentials: 'same-origin' }, true)
      .catch(() => { /* só um palpite: falhar aqui não importa */ });
  });
}

export const api = {
  get: (caminho, opcoes) => requisicao(caminho, { method: 'GET', ...opcoes }),

  post: (caminho, corpo, opcoes) => requisicao(caminho, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo || {}),
    ...opcoes,
  }),

  put: (caminho, corpo, opcoes) => requisicao(caminho, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(corpo || {}),
    ...opcoes,
  }),

  del: (caminho, opcoes) => requisicao(caminho, { method: 'DELETE', ...opcoes }),

  /** Envio multipart (fotos e notas fiscais). */
  form: (caminho, formData, opcoes) => requisicao(caminho, { method: 'POST', body: formData, ...opcoes }),
};

/** Downloads passam por link direto (GET autenticado por cookie de sessão). */
export function baixar(url) {
  const link = document.createElement('a');
  link.href = url;
  link.rel = 'noopener';
  document.body.appendChild(link);
  link.click();
  link.remove();
}
