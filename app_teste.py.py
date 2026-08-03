import streamlit as st

# 1. Configuração da Tela
st.set_page_config(page_title="Gestão de Obras", layout="centered", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    div.stButton > button:first-child {
        background-color: #007AFF; color: white; border-radius: 8px; height: 45px; font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# 2. Navegação Principal (Obras)
st.sidebar.title("🏗️ Minhas Obras")
obra = st.sidebar.radio("Selecione a Obra:", ["Damha 1 - Lote 15", "Damha 1 - Lote 22", "Condomínio Ipês"])

st.title(obra)

# 3. Navegação Temporal (Semanas e Dias)
semana = st.selectbox("Selecione a Semana", ["Semana 1", "Semana 2", "Semana 3", "Semana 4"])

# O uso de abas simula muito bem a navegação por dias da semana no celular
aba_seg, aba_ter, aba_qua, aba_qui, aba_sex = st.tabs(["Seg", "Ter", "Qua", "Qui", "Sex"])

with aba_seg:
    st.subheader(f"Diário - Segunda-feira ({semana})")
    
    # MÓDULO 1: Avanço Físico (RDO)
    with st.expander("📝 Produção do Dia (RDO)", expanded=True):
        producao = st.text_area("O que foi produzido hoje?", placeholder="Ex: Levante de alvenaria e concretagem das vergas...")
        clima = st.selectbox("Condição Climática", ["Ensolarado", "Nublado", "Chuva Leve", "Chuva Forte (Paralisação)"])
        st.button("Salvar RDO Diário", key="btn_rdo_seg")
        
    # MÓDULO 2: Equipe
    with st.expander("👷 Efetivo (Folha)", expanded=False):
        st.write("Marque quem trabalhou hoje:")
        st.checkbox("Caio (Mestre) - 8h")
        st.checkbox("João (Pedreiro) - 8h")
        st.checkbox("Avulso (Servente) - 4h")
        st.button("Salvar Efetivo", key="btn_eqp_seg")
        
    # MÓDULO 3: Caixa e Materiais
    with st.expander("💸 Despesas e Materiais", expanded=False):
        descricao = st.text_input("Descrição do Gasto", placeholder="Ex: 50 sacos de cimento Votorantim")
        valor = st.number_input("Valor Total (R$)", min_value=0.0, format="%.2f")
        tipo_pagamento = st.radio("Pagamento", ["Reembolso (Eu paguei)", "Em Aberto (Cliente paga)"], horizontal=True)
        st.file_uploader("Anexar Nota ou PIX", type=['jpg', 'png', 'pdf'])
        st.button("Lançar Despesa", key="btn_desp_seg")

with aba_ter:
    st.write("Interface de terça-feira entra aqui...")

# 4. Botões Mágicos de Sexta-feira
st.divider()
st.subheader("📊 Fechamento da Semana")
col1, col2 = st.columns(2)
with col1:
    st.button("Gerar Relatório de Obra (PDF)", use_container_width=True)
with col2:
    st.button("Gerar Fechamento Financeiro", use_container_width=True)