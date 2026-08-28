/* ==========================================================================
   EQUIPE — cadastro de profissionais e resumo de horas da semana.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeAdministrar, podeFinanceiro } from '../estado.js';
import { recarregarObra } from '../navegacao.js';
import {
  abrirModal, confirmar, esc, esqueleto, fecharModal, formularioModal, kpi, moeda, num, toast, vazio,
} from '../util.js';

let mostrarInativos = false;

export async function renderizar(raiz) {
  if (!estado.obraId) { raiz.innerHTML = vazio('＋', 'Selecione uma obra.'); return; }

  raiz.innerHTML = esqueleto(3);
  const [funcionarios, fechamento] = await Promise.all([
    api.get(`/api/obras/${estado.obraId}/funcionarios${mostrarInativos ? '?inativos=1' : ''}`),
    estado.semanaId ? api.get(`/api/semanas/${estado.semanaId}/fechamento`) : Promise.resolve(null),
  ]);

  const ativos = funcionarios.filter((f) => f.ativo);
  const folha = fechamento?.folha_detalhada || [];
  const indicadores = fechamento?.indicadores || {};
  const custoFolha = fechamento?.financeiro?.folha || 0;

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">FOL · EQUIPE DA OBRA</span><h2>Equipe</h2></div>
      ${podeAdministrar() ? '<button class="btn btn-brass btn-small" data-acao="novo">+ Profissional</button>' : ''}
    </div>

    <div class="kpi-grid">
      ${kpi({ rotulo: 'Ativos', valor: num(ativos.length), sub: 'na obra', destaque: true })}
      ${kpi({ rotulo: 'Horas na semana', valor: num(indicadores.total_horas, 1) + ' h', sub: `${indicadores.dias_trabalhados || 0} dia(s)` })}
      ${kpi({ rotulo: 'Efetivo médio', valor: num(indicadores.efetivo_medio, 1), sub: 'por dia trabalhado' })}
      ${podeFinanceiro()
        ? kpi({ rotulo: 'Folha da semana', valor: moeda(custoFolha), sub: 'com base no ponto' })
        : kpi({ rotulo: 'Dias com ponto', valor: num(indicadores.dias_trabalhados), sub: 'nesta semana' })}
    </div>

    ${folha.length ? `
      <div class="section-head"><h3>Horas por profissional · semana ${fechamento.semana_numero}</h3></div>
      <div class="card">
        ${folha.map((p) => {
          const maior = folha[0]?.horas || 1;
          return `
            <div style="margin-bottom:11px">
              <div class="progresso-legenda" style="margin:0 0 4px">
                <span style="color:var(--text)">${esc(p.nome)}${p.funcao ? ` · ${esc(p.funcao)}` : ''}</span>
                <span>${num(p.horas, 1)} h${podeFinanceiro() ? ` · ${moeda(p.total)}` : ''}</span>
              </div>
              <div class="progresso-trilho"><div class="progresso-preenchido" style="width:${(p.horas / maior) * 100}%;background:var(--brass)"></div></div>
            </div>`;
        }).join('')}
      </div>` : ''}

    <div class="section-head"><h3>Cadastro</h3>
      <button class="btn btn-ghost btn-small" data-acao="inativos">${mostrarInativos ? 'Ocultar inativos' : 'Ver inativos'}</button>
    </div>

    <div class="lista">
      ${funcionarios.length ? funcionarios.map((f) => `
        <div class="staff-row" data-func="${f.id}" style="${f.ativo ? '' : 'opacity:.55'}">
          <div class="staff-view">
            <div class="staff-info">
              <div class="staff-name">${esc(f.nome)}
                ${f.por_producao ? '<span class="badge badge-reembolso">por produção</span>' : ''}
                ${f.ativo ? '' : '<span class="badge badge-neutro">inativo</span>'}
              </div>
              <div class="staff-meta">
                ${esc(f.funcao || 'sem função')}${!f.por_producao && f.valor_hora !== null && f.valor_hora !== undefined ? ` · ${moeda(f.valor_hora)}/h` : ''}
                ${f.por_producao ? ` · ${num(f.tarefas)} tarefa(s)` : ''}
                ${f.telefone ? ` · ${esc(f.telefone)}` : ''}
              </div>
            </div>
            ${podeAdministrar() ? `
              <div class="item-acoes">
                ${f.por_producao && f.ativo ? `<button class="icon-mini" data-tarefa="${f.id}" title="Nova tarefa">＋</button>` : ''}
                <button class="icon-mini" data-editar="${f.id}" title="Editar">✎</button>
                ${f.ativo ? `<button class="icon-mini" data-remover="${f.id}" title="Desativar">🗑</button>` : ''}
              </div>` : ''}
          </div>

          ${f.por_producao && podeFinanceiro() && f.contratado ? `
            <div class="contrato-saldos" style="margin-top:10px;padding-top:10px">
              <div><span class="item-sub">CONTRATADO</span><b>${moeda(f.contratado)}</b></div>
              <div><span class="item-sub">MEDIDO</span><b>${moeda(f.medido)}</b></div>
              <div><span class="item-sub">A MEDIR</span><b>${moeda(f.saldo_tarefas)}</b></div>
            </div>` : ''}
        </div>`).join('') : vazio('☰', 'Nenhum profissional cadastrado nesta obra.')}
    </div>

    <p class="small faint" style="margin-top:14px">
      <b>Fixo</b> é pago por hora e entra na folha da semana. <b>Por produção</b> é pago
      por tarefa medida em percentual — o ponto dele serve só para controle de presença
      e não gera custo de folha, evitando cobrança dupla.
      Ao editar o valor da hora, apenas os próximos lançamentos usam o novo valor:
      semanas já lançadas mantêm o valor vigente na época.
    </p>
  `;

  const botaoNovo = raiz.querySelector('[data-acao=novo]');
  if (botaoNovo) botaoNovo.onclick = () => abrirFormularioProfissional(raiz);

  raiz.querySelector('[data-acao=inativos]').onclick = () => {
    mostrarInativos = !mostrarInativos;
    renderizar(raiz);
  };

  raiz.querySelectorAll('[data-editar]').forEach((btn) => {
    btn.onclick = () => {
      const f = funcionarios.find((x) => String(x.id) === btn.dataset.editar);
      abrirFormularioProfissional(raiz, f);
    };
  });

  raiz.querySelectorAll('[data-tarefa]').forEach((btn) => {
    btn.onclick = async () => {
      const f = funcionarios.find((x) => String(x.id) === btn.dataset.tarefa);
      const dados = await formularioModal({
        titulo: `Nova tarefa · ${f.nome}`,
        descricao: 'Empreitada fechada: você mede o percentual executado a cada semana e o app calcula o valor.',
        campos: [
          { nome: 'servico', rotulo: 'Serviço', placeholder: 'Ex: forro de gesso do 1º pavimento' },
          { nome: 'valor_total', rotulo: 'Valor combinado (R$)', tipo: 'number' },
        ],
        rotulo: 'Criar tarefa',
      });
      if (!dados || !dados.servico.trim()) return;
      await api.post(`/api/obras/${estado.obraId}/contratos`, { ...dados, funcionario_id: f.id });
      toast('Tarefa criada. Meça o avanço na aba Terceiros.', 'ok');
      await renderizar(raiz);
    };
  });

  raiz.querySelectorAll('[data-remover]').forEach((btn) => {
    btn.onclick = async () => {
      const f = funcionarios.find((x) => String(x.id) === btn.dataset.remover);
      const ok = await confirmar({
        titulo: `Desativar ${f.nome}?`,
        texto: 'O histórico de horas já lançado é mantido nos fechamentos anteriores.',
        confirmar: 'Desativar', perigo: true,
      });
      if (!ok) return;
      await api.del(`/api/funcionarios/${f.id}`);
      toast('Profissional desativado.');
      await recarregarObra({ manterAba: true });
    };
  });
}

/* --------------------------------------------------------------------------
   Formulário de profissional com tipo de contrato.

   O campo "valor da hora" aparece e some conforme o tipo escolhido: quem é
   pago por produção não tem salário por hora, e deixar o campo visível só
   convidaria a preencher errado — resultando em cobrança dupla (uma no ponto,
   outra na medição da tarefa).
   -------------------------------------------------------------------------- */

function abrirFormularioProfissional(raiz, funcionario = null) {
  const edicao = !!funcionario;
  const tipoInicial = funcionario?.tipo_contrato || 'fixo';

  abrirModal(`
    <h3>${edicao ? 'Editar profissional' : 'Novo profissional'}</h3>

    <label class="field-label">Nome</label>
    <input type="text" id="fnNome" value="${esc(funcionario?.nome || '')}" placeholder="Nome completo">

    <label class="field-label">Função</label>
    <input type="text" id="fnFuncao" value="${esc(funcionario?.funcao || '')}"
           placeholder="pedreiro, servente, gesseiro...">

    <label class="field-label">Tipo de contrato</label>
    <div class="segmented" id="fnTipo">
      <button type="button" data-tipo="fixo" class="${tipoInicial === 'fixo' ? 'ativo' : ''}">Fixo (por hora)</button>
      <button type="button" data-tipo="producao" class="${tipoInicial === 'producao' ? 'ativo' : ''}">Por produção</button>
    </div>

    <div id="fnBoxHora" class="${tipoInicial === 'producao' ? 'hidden' : ''}">
      <label class="field-label">Valor da hora (R$)</label>
      <div class="input-moeda">
        <input type="number" id="fnValorHora" step="0.01" min="0" inputmode="decimal"
               value="${funcionario?.valor_hora ?? ''}" placeholder="0,00">
      </div>
    </div>

    <div id="fnAvisoProducao" class="alerta ${tipoInicial === 'producao' ? '' : 'hidden'}" style="margin-top:12px">
      <span class="alerta-ico">ℹ</span>
      <div><b>Pago por tarefa, não por hora</b>
        <div class="small">Cadastre as tarefas com o valor combinado e meça o percentual
        executado a cada semana. O ponto pode continuar sendo lançado para controle de
        presença — ele não gera custo de folha.</div>
      </div>
    </div>

    <label class="field-label">CPF ${tipoInicial === 'producao' ? '' : '(opcional)'}</label>
    <input type="text" id="fnDocumento" value="${esc(funcionario?.documento || '')}"
           inputmode="numeric" placeholder="000.000.000-00">

    <label class="field-label">Chave PIX (opcional)</label>
    <input type="text" id="fnPix" value="${esc(funcionario?.chave_pix || '')}" placeholder="CPF, telefone ou e-mail">

    <label class="field-label">Telefone (opcional)</label>
    <input type="tel" id="fnTelefone" value="${esc(funcionario?.telefone || '')}">

    <div class="btn-linha">
      <button class="btn btn-outline" id="fnCancelar">Cancelar</button>
      <button class="btn btn-brass" id="fnSalvar">Salvar</button>
    </div>
  `, (modal) => {
    let tipo = tipoInicial;

    modal.querySelectorAll('#fnTipo button').forEach((btn) => {
      btn.onclick = () => {
        tipo = btn.dataset.tipo;
        modal.querySelectorAll('#fnTipo button').forEach((b) => b.classList.toggle('ativo', b === btn));
        modal.querySelector('#fnBoxHora').classList.toggle('hidden', tipo === 'producao');
        modal.querySelector('#fnAvisoProducao').classList.toggle('hidden', tipo !== 'producao');
      };
    });

    modal.querySelector('#fnCancelar').onclick = fecharModal;

    modal.querySelector('#fnSalvar').onclick = async () => {
      const nome = modal.querySelector('#fnNome').value.trim();
      if (!nome) { toast('Informe o nome.', 'erro'); return; }

      const valorHora = Number(modal.querySelector('#fnValorHora').value) || 0;
      if (tipo === 'fixo' && valorHora <= 0) {
        toast('Contrato fixo precisa do valor da hora.', 'erro');
        return;
      }

      const corpo = {
        nome,
        funcao: modal.querySelector('#fnFuncao').value.trim(),
        tipo_contrato: tipo,
        valor_hora: tipo === 'producao' ? 0 : valorHora,
        documento: modal.querySelector('#fnDocumento').value.trim(),
        chave_pix: modal.querySelector('#fnPix').value.trim(),
        telefone: modal.querySelector('#fnTelefone').value.trim(),
      };

      if (edicao) await api.put(`/api/funcionarios/${funcionario.id}`, { ...corpo, ativo: 1 });
      else await api.post(`/api/obras/${estado.obraId}/funcionarios`, corpo);

      fecharModal();
      toast(edicao ? 'Cadastro atualizado.' : `${nome} adicionado à equipe.`, 'ok');
      await recarregarObra({ manterAba: true });
    };

    setTimeout(() => modal.querySelector('#fnNome').focus(), 150);
  });
}
