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
  state.horasCache = await api(`/api/dias/${diaId}/horas`);
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
    const row = document.createElement("div");
    row.className = "staff-row";
    const horasAtual = state.horasCache[String(f.id)] || 0;
    row.innerHTML = `
      <div class="staff-info">
        <div class="staff-name">${f.nome}${f.funcao ? " · " + f.funcao : ""}</div>
        <div class="staff-meta">${moeda(f.valor_hora)}/h</div>
      </div>
      <input type="number" class="staff-hours" min="0" max="24" step="0.5"
             value="${horasAtual}" data-func-id="${f.id}" data-valor="${f.valor_hora}">
    `;
    wrap.appendChild(row);
  });

  $all(".staff-hours").forEach((inp) => inp.addEventListener("input", calcularTotalFolha));
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
    const row = document.createElement("div");
    row.className = "expense-row";
    row.innerHTML = `
      <div>
        <div class="expense-desc">${d.descricao}<span class="badge ${badgeCls}">${d.status_pagamento}</span></div>
        <div class="expense-cat">${d.categoria}${d.imagem_path ? " · 📎 anexo" : ""}</div>
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

  const rep = $("#relatorioFisico");
  rep.innerHTML = "";
  const metaLinha = document.createElement("p");
  metaLinha.className = "rdo-day-text";
  metaLinha.innerHTML = `<b>Meta:</b> ${d.meta || "(não definida)"} ${d.meta_atingida ? "✅" : "❌"}`;
  rep.appendChild(metaLinha);

  let houveConteudo = false;
  d.dias.forEach((dia) => {
    const temConteudo = dia.producao_dia || dia.observacoes || (dia.clima && !dia.clima.includes("Limpo"));
    if (!temConteudo) return;
    houveConteudo = true;
    const bloco = document.createElement("div");
    bloco.className = "rdo-day";
    bloco.innerHTML = `
      <div class="rdo-day-head">${dia.dia_semana} · ${dia.clima || "-"}</div>
      ${dia.producao_dia ? `<div class="rdo-day-text">${dia.producao_dia}</div>` : ""}
      ${dia.observacoes ? `<div class="rdo-day-obs">Obs: ${dia.observacoes}</div>` : ""}
    `;
    rep.appendChild(bloco);
  });

  if (!houveConteudo) {
    const p = document.createElement("p");
    p.className = "rdo-empty";
    p.textContent = "Nenhum RDO preenchido ainda nesta semana.";
    rep.appendChild(p);
  }

  $("#tbFolha").textContent = moeda(d.financeiro.folha);
  $("#tbReembolso").textContent = moeda(d.financeiro.reembolso);
  $("#tbTerceiros").textContent = moeda(d.financeiro.terceiros);
  $("#tbPago").textContent = moeda(d.financeiro.pago);
  $("#tbObra").textContent = d.obra_nome;
  $("#tbSemana").textContent = d.semana_numero;

  $("#btnExportar").href = `/api/semanas/${state.semanaId}/relatorio.txt`;
}

// ---------------------------------------------------------------- boot

function atualizarDataHoje() {
  const hoje = new Date();
  $("#dataHoje").textContent = hoje.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
}

atualizarDataHoje();
carregarObras();
