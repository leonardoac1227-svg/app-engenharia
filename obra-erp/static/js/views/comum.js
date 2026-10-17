/* ==========================================================================
   Componentes usados por mais de uma view: seletor de semana e faixa de dias.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeAdministrar } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import { confirmar, dataBR, dataCurta, esc, formularioModal, toast } from '../util.js';

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
      ${podeAdministrar() ? `
        <button class="btn btn-outline btn-small" data-acao="nova-semana" style="margin-bottom:1px">+ Semana</button>
        <button class="icon-mini" data-acao="excluir-semana" title="Excluir esta semana" aria-label="Excluir esta semana" style="margin-bottom:5px">🗑</button>` : ''}
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
      // A próxima semana começa na segunda depois da última cadastrada.
      const ultima = estado.semanas[estado.semanas.length - 1];
      const sugestao = ultima?.data_inicio ? somarDias(segundaDe(ultima.data_inicio), 7) : proximaSegunda();

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
      // O servidor sempre acerta o início para a segunda-feira daquela semana.
      const periodo = `seg ${dataCurta(nova.data_inicio)} a dom ${dataCurta(somarDias(nova.data_inicio, 6))}`;
      toast(nova.parcelas_previstas
        ? `Semana criada (${periodo}) · ${nova.parcelas_previstas} parcela(s) com vencimento nela.`
        : `Semana criada · ${periodo}.`, 'ok');
      await recarregarSemana({ recarregarLista: true });
    };
  });

  const btnExcluir = raiz.querySelector('[data-acao=excluir-semana]');
  if (btnExcluir) btnExcluir.onclick = excluirSemanaAtiva;
}

async function excluirSemanaAtiva() {
  const semana = estado.semanas.find((s) => s.id === estado.semanaId);
  if (!semana) return;

  // O que se perde, contado a partir dos dias já carregados na tela.
  const fotos = estado.dias.reduce((soma, d) => soma + (d.fotos || 0), 0);
  const lancamentos = estado.dias.reduce((soma, d) => soma + (d.qtd_despesas || 0), 0);
  const diasComPonto = estado.dias.filter((d) => d.tem_ponto).length;
  const perdas = [
    lancamentos && `${lancamentos} lançamento(s) do caixa, com os anexos`,
    fotos && `${fotos} foto(s)`,
    diasComPonto && `o ponto de ${diasComPonto} dia(s)`,
  ].filter(Boolean);

  const ok = await confirmar({
    titulo: `Excluir a semana ${semana.numero}?`,
    texto: `${dataBR(semana.data_inicio)} a ${dataBR(semana.data_fim)}.\n\n`
      + (perdas.length
        ? `Será apagado: ${perdas.join(', ')}. Isso não pode ser desfeito.`
        : 'A semana está vazia: nada foi lançado nela.')
      + '\n\nParcelas de notas de outras semanas que venciam aqui continuam em Contas a pagar.',
    confirmar: 'Excluir semana',
    perigo: true,
  });
  if (!ok) return;

  const r = await api.del(`/api/semanas/${semana.id}`);
  toast(`Semana ${semana.numero} excluída.`
    + (r?.parcelas_removidas ? ` ${r.parcelas_removidas} parcela(s) futura(s) das notas dela também.` : ''), 'ok');
  estado.semanaId = null;
  estado.diaAtivo = null;
  await recarregarSemana({ recarregarLista: true });
}

/* Datas como texto AAAA-MM-DD, contadas em UTC. Converter com new Date() e
   formatar no fuso local devolvia o dia anterior no Brasil (UTC−3): a
   "segunda 00:00 UTC" virava domingo às 21h — e a semana nova sugeria domingo. */
function somarDias(iso, dias) {
  const [a, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(Date.UTC(a, m - 1, d + dias)).toISOString().slice(0, 10);
}

function segundaDe(iso) {
  const [a, m, d] = iso.slice(0, 10).split('-').map(Number);
  const diaDaSemana = new Date(Date.UTC(a, m - 1, d)).getUTCDay();   // 0 = domingo
  return somarDias(iso, -((diaDaSemana + 6) % 7));
}

function proximaSegunda() {
  return somarDias(segundaDe(new Date().toLocaleDateString('sv-SE')), 7);
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
