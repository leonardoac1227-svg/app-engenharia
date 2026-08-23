/* ==========================================================================
   Utilitários de interface — DOM, formatação e componentes compartilhados.
   ========================================================================== */

export const $ = (sel, raiz = document) => raiz.querySelector(sel);
export const $$ = (sel, raiz = document) => Array.from(raiz.querySelectorAll(sel));

/** Escapa texto vindo do usuário antes de entrar em innerHTML.
 *  Sem isso, uma descrição com "<" quebra o layout — e um texto malicioso
 *  vira script executando na sessão do gestor. */
export function esc(valor) {
  if (valor === null || valor === undefined) return '';
  return String(valor)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export const DIAS_SEMANA = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo'];

export const CLIMAS = ['☀️ Limpo', '⛅ Parcialmente nublado', '☁️ Nublado',
  '🌦️ Chuva leve', '⛈️ Chuva c/ paralisação', '🌡️ Calor extremo'];

// ---------------------------------------------------------------- formatação

export function moeda(v) {
  return 'R$ ' + (Number(v) || 0).toLocaleString('pt-BR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
}

/** Versão curta para KPIs em tela pequena: R$ 128,4 mil. */
export function moedaCurta(v) {
  const n = Number(v) || 0;
  if (Math.abs(n) >= 1000000) return 'R$ ' + (n / 1000000).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + ' mi';
  if (Math.abs(n) >= 10000) return 'R$ ' + (n / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' mil';
  return moeda(n);
}

export function num(v, casas = 0) {
  return (Number(v) || 0).toLocaleString('pt-BR', { maximumFractionDigits: casas });
}

export function dataBR(iso) {
  if (!iso) return '';
  const [a, m, d] = String(iso).slice(0, 10).split('-');
  return d && m && a ? `${d}/${m}/${a}` : String(iso);
}

export function dataCurta(iso) {
  if (!iso) return '';
  const [, m, d] = String(iso).slice(0, 10).split('-');
  return d && m ? `${d}/${m}` : '';
}

export function dataHoraBR(iso) {
  if (!iso) return '';
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return String(iso);
  return dt.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export const hojeISO = () => new Date().toLocaleDateString('sv-SE'); // sv-SE = YYYY-MM-DD

export function iniciais(nome) {
  const partes = String(nome || '').trim().split(/\s+/);
  if (!partes[0]) return '--';
  return ((partes[0][0] || '') + (partes.length > 1 ? partes[partes.length - 1][0] : '')).toUpperCase();
}

export function debounce(fn, ms = 400) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ---------------------------------------------------------------- toast

let toastTimer;
export function toast(mensagem, tipo = '') {
  const el = $('#toast');
  if (!el) return;
  el.textContent = mensagem;
  el.className = 'toast show ' + tipo;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast ' + tipo; }, 2800);
}

// ---------------------------------------------------------------- modal

export function abrirModal(html, aoAbrir) {
  const overlay = $('#modalOverlay');
  $('#modalConteudo').innerHTML = html;
  overlay.classList.add('open');
  if (typeof aoAbrir === 'function') aoAbrir($('#modalConteudo'));
  return $('#modalConteudo');
}

export function fecharModal() {
  $('#modalOverlay').classList.remove('open');
  setTimeout(() => { const c = $('#modalConteudo'); if (c) c.innerHTML = ''; }, 200);
}

/** Substitui window.confirm() por um diálogo dentro da identidade do app. */
export function confirmar({ titulo, texto = '', confirmar: rotulo = 'Confirmar', perigo = false }) {
  return new Promise((resolve) => {
    abrirModal(`
      <h3>${esc(titulo)}</h3>
      ${texto ? `<p class="muted small">${esc(texto)}</p>` : ''}
      <div class="btn-linha">
        <button class="btn btn-outline" data-acao="cancelar">Cancelar</button>
        <button class="btn ${perigo ? 'btn-perigo' : 'btn-brass'}" data-acao="ok">${esc(rotulo)}</button>
      </div>`, (raiz) => {
      raiz.querySelector('[data-acao=cancelar]').onclick = () => { fecharModal(); resolve(false); };
      raiz.querySelector('[data-acao=ok]').onclick = () => { fecharModal(); resolve(true); };
    });
  });
}

/** Formulário rápido em modal. campos: [{nome, rotulo, tipo, valor, placeholder, opcoes}] */
export function formularioModal({ titulo, campos, rotulo = 'Salvar', descricao = '' }) {
  return new Promise((resolve) => {
    const html = campos.map((c) => {
      const id = `campo_${c.nome}`;
      if (c.tipo === 'select') {
        const opcoes = (c.opcoes || []).map((o) => {
          const valor = typeof o === 'object' ? o.valor : o;
          const texto = typeof o === 'object' ? o.texto : o;
          return `<option value="${esc(valor)}"${String(valor) === String(c.valor ?? '') ? ' selected' : ''}>${esc(texto)}</option>`;
        }).join('');
        return `<label class="field-label" for="${id}">${esc(c.rotulo)}</label><select id="${id}">${opcoes}</select>`;
      }
      if (c.tipo === 'textarea') {
        return `<label class="field-label" for="${id}">${esc(c.rotulo)}</label>
                <textarea id="${id}" rows="3" placeholder="${esc(c.placeholder || '')}">${esc(c.valor || '')}</textarea>`;
      }
      return `<label class="field-label" for="${id}">${esc(c.rotulo)}</label>
              <input type="${c.tipo || 'text'}" id="${id}" value="${esc(c.valor ?? '')}"
                     placeholder="${esc(c.placeholder || '')}" ${c.tipo === 'number' ? 'inputmode="decimal" step="0.01"' : ''}>`;
    }).join('');

    abrirModal(`
      <h3>${esc(titulo)}</h3>
      ${descricao ? `<p class="muted small">${esc(descricao)}</p>` : ''}
      ${html}
      <div class="btn-linha">
        <button class="btn btn-outline" data-acao="cancelar">Cancelar</button>
        <button class="btn btn-brass" data-acao="ok">${esc(rotulo)}</button>
      </div>`, (raiz) => {
      const primeiro = raiz.querySelector('input, textarea, select');
      if (primeiro) setTimeout(() => primeiro.focus(), 120);
      raiz.querySelector('[data-acao=cancelar]').onclick = () => { fecharModal(); resolve(null); };
      raiz.querySelector('[data-acao=ok]').onclick = () => {
        const dados = {};
        campos.forEach((c) => { dados[c.nome] = raiz.querySelector(`#campo_${c.nome}`).value; });
        fecharModal();
        resolve(dados);
      };
      raiz.querySelectorAll('input').forEach((inp) => {
        inp.addEventListener('keydown', (e) => {
          if (e.key === 'Enter') { e.preventDefault(); raiz.querySelector('[data-acao=ok]').click(); }
        });
      });
    });
  });
}

// ---------------------------------------------------------------- componentes

export function barraProgresso(percentual, legendaEsq = '', legendaDir = '') {
  const pct = Number(percentual) || 0;
  const cor = pct <= 85 ? '' : (pct <= 100 ? 'amarelo' : 'vermelho');
  return `
    <div class="progresso">
      <div class="progresso-trilho">
        <div class="progresso-preenchido ${cor}" style="width:${Math.min(pct, 100)}%"></div>
      </div>
      ${(legendaEsq || legendaDir) ? `<div class="progresso-legenda"><span>${esc(legendaEsq)}</span><span>${esc(legendaDir)}</span></div>` : ''}
    </div>`;
}

export function kpi({ rotulo, valor, sub = '', estado = '', destaque = false }) {
  return `
    <div class="kpi ${estado} ${destaque ? 'destaque' : ''}">
      <div class="kpi-label">${esc(rotulo)}</div>
      <div class="kpi-valor ${String(valor).length > 11 ? 'pequeno' : ''}">${esc(valor)}</div>
      ${sub ? `<div class="kpi-sub">${esc(sub)}</div>` : ''}
    </div>`;
}

export function vazio(icone, texto, acaoHtml = '') {
  return `<div class="vazio"><div class="vazio-ico">${icone}</div><p>${esc(texto)}</p>${acaoHtml}</div>`;
}

export function esqueleto(qtd = 3) {
  return Array.from({ length: qtd }, () => '<div class="skeleton"></div>').join('');
}

export function abrirLightbox(url, info = '') {
  $('#lightboxImg').src = url;
  $('#lightboxInfo').textContent = info;
  $('#lightbox').classList.add('open');
}

export function badgeStatus(status, vencida = false) {
  const classes = { 'Reembolso': 'badge-reembolso', 'Em Aberto': 'badge-em-aberto', 'Pago': 'badge-pago' };
  const badge = `<span class="badge ${classes[status] || 'badge-neutro'}">${esc(status)}</span>`;
  return vencida ? badge + '<span class="badge badge-vencida">vencida</span>' : badge;
}
