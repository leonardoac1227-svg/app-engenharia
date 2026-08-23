/* ==========================================================================
   CAIXA DA OBRA — fluxo de caixa, contas a pagar e lançamentos da semana.
   Visível apenas para o papel "gestor".
   ========================================================================== */

import { api, baixar } from '../api.js';
import { estado, podeFinanceiro } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import {
  abrirLightbox, abrirModal, badgeStatus, confirmar, dataBR, esc, esqueleto, fecharModal,
  hojeISO, kpi, moeda, moedaCurta, rosca, toast, vazio,
} from '../util.js';
import { htmlBarraSemana, ligarBarraSemana } from './comum.js';

const CATEGORIAS = ['Material', 'Serviço', 'Locação', 'Frete', 'Alimentação', 'Outro'];
const STATUS = ['Reembolso', 'Em Aberto', 'Pago'];

let filtroStatus = 'todos';

/** Modal de lançamento/edição de despesa — usado no diário e nesta view. */
export function abrirModalDespesa(dia, aoSalvar, despesa = null) {
  const edicao = !!despesa;
  const statusInicial = despesa?.status_pagamento || 'Em Aberto';

  const opcoesDia = estado.dias.map((d) => `
    <option value="${d.id}"${d.id === dia?.id ? ' selected' : ''}>${esc(d.dia_semana)} · ${dataBR(d.data)}</option>`).join('');

  abrirModal(`
    <h3>${edicao ? 'Editar lançamento' : 'Lançar despesa'}</h3>

    ${edicao ? '' : `<label class="field-label">Dia do lançamento</label><select id="dspDia">${opcoesDia}</select>`}

    <label class="field-label">Categoria</label>
    <select id="dspCategoria">
      ${CATEGORIAS.map((c) => `<option${c === (despesa?.categoria || 'Material') ? ' selected' : ''}>${esc(c)}</option>`).join('')}
    </select>

    <label class="field-label">Descrição</label>
    <input type="text" id="dspDescricao" value="${esc(despesa?.descricao || '')}" placeholder="Ex: 20 sacos de cimento CP-II">

    <label class="field-label">Fornecedor</label>
    <input type="text" id="dspFornecedor" value="${esc(despesa?.fornecedor || '')}" placeholder="Ex: Casa do Construtor">

    <label class="field-label">Valor</label>
    <div class="input-moeda"><input type="number" id="dspValor" inputmode="decimal" step="0.01" min="0"
      value="${despesa?.valor || ''}" placeholder="0,00"></div>

    <label class="field-label">Status do pagamento</label>
    <div class="segmented" id="dspStatus">
      ${STATUS.map((s) => `<button type="button" data-status="${s}" class="${s === statusInicial ? 'ativo' : ''}">${s}</button>`).join('')}
    </div>

    <div id="dspVencimentoBox" class="${statusInicial === 'Em Aberto' ? '' : 'hidden'}">
      <label class="field-label">Vencimento</label>
      <input type="date" id="dspVencimento" value="${esc(despesa?.vencimento || '')}">
    </div>

    ${estado.etapas.length ? `
      <label class="field-label">Etapa do cronograma</label>
      <select id="dspEtapa">
        <option value="">— sem vínculo —</option>
        ${estado.etapas.map((e) => `<option value="${e.id}"${String(e.id) === String(despesa?.etapa_id || '') ? ' selected' : ''}>${esc(e.nome)}</option>`).join('')}
      </select>` : ''}

    ${edicao ? '' : `
      <label class="field-label">Nota fiscal / comprovante</label>
      <input type="file" id="dspAnexo" accept="image/*,application/pdf">`}

    <div class="btn-linha">
      <button class="btn btn-outline" id="dspCancelar">Cancelar</button>
      <button class="btn btn-brass" id="dspSalvar">${edicao ? 'Salvar' : 'Lançar'}</button>
    </div>
  `, (raiz) => {
    let status = statusInicial;
    raiz.querySelectorAll('#dspStatus button').forEach((btn) => {
      btn.onclick = () => {
        status = btn.dataset.status;
        raiz.querySelectorAll('#dspStatus button').forEach((b) => b.classList.toggle('ativo', b === btn));
        raiz.querySelector('#dspVencimentoBox').classList.toggle('hidden', status !== 'Em Aberto');
      };
    });

    raiz.querySelector('#dspCancelar').onclick = fecharModal;

    raiz.querySelector('#dspSalvar').onclick = async () => {
      const descricao = raiz.querySelector('#dspDescricao').value.trim();
      const valor = parseFloat(raiz.querySelector('#dspValor').value);
      if (!descricao) { toast('Informe a descrição.', 'erro'); return; }
      if (!valor || valor <= 0) { toast('Informe um valor válido.', 'erro'); return; }

      const botao = raiz.querySelector('#dspSalvar');
      botao.disabled = true;
      try {
        if (edicao) {
          await api.put(`/api/despesas/${despesa.id}`, {
            categoria: raiz.querySelector('#dspCategoria').value,
            descricao,
            fornecedor: raiz.querySelector('#dspFornecedor').value.trim(),
            valor,
            status_pagamento: status,
            vencimento: status === 'Em Aberto' ? raiz.querySelector('#dspVencimento').value : '',
            etapa_id: raiz.querySelector('#dspEtapa')?.value || null,
          });
        } else {
          const diaId = raiz.querySelector('#dspDia')?.value || dia.id;
          const dados = new FormData();
          dados.append('categoria', raiz.querySelector('#dspCategoria').value);
          dados.append('descricao', descricao);
          dados.append('fornecedor', raiz.querySelector('#dspFornecedor').value.trim());
          dados.append('valor', valor);
          dados.append('status_pagamento', status);
          if (status === 'Em Aberto') dados.append('vencimento', raiz.querySelector('#dspVencimento').value || '');
          const etapa = raiz.querySelector('#dspEtapa')?.value;
          if (etapa) dados.append('etapa_id', etapa);
          const arquivo = raiz.querySelector('#dspAnexo')?.files[0];
          if (arquivo) dados.append('imagem', arquivo);
          await api.form(`/api/dias/${diaId}/despesas`, dados);
        }
        fecharModal();
        toast(edicao ? 'Lançamento atualizado.' : 'Despesa lançada.', 'ok');
        if (aoSalvar) await aoSalvar();
      } finally { botao.disabled = false; }
    };

    setTimeout(() => raiz.querySelector('#dspDescricao').focus(), 150);
  });
}

export async function renderizar(raiz) {
  if (!podeFinanceiro()) {
    raiz.innerHTML = vazio('🔒', 'Seu perfil não tem acesso ao módulo financeiro.');
    return;
  }
  if (!estado.obraId) { raiz.innerHTML = vazio('＋', 'Selecione uma obra.'); return; }
  if (!estado.semanas.length) {
    raiz.innerHTML = `<div class="section-head"><h2>Caixa</h2></div>${htmlBarraSemana()}`;
    ligarBarraSemana(raiz);
    return;
  }

  raiz.innerHTML = esqueleto(4);
  const [painel, fechamento] = await Promise.all([
    api.get(`/api/obras/${estado.obraId}/financeiro`),
    api.get(`/api/semanas/${estado.semanaId}/fechamento`),
  ]);

  const fin = fechamento.financeiro;
  const despesas = filtroStatus === 'todos'
    ? fechamento.despesas
    : fechamento.despesas.filter((x) => x.status_pagamento === filtroStatus);

  const listaConta = (itens, titulo, aviso) => `
    <div class="section-head"><h3>${esc(titulo)}</h3></div>
    <div class="lista">${itens.map((x) => `
      <div class="item ${aviso ? 'alerta-vencida' : ''}">
        <div class="item-corpo">
          <div class="item-titulo">${esc(x.descricao)}</div>
          <div class="item-sub">${x.vencimento ? (aviso ? 'venceu ' : 'vence ') + dataBR(x.vencimento) : 'sem vencimento'}${x.fornecedor ? ' · ' + esc(x.fornecedor) : ''}</div>
        </div>
        <div class="item-valor">${moeda(x.valor)}</div>
        <div class="item-acoes"><button class="icon-mini" data-pagar="${x.id}" title="Marcar como pago">✓</button></div>
      </div>`).join('')}</div>`;

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">FIN · CAIXA DA OBRA</span><h2>Financeiro</h2></div>
      <button class="btn btn-brass btn-small" data-acao="nova-despesa">+ Lançar</button>
    </div>

    <div class="kpi-grid">
      ${kpi({ rotulo: 'Em aberto', valor: moedaCurta(painel.total_em_aberto), sub: 'contas a pagar', estado: painel.total_em_aberto ? 'amarelo' : 'verde', destaque: true })}
      ${kpi({ rotulo: 'Vencido', valor: moedaCurta(painel.total_vencido), sub: `${painel.vencidas.length} conta(s)`, estado: painel.total_vencido ? 'vermelho' : 'verde' })}
      ${kpi({ rotulo: 'Vence em 7 dias', valor: moedaCurta(painel.total_proximas), sub: `${painel.proximas.length} conta(s)` })}
      ${kpi({ rotulo: 'Custo da semana', valor: moedaCurta(fin.custo_semana), sub: `folha ${moedaCurta(fin.folha)}` })}
    </div>

    ${painel.vencidas.length ? listaConta(painel.vencidas, '⚠ Contas vencidas', true) : ''}
    ${painel.proximas.length ? listaConta(painel.proximas, 'Vencendo esta semana', false) : ''}

    <div class="tick-divider"></div>
    ${htmlBarraSemana()}

    <div class="chips" style="margin-bottom:12px">
      ${['todos', ...STATUS].map((s) => `
        <button class="chip ${filtroStatus === s ? 'ativo' : ''}" data-filtro="${esc(s)}">${s === 'todos' ? 'Todos' : esc(s)}</button>`).join('')}
    </div>

    <div class="lista">
      ${despesas.length ? despesas.map((x) => `
        <div class="item ${x.status_pagamento === 'Em Aberto' && x.vencimento && x.vencimento < hojeISO() ? 'alerta-vencida' : ''}">
          <div class="item-corpo">
            <div class="item-titulo">${esc(x.descricao)}${badgeStatus(x.status_pagamento, x.status_pagamento === 'Em Aberto' && x.vencimento && x.vencimento < hojeISO())}</div>
            <div class="item-sub">
              ${dataBR(x.data)} · ${esc(x.categoria)}${x.fornecedor ? ' · ' + esc(x.fornecedor) : ''}
              ${x.etapa_nome ? ' · ' + esc(x.etapa_nome) : ''}
            </div>
            ${x.anexo_url ? `<div class="item-sub"><a href="#" data-anexo="${esc(x.anexo_url)}" data-nome="${esc(x.nome_arquivo || 'anexo')}">📎 ${esc(x.nome_arquivo || 'ver anexo')}</a></div>` : ''}
          </div>
          <div class="item-valor">${moeda(x.valor)}</div>
          <div class="item-acoes">
            <button class="icon-mini" data-editar="${x.id}" title="Editar">✎</button>
            <button class="icon-mini" data-excluir="${x.id}" title="Excluir">🗑</button>
          </div>
        </div>`).join('') : '<p class="vazio-inline">Nenhum lançamento com esse filtro nesta semana.</p>'}
    </div>

    <div class="ledger-total">SEMANA ${fechamento.semana_numero} · ${moeda(fin.despesas)} em materiais e serviços</div>

    ${painel.por_categoria.length ? `
      <div class="section-head"><h3>Onde o dinheiro foi</h3><span class="small muted">obra toda</span></div>
      <div class="card">
        ${rosca(
          painel.por_categoria.map((c) => ({ rotulo: c.categoria, valor: c.total })),
          { titulo: 'GASTO', formatar: moedaCurta },
        )}
      </div>` : ''}

    ${painel.maiores_fornecedores.length > 1 ? `
      <div class="section-head"><h3>Maiores fornecedores</h3></div>
      <div class="lista">
        ${painel.maiores_fornecedores.map((f) => `
          <div class="item">
            <div class="item-corpo"><div class="item-titulo">${esc(f.fornecedor)}</div>
              <div class="item-sub">${f.itens} lançamento(s)</div></div>
            <div class="item-valor">${moeda(f.total)}</div>
          </div>`).join('')}
      </div>` : ''}

    <button class="btn btn-outline" data-acao="csv">⬇ Exportar despesas da semana (.csv)</button>
  `;

  ligarBarraSemana(raiz);

  raiz.querySelectorAll('[data-filtro]').forEach((chip) => {
    chip.onclick = () => { filtroStatus = chip.dataset.filtro; renderizar(raiz); };
  });

  const recarregar = async () => { await recarregarSemana({ manterDia: true }); };

  raiz.querySelector('[data-acao=nova-despesa]').onclick = () => {
    const dia = estado.dias.find((d) => d.hoje) || estado.diaAtivo || estado.dias[0];
    abrirModalDespesa(dia, recarregar);
  };

  raiz.querySelectorAll('[data-pagar]').forEach((btn) => {
    btn.onclick = async () => {
      await api.post(`/api/despesas/${btn.dataset.pagar}/pagar`, {});
      toast('Conta baixada como paga.', 'ok');
      await recarregar();
    };
  });

  raiz.querySelectorAll('[data-editar]').forEach((btn) => {
    btn.onclick = () => {
      const despesa = fechamento.despesas.find((x) => String(x.id) === btn.dataset.editar);
      abrirModalDespesa(null, recarregar, despesa);
    };
  });

  raiz.querySelectorAll('[data-excluir]').forEach((btn) => {
    btn.onclick = async () => {
      const ok = await confirmar({ titulo: 'Excluir este lançamento?', texto: 'O anexo também será apagado.', perigo: true, confirmar: 'Excluir' });
      if (!ok) return;
      await api.del(`/api/despesas/${btn.dataset.excluir}`);
      toast('Lançamento removido.');
      await recarregar();
    };
  });

  raiz.querySelectorAll('[data-anexo]').forEach((link) => {
    link.onclick = (e) => {
      e.preventDefault();
      const url = link.dataset.anexo;
      if (/\.pdf$/i.test(link.dataset.nome)) { window.open(url, '_blank', 'noopener'); return; }
      abrirLightbox(url, link.dataset.nome);
    };
  });

  raiz.querySelector('[data-acao=csv]').onclick = () => baixar(`/api/semanas/${estado.semanaId}/despesas.csv`);
}
