# -*- coding: utf-8 -*-
"""
OBRA ERP DE BOLSO
Gestão física e financeira de obras de alto padrão — mobile-first.

Stack: Streamlit + SQLite (banco relacional em arquivo local, sem
necessidade de servidor SQL externo). Pronto para rodar com
`streamlit run app.py` e para deploy no Render.
"""

import os
import io
import sqlite3
import uuid
from datetime import datetime, date

import streamlit as st

# --------------------------------------------------------------------------
# CONFIGURAÇÃO GERAL
# --------------------------------------------------------------------------

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "obra_erp.db")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
CLIMA_OPCOES = ["☀️ Limpo", "☁️ Nublado", "🌦️ Chuva Leve", "⛈️ Chuva c/ Paralisação"]
CATEGORIA_OPCOES = ["Material", "Serviço", "Locação", "Frete", "Outro"]
STATUS_OPCOES = ["Reembolso", "Em Aberto", "Pago"]

st.set_page_config(
    page_title="Obra ERP",
    page_icon="🏗️",
    layout="centered",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------
# BANCO DE DADOS (SQLite — simula um banco relacional completo)
# --------------------------------------------------------------------------

@st.cache_resource
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS obras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            criado_em TEXT NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS semanas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER NOT NULL,
            numero INTEGER NOT NULL,
            meta TEXT DEFAULT '',
            meta_atingida INTEGER DEFAULT 0,
            criado_em TEXT NOT NULL,
            FOREIGN KEY (obra_id) REFERENCES obras (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS funcionarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            obra_id INTEGER NOT NULL,
            nome TEXT NOT NULL,
            funcao TEXT DEFAULT '',
            valor_hora REAL NOT NULL DEFAULT 0,
            ativo INTEGER DEFAULT 1,
            FOREIGN KEY (obra_id) REFERENCES obras (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            semana_id INTEGER NOT NULL,
            dia_semana TEXT NOT NULL,
            producao_dia TEXT DEFAULT '',
            clima TEXT DEFAULT '',
            observacoes TEXT DEFAULT '',
            UNIQUE(semana_id, dia_semana),
            FOREIGN KEY (semana_id) REFERENCES semanas (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS horas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dia_id INTEGER NOT NULL,
            funcionario_id INTEGER NOT NULL,
            horas_trabalhadas REAL DEFAULT 0,
            UNIQUE(dia_id, funcionario_id),
            FOREIGN KEY (dia_id) REFERENCES dias (id),
            FOREIGN KEY (funcionario_id) REFERENCES funcionarios (id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS despesas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dia_id INTEGER NOT NULL,
            categoria TEXT NOT NULL,
            descricao TEXT DEFAULT '',
            valor REAL NOT NULL DEFAULT 0,
            status_pagamento TEXT NOT NULL,
            imagem_path TEXT DEFAULT '',
            criado_em TEXT NOT NULL,
            FOREIGN KEY (dia_id) REFERENCES dias (id)
        );
    """)

    conn.commit()


# ---------------------------- CRUD: OBRAS --------------------------------

def add_obra(nome):
    conn = get_conn()
    conn.execute(
        "INSERT INTO obras (nome, criado_em) VALUES (?, ?)",
        (nome.strip(), datetime.now().isoformat()),
    )
    conn.commit()


def list_obras():
    conn = get_conn()
    return conn.execute("SELECT id, nome FROM obras ORDER BY id").fetchall()


# ---------------------------- CRUD: SEMANAS -------------------------------

def add_semana(obra_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(MAX(numero), 0) FROM semanas WHERE obra_id = ?", (obra_id,)
    ).fetchone()
    proximo_numero = row[0] + 1
    conn.execute(
        "INSERT INTO semanas (obra_id, numero, meta, meta_atingida, criado_em) "
        "VALUES (?, ?, '', 0, ?)",
        (obra_id, proximo_numero, datetime.now().isoformat()),
    )
    conn.commit()
    return proximo_numero


def list_semanas(obra_id):
    conn = get_conn()
    return conn.execute(
        "SELECT id, numero, meta, meta_atingida FROM semanas WHERE obra_id = ? ORDER BY numero",
        (obra_id,),
    ).fetchall()


def update_meta(semana_id, meta, atingida):
    conn = get_conn()
    conn.execute(
        "UPDATE semanas SET meta = ?, meta_atingida = ? WHERE id = ?",
        (meta, 1 if atingida else 0, semana_id),
    )
    conn.commit()


# ---------------------------- CRUD: DIAS ----------------------------------

def get_or_create_dia(semana_id, dia_semana):
    conn = get_conn()
    row = conn.execute(
        "SELECT id, producao_dia, clima, observacoes FROM dias WHERE semana_id = ? AND dia_semana = ?",
        (semana_id, dia_semana),
    ).fetchone()
    if row:
        return row
    conn.execute(
        "INSERT INTO dias (semana_id, dia_semana) VALUES (?, ?)",
        (semana_id, dia_semana),
    )
    conn.commit()
    return get_or_create_dia(semana_id, dia_semana)


def update_rdo(dia_id, producao, clima, observacoes):
    conn = get_conn()
    conn.execute(
        "UPDATE dias SET producao_dia = ?, clima = ?, observacoes = ? WHERE id = ?",
        (producao, clima, observacoes, dia_id),
    )
    conn.commit()


# ------------------------ CRUD: FUNCIONÁRIOS ------------------------------

def add_funcionario(obra_id, nome, funcao, valor_hora):
    conn = get_conn()
    conn.execute(
        "INSERT INTO funcionarios (obra_id, nome, funcao, valor_hora, ativo) VALUES (?, ?, ?, ?, 1)",
        (obra_id, nome.strip(), funcao.strip(), valor_hora),
    )
    conn.commit()


def list_funcionarios(obra_id, apenas_ativos=True):
    conn = get_conn()
    q = "SELECT id, nome, funcao, valor_hora FROM funcionarios WHERE obra_id = ?"
    if apenas_ativos:
        q += " AND ativo = 1"
    q += " ORDER BY nome"
    return conn.execute(q, (obra_id,)).fetchall()


def desativar_funcionario(funcionario_id):
    conn = get_conn()
    conn.execute("UPDATE funcionarios SET ativo = 0 WHERE id = ?", (funcionario_id,))
    conn.commit()


# ---------------------------- CRUD: HORAS ---------------------------------

def upsert_horas(dia_id, funcionario_id, horas_trabalhadas):
    conn = get_conn()
    conn.execute(
        """INSERT INTO horas (dia_id, funcionario_id, horas_trabalhadas)
           VALUES (?, ?, ?)
           ON CONFLICT(dia_id, funcionario_id)
           DO UPDATE SET horas_trabalhadas = excluded.horas_trabalhadas""",
        (dia_id, funcionario_id, horas_trabalhadas),
    )
    conn.commit()


def get_horas_dia(dia_id):
    conn = get_conn()
    return dict(
        conn.execute(
            "SELECT funcionario_id, horas_trabalhadas FROM horas WHERE dia_id = ?",
            (dia_id,),
        ).fetchall()
    )


# --------------------------- CRUD: DESPESAS -------------------------------

def add_despesa(dia_id, categoria, descricao, valor, status, imagem_path=""):
    conn = get_conn()
    conn.execute(
        """INSERT INTO despesas (dia_id, categoria, descricao, valor, status_pagamento, imagem_path, criado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (dia_id, categoria, descricao, valor, status, imagem_path, datetime.now().isoformat()),
    )
    conn.commit()


def list_despesas(dia_id):
    conn = get_conn()
    return conn.execute(
        """SELECT id, categoria, descricao, valor, status_pagamento, imagem_path
           FROM despesas WHERE dia_id = ? ORDER BY id DESC""",
        (dia_id,),
    ).fetchall()


def delete_despesa(despesa_id):
    conn = get_conn()
    conn.execute("DELETE FROM despesas WHERE id = ?", (despesa_id,))
    conn.commit()


# ----------------------- AGREGAÇÕES SEMANAIS (FECHAMENTO) -----------------

def get_dias_da_semana(semana_id):
    """Garante que os 7 dias existam e retorna na ordem correta."""
    dias = []
    for d in DIAS_SEMANA:
        dias.append(get_or_create_dia(semana_id, d))
    return dias  # cada item: (id, producao_dia, clima, observacoes)


def get_week_financials(semana_id, obra_id):
    conn = get_conn()
    dias_ids = [d[0] for d in get_dias_da_semana(semana_id)]
    if not dias_ids:
        return {"folha": 0.0, "reembolso": 0.0, "em_aberto": 0.0, "pago": 0.0, "terceiros": 0.0}

    placeholders = ",".join("?" * len(dias_ids))

    # Total da folha de pagamento (horas x valor_hora)
    rows = conn.execute(
        f"""SELECT h.funcionario_id, h.horas_trabalhadas, f.valor_hora
            FROM horas h JOIN funcionarios f ON h.funcionario_id = f.id
            WHERE h.dia_id IN ({placeholders})""",
        dias_ids,
    ).fetchall()
    total_folha = sum(h * v for _, h, v in rows)

    # Totais de despesas por status
    def soma_status(status):
        r = conn.execute(
            f"""SELECT COALESCE(SUM(valor), 0) FROM despesas
                WHERE dia_id IN ({placeholders}) AND status_pagamento = ?""",
            dias_ids + [status],
        ).fetchone()
        return r[0] or 0.0

    total_reembolso = soma_status("Reembolso")
    total_em_aberto = soma_status("Em Aberto")
    total_pago = soma_status("Pago")

    return {
        "folha": total_folha,
        "reembolso": total_reembolso,
        "em_aberto": total_em_aberto,
        "pago": total_pago,
        "terceiros": total_folha + total_em_aberto,
    }


def get_dia_folha_total(dia_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT h.horas_trabalhadas, f.valor_hora FROM horas h
           JOIN funcionarios f ON h.funcionario_id = f.id WHERE h.dia_id = ?""",
        (dia_id,),
    ).fetchall()
    return sum(h * v for h, v in rows)


def get_dia_despesas_total(dia_id):
    conn = get_conn()
    r = conn.execute("SELECT COALESCE(SUM(valor),0) FROM despesas WHERE dia_id = ?", (dia_id,)).fetchone()
    return r[0] or 0.0


def moeda(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------
# ESTILO — DARK MODE PREMIUM, MOBILE-FIRST
# --------------------------------------------------------------------------

def inject_css():
    st.markdown("""
    <style>
        :root {
            --bg: #0B0E13;
            --card: #151A21;
            --card-border: #232933;
            --accent: #C9A34E;
            --accent-soft: rgba(201,163,78,0.15);
            --text: #EDEFF2;
            --text-muted: #9AA3AE;
            --green: #3FBF7F;
            --red: #E1604D;
        }
        .stApp { background-color: var(--bg); color: var(--text); }
        section[data-testid="stSidebar"] { background-color: #0F131A; border-right: 1px solid var(--card-border); }

        h1, h2, h3, h4 { color: var(--text) !important; font-weight: 700 !important; letter-spacing: -0.3px; }

        /* Botões grandes, largura total, toque com o polegar */
        .stButton>button, .stDownloadButton>button, .stFormSubmitButton>button {
            width: 100%;
            border-radius: 14px;
            height: 3em;
            font-weight: 600;
            background: linear-gradient(135deg, var(--accent), #A8843A);
            color: #14100A;
            border: none;
            transition: transform 0.05s ease-in-out;
        }
        .stButton>button:active, .stFormSubmitButton>button:active { transform: scale(0.98); }

        /* Cards / expanders arredondados */
        div[data-testid="stExpander"] {
            background-color: var(--card);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            margin-bottom: 12px;
            overflow: hidden;
        }
        div[data-testid="stExpander"] summary {
            font-weight: 600;
            padding: 4px 2px;
        }

        /* Inputs */
        .stTextInput input, .stNumberInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] {
            background-color: #10141A !important;
            border-radius: 10px !important;
            border: 1px solid var(--card-border) !important;
            color: var(--text) !important;
        }

        /* Tabs (dias da semana) */
        button[data-baseweb="tab"] {
            border-radius: 10px 10px 0 0;
            font-weight: 600;
        }

        /* Cartões de resumo financeiro */
        .metric-card {
            background-color: var(--card);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 16px 18px;
            margin-bottom: 10px;
        }
        .metric-label { color: var(--text-muted); font-size: 0.82rem; font-weight: 500; }
        .metric-value { color: var(--accent); font-size: 1.6rem; font-weight: 700; }

        .badge {
            display: inline-block; padding: 2px 10px; border-radius: 999px;
            font-size: 0.72rem; font-weight: 700; margin-left: 6px;
        }
        .badge-reembolso { background: rgba(63,191,127,0.15); color: var(--green); }
        .badge-aberto { background: rgba(225,96,77,0.15); color: var(--red); }
        .badge-pago { background: var(--accent-soft); color: var(--accent); }

        hr { border-color: var(--card-border) !important; }
    </style>
    """, unsafe_allow_html=True)


def metric_card(label, value, col=None):
    target = col if col is not None else st
    target.markdown(
        f"""<div class="metric-card">
                <div class="metric-label">{label}</div>
                <div class="metric-value">{value}</div>
            </div>""",
        unsafe_allow_html=True,
    )


def badge(status):
    cls = {"Reembolso": "badge-reembolso", "Em Aberto": "badge-aberto", "Pago": "badge-pago"}.get(status, "")
    return f'<span class="badge {cls}">{status}</span>'


# --------------------------------------------------------------------------
# SIDEBAR — TROCA DE OBRAS (CENTROS DE CUSTO)
# --------------------------------------------------------------------------

def sidebar_obras():
    with st.sidebar:
        st.markdown("### 🏗️ Obras")
        obras = list_obras()

        if not obras:
            st.caption("Nenhuma obra cadastrada ainda.")
            obra_id = None
        else:
            nomes = {oid: nome for oid, nome in obras}
            if "obra_id" not in st.session_state or st.session_state.obra_id not in nomes:
                st.session_state.obra_id = obras[0][0]

            escolha = st.radio(
                "Selecione a obra ativa",
                options=list(nomes.keys()),
                format_func=lambda x: nomes[x],
                index=list(nomes.keys()).index(st.session_state.obra_id),
                label_visibility="collapsed",
            )
            st.session_state.obra_id = escolha
            obra_id = escolha

        st.markdown("---")
        with st.expander("➕ Nova Obra"):
            novo_nome = st.text_input("Nome da obra", placeholder="Ex: Obra 3 - Cobertura", key="novo_nome_obra")
            if st.button("Criar obra", key="btn_criar_obra"):
                if novo_nome.strip():
                    add_obra(novo_nome)
                    st.success("Obra criada!")
                    st.rerun()
                else:
                    st.warning("Digite um nome para a obra.")

        st.markdown("---")
        st.caption("ERP de Bolso · dados isolados por obra")
        return obra_id


# --------------------------------------------------------------------------
# SELEÇÃO / CRIAÇÃO DE SEMANA
# --------------------------------------------------------------------------

def selecionar_semana(obra_id):
    semanas = list_semanas(obra_id)

    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("+ Semana"):
            add_semana(obra_id)
            st.rerun()

    if not semanas:
        with col1:
            st.info("Nenhuma semana cadastrada nesta obra. Crie a primeira semana →")
        return None

    opcoes = {sid: f"Semana {num}" for sid, num, meta, atingida in semanas}
    if "semana_id" not in st.session_state or st.session_state.semana_id not in opcoes:
        st.session_state.semana_id = semanas[-1][0]  # última semana por padrão

    with col1:
        escolha = st.selectbox(
            "Semana ativa",
            options=list(opcoes.keys()),
            format_func=lambda x: opcoes[x],
            index=list(opcoes.keys()).index(st.session_state.semana_id),
            label_visibility="collapsed",
        )
    st.session_state.semana_id = escolha
    return escolha


# --------------------------------------------------------------------------
# META DA SEMANA
# --------------------------------------------------------------------------

def bloco_meta(semana_id):
    conn = get_conn()
    row = conn.execute("SELECT meta, meta_atingida FROM semanas WHERE id = ?", (semana_id,)).fetchone()
    meta_atual, atingida_atual = row

    with st.container():
        st.markdown("#### 🎯 Meta da Semana")
        meta = st.text_area(
            "Objetivo principal da semana",
            value=meta_atual or "",
            placeholder="Ex: Concluir alvenaria do 2º pavimento",
            label_visibility="collapsed",
            key=f"meta_{semana_id}",
        )
        atingida = st.checkbox("✅ Meta atingida", value=bool(atingida_atual), key=f"atingida_{semana_id}")
        if st.button("Salvar meta", key=f"salvar_meta_{semana_id}"):
            update_meta(semana_id, meta, atingida)
            st.success("Meta atualizada.")
            st.rerun()
    st.markdown("---")


# --------------------------------------------------------------------------
# MÓDULO 1 — RDO (AVANÇO FÍSICO)
# --------------------------------------------------------------------------

def modulo_rdo(dia_id, producao_atual, clima_atual, obs_atual):
    with st.expander("📋 RDO · Avanço Físico", expanded=False):
        producao = st.text_area(
            "Produção do dia",
            value=producao_atual or "",
            placeholder="Ex: Levante de alvenaria concluído no 2º pavimento",
            key=f"producao_{dia_id}",
        )
        clima_index = CLIMA_OPCOES.index(clima_atual) if clima_atual in CLIMA_OPCOES else 0
        clima = st.selectbox("Clima", CLIMA_OPCOES, index=clima_index, key=f"clima_{dia_id}")
        obs = st.text_area(
            "Observações gerais",
            value=obs_atual or "",
            placeholder="Ex: Falta de material X, atraso de fornecedor Y...",
            key=f"obs_{dia_id}",
        )
        if st.button("Salvar RDO", key=f"salvar_rdo_{dia_id}"):
            update_rdo(dia_id, producao, clima, obs)
            st.success("RDO salvo.")
            st.rerun()


# --------------------------------------------------------------------------
# MÓDULO 2 — FOLHA DE PAGAMENTO (EFETIVO DIÁRIO)
# --------------------------------------------------------------------------

def modulo_folha(obra_id, dia_id):
    with st.expander("👷 Folha de Pagamento · Efetivo Diário", expanded=False):
        funcionarios = list_funcionarios(obra_id)
        horas_registradas = get_horas_dia(dia_id)

        with st.expander("➕ Cadastrar funcionário / equipe"):
            nome_f = st.text_input("Nome", key=f"novo_func_nome_{obra_id}")
            funcao_f = st.text_input("Função (mestre, pedreiro, servente...)", key=f"novo_func_funcao_{obra_id}")
            valor_f = st.number_input("Valor da hora (R$)", min_value=0.0, step=1.0, key=f"novo_func_valor_{obra_id}")
            if st.button("Adicionar à equipe", key=f"btn_add_func_{obra_id}"):
                if nome_f.strip():
                    add_funcionario(obra_id, nome_f, funcao_f, valor_f)
                    st.success(f"{nome_f} adicionado(a) à equipe.")
                    st.rerun()
                else:
                    st.warning("Informe o nome do funcionário.")

        if not funcionarios:
            st.caption("Nenhum funcionário cadastrado nesta obra ainda. Use o painel acima para adicionar.")
            return

        st.markdown("**Horas trabalhadas hoje:**")
        total_dia = 0.0
        valores_inputados = {}
        for fid, nome, funcao, valor_hora in funcionarios:
            horas_atual = horas_registradas.get(fid, 0.0)
            c1, c2 = st.columns([2, 1])
            with c1:
                label = f"{nome}" + (f" · {funcao}" if funcao else "")
                st.caption(f"{label}  ·  {moeda(valor_hora)}/h")
            with c2:
                h = st.number_input(
                    "h", min_value=0.0, max_value=24.0, step=0.5,
                    value=float(horas_atual), key=f"horas_{dia_id}_{fid}",
                    label_visibility="collapsed",
                )
            valores_inputados[fid] = h
            total_dia += h * valor_hora

        if st.button("Salvar ponto do dia", key=f"salvar_horas_{dia_id}"):
            for fid, h in valores_inputados.items():
                upsert_horas(dia_id, fid, h)
            st.success("Ponto do dia salvo.")
            st.rerun()

        st.markdown(f"**Total da folha no dia: <span style='color:#C9A34E'>{moeda(total_dia)}</span>**", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# MÓDULO 3 — CAIXA E MATERIAIS (AVANÇO FINANCEIRO)
# --------------------------------------------------------------------------

def modulo_caixa(dia_id):
    with st.expander("💰 Caixa e Materiais", expanded=False):
        with st.form(key=f"form_despesa_{dia_id}", clear_on_submit=True):
            categoria = st.selectbox("Categoria", CATEGORIA_OPCOES)
            descricao = st.text_input("Descrição / Fornecedor", placeholder="Ex: Cimento - Casa do Construtor")
            valor = st.number_input("Valor (R$)", min_value=0.0, step=10.0)
            status = st.radio(
                "Status do pagamento",
                STATUS_OPCOES,
                horizontal=True,
                help="Reembolso: você adiantou. Em Aberto: cliente paga na sexta. Pago: já resolvido.",
            )
            imagem = st.file_uploader("📎 Nota fiscal / comprovante PIX", type=["png", "jpg", "jpeg", "pdf"])
            enviado = st.form_submit_button("Lançar despesa")

            if enviado:
                if not descricao.strip() or valor <= 0:
                    st.warning("Preencha descrição e valor válido.")
                else:
                    img_path = ""
                    if imagem is not None:
                        ext = os.path.splitext(imagem.name)[1]
                        img_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
                        with open(img_path, "wb") as f:
                            f.write(imagem.getbuffer())
                    add_despesa(dia_id, categoria, descricao, valor, status, img_path)
                    st.success("Despesa lançada.")
                    st.rerun()

        despesas = list_despesas(dia_id)
        if despesas:
            st.markdown("**Lançamentos do dia:**")
            total_dia = 0.0
            for did, categoria, descricao, valor, status, img in despesas:
                total_dia += valor
                c1, c2 = st.columns([4, 1])
                with c1:
                    st.markdown(
                        f"**{descricao}** · {categoria}  \n"
                        f"{moeda(valor)} {badge(status)}"
                        + (" · 📎" if img else ""),
                        unsafe_allow_html=True,
                    )
                with c2:
                    if st.button("🗑️", key=f"del_desp_{did}"):
                        delete_despesa(did)
                        st.rerun()
            st.markdown(f"**Total lançado no dia: <span style='color:#C9A34E'>{moeda(total_dia)}</span>**", unsafe_allow_html=True)
        else:
            st.caption("Nenhum lançamento neste dia ainda.")


# --------------------------------------------------------------------------
# FECHAMENTO DA SEMANA (RELATÓRIOS)
# --------------------------------------------------------------------------

def gerar_relatorio_texto(obra_nome, semana_num, meta, atingida, dias_data, financeiro):
    linhas = []
    linhas.append(f"RELATÓRIO SEMANAL — {obra_nome} — Semana {semana_num}")
    linhas.append("=" * 50)
    linhas.append("")
    linhas.append("META DA SEMANA")
    linhas.append(f"- {meta or '(não definida)'}")
    linhas.append(f"- Status: {'✅ ATINGIDA' if atingida else '❌ NÃO ATINGIDA'}")
    linhas.append("")
    linhas.append("RELATÓRIO FÍSICO (RDO)")
    for dia_nome, producao, clima, obs in dias_data:
        if producao or (clima and "Limpo" not in clima) or obs:
            linhas.append(f"\n[{dia_nome}] Clima: {clima or '-'}")
            if producao:
                linhas.append(f"  Produção: {producao}")
            if obs:
                linhas.append(f"  Observações: {obs}")
    linhas.append("")
    linhas.append("RELATÓRIO FINANCEIRO")
    linhas.append(f"- Total Folha de Pagamento: {moeda(financeiro['folha'])}")
    linhas.append(f"- Total a transferir p/ empresa do gestor (Reembolso): {moeda(financeiro['reembolso'])}")
    linhas.append(f"- Total a pagar a terceiros (Folha + Em Aberto): {moeda(financeiro['terceiros'])}")
    linhas.append(f"- Total já Pago no período: {moeda(financeiro['pago'])}")
    linhas.append("")
    linhas.append(f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    return "\n".join(linhas)


def bloco_fechamento(obra_nome, semana_id, semana_num):
    conn = get_conn()
    meta, atingida = conn.execute(
        "SELECT meta, meta_atingida FROM semanas WHERE id = ?", (semana_id,)
    ).fetchone()

    dias_raw = get_dias_da_semana(semana_id)  # (id, producao, clima, obs) na ordem Seg..Dom
    dias_data = [
        (DIAS_SEMANA[i], dias_raw[i][1], dias_raw[i][2], dias_raw[i][3])
        for i in range(len(DIAS_SEMANA))
    ]

    financeiro = get_week_financials(semana_id, obra_id=st.session_state.obra_id)

    st.markdown("## 🔒 Fechamento da Semana")
    st.caption("Compilado físico e financeiro para envio ao cliente / diretoria.")

    with st.expander("📄 Relatório Físico (RDO da semana)", expanded=False):
        st.markdown(f"**Meta:** {meta or '_(não definida)_'}  {'✅' if atingida else '❌'}")
        houve_conteudo = False
        for dia_nome, producao, clima, obs in dias_data:
            if producao or obs or (clima and "Limpo" not in (clima or "")):
                houve_conteudo = True
                st.markdown(f"**{dia_nome}** · {clima or '-'}")
                if producao:
                    st.write(producao)
                if obs:
                    st.caption(f"Obs: {obs}")
                st.markdown("---")
        if not houve_conteudo:
            st.caption("Nenhum RDO preenchido ainda nesta semana.")

    st.markdown("#### 💵 Relatório Financeiro")
    c1, c2 = st.columns(2)
    metric_card("Resumo da Folha", moeda(financeiro["folha"]), c1)
    metric_card("Transferir p/ Gestor (Reembolso)", moeda(financeiro["reembolso"]), c2)
    c3, c4 = st.columns(2)
    metric_card("A pagar a Terceiros (Folha + Em Aberto)", moeda(financeiro["terceiros"]), c3)
    metric_card("Total já Pago no período", moeda(financeiro["pago"]), c4)

    relatorio = gerar_relatorio_texto(obra_nome, semana_num, meta, atingida, dias_data, financeiro)
    st.download_button(
        "⬇️ Exportar relatório da semana (.txt)",
        data=relatorio.encode("utf-8"),
        file_name=f"{obra_nome.replace(' ', '_')}_semana_{semana_num}.txt",
        mime="text/plain",
    )


# --------------------------------------------------------------------------
# APP PRINCIPAL
# --------------------------------------------------------------------------

def main():
    init_db()
    inject_css()

    obra_id = sidebar_obras()

    st.markdown("# 🏗️ Obra ERP")
    st.caption("Lançamentos de canteiro em poucos segundos.")

    if obra_id is None:
        st.info("👈 Crie sua primeira obra no menu lateral para começar.")
        return

    obra_nome = dict(list_obras())[obra_id]
    st.markdown(f"### {obra_nome}")

    semana_id = selecionar_semana(obra_id)
    if semana_id is None:
        return

    semana_num = dict((sid, num) for sid, num, _, _ in list_semanas(obra_id))[semana_id]
    st.markdown(f"#### Semana {semana_num}")

    bloco_meta(semana_id)

    tabs = st.tabs(DIAS_SEMANA)
    for i, dia_nome in enumerate(DIAS_SEMANA):
        with tabs[i]:
            dia_id, producao_atual, clima_atual, obs_atual = get_or_create_dia(semana_id, dia_nome)

            folha_total = get_dia_folha_total(dia_id)
            despesas_total = get_dia_despesas_total(dia_id)
            st.caption(
                f"Folha do dia: {moeda(folha_total)}   ·   Despesas do dia: {moeda(despesas_total)}"
            )

            modulo_rdo(dia_id, producao_atual, clima_atual, obs_atual)
            modulo_folha(obra_id, dia_id)
            modulo_caixa(dia_id)

    st.markdown("---")
    bloco_fechamento(obra_nome, semana_id, semana_num)


if __name__ == "__main__":
    main()
