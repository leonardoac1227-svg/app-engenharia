/* ==========================================================================
   EQUIPE — cadastro de profissionais e resumo de horas da semana.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeAdministrar, podeFinanceiro } from '../estado.js';
import { recarregarObra } from '../navegacao.js';
import { confirmar, esc, esqueleto, formularioModal, kpi, moeda, num, toast, vazio } from '../util.js';

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
              <div class="staff-name">${esc(f.nome)}${f.ativo ? '' : '<span class="badge badge-neutro">inativo</span>'}</div>
              <div class="staff-meta">
                ${esc(f.funcao || 'sem função')}${f.valor_hora !== null && f.valor_hora !== undefined ? ` · ${moeda(f.valor_hora)}/h` : ''}
                ${f.telefone ? ` · ${esc(f.telefone)}` : ''}
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

    <p class="small faint" style="margin-top:14px">
      Ao editar o valor da hora, apenas os próximos lançamentos usam o novo valor —
      semanas já lançadas mantêm o valor vigente na época, preservando o histórico.
    </p>
  `;

  const botaoNovo = raiz.querySelector('[data-acao=novo]');
  if (botaoNovo) {
    botaoNovo.onclick = async () => {
      const dados = await formularioModal({
        titulo: 'Novo profissional',
        campos: [
          { nome: 'nome', rotulo: 'Nome' },
          { nome: 'funcao', rotulo: 'Função', placeholder: 'pedreiro, servente, mestre...' },
          { nome: 'valor_hora', rotulo: 'Valor da hora (R$)', tipo: 'number' },
          { nome: 'telefone', rotulo: 'Telefone (opcional)', tipo: 'tel' },
        ],
      });
      if (!dados || !dados.nome.trim()) return;
      await api.post(`/api/obras/${estado.obraId}/funcionarios`, dados);
      toast(`${dados.nome} adicionado à equipe.`, 'ok');
      await recarregarObra({ manterAba: true });
    };
  }

  raiz.querySelector('[data-acao=inativos]').onclick = () => {
    mostrarInativos = !mostrarInativos;
    renderizar(raiz);
  };

  raiz.querySelectorAll('[data-editar]').forEach((btn) => {
    btn.onclick = async () => {
      const f = funcionarios.find((x) => String(x.id) === btn.dataset.editar);
      const dados = await formularioModal({
        titulo: 'Editar profissional',
        descricao: 'O novo valor da hora vale apenas para lançamentos futuros.',
        campos: [
          { nome: 'nome', rotulo: 'Nome', valor: f.nome },
          { nome: 'funcao', rotulo: 'Função', valor: f.funcao },
          { nome: 'valor_hora', rotulo: 'Valor da hora (R$)', tipo: 'number', valor: f.valor_hora },
          { nome: 'telefone', rotulo: 'Telefone', tipo: 'tel', valor: f.telefone },
        ],
      });
      if (!dados) return;
      await api.put(`/api/funcionarios/${f.id}`, { ...dados, ativo: 1 });
      toast('Cadastro atualizado.', 'ok');
      await recarregarObra({ manterAba: true });
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
