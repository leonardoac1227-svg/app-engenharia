/* ==========================================================================
   AJUSTES — perfil, dados da obra, usuários e auditoria.
   ========================================================================== */

import { api } from '../api.js';
import { estado, podeAdministrar } from '../estado.js';
import { navegar, recarregarObra } from '../navegacao.js';
import {
  abrirModal, confirmar, dataHoraBR, esc, esqueleto, fecharModal, formularioModal, moeda,
  num, toast,
} from '../util.js';

const PAPEIS = [
  { valor: 'gestor', texto: 'Gestor / Engenheiro — acesso total' },
  { valor: 'mestre', texto: 'Mestre de obra — fotos e ponto, sem financeiro' },
  { valor: 'cliente', texto: 'Cliente / Síndico — somente leitura' },
];

export async function renderizar(raiz) {
  raiz.innerHTML = esqueleto(3);

  const admin = podeAdministrar();
  const [obra, usuarios, auditoria] = await Promise.all([
    estado.obraId ? api.get(`/api/obras/${estado.obraId}`) : Promise.resolve(null),
    admin ? api.get('/api/usuarios') : Promise.resolve([]),
    admin ? api.get('/api/auditoria?limite=15') : Promise.resolve([]),
  ]);

  const usuario = estado.usuario;

  raiz.innerHTML = `
    <div class="section-head"><div><span class="eyebrow">CONFIGURAÇÃO</span><h2>Ajustes</h2></div></div>

    <!-- ---------------- perfil ---------------- -->
    <div class="card">
      <span class="eyebrow">SEU ACESSO</span>
      <div class="row-between" style="align-items:center">
        <div>
          <div style="font-weight:600">${esc(usuario.nome)}</div>
          <div class="item-sub">${esc(usuario.email)} · ${esc(usuario.papel_label)}</div>
        </div>
      </div>
      ${usuario.senha_provisoria ? `
        <div class="alerta aviso" style="margin-top:12px;margin-bottom:0">
          <span class="alerta-ico">🔑</span>
          <div><b>Senha provisória em uso</b><div class="small">Defina uma senha pessoal antes de compartilhar o acesso.</div></div>
        </div>` : ''}
      <div class="btn-linha">
        <button class="btn btn-outline" data-acao="senha">Trocar senha</button>
        <button class="btn btn-ghost" data-acao="sair">Sair</button>
      </div>
    </div>

    <!-- ---------------- obra ---------------- -->
    ${obra ? `
      <div class="section-head"><h3>Dados da obra</h3>
        ${admin ? '<button class="btn btn-outline btn-small" data-acao="editar-obra">Editar</button>' : ''}
      </div>
      <div class="card">
        <div class="item-sub">NOME</div><div style="margin-bottom:8px">${esc(obra.nome)}</div>
        <div class="item-sub">CLIENTE</div><div style="margin-bottom:8px">${esc(obra.cliente || '—')}</div>
        <div class="item-sub">SIGLA</div><div style="margin-bottom:8px">${esc(obra.sigla || '—')}</div>
        <div class="item-sub">ENDEREÇO</div><div style="margin-bottom:8px">${esc(obra.endereco || '—')}</div>
        <div class="row" style="gap:16px">
          <div><div class="item-sub">ORÇAMENTO TOTAL</div><div class="mono">${obra.orcamento_total ? moeda(obra.orcamento_total) : '—'}</div></div>
          <div><div class="item-sub">STATUS</div><div>${esc(obra.status)}</div></div>
        </div>
      </div>` : ''}

    <!-- ---------------- usuários ---------------- -->
    ${admin ? `
      <div class="section-head"><h3>Usuários e acessos</h3>
        <button class="btn btn-outline btn-small" data-acao="novo-usuario">+ Usuário</button>
      </div>
      <div class="lista">
        ${usuarios.map((u) => `
          <div class="item" style="${u.ativo ? '' : 'opacity:.55'}">
            <div class="item-corpo">
              <div class="item-titulo">${esc(u.nome)}${u.ativo ? '' : '<span class="badge badge-neutro">inativo</span>'}</div>
              <div class="item-sub">${esc(u.email)} · ${esc(u.papel_label)}${u.papel !== 'gestor' ? ` · ${u.obras.length} obra(s)` : ''}</div>
              <div class="item-sub">${u.ultimo_acesso ? 'último acesso ' + dataHoraBR(u.ultimo_acesso) : 'nunca acessou'}</div>
            </div>
            <div class="item-acoes">
              <button class="icon-mini" data-editar-usuario="${u.id}">✎</button>
              ${u.id === estado.usuario.id ? '' : `<button class="icon-mini" data-remover-usuario="${u.id}">🗑</button>`}
            </div>
          </div>`).join('')}
      </div>
      <p class="small faint" style="margin-top:10px">
        O mestre de obra só enxerga fotos e ponto. O cliente enxerga fotos e fechamento,
        sem poder editar nada. Ambos precisam ser vinculados às obras que podem ver.
      </p>` : ''}

    <!-- ---------------- auditoria ---------------- -->
    ${admin && auditoria.length ? `
      <div class="section-head"><h3>Últimas ações</h3></div>
      <div class="card">
        ${auditoria.map((a) => `
          <div class="item-sub" style="padding:4px 0">
            ${dataHoraBR(a.criado_em)} · <b style="color:var(--text-muted)">${esc(a.usuario_nome || 'sistema')}</b>
            · ${esc(a.acao.replace(/_/g, ' '))}${a.detalhe ? ' · ' + esc(a.detalhe) : ''}
          </div>`).join('')}
      </div>` : ''}

    ${admin ? `
      <div class="section-head"><h3>Onde os dados estão sendo salvos</h3>
        <button class="btn btn-outline btn-small" data-acao="diagnostico">Testar agora</button>
      </div>
      <div class="card" id="cardDiagnostico">
        <p class="small muted" style="margin-bottom:0">
          Toque em <b>Testar agora</b> para gravar e ler de verdade no banco e no
          armazenamento — é a única forma de saber se o Supabase está mesmo respondendo.
        </p>
      </div>` : ''}

    ${admin && estado.obraId ? `
      <div class="section-head"><h3>Zona de risco</h3></div>
      <button class="btn btn-perigo" data-acao="arquivar">Arquivar esta obra</button>
      <p class="small faint" style="margin-top:8px">
        Arquivar mantém todo o histórico e apenas tira a obra da lista principal.
      </p>` : ''}

    <p class="small faint" style="margin-top:22px;text-align:center">
      Obra ERP · versão ${esc(estado.versao || '—')}
    </p>
  `;

  ligarEventos(raiz, { obra, usuarios });
}

function ligarEventos(raiz, { obra, usuarios }) {
  const acao = (nome, fn) => {
    const btn = raiz.querySelector(`[data-acao=${nome}]`);
    if (btn) btn.onclick = fn;
  };

  acao('senha', async () => {
    const dados = await formularioModal({
      titulo: 'Trocar senha',
      campos: [
        { nome: 'senha_atual', rotulo: 'Senha atual', tipo: 'password' },
        { nome: 'senha_nova', rotulo: 'Nova senha (mínimo 6 caracteres)', tipo: 'password' },
      ],
      rotulo: 'Trocar',
    });
    if (!dados) return;
    await api.post('/api/auth/senha', dados);
    toast('Senha alterada.', 'ok');
    estado.usuario.senha_provisoria = false;
    await renderizar(raiz);
  });

  acao('sair', async () => {
    const ok = await confirmar({ titulo: 'Sair da conta?', confirmar: 'Sair' });
    if (!ok) return;
    await api.post('/api/auth/logout', {});
    window.location.href = '/login';
  });

  acao('editar-obra', async () => {
    const dados = await formularioModal({
      titulo: 'Dados da obra',
      campos: [
        { nome: 'nome', rotulo: 'Nome', valor: obra.nome },
        { nome: 'cliente', rotulo: 'Cliente', valor: obra.cliente },
        { nome: 'sigla', rotulo: 'Sigla da obra (ex.: I19)', valor: obra.sigla,
          placeholder: 'usada no relatório de pagamento' },
        { nome: 'endereco', rotulo: 'Endereço', valor: obra.endereco },
        { nome: 'orcamento_total', rotulo: 'Orçamento total (R$)', tipo: 'number', valor: obra.orcamento_total || '' },
        { nome: 'data_inicio', rotulo: 'Início da obra', tipo: 'date', valor: obra.data_inicio || '' },
        { nome: 'previsao_fim', rotulo: 'Previsão de entrega', tipo: 'date', valor: obra.previsao_fim || '' },
        { nome: 'status', rotulo: 'Situação', tipo: 'select', valor: obra.status,
          opcoes: [{ valor: 'ativa', texto: 'Em andamento' }, { valor: 'pausada', texto: 'Paralisada' }, { valor: 'concluida', texto: 'Concluída' }] },
      ],
    });
    if (!dados) return;
    await api.put(`/api/obras/${estado.obraId}`, dados);
    toast('Obra atualizada.', 'ok');
    await recarregarObra({ manterAba: true });
  });

  acao('novo-usuario', () => abrirModalUsuario(null, raiz));
  raiz.querySelectorAll('[data-editar-usuario]').forEach((btn) => {
    btn.onclick = () => abrirModalUsuario(usuarios.find((u) => String(u.id) === btn.dataset.editarUsuario), raiz);
  });
  raiz.querySelectorAll('[data-remover-usuario]').forEach((btn) => {
    btn.onclick = async () => {
      const ok = await confirmar({ titulo: 'Desativar este usuário?', perigo: true, confirmar: 'Desativar' });
      if (!ok) return;
      await api.del(`/api/usuarios/${btn.dataset.removerUsuario}`);
      await renderizar(raiz);
    };
  });

  acao('diagnostico', async () => {
    const card = raiz.querySelector('#cardDiagnostico');
    card.innerHTML = '<p class="small muted" style="margin-bottom:0">Testando conexão...</p>';
    try {
      const d = await api.get('/api/diagnostico');
      const banco = d.banco;
      const anexos = d.anexos;
      const registros = banco.registros || {};

      card.innerHTML = `
        <div class="diag-linha">
          <span class="diag-farol ${banco.ok ? 'ok' : 'erro'}"></span>
          <div class="diag-corpo">
            <div class="item-titulo">
              Banco de dados
              <span class="badge ${banco.tipo === 'postgres' ? 'badge-pago' : 'badge-neutro'}">
                ${banco.tipo === 'postgres' ? 'Supabase' : 'SQLite local'}</span>
            </div>
            <div class="diag-destino">${esc(banco.destino || '')}</div>
            <div class="item-sub">
              ${banco.ok
                ? `respondeu em ${num(banco.latencia_ms, 0)} ms · ${num(registros.obras)} obra(s), ${num(registros.dias)} dia(s), ${num(registros.despesas)} lançamento(s)`
                : esc(banco.erro || 'sem resposta')}
            </div>
          </div>
        </div>
        <div class="diag-linha">
          <span class="diag-farol ${anexos.ok ? 'ok' : 'erro'}"></span>
          <div class="diag-corpo">
            <div class="item-titulo">
              Fotos e notas fiscais
              <span class="badge ${anexos.tipo === 'supabase' ? 'badge-pago' : 'badge-neutro'}">
                ${anexos.tipo === 'supabase' ? 'Supabase Storage' : 'disco do servidor'}</span>
            </div>
            <div class="diag-destino">${esc(anexos.destino || '')}${anexos.bucket ? ' · bucket ' + esc(anexos.bucket) : ''}</div>
            <div class="item-sub">
              ${anexos.ok
                ? (anexos.latencia_ms ? `gravou e leu em ${num(anexos.latencia_ms, 0)} ms` : 'pasta acessível')
                : esc(anexos.erro || 'falhou')}
              ${anexos.registrados !== undefined ? `<br>${num(anexos.registrados)} anexo(s) registrado(s)`
                + (anexos.ausentes ? ` · <b style="color:var(--danger)">${num(anexos.ausentes)} de ${num(anexos.conferidos)} conferidos não abrem</b>` : '') : ''}
            </div>
          </div>
        </div>
        ${banco.tipo !== 'postgres' ? `
          <div class="alerta aviso" style="margin-bottom:0">
            <span class="alerta-ico">!</span>
            <div><b>Você não está usando o Supabase</b>
            <div class="small">A variável DATABASE_URL não chegou ao servidor. No Render sem disco
            persistente, os dados somem no próximo deploy.</div></div>
          </div>` : ''}
        ${(d.configuracao || []).length ? `
          <div class="alerta perigo" style="margin-bottom:0">
            <span class="alerta-ico">!</span>
            <div><b>Problema na configuração</b>
              ${d.configuracao.map((a) => `<div class="small">${esc(a)}</div>`).join('')}
            </div>
          </div>` : ''}
        ${anexos.aviso && anexos.tipo !== 'supabase' ? `
          <div class="alerta aviso" style="margin-bottom:0">
            <span class="alerta-ico">!</span><div class="small">${esc(anexos.aviso)}</div>
          </div>` : ''}`;
    } catch (erro) {
      card.innerHTML = '<p class="small" style="color:var(--danger);margin-bottom:0">Não foi possível executar o teste.</p>';
    }
  });

  acao('arquivar', async () => {
    const ok = await confirmar({
      titulo: 'Arquivar esta obra?',
      texto: 'A obra sai da lista principal, mas todo o histórico é preservado e pode ser reaberto.',
      confirmar: 'Arquivar', perigo: true,
    });
    if (!ok) return;
    await api.del(`/api/obras/${estado.obraId}`);
    toast('Obra arquivada.');
    estado.obraId = null;
    await recarregarObra({ recarregarLista: true });
    navegar('home');
  });
}

/** Modal de usuário com seleção das obras que ele pode acessar. */
function abrirModalUsuario(usuario, raiz) {
  const edicao = !!usuario;
  abrirModal(`
    <h3>${edicao ? 'Editar usuário' : 'Novo usuário'}</h3>

    <label class="field-label">Nome</label>
    <input type="text" id="usrNome" value="${esc(usuario?.nome || '')}">

    ${edicao ? '' : `
      <label class="field-label">E-mail</label>
      <input type="email" id="usrEmail" inputmode="email" placeholder="pessoa@empresa.com.br">`}

    <label class="field-label">${edicao ? 'Nova senha (deixe vazio para manter)' : 'Senha provisória'}</label>
    <input type="text" id="usrSenha" placeholder="mínimo 6 caracteres">

    <label class="field-label">Perfil de acesso</label>
    <select id="usrPapel">
      ${PAPEIS.map((p) => `<option value="${p.valor}"${p.valor === (usuario?.papel || 'mestre') ? ' selected' : ''}>${esc(p.texto)}</option>`).join('')}
    </select>

    <label class="field-label">Obras que pode acessar</label>
    <div class="stack" id="usrObras">
      ${estado.obras.map((o) => `
        <label class="checkbox-row" style="margin:0">
          <input type="checkbox" value="${o.id}" ${usuario?.obras?.includes(o.id) ? 'checked' : ''}>
          <span>${esc(o.nome)}</span>
        </label>`).join('')}
    </div>
    <p class="small faint">O gestor acessa todas as obras automaticamente.</p>

    <div class="btn-linha">
      <button class="btn btn-outline" id="usrCancelar">Cancelar</button>
      <button class="btn btn-brass" id="usrSalvar">Salvar</button>
    </div>
  `, (modal) => {
    modal.querySelector('#usrCancelar').onclick = fecharModal;
    modal.querySelector('#usrSalvar').onclick = async () => {
      const obras = Array.from(modal.querySelectorAll('#usrObras input:checked')).map((c) => Number(c.value));
      const corpo = {
        nome: modal.querySelector('#usrNome').value.trim(),
        papel: modal.querySelector('#usrPapel').value,
        obras,
      };
      const senha = modal.querySelector('#usrSenha').value.trim();
      if (senha) corpo.senha = senha;

      if (edicao) {
        await api.put(`/api/usuarios/${usuario.id}`, corpo);
      } else {
        corpo.email = modal.querySelector('#usrEmail').value.trim();
        if (!corpo.senha) { toast('Defina uma senha provisória.', 'erro'); return; }
        await api.post('/api/usuarios', corpo);
      }
      fecharModal();
      toast('Usuário salvo.', 'ok');
      await renderizar(raiz);
    };
  });
}
