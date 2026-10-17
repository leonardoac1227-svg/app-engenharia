/* ==========================================================================
   EQUIPE — cadastro de profissionais e resumo de horas da semana.
   O ponto é só controle de presença: pagamentos vão como despesa no Caixa.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeAdministrar } from '../estado.js';
import { recarregarObra } from '../navegacao.js';
import {
  abrirModal, confirmar, esc, esqueleto, fecharModal, kpi, num, toast, vazio,
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
  const horasPorPessoa = fechamento?.folha_detalhada || [];
  const indicadores = fechamento?.indicadores || {};

  raiz.innerHTML = `
    <div class="section-head">
      <div><span class="eyebrow">EQUIPE DA OBRA</span><h2>Equipe</h2></div>
      ${podeAdministrar() ? '<button class="btn btn-brass btn-small" data-acao="novo">+ Profissional</button>' : ''}
    </div>

    <div class="kpi-grid">
      ${kpi({ rotulo: 'Ativos', valor: num(ativos.length), sub: 'na obra', destaque: true })}
      ${kpi({ rotulo: 'Horas na semana', valor: num(indicadores.total_horas, 1) + ' h', sub: `${indicadores.dias_trabalhados || 0} dia(s)` })}
      ${kpi({ rotulo: 'Efetivo médio', valor: num(indicadores.efetivo_medio, 1), sub: 'por dia trabalhado' })}
      ${kpi({ rotulo: 'Dias com ponto', valor: num(indicadores.dias_trabalhados), sub: 'nesta semana' })}
    </div>

    ${horasPorPessoa.length ? `
      <div class="section-head"><h3>Horas por profissional · semana ${fechamento.semana_numero}</h3></div>
      <div class="card">
        ${horasPorPessoa.map((p) => {
          const maior = horasPorPessoa[0]?.horas || 1;
          return `
            <div style="margin-bottom:11px">
              <div class="progresso-legenda" style="margin:0 0 4px">
                <span style="color:var(--text)">${esc(p.nome)}${p.funcao ? ` · ${esc(p.funcao)}` : ''}</span>
                <span>${num(p.horas, 1)} h</span>
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
                ${f.ativo ? '' : '<span class="badge badge-neutro">inativo</span>'}
              </div>
              <div class="staff-meta">
                ${esc(f.funcao || 'sem função')}${f.telefone ? ` · ${esc(f.telefone)}` : ''}
              </div>
            </div>
            ${podeAdministrar() ? `
              <div class="item-acoes">
                <button class="icon-mini" data-editar="${f.id}" title="Editar">✎</button>
                ${f.ativo ? `<button class="icon-mini" data-remover="${f.id}" title="Desativar">🗑</button>` : ''}
              </div>` : ''}
          </div>
        </div>`).join('') : vazio('☰', 'Nenhum profissional cadastrado nesta obra.')}
    </div>
  `;

  const botaoNovo = raiz.querySelector('[data-acao=novo]');
  if (botaoNovo) botaoNovo.onclick = () => abrirFormularioProfissional();

  raiz.querySelector('[data-acao=inativos]').onclick = () => {
    mostrarInativos = !mostrarInativos;
    renderizar(raiz);
  };

  raiz.querySelectorAll('[data-editar]').forEach((btn) => {
    btn.onclick = () => {
      const f = funcionarios.find((x) => String(x.id) === btn.dataset.editar);
      abrirFormularioProfissional(f);
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

function abrirFormularioProfissional(funcionario = null) {
  const edicao = !!funcionario;

  abrirModal(`
    <h3>${edicao ? 'Editar profissional' : 'Novo profissional'}</h3>

    <label class="field-label">Nome</label>
    <input type="text" id="fnNome" value="${esc(funcionario?.nome || '')}" placeholder="Nome completo">

    <label class="field-label">Função</label>
    <input type="text" id="fnFuncao" value="${esc(funcionario?.funcao || '')}"
           placeholder="pedreiro, servente, gesseiro...">

    <label class="field-label">CPF (opcional)</label>
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
    modal.querySelector('#fnCancelar').onclick = fecharModal;

    modal.querySelector('#fnSalvar').onclick = async () => {
      const nome = modal.querySelector('#fnNome').value.trim();
      if (!nome) { toast('Informe o nome.', 'erro'); return; }

      const corpo = {
        nome,
        funcao: modal.querySelector('#fnFuncao').value.trim(),
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
