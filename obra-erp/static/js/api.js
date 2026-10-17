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
   Cache curto de leitura.

   Trocar de aba refazia todas as consultas da tela. No wifi do escritório
   ninguém nota; no 4G do canteiro é um segundo de espera para ver dado que
   acabou de chegar. Aqui a resposta de um GET vale por alguns segundos.

   A validade é curta de propósito, e **qualquer gravação limpa o cache
   inteiro**: é melhor refazer uma consulta à toa do que mostrar um saldo
   desatualizado logo depois de lançar uma despesa.
   -------------------------------------------------------------------------- */

const CACHE = new Map();
const VALIDADE = 12_000;   // ms

export function limparCache() {
  CACHE.clear();
}

function doCache(caminho) {
  const guardado = CACHE.get(caminho);
  if (!guardado) return null;
  if (Date.now() - guardado.em > VALIDADE) {
    CACHE.delete(caminho);
    return null;
  }
  // Cópia: quem recebe pode alterar o objeto sem contaminar o cache.
  return JSON.parse(JSON.stringify(guardado.dados));
}

async function requisicao(caminho, opcoes = {}) {
  const { silencioso = false, semCache = false, ...init } = opcoes;
  init.headers = init.headers || {};
  if (!['GET', 'HEAD'].includes((init.method || 'GET').toUpperCase())) {
    init.headers['X-CSRF-Token'] = csrf;
  }
  init.credentials = 'same-origin';

  const metodo = (init.method || 'GET').toUpperCase();
  const podeUsarCache = metodo === 'GET' && !semCache;

  if (podeUsarCache) {
    const guardado = doCache(caminho);
    if (guardado !== null) return guardado;
  } else if (metodo !== 'GET') {
    // Escreveu: tudo que estava guardado pode ter ficado velho.
    limparCache();
  }

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

  if (resposta.status === 204) return null;

  const tipo = resposta.headers.get('content-type') || '';
  if (!tipo.includes('application/json')) return resposta;

  const dados = await resposta.json();
  if (podeUsarCache && dados && typeof dados === 'object') {
    CACHE.set(caminho, { dados, em: Date.now() });
  }
  return dados;
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
