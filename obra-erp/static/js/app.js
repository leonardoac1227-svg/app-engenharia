/* ==========================================================================
   OBRA ERP — bootstrap da aplicação.
   Carrega a sessão, monta a lista de obras, controla a navegação entre as
   views e centraliza o ciclo de recarga de dados.
   ========================================================================== */

import { aoAtualizarEmSegundoPlano, api, definirCsrf, preaquecer } from './api.js';
import {
  abaPreferida, estado, obraPreferida, podeAdministrar, podeFinanceiro, podeLancar,
  salvarAbaPreferida, salvarObraPreferida,
} from './estado.js';
import { registrarNavegacao } from './navegacao.js';
import {
  $, $$, abrirModal, badgeObra, classeSeloObra, esc, fecharModal, formularioModal, iniciais,
  moedaCurta, toast, vazio,
} from './util.js';

import * as viewAjustes from './views/ajustes.js';
import * as viewDiario from './views/diario.js';
import * as viewEquipe from './views/equipe.js';
import * as viewFechamento from './views/fechamento.js';
import * as viewFinanceiro from './views/financeiro.js';
import * as viewHome from './views/home.js';

const VIEWS = {
  home: { el: '#viewHome', modulo: viewHome, titulo: 'PAINEL' },
  diario: { el: '#viewDiario', modulo: viewDiario, titulo: 'DIÁRIO' },
  equipe: { el: '#viewEquipe', modulo: viewEquipe, titulo: 'EQUIPE' },
  financeiro: { el: '#viewFinanceiro', modulo: viewFinanceiro, titulo: 'CAIXA' },
  fechamento: { el: '#viewFechamento', modulo: viewFechamento, titulo: 'FECHAMENTO' },
  ajustes: { el: '#viewAjustes', modulo: viewAjustes, titulo: 'AJUSTES' },
};

// ---------------------------------------------------------------- navegação

async function navegar(aba, { forcar = false } = {}) {
  if (!VIEWS[aba]) aba = 'home';
  if (aba === estado.aba && !forcar) { await renderizarAtual(); return; }

  estado.aba = aba;
  salvarAbaPreferida(aba);

  Object.entries(VIEWS).forEach(([nome, cfg]) => {
    const elemento = $(cfg.el);
    if (elemento) { // SÓ altera se o elemento existir no HTML
      elemento.classList.toggle('hidden', nome !== aba);
    }
  });
  
  $$('.tabbar-btn').forEach((btn) => btn.classList.toggle('active', btn.dataset.aba === aba));
  $('#topbarEyebrow').textContent = VIEWS[aba].titulo;
  window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });

  await renderizarAtual();
}

async function renderizarAtual() {
  const cfg = VIEWS[estado.aba] || VIEWS.home;
  const raiz = $(cfg.el);

  // Trava de segurança nova:
  if (!raiz) {
    console.warn(`Atenção: A tela ${cfg.el} não foi encontrada no HTML.`);
    return;
  }

  try {
    await cfg.modulo.renderizar(raiz);
  } catch (erro) {
    if (erro?.status === 401) return;
    raiz.innerHTML = vazio('!', 'Não foi possível carregar esta tela.',
      '<button class="btn btn-outline" data-acao="recarregar">Tentar de novo</button>');
    raiz.querySelector('[data-acao=recarregar]').onclick = () => window.location.reload();
    console.error(erro);
  }
  atualizarAlertasTabbar();
  atualizarFab();
  agendarPreaquecimento();
}

/* ---------------------------------------------------------- pré-carregamento

   Depois que a tela atual aparece, aproveita o tempo ocioso para buscar o que
   as outras abas vão pedir. Quando o usuário toca na aba, o dado já está em
   memória e a tela abre sem esqueleto. As URLs aqui precisam ser idênticas às
   que cada view pede — é o caminho que serve de chave do cache.
   -------------------------------------------------------------------------- */

let preaquecimentoAgendado = false;

function agendarPreaquecimento() {
  if (preaquecimentoAgendado || !estado.obraId) return;
  if (navigator.connection?.saveData) return;
  preaquecimentoAgendado = true;
  const ocioso = window.requestIdleCallback || ((fn) => setTimeout(fn, 400));
  ocioso(() => {
    preaquecimentoAgendado = false;
    if (!navigator.onLine || !estado.obraId) return;

    const caminhos = [`/api/obras/${estado.obraId}/dashboard`];
    if (estado.usuario?.papel !== 'cliente') caminhos.push(`/api/obras/${estado.obraId}/funcionarios`);
    if (estado.semanaId) {
      caminhos.push(`/api/semanas/${estado.semanaId}/fechamento`);
      caminhos.push(`/api/semanas/${estado.semanaId}/arquivos`);
    }
    if (podeFinanceiro()) {
      caminhos.push(`/api/obras/${estado.obraId}/financeiro`);
      caminhos.push(`/api/obras/${estado.obraId}/locacoes`);
    }

    // O diário abre no dia de hoje; dentro dele, a semana inteira fica pronta
    // e a troca de dia passa a ser instantânea.
    const hoje = estado.dias.find((d) => d.hoje) || estado.dias.find((d) => d.util) || estado.dias[0];
    const dias = estado.aba === 'diario' ? estado.dias : (hoje ? [hoje] : []);
    dias.forEach((d) => {
      caminhos.push(`/api/dias/${d.id}/horas`);
      if (podeFinanceiro()) caminhos.push(`/api/dias/${d.id}/despesas`);
    });
    preaquecer(caminhos);
  }, { timeout: 1500 });
}

/* A conferência de fundo achou dado novo (lançado em outro aparelho):
   redesenha, mas nunca no meio de uma digitação ou com um diálogo aberto. */
let redesenhoPendente;
aoAtualizarEmSegundoPlano(() => {
  clearTimeout(redesenhoPendente);
  redesenhoPendente = setTimeout(() => {
    const raiz = $(VIEWS[estado.aba]?.el);
    const digitando = raiz && raiz.contains(document.activeElement)
      && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    if (digitando || $('#modalOverlay').classList.contains('open')) return;
    renderizarAtual();
  }, 120);
});

function atualizarAlertasTabbar() {
  const pendentes = estado.dias.filter((d) => d.pendente).length;
  const botao = $('.tabbar-btn[data-aba=diario]');
  if (!botao) return;
  botao.querySelector('.tab-alerta')?.remove();
  if (pendentes > 0) {
    const marca = document.createElement('span');
    marca.className = 'tab-alerta';
    marca.title = `${pendentes} dia(s) sem lançamento`;
    botao.appendChild(marca);
  }
}

/* --------------------------------------------------------------- FAB */

/** O botão flutuante muda de repertório conforme o papel e a tela atual. */
function atualizarFab() {
  const fab = $('#btnFab');
  const escondido = !podeLancar() || estado.aba === 'ajustes' || !estado.obraId;
  fab.classList.toggle('hidden', escondido);
}

function abrirAcoesRapidas() {
  const dia = estado.dias.find((d) => d.hoje) || estado.diaAtivo || estado.dias[0];

  const acoes = [
    { ico: '📷', titulo: 'Foto do andamento', sub: 'anexa ao diário de hoje',
      fn: () => navegar('diario') },
    { ico: '⏱', titulo: 'Ponto da equipe', sub: 'horas trabalhadas de hoje',
      fn: () => navegar('diario') },
  ];

  if (podeFinanceiro() && dia) {
    acoes.push({
      ico: '$', titulo: 'Lançar despesa', sub: 'material, serviço ou locação',
      fn: async () => {
        const { abrirModalDespesa } = await import('./views/financeiro.js');
        abrirModalDespesa(dia, () => recarregarSemana({ manterDia: true }));
      },
    });
  }

  abrirModal(`
    <h3>Lançamento rápido</h3>
    <p class="small muted">${dia ? `Dia de referência: ${esc(dia.dia_semana)}` : 'Crie uma semana para começar.'}</p>
    <div class="lista" style="margin-top:12px">
      ${acoes.map((a, i) => `
        <button class="obra-item" data-acao-rapida="${i}">
          <span class="obra-selo">${a.ico}</span>
          <span class="obra-item-corpo">
            <span class="obra-item-nome">${esc(a.titulo)}</span>
            <span class="obra-item-sub">${esc(a.sub)}</span>
          </span>
          <span class="acc-chevron">›</span>
        </button>`).join('')}
    </div>
  `, (raiz) => {
    raiz.querySelectorAll('[data-acao-rapida]').forEach((btn) => {
      btn.onclick = () => { fecharModal(); acoes[Number(btn.dataset.acaoRapida)].fn(); };
    });
  });
}

function ajustarPermissoes() {
  $('.tabbar-btn[data-aba=financeiro]').classList.toggle('hidden', !podeFinanceiro());
  $('.tabbar-btn[data-aba=equipe]').classList.toggle('hidden', estado.usuario.papel === 'cliente');
  $('#drawerFoot').classList.toggle('hidden', !podeAdministrar());
}

// ---------------------------------------------------------------- dados

async function carregarObras({ manterSelecao = true } = {}) {
  estado.obras = await api.get(
    `/api/obras${estado.mostrarArquivadas ? '?arquivadas=1' : ''}`, { semCache: true });
  renderizarDrawer();

  if (!estado.obras.length) {
    estado.obraId = null;
    estado.obra = null;
    return;
  }
  const preferida = manterSelecao ? (estado.obraId || obraPreferida()) : null;
  const alvo = estado.obras.find((o) => o.id === preferida) || estado.obras[0];
  await selecionarObra(alvo.id, { renderizar: false });
}

function renderizarDrawer() {
  const lista = $('#listaObras');
  if (!estado.obras.length) {
    lista.innerHTML = '<p class="vazio-inline">Nenhuma obra cadastrada ainda.</p>';
    return;
  }
  lista.innerHTML = estado.obras.map((o) => `
    <button class="obra-item ${o.id === estado.obraId ? 'active' : ''}" data-obra="${o.id}">
      <span class="obra-selo ${classeSeloObra(o.status, o.arquivada)}">${iniciais(o.nome)}</span>
      <span class="obra-item-corpo">
        <span class="obra-item-nome">${esc(o.nome)}</span>
        <span class="obra-item-sub">
          ${o.cliente ? esc(o.cliente) + ' · ' : ''}${o.total_semanas} semana(s)
          ${podeFinanceiro() && o.orcamento_total ? ' · ' + moedaCurta(o.orcamento_total) : ''}
        </span>
      </span>
      ${badgeObra(o.status, o.arquivada)}
    </button>`).join('');

  lista.querySelectorAll('[data-obra]').forEach((btn) => {
    btn.onclick = () => { fecharDrawer(); selecionarObra(Number(btn.dataset.obra)); };
  });
}

async function selecionarObra(obraId, { renderizar = true } = {}) {
  estado.obraId = obraId;
  salvarObraPreferida(obraId);
  estado.obra = estado.obras.find((o) => o.id === obraId) || null;
  estado.semanaId = null;
  estado.diaAtivo = null;
  $('#topbarTitulo').textContent = estado.obra?.nome || 'Obra ERP';
  renderizarDrawer();

  // Uma requisição em vez de três: funcionários, semanas e os dias
  // da semana atual vêm juntos. O servidor não trabalha menos, mas o aparelho
  // deixa de esperar várias viagens de rede em fila.
  aplicarContexto(await api.get(`/api/obras/${obraId}/contexto`, { semCache: true }));

  if (renderizar) await renderizarAtual();
}

/** Copia para o estado o pacote devolvido por /api/inicio e /contexto. */
function aplicarContexto(contexto) {
  if (!contexto) {
    estado.funcionarios = []; estado.semanas = [];
    estado.semanaId = null; estado.semana = null; estado.dias = [];
    return;
  }
  estado.funcionarios = contexto.funcionarios || [];
  estado.semanas = contexto.semanas || [];
  estado.semanaId = contexto.semana_id || null;
  estado.semana = estado.semanas.find((s) => s.id === estado.semanaId) || null;
  estado.dias = contexto.dias || [];
  if (contexto.obra) estado.obra = { ...estado.obra, ...contexto.obra };
}

async function recarregarSemana({ manterDia = false, recarregarLista = false } = {}) {
  if (recarregarLista) {
    estado.semanas = await api.get(`/api/obras/${estado.obraId}/semanas`, { semCache: true });
    if (!estado.semanas.some((s) => s.id === estado.semanaId)) {
      estado.semanaId = estado.semanas[estado.semanas.length - 1]?.id || null;
    }
  }
  estado.semana = estado.semanas.find((s) => s.id === estado.semanaId) || null;
  estado.dias = estado.semanaId
    ? await api.get(`/api/semanas/${estado.semanaId}/dias`, { semCache: true }) : [];
  if (!manterDia) estado.diaAtivo = null;
  await renderizarAtual();
}

async function recarregarObra({ recarregarLista = false } = {}) {
  if (recarregarLista || !estado.obraId) {
    await carregarObras();
    await renderizarAtual();
    return;
  }
  await selecionarObra(estado.obraId);
}

// ---------------------------------------------------------------- drawer

const abrirDrawer = () => {
  $('#drawer').classList.add('open');
  $('#drawerOverlay').classList.add('open');
};
const fecharDrawer = () => {
  $('#drawer').classList.remove('open');
  $('#drawerOverlay').classList.remove('open');
};

// ---------------------------------------------------------------- boot

async function iniciar() {
  // Abertura em uma requisição: sessão, obras e o contexto da última obra
  // usada. Eram sete idas ao servidor, quatro delas em fila.
  const preferida = obraPreferida();
  const inicio = await api.get(
    `/api/inicio${preferida ? `?obra=${preferida}` : ''}`, { silencioso: true });

  if (!inicio.autenticado) { window.location.href = '/login'; return; }

  estado.usuario = inicio.usuario;
  estado.versao = inicio.versao || '';
  if (inicio.reembolso_padrao) estado.reembolsoPadrao = inicio.reembolso_padrao;
  if (inicio.jornada_horas) estado.jornadaHoras = inicio.jornada_horas;
  definirCsrf(inicio.csrf);
  $('#btnPerfil').textContent = iniciais(inicio.usuario.nome);
  ajustarPermissoes();

  estado.obras = inicio.obras || [];
  if (inicio.contexto) {
    estado.obraId = inicio.contexto.obra_id;
    estado.obra = estado.obras.find((o) => o.id === estado.obraId) || null;
    salvarObraPreferida(estado.obraId);
    aplicarContexto(inicio.contexto);
    $('#topbarTitulo').textContent = estado.obra?.nome || 'Obra ERP';
  }
  renderizarDrawer();

  const abaInicial = VIEWS[abaPreferida()] ? abaPreferida() : 'home';
  const restritas = ['financeiro'];
  const abaSegura = (!podeFinanceiro() && restritas.includes(abaInicial)) ? 'home' : abaInicial;
  await navegar(abaSegura, { forcar: true });

  if (inicio.usuario.senha_provisoria) {
    toast('Você ainda usa a senha provisória — troque em Ajustes.', 'erro');
  }
}

function ligarEventosGlobais() {
  $('#btnMenu').onclick = abrirDrawer;
  $('#drawerOverlay').onclick = fecharDrawer;
  $('#btnPerfil').onclick = () => navegar('ajustes');
  $('#btnFab').onclick = abrirAcoesRapidas;

  $$('.tabbar-btn').forEach((btn) => { btn.onclick = () => navegar(btn.dataset.aba); });

  $('#btnNovaObra').onclick = async () => {
    const dados = await formularioModal({
      titulo: 'Nova obra',
      descricao: 'Cada obra é um centro de custo isolado.',
      campos: [
        { nome: 'nome', rotulo: 'Nome da obra', placeholder: 'Ex: Residência Alphaville' },
        { nome: 'cliente', rotulo: 'Cliente', placeholder: 'Ex: Família Souza' },
        { nome: 'endereco', rotulo: 'Endereço', placeholder: 'Rua, número, cidade' },
        { nome: 'orcamento_total', rotulo: 'Orçamento total (R$)', tipo: 'number' },
        { nome: 'data_inicio', rotulo: 'Início da obra', tipo: 'date', valor: new Date().toLocaleDateString('sv-SE') },
      ],
      rotulo: 'Criar obra',
    });
    if (!dados || !dados.nome.trim()) return;

    const nova = await api.post('/api/obras', dados);
    fecharDrawer();
    toast('Obra criada. Crie a primeira semana no Diário.', 'ok');
    estado.obraId = nova.id;
    await carregarObras();
    await navegar('diario', { forcar: true });
  };

  $('#btnVerArquivadas').onclick = async () => {
    estado.mostrarArquivadas = !estado.mostrarArquivadas;
    $('#btnVerArquivadas').textContent = estado.mostrarArquivadas
      ? 'Ocultar obras arquivadas' : 'Mostrar obras arquivadas';
    await carregarObras();
    await renderizarAtual();
  };

  $('#modalOverlay').onclick = (e) => {
    if (e.target.id === 'modalOverlay') $('#modalOverlay').classList.remove('open');
  };
  $('#lightboxFechar').onclick = () => $('#lightbox').classList.remove('open');
  $('#lightbox').onclick = (e) => { if (e.target.id === 'lightbox') $('#lightbox').classList.remove('open'); };

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    $('#modalOverlay').classList.remove('open');
    $('#lightbox').classList.remove('open');
    fecharDrawer();
  });

  const banner = $('#bannerOffline');
  const atualizarConexao = () => banner.classList.toggle('show', !navigator.onLine);
  window.addEventListener('online', () => { atualizarConexao(); renderizarAtual(); });
  window.addEventListener('offline', atualizarConexao);
  atualizarConexao();

  // recarrega ao voltar para o app depois de um tempo em segundo plano
  let saiuEm = 0;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { saiuEm = Date.now(); return; }
    if (saiuEm && Date.now() - saiuEm > 5 * 60 * 1000) renderizarAtual();
  });

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { /* PWA é opcional */ });
  }
}

registrarNavegacao({ navegar, recarregarObra, recarregarSemana, renderizarAtual });
ligarEventosGlobais();
iniciar();
