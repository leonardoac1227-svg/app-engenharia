/* ==========================================================================
   OBRA ERP — frontend (vanilla JS, sem frameworks)
   ========================================================================== */

const DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

const state = {
  obraId: null,
  obraNome: "",
  semanaId: null,
  semanaNumero: null,
  diaAtivo: "Segunda",
  diasCache: {},        // dia_semana -> objeto dia
  funcionarios: [],
  horasCache: {},        // funcionario_id -> horas
  statusSelecionado: "Em Aberto",
};

// ---------------------------------------------------------------- helpers

const $ = (sel) => document.querySelector(sel);
const $all = (sel) => Array.from(document.querySelectorAll(sel));

function moeda(v) {
  v = Number(v) || 0;
  return "R$ " + v.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove("show"), 2200);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let msg = "Erro na operação.";
    try { const j = await res.json(); msg = j.erro || msg; } catch (e) {}
    toast("⚠ " + msg);
    throw new Error(msg);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res;
}

function apiJSON(path, method, body) {
  return api(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------- drawer

function abrirDrawer() {
  $("#drawer").classList.add("open");
  $("#drawerOverlay").classList.add("open");
}
function fecharDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawerOverlay").classList.remove("open");
}

$("#btnMenu").addEventListener("click", abrirDrawer);
$("#drawerOverlay").addEventListener("click", fecharDrawer);
$("#btnAbrirDrawerVazio").addEventListener("click", abrirDrawer);

// ---------------------------------------------------------------- obras

async function carregarObras() {
  const obras = await api("/api/obras");
  const lista = $("#listaObras");
  lista.innerHTML = "";

  if (obras.length === 0) {
    lista.innerHTML = '<p class="empty-inline">Nenhuma obra cadastrada.</p>';
  }

  obras.forEach((o) => {
    const btn = document.createElement("button");
    btn.className = "obra-item" + (o.id === state.obraId ? " active" : "");
    btn.textContent = o.nome;
    btn.addEventListener("click", () => selecionarObra(o.id, o.nome));
    lista.appendChild(btn);
  });

  if (!state.obraId && obras.length > 0) {
    selecionarObra(obras[0].id, obras[0].nome);
  } else if (obras.length === 0) {
    $("#emptyState").classList.remove("hidden");
    $("#obraContent").classList.add("hidden");
  }
}

async function selecionarObra(id, nome) {
  state.obraId = id;
  state.obraNome = nome;
  state.semanaId = null;
  $("#obraNomeTopo").textContent = nome;
  $all(".obra-item").forEach((b) => b.classList.toggle("active", b.textContent === nome));
  $("#emptyState").classList.add("hidden");
  $("#obraContent").classList.remove("hidden");
  fecharDrawer();
  await carregarFuncionarios();
  await carregarSemanas();
}

$("#btnCriarObra").addEventListener("click", async () => {
  const input = $("#inputNovaObra");
  const nome = input.value.trim();
  if (!nome) { toast("Digite um nome para a obra."); return; }
  const nova = await apiJSON("/api/obras", "POST", { nome });
  input.value = "";
  await carregarObras();
  selecionarObra(nova.id, nova.nome);
});

// ---------------------------------------------------------------- semanas

async function carregarSemanas() {
  const semanas = await api(`/api/obras/${state.obraId}/semanas`);
  const select = $("#selectSemana");
  select.innerHTML = "";

  if (semanas.length === 0) {
    $("#semanaVaziaMsg").classList.remove("hidden");
    $("#semanaContent").classList.add("hidden");
    return;
  }
  $("#semanaVaziaMsg").classList.add("hidden");

  semanas.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `Semana ${s.numero}`;
    select.appendChild(opt);
  });

  const alvo = state.semanaId && semanas.some((s) => s.id === state.semanaId)
    ? state.semanaId
    : semanas[semanas.length - 1].id;

  select.value = alvo;
  await selecionarSemana(alvo);
}

$("#selectSemana").addEventListener("change", (e) => selecionarSemana(Number(e.target.value)));

$("#btnNovaSemana").addEventListener("click", async () => {
  await apiJSON(`/api/obras/${state.obraId}/semanas`, "POST", {});
  state.semanaId = null;
  await carregarSemanas();
  toast("Nova semana criada.");
});

async function selecionarSemana(semanaId) {
  state.semanaId = semanaId;
  const semanas = await api(`/api/obras/${state.obraId}/semanas`);
  const s = semanas.find((x) => x.id === semanaId);
  state.semanaNumero = s ? s.numero : null;

  $("#semanaContent").classList.remove("hidden");
  $("#inputMeta").value = s ? s.meta : "";
  $("#checkMetaAtingida").checked = !!(s && s.meta_atingida);

  montarTabs();
  await carregarDias();
  await selecionarDia(state.diaAtivo);
  await carregarFechamento();
}

$("#btnSalvarMeta").addEventListener("click", async () => {
  await apiJSON(`/api/semanas/${state.semanaId}`, "PUT", {
    meta: $("#inputMeta").value,
    meta_atingida: $("#checkMetaAtingida").checked,
  });
  toast("Meta salva.");
  await carregarFechamento();
});

// ---------------------------------------------------------------- dias / tabs

function montarTabs() {
  const nav = $("#tabsDias");
  nav.innerHTML = "";
  DIAS_SEMANA.forEach((d) => {
    const btn = document.createElement("button");
    btn.className = "tab-btn" + (d === state.diaAtivo ? " active" : "");
    btn.textContent = d.slice(0, 3);
    btn.addEventListener("click", () => selecionarDia(d));
    nav.appendChild(btn);
  });
}

async function carregarDias() {
  const dias = await api(`/api/semanas/${state.semanaId}/dias`);
  state.diasCache = {};
  dias.forEach((d) => { state.diasCache[d.dia_semana] = d; });
}

async function selecionarDia(diaNome) {
  state.diaAtivo = diaNome;
  $all(".tab-btn").forEach((b) => b.classList.toggle("active", b.textContent === diaNome.slice(0, 3)));

  const dia = state.diasCache[diaNome];
  $("#inputProducao").value = dia.producao_dia || "";
  $("#selectClima").value = dia.clima || "☀️ Limpo";
  $("#inputObs").value = dia.observacoes || "";

  await carregarHoras(dia.id);
  renderFuncionarios();
  await carregarDespesas(dia.id);
  await carregarTotaisDia(dia.id);
}

$("#btnSalvarRdo").addEventListener("click", async () => {
  const dia = state.diasCache[state.diaAtivo];
  await apiJSON(`/api/dias/${dia.id}`, "PUT", {
    producao_dia: $("#inputProducao").value,
    clima: $("#selectClima").value,
    observacoes: $("#inputObs").value,
  });
  dia.producao_dia = $("#inputProducao").value;
  dia.clima = $("#selectClima").value;
  dia.observacoes = $("#inputObs").value;
  toast("RDO salvo.");
  await carregarFechamento();
});

async function carregarTotaisDia(diaId) {
  const t = await api(`/api/dias/${diaId}/totais`);
  $("#dayTotals").textContent = `FOLHA DO DIA ${moeda(t.folha)}   ·   DESPESAS DO DIA ${moeda(t.despesas)}`;
}

// ---------------------------------------------------------------- funcionários / folha

async function carregarFuncionarios() {
  state.funcionarios = await api(`/api/obras/${state.obraId}/funcionarios`);
}

$("#btnAddFunc").addEventListener("click", async () => {
  const nome = $("#inputFuncNome").value.trim();
  const funcao = $("#inputFuncFuncao").value.trim();
  const valor = parseFloat($("#inputFuncValor").value) || 0;
  if (!nome) { toast("Informe o nome do funcionário."); return; }

  await apiJSON(`/api/obras/${state.obraId}/funcionarios`, "POST", { nome, funcao, valor_hora: valor });
  $("#inputFuncNome").value = "";
  $("#inputFuncFuncao").value = "";
  $("#inputFuncValor").value = "";
  await carregarFuncionarios();
  renderFuncionarios();
  toast(`${nome} adicionado(a) à equipe.`);
});

async function carregarHoras(diaId) {
  state.horasCache = await api(`/api/dias/${diaId}/horas`); // { "func_id": {horas, valor_hora} }
}

function renderFuncionarios() {
  const wrap = $("#listaFuncionarios");
  wrap.innerHTML = "";

  if (state.funcionarios.length === 0) {
    wrap.innerHTML = '<p class="empty-inline">Nenhum funcionário cadastrado nesta obra ainda.</p>';
    calcularTotalFolha();
    return;
  }

  state.funcionarios.forEach((f) => {
    const registro = state.horasCache[String(f.id)];
    const horasAtual = registro ? registro.horas : 0;
    // Se já existe lançamento no dia, usa o valor/hora que foi confirmado
    // (snapshot). Se ainda não existe, mostra o valor atual como referência.
    const valorReferencia = registro ? registro.valor_hora : f.valor_hora;

    const row = document.createElement("div");
    row.className = "staff-row";
    row.dataset.funcId = f.id;
    row.innerHTML = `
      <div class="staff-view">
        <div class="staff-info">
          <div class="staff-name">${f.nome}${f.funcao ? " · " + f.funcao : ""}</div>
          <div class="staff-meta">${moeda(valorReferencia)}/h</div>
        </div>
        <input type="number" class="staff-hours" min="0" max="24" step="0.5"
               value="${horasAtual}" data-func-id="${f.id}" data-valor="${valorReferencia}">
        <button type="button" class="icon-mini btn-edit-func" title="Editar">✎</button>
        <button type="button" class="icon-mini btn-del-func" title="Remover da equipe">🗑</button>
      </div>
      <div class="staff-edit hidden">
        <input type="text" class="edit-nome" value="${f.nome}" placeholder="Nome">
        <input type="text" class="edit-funcao" value="${f.funcao || ""}" placeholder="Função">
        <input type="number" class="edit-valor" value="${f.valor_hora}" min="0" step="0.5" placeholder="Valor da hora">
        <div class="edit-actions">
          <button type="button" class="btn btn-outline btn-small btn-cancel-edit">Cancelar</button>
          <button type="button" class="btn btn-brass btn-small btn-save-edit">Salvar</button>
        </div>
      </div>
    `;
    wrap.appendChild(row);
  });

  $all(".staff-hours").forEach((inp) => inp.addEventListener("input", calcularTotalFolha));

  $all(".btn-edit-func").forEach((btn) => btn.addEventListener("click", (e) => {
    const row = e.target.closest(".staff-row");
    row.querySelector(".staff-view").classList.add("hidden");
    row.querySelector(".staff-edit").classList.remove("hidden");
  }));

  $all(".btn-cancel-edit").forEach((btn) => btn.addEventListener("click", (e) => {
    const row = e.target.closest(".staff-row");
    row.querySelector(".staff-edit").classList.add("hidden");
    row.querySelector(".staff-view").classList.remove("hidden");
  }));

  $all(".btn-save-edit").forEach((btn) => btn.addEventListener("click", async (e) => {
    const row = e.target.closest(".staff-row");
    const funcId = row.dataset.funcId;
    const nome = row.querySelector(".edit-nome").value.trim();
    const funcao = row.querySelector(".edit-funcao").value.trim();
    const valor_hora = parseFloat(row.querySelector(".edit-valor").value) || 0;

    if (!nome) { toast("Informe o nome do funcionário."); return; }

    await apiJSON(`/api/funcionarios/${funcId}`, "PUT", { nome, funcao, valor_hora });
    toast("Funcionário atualizado. (Semanas já lançadas não mudam de valor.)");
    await carregarFuncionarios();
    renderFuncionarios();
  }));

  $all(".btn-del-func").forEach((btn) => btn.addEventListener("click", async (e) => {
    const row = e.target.closest(".staff-row");
    const funcId = row.dataset.funcId;
    const nomeFunc = row.querySelector(".staff-name").textContent;
    if (!confirm(`Remover ${nomeFunc} da equipe ativa? O histórico de horas já lançado é mantido.`)) return;
    await api(`/api/funcionarios/${funcId}`, { method: "DELETE" });
    await carregarFuncionarios();
    renderFuncionarios();
    toast("Funcionário removido da equipe ativa.");
  }));

  calcularTotalFolha();
}

function calcularTotalFolha() {
  let total = 0;
  $all(".staff-hours").forEach((inp) => {
    total += (parseFloat(inp.value) || 0) * parseFloat(inp.dataset.valor);
  });
  $("#totalFolhaDia").textContent = `TOTAL DA FOLHA NO DIA · ${moeda(total)}`;
}

$("#btnSalvarHoras").addEventListener("click", async () => {
  const dia = state.diasCache[state.diaAtivo];
  const registros = $all(".staff-hours").map((inp) => ({
    funcionario_id: Number(inp.dataset.funcId),
    horas: parseFloat(inp.value) || 0,
  }));
  await apiJSON(`/api/dias/${dia.id}/horas`, "PUT", { registros });
  toast("Ponto do dia salvo.");
  await carregarTotaisDia(dia.id);
  await carregarFechamento();
});

// ---------------------------------------------------------------- caixa / despesas

$all(".toggle-opt").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.statusSelecionado = btn.dataset.status;
    $all(".toggle-opt").forEach((b) => {
      b.classList.remove("active-reembolso", "active-em-aberto", "active-pago");
    });
    const cls = { "Reembolso": "active-reembolso", "Em Aberto": "active-em-aberto", "Pago": "active-pago" }[btn.dataset.status];
    btn.classList.add(cls);
  });
});
// estado inicial visual
document.querySelector('[data-status="Em Aberto"]').classList.add("active-em-aberto");

async function carregarDespesas(diaId) {
  const despesas = await api(`/api/dias/${diaId}/despesas`);
  const wrap = $("#listaDespesas");
  wrap.innerHTML = "";
  let total = 0;

  if (despesas.length === 0) {
    wrap.innerHTML = '<p class="empty-inline">Nenhum lançamento neste dia ainda.</p>';
  }

  despesas.forEach((d) => {
    total += d.valor;
    const badgeCls = { "Reembolso": "badge-reembolso", "Em Aberto": "badge-em-aberto", "Pago": "badge-pago" }[d.status_pagamento] || "";
    const anexoTexto = d.nome_arquivo ? `📎 ${d.nome_arquivo}` : (d.imagem_path ? "📎 anexo" : "");
    const row = document.createElement("div");
    row.className = "expense-row";
    row.innerHTML = `
      <div>
        <div class="expense-desc">${d.descricao}<span class="badge ${badgeCls}">${d.status_pagamento}</span></div>
        <div class="expense-cat">${d.categoria}${anexoTexto ? " · " + anexoTexto : ""}</div>
        <div class="expense-value">${moeda(d.valor)}</div>
      </div>
      <button class="del-btn" data-id="${d.id}">🗑</button>
    `;
    wrap.appendChild(row);
  });

  $all(".del-btn").forEach((b) => b.addEventListener("click", async () => {
    await api(`/api/despesas/${b.dataset.id}`, { method: "DELETE" });
    await carregarDespesas(diaId);
    await carregarTotaisDia(diaId);
    await carregarFechamento();
    toast("Lançamento removido.");
  }));

  $("#totalDespesasDia").textContent = `TOTAL LANÇADO NO DIA · ${moeda(total)}`;
}

$("#btnLancarDespesa").addEventListener("click", async () => {
  const dia = state.diasCache[state.diaAtivo];
  const descricao = $("#inputDescricao").value.trim();
  const valor = parseFloat($("#inputValor").value);

  if (!descricao || !valor || valor <= 0) {
    toast("Preencha descrição e valor válido.");
    return;
  }

  const form = new FormData();
  form.append("categoria", $("#inputCategoria").value);
  form.append("descricao", descricao);
  form.append("valor", valor);
  form.append("status_pagamento", state.statusSelecionado);
  const arquivo = $("#inputImagem").files[0];
  if (arquivo) form.append("imagem", arquivo);

  await api(`/api/dias/${dia.id}/despesas`, { method: "POST", body: form });

  $("#inputDescricao").value = "";
  $("#inputValor").value = "";
  $("#inputImagem").value = "";

  await carregarDespesas(dia.id);
  await carregarTotaisDia(dia.id);
  await carregarFechamento();
  toast("Despesa lançada.");
});

// ---------------------------------------------------------------- fechamento

async function carregarFechamento() {
  if (!state.semanaId) return;
  const d = await api(`/api/semanas/${state.semanaId}/fechamento`);
  const DIAS_UTEIS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta"];

  // ---- 1) Avanço físico Seg-Sex ----
  const rep = $("#relatorioFisico");
  rep.innerHTML = "";

  const metaLinha = document.createElement("p");
  metaLinha.className = "rdo-day-text";
  metaLinha.innerHTML = `<b>Meta:</b> ${d.meta || "(não definida)"} ${d.meta_atingida ? "✅" : "❌"}`;
  rep.appendChild(metaLinha);

  const subAvanco = document.createElement("div");
  subAvanco.className = "rdo-subtitle";
  subAvanco.textContent = "Avanço da semana (Seg a Sex)";
  rep.appendChild(subAvanco);

  d.dias.filter((dia) => DIAS_UTEIS.includes(dia.dia_semana)).forEach((dia) => {
    const bloco = document.createElement("div");
    bloco.className = "rdo-day";
    bloco.innerHTML = `
      <div class="rdo-day-head">${dia.dia_semana}</div>
      <div class="rdo-day-text">${dia.producao_dia || "<i>sem registro de produção</i>"}</div>
    `;
    rep.appendChild(bloco);
  });

  const fimDeSemana = d.dias.filter((dia) => !DIAS_UTEIS.includes(dia.dia_semana) && (dia.producao_dia || dia.observacoes));
  if (fimDeSemana.length > 0) {
    const subFDS = document.createElement("div");
    subFDS.className = "rdo-subtitle";
    subFDS.textContent = "Fim de semana";
    rep.appendChild(subFDS);
    fimDeSemana.forEach((dia) => {
      const bloco = document.createElement("div");
      bloco.className = "rdo-day";
      bloco.innerHTML = `<div class="rdo-day-head">${dia.dia_semana}</div><div class="rdo-day-text">${dia.producao_dia || ""}</div>`;
      rep.appendChild(bloco);
    });
  }

  // ---- 2) Clima ----
  const subClima = document.createElement("div");
  subClima.className = "rdo-subtitle";
  subClima.textContent = "Ocorrências de clima";
  rep.appendChild(subClima);
  if (d.clima_alertas.length > 0) {
    d.clima_alertas.forEach((c) => {
      const p = document.createElement("p");
      p.className = "rdo-day-obs";
      p.textContent = `${c.dia_semana}: ${c.clima}`;
      rep.appendChild(p);
    });
  } else {
    const p = document.createElement("p");
    p.className = "rdo-empty";
    p.textContent = "Sem ocorrências relevantes nesta semana.";
    rep.appendChild(p);
  }

  // ---- 3) Observações e alertas ----
  const subObs = document.createElement("div");
  subObs.className = "rdo-subtitle";
  subObs.textContent = "Observações e alertas";
  rep.appendChild(subObs);
  const comObs = d.dias.filter((dia) => dia.observacoes);
  if (comObs.length > 0) {
    comObs.forEach((dia) => {
      const p = document.createElement("p");
      p.className = "rdo-day-obs";
      p.innerHTML = `<b>${dia.dia_semana}:</b> ${dia.observacoes}`;
      rep.appendChild(p);
    });
  } else {
    const p = document.createElement("p");
    p.className = "rdo-empty";
    p.textContent = "Nenhuma observação registrada.";
    rep.appendChild(p);
  }

  // ---- 4) Financeiro (title block) ----
  $("#tbFolha").textContent = moeda(d.financeiro.folha);
  $("#tbReembolso").textContent = moeda(d.financeiro.reembolso);
  $("#tbTerceiros").textContent = moeda(d.financeiro.terceiros);
  $("#tbPago").textContent = moeda(d.financeiro.pago);
  $("#tbObra").textContent = d.obra_nome;
  $("#tbSemana").textContent = d.semana_numero;

  // ---- 5) Despesas detalhadas da semana (com nome do anexo) ----
  const wrapDesp = $("#despesasSemana");
  wrapDesp.innerHTML = "";
  if (d.despesas.length === 0) {
    wrapDesp.innerHTML = '<p class="empty-inline">Nenhum lançamento de caixa nesta semana.</p>';
  } else {
    d.despesas.forEach((desp) => {
      const badgeCls = { "Reembolso": "badge-reembolso", "Em Aberto": "badge-em-aberto", "Pago": "badge-pago" }[desp.status_pagamento] || "";
      const anexoTexto = desp.nome_arquivo ? `📎 ${desp.nome_arquivo}` : (desp.imagem_path ? "📎 anexo" : "sem anexo");
      const row = document.createElement("div");
      row.className = "expense-row";
      row.innerHTML = `
        <div>
          <div class="expense-desc">${desp.descricao}<span class="badge ${badgeCls}">${desp.status_pagamento}</span></div>
          <div class="expense-cat">${desp.dia_semana} · ${desp.categoria} · ${anexoTexto}</div>
          <div class="expense-value">${moeda(desp.valor)}</div>
        </div>
      `;
      wrapDesp.appendChild(row);
    });
  }

  $("#btnExportar").href = `/api/semanas/${state.semanaId}/relatorio.txt`;
}

// ---------------------------------------------------------------- boot

function atualizarDataHoje() {
  const hoje = new Date();
  $("#dataHoje").textContent = hoje.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
}

atualizarDataHoje();
carregarObras();
