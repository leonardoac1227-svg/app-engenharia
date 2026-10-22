/* ==========================================================================
   ASSISTENTE — fale ou anexe, a IA prepara os lançamentos do dia.

   O gestor dita como numa conversa ("hoje o João fez 8 horas, comprei
   cimento, nota 4521...") e anexa fotos e notas. O servidor devolve ações
   prontas (diário, ponto, despesas, fotos...). Cada ação é gravada pelos
   mesmos endpoints dos formulários, então valem as mesmas travas: nota em
   duplicidade, permissão do perfil e auditoria.

   Com "Gravar automaticamente" ligado, tudo que está completo é gravado na
   hora; exclusões e lançamentos sem valor sempre esperam um toque.
   ========================================================================== */

import { api } from '../api.js';
import { comprimirLote } from '../compressao.js';
import { estado, lerPreferencia, podeFinanceiro, podeLancar, salvarPreferencia } from '../estado.js';
import { navegar, recarregarSemana } from '../navegacao.js';
import { dataBR, esc, hojeISO, moeda, toast, vazio } from '../util.js';

const CHAVE_AUTO = 'obraerp:ia_auto';
const CATEGORIAS = ['Material', 'Serviço', 'Locação', 'Frete', 'Alimentação', 'Outro'];
const STATUS = ['Em Aberto', 'Pago', 'Reembolso'];
const ORDEM = ['funcionario', 'diario', 'ponto', 'foto', 'despesa', 'editar_despesa', 'pagar_despesa', 'excluir_despesa'];
const TITULOS = {
  diario: '📋 Diário de obra', ponto: '👷 Ponto da equipe', funcionario: '➕ Novo profissional',
  despesa: '💰 Despesa', editar_despesa: '✎ Corrigir lançamento', pagar_despesa: '✓ Baixa de pagamento',
  excluir_despesa: '🗑 Excluir lançamento', foto: '📷 Fotos do andamento',
};

const conversas = {};      // obraId -> [mensagens]
let anexos = [];           // Files aguardando envio
let rascunho = '';
let dataReferencia = null;
let status = null;         // { disponivel, provedores }
let reconhecimento = null;
let ouvindo = false;
let enviando = false;

const autoLigado = () => lerPreferencia(CHAVE_AUTO) !== '0';
const conversa = () => (conversas[estado.obraId] ||= []);
const semAcento = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();

/** Abre o assistente já apontando para um dia (chamado pelo diário). */
export function abrirAssistente(data) {
  if (data) dataReferencia = data;
  navegar('assistente');
}

// ---------------------------------------------------------------- render

export async function renderizar(raiz) {
  if (!estado.obraId) { raiz.innerHTML = vazio('🤖', 'Selecione uma obra para usar o assistente.'); return; }
  if (!podeLancar()) { raiz.innerHTML = vazio('🤖', 'Seu perfil é somente leitura.'); return; }

  if (!status) {
    try { status = await api.get('/api/assistente/status', { silencioso: true }); } catch (_) { status = { disponivel: false }; }
  }
  if (!dataReferencia) dataReferencia = estado.diaAtivo?.data || hojeISO();

  const mensagens = conversa();
  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">ASSISTENTE DA OBRA</span><h2>Fale o que foi feito</h2></div>
      <button class="btn btn-ghost btn-small" data-acao="voltar">‹ Diário</button>
    </div>

    ${status.disponivel ? '' : `
      <div class="alerta aviso">
        <span class="alerta-ico">!</span>
        <div><b>Assistente desligado no servidor</b>
          <div class="small">Configure a variável <b>GEMINI_API_KEY</b> (gratuita, no Google AI Studio)
          ou <b>ANTHROPIC_API_KEY</b> no Render. O passo a passo está no README.</div></div>
      </div>`}

    <div class="card ia-config">
      <div class="row-between">
        <div style="flex:1;min-width:0">
          <span class="field-label" style="margin-top:0">DIA DE REFERÊNCIA ("hoje")</span>
          <input type="date" data-campo="referencia" value="${esc(dataReferencia)}">
        </div>
      </div>
      <label class="checkbox-row" style="margin-bottom:0">
        <input type="checkbox" data-campo="auto" ${autoLigado() ? 'checked' : ''}>
        <span>Gravar automaticamente <span class="small faint">(exclusões sempre pedem confirmação)</span></span>
      </label>
    </div>

    <div class="ia-conversa" id="iaConversa">
      ${mensagens.length ? mensagens.map(htmlMensagem).join('') : htmlBoasVindas()}
      ${enviando ? '<div class="ia-msg ia-assistente"><div class="ia-balao ia-pensando">Analisando<span>.</span><span>.</span><span>.</span></div></div>' : ''}
    </div>

    <div class="card ia-composer">
      ${anexos.length ? `<div class="ia-anexos">${anexos.map((a, i) => `
        <span class="ia-anexo">${a.type === 'application/pdf' ? '📄' : '🖼'} ${esc(a.name)}
          <button data-remover-anexo="${i}" aria-label="Remover anexo">✕</button></span>`).join('')}</div>` : ''}
      <textarea id="iaTexto" rows="3" placeholder="Ex: Hoje o João e o Pedro fizeram 8 horas, o Carlos saiu meio-dia (médico). Concretamos a laje do 2º pavimento. Comprei 20 sacos de cimento na Casa do Construtor, nota 4521, R$ 1.380, paguei do meu bolso.">${esc(rascunho)}</textarea>
      <div class="ia-barra">
        <button class="icon-btn ${ouvindo ? 'ia-gravando' : ''}" data-acao="microfone" aria-label="Ditar por voz" title="Ditar por voz">🎤</button>
        <button class="icon-btn" data-acao="camera" aria-label="Tirar foto" title="Tirar foto">📷</button>
        <button class="icon-btn" data-acao="anexar" aria-label="Anexar arquivo" title="Anexar foto ou PDF">📎</button>
        <button class="btn btn-brass ia-enviar" data-acao="enviar" ${enviando || !status.disponivel ? 'disabled' : ''}>
          ${enviando ? 'Analisando...' : 'Enviar'}</button>
      </div>
      ${ouvindo ? '<p class="small ia-ouvindo">● Ouvindo... fale normalmente e toque no 🎤 para parar.</p>' : ''}
      <input type="file" id="iaArquivos" accept="image/*,application/pdf" multiple class="hidden">
      <input type="file" id="iaCamera" accept="image/*" capture="environment" class="hidden">
    </div>
  `;
  ligarEventos(raiz);
  const fim = raiz.querySelector('#iaConversa');
  if (fim && (mensagens.length || enviando)) fim.lastElementChild?.scrollIntoView({ block: 'nearest' });
}

function htmlBoasVindas() {
  return `
    <div class="ia-msg ia-assistente"><div class="ia-balao">
      Me conte o dia como contaria para um colega: quem trabalhou, o que foi executado, o clima,
      ocorrências e o que foi comprado. Pode anexar a nota fiscal, cupom ou boleto: eu leio valor,
      número da nota, fornecedor e vencimento.${podeFinanceiro() ? '' : ' (Despesas ficam com o gestor.)'}
      <div class="small faint" style="margin-top:8px">Dica: toque no 🎤 e fale. Depois toque em Enviar.</div>
    </div></div>`;
}

function htmlMensagem(m, indice) {
  if (m.papel === 'usuario') {
    return `<div class="ia-msg ia-usuario"><div class="ia-balao">
      ${esc(m.texto || '').replace(/\n/g, '<br>') || '<span class="faint">(só anexos)</span>'}
      ${m.nomesArquivos?.length ? `<div class="small ia-arquivos">📎 ${m.nomesArquivos.map(esc).join(', ')}</div>` : ''}
    </div></div>`;
  }
  if (m.erro) {
    return `<div class="ia-msg ia-assistente"><div class="ia-balao ia-erro">⚠ ${esc(m.erro)}</div></div>`;
  }
  const pendentes = m.acoes.filter((a) => a.estado === 'pendente' && a.incluir).length;
  return `
    <div class="ia-msg ia-assistente" data-msg="${indice}">
      <div class="ia-balao">${esc(m.texto)}
        <div class="small faint" style="margin-top:6px">via ${m.fonte === 'anthropic' ? 'Claude' : 'Gemini'}</div>
      </div>
      ${(m.avisos || []).map((a) => `<div class="small ia-aviso">• ${esc(a)}</div>`).join('')}
      ${m.acoes.map((a, i) => htmlAcao(m, a, i)).join('')}
      ${pendentes ? `
        <button class="btn btn-brass" data-gravar="${indice}" ${m.gravando ? 'disabled' : ''}>
          ${m.gravando ? 'Gravando...' : `Gravar ${pendentes} lançamento(s)`}</button>` : ''}
    </div>`;
}

function seloEstado(a) {
  if (a.estado === 'ok') return '<span class="badge badge-pago">gravado</span>';
  if (a.estado === 'erro') return '<span class="badge badge-vencida">erro</span>';
  if (a.estado === 'gravando') return '<span class="badge badge-neutro">gravando...</span>';
  if (a.faltando?.length) return `<span class="badge badge-em-aberto">falta ${esc(a.faltando.join(' e '))}</span>`;
  return '';
}

function campo(i, nome, valor, { tipo = 'text', rotulo = '', placeholder = '' } = {}) {
  return `<label class="ia-campo"><span>${esc(rotulo)}</span>
    <input type="${tipo}" data-a="${i}" data-campo="${nome}" value="${esc(valor ?? '')}" placeholder="${esc(placeholder)}"
      ${tipo === 'number' ? 'inputmode="decimal" step="0.01"' : ''}></label>`;
}

function selecao(i, nome, valor, opcoes, rotulo) {
  return `<label class="ia-campo"><span>${esc(rotulo)}</span><select data-a="${i}" data-campo="${nome}">
    ${opcoes.map((o) => `<option${o === valor ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
}

function htmlAcao(m, a, i) {
  const editavel = a.estado === 'pendente' || a.estado === 'erro';
  const dis = editavel ? '' : ' disabled';
  let corpo = '';

  if (a.tipo === 'diario') {
    corpo = `
      <label class="ia-campo bloco"><span>Serviços executados</span>
        <textarea data-a="${i}" data-campo="producao_dia" rows="4"${dis}>${esc(a.producao_dia)}</textarea></label>
      ${campo(i, 'clima', a.clima, { rotulo: 'Clima' })}
      <label class="ia-campo bloco"><span>Observações</span>
        <textarea data-a="${i}" data-campo="observacoes" rows="3"${dis}>${esc(a.observacoes)}</textarea></label>`;
  } else if (a.tipo === 'ponto') {
    corpo = a.registros.map((r, j) => `
      <div class="ia-ponto">
        <span class="ia-ponto-nome">${esc(r.nome)}${r.funcionario_id ? '' : ' <span class="badge badge-em-aberto">novo</span>'}</span>
        <input type="number" min="0" max="24" step="0.5" inputmode="decimal" data-a="${i}" data-reg="${j}" data-campo="horas" value="${esc(r.horas)}"${dis}>
        <input type="text" placeholder="justificativa" data-a="${i}" data-reg="${j}" data-campo="justificativa" value="${esc(r.justificativa)}"${dis}>
      </div>`).join('');
  } else if (a.tipo === 'funcionario') {
    corpo = campo(i, 'nome', a.nome, { rotulo: 'Nome' }) + campo(i, 'funcao', a.funcao, { rotulo: 'Função' });
  } else if (a.tipo === 'despesa') {
    corpo = `
      <div class="ia-grade">
        ${selecao(i, 'categoria', a.categoria, CATEGORIAS, 'Categoria')}
        ${campo(i, 'valor', a.valor || '', { tipo: 'number', rotulo: a.parcelas ? 'Valor total' : 'Valor (R$)' })}
      </div>
      ${campo(i, 'descricao', a.descricao, { rotulo: 'Descrição' })}
      <div class="ia-grade">
        ${campo(i, 'fornecedor', a.fornecedor, { rotulo: 'Fornecedor' })}
        ${campo(i, 'numero_nota', a.numero_nota, { rotulo: 'Nº da nota' })}
      </div>
      <div class="ia-grade">
        ${selecao(i, 'status_pagamento', a.status_pagamento, STATUS, 'Situação')}
        ${a.status_pagamento === 'Reembolso'
          ? campo(i, 'reembolsar_para', a.reembolsar_para, { rotulo: 'Reembolsar para' })
          : campo(i, 'vencimento', a.vencimento, { tipo: 'date', rotulo: a.parcelas ? '1º vencimento' : 'Vencimento' })}
      </div>
      ${a.parcelas ? `<p class="small muted">Parcelada em ${a.parcelas}x (${esc(a.parcela_frequencia)}) de ~${moeda((a.valor || 0) / a.parcelas)}</p>` : ''}
      ${a.anexos?.length ? `<p class="small muted">📎 ${a.anexos.map((k) => esc(m.arquivos[k]?.name || '')).join(', ')}</p>` : ''}`;
  } else if (a.tipo === 'editar_despesa') {
    const atual = a.despesa_atual || {};
    corpo = `<p class="small"><b>${esc(atual.descricao)}</b> · ${moeda(atual.valor)} · ${dataBR(atual.data)}</p>
      ${Object.entries(a.alteracoes || {}).map(([k, v]) => `
        <p class="small">${esc(k.replace('_', ' '))}: <s class="faint">${esc(k === 'valor' ? moeda(atual[k]) : (atual[k] || '—'))}</s>
        → <b>${esc(k === 'valor' ? moeda(v) : v)}</b></p>`).join('')}
      ${a.anexos?.length ? `<p class="small muted">📎 anexar ${a.anexos.map((k) => esc(m.arquivos[k]?.name || '')).join(', ')}</p>` : ''}`;
  } else if (a.tipo === 'pagar_despesa' || a.tipo === 'excluir_despesa') {
    const atual = a.despesa_atual || {};
    corpo = `<p class="small"><b>${esc(atual.descricao)}</b>${atual.fornecedor ? ` · ${esc(atual.fornecedor)}` : ''}
      · ${moeda(atual.valor)}${atual.numero_nota ? ` · NF ${esc(atual.numero_nota)}` : ''}</p>
      ${a.tipo === 'pagar_despesa' ? campo(i, 'pago_em', a.pago_em, { tipo: 'date', rotulo: 'Pago em' }) : ''}`;
  } else if (a.tipo === 'foto') {
    corpo = `<div class="ia-fotos">${a.anexos.map((k) => {
      const arq = m.arquivos[k];
      return arq ? `<img src="${esc(urlDe(arq))}" alt="${esc(arq.name)}">` : '';
    }).join('')}</div>
      <div class="ia-grade">
        ${campo(i, 'ambiente', a.ambiente, { rotulo: 'Ambiente' })}
        ${campo(i, 'legenda', a.legenda, { rotulo: 'Legenda' })}
      </div>`;
  }

  const mostraData = !['editar_despesa', 'pagar_despesa', 'excluir_despesa', 'funcionario'].includes(a.tipo);
  return `
    <div class="ia-acao ${a.estado}" data-acao-card="${i}">
      <div class="row-between">
        <label class="ia-acao-titulo">
          ${editavel ? `<input type="checkbox" data-a="${i}" data-campo="incluir" ${a.incluir ? 'checked' : ''}>` : ''}
          <b>${TITULOS[a.tipo] || a.tipo}</b>
        </label>
        ${seloEstado(a)}
      </div>
      ${a.resumo ? `<div class="small muted">${esc(a.resumo)}</div>` : ''}
      ${mostraData ? `<div class="ia-data">${campo(i, 'data', a.data, { tipo: 'date', rotulo: 'Dia' })}</div>` : ''}
      <fieldset class="ia-corpo"${dis}>${corpo}</fieldset>
      ${a.estado === 'erro' ? `<p class="small ia-erro-txt">⚠ ${esc(a.erro)}</p>` : ''}
      ${a.estado === 'erro' && a.codigo === 'nota_duplicada' ? `<button class="btn btn-outline btn-small" data-forcar="${i}">Lançar mesmo assim</button>` : ''}
      ${a.tipo === 'excluir_despesa' && a.estado === 'pendente' ? '<p class="small ia-erro-txt">Exclusão: só acontece quando você tocar em Gravar.</p>' : ''}
    </div>`;
}

const urls = new WeakMap();
function urlDe(arquivo) {
  if (!urls.has(arquivo)) urls.set(arquivo, URL.createObjectURL(arquivo));
  return urls.get(arquivo);
}

// ---------------------------------------------------------------- eventos

function ligarEventos(raiz) {
  raiz.querySelector('[data-acao=voltar]').onclick = () => navegar('diario');
  raiz.querySelector('[data-campo=referencia]').onchange = (e) => { dataReferencia = e.target.value || hojeISO(); };
  raiz.querySelector('[data-campo=auto]').onchange = (e) => salvarPreferencia(CHAVE_AUTO, e.target.checked ? '1' : '0');

  const texto = raiz.querySelector('#iaTexto');
  texto.oninput = () => { rascunho = texto.value; };

  const inputArquivos = raiz.querySelector('#iaArquivos');
  const inputCamera = raiz.querySelector('#iaCamera');
  const adicionar = (lista) => {
    anexos = [...anexos, ...Array.from(lista)].slice(0, 10);
    renderizar(raiz);
  };
  raiz.querySelector('[data-acao=anexar]').onclick = () => inputArquivos.click();
  raiz.querySelector('[data-acao=camera]').onclick = () => inputCamera.click();
  inputArquivos.onchange = () => adicionar(inputArquivos.files);
  inputCamera.onchange = () => adicionar(inputCamera.files);
  raiz.querySelectorAll('[data-remover-anexo]').forEach((btn) => {
    btn.onclick = () => { anexos.splice(Number(btn.dataset.removerAnexo), 1); renderizar(raiz); };
  });

  raiz.querySelector('[data-acao=microfone]').onclick = () => alternarMicrofone(raiz);
  raiz.querySelector('[data-acao=enviar]').onclick = () => enviar(raiz);

  // Edição dos cartões: o objeto da ação acompanha o que é digitado.
  raiz.querySelectorAll('[data-msg]').forEach((bloco) => {
    const msg = conversa()[Number(bloco.dataset.msg)];
    bloco.querySelectorAll('[data-a]').forEach((inp) => {
      const acao = msg.acoes[Number(inp.dataset.a)];
      const evento = inp.type === 'checkbox' || inp.tagName === 'SELECT' ? 'change' : 'input';
      inp.addEventListener(evento, () => {
        const nome = inp.dataset.campo;
        const valor = inp.type === 'checkbox' ? inp.checked : inp.value;
        if (inp.dataset.reg !== undefined) {
          acao.registros[Number(inp.dataset.reg)][nome] = nome === 'horas' ? parseFloat(valor) || 0 : valor;
          return;
        }
        acao[nome] = nome === 'valor' ? parseFloat(String(valor).replace(',', '.')) || 0 : valor;
        if (nome === 'valor' || nome === 'descricao') {
          acao.faltando = [!acao.descricao && 'descrição', !acao.valor && 'valor'].filter(Boolean);
        }
        if (['incluir', 'status_pagamento'].includes(nome)) renderizar(raiz);
      });
    });
    const gravar = bloco.querySelector('[data-gravar]');
    if (gravar) gravar.onclick = () => aplicar(msg, raiz);
    bloco.querySelectorAll('[data-forcar]').forEach((btn) => {
      btn.onclick = () => {
        const acao = msg.acoes[Number(btn.dataset.forcar)];
        acao.confirmarDuplicidade = true;
        acao.estado = 'pendente';
        acao.incluir = true;
        aplicar(msg, raiz);
      };
    });
  });
}

function alternarMicrofone(raiz) {
  const Reconhecedor = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Reconhecedor) {
    toast('Este navegador não dita por voz. Use o microfone do teclado do celular.', 'erro');
    return;
  }
  if (ouvindo && reconhecimento) { reconhecimento.stop(); return; }

  const base = rascunho ? rascunho.replace(/\s*$/, ' ') : '';
  let finais = '';
  reconhecimento = new Reconhecedor();
  reconhecimento.lang = 'pt-BR';
  reconhecimento.continuous = true;
  reconhecimento.interimResults = true;
  reconhecimento.onresult = (evento) => {
    let parcial = '';
    for (let i = evento.resultIndex; i < evento.results.length; i += 1) {
      const trecho = evento.results[i][0].transcript;
      if (evento.results[i].isFinal) finais += trecho + ' '; else parcial += trecho;
    }
    rascunho = (base + finais + parcial).trimStart();
    const campoTexto = document.querySelector('#iaTexto');
    if (campoTexto) campoTexto.value = rascunho;
  };
  reconhecimento.onerror = (e) => {
    if (e.error === 'not-allowed') toast('Permita o uso do microfone para ditar.', 'erro');
  };
  reconhecimento.onend = () => { ouvindo = false; rascunho = (base + finais).trim(); renderizar(raiz); };
  reconhecimento.start();
  ouvindo = true;
  renderizar(raiz);
}

// ---------------------------------------------------------------- envio

function historicoParaServidor() {
  return conversa().slice(-8).map((m) => {
    if (m.papel === 'usuario') return { papel: 'usuario', texto: m.texto || '(anexos)' };
    const gravados = (m.acoes || []).filter((a) => a.estado === 'ok').map((a) => a.resumo).filter(Boolean);
    return { papel: 'assistente', texto: (m.texto || m.erro || '') + (gravados.length ? ` [gravado: ${gravados.join('; ')}]` : '') };
  });
}

async function enviar(raiz) {
  if (enviando) return;
  if (ouvindo && reconhecimento) reconhecimento.stop();
  const mensagem = (document.querySelector('#iaTexto')?.value || rascunho).trim();
  if (!mensagem && !anexos.length) { toast('Fale, escreva ou anexe algo.'); return; }

  const historico = historicoParaServidor();
  const { arquivos } = await comprimirLote(anexos);
  conversa().push({ papel: 'usuario', texto: mensagem, nomesArquivos: arquivos.map((a) => a.name) });
  rascunho = '';
  anexos = [];
  enviando = true;
  await renderizar(raiz);

  const dados = new FormData();
  dados.append('mensagem', mensagem);
  dados.append('data_referencia', dataReferencia || hojeISO());
  dados.append('historico', JSON.stringify(historico));
  arquivos.forEach((a) => dados.append('arquivos', a));

  try {
    const r = await api.form(`/api/obras/${estado.obraId}/assistente`, dados, { silencioso: true });
    const msg = {
      papel: 'assistente', texto: r.resposta, fonte: r.fonte, avisos: r.avisos || [], arquivos,
      acoes: (r.acoes || []).map((a) => ({ ...a, estado: 'pendente', incluir: true })),
    };
    conversa().push(msg);
    enviando = false;
    const automaticas = msg.acoes.filter((a) => !a.requer_confirmacao && !a.faltando?.length);
    if (autoLigado() && automaticas.length) {
      msg.acoes.forEach((a) => { a.incluir = automaticas.includes(a); });
      await renderizar(raiz);
      await aplicar(msg, raiz);
      msg.acoes.forEach((a) => { if (a.estado === 'pendente') a.incluir = true; });
    }
  } catch (erro) {
    conversa().push({ papel: 'assistente', erro: erro.message || 'Não foi possível falar com o assistente.' });
    // Devolve o texto para o campo: falhou, ninguém precisa ditar tudo de novo.
    rascunho = mensagem;
  } finally {
    enviando = false;
    await renderizar(raiz);
  }
}

// ---------------------------------------------------------------- gravação

async function aplicar(msg, raiz) {
  if (msg.gravando) return;
  msg.gravando = true;
  const fila = msg.acoes
    .filter((a) => a.incluir && a.estado === 'pendente')
    .sort((x, y) => ORDEM.indexOf(x.tipo) - ORDEM.indexOf(y.tipo));
  if (!fila.length) { msg.gravando = false; return; }
  renderizar(raiz);

  const dias = {};
  const novos = {};
  let semanaCriada = false;
  const diaDe = async (data) => {
    const alvo = data || dataReferencia || hojeISO();
    if (!dias[alvo]) {
      const r = await api.post(`/api/obras/${estado.obraId}/dia-por-data`, { data: alvo }, { silencioso: true });
      dias[alvo] = r.dia_id;
      semanaCriada = semanaCriada || r.semana_criada;
    }
    return dias[alvo];
  };
  const arquivosDe = (indices) => (indices || []).map((k) => msg.arquivos[k]).filter(Boolean);

  for (const a of fila) {
    a.estado = 'gravando';
    try {
      await gravarAcao(a, { diaDe, arquivosDe, novos });
      a.estado = 'ok';
    } catch (erro) {
      a.estado = 'erro';
      a.erro = erro.message || 'Falha ao gravar.';
      a.codigo = erro.codigo || '';
    }
  }
  msg.gravando = false;

  const ok = fila.filter((a) => a.estado === 'ok').length;
  const falhas = fila.length - ok;
  toast(falhas ? `${ok} gravado(s), ${falhas} com erro — confira abaixo.` : `${ok} lançamento(s) gravado(s).`,
    falhas ? 'erro' : 'ok');
  if (ok) await recarregarSemana({ manterDia: true, recarregarLista: semanaCriada });
  else renderizar(raiz);
}

async function gravarAcao(a, { diaDe, arquivosDe, novos }) {
  const opcoes = { silencioso: true };

  if (a.tipo === 'funcionario') {
    const r = await api.post(`/api/obras/${estado.obraId}/funcionarios`, { nome: a.nome, funcao: a.funcao }, opcoes);
    novos[semAcento(a.nome)] = r.id;
    estado.funcionarios = [...estado.funcionarios, { id: r.id, nome: a.nome, funcao: a.funcao }];
    return;
  }

  if (a.tipo === 'diario') {
    await api.put(`/api/dias/${await diaDe(a.data)}`,
      { producao_dia: a.producao_dia, clima: a.clima, observacoes: a.observacoes }, opcoes);
    return;
  }

  if (a.tipo === 'ponto') {
    const registros = a.registros.map((r) => ({
      funcionario_id: r.funcionario_id || novos[semAcento(r.nome)] || 0,
      horas: Number(r.horas) || 0,
      justificativa: r.justificativa || '',
    })).filter((r) => r.funcionario_id);
    if (!registros.length) throw new Error('Nenhum funcionário cadastrado para este ponto.');
    await api.put(`/api/dias/${await diaDe(a.data)}/horas`, { registros }, opcoes);
    return;
  }

  if (a.tipo === 'foto') {
    const dados = new FormData();
    arquivosDe(a.anexos).forEach((f) => dados.append('fotos', f));
    if (a.legenda) dados.append('legenda', a.legenda);
    if (a.ambiente) dados.append('ambiente', a.ambiente);
    await api.form(`/api/dias/${await diaDe(a.data)}/fotos`, dados, opcoes);
    return;
  }

  if (a.tipo === 'despesa') {
    if (!a.descricao || !a.valor) throw new Error('Preencha descrição e valor antes de gravar.');
    const dados = new FormData();
    const campos = {
      categoria: a.categoria, descricao: a.descricao, fornecedor: a.fornecedor, valor: a.valor,
      status_pagamento: a.status_pagamento, numero_nota: a.numero_nota, vencimento: a.vencimento,
      reembolsar_para: a.status_pagamento === 'Reembolso' ? a.reembolsar_para : '',
    };
    if (a.parcelas) Object.assign(campos, { parcelas: a.parcelas, parcela_frequencia: a.parcela_frequencia });
    if (a.confirmarDuplicidade) campos.confirmar_duplicidade = '1';
    Object.entries(campos).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') dados.append(k, v); });
    arquivosDe(a.anexos).forEach((f) => dados.append('anexos', f));
    await api.form(`/api/dias/${await diaDe(a.data)}/despesas`, dados, opcoes);
    return;
  }

  if (a.tipo === 'editar_despesa') {
    const alteracoes = { ...(a.alteracoes || {}) };
    if (a.confirmarDuplicidade) alteracoes.confirmar_duplicidade = true;
    if (Object.keys(a.alteracoes || {}).length) await api.put(`/api/despesas/${a.despesa_id}`, alteracoes, opcoes);
    const arquivos = arquivosDe(a.anexos);
    if (arquivos.length) {
      const dados = new FormData();
      arquivos.forEach((f) => dados.append('anexos', f));
      await api.form(`/api/despesas/${a.despesa_id}/anexos`, dados, opcoes);
    }
    return;
  }

  if (a.tipo === 'pagar_despesa') {
    await api.post(`/api/despesas/${a.despesa_id}/pagar`, { pago_em: a.pago_em || hojeISO() }, opcoes);
    return;
  }

  if (a.tipo === 'excluir_despesa') {
    await api.del(`/api/despesas/${a.despesa_id}`, opcoes);
  }
}

