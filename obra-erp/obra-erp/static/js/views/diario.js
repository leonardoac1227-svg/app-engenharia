/* ==========================================================================
   DIÁRIO DE OBRA (RDO) — produção, clima, fotos, medições, ponto e caixa
   do dia selecionado.
   ========================================================================== */

import { api } from '../api.js';
import { comprimirLote } from '../compressao.js';
import { estado, limparRascunho, lerRascunho, podeFinanceiro, podeLancar, salvarRascunho } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import {
  abrirLightbox, badgeStatus, CLIMAS, confirmar, dataBR, debounce, esc, formularioModal,
  moeda, num, toast, tratarFotosQuebradas, vazio,
} from '../util.js';
import { htmlBarraSemana, htmlDiasStrip, ligarBarraSemana } from './comum.js';
import { abrirModalDespesa } from './financeiro.js';

let cacheDia = { horas: {}, despesas: [], medicoes: [], metas: [] };

function diaSelecionado() {
  if (!estado.dias.length) return null;
  if (estado.diaAtivo) {
    const atual = estado.dias.find((d) => d.id === estado.diaAtivo.id);
    if (atual) return atual;
  }
  return estado.dias.find((d) => d.hoje) || estado.dias.find((d) => d.util) || estado.dias[0];
}

async function carregarDadosDoDia(dia) {
  const pedidos = [
    api.get(`/api/dias/${dia.id}/horas`),
    api.get(`/api/dias/${dia.id}/medicoes`),
    podeFinanceiro() ? api.get(`/api/dias/${dia.id}/despesas`) : Promise.resolve([]),
    api.get(`/api/dias/${dia.id}/metas`),
  ];
  const [horas, medicoes, despesas, metas] = await Promise.all(pedidos);
  cacheDia = { horas, medicoes, despesas, metas };
}

export async function renderizar(raiz) {
  if (!estado.obraId) {
    raiz.innerHTML = vazio('＋', 'Selecione uma obra para lançar o diário.');
    return;
  }
  if (!estado.semanas.length) {
    raiz.innerHTML = `<div class="section-head"><h2>Diário de obra</h2></div>${htmlBarraSemana()}`;
    ligarBarraSemana(raiz);
    return;
  }

  const dia = diaSelecionado();
  estado.diaAtivo = dia;
  if (!dia) { raiz.innerHTML = vazio('◷', 'Semana sem dias cadastrados.'); return; }

  await carregarDadosDoDia(dia);

  const rascunho = lerRascunho(dia.id);
  const producao = rascunho?.producao_dia ?? dia.producao_dia ?? '';
  const observacoes = rascunho?.observacoes ?? dia.observacoes ?? '';
  const clima = rascunho?.clima ?? dia.clima ?? CLIMAS[0];
  const temRascunho = !!rascunho && (rascunho.producao_dia !== dia.producao_dia || rascunho.observacoes !== dia.observacoes);
  const somenteLeitura = !podeLancar();

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">DOC A · DIÁRIO DE CANTEIRO</span><h2>${esc(dia.dia_semana)}, ${dataBR(dia.data)}</h2></div>
    </div>

    ${htmlBarraSemana()}
    ${htmlDiasStrip(dia.id)}

    <div class="card tight" style="margin-top:10px">
      <div class="row-between">
        <div>
          <div class="kpi-label">SITUAÇÃO DO DIA</div>
          <div class="small" style="margin-top:4px">
            ${dia.tem_rdo ? '✓ RDO preenchido' : '○ sem RDO'} ·
            ${dia.tem_ponto ? `✓ ${num(dia.horas, 1)} h` : '○ sem ponto'} ·
            ${dia.fotos ? `📷 ${dia.fotos}` : '○ sem fotos'}
          </div>
        </div>
        ${dia.assinado
          ? `<span class="badge badge-reembolso" title="${esc(dia.assinado_em || '')}">assinado</span>`
          : (somenteLeitura ? '' : '<button class="btn btn-outline btn-small" data-acao="assinar">Assinar RDO</button>')}
      </div>
      ${dia.assinado ? `<div class="item-sub" style="margin-top:6px">Assinado por ${esc(dia.assinado_por)} em ${dataBR((dia.assinado_em || '').slice(0, 10))}</div>` : ''}
    </div>

    ${temRascunho ? `<div class="alerta aviso"><span class="alerta-ico">✎</span>
      <div><b>Rascunho local restaurado</b><div class="small">Havia texto não enviado neste dia. Confira e salve.</div></div></div>` : ''}

    <!-- ===================== RDO ===================== -->
    <details class="accordion" open>
      <summary>
        <span class="acc-code">RDO-01</span>
        <span class="acc-title">Avanço físico</span>
        <span class="acc-status">${dia.tem_rdo ? '✓' : ''}</span>
        <span class="acc-chevron">›</span>
      </summary>
      <div class="acc-body">
        <label class="field-label" for="inputProducao">Produção do dia</label>
        <textarea id="inputProducao" rows="3" ${somenteLeitura ? 'disabled' : ''}
          placeholder="Ex: levante de alvenaria concluído no 2º pavimento">${esc(producao)}</textarea>

        <label class="field-label" for="selectClima">Clima</label>
        <select id="selectClima" ${somenteLeitura ? 'disabled' : ''}>
          ${CLIMAS.map((c) => `<option${c === clima ? ' selected' : ''}>${esc(c)}</option>`).join('')}
          ${clima && !CLIMAS.includes(clima) ? `<option selected>${esc(clima)}</option>` : ''}
        </select>

        <label class="field-label" for="inputObs">Observações e ocorrências</label>
        <textarea id="inputObs" rows="2" ${somenteLeitura ? 'disabled' : ''}
          placeholder="Ex: falta de material, atraso de fornecedor, visita da fiscalização...">${esc(observacoes)}</textarea>

        <label class="field-label">Registro fotográfico</label>
        <div class="foto-grid" id="gridFotos">
          ${(dia.fotos_lista || []).map((f) => `
            <div class="foto-item" data-foto="${f.id}">
              <img src="${esc(f.url)}" alt="${esc(f.legenda || 'Foto do dia')}" loading="lazy">
              ${f.legenda || f.ambiente ? `<div class="foto-legenda">${esc(f.ambiente || f.legenda)}</div>` : ''}
              ${somenteLeitura ? '' : `<button class="foto-remover" data-remover-foto="${f.id}" aria-label="Remover foto">✕</button>`}
            </div>`).join('')}
          ${somenteLeitura ? '' : `
            <button class="foto-add" id="btnAddFoto"><span>＋</span><span>ADICIONAR</span></button>`}
        </div>

        ${somenteLeitura ? '' : `
          <div class="btn-linha" style="margin-top:0">
            <button class="btn btn-outline btn-small" id="btnCamera" style="flex:1">📷 Tirar foto</button>
            <button class="btn btn-outline btn-small" id="btnGaleria" style="flex:1">🖼 Galeria / arquivos</button>
          </div>`}

        <!-- dois inputs: com capture, o Android abre direto a câmera; sem
             capture, abre a galeria e o gerenciador de arquivos -->
        <input type="file" id="inputCamera" accept="image/*" capture="environment" multiple class="hidden">
        <input type="file" id="inputFotos" accept="image/*" multiple class="hidden">

        ${somenteLeitura ? '' : '<button class="btn btn-brass" data-acao="salvar-rdo">Salvar RDO</button>'}
      </div>
    </details>

    <!-- ===================== METAS ===================== -->
    <details class="accordion" ${cacheDia.metas.some((m) => !m.concluida) ? 'open' : ''}>
      <summary>
        <span class="acc-code">MET-02</span>
        <span class="acc-title">Metas da semana</span>
        <span class="acc-status">${cacheDia.metas.length
          ? `${cacheDia.metas.filter((m) => m.concluida).length}/${cacheDia.metas.length}` : ''}</span>
        <span class="acc-chevron">›</span>
      </summary>
      <div class="acc-body">
        ${cacheDia.metas.length ? cacheDia.metas.map((m) => `
          <div class="meta-linha ${m.concluida ? 'concluida' : ''}" data-meta="${m.id}">
            <div class="meta-topo">
              <div class="meta-nome">
                ${esc(m.nome)}
                ${m.critica ? `<span class="badge badge-vencida">${m.semanas_arrastada + 1}ª semana</span>`
                  : (m.arrastada ? `<span class="badge badge-em-aberto">arrastada</span>` : '')}
                ${m.concluida ? '<span class="badge badge-pago">concluída</span>' : ''}
              </div>
              <div class="meta-pct" data-pct="${m.id}">${num(m.percentual_dia, 0)}%</div>
            </div>
            <div class="progresso-trilho">
              <div class="progresso-preenchido meta" data-barra="${m.id}"
                   style="width:${Math.min(m.percentual_dia, 100)}%"></div>
            </div>
            ${somenteLeitura ? '' : `
              <input type="range" class="meta-range" data-range="${m.id}" min="0" max="100" step="5"
                     value="${m.percentual_dia}" aria-label="Percentual de ${esc(m.nome)}">
              <div class="meta-acoes">
                <button class="btn btn-ghost btn-small" data-meta-obs="${m.id}">+ observação</button>
                <button class="btn btn-outline btn-small" data-meta-100="${m.id}">Concluir</button>
                <button class="btn btn-brass btn-small" data-meta-salvar="${m.id}">Salvar</button>
              </div>`}
            ${m.marcado_hoje ? `<div class="item-sub">✓ marcada neste dia${m.observacao_dia ? ' · ' + esc(m.observacao_dia) : ''}</div>` : ''}
          </div>`).join('') : '<p class="vazio-inline">Nenhuma meta definida para esta semana.</p>'}

        ${somenteLeitura ? '' : '<button class="btn btn-outline" data-acao="nova-meta">+ Nova meta</button>'}
        <p class="small faint" style="margin-top:10px;margin-bottom:0">
          Meta não concluída passa sozinha para a próxima semana, com contador de arrasto.
          Meta em 100% sai da lista e fica registrada no relatório desta semana.
        </p>
      </div>
    </details>

    <!-- ===================== MEDIÇÕES ===================== -->
    <details class="accordion">
      <summary>
        <span class="acc-code">MED-03</span>
        <span class="acc-title">Medições de serviço</span>
        <span class="acc-status">${cacheDia.medicoes.length || ''}</span>
        <span class="acc-chevron">›</span>
      </summary>
      <div class="acc-body">
        <p class="small muted">Quantidades executadas no dia (m², m³, m linear...). É o que
        alimenta o previsto x realizado físico e atende à fiscalização.</p>
        <div class="lista" id="listaMedicoes">
          ${cacheDia.medicoes.length ? cacheDia.medicoes.map((m) => `
            <div class="item">
              <div class="item-corpo">
                <div class="item-titulo">${esc(m.descricao)}</div>
                <div class="item-sub">${m.etapa_nome ? esc(m.etapa_nome) : 'sem etapa vinculada'}</div>
              </div>
              <div class="item-valor">${num(m.quantidade, 2)} ${esc(m.unidade)}</div>
              ${somenteLeitura ? '' : `<div class="item-acoes"><button class="icon-mini" data-remover-medicao="${m.id}">🗑</button></div>`}
            </div>`).join('') : '<p class="vazio-inline">Nenhuma medição registrada neste dia.</p>'}
        </div>
        ${somenteLeitura ? '' : '<button class="btn btn-outline" data-acao="nova-medicao">+ Registrar medição</button>'}
      </div>
    </details>

    <!-- ===================== PONTO ===================== -->
    <details class="accordion">
      <summary>
        <span class="acc-code">FOL-04</span>
        <span class="acc-title">Ponto da equipe</span>
        <span class="acc-status">${dia.tem_ponto ? `${num(dia.horas, 1)} h` : ''}</span>
        <span class="acc-chevron">›</span>
      </summary>
      <div class="acc-body">
        ${estado.funcionarios.length ? `
          ${somenteLeitura ? '' : `
            <div class="btn-linha" style="margin-top:4px">
              <button class="btn btn-ghost btn-small" data-acao="repetir-ponto" style="flex:1">↺ Repetir último dia</button>
              <button class="btn btn-ghost btn-small" data-acao="jornada-padrao" style="flex:1">8h para todos</button>
            </div>` }
          <div class="lista" id="listaPonto" style="margin-top:12px">
            ${estado.funcionarios.map((f) => {
              const registro = cacheDia.horas[String(f.id)];
              const horas = registro ? registro.horas : 0;
              const valorHora = registro?.valor_hora ?? f.valor_hora;
              return `
                <div class="staff-row">
                  <div class="staff-view">
                    <div class="staff-info">
                      <div class="staff-name">${esc(f.nome)}</div>
                      <div class="staff-meta">${esc(f.funcao || 'sem função')}${valorHora !== null && valorHora !== undefined ? ` · ${moeda(valorHora)}/h` : ''}</div>
                    </div>
                    <div class="contador">
                      <button type="button" data-passo="-1" ${somenteLeitura ? 'disabled' : ''}>−</button>
                      <input type="number" class="campo-horas" min="0" max="24" step="0.5"
                             value="${horas || ''}" placeholder="0" inputmode="decimal"
                             data-func="${f.id}" data-valor="${valorHora || 0}" ${somenteLeitura ? 'disabled' : ''}>
                      <button type="button" data-passo="1" ${somenteLeitura ? 'disabled' : ''}>+</button>
                    </div>
                  </div>
                </div>`;
            }).join('')}
          </div>
          ${podeFinanceiro() ? '<div class="ledger-total" id="totalFolhaDia"></div>' : ''}
          ${somenteLeitura ? '' : '<button class="btn btn-brass" data-acao="salvar-ponto">Salvar ponto do dia</button>'}
        ` : vazio('☰', 'Nenhum profissional cadastrado nesta obra.',
            '<button class="btn btn-outline" data-ir-equipe="1">Cadastrar equipe</button>')}
      </div>
    </details>

    <!-- ===================== CAIXA DO DIA ===================== -->
    ${podeFinanceiro() ? `
    <details class="accordion">
      <summary>
        <span class="acc-code">FIN-05</span>
        <span class="acc-title">Caixa do dia</span>
        <span class="acc-status">${cacheDia.despesas.length ? moeda(cacheDia.despesas.reduce((s, x) => s + x.valor, 0)) : ''}</span>
        <span class="acc-chevron">›</span>
      </summary>
      <div class="acc-body">
        <div class="lista">
          ${cacheDia.despesas.length ? cacheDia.despesas.map((x) => `
            <div class="item ${x.vencida ? 'alerta-vencida' : ''}">
              <div class="item-corpo">
                <div class="item-titulo">${esc(x.descricao)}${badgeStatus(x.status_pagamento, x.vencida)}</div>
                <div class="item-sub">${esc(x.categoria)}${x.fornecedor ? ' · ' + esc(x.fornecedor) : ''}${x.nome_arquivo ? ' · 📎 ' + esc(x.nome_arquivo) : ''}</div>
              </div>
              <div class="item-valor">${moeda(x.valor)}</div>
              <div class="item-acoes"><button class="icon-mini" data-remover-despesa="${x.id}">🗑</button></div>
            </div>`).join('') : '<p class="vazio-inline">Nenhum lançamento neste dia.</p>'}
        </div>
        <button class="btn btn-brass" data-acao="nova-despesa">+ Lançar despesa</button>
      </div>
    </details>` : ''}
  `;

  ligarEventos(raiz, dia);
}

function ligarEventos(raiz, dia) {
  ligarBarraSemana(raiz);

  raiz.querySelectorAll('[data-dia]').forEach((btn) => {
    btn.onclick = async () => {
      estado.diaAtivo = estado.dias.find((d) => d.id === Number(btn.dataset.dia));
      await renderizar(raiz);
      raiz.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  });

  const irEquipe = raiz.querySelector('[data-ir-equipe]');
  if (irEquipe) irEquipe.onclick = () => import('../navegacao.js').then((m) => m.navegar('equipe'));

  // ---------------- RDO
  const campos = ['#inputProducao', '#selectClima', '#inputObs'].map((s) => raiz.querySelector(s));
  const guardarRascunho = debounce(() => {
    salvarRascunho(dia.id, {
      producao_dia: campos[0].value, clima: campos[1].value, observacoes: campos[2].value,
    });
  }, 600);
  campos.forEach((c) => c && c.addEventListener('input', guardarRascunho));

  // ---------------------------------------------------------------- metas
  const observacoesMeta = {};

  raiz.querySelectorAll('[data-range]').forEach((range) => {
    const id = range.dataset.range;
    range.oninput = () => {
      raiz.querySelector(`[data-pct="${id}"]`).textContent = `${range.value}%`;
      raiz.querySelector(`[data-barra="${id}"]`).style.width = `${range.value}%`;
    };
  });

  raiz.querySelectorAll('[data-meta-100]').forEach((btn) => {
    btn.onclick = () => {
      const id = btn.dataset.meta100;
      const range = raiz.querySelector(`[data-range="${id}"]`);
      range.value = 100;
      range.dispatchEvent(new Event('input'));
    };
  });

  raiz.querySelectorAll('[data-meta-obs]').forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.metaObs;
      const dados = await formularioModal({
        titulo: 'Observação do dia',
        descricao: 'Aparece no relatório junto do percentual — útil para explicar por que a meta travou.',
        campos: [{ nome: 'observacao', rotulo: 'O que aconteceu', tipo: 'textarea',
                   valor: observacoesMeta[id] || '',
                   placeholder: 'Ex: faltou massa, equipe deslocada para a laje' }],
      });
      if (!dados) return;
      observacoesMeta[id] = dados.observacao;
      btn.textContent = dados.observacao ? '✎ observação' : '+ observação';
    };
  });

  raiz.querySelectorAll('[data-meta-salvar]').forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.metaSalvar;
      const percentual = Number(raiz.querySelector(`[data-range="${id}"]`).value);
      btn.disabled = true;
      try {
        const meta = await api.post(`/api/dias/${dia.id}/metas/${id}/progresso`, {
          percentual, observacao: observacoesMeta[id] || '',
        });
        toast(meta.concluida ? `Meta concluída: ${meta.nome}` : 'Progresso salvo.', 'ok');
        await recarregarSemana({ manterDia: true });
      } finally { btn.disabled = false; }
    };
  });

  const btnNovaMeta = raiz.querySelector('[data-acao=nova-meta]');
  if (btnNovaMeta) {
    btnNovaMeta.onclick = async () => {
      const dados = await formularioModal({
        titulo: 'Nova meta da semana',
        descricao: 'Dê um nome objetivo e mensurável — é o que o cliente vai ler no relatório.',
        campos: [
          { nome: 'nome', rotulo: 'Meta', placeholder: 'Ex: Reboco interno do 2º pavimento' },
          { nome: 'descricao', rotulo: 'Detalhe (opcional)', placeholder: 'Ex: exceto banheiros' },
          { nome: 'percentual', rotulo: 'Já executado (%)', tipo: 'number', valor: 0 },
        ],
        rotulo: 'Criar meta',
      });
      if (!dados || !dados.nome.trim()) return;
      await api.post(`/api/semanas/${estado.semanaId}/metas`, dados);
      toast('Meta criada.', 'ok');
      await recarregarSemana({ manterDia: true });
    };
  }

  const btnSalvarRdo = raiz.querySelector('[data-acao=salvar-rdo]');
  if (btnSalvarRdo) {
    btnSalvarRdo.onclick = async () => {
      btnSalvarRdo.disabled = true;
      try {
        await api.put(`/api/dias/${dia.id}`, {
          producao_dia: campos[0].value, clima: campos[1].value, observacoes: campos[2].value,
        });
        limparRascunho(dia.id);
        toast('RDO salvo.', 'ok');
        await recarregarSemana({ manterDia: true });
      } finally { btnSalvarRdo.disabled = false; }
    };
  }

  const btnAssinar = raiz.querySelector('[data-acao=assinar]');
  if (btnAssinar) {
    btnAssinar.onclick = async () => {
      const ok = await confirmar({
        titulo: 'Assinar o RDO deste dia?',
        texto: 'Seu nome e a data/hora ficam registrados no relatório como responsável pelo conteúdo lançado.',
        confirmar: 'Assinar',
      });
      if (!ok) return;
      await api.post(`/api/dias/${dia.id}/assinatura`, {});
      toast('RDO assinado.', 'ok');
      await recarregarSemana({ manterDia: true });
    };
  }

  tratarFotosQuebradas(raiz);

  // ---------------- fotos
  const inputFotos = raiz.querySelector('#inputFotos');
  const inputCamera = raiz.querySelector('#inputCamera');
  const btnAddFoto = raiz.querySelector('#btnAddFoto');

  const enviarFotos = async (lista, botao) => {
    if (!lista.length) return;
    const rotuloOriginal = botao?.textContent;

    if (botao) botao.textContent = 'preparando...';
    // Comprime antes de subir: no 4G do canteiro, é a diferença entre
    // segundos e minutos de espera.
    const { arquivos, resumo, houveGanho } = await comprimirLote(
      Array.from(lista).slice(0, 10));

    const dados = new FormData();
    arquivos.forEach((arquivo) => dados.append('fotos', arquivo));
    const ambiente = raiz.querySelector('#inputAmbienteFoto')?.value || '';
    if (ambiente) dados.append('ambiente', ambiente);

    if (botao) botao.textContent = 'enviando...';
    try {
      const resposta = await api.form(`/api/dias/${dia.id}/fotos`, dados);
      toast(houveGanho
        ? `${resposta.fotos.length} foto(s) anexada(s) · ${resumo}`
        : `${resposta.fotos.length} foto(s) anexada(s).`, 'ok');
      await recarregarSemana({ manterDia: true });
    } finally {
      if (botao) botao.textContent = rotuloOriginal;
      if (inputFotos) inputFotos.value = '';
      if (inputCamera) inputCamera.value = '';
    }
  };

  if (btnAddFoto && inputFotos) btnAddFoto.onclick = () => inputFotos.click();
  const btnGaleria = raiz.querySelector('#btnGaleria');
  const btnCamera = raiz.querySelector('#btnCamera');
  if (btnGaleria) btnGaleria.onclick = () => inputFotos.click();
  if (btnCamera) btnCamera.onclick = () => inputCamera.click();

  if (inputFotos) inputFotos.onchange = () => enviarFotos(inputFotos.files, btnGaleria || btnAddFoto);
  if (inputCamera) inputCamera.onchange = () => enviarFotos(inputCamera.files, btnCamera);

  raiz.querySelectorAll('.foto-item img').forEach((img) => {
    img.onclick = () => abrirLightbox(img.src, img.alt);
  });
  raiz.querySelectorAll('[data-remover-foto]').forEach((btn) => {
    btn.onclick = async (e) => {
      e.stopPropagation();
      const ok = await confirmar({ titulo: 'Remover esta foto?', perigo: true, confirmar: 'Remover' });
      if (!ok) return;
      await api.del(`/api/fotos/${btn.dataset.removerFoto}`);
      await recarregarSemana({ manterDia: true });
    };
  });

  // ---------------- medições
  const btnMedicao = raiz.querySelector('[data-acao=nova-medicao]');
  if (btnMedicao) {
    btnMedicao.onclick = async () => {
      const dados = await formularioModal({
        titulo: 'Registrar medição',
        campos: [
          { nome: 'descricao', rotulo: 'Serviço executado', placeholder: 'Ex: reboco interno' },
          { nome: 'quantidade', rotulo: 'Quantidade', tipo: 'number' },
          { nome: 'unidade', rotulo: 'Unidade', tipo: 'select', valor: 'm²', opcoes: ['m²', 'm³', 'm', 'un', 'kg', 'sc', 'h'] },
          { nome: 'etapa_id', rotulo: 'Etapa do cronograma', tipo: 'select',
            opcoes: [{ valor: '', texto: '— sem vínculo —' }, ...estado.etapas.map((e) => ({ valor: e.id, texto: e.nome }))] },
        ],
      });
      if (!dados || !dados.descricao) return;
      await api.post(`/api/dias/${dia.id}/medicoes`, dados);
      toast('Medição registrada.', 'ok');
      await renderizar(raiz);
    };
  }
  raiz.querySelectorAll('[data-remover-medicao]').forEach((btn) => {
    btn.onclick = async () => {
      await api.del(`/api/medicoes/${btn.dataset.removerMedicao}`);
      await renderizar(raiz);
    };
  });

  // ---------------- ponto
  const camposHoras = Array.from(raiz.querySelectorAll('.campo-horas'));
  const atualizarTotal = () => {
    const alvo = raiz.querySelector('#totalFolhaDia');
    if (!alvo) return;
    const total = camposHoras.reduce(
      (soma, inp) => soma + (parseFloat(inp.value) || 0) * (parseFloat(inp.dataset.valor) || 0), 0);
    const horas = camposHoras.reduce((soma, inp) => soma + (parseFloat(inp.value) || 0), 0);
    alvo.textContent = `${num(horas, 1)} h · FOLHA DO DIA ${moeda(total)}`;
  };
  camposHoras.forEach((inp) => inp.addEventListener('input', atualizarTotal));
  raiz.querySelectorAll('[data-passo]').forEach((btn) => {
    btn.onclick = () => {
      const input = btn.parentElement.querySelector('.campo-horas');
      const novo = Math.min(24, Math.max(0, (parseFloat(input.value) || 0) + Number(btn.dataset.passo)));
      input.value = novo || '';
      atualizarTotal();
    };
  });
  atualizarTotal();

  const btnJornada = raiz.querySelector('[data-acao=jornada-padrao]');
  if (btnJornada) {
    btnJornada.onclick = () => {
      camposHoras.forEach((inp) => { inp.value = 8; });
      atualizarTotal();
      toast('8h preenchidas — confira e salve.');
    };
  }

  const btnRepetir = raiz.querySelector('[data-acao=repetir-ponto]');
  if (btnRepetir) {
    btnRepetir.onclick = async () => {
      await api.post(`/api/dias/${dia.id}/horas/repetir`, {});
      toast('Ponto copiado do último dia lançado.', 'ok');
      await recarregarSemana({ manterDia: true });
    };
  }

  const btnSalvarPonto = raiz.querySelector('[data-acao=salvar-ponto]');
  if (btnSalvarPonto) {
    btnSalvarPonto.onclick = async () => {
      btnSalvarPonto.disabled = true;
      try {
        const registros = camposHoras.map((inp) => ({
          funcionario_id: Number(inp.dataset.func), horas: parseFloat(inp.value) || 0,
        }));
        await api.put(`/api/dias/${dia.id}/horas`, { registros });
        toast('Ponto salvo.', 'ok');
        await recarregarSemana({ manterDia: true });
      } finally { btnSalvarPonto.disabled = false; }
    };
  }

  // ---------------- caixa do dia
  const btnDespesa = raiz.querySelector('[data-acao=nova-despesa]');
  if (btnDespesa) {
    btnDespesa.onclick = () => abrirModalDespesa(dia, async () => {
      await recarregarSemana({ manterDia: true });
    });
  }
  raiz.querySelectorAll('[data-remover-despesa]').forEach((btn) => {
    btn.onclick = async () => {
      const ok = await confirmar({ titulo: 'Excluir este lançamento?', perigo: true, confirmar: 'Excluir' });
      if (!ok) return;
      await api.del(`/api/despesas/${btn.dataset.removerDespesa}`);
      toast('Lançamento removido.');
      await recarregarSemana({ manterDia: true });
    };
  });
}
