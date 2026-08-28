/* ==========================================================================
   PAINEL DA OBRA — a "foto da obra em 3 segundos".
   Nenhum software do setor abre direto no formulário de lançamento; abre aqui.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeFinanceiro } from '../estado.js';
import { navegar } from '../navegacao.js';
import { barraProgresso, dataBR, dataCurta, esc, esqueleto, kpi, moeda, moedaCurta, num, vazio } from '../util.js';

export async function renderizar(raiz) {
  if (!estado.obraId) {
    raiz.innerHTML = vazio('＋', 'Nenhuma obra selecionada.',
      '<button class="btn btn-brass" id="btnAbrirObras">Abrir minhas obras</button>');
    const btn = raiz.querySelector('#btnAbrirObras');
    if (btn) btn.onclick = () => document.querySelector('#btnMenu').click();
    return;
  }

  raiz.innerHTML = esqueleto(4);
  const d = await api.get(`/api/obras/${estado.obraId}/dashboard`);
  const t = d.totais || {};
  const sem = d.semana_atual;
  const financeiro = podeFinanceiro();

  const blocos = [];

  // ---- cabeçalho da obra ----
  blocos.push(`
    <div class="section-head">
      <div>
        <span class="eyebrow">PAINEL DA OBRA</span>
        <h2>${esc(d.obra.nome)}</h2>
        <p class="small muted" style="margin-top:4px">
          ${d.obra.cliente ? esc(d.obra.cliente) + ' · ' : ''}
          ${sem ? `Semana ${sem.numero} · ${dataCurta(sem.data_inicio)} a ${dataCurta(sem.data_fim)}` : 'Nenhuma semana cadastrada'}
        </p>
      </div>
    </div>`);

  // ---- pendências ----
  if (d.pendencias?.length) {
    blocos.push(`
      <div class="alerta aviso">
        <span class="alerta-ico">⚠</span>
        <div>
          <b>${d.pendencias.length} ponto(s) de atenção</b>
          <div class="small" style="margin-top:4px">${d.pendencias.map(esc).join(' · ')}</div>
        </div>
      </div>`);
  }

  // ---- KPIs ----
  const cartoes = [];
  if (financeiro) {
    cartoes.push(kpi({
      rotulo: 'Gasto acumulado', valor: moedaCurta(t.gasto_total),
      sub: t.orcamento_total ? `de ${moedaCurta(t.orcamento_total)}` : 'orçamento não informado',
      destaque: true,
    }));
    cartoes.push(kpi({
      rotulo: 'Custo da semana', valor: moedaCurta(sem?.financeiro?.custo_semana || 0),
      sub: sem?.financeiro?.orcamento_previsto
        ? `${num(sem.financeiro.percentual_orcamento, 1)}% do previsto` : 'sem meta de gasto',
      estado: sem?.financeiro?.farol === 'sem_meta' ? '' : (sem?.financeiro?.farol || ''),
    }));
    cartoes.push(kpi({
      rotulo: 'A pagar', valor: moedaCurta(t.a_pagar),
      sub: t.reembolso ? `+ ${moedaCurta(t.reembolso)} reembolso` : 'contas em aberto',
      estado: t.a_pagar > 0 ? 'amarelo' : 'verde',
    }));
  } else {
    cartoes.push(kpi({ rotulo: 'Equipe ativa', valor: num(t.equipe_ativa), sub: 'profissionais', destaque: true }));
    cartoes.push(kpi({ rotulo: 'Horas na semana', valor: num(sem?.indicadores?.total_horas, 1) + ' h', sub: `${sem?.indicadores?.dias_trabalhados || 0} dia(s)` }));
    cartoes.push(kpi({ rotulo: 'Fotos no diário', valor: num(t.total_fotos), sub: 'registro fotográfico' }));
  }
  cartoes.push(kpi({
    rotulo: 'Avanço físico',
    valor: d.avanco_fisico === null || d.avanco_fisico === undefined ? '—' : `${num(d.avanco_fisico, 1)}%`,
    sub: d.etapas?.length ? `${d.etapas.length} etapas` : 'sem cronograma',
  }));
  blocos.push(`<div class="kpi-grid">${cartoes.join('')}</div>`);

  // ---- orçamento da obra ----
  if (financeiro && t.orcamento_total > 0) {
    blocos.push(`
      <div class="card" style="margin-top:12px">
        <span class="eyebrow">ORÇAMENTO DA OBRA · PREVISTO x REALIZADO</span>
        ${barraProgresso(t.percentual_orcamento,
          `${moeda(t.gasto_total)} realizado`,
          `${num(t.percentual_orcamento, 1)}% de ${moeda(t.orcamento_total)}`)}
        <p class="small muted" style="margin-top:10px;margin-bottom:0">
          Saldo disponível: <b class="mono">${moeda(t.saldo)}</b>
          ${t.dias_de_obra !== null && t.dias_de_obra !== undefined ? ` · ${num(t.dias_de_obra)} dias de obra` : ''}
        </p>
      </div>`);
  }

  // ---- semana atual ----
  if (sem) {
    const fin = sem.financeiro || {};
    blocos.push(`
      <div class="section-head"><h3>Semana ${sem.numero}</h3>
        <button class="btn btn-outline btn-small" data-ir="diario">Abrir diário</button></div>
      <div class="card">
        <div class="row-between" style="margin-bottom:8px">
          <span class="eyebrow">METAS DA SEMANA</span>
          ${sem.metas_resumo?.total
            ? `<span class="small muted">${sem.metas_resumo.concluidas}/${sem.metas_resumo.total} concluídas</span>` : ''}
        </div>

        ${sem.metas?.length ? sem.metas.slice(0, 4).map((m) => `
          <div class="meta-linha ${m.concluida ? 'concluida' : ''}">
            <div class="meta-topo">
              <div class="meta-nome">${esc(m.nome)}
                ${m.critica ? `<span class="badge badge-vencida">${m.semanas_arrastada + 1}ª sem.</span>`
                  : (m.arrastada && !m.concluida ? '<span class="badge badge-em-aberto">arrastada</span>' : '')}
              </div>
              <div class="meta-pct">${num(m.percentual, 0)}%</div>
            </div>
            <div class="progresso-trilho">
              <div class="progresso-preenchido meta" style="width:${Math.min(m.percentual, 100)}%"></div>
            </div>
          </div>`).join('')
        : `<p style="margin-bottom:8px">${sem.meta ? esc(sem.meta) : '<span class="rdo-vazio">Nenhuma meta definida.</span>'}</p>`}
        ${sem.metas?.length > 4 ? `<p class="small muted" style="margin:8px 0 0">+${sem.metas.length - 4} outra(s) meta(s)</p>` : ''}

        ${financeiro && fin.orcamento_previsto ? barraProgresso(fin.percentual_orcamento,
            `${moeda(fin.custo_semana)} gastos`, `previsto ${moeda(fin.orcamento_previsto)}`) : ''}
        ${sem.dias_pendentes?.length ? `
          <div class="alerta perigo" style="margin-top:12px;margin-bottom:0">
            <span class="alerta-ico">●</span>
            <div><b>Dias sem lançamento:</b> ${sem.dias_pendentes.map(esc).join(', ')}</div>
          </div>` : ''}
        <div class="progresso-legenda" style="margin-top:12px">
          <span>${num(sem.indicadores?.total_horas, 1)} h lançadas</span>
          <span>${sem.indicadores?.dias_com_rdo || 0}/7 dias com RDO</span>
          <span>${sem.indicadores?.total_fotos || 0} fotos</span>
        </div>
      </div>`);
  }

  // ---- gráfico de custo por semana ----
  if (financeiro && d.serie_semanal?.length > 1) {
    const maximo = Math.max(...d.serie_semanal.map((s) => Math.max(s.custo, s.previsto)), 1);
    const colunas = d.serie_semanal.slice(-12).map((s) => {
      const altura = Math.max((s.custo / maximo) * 76, 3);
      const alturaPrevisto = s.previsto ? (s.previsto / maximo) * 76 : 0;
      const excedeu = s.previsto && s.custo > s.previsto;
      return `
        <div class="grafico-col" title="Semana ${s.numero}: ${moeda(s.custo)}">
          <div class="grafico-barra ${excedeu ? 'excedeu' : ''}" style="height:${altura}px">
            ${alturaPrevisto ? `<div class="previsto" style="bottom:${alturaPrevisto - altura}px"></div>` : ''}
          </div>
          <div class="grafico-label">S${s.numero}</div>
        </div>`;
    }).join('');
    blocos.push(`
      <div class="card">
        <span class="eyebrow">CUSTO POR SEMANA</span>
        <div class="grafico">${colunas}</div>
        <div class="progresso-legenda" style="margin-top:6px">
          <span>■ realizado</span><span>— linha verde: previsto</span>
        </div>
      </div>`);
  }

  // ---- etapas ----
  if (d.etapas?.length) {
    const linhas = d.etapas.slice(0, 8).map((e) => `
      <div style="margin-bottom:10px">
        <div class="progresso-legenda" style="margin:0 0 4px">
          <span style="color:var(--text)">${esc(e.nome)}</span>
          <span>${num(e.percentual, 0)}%${e.peso ? ` · peso ${num(e.peso, 0)}` : ''}</span>
        </div>
        <div class="progresso-trilho"><div class="progresso-preenchido" style="width:${Math.min(e.percentual, 100)}%"></div></div>
      </div>`).join('');
    blocos.push(`
      <div class="section-head"><h3>Cronograma físico</h3>
        <button class="btn btn-outline btn-small" data-ir="ajustes">Editar</button></div>
      <div class="card">${linhas}</div>`);
  }

  // ---- contas a vencer ----
  if (financeiro && d.contas_a_vencer?.length) {
    const itens = d.contas_a_vencer.map((c) => `
      <div class="item ${c.vencida ? 'alerta-vencida' : ''}">
        <div class="item-corpo">
          <div class="item-titulo">${esc(c.descricao)}</div>
          <div class="item-sub">${c.vencida ? '⚠ vencida em ' : 'vence '}${dataBR(c.vencimento)}${c.fornecedor ? ' · ' + esc(c.fornecedor) : ''}</div>
        </div>
        <div class="item-valor">${moeda(c.valor)}</div>
      </div>`).join('');
    blocos.push(`
      <div class="section-head"><h3>Contas a pagar</h3>
        <button class="btn btn-outline btn-small" data-ir="financeiro">Ver caixa</button></div>
      <div class="lista">${itens}</div>`);
  }

  // ---- renovações de aluguel ----
  if (financeiro && d.locacoes?.quantidade) {
    const itens = [...d.locacoes.vencidas, ...d.locacoes.proximas].map((p) => {
      const vencida = d.locacoes.vencidas.some((v) => v.id === p.id);
      return `
        <div class="item ${vencida ? 'alerta-vencida' : ''}">
          <div class="item-corpo">
            <div class="item-titulo">${esc(p.equipamento)}
              <span class="badge ${vencida ? 'badge-vencida' : 'badge-em-aberto'}">
                ${vencida ? 'renovação vencida' : 'renova em breve'}</span>
            </div>
            <div class="item-sub">${vencida ? 'venceu' : 'vence'} ${dataBR(p.vencimento)}
              ${p.fornecedor ? ' · ' + esc(p.fornecedor) : ''}</div>
          </div>
          <div class="item-valor">${moeda(p.valor)}</div>
        </div>`;
    }).join('');

    blocos.push(`
      <div class="section-head"><h3>Aluguéis a renovar</h3>
        <button class="btn btn-outline btn-small" data-ir="financeiro">Ver caixa</button></div>
      <div class="lista">${itens}</div>
      <p class="small faint" style="margin-top:8px">
        Equipamento já devolvido? Encerre a locação no Caixa para parar as cobranças.
      </p>`);
  }

  // ---- últimas despesas ----
  if (financeiro && d.ultimas_despesas?.length) {
    const itens = d.ultimas_despesas.map((x) => `
      <div class="item">
        <div class="item-corpo">
          <div class="item-titulo">${esc(x.descricao)}</div>
          <div class="item-sub">${dataBR(x.data)} · ${esc(x.categoria)} · ${esc(x.status_pagamento)}</div>
        </div>
        <div class="item-valor">${moeda(x.valor)}</div>
      </div>`).join('');
    blocos.push(`
      <div class="section-head"><h3>Últimos lançamentos</h3></div>
      <div class="lista">${itens}</div>`);
  }

  // ---- atalhos ----
  blocos.push(`
    <div class="btn-linha" style="margin-top:18px">
      <button class="btn btn-brass" data-ir="diario">Lançar o dia de hoje</button>
      <button class="btn btn-outline" data-ir="fechamento">Fechamento</button>
    </div>`);

  raiz.innerHTML = blocos.join('');
  raiz.querySelectorAll('[data-ir]').forEach((btn) => {
    btn.onclick = () => navegar(btn.dataset.ir);
  });
}
