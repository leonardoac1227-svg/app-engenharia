/* ==========================================================================
   TERCEIRIZADOS — contratos globais pagos por medição semanal.
   Visível apenas para o gestor (envolve valor de contrato e caixa).
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeFinanceiro } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import {
  abrirModal, confirmar, esc, esqueleto, fecharModal, formularioModal, kpi, moeda,
  moedaCurta, num, toast, vazio,
} from '../util.js';
import { htmlBarraSemana, ligarBarraSemana } from './comum.js';

let mostrarEncerrados = false;
let filtroTipo = 'todos';   // todos | empresa | avulso

const ROTULO_STATUS = {
  ativo: ['Em execução', 'badge-reembolso'],
  concluido: ['Concluído', 'badge-pago'],
  cancelado: ['Cancelado', 'badge-neutro'],
};

export async function renderizar(raiz) {
  if (!podeFinanceiro()) {
    raiz.innerHTML = vazio('🔒', 'Somente o gestor acessa os contratos de terceirizados.');
    return;
  }
  if (!estado.obraId) { raiz.innerHTML = vazio('＋', 'Selecione uma obra.'); return; }

  raiz.innerHTML = esqueleto(4);
  const alvoSemana = estado.semanaId ? `?semana_id=${estado.semanaId}` : '';
  const painel = await api.get(`/api/obras/${estado.obraId}/terceirizados${alvoSemana}`);
  const contratos = painel.contratos
    .filter((c) => mostrarEncerrados || c.status === 'ativo')
    .filter((c) => filtroTipo === 'todos' || c.tipo === filtroTipo);

  const medidoPct = painel.total_contratado
    ? (painel.total_medido / painel.total_contratado) * 100 : 0;

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">TER · EMPREITEIROS</span><h2>Terceirizados</h2></div>
      <button class="btn btn-brass btn-small" data-acao="novo">+ Contrato</button>
    </div>

    <div class="kpi-grid">
      ${kpi({ rotulo: 'Contratado', valor: moedaCurta(painel.total_contratado),
              sub: `${painel.ativos} em execução`, destaque: true })}
      ${kpi({ rotulo: 'Já medido', valor: moedaCurta(painel.total_medido),
              sub: `${num(medidoPct, 0)}% do contratado` })}
      ${kpi({ rotulo: 'Saldo a medir', valor: moedaCurta(painel.total_saldo), sub: 'contratos ativos' })}
      ${painel.avulsos
        ? kpi({ rotulo: 'Mão de obra produção', valor: moedaCurta(painel.total_producao),
                sub: `${painel.avulsos} avulso(s)` })
        : kpi({ rotulo: 'A pagar', valor: moedaCurta(painel.total_a_pagar),
                sub: 'medido e não quitado', estado: painel.total_a_pagar ? 'amarelo' : 'verde' })}
    </div>

    ${estado.semanas.length ? htmlBarraSemana() : ''}

    <div class="chips" style="margin-bottom:12px">
      ${[['todos', 'Todos'], ['empresa', `Empresas (${painel.empresas})`],
         ['avulso', `Por produção (${painel.avulsos})`]].map(([valor, rotulo]) => `
        <button class="chip ${filtroTipo === valor ? 'ativo' : ''}" data-tipo="${valor}">${rotulo}</button>`).join('')}
    </div>

    ${contratos.length ? contratos.map((c) => {
      const [rotulo, classe] = ROTULO_STATUS[c.status] || ROTULO_STATUS.ativo;
      const naSemana = c.medicao_semana;
      return `
        <div class="card contrato" data-contrato="${c.id}">
          <div class="row-between" style="align-items:flex-start">
            <div style="min-width:0;flex:1">
              <div class="item-titulo">${esc(c.empresa)}
                ${c.tipo === 'avulso' ? '<span class="badge badge-reembolso">por produção</span>' : ''}
                <span class="badge ${classe}">${rotulo}</span>
              </div>
              <div class="item-sub">${esc(c.servico)}${c.etapa_nome ? ' · ' + esc(c.etapa_nome) : ''}
                ${c.tipo === 'avulso' && c.funcionario_documento ? ' · CPF ' + esc(c.funcionario_documento) : ''}
              </div>
            </div>
            <div class="item-acoes">
              <button class="icon-mini" data-editar="${c.id}" title="Editar contrato">✎</button>
              <button class="icon-mini" data-historico="${c.id}" title="Histórico de medições">☰</button>
            </div>
          </div>

          <div class="progresso" style="margin-top:12px">
            <div class="progresso-trilho">
              <div class="progresso-preenchido meta" style="width:${Math.min(c.medido_percentual, 100)}%"></div>
            </div>
            <div class="progresso-legenda">
              <span>${num(c.medido_percentual, 1)}% medido · ${moeda(c.medido_valor)}</span>
              <span>de ${moeda(c.valor_total)}</span>
            </div>
          </div>

          <div class="contrato-saldos">
            <div><span class="item-sub">SALDO A MEDIR</span><b>${moeda(c.saldo_valor)}</b></div>
            <div><span class="item-sub">A PAGAR</span>
              <b class="${c.a_pagar ? 'valor-alerta' : ''}">${moeda(c.a_pagar)}</b></div>
            <div><span class="item-sub">MEDIÇÕES</span><b>${num(c.medicoes_count)}</b></div>
          </div>

          ${naSemana ? `
            <div class="alerta" style="margin:10px 0 0">
              <span class="alerta-ico">✓</span>
              <div>Semana ${estado.semana?.numero ?? ''} medida em <b>${num(naSemana.percentual, 1)}%</b> · ${moeda(naSemana.valor)}
                <div class="small">${naSemana.entra_no_caixa
                  ? `no caixa da semana · ${esc(naSemana.status_pagamento || 'Em Aberto')}`
                  : 'fora do caixa desta semana'}</div>
              </div>
            </div>` : ''}

          ${c.status === 'ativo' && estado.semanaId ? `
            <button class="btn ${naSemana ? 'btn-ghost' : 'btn-outline'}" data-medir="${c.id}" style="margin-top:10px">
              ${naSemana ? 'Corrigir medição da semana' : `Medir semana ${estado.semana?.numero ?? ''}`}
            </button>` : ''}
          ${c.status === 'concluido' ? '<p class="small faint" style="margin:10px 0 0">Contrato 100% medido.</p>' : ''}
        </div>`;
    }).join('') : vazio('☰', 'Nenhum contrato de terceirizado nesta obra.',
        '<p class="small muted">Cadastre o contrato global (ex.: pintura por R$ 50.000) e lance a medição a cada semana.</p>')}

    <button class="btn btn-ghost btn-small" data-acao="encerrados" style="width:100%;margin-top:10px">
      ${mostrarEncerrados ? 'Ocultar encerrados' : 'Mostrar encerrados e cancelados'}
    </button>

    <p class="small faint" style="margin-top:16px">
      A medição marcada como “entra no pagamento da semana” vira uma despesa em Caixa,
      com o contrato como fornecedor — ela aparece em contas a pagar, no custo da semana
      e no relatório, sem ser contada duas vezes.
    </p>
  `;

  if (estado.semanas.length) ligarBarraSemana(raiz);

  raiz.querySelectorAll('[data-tipo]').forEach((chip) => {
    chip.onclick = () => { filtroTipo = chip.dataset.tipo; renderizar(raiz); };
  });

  raiz.querySelector('[data-acao=encerrados]').onclick = () => {
    mostrarEncerrados = !mostrarEncerrados;
    renderizar(raiz);
  };

  raiz.querySelector('[data-acao=novo]').onclick = () => abrirFormularioContrato(raiz);

  raiz.querySelectorAll('[data-editar]').forEach((btn) => {
    btn.onclick = () => {
      const contrato = painel.contratos.find((c) => String(c.id) === btn.dataset.editar);
      abrirFormularioContrato(raiz, contrato);
    };
  });

  raiz.querySelectorAll('[data-medir]').forEach((btn) => {
    btn.onclick = () => {
      const contrato = painel.contratos.find((c) => String(c.id) === btn.dataset.medir);
      abrirModalMedicao(raiz, contrato);
    };
  });

  raiz.querySelectorAll('[data-historico]').forEach((btn) => {
    btn.onclick = () => abrirHistorico(raiz, btn.dataset.historico);
  });
}

/* -------------------------------------------------------------- contrato */

async function abrirFormularioContrato(raiz, contrato = null) {
  // Avulsos disponíveis: só quem está cadastrado como "por produção".
  const avulsos = estado.funcionarios.filter((f) => f.por_producao && f.ativo);

  if (!contrato && avulsos.length) {
    const escolha = await escolherNatureza(avulsos);
    if (!escolha) return;
    if (escolha.avulso) return criarTarefaAvulso(raiz, avulsos);
  }

  const campos = [
    { nome: 'empresa', rotulo: 'Empresa / prestador', valor: contrato?.empresa,
      placeholder: 'Ex: Pinturas Silva' },
    { nome: 'servico', rotulo: 'Serviço contratado', valor: contrato?.servico,
      placeholder: 'Ex: pintura interna e externa' },
    { nome: 'valor_total', rotulo: 'Valor global do contrato (R$)', tipo: 'number',
      valor: contrato?.valor_total },
    { nome: 'responsavel', rotulo: 'Responsável (opcional)', valor: contrato?.responsavel },
    { nome: 'telefone', rotulo: 'Telefone (opcional)', tipo: 'tel', valor: contrato?.telefone },
  ];

  if (estado.etapas.length) {
    campos.push({
      nome: 'etapa_id', rotulo: 'Etapa do cronograma', tipo: 'select',
      valor: contrato?.etapa_id || '',
      opcoes: [{ valor: '', texto: '— sem vínculo —' },
        ...estado.etapas.map((e) => ({ valor: String(e.id), texto: e.nome }))],
    });
  }
  if (contrato) {
    campos.push({
      nome: 'status', rotulo: 'Situação', tipo: 'select', valor: contrato.status,
      opcoes: [{ valor: 'ativo', texto: 'Em execução' },
        { valor: 'concluido', texto: 'Concluído' },
        { valor: 'cancelado', texto: 'Cancelado' }],
    });
  }

  const dados = await formularioModal({
    titulo: contrato ? 'Editar contrato' : 'Novo contrato',
    descricao: contrato
      ? 'Alterar o valor global recalcula as medições já lançadas (aditivo).'
      : 'O valor global é a base do cálculo: cada medição paga a fatia correspondente.',
    campos,
    rotulo: contrato ? 'Salvar' : 'Criar contrato',
  });
  if (!dados) return;

  if (contrato) await api.put(`/api/contratos/${contrato.id}`, dados);
  else await api.post(`/api/obras/${estado.obraId}/contratos`, dados);

  toast(contrato ? 'Contrato atualizado.' : 'Contrato criado.', 'ok');
  await renderizar(raiz);
}

/* -------------------------------------------------------------- medição */

function abrirModalMedicao(raiz, contrato) {
  const restante = contrato.saldo_percentual;

  abrirModal(`
    <h3>Medir a semana ${estado.semana?.numero ?? ''}</h3>
    <p class="small muted">${esc(contrato.empresa)} · ${esc(contrato.servico)}</p>

    <div class="medicao-resumo">
      <div><span class="item-sub">CONTRATO</span><b>${moeda(contrato.valor_total)}</b></div>
      <div><span class="item-sub">JÁ MEDIDO</span><b>${num(contrato.medido_percentual, 1)}%</b></div>
      <div><span class="item-sub">RESTA MEDIR</span><b>${num(restante, 1)}%</b></div>
    </div>

    <label class="field-label">Percentual executado nesta semana</label>
    <div class="row">
      <input type="range" id="medRange" class="meta-range" min="0" max="${restante}" step="0.5" value="0"
             style="flex:1;margin:0">
      <input type="number" id="medPct" min="0" max="${restante}" step="0.5" value="0"
             style="width:90px;text-align:center;font-weight:700">
    </div>

    <div class="medicao-valor">
      <span class="item-sub">VALOR A PAGAR NESTA MEDIÇÃO</span>
      <div id="medValor">${moeda(0)}</div>
      <span class="item-sub" id="medSaldo">Saldo do contrato após esta medição: ${moeda(contrato.saldo_valor)}</span>
    </div>

    <label class="checkbox-row">
      <input type="checkbox" id="medCaixa" checked>
      <span>Este custo entra no pagamento da semana atual</span>
    </label>
    <p class="small faint" style="margin-top:-4px">
      Ligado, vira uma despesa em Caixa com vencimento e baixa de pagamento.
      Desligado, registra só o avanço do contrato.
    </p>

    <div id="medVencimentoBox">
      <label class="field-label">Vencimento (opcional)</label>
      <input type="date" id="medVencimento">
    </div>

    <label class="field-label">Observação (opcional)</label>
    <input type="text" id="medObs" placeholder="Ex: medição conferida com o encarregado">

    <div class="btn-linha">
      <button class="btn btn-outline" id="medCancelar">Cancelar</button>
      <button class="btn btn-brass" id="medSalvar">Lançar medição</button>
    </div>
  `, (modal) => {
    const range = modal.querySelector('#medRange');
    const numero = modal.querySelector('#medPct');
    const caixa = modal.querySelector('#medCaixa');

    const atualizar = (valor) => {
      const pct = Math.min(Math.max(Number(valor) || 0, 0), restante);
      range.value = pct;
      numero.value = pct;
      const reais = (contrato.valor_total * pct) / 100;
      modal.querySelector('#medValor').textContent = moeda(reais);
      modal.querySelector('#medSaldo').textContent =
        `Saldo do contrato após esta medição: ${moeda(Math.max(contrato.saldo_valor - reais, 0))}`;
    };

    range.oninput = () => atualizar(range.value);
    numero.oninput = () => atualizar(numero.value);
    caixa.onchange = () => {
      modal.querySelector('#medVencimentoBox').classList.toggle('hidden', !caixa.checked);
    };

    modal.querySelector('#medCancelar').onclick = fecharModal;
    modal.querySelector('#medSalvar').onclick = async () => {
      const percentual = Number(numero.value);
      if (!percentual || percentual <= 0) { toast('Informe o percentual medido.', 'erro'); return; }

      const botao = modal.querySelector('#medSalvar');
      botao.disabled = true;
      try {
        const r = await api.post(
          `/api/contratos/${contrato.id}/semanas/${estado.semanaId}/medicao`, {
            percentual,
            entra_no_caixa: caixa.checked,
            vencimento: caixa.checked ? modal.querySelector('#medVencimento').value : '',
            observacao: modal.querySelector('#medObs').value.trim(),
          });
        fecharModal();
        toast(r.contrato_concluido
          ? `Contrato concluído — 100% medido.`
          : `Medição lançada: ${moeda(r.valor)}.`, 'ok');
        await recarregarSemana({ manterDia: true });
        await renderizar(raiz);
      } finally { botao.disabled = false; }
    };
  });
}

/* -------------------------------------------------------------- histórico */

async function abrirHistorico(raiz, contratoId) {
  const contrato = await api.get(`/api/contratos/${contratoId}`);

  abrirModal(`
    <h3>${esc(contrato.empresa)}</h3>
    <p class="small muted">${esc(contrato.servico)} · contrato de ${moeda(contrato.valor_total)}</p>

    ${contrato.medicoes.length ? `
      <div class="lista" style="margin-top:14px">
        ${contrato.medicoes.map((m) => `
          <div class="item">
            <div class="item-corpo">
              <div class="item-titulo">Semana ${m.semana_numero} · ${num(m.percentual, 1)}%
                ${m.entra_no_caixa
                  ? `<span class="badge ${m.status_pagamento === 'Pago' ? 'badge-pago' : 'badge-em-aberto'}">${esc(m.status_pagamento || 'Em Aberto')}</span>`
                  : '<span class="badge badge-neutro">fora do caixa</span>'}
              </div>
              <div class="item-sub">acumulado ${num(m.acumulado, 1)}%${m.observacao ? ' · ' + esc(m.observacao) : ''}</div>
            </div>
            <div class="item-valor">${moeda(m.valor)}</div>
            <div class="item-acoes">
              <button class="icon-mini" data-apagar-medicao="${m.id}" title="Remover medição">🗑</button>
            </div>
          </div>`).join('')}
      </div>
      <div class="ledger-total">TOTAL MEDIDO · ${moeda(contrato.medido_valor)} · SALDO ${moeda(contrato.saldo_valor)}</div>
    ` : '<p class="vazio-inline">Nenhuma medição lançada ainda.</p>'}

    ${contrato.medicoes.length === 0 ? `
      <button class="btn btn-perigo" data-apagar-contrato="${contrato.id}">Excluir contrato</button>` : ''}
    <button class="btn btn-outline" id="histFechar" style="margin-top:10px">Fechar</button>
  `, (modal) => {
    modal.querySelector('#histFechar').onclick = fecharModal;

    modal.querySelectorAll('[data-apagar-medicao]').forEach((btn) => {
      btn.onclick = async () => {
        const confirma = await confirmar({
          titulo: 'Remover esta medição?',
          texto: 'A despesa gerada por ela no caixa também é removida.',
          confirmar: 'Remover', perigo: true,
        });
        if (!confirma) return;
        await api.del(`/api/medicoes-contrato/${btn.dataset.apagarMedicao}`);
        fecharModal();
        toast('Medição removida.');
        await recarregarSemana({ manterDia: true });
        await renderizar(raiz);
      };
    });

    const btnApagar = modal.querySelector('[data-apagar-contrato]');
    if (btnApagar) {
      btnApagar.onclick = async () => {
        const confirma = await confirmar({
          titulo: 'Excluir este contrato?', confirmar: 'Excluir', perigo: true,
        });
        if (!confirma) return;
        await api.del(`/api/contratos/${btnApagar.dataset.apagarContrato}`);
        fecharModal();
        toast('Contrato excluído.');
        await renderizar(raiz);
      };
    }
  });
}


/* --------------------------------------------------------------------------
   Ao criar um contrato, o app pergunta a natureza do contratado: empresa
   terceirizada ou profissional da equipe pago por produção. A mecânica de
   medição é a mesma; muda só quem recebe.
   -------------------------------------------------------------------------- */

function escolherNatureza(avulsos) {
  return new Promise((resolve) => {
    let respondido = false;
    const responder = (valor) => {
      if (respondido) return;
      respondido = true;
      resolve(valor);
    };

    abrirModal(`
      <h3>Novo contrato</h3>
      <p class="small muted">Quem vai executar este serviço?</p>
      <div class="lista" style="margin-top:12px">
        <button class="obra-item" data-escolha="empresa">
          <span class="obra-selo">🏢</span>
          <span class="obra-item-corpo">
            <span class="obra-item-nome">Empresa terceirizada</span>
            <span class="obra-item-sub">pintura, forro, esquadrias...</span>
          </span>
          <span class="acc-chevron">›</span>
        </button>
        <button class="obra-item" data-escolha="avulso">
          <span class="obra-selo parada">👷</span>
          <span class="obra-item-corpo">
            <span class="obra-item-nome">Profissional por produção</span>
            <span class="obra-item-sub">${avulsos.length} cadastrado(s) na equipe</span>
          </span>
          <span class="acc-chevron">›</span>
        </button>
      </div>
    `, (modal) => {
      modal.querySelectorAll('[data-escolha]').forEach((btn) => {
        btn.onclick = () => {
          const escolha = btn.dataset.escolha;
          fecharModal();
          responder({ avulso: escolha === 'avulso' });
        };
      });

      // Fechar o modal por fora (overlay ou Esc) conta como desistir.
      const overlay = document.querySelector('#modalOverlay');
      const observador = new MutationObserver(() => {
        if (!overlay.classList.contains('open')) {
          observador.disconnect();
          responder(null);
        }
      });
      observador.observe(overlay, { attributes: true, attributeFilter: ['class'] });
    });
  });
}

async function criarTarefaAvulso(raiz, avulsos) {
  const dados = await formularioModal({
    titulo: 'Tarefa por produção',
    descricao: 'Empreitada fechada com um profissional da equipe. A medição semanal funciona igual à das empresas.',
    campos: [
      { nome: 'funcionario_id', rotulo: 'Profissional', tipo: 'select',
        opcoes: avulsos.map((f) => ({ valor: String(f.id), texto: `${f.nome}${f.funcao ? ' · ' + f.funcao : ''}` })) },
      { nome: 'servico', rotulo: 'Serviço', placeholder: 'Ex: forro de gesso do 1º pavimento' },
      { nome: 'valor_total', rotulo: 'Valor combinado (R$)', tipo: 'number' },
      ...(estado.etapas.length ? [{
        nome: 'etapa_id', rotulo: 'Etapa do cronograma', tipo: 'select',
        opcoes: [{ valor: '', texto: '— sem vínculo —' },
          ...estado.etapas.map((e) => ({ valor: String(e.id), texto: e.nome }))],
      }] : []),
    ],
    rotulo: 'Criar tarefa',
  });
  if (!dados || !dados.servico.trim()) return;

  await api.post(`/api/obras/${estado.obraId}/contratos`, dados);
  toast('Tarefa criada.', 'ok');
  await renderizar(raiz);
}
