/* ==========================================================================
   DIÁRIO — fotos do andamento, ponto da equipe e caixa do dia selecionado.
   ========================================================================== */

import { api } from '../api.js';
import { comprimirLote } from '../compressao.js';
import { estado, podeFinanceiro, podeLancar } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import {
  abrirLightbox, badgeStatus, confirmar, dataBR, esc, moeda, num, toast, tratarFotosQuebradas, vazio,
} from '../util.js';
import { htmlBarraSemana, htmlDiasStrip, ligarBarraSemana } from './comum.js';
import { abrirModalDespesa } from './financeiro.js';

let cacheDia = { horas: {}, despesas: [] };

function diaSelecionado() {
  if (!estado.dias.length) return null;
  if (estado.diaAtivo) {
    const atual = estado.dias.find((d) => d.id === estado.diaAtivo.id);
    if (atual) return atual;
  }
  return estado.dias.find((d) => d.hoje) || estado.dias.find((d) => d.util) || estado.dias[0];
}

async function carregarDadosDoDia(dia) {
  const [horas, despesas] = await Promise.all([
    api.get(`/api/dias/${dia.id}/horas`),
    podeFinanceiro() ? api.get(`/api/dias/${dia.id}/despesas`) : Promise.resolve([]),
  ]);
  cacheDia = { horas, despesas };
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

  const somenteLeitura = !podeLancar();

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">DIÁRIO DA OBRA</span><h2>${esc(dia.dia_semana)}, ${dataBR(dia.data)}</h2></div>
    </div>

    ${htmlBarraSemana()}
    ${htmlDiasStrip(dia.id)}

    <div class="card tight" style="margin-top:10px">
      <div class="kpi-label">SITUAÇÃO DO DIA</div>
      <div class="small" style="margin-top:4px">
        ${dia.fotos ? `📷 ${dia.fotos} foto(s)` : '○ sem fotos'} ·
        ${dia.tem_ponto ? `✓ ${num(dia.horas, 1)} h` : '○ sem ponto'}
        ${podeFinanceiro() ? ` · ${dia.qtd_despesas ? `✓ ${dia.qtd_despesas} lançamento(s)` : '○ sem lançamentos'}` : ''}
      </div>
    </div>

    <!-- ===================== FOTOS ===================== -->
    <details class="accordion" open>
      <summary>
        <span class="acc-code">FOT-01</span>
        <span class="acc-title">Fotos do andamento</span>
        <span class="acc-status">${dia.fotos || ''}</span>
        <span class="acc-chevron">›</span>
      </summary>
      <div class="acc-body">
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
        ${!somenteLeitura || dia.fotos ? '' : '<p class="vazio-inline">Nenhuma foto neste dia.</p>'}

        ${somenteLeitura ? '' : `
          <div class="btn-linha" style="margin-top:0">
            <button class="btn btn-outline btn-small" id="btnCamera" style="flex:1">📷 Tirar foto</button>
            <button class="btn btn-outline btn-small" id="btnGaleria" style="flex:1">🖼 Galeria / arquivos</button>
          </div>`}

        <!-- dois inputs: com capture, o Android abre direto a câmera; sem
             capture, abre a galeria e o gerenciador de arquivos -->
        <input type="file" id="inputCamera" accept="image/*" capture="environment" multiple class="hidden">
        <input type="file" id="inputFotos" accept="image/*" multiple class="hidden">
      </div>
    </details>

    <!-- ===================== PONTO ===================== -->
    <details class="accordion">
      <summary>
        <span class="acc-code">PON-02</span>
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
              return `
                <div class="staff-row">
                  <div class="staff-view">
                    <div class="staff-info">
                      <div class="staff-name">${esc(f.nome)}</div>
                      <div class="staff-meta">${esc(f.funcao || 'sem função')}</div>
                    </div>
                    <div class="contador">
                      <button type="button" data-passo="-1" ${somenteLeitura ? 'disabled' : ''}>−</button>
                      <input type="number" class="campo-horas" min="0" max="24" step="0.5"
                             value="${horas || ''}" placeholder="0" inputmode="decimal"
                             data-func="${f.id}" ${somenteLeitura ? 'disabled' : ''}>
                      <button type="button" data-passo="1" ${somenteLeitura ? 'disabled' : ''}>+</button>
                    </div>
                  </div>
                </div>`;
            }).join('')}
          </div>
          <div class="ledger-total" id="totalHorasDia"></div>
          ${somenteLeitura ? '' : '<button class="btn btn-brass" data-acao="salvar-ponto">Salvar ponto do dia</button>'}
        ` : vazio('☰', 'Nenhum profissional cadastrado nesta obra.',
            '<button class="btn btn-outline" data-ir-equipe="1">Cadastrar equipe</button>')}
      </div>
    </details>

    <!-- ===================== CAIXA DO DIA ===================== -->
    ${podeFinanceiro() ? `
    <details class="accordion">
      <summary>
        <span class="acc-code">FIN-03</span>
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
      // marca o dia tocado já, antes de os dados chegarem
      raiz.querySelectorAll('.dia-btn.ativo').forEach((b) => b.classList.remove('ativo'));
      btn.classList.add('ativo');
      estado.diaAtivo = estado.dias.find((d) => d.id === Number(btn.dataset.dia));
      await renderizar(raiz);
      raiz.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
  });

  const irEquipe = raiz.querySelector('[data-ir-equipe]');
  if (irEquipe) irEquipe.onclick = () => import('../navegacao.js').then((m) => m.navegar('equipe'));

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

  // ---------------- ponto
  const camposHoras = Array.from(raiz.querySelectorAll('.campo-horas'));
  const atualizarTotal = () => {
    const alvo = raiz.querySelector('#totalHorasDia');
    if (!alvo) return;
    const horas = camposHoras.reduce((soma, inp) => soma + (parseFloat(inp.value) || 0), 0);
    const presentes = camposHoras.filter((inp) => parseFloat(inp.value) > 0).length;
    alvo.textContent = `${presentes} PRESENTE(S) · ${num(horas, 1)} h`;
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
