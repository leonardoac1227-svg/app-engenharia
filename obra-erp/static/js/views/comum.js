/* ==========================================================================
   Componentes usados por mais de uma view: seletor de semana e faixa de dias.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeAdministrar } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import { dataCurta, esc, formularioModal, toast } from '../util.js';

/** Seletor de semana + criação de nova semana. */
export function htmlBarraSemana() {
  if (!estado.semanas.length) {
    return `
      <div class="alerta aviso">
        <span class="alerta-ico">◷</span>
        <div>
          <b>Nenhuma semana cadastrada</b>
          <div class="small">Crie a primeira semana para começar a lançar o diário.</div>
        </div>
      </div>
      ${podeAdministrar() ? '<button class="btn btn-brass" data-acao="nova-semana">+ Criar primeira semana</button>' : ''}`;
  }

  const opcoes = estado.semanas.map((s) => `
    <option value="${s.id}"${s.id === estado.semanaId ? ' selected' : ''}>
      Semana ${s.numero} · ${dataCurta(s.data_inicio)} a ${dataCurta(s.data_fim)}${s.atual ? ' (atual)' : ''}
    </option>`).join('');

  return `
    <div class="row-between" style="margin-bottom:14px">
      <div style="flex:1;min-width:0">
        <span class="field-label" style="margin-top:0">SEMANA ATIVA</span>
        <select data-campo="semana">${opcoes}</select>
      </div>
      ${podeAdministrar() ? '<button class="btn btn-outline btn-small" data-acao="nova-semana" style="margin-bottom:1px">+ Semana</button>' : ''}
    </div>`;
}

export function ligarBarraSemana(raiz) {
  const seletor = raiz.querySelector('[data-campo=semana]');
  if (seletor) {
    seletor.onchange = async () => {
      estado.semanaId = Number(seletor.value);
      await recarregarSemana();
    };
  }

  raiz.querySelectorAll('[data-acao=nova-semana]').forEach((btn) => {
    btn.onclick = async () => {
      const ultima = estado.semanas[estado.semanas.length - 1];
      const sugestao = ultima?.data_fim
        ? new Date(new Date(ultima.data_fim).getTime() + 86400000).toLocaleDateString('sv-SE')
        : proximaSegunda();

      const dados = await formularioModal({
        titulo: 'Nova semana',
        descricao: 'A semana vai de segunda a domingo a partir da data escolhida.',
        campos: [
          { nome: 'data_inicio', rotulo: 'Início (segunda-feira)', tipo: 'date', valor: sugestao },
        ],
      });
      if (!dados) return;

      const nova = await api.post(`/api/obras/${estado.obraId}/semanas`, dados);
      estado.semanaId = nova.id;
      toast('Semana criada.', 'ok');
      await recarregarSemana({ recarregarLista: true });
    };
  });
}

function proximaSegunda() {
  const hoje = new Date();
  const diff = (1 - hoje.getDay() + 7) % 7 || 7;
  return new Date(hoje.getTime() + diff * 86400000).toLocaleDateString('sv-SE');
}

/** Faixa de dias com marcas de ponto / caixa / foto e alerta de pendência. */
export function htmlDiasStrip(diaAtivoId) {
  return `<div class="dias-strip">${estado.dias.map((d) => {
    const marcas = [
      d.tem_ponto ? '<span class="marca ponto"></span>' : '',
      d.qtd_despesas ? '<span class="marca caixa"></span>' : '',
      d.fotos ? '<span class="marca foto"></span>' : '',
    ].join('');
    const classes = ['dia-btn'];
    if (d.id === diaAtivoId) classes.push('ativo');
    if (d.hoje) classes.push('hoje');
    if (d.pendente) classes.push('pendente');
    if (!d.util) classes.push('fds');
    return `
      <button class="${classes.join(' ')}" data-dia="${d.id}" title="${esc(d.dia_semana)}">
        <span class="dia-nome">${esc(d.dia_semana.slice(0, 3))}</span>
        <span class="dia-num">${dataCurta(d.data).split('/')[0] || ''}</span>
        <span class="dia-marcas">${marcas || '<span class="marca"></span>'}</span>
      </button>`;
  }).join('')}</div>`;
}
