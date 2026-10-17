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
const LIMITE_ANEXOS = 5;   // mesmo limite do servidor

/** Links dos anexos de um lançamento, um por arquivo. */
export function htmlAnexos(x) {
  const anexos = x.anexos?.length ? x.anexos
    : (x.anexo_url ? [{ url: x.anexo_url, nome: x.nome_arquivo || 'ver anexo' }] : []);
  if (!anexos.length) return '';
  return `<div class="item-sub anexos-links">${anexos.map((a) => `
    <a href="#" data-anexo="${esc(a.url)}" data-nome="${esc(a.nome)}">📎 ${esc(a.nome)}</a>`).join('')}</div>`;
}

/** Texto curto "reembolso p/ Fulano" para a linha do lançamento. */
export const textoReembolso = (x) => (
  x.status_pagamento === 'Reembolso' && x.reembolsar_para ? ` · reembolso p/ ${esc(x.reembolsar_para)}` : '');

/** Foto abre no visualizador; PDF abre em outra aba. */
export function ligarLinksAnexo(raiz) {
  raiz.querySelectorAll('[data-anexo]').forEach((link) => {
    link.onclick = (e) => {
      e.preventDefault();
      const url = link.dataset.anexo;
      if (/\.pdf$/i.test(link.dataset.nome) || /\.pdf$/i.test(url)) { window.open(url, '_blank', 'noopener'); return; }
      abrirLightbox(url, link.dataset.nome);
    };
  });
}

let filtroStatus = 'todos';

/** Modal de lançamento/edição de despesa — usado no diário e nesta view. */
export function abrirModalDespesa(dia, aoSalvar, despesa = null) {
  const edicao = !!despesa;
  const statusInicial = despesa?.status_pagamento || 'Em Aberto';
  const diaInicial = edicao ? despesa.dia_id : dia?.id;
  // Parcela agendada em outra semana não aparece na lista de dias: aí o dia fica como está.
  const podeTrocarDia = !edicao || estado.dias.some((d) => d.id === diaInicial);

  const opcoesDia = estado.dias.map((d) => `
    <option value="${d.id}"${d.id === diaInicial ? ' selected' : ''}>${esc(d.dia_semana)} · ${dataBR(d.data)}</option>`).join('');

  abrirModal(`
    <h3>${edicao ? 'Editar lançamento' : 'Lançar despesa'}</h3>

    ${podeTrocarDia ? `<label class="field-label">Dia do lançamento</label><select id="dspDia">${opcoesDia}</select>` : ''}

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

    ${edicao ? '' : `
      <div id="dspFreteBox" class="hidden">
        <div class="alerta" style="margin:14px 0 4px">
          <span class="alerta-ico">🚚</span>
          <div><b>Frete com várias viagens</b>
            <div class="small">O valor acima é o de <b>cada</b> viagem. Cada uma vira um
            lançamento no dia em que aconteceu.</div>
          </div>
        </div>

        <label class="field-label">Quem fez o frete</label>
        <input type="text" id="dspFreteTransportador" placeholder="Ex: Diego Motoboy">

        <label class="field-label">Quantas viagens</label>
        <input type="number" id="dspFreteViagens" min="1" max="30" step="1" value="1" inputmode="numeric">

        <label class="field-label">Data de cada viagem</label>
        <div id="dspFreteDatas" class="stack"></div>
        <p class="small faint" id="dspFretePrevia" style="margin-top:8px"></p>
      </div>

      <label class="checkbox-row" style="margin-top:16px">
        <input type="checkbox" id="dspParcelar">
        <span>Nota parcelada</span>
      </label>

      <div id="dspParcelaBox" class="hidden">
        <div class="alerta" style="margin:4px 0 8px">
          <span class="alerta-ico">↻</span>
          <div><b>O valor acima é o total da nota</b>
            <div class="small">Ele será dividido, e cada parcela vira uma conta a pagar
            com seu próprio vencimento.</div>
          </div>
        </div>

        <label class="field-label">Número de parcelas</label>
        <input type="number" id="dspParcelas" min="2" max="36" step="1" value="2" inputmode="numeric">

        <label class="field-label">Intervalo entre as parcelas</label>
        <select id="dspParcelaFrequencia">
          <option value="mensal" selected>Mensal</option>
          <option value="quinzenal">Quinzenal</option>
          <option value="semanal">Semanal</option>
          <option value="dias">Intervalo personalizado (dias)</option>
        </select>

        <div id="dspParcelaIntervaloBox" class="hidden">
          <label class="field-label">A cada quantos dias</label>
          <input type="number" id="dspParcelaIntervalo" min="1" max="365" step="1" value="30" inputmode="numeric">
        </div>

        <p class="small faint" id="dspParcelaPrevia" style="margin-top:8px"></p>
      </div>`}

    <label class="field-label">Status do pagamento</label>
    <div class="segmented" id="dspStatus">
      ${STATUS.map((s) => `<button type="button" data-status="${s}" class="${s === statusInicial ? 'ativo' : ''}">${s}</button>`).join('')}
    </div>

    <div id="dspVencimentoBox" class="${statusInicial === 'Em Aberto' ? '' : 'hidden'}">
      <label class="field-label">Vencimento</label>
      <input type="date" id="dspVencimento" value="${esc(despesa?.vencimento || '')}">
    </div>

    <div id="dspReembolsoBox" class="${statusInicial === 'Reembolso' ? '' : 'hidden'}">
      <label class="field-label" for="dspReembolsarPara">Reembolsar para</label>
      <input type="text" id="dspReembolsarPara" maxlength="80" autocomplete="off"
             value="${esc(despesa?.reembolsar_para || estado.reembolsoPadrao)}"
             placeholder="Quem adiantou o dinheiro">
    </div>

    <label class="field-label">Notas fiscais / comprovantes <span class="faint" id="dspAnexoContagem"></span></label>
    <div id="dspAnexoLista" class="stack" style="gap:6px"></div>
    <div class="btn-linha" style="margin-top:8px">
      <button type="button" class="btn btn-outline btn-small" id="dspBtnCamera" style="flex:1">📷 Câmera</button>
      <button type="button" class="btn btn-outline btn-small" id="dspBtnArquivo" style="flex:1">📎 Galeria / arquivo</button>
    </div>
    <input type="file" id="dspAnexoCamera" accept="image/*" capture="environment" class="hidden">
    <input type="file" id="dspAnexo" accept="image/*,application/pdf,.pdf" multiple class="hidden">

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
        raiz.querySelector('#dspReembolsoBox').classList.toggle('hidden', status !== 'Reembolso');
      };
    });

    // ---- frete: uma linha de data por viagem
    const caixaFrete = raiz.querySelector('#dspFreteBox');
    const campoViagens = raiz.querySelector('#dspFreteViagens');
    const listaDatas = raiz.querySelector('#dspFreteDatas');

    const montarDatasFrete = () => {
      if (!caixaFrete) return;
      const quantidade = Math.min(Math.max(Number(campoViagens.value) || 1, 1), 30);
      const atuais = Array.from(listaDatas.querySelectorAll('input')).map((i) => i.value);
      const referencia = dia?.data || hojeISO();

      listaDatas.innerHTML = Array.from({ length: quantidade }, (_, i) => `
        <div class="row" style="gap:8px">
          <span class="item-sub" style="width:64px;flex:none">${i + 1}ª viagem</span>
          <input type="date" data-frete-data="${i}" value="${esc(atuais[i] || referencia)}" style="flex:1">
        </div>`).join('');

      const valor = parseFloat(raiz.querySelector('#dspValor').value) || 0;
      raiz.querySelector('#dspFretePrevia').textContent = valor
        ? `${quantidade} viagem(ns) × ${moeda(valor)} = ${moeda(quantidade * valor)} no total`
        : `${quantidade} viagem(ns) — informe o valor de cada uma`;
    };

    // ---- parcelamento: divide o total da nota em contas a pagar
    const checkParcelar = raiz.querySelector('#dspParcelar');
    const caixaParcelas = raiz.querySelector('#dspParcelaBox');
    const campoParcelas = raiz.querySelector('#dspParcelas');
    const campoFrequenciaParcela = raiz.querySelector('#dspParcelaFrequencia');
    const campoIntervalo = raiz.querySelector('#dspParcelaIntervalo');

    const previaParcelas = () => {
      if (!caixaParcelas || caixaParcelas.classList.contains('hidden')) return;
      raiz.querySelector('#dspParcelaIntervaloBox')
        .classList.toggle('hidden', campoFrequenciaParcela.value !== 'dias');

      const quantidade = Math.max(Number(campoParcelas.value) || 2, 2);
      const total = parseFloat(raiz.querySelector('#dspValor').value) || 0;
      // Mesma divisão do servidor: a sobra de centavos vai para a última.
      const centavos = Math.round(total * 100);
      const baseCentavos = Math.floor(centavos / quantidade);
      const base = baseCentavos / 100;
      const ultima = (centavos - baseCentavos * (quantidade - 1)) / 100;

      raiz.querySelector('#dspParcelaPrevia').textContent = total
        ? `${quantidade}x de ${moeda(base)}${Math.abs(ultima - base) > 0.001
            ? ` (última de ${moeda(ultima)})` : ''} · total ${moeda(total)}`
        : `${quantidade} parcelas — informe o valor total da nota`;
    };

    if (checkParcelar) {
      checkParcelar.onchange = () => {
        caixaParcelas.classList.toggle('hidden', !checkParcelar.checked);
        previaParcelas();
      };
      campoParcelas.oninput = previaParcelas;
      campoFrequenciaParcela.onchange = previaParcelas;
      raiz.querySelector('#dspValor').addEventListener('input', previaParcelas);
    }

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
        const categoria = campoCategoria.value;
        boxLocacao.classList.toggle('hidden', categoria !== 'Locação');
        if (caixaFrete) {
          caixaFrete.classList.toggle('hidden', categoria !== 'Frete');
          if (categoria === 'Frete') montarDatasFrete();
        }
        atualizarPrevia();
      };
      campoCategoria.onchange = alternarLocacao;
      campoFrequencia.onchange = atualizarPrevia;
      campoCiclos.oninput = atualizarPrevia;
      campoValor.addEventListener('input', atualizarPrevia);
      if (campoViagens) {
        campoViagens.oninput = montarDatasFrete;
        campoValor.addEventListener('input', montarDatasFrete);
      }
      alternarLocacao();
    }

    // ---- anexos: galeria, arquivos ou câmera, sempre comprimidos no aparelho.
    // Na edição, os que já existem só saem de fato ao salvar.
    const existentes = [...(despesa?.anexos || [])];
    const removidos = new Set();
    const novos = [];
    let preparando = 0;
    const campoAnexo = raiz.querySelector('#dspAnexo');
    const campoCamera = raiz.querySelector('#dspAnexoCamera');
    const listaAnexos = raiz.querySelector('#dspAnexoLista');

    const totalAnexos = () => existentes.filter((a) => !removidos.has(a.id)).length + novos.length;

    const desenharAnexos = () => {
      const mantidos = existentes.filter((a) => !removidos.has(a.id));
      listaAnexos.innerHTML = [
        ...mantidos.map((a) => `
          <div class="anexo-info" style="margin-top:0">
            <span class="anexo-nome">📎 ${esc(a.nome)}</span>
            <button type="button" class="icon-mini" data-tirar-existente="${a.id}" title="Remover anexo">✕</button>
          </div>`),
        ...novos.map((n, i) => `
          <div class="anexo-info" style="margin-top:0">
            <span class="anexo-nome">📎 ${esc(n.arquivo.name)}</span>
            <span class="anexo-tamanho">${n.reduziu
              ? `${formatarBytes(n.original)} → <b>${formatarBytes(n.arquivo.size)}</b>`
              : formatarBytes(n.arquivo.size)}${n.ehPdf ? ' · o servidor ainda comprime o PDF' : ''}</span>
            <button type="button" class="icon-mini" data-tirar-novo="${i}" title="Remover anexo">✕</button>
          </div>`),
        preparando ? '<div class="anexo-info" style="margin-top:0"><span class="muted">preparando arquivo...</span></div>' : '',
      ].join('');

      const total = totalAnexos();
      raiz.querySelector('#dspAnexoContagem').textContent = total ? `(${total} de ${LIMITE_ANEXOS})` : `(até ${LIMITE_ANEXOS})`;
      const cheio = total + preparando >= LIMITE_ANEXOS;
      raiz.querySelector('#dspBtnCamera').disabled = cheio;
      raiz.querySelector('#dspBtnArquivo').disabled = cheio;

      listaAnexos.querySelectorAll('[data-tirar-existente]').forEach((btn) => {
        btn.onclick = () => { removidos.add(Number(btn.dataset.tirarExistente)); desenharAnexos(); };
      });
      listaAnexos.querySelectorAll('[data-tirar-novo]').forEach((btn) => {
        btn.onclick = () => { novos.splice(Number(btn.dataset.tirarNovo), 1); desenharAnexos(); };
      });
    };

    const receberAnexos = async (lista) => {
      const vagas = LIMITE_ANEXOS - totalAnexos() - preparando;
      const arquivos = Array.from(lista || []);
      if (arquivos.length > vagas) toast(`Cada lançamento aceita até ${LIMITE_ANEXOS} anexos.`, 'erro');
      const aceitos = arquivos.slice(0, Math.max(vagas, 0));
      preparando += aceitos.length;
      desenharAnexos();
      for (const arquivo of aceitos) {
        // PDF não dá para comprimir no navegador: vai inteiro e o servidor
        // reamostra as imagens embutidas antes de gravar.
        const ehPdf = /pdf$/i.test(arquivo.type) || /\.pdf$/i.test(arquivo.name || '');
        const pronto = ehPdf ? arquivo : await comprimirImagem(arquivo);
        novos.push({ arquivo: pronto, original: arquivo.size, ehPdf,
          reduziu: arquivo.size - pronto.size > 50 * 1024 });
        preparando -= 1;
        desenharAnexos();
      }
      campoAnexo.value = '';
      campoCamera.value = '';
    };

    raiz.querySelector('#dspBtnArquivo').onclick = () => campoAnexo.click();
    raiz.querySelector('#dspBtnCamera').onclick = () => campoCamera.click();
    campoAnexo.onchange = () => receberAnexos(campoAnexo.files);
    campoCamera.onchange = () => receberAnexos(campoCamera.files);
    desenharAnexos();

    raiz.querySelector('#dspCancelar').onclick = fecharModal;

    raiz.querySelector('#dspSalvar').onclick = async () => {
      const descricao = raiz.querySelector('#dspDescricao').value.trim();
      const valor = parseFloat(raiz.querySelector('#dspValor').value);
      if (!descricao) { toast('Informe a descrição.', 'erro'); return; }
      if (!valor || valor <= 0) { toast('Informe um valor válido.', 'erro'); return; }

      if (preparando) { toast('Aguarde o anexo terminar de carregar.', 'erro'); return; }
      const reembolsarPara = raiz.querySelector('#dspReembolsarPara').value.trim();
      if (status === 'Reembolso' && !reembolsarPara) {
        toast('Informe para quem é o reembolso.', 'erro'); return;
      }

      const botao = raiz.querySelector('#dspSalvar');
      const numeroNota = raiz.querySelector('#dspNota').value.trim();
      const diaEscolhido = Number(raiz.querySelector('#dspDia')?.value) || null;

      // O servidor recusa nota repetida do mesmo fornecedor (409). Aqui a
      // gente mostra o que já existe e deixa o gestor decidir — nota
      // parcelada em duas competências é caso legítimo.
      const enviar = async (confirmarDuplicidade) => {
        if (edicao) {
          await api.put(`/api/despesas/${despesa.id}`, {
            ...(diaEscolhido && diaEscolhido !== despesa.dia_id ? { dia_id: diaEscolhido } : {}),
            reembolsar_para: reembolsarPara,
            categoria: raiz.querySelector('#dspCategoria').value,
            descricao,
            fornecedor: raiz.querySelector('#dspFornecedor').value.trim(),
            valor,
            numero_nota: numeroNota,
            confirmar_duplicidade: confirmarDuplicidade,
            status_pagamento: status,
            vencimento: status === 'Em Aberto' ? raiz.querySelector('#dspVencimento').value : '',
          }, { silencioso: true });
          // Os dados já foram salvos; daqui em diante, erro de anexo aparece
          // como aviso normal (não como nota duplicada).
          // Cada passo concluído sai da fila: se um falhar, tocar em Salvar
          // de novo continua de onde parou, sem repetir o que já foi feito.
          for (const id of [...removidos]) {
            await api.del(`/api/despesas/${despesa.id}/anexos/${id}`);
            removidos.delete(id);
            existentes.splice(existentes.findIndex((a) => a.id === id), 1);
          }
          if (novos.length) {
            const envio = new FormData();
            novos.forEach((n) => envio.append('anexos', n.arquivo));
            const resposta = await api.form(`/api/despesas/${despesa.id}/anexos`, envio);
            novos.length = 0;
            existentes.splice(0, existentes.length, ...resposta.anexos);
            desenharAnexos();
            return resposta;
          }
          return null;
        }
        {
          const diaId = diaEscolhido || dia.id;
          const dados = new FormData();
          dados.append('categoria', raiz.querySelector('#dspCategoria').value);
          dados.append('descricao', descricao);
          dados.append('fornecedor', raiz.querySelector('#dspFornecedor').value.trim());
          dados.append('valor', valor);
          dados.append('status_pagamento', status);
          if (numeroNota) dados.append('numero_nota', numeroNota);
          if (confirmarDuplicidade) dados.append('confirmar_duplicidade', '1');
          if (status === 'Em Aberto') dados.append('vencimento', raiz.querySelector('#dspVencimento').value || '');
          if (status === 'Reembolso') dados.append('reembolsar_para', reembolsarPara);
          if (campoCategoria.value === 'Frete' && caixaFrete && !caixaFrete.classList.contains('hidden')) {
            const transportador = raiz.querySelector('#dspFreteTransportador').value.trim();
            if (transportador) {
              dados.append('frete_transportador', transportador);
              dados.append('frete_viagens', campoViagens.value || '1');
              raiz.querySelectorAll('[data-frete-data]').forEach((campo) => {
                dados.append('frete_datas', campo.value);
              });
            }
          }
          if (checkParcelar?.checked) {
            dados.append('parcelas', campoParcelas.value || '2');
            dados.append('parcela_frequencia', campoFrequenciaParcela.value);
            if (campoFrequenciaParcela.value === 'dias') {
              dados.append('parcela_intervalo', campoIntervalo.value || '30');
            }
          }
          if (campoCategoria.value === 'Locação' && campoFrequencia?.value) {
            dados.append('frequencia', campoFrequencia.value);
            dados.append('ciclos', campoCiclos.value || '1');
            dados.append('equipamento', raiz.querySelector('#dspEquipamento').value.trim() || descricao);
            dados.append('data_locacao', raiz.querySelector('#dspVencimento').value || '');
          }
          novos.forEach((n) => dados.append('anexos', n.arquivo));
          return api.form(`/api/dias/${diaId}/despesas`, dados, { silencioso: true });
        }
      };

      botao.disabled = true;
      try {
        let resposta;
        try {
          resposta = await enviar(false);
        } catch (erro) {
          if (erro?.codigo !== 'nota_duplicada') {
            // O envio principal é silencioso por causa da nota duplicada;
            // qualquer outro erro precisa aparecer.
            toast('⚠ ' + erro.message, 'erro');
            return;
          }
          const seguir = await confirmar({
            titulo: 'Nota já lançada',
            texto: `${erro.message}\n\nLançar mesmo assim? (nota parcelada em duas competências é um caso válido)`,
            confirmar: 'Lançar mesmo assim', perigo: true,
          });
          if (!seguir) return;
          resposta = await enviar(true);
        }

        if (resposta?.frete) {
          const f = resposta.frete;
          toast(`${f.viagens} frete(s) de ${esc(f.transportador)} · total ${moeda(f.valor_total)}`, 'ok');
        }
        if (resposta?.parcelamento) {
          const p = resposta.parcelamento;
          toast(`${p.parcelas}x de ${moeda(p.valor_parcela)} · 1º vencimento ${dataBR(p.primeiro_vencimento)}`, 'ok');
        }
        if (resposta?.locacao_id) {
          toast('Locação criada — as renovações vão aparecer no painel.', 'ok');
        }
        if (resposta?.compressao?.houve_ganho) {
          toast(`Anexos comprimidos: ${resposta.compressao.original} → ${resposta.compressao.final}`, 'ok');
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
      ${kpi({ rotulo: 'Custo da semana', valor: moedaCurta(fin.custo_semana), sub: fin.folha ? `folha ${moedaCurta(fin.folha)}` : `${fechamento.despesas.length} lançamento(s)` })}
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
              ${x.parcela_total ? ` · ${x.grupo_tipo === 'frete' ? 'viagem' : 'parcela'} ${x.parcela_numero}/${x.parcela_total}` : ''}${textoReembolso(x)}
            </div>
            ${htmlAnexos(x)}
          </div>
          <div class="item-valor">${moeda(x.valor)}</div>
          <div class="item-acoes">
            <button class="icon-mini" data-editar="${x.id}" title="Editar">✎</button>
            <button class="icon-mini" data-excluir="${x.id}" title="Excluir">🗑</button>
          </div>
        </div>`).join('') : '<p class="vazio-inline">Nenhum lançamento com esse filtro nesta semana.</p>'}
    </div>

    <div class="ledger-total">SEMANA ${fechamento.semana_numero} · ${moeda(fin.despesas)} em materiais e serviços</div>

    ${fin.em_aberto || fin.reembolso ? `
      <button class="btn btn-outline" data-acao="pagar-tudo" style="margin-top:10px">
        ✓ Marcar todos os custos da semana como pagos
      </button>
      <p class="small faint" style="margin-top:6px">
        Dá baixa nas ${moeda(fin.em_aberto)} em aberto desta semana de uma vez.
        ${fin.reembolso ? `Os ${moeda(fin.reembolso)} de reembolso ficam de fora — você escolhe na confirmação.` : ''}
      </p>` : ''}

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

  const botaoPagarTudo = raiz.querySelector('[data-acao=pagar-tudo]');
  if (botaoPagarTudo) {
    botaoPagarTudo.onclick = async () => {
      const temReembolso = fin.reembolso > 0;
      const confirma = await confirmar({
        titulo: 'Marcar tudo como pago?',
        texto: `Serão baixadas ${moeda(fin.em_aberto)} em contas em aberto da semana ${fechamento.semana_numero}.`
          + (temReembolso
            ? `\n\nOs ${moeda(fin.reembolso)} de reembolso NÃO entram — eles são acerto com quem adiantou o dinheiro.`
            : ''),
        confirmar: 'Marcar como pago',
      });
      if (!confirma) return;

      const r = await api.post(`/api/semanas/${estado.semanaId}/despesas/pagar-todas`, {});
      toast(r.quitadas
        ? `${r.quitadas} conta(s) baixada(s) · ${moeda(r.total)}`
        : 'Nenhuma conta em aberto nesta semana.', 'ok');
      await recarregar();
    };
  }

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
      const ok = await confirmar({ titulo: 'Excluir este lançamento?', texto: 'Os anexos também serão apagados.', perigo: true, confirmar: 'Excluir' });
      if (!ok) return;
      await api.del(`/api/despesas/${btn.dataset.excluir}`);
      toast('Lançamento removido.');
      await recarregar();
    };
  });

  ligarLinksAnexo(raiz);

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
