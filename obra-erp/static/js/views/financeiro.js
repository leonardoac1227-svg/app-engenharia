/* ==========================================================================
   CAIXA DA OBRA — fluxo de caixa, contas a pagar e lançamentos da semana.
   Visível apenas para o papel "gestor".
   ========================================================================== */

import { api, baixar } from '../api.js';
import { comprimirImagem, formatarBytes } from '../compressao.js';
import { estado, podeFinanceiro } from '../estado.js';
import { recarregarSemana } from '../navegacao.js';
import {
  abrirLightbox, abrirModal, badgeStatus, confirmar, dataBR, esc, esqueleto, fecharModal,
  formularioModal, hojeISO, kpi, moeda, moedaCurta, rosca, toast, vazio,
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

    <label class="field-label">Número da nota fiscal</label>
    <input type="text" id="dspNota" value="${esc(despesa?.numero_nota || '')}"
           inputmode="numeric" placeholder="Ex: 12345">

    <label class="field-label">Valor</label>
    <div class="input-moeda"><input type="number" id="dspValor" inputmode="decimal" step="0.01" min="0"
      value="${despesa?.valor || ''}" placeholder="0,00"></div>

    <div id="dspLocacaoBox" class="${(despesa?.categoria || 'Material') === 'Locação' && !edicao ? '' : 'hidden'}">
      <div class="alerta" style="margin:14px 0 4px">
        <span class="alerta-ico">↻</span>
        <div><b>Aluguel se repete</b>
          <div class="small">Informe o intervalo e por quantos períodos. O app monta o
          cronograma e avisa a cada renovação — você confirma o pagamento com um toque.</div>
        </div>
      </div>

      <label class="field-label">Equipamento</label>
      <input type="text" id="dspEquipamento" placeholder="Ex: andaime da fachada">

      <label class="field-label">Frequência do custo</label>
      <select id="dspFrequencia">
        <option value="">— sem recorrência (cobrança única) —</option>
        <option value="diaria">Diária</option>
        <option value="semanal">Semanal</option>
        <option value="quinzenal">Quinzenal</option>
        <option value="mensal" selected>Mensal</option>
      </select>

      <div id="dspDuracaoBox">
        <label class="field-label">Duração <span id="dspDuracaoUnidade" class="faint">(quantos meses)</span></label>
        <input type="number" id="dspCiclos" min="1" max="366" step="1" value="3" inputmode="numeric">
        <p class="small faint" id="dspPrevia" style="margin-top:6px"></p>
      </div>
    </div>

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
      <div class="btn-linha" style="margin-top:0">
        <button type="button" class="btn btn-outline btn-small" id="dspBtnCamera" style="flex:1">📷 Câmera</button>
        <button type="button" class="btn btn-outline btn-small" id="dspBtnArquivo" style="flex:1">📎 Galeria / arquivo</button>
      </div>
      <input type="file" id="dspAnexoCamera" accept="image/*" capture="environment" class="hidden">
      <input type="file" id="dspAnexo" accept="image/*,application/pdf,.pdf" class="hidden">
      <div id="dspAnexoInfo" class="anexo-info hidden"></div>`}

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

    // ---- locação: campos de recorrência só fazem sentido nessa categoria
    const campoCategoria = raiz.querySelector('#dspCategoria');
    const boxLocacao = raiz.querySelector('#dspLocacaoBox');
    const campoFrequencia = raiz.querySelector('#dspFrequencia');
    const campoCiclos = raiz.querySelector('#dspCiclos');
    const campoValor = raiz.querySelector('#dspValor');

    const UNIDADES = {
      diaria: ['quantos dias', 'dia(s)'],
      semanal: ['quantas semanas', 'semana(s)'],
      quinzenal: ['quantas quinzenas', 'quinzena(s)'],
      mensal: ['quantos meses', 'mês(es)'],
    };

    const atualizarPrevia = () => {
      if (!boxLocacao) return;
      const frequencia = campoFrequencia.value;
      const temRecorrencia = !!frequencia;
      raiz.querySelector('#dspDuracaoBox').classList.toggle('hidden', !temRecorrencia);
      if (!temRecorrencia) return;

      const [rotulo, unidade] = UNIDADES[frequencia];
      raiz.querySelector('#dspDuracaoUnidade').textContent = `(${rotulo})`;
      const ciclos = Number(campoCiclos.value) || 1;
      const valor = parseFloat(campoValor.value) || 0;
      raiz.querySelector('#dspPrevia').textContent = valor
        ? `${ciclos} ${unidade} × ${moeda(valor)} = ${moeda(ciclos * valor)} no total`
        : `${ciclos} ${unidade} de aluguel`;
    };

    if (boxLocacao) {
      const alternarLocacao = () => {
        boxLocacao.classList.toggle('hidden', campoCategoria.value !== 'Locação');
        atualizarPrevia();
      };
      campoCategoria.onchange = alternarLocacao;
      campoFrequencia.onchange = atualizarPrevia;
      campoCiclos.oninput = atualizarPrevia;
      campoValor.addEventListener('input', atualizarPrevia);
      alternarLocacao();
    }

    // ---- anexo: galeria, arquivos ou câmera, sempre comprimido no aparelho
    let anexoEscolhido = null;
    const campoAnexo = raiz.querySelector('#dspAnexo');
    const campoCamera = raiz.querySelector('#dspAnexoCamera');
    const info = raiz.querySelector('#dspAnexoInfo');

    const receberAnexo = async (arquivo) => {
      if (!arquivo) return;
      const tamanhoOriginal = arquivo.size;
      info.classList.remove('hidden');
      info.innerHTML = '<span class="muted">preparando arquivo...</span>';

      // PDF não dá para comprimir no navegador: vai inteiro e o servidor
      // reamostra as imagens embutidas antes de gravar.
      const ehPdf = /pdf$/i.test(arquivo.type) || /\.pdf$/i.test(arquivo.name || '');
      anexoEscolhido = ehPdf ? arquivo : await comprimirImagem(arquivo);

      const reduziu = tamanhoOriginal - anexoEscolhido.size > 50 * 1024;
      info.innerHTML = `
        <span class="anexo-nome">📎 ${esc(anexoEscolhido.name)}</span>
        <span class="anexo-tamanho">${reduziu
          ? `${formatarBytes(tamanhoOriginal)} → <b>${formatarBytes(anexoEscolhido.size)}</b>`
          : formatarBytes(anexoEscolhido.size)}${ehPdf ? ' · o servidor ainda comprime o PDF' : ''}</span>
        <button type="button" class="icon-mini" id="dspAnexoLimpar" title="Remover anexo">✕</button>`;

      info.querySelector('#dspAnexoLimpar').onclick = () => {
        anexoEscolhido = null;
        campoAnexo.value = '';
        campoCamera.value = '';
        info.classList.add('hidden');
        info.innerHTML = '';
      };
    };

    if (campoAnexo) {
      raiz.querySelector('#dspBtnArquivo').onclick = () => campoAnexo.click();
      raiz.querySelector('#dspBtnCamera').onclick = () => campoCamera.click();
      campoAnexo.onchange = () => receberAnexo(campoAnexo.files[0]);
      campoCamera.onchange = () => receberAnexo(campoCamera.files[0]);
    }

    raiz.querySelector('#dspCancelar').onclick = fecharModal;

    raiz.querySelector('#dspSalvar').onclick = async () => {
      const descricao = raiz.querySelector('#dspDescricao').value.trim();
      const valor = parseFloat(raiz.querySelector('#dspValor').value);
      if (!descricao) { toast('Informe a descrição.', 'erro'); return; }
      if (!valor || valor <= 0) { toast('Informe um valor válido.', 'erro'); return; }

      const botao = raiz.querySelector('#dspSalvar');
      const numeroNota = raiz.querySelector('#dspNota').value.trim();

      // O servidor recusa nota repetida do mesmo fornecedor (409). Aqui a
      // gente mostra o que já existe e deixa o gestor decidir — nota
      // parcelada em duas competências é caso legítimo.
      const enviar = async (confirmarDuplicidade) => {
        if (edicao) {
          return api.put(`/api/despesas/${despesa.id}`, {
            categoria: raiz.querySelector('#dspCategoria').value,
            descricao,
            fornecedor: raiz.querySelector('#dspFornecedor').value.trim(),
            valor,
            numero_nota: numeroNota,
            confirmar_duplicidade: confirmarDuplicidade,
            status_pagamento: status,
            vencimento: status === 'Em Aberto' ? raiz.querySelector('#dspVencimento').value : '',
            etapa_id: raiz.querySelector('#dspEtapa')?.value || null,
          }, { silencioso: true });
        }
        {
          const diaId = raiz.querySelector('#dspDia')?.value || dia.id;
          const dados = new FormData();
          dados.append('categoria', raiz.querySelector('#dspCategoria').value);
          dados.append('descricao', descricao);
          dados.append('fornecedor', raiz.querySelector('#dspFornecedor').value.trim());
          dados.append('valor', valor);
          dados.append('status_pagamento', status);
          if (numeroNota) dados.append('numero_nota', numeroNota);
          if (confirmarDuplicidade) dados.append('confirmar_duplicidade', '1');
          if (status === 'Em Aberto') dados.append('vencimento', raiz.querySelector('#dspVencimento').value || '');
          const etapa = raiz.querySelector('#dspEtapa')?.value;
          if (etapa) dados.append('etapa_id', etapa);
          if (campoCategoria.value === 'Locação' && campoFrequencia?.value) {
            dados.append('frequencia', campoFrequencia.value);
            dados.append('ciclos', campoCiclos.value || '1');
            dados.append('equipamento', raiz.querySelector('#dspEquipamento').value.trim() || descricao);
            dados.append('data_locacao', raiz.querySelector('#dspVencimento').value || '');
          }
          if (anexoEscolhido) dados.append('imagem', anexoEscolhido);
          return api.form(`/api/dias/${diaId}/despesas`, dados, { silencioso: true });
        }
      };

      botao.disabled = true;
      try {
        let resposta;
        try {
          resposta = await enviar(false);
        } catch (erro) {
          if (erro?.codigo !== 'nota_duplicada') throw erro;
          const seguir = await confirmar({
            titulo: 'Nota já lançada',
            texto: `${erro.mensagem}\n\nLançar mesmo assim? (nota parcelada em duas competências é um caso válido)`,
            confirmar: 'Lançar mesmo assim', perigo: true,
          });
          if (!seguir) return;
          resposta = await enviar(true);
        }

        if (resposta?.locacao_id) {
          toast('Locação criada — as renovações vão aparecer no painel.', 'ok');
        }
        if (resposta?.compressao?.houve_ganho) {
          toast(`Anexo comprimido: ${resposta.compressao.original} → ${resposta.compressao.final}`, 'ok');
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

    <div id="blocoLocacoes"></div>

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
              ${x.numero_nota ? ' · NF ' + esc(x.numero_nota) : ''}
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
  renderizarLocacoes(raiz);

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


/* --------------------------------------------------------------------------
   Locação de equipamentos: aluguel que se repete e costuma escapar do radar.
   O bloco mostra o que vence, o custo mensal comprometido e permite lançar a
   renovação em um toque.
   -------------------------------------------------------------------------- */

const ROTULO_FREQUENCIA = {
  diaria: 'diária', semanal: 'semanal', quinzenal: 'quinzenal', mensal: 'mensal',
};

async function renderizarLocacoes(raiz) {
  const alvo = raiz.querySelector('#blocoLocacoes');
  if (!alvo) return;

  const painel = await api.get(`/api/obras/${estado.obraId}/locacoes`);
  const ativas = painel.locacoes.filter((l) => l.status === 'ativa');
  const pendentes = [...painel.alertas.vencidas, ...painel.alertas.proximas];

  if (!painel.locacoes.length) {
    alvo.innerHTML = `
      <div class="section-head"><h3>Equipamentos alugados</h3></div>
      <div class="card">
        <p class="small muted" style="margin-bottom:0">
          Nenhum equipamento alugado. Ao lançar uma despesa da categoria <b>Locação</b>,
          informe a frequência e o app passa a avisar cada renovação.
        </p>
      </div>`;
    return;
  }

  alvo.innerHTML = `
    <div class="section-head">
      <h3>Equipamentos alugados</h3>
      <span class="small muted">${moedaCurta(painel.custo_mensal_estimado)}/mês</span>
    </div>

    ${pendentes.length ? `
      <div class="lista" style="margin-bottom:12px">
        ${pendentes.map((p) => {
          const vencida = p.vencimento < hojeISO();
          return `
            <div class="item ${vencida ? 'alerta-vencida' : ''}">
              <div class="item-corpo">
                <div class="item-titulo">${esc(p.equipamento)}
                  <span class="badge ${vencida ? 'badge-vencida' : 'badge-em-aberto'}">
                    ${vencida ? 'renovação vencida' : 'renova em breve'}</span>
                </div>
                <div class="item-sub">${vencida ? 'venceu' : 'vence'} ${dataBR(p.vencimento)}
                  · ${esc(ROTULO_FREQUENCIA[p.frequencia] || p.frequencia)}
                  ${p.fornecedor ? ' · ' + esc(p.fornecedor) : ''}</div>
              </div>
              <div class="item-valor">${moeda(p.valor)}</div>
              <div class="item-acoes">
                <button class="icon-mini" data-lancar-parcela="${p.id}" title="Lançar no caixa">✓</button>
              </div>
            </div>`;
        }).join('')}
      </div>` : ''}

    ${ativas.map((l) => `
      <div class="card" style="padding:14px">
        <div class="row-between" style="align-items:flex-start">
          <div style="flex:1;min-width:0">
            <div class="item-titulo">${esc(l.equipamento)}</div>
            <div class="item-sub">
              ${moeda(l.valor_parcela)} · ${esc(l.frequencia_label.toLowerCase())}
              ${l.fornecedor ? ' · ' + esc(l.fornecedor) : ''}
            </div>
          </div>
          <div class="item-acoes">
            <button class="icon-mini" data-prorrogar="${l.id}" title="Prorrogar">＋</button>
            <button class="icon-mini" data-encerrar="${l.id}" title="Encerrar (devolvido)">⏹</button>
          </div>
        </div>

        <div class="progresso" style="margin-top:10px">
          <div class="progresso-trilho">
            <div class="progresso-preenchido meta"
                 style="width:${(l.parcelas_pagas / l.ciclos) * 100}%"></div>
          </div>
          <div class="progresso-legenda">
            <span>${l.parcelas_pagas}/${l.ciclos} pagas · ${moeda(l.total_lancado)}</span>
            <span>${l.proxima_parcela ? 'próxima ' + dataBR(l.proxima_parcela.vencimento) : 'encerrando'}</span>
          </div>
        </div>
      </div>`).join('')}

    ${painel.locacoes.length > ativas.length ? `
      <p class="small faint">${painel.locacoes.length - ativas.length} locação(ões) encerrada(s).</p>` : ''}
  `;

  const recarregar = async () => { await recarregarSemana({ manterDia: true }); };

  alvo.querySelectorAll('[data-lancar-parcela]').forEach((btn) => {
    btn.onclick = async () => {
      await api.post(`/api/locacoes/parcelas/${btn.dataset.lancarParcela}/lancar`,
                     { status_pagamento: 'Em Aberto' });
      toast('Renovação lançada em contas a pagar.', 'ok');
      await recarregar();
    };
  });

  alvo.querySelectorAll('[data-encerrar]').forEach((btn) => {
    btn.onclick = async () => {
      const confirma = await confirmar({
        titulo: 'Equipamento devolvido?',
        texto: 'As renovações futuras são canceladas. O que já foi pago continua no histórico.',
        confirmar: 'Encerrar locação',
      });
      if (!confirma) return;
      const r = await api.post(`/api/locacoes/${btn.dataset.encerrar}/encerrar`, {});
      toast(`Locação encerrada · ${r.canceladas} renovação(ões) cancelada(s).`, 'ok');
      await recarregar();
    };
  });

  alvo.querySelectorAll('[data-prorrogar]').forEach((btn) => {
    btn.onclick = async () => {
      const dados = await formularioModal({
        titulo: 'Prorrogar locação',
        descricao: 'Quantos períodos a mais? O cronograma continua da última renovação.',
        campos: [{ nome: 'ciclos', rotulo: 'Períodos adicionais', tipo: 'number', valor: 1 }],
        rotulo: 'Prorrogar',
      });
      if (!dados) return;
      await api.post(`/api/locacoes/${btn.dataset.prorrogar}/prorrogar`, dados);
      toast('Locação prorrogada.', 'ok');
      await recarregar();
    };
  });
}
