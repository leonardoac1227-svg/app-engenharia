/* ==========================================================================
   ASSISTENTE — fale ou anexe, a IA faz no app o que você pediu.

   O gestor dita como numa conversa e anexa fotos, notas, comprovantes e
   áudios. O servidor devolve ações do catálogo (diário, ponto, equipe, fotos,
   despesas, frete, aluguel, semana, folha, relatório, obra) já conferidas.

   Gravar também é com o servidor (/assistente/aplicar), que confere tudo de
   novo e usa os mesmos endpoints dos formulários — o mesmo caminho do bot do
   Telegram. Com "Gravar automaticamente", o que está completo é gravado na
   hora; o que é irreversível ou em lote espera um toque.
   ========================================================================== */

import { api, baixar } from '../api.js';
import { comprimirLote } from '../compressao.js';
import { estado, lerPreferencia, podeFinanceiro, podeLancar, salvarPreferencia } from '../estado.js';
import { navegar, recarregarSemana } from '../navegacao.js';
import { esc, hojeISO, toast, vazio } from '../util.js';

const CHAVE_AUTO = 'obraerp:ia_auto';
const CATEGORIAS = ['Material', 'Serviço', 'Locação', 'Frete', 'Alimentação', 'Outro'];
const STATUS = ['Em Aberto', 'Pago', 'Reembolso'];

// Campos que dá para corrigir no cartão antes de gravar. O resto aparece na descrição.
const EDITAVEIS = {
  data: ['Dia', 'date'], descricao: ['Descrição', 'text'], valor: ['Valor (R$)', 'number'],
  fornecedor: ['Fornecedor', 'text'], numero_nota: ['Nº da nota', 'text'],
  categoria: ['Categoria', CATEGORIAS], status_pagamento: ['Situação', STATUS],
  vencimento: ['Vencimento', 'date'], reembolsar_para: ['Reembolsar para', 'text'],
  producao_dia: ['Serviços executados', 'textarea'], clima: ['Clima', 'text'],
  observacoes: ['Observações', 'textarea'], legenda: ['Legenda', 'text'], ambiente: ['Ambiente', 'text'],
  nome: ['Nome', 'text'], funcao: ['Função', 'text'], meta: ['Meta', 'text'],
  orcamento_previsto: ['Orçamento previsto', 'number'], pago_em: ['Pago em', 'date'],
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
          no Render. O passo a passo está no README.</div></div>
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
        <span>Gravar automaticamente <span class="small faint">(exclusões, baixas em lote e fechar semana pedem confirmação)</span></span>
      </label>
    </div>

    <div class="ia-conversa" id="iaConversa">
      ${mensagens.length ? mensagens.map(htmlMensagem).join('') : htmlBoasVindas()}
      ${enviando ? '<div class="ia-msg ia-assistente"><div class="ia-balao ia-pensando">Analisando<span>.</span><span>.</span><span>.</span></div></div>' : ''}
    </div>

    <div class="card ia-composer">
      ${anexos.length ? `<div class="ia-anexos">${anexos.map((a, i) => `
        <span class="ia-anexo">${a.type === 'application/pdf' ? '📄' : (a.type.startsWith('audio/') ? '🎙' : '🖼')} ${esc(a.name)}
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
      <input type="file" id="iaArquivos" accept="image/*,application/pdf,audio/*" multiple class="hidden">
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
      Faço tudo que o app faz. Conte o dia como contaria para um colega: quem trabalhou e quantas horas,
      o que foi executado, o clima, as ocorrências e o que foi comprado. Anexe nota, cupom, boleto ou
      comprovante de PIX: eu leio o total, o número e o fornecedor.${podeFinanceiro() ? '' : ' (Despesas ficam com o gestor.)'}
      <div class="small faint" style="margin-top:8px">Também posso: mudar a situação de uma despesa, dar baixa,
      cadastrar ou desligar alguém, lançar aluguel e frete, definir a meta da semana, mandar o relatório e
      responder perguntas como "quanto gastei essa semana?".</div>
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
      <div class="ia-balao">
        ${m.transcricao ? `<div class="small muted" style="margin-bottom:6px">🎙 ${esc(m.transcricao)}</div>` : ''}
        ${esc(m.texto)}
        <div class="small faint" style="margin-top:6px">via ${esc(m.modelo || m.fonte || 'IA')}</div>
      </div>
      ${(m.avisos || []).map((a) => `<div class="small ia-aviso">• ${esc(a)}</div>`).join('')}
      ${m.acoes.map((a, i) => htmlAcao(a, i)).join('')}
      ${pendentes ? `
        <button class="btn btn-brass" data-gravar="${indice}" ${m.gravando ? 'disabled' : ''}>
          ${m.gravando ? 'Gravando...' : `Gravar ${pendentes} ação(ões)`}</button>` : ''}
    </div>`;
}

function seloEstado(a) {
  if (a.estado === 'ok') return '<span class="badge badge-pago">feito</span>';
  if (a.estado === 'erro') return '<span class="badge badge-vencida">erro</span>';
  if (a.estado === 'gravando') return '<span class="badge badge-neutro">gravando...</span>';
  if (a.dados.faltando?.length) return `<span class="badge badge-em-aberto">falta ${esc(a.dados.faltando.join(' e '))}</span>`;
  if (a.confirmar) return '<span class="badge badge-em-aberto">confirme</span>';
  return '';
}

function htmlCampo(i, nome, valor) {
  const [rotulo, tipo] = EDITAVEIS[nome];
  if (Array.isArray(tipo)) {
    return `<label class="ia-campo"><span>${esc(rotulo)}</span><select data-a="${i}" data-campo="${nome}">
      ${tipo.map((o) => `<option${o === valor ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
  }
  if (tipo === 'textarea') {
    return `<label class="ia-campo bloco"><span>${esc(rotulo)}</span>
      <textarea data-a="${i}" data-campo="${nome}" rows="3">${esc(valor)}</textarea></label>`;
  }
  return `<label class="ia-campo"><span>${esc(rotulo)}</span>
    <input type="${tipo}" data-a="${i}" data-campo="${nome}" value="${esc(valor ?? '')}"
      ${tipo === 'number' ? 'inputmode="decimal" step="0.01"' : ''}></label>`;
}

function htmlAcao(a, i) {
  const editavel = a.estado === 'pendente' || a.estado === 'erro';
  const campos = Object.keys(EDITAVEIS).filter((k) => k in a.dados && typeof a.dados[k] !== 'object');
  const registros = Array.isArray(a.dados.registros) ? a.dados.registros : null;
  return `
    <div class="ia-acao ${a.estado}">
      <div class="row-between">
        <label class="ia-acao-titulo">
          ${editavel ? `<input type="checkbox" data-a="${i}" data-campo="incluir" ${a.incluir ? 'checked' : ''}>` : ''}
          <b>${esc(a.titulo)}</b>
        </label>
        ${seloEstado(a)}
      </div>
      <div class="small muted">${esc(a.descricao)}</div>
      ${editavel && (campos.length || registros) ? `
        <details class="ia-editar"><summary class="small">Conferir / corrigir</summary>
          <fieldset class="ia-corpo">
            ${registros ? registros.map((r, j) => `
              <div class="ia-ponto">
                <span class="ia-ponto-nome">${esc(r.nome)}${r.funcionario_id ? '' : ' <span class="badge badge-em-aberto">novo</span>'}</span>
                <input type="number" min="0" max="24" step="0.5" inputmode="decimal" data-a="${i}" data-reg="${j}" data-campo="horas" value="${esc(r.horas)}">
                <input type="text" placeholder="justificativa" data-a="${i}" data-reg="${j}" data-campo="justificativa" value="${esc(r.justificativa)}">
              </div>`).join('') : ''}
            <div class="ia-grade">${campos.filter((k) => !['producao_dia', 'observacoes'].includes(k)).map((k) => htmlCampo(i, k, a.dados[k])).join('')}</div>
            ${campos.filter((k) => ['producao_dia', 'observacoes'].includes(k)).map((k) => htmlCampo(i, k, a.dados[k])).join('')}
          </fieldset>
        </details>` : ''}
      ${a.estado === 'erro' ? `<p class="small ia-erro-txt">⚠ ${esc(a.erro)}</p>` : ''}
      ${a.estado === 'erro' && a.codigo === 'nota_duplicada' ? `<button class="btn btn-outline btn-small" data-forcar="${i}">Lançar mesmo assim</button>` : ''}
      ${a.link ? `<button class="btn btn-outline btn-small" data-baixar="${esc(a.link)}">⬇ Baixar</button>` : ''}
    </div>`;
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
  raiz.querySelectorAll('[data-baixar]').forEach((btn) => { btn.onclick = () => baixar(btn.dataset.baixar); });

  // Edição dos cartões: os dados da ação acompanham o que é digitado.
  raiz.querySelectorAll('[data-msg]').forEach((bloco) => {
    const msg = conversa()[Number(bloco.dataset.msg)];
    bloco.querySelectorAll('[data-a]').forEach((inp) => {
      const acao = msg.acoes[Number(inp.dataset.a)];
      const evento = inp.type === 'checkbox' || inp.tagName === 'SELECT' ? 'change' : 'input';
      inp.addEventListener(evento, () => {
        const nome = inp.dataset.campo;
        const valor = inp.type === 'checkbox' ? inp.checked : inp.value;
        if (nome === 'incluir') { acao.incluir = valor; renderizar(raiz); return; }
        if (inp.dataset.reg !== undefined) {
          acao.dados.registros[Number(inp.dataset.reg)][nome] = nome === 'horas' ? parseFloat(valor) || 0 : valor;
          return;
        }
        acao.dados[nome] = ['valor', 'orcamento_previsto'].includes(nome)
          ? parseFloat(String(valor).replace(',', '.')) || 0 : valor;
        if (nome === 'valor' || nome === 'descricao') {
          const faltando = [!acao.dados.descricao && 'descrição', !acao.dados.valor && 'valor'].filter(Boolean);
          if (faltando.length) acao.dados.faltando = faltando; else delete acao.dados.faltando;
        }
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
    const feitos = (m.acoes || []).filter((a) => a.estado === 'ok').map((a) => a.descricao).filter(Boolean);
    return { papel: 'assistente', texto: (m.texto || m.erro || '') + (feitos.length ? ` [gravado: ${feitos.join('; ')}]` : '') };
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
      papel: 'assistente', texto: r.resposta, fonte: r.fonte, modelo: r.modelo, transcricao: r.transcricao,
      avisos: r.avisos || [], arquivos,
      acoes: (r.acoes || []).map((a) => ({ ...a, estado: 'pendente', incluir: true })),
    };
    conversa().push(msg);
    enviando = false;
    const automaticas = msg.acoes.filter((a) => !a.confirmar && !a.dados.faltando?.length);
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
  const fila = msg.acoes.filter((a) => a.incluir && a.estado === 'pendente');
  if (!fila.length) return;
  msg.gravando = true;
  fila.forEach((a) => { a.estado = 'gravando'; });
  renderizar(raiz);

  const dados = new FormData();
  dados.append('acoes', JSON.stringify(fila.map((a) => ({
    tipo: a.tipo, dados: a.dados, confirmar_duplicidade: !!a.confirmarDuplicidade,
  }))));
  dados.append('data_referencia', dataReferencia || hojeISO());
  (msg.arquivos || []).forEach((f) => dados.append('arquivos', f));

  try {
    const r = await api.form(`/api/obras/${estado.obraId}/assistente/aplicar`, dados, { silencioso: true });
    r.resultados.forEach((res) => {
      const a = fila[res.indice];
      if (!a) return;
      a.estado = res.ok ? 'ok' : 'erro';
      a.erro = res.erro || '';
      a.codigo = res.codigo || '';
      if (res.descricao) a.descricao = res.descricao;
      if (res.link) a.link = res.link;
    });
  } catch (erro) {
    fila.forEach((a) => { a.estado = 'erro'; a.erro = erro.message || 'Falha ao gravar.'; });
  }
  msg.gravando = false;

  const ok = fila.filter((a) => a.estado === 'ok').length;
  const falhas = fila.length - ok;
  toast(falhas ? `${ok} feito(s), ${falhas} com erro — confira abaixo.` : `${ok} ação(ões) gravada(s).`,
    falhas ? 'erro' : 'ok');
  if (ok) await recarregarSemana({ manterDia: true, recarregarLista: true });
  else renderizar(raiz);
}
