/* ==========================================================================
   FECHAMENTO DA SEMANA — o documento que vai para o cliente.
   ========================================================================== */

import { api, baixar } from '../api.js';
import { estado, podeAdministrar, podeFinanceiro } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import {
  abrirLightbox, badgeStatus, barraProgresso, confirmar, dataBR, esc, esqueleto,
  kpi, moeda, moedaCurta, num, toast, tratarFotosQuebradas, vazio,
} from '../util.js';
import { htmlBarraSemana, ligarBarraSemana } from './comum.js';

export async function renderizar(raiz) {
  if (!estado.obraId) { raiz.innerHTML = vazio('＋', 'Selecione uma obra.'); return; }
  if (!estado.semanaId) {
    raiz.innerHTML = `<div class="section-head"><h2>Fechamento</h2></div>${htmlBarraSemana()}`;
    ligarBarraSemana(raiz);
    return;
  }

  raiz.innerHTML = esqueleto(5);
  const d = await api.get(`/api/semanas/${estado.semanaId}/fechamento`);
  const fin = d.financeiro || {};
  const ind = d.indicadores || {};
  const financeiro = podeFinanceiro();

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">FECHAMENTO</span><h2>Semana ${d.semana_numero}</h2>
        <p class="small muted" style="margin-top:4px">${dataBR(d.data_inicio)} a ${dataBR(d.data_fim)} · ${esc(d.obra_nome)}</p>
      </div>
      ${d.fechada ? '<span class="badge badge-reembolso">fechada</span>' : ''}
    </div>

    ${htmlBarraSemana()}

    <div class="card" id="cardResumo">
      <div class="resumo-ia">
        <div class="fonte">RESUMO EXECUTIVO</div>
        <div id="textoResumo" class="muted small">Gerando resumo da semana...</div>
      </div>
      ${financeiro ? '<button class="btn btn-ghost btn-small" data-acao="resumo-ia" style="width:100%;margin-top:10px">↻ Regerar resumo</button>' : ''}
    </div>

    <div class="kpi-grid">
      ${financeiro
        ? kpi({ rotulo: 'Custo da semana', valor: moedaCurta(fin.custo_semana),
                sub: fin.a_pagar ? `falta pagar ${moedaCurta(fin.a_pagar)}` : 'tudo pago',
                estado: fin.a_pagar ? '' : 'verde', destaque: true })
        : kpi({ rotulo: 'Horas', valor: num(ind.total_horas, 1) + ' h', sub: `${ind.dias_trabalhados} dia(s)`, destaque: true })}
      ${financeiro
        ? kpi({ rotulo: 'Horas', valor: num(ind.total_horas, 1) + ' h', sub: `${ind.dias_trabalhados} dia(s)` })
        : kpi({ rotulo: 'Dias com ponto', valor: num(ind.dias_trabalhados), sub: 'nesta semana' })}
      ${kpi({ rotulo: 'Efetivo médio', valor: num(ind.efetivo_medio, 1), sub: 'profissionais/dia' })}
      ${kpi({ rotulo: 'Fotos', valor: num(ind.total_fotos), sub: 'no registro' })}
    </div>

    ${financeiro && fin.orcamento_previsto ? `
      <div class="card" style="margin-top:12px">
        <span class="eyebrow">PREVISTO x REALIZADO</span>
        ${barraProgresso(fin.percentual_orcamento, `${moeda(fin.custo_semana)} realizado`, `${num(fin.percentual_orcamento, 1)}% de ${moeda(fin.orcamento_previsto)}`)}
        <p class="small ${fin.saldo < 0 ? '' : 'muted'}" style="margin-top:8px;margin-bottom:0;${fin.saldo < 0 ? 'color:var(--clay)' : ''}">
          ${fin.saldo < 0 ? `Estouro de ${moeda(Math.abs(fin.saldo))} no orçamento da semana.` : `Saldo de ${moeda(fin.saldo)} dentro do previsto.`}
        </p>
      </div>` : ''}

    ${d.dias_pendentes.length ? `
      <div class="alerta perigo">
        <span class="alerta-ico">●</span>
        <div><b>Dias úteis sem lançamento:</b> ${d.dias_pendentes.map(esc).join(', ')}
        <div class="small">Preencha antes de enviar o relatório ao cliente.</div></div>
      </div>` : ''}

    ${d.fotos.length ? `
      <div class="section-head"><h3>Registro fotográfico</h3><span class="small muted">${d.fotos.length} foto(s)</span></div>
      <div class="foto-grid">
        ${d.fotos.map((f) => `
          <div class="foto-item">
            <img src="${esc(f.url)}" alt="${esc(f.legenda || 'Foto da semana')}" loading="lazy">
            ${f.ambiente || f.legenda ? `<div class="foto-legenda">${esc(f.ambiente || f.legenda)}</div>` : ''}
          </div>`).join('')}
      </div>` : ''}

    ${financeiro ? `
      <div class="section-head"><h3>Quadro financeiro</h3></div>
      <div class="title-block">
        <div class="title-block-row title-block-header">
          <span>EM ABERTO</span><span>REEMBOLSO GESTOR</span>
        </div>
        <div class="title-block-row title-block-values">
          <span>${moeda(fin.em_aberto)}</span><span>${moeda(fin.reembolso)}</span>
        </div>
        <div class="title-block-row title-block-header">
          <span>TOTAL PAGO</span><span>FALTA PAGAR</span>
        </div>
        <div class="title-block-row title-block-values">
          <span>${moeda(fin.pago)}</span><span>${moeda(fin.a_pagar)}</span>
        </div>
        <div class="title-block-foot">
          OBRA: <b>${esc(d.obra_nome)}</b> · SEMANA <b>${d.semana_numero}</b> · CUSTO TOTAL <b>${moeda(fin.custo_semana)}</b>
        </div>
      </div>

      ${fin.folha ? `
        <div class="alerta ${d.folha_paga ? '' : 'aviso'}" style="margin-top:12px">
          <span class="alerta-ico">${d.folha_paga ? '✓' : '●'}</span>
          <div>
            <b>${d.folha_paga
              ? `Folha quitada${d.folha_paga_em ? ' em ' + dataBR(d.folha_paga_em) : ''}`
              : `Folha em aberto · ${moeda(fin.folha)}`}</b>
            <div class="small">${d.folha_paga
              ? 'As horas continuam registradas — o custo da semana não muda. Só o valor pendente zerou.'
              : 'Fechar a semana quita a folha automaticamente. As contas de material e locação continuam pendentes.'}</div>
          </div>
        </div>
        <button class="btn btn-outline btn-small" data-acao="folha"
                style="width:100%;margin-bottom:12px">
          ${d.folha_paga ? '↺ Reabrir folha (paguei por engano)' : '✓ Marcar folha como paga'}
        </button>` : ''}

      ${fin.folha && d.folha_detalhada.length ? `
        <div class="section-head"><h3>Folha por profissional</h3></div>
        <div class="lista">
          ${d.folha_detalhada.map((p) => `
            <div class="item">
              <div class="item-corpo"><div class="item-titulo">${esc(p.nome)}</div>
                <div class="item-sub">${esc(p.funcao || 'sem função')} · ${num(p.horas, 1)} h</div></div>
              <div class="item-valor">${moeda(p.total)}</div>
            </div>`).join('')}
        </div>` : ''}

      ${d.despesas.length ? `
        <div class="section-head"><h3>Despesas da semana</h3></div>
        <div class="lista">
          ${d.despesas.map((x) => `
            <div class="item">
              <div class="item-corpo">
                <div class="item-titulo">${esc(x.descricao)}${badgeStatus(x.status_pagamento)}</div>
                <div class="item-sub">${esc(x.dia_semana)} · ${esc(x.categoria)}${x.fornecedor ? ' · ' + esc(x.fornecedor) : ''}${x.numero_nota ? ' · NF ' + esc(x.numero_nota) : ''}${x.parcela_total ? ` · ${x.parcela_numero}/${x.parcela_total}` : ''}${x.nome_arquivo ? ' · 📎' : ''}</div>
              </div>
              <div class="item-valor">${moeda(x.valor)}</div>
            </div>`).join('')}
        </div>
        <div class="ledger-total">TOTAL EM MATERIAIS E SERVIÇOS · ${moeda(fin.despesas)}</div>` : ''}
    ` : ''}

    ${d.fretes?.viagens ? `
      <div class="section-head"><h3>Fretes</h3>
        <span class="small muted">${d.fretes.viagens} viagem(ns) · ${moeda(d.fretes.total)}</span></div>
      <div class="lista">
        ${d.fretes.transportadores.map((t) => `
          <div class="item">
            <div class="item-corpo">
              <div class="item-titulo">${esc(t.transportador)}</div>
              <div class="item-sub">${t.viagens} viagem(ns) na semana</div>
            </div>
            <div class="item-valor">${moeda(t.total)}</div>
          </div>`).join('')}
      </div>` : ''}

    <div class="tick-divider"></div>

    <div class="section-head">
      <h3>Arquivos da semana</h3>
      <span class="small muted" id="arquivosContagem">carregando...</span>
    </div>
    <div id="blocoArquivos"></div>

    <div class="tick-divider"></div>

    <div class="section-head"><h3>Exportar</h3></div>
    <button class="btn btn-brass" data-acao="pdf">⬇ Relatório em PDF (para o cliente)</button>
    ${financeiro ? `
      <button class="btn btn-outline" data-acao="planilha" style="margin-top:10px">
        📊 Relatório de pagamento (.xlsx)
      </button>
      <p class="small faint" style="margin-top:6px">
        Planilha no modelo do escritório: fornecedor, descrição, data, nº da nota e valor,
        separados por reembolso, a pagar e já pagas. Sai com uma aba por obra do mesmo período.
      </p>` : ''}
    <div class="btn-linha">
      <button class="btn btn-outline btn-small" data-acao="txt" style="flex:1">Texto (.txt)</button>
      ${financeiro ? '<button class="btn btn-outline btn-small" data-acao="csv" style="flex:1">Planilha (.csv)</button>' : ''}
    </div>
    ${podeAdministrar() ? `
      <button class="btn ${d.fechada ? 'btn-ghost' : 'btn-outline'}" data-acao="fechar" style="margin-top:10px">
        ${d.fechada ? '↺ Reabrir semana' : '✓ Marcar semana como fechada'}
      </button>` : ''}
  `;

  ligarBarraSemana(raiz);
  await carregarResumo(raiz);

  raiz.querySelector('[data-acao=pdf]').onclick = () => {
    toast('Gerando PDF...');
    baixar(`/api/semanas/${estado.semanaId}/relatorio.pdf`);
  };
  raiz.querySelector('[data-acao=txt]').onclick = () => baixar(`/api/semanas/${estado.semanaId}/relatorio.txt`);
  const btnPlanilha = raiz.querySelector('[data-acao=planilha]');
  if (btnPlanilha) {
    btnPlanilha.onclick = () => {
      toast('Gerando planilha de pagamento...');
      baixar(`/api/semanas/${estado.semanaId}/pagamentos.xlsx`);
    };
  }
  const btnCsv = raiz.querySelector('[data-acao=csv]');
  if (btnCsv) btnCsv.onclick = () => baixar(`/api/semanas/${estado.semanaId}/despesas.csv`);

  const btnResumo = raiz.querySelector('[data-acao=resumo-ia]');
  if (btnResumo) btnResumo.onclick = () => carregarResumo(raiz, true);

  const btnFolha = raiz.querySelector('[data-acao=folha]');
  if (btnFolha) {
    btnFolha.onclick = async () => {
      if (d.folha_paga) {
        await api.del(`/api/semanas/${estado.semanaId}/folha/pagar`);
        toast('Folha reaberta.');
      } else {
        const r = await api.post(`/api/semanas/${estado.semanaId}/folha/pagar`, {});
        toast(`Folha quitada · ${moeda(r.valor)}`, 'ok');
      }
      await recarregarSemana({ manterDia: true });
    };
  }

  const btnFechar = raiz.querySelector('[data-acao=fechar]');
  if (btnFechar) {
    btnFechar.onclick = async () => {
      if (!d.fechada) {
        const ok = await confirmar({
          titulo: 'Fechar a semana?',
          texto: 'Marca a semana como concluída para controle. Os lançamentos continuam editáveis.'
            + (fin.folha && !d.folha_paga
              ? `\n\nA folha de ${moeda(fin.folha)} será quitada — as horas continuam registradas, `
                + 'só o valor pendente zera. Contas de material e locação seguem em aberto.'
              : ''),
          confirmar: 'Fechar semana',
        });
        if (!ok) return;
      }
      const r = await api.put(`/api/semanas/${estado.semanaId}`, { fechada: !d.fechada });
      toast(d.fechada ? 'Semana reaberta.'
        : (r?.folha?.paga ? `Semana fechada · folha de ${moeda(r.folha.valor)} quitada` : 'Semana fechada.'), 'ok');
      await recarregarSemana({ manterDia: true });
    };
  }

  renderizarArquivos(raiz, financeiro);
  tratarFotosQuebradas(raiz);

  raiz.querySelectorAll('.foto-item img').forEach((img) => {
    img.onclick = () => abrirLightbox(img.src, img.alt);
  });
}

async function carregarResumo(raiz, forcarIa = false) {
  const alvo = raiz.querySelector('#textoResumo');
  if (!alvo) return;
  alvo.textContent = 'Gerando resumo da semana...';
  try {
    const r = await api.get(`/api/semanas/${estado.semanaId}/resumo?ia=${forcarIa ? 1 : 1}`, { silencioso: true });
    alvo.classList.remove('muted', 'small');
    alvo.textContent = r.texto;
    const fonte = raiz.querySelector('.resumo-ia .fonte');
    if (fonte) fonte.textContent = r.fonte === 'ia' ? 'RESUMO EXECUTIVO · GERADO POR IA' : 'RESUMO EXECUTIVO · AUTOMÁTICO';
  } catch (_) {
    alvo.textContent = 'Não foi possível gerar o resumo agora.';
  }
}


/* --------------------------------------------------------------------------
   Central de arquivos: tudo que foi anexado na semana num lugar só.

   Antes, a nota fiscal ficava dentro do lançamento e a foto dentro do dia —
   quando o contador pedia "as notas da semana", alguém abria despesa por
   despesa. Aqui elas aparecem juntas, com filtro e um pacote .zip para enviar
   de uma vez.
   -------------------------------------------------------------------------- */

let filtroArquivos = 'todos';

async function renderizarArquivos(raiz, financeiro) {
  const alvo = raiz.querySelector('#blocoArquivos');
  const contagem = raiz.querySelector('#arquivosContagem');
  if (!alvo) return;

  const indice = await api.get(`/api/semanas/${estado.semanaId}/arquivos`);
  contagem.textContent = indice.total
    ? `${indice.notas} nota(s) · ${indice.fotos} foto(s)` : 'nenhum arquivo';

  if (!indice.total) {
    alvo.innerHTML = `<div class="card"><p class="small muted" style="margin:0">
      Nenhum arquivo anexado nesta semana. Notas fiscais e comprovantes anexados no Caixa,
      e fotos do Diário, aparecem aqui automaticamente.</p></div>`;
    return;
  }

  const visiveis = indice.arquivos.filter((a) => filtroArquivos === 'todos' || a.tipo === filtroArquivos);

  alvo.innerHTML = `
    ${indice.notas && indice.fotos ? `
      <div class="chips" style="margin-bottom:12px">
        ${[['todos', `Todos (${indice.total})`], ['nota', `Notas (${indice.notas})`],
           ['foto', `Fotos (${indice.fotos})`]].map(([valor, rotulo]) => `
          <button class="chip ${filtroArquivos === valor ? 'ativo' : ''}" data-farq="${valor}">${rotulo}</button>`).join('')}
      </div>` : ''}

    <div class="lista">
      ${visiveis.map((a) => `
        <a class="item arquivo-item" href="${esc(a.url)}" target="_blank" rel="noopener">
          <span class="arquivo-icone ${a.tipo === 'foto' ? 'foto' : (a.eh_pdf ? 'pdf' : 'imagem')}">
            ${a.tipo === 'foto' ? '🖼' : (a.eh_pdf ? 'PDF' : 'IMG')}
          </span>
          <span class="item-corpo">
            <span class="item-titulo">${esc(a.titulo)}</span>
            <span class="item-sub">
              ${esc(a.dia_semana)} · ${dataBR(a.data)}${a.subtitulo ? ' · ' + esc(a.subtitulo) : ''}
            </span>
          </span>
          ${a.valor ? `<span class="item-valor">${moeda(a.valor)}</span>` : ''}
        </a>`).join('')}
    </div>

    ${financeiro ? `
      <button class="btn btn-outline" data-acao="zip" style="margin-top:12px">
        ⬇ Baixar todos em um .zip (${indice.total})
      </button>
      <p class="small faint" style="margin-top:6px">
        As notas saem renomeadas com data, número e fornecedor — prontas para enviar ao contador.
      </p>` : ''}
  `;

  alvo.querySelectorAll('[data-farq]').forEach((chip) => {
    chip.onclick = () => { filtroArquivos = chip.dataset.farq; renderizarArquivos(raiz, financeiro); };
  });

  const botaoZip = alvo.querySelector('[data-acao=zip]');
  if (botaoZip) {
    botaoZip.onclick = () => {
      toast('Montando o pacote...');
      baixar(`/api/semanas/${estado.semanaId}/arquivos.zip`);
    };
  }
}
