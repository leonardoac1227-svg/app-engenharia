import streamlit as st
import pandas as pd
from datetime import datetime

# ==========================================
# 1. CONFIGURAÇÃO DA INTERFACE (MOBILE-FIRST)
# ==========================================
st.set_page_config(page_title="ERP Engenharia", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    div.stButton > button:first-child {
        background-color: #0F172A; color: white; border-radius: 8px; height: 48px; font-weight: bold; width: 100%; margin-top: 10px;
    }
    .st-emotion-cache-1v0mbdj > img { border-radius: 10px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. BANCO DE DADOS TEMPORÁRIO (ESTRUTURA RELACIONAL)
# ==========================================
def iniciar_banco():
    if 'dados' not in st.session_state:
        st.session_state.dados = {
            'metas': {}, # { 'Obra-Semana': {'texto': '', 'concluida': False} }
            'rdo': [],   # Lista de dicionários diários
            'equipe': [
                {"nome": "Caio (Empreiteiro)", "valor_hora": 25.00},
                {"nome": "Ozirlei Emanuel (Fundação)", "valor_hora": 20.00},
                {"nome": "Avulso (Servente)", "valor_hora": 12.00}
            ],
            'horas_trabalhadas': [], # Registros de ponto diário
            'despesas': []           # Caixa e materiais
        }

iniciar_banco()
db = st.session_state.dados

# ==========================================
# 3. NAVEGAÇÃO PRINCIPAL E ESTRUTURA
# ==========================================
st.sidebar.title("🏗️ Centros de Custo")
obras = ["Obra 1 - Damha 1 (Acabamento)", "Obra 2 - Damha 1 (Fundação)", "Obra 3 - Damha 1 (Alvenaria)"]
obra_ativa = st.sidebar.radio("Selecione a Obra:", obras)

st.title(obra_ativa.split("-")[0].strip()) # Mostra só "Obra 1" grande no topo
st.caption(f"📍 {obra_ativa.split('-')[1].strip() if '-' in obra_ativa else obra_ativa}")

# Navegação de Semanas
semanas = [f"Semana {i}" for i in range(1, 11)]
semana_ativa = st.selectbox("Cronograma:", semanas)

chave_meta = f"{obra_ativa}-{semana_ativa}"
if chave_meta not in db['metas']:
    db['metas'][chave_meta] = {'texto': '', 'concluida': False}

# Quadro de Metas
with st.container(border=True):
    st.markdown("🎯 **Meta da Semana**")
    nova_meta = st.text_input("Objetivo principal:", value=db['metas'][chave_meta]['texto'], placeholder="Ex: Finalizar levante de alvenaria...")
    db['metas'][chave_meta]['texto'] = nova_meta
    db['metas'][chave_meta]['concluida'] = st.checkbox("✅ Meta Atingida", value=db['metas'][chave_meta]['concluida'])

st.divider()

# ==========================================
# 4. DIÁRIO DE OBRA (DIAS DA SEMANA)
# ==========================================
dias_semana = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
abas = st.tabs(dias_semana)

for idx, aba in enumerate(abas):
    dia_nome = dias_semana[idx]
    chave_dia = f"{obra_ativa}-{semana_ativa}-{dia_nome}"
    
    with aba:
        # --- MÓDULO A: AVANÇO FÍSICO (RDO) ---
        with st.expander("📝 Produção e RDO", expanded=False):
            producao = st.text_area("O que foi produzido hoje?", key=f"prod_{chave_dia}")
            clima = st.selectbox("Clima", ["Limpo", "Nublado", "Chuva Leve", "Chuva (Paralisou)"], key=f"clima_{chave_dia}")
            obs = st.text_input("Observações/Ocorrências", key=f"obs_{chave_dia}")
            
            if st.button("Salvar RDO", key=f"btn_rdo_{chave_dia}"):
                db['rdo'].append({"chave": chave_dia, "producao": producao, "clima": clima, "obs": obs})
                st.success("RDO Salvo!")

        # --- MÓDULO B: EFETIVO E PONTO ---
        with st.expander("👷 Controle de Efetivo", expanded=False):
            st.write("Marque as horas trabalhadas hoje:")
            horas_dia = {}
            for func in db['equipe']:
                horas = st.number_input(f"{func['nome']} (R${func['valor_hora']}/h)", min_value=0.0, max_value=24.0, step=1.0, key=f"hr_{chave_dia}_{func['nome']}")
                if horas > 0:
                    horas_dia[func['nome']] = {"horas": horas, "valor_hora": func['valor_hora'], "total": horas * func['valor_hora']}
            
            if st.button("Registrar Ponto", key=f"btn_pt_{chave_dia}"):
                # Remove registros antigos desse dia para não duplicar se o usuário editar
                db['horas_trabalhadas'] = [h for h in db['horas_trabalhadas'] if h['chave'] != chave_dia]
                for nome, dados in horas_dia.items():
                    db['horas_trabalhadas'].append({
                        "chave": chave_dia, "obra": obra_ativa, "semana": semana_ativa, "dia": dia_nome,
                        "nome": nome, "horas": dados['horas'], "custo_total": dados['total']
                    })
                st.success("Folha do dia atualizada!")

        # --- MÓDULO C: CAIXA E MATERIAIS ---
        with st.expander("💸 Notas e Lançamentos", expanded=False):
            cat = st.selectbox("Categoria", ["Material", "Serviço Terceirizado", "Locação", "Logística (Frete)"], key=f"cat_{chave_dia}")
            desc = st.text_input("Descrição e Fornecedor", placeholder="Ex: Cimento CP II - RS ROCHA LTDA", key=f"desc_{chave_dia}")
            
            col_v, col_t = st.columns(2)
            with col_v:
                valor = st.number_input("Valor (R$)", min_value=0.0, format="%.2f", key=f"val_{chave_dia}")
            with col_t:
                tipo_pag = st.selectbox("Pagamento", ["Reembolso (Eu)", "Em Aberto (Cliente)", "Já Pago"], key=f"pag_{chave_dia}")
            
            st.file_uploader("📸 Foto da Nota/PIX (Simulação IA)", type=['jpg', 'png'], key=f"foto_{chave_dia}")
            
            if st.button("Lançar Despesa", key=f"btn_desp_{chave_dia}"):
                if desc and valor > 0:
                    db['despesas'].append({
                        "chave": chave_dia, "obra": obra_ativa, "semana": semana_ativa, "dia": dia_nome,
                        "categoria": cat, "descricao": desc, "valor": valor, "tipo": tipo_pag
                    })
                    st.success("Lançado no caixa!")

# ==========================================
# 5. GERADOR DE RELATÓRIOS (FECHAMENTO DE SEXTA)
# ==========================================
st.divider()
st.subheader("📊 Fechamento da Semana")

col_rel1, col_rel2 = st.columns(2)

# Filtra dados apenas da obra e semana ativas
despesas_semana = [d for d in db['despesas'] if d['obra'] == obra_ativa and d['semana'] == semana_ativa]
horas_semana = [h for h in db['horas_trabalhadas'] if h['obra'] == obra_ativa and h['semana'] == semana_ativa]

with col_rel1:
    if st.button("📄 Resumo Diário (RDO)"):
        st.info(f"**Meta:** {db['metas'][chave_meta]['texto']} ({'Concluída ✅' if db['metas'][chave_meta]['concluida'] else 'Pendente ⏳'})")
        # Mostra os RDOs preenchidos
        rdos_filtrados = [r for r in db['rdo'] if r['chave'].startswith(f"{obra_ativa}-{semana_ativa}")]
        if rdos_filtrados:
            for r in rdos_filtrados:
                dia_rdo = r['chave'].split('-')[-1]
                st.write(f"**{dia_rdo}:** {r['producao']} *(Clima: {r['clima']})*")
        else:
            st.warning("Nenhum RDO preenchido nesta semana.")

with col_rel2:
    if st.button("💰 Acerto Financeiro"):
        df_desp = pd.DataFrame(despesas_semana)
        df_horas = pd.DataFrame(horas_semana)
        
        # 1. Resumo da Folha
        st.markdown("**👷 Folha de Pagamento**")
        total_folha = 0
        if not df_horas.empty:
            folha_agrupada = df_horas.groupby("nome")['custo_total'].sum().reset_index()
            st.dataframe(folha_agrupada, hide_index=True, use_container_width=True)
            total_folha = folha_agrupada['custo_total'].sum()
        else:
            st.write("Sem registros de ponto.")
            
        # 2. Resumo de Caixa
        st.markdown("**📦 Materiais e Serviços**")
        total_reembolso = 0
        total_pendente = 0
        if not df_desp.empty:
            total_reembolso = df_desp[df_desp['tipo'] == 'Reembolso (Eu)']['valor'].sum()
            total_pendente = df_desp[df_desp['tipo'] == 'Em Aberto (Cliente)']['valor'].sum()
            st.dataframe(df_desp[['dia', 'descricao', 'valor', 'tipo']], hide_index=True, use_container_width=True)
        else:
            st.write("Sem compras na semana.")
            
        # 3. O "Cheque" Final
        st.error(f"**💵 A TRANSFERIR PARA A EMPRESA:** R$ {total_reembolso:,.2f}")
        st.warning(f"**💸 A PAGAR TERCEIROS (Folha + Fornecedores):** R$ {total_pendente + total_folha:,.2f}")
