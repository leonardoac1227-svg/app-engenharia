# -*- coding: utf-8 -*-
"""Configuração central do Obra ERP.

Tudo que depende do ambiente (caminho do banco, pasta de uploads, chave de
sessão, credenciais iniciais) fica isolado aqui, para que o mesmo código rode
sem alteração no notebook do gestor, num servidor da empresa ou no Render.

Variáveis de ambiente reconhecidas
----------------------------------
DATA_DIR        Pasta onde ficam o banco e os anexos. Aponte para o disco
                persistente do Render (ex: /var/data) para não perder dados.
SECRET_KEY      Chave de assinatura da sessão. Se ausente, é gerada e salva
                em DATA_DIR/.secret_key (mantém o login válido entre deploys).
ADMIN_EMAIL     E-mail do primeiro usuário gestor (padrão: gestor@obra.local).
ADMIN_SENHA     Senha inicial desse usuário (padrão: obra123 — troque no 1º acesso).
ADMIN_NOME      Nome exibido do primeiro usuário.
ANTHROPIC_API_KEY  Habilita o resumo executivo semanal por IA (opcional).
MAX_UPLOAD_MB   Tamanho máximo de cada envio (padrão 25 MB).
"""

from __future__ import annotations

import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Pasta de dados: fora do código-fonte por padrão, para sobreviver a deploys
# quando montada em um disco persistente.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(BASE_DIR, "dados")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

# Instalações antigas guardavam os anexos aqui; continuamos servindo esses
# arquivos para não quebrar lançamentos já feitos.
LEGACY_UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")

DB_PATH = os.environ.get("DB_PATH") or os.path.join(DATA_DIR, "obra_erp.db")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))

EXT_IMAGEM = {"png", "jpg", "jpeg", "webp", "heic", "heif"}
EXT_DOCUMENTO = {"pdf"}
EXT_PERMITIDAS = EXT_IMAGEM | EXT_DOCUMENTO

DIAS_SEMANA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
DIAS_UTEIS = DIAS_SEMANA[:5]

PAPEIS = {
    "gestor": "Gestor / Engenheiro",
    "mestre": "Mestre de obra",
    "cliente": "Cliente / Síndico",
}

CATEGORIAS_PADRAO = ["Material", "Serviço", "Locação", "Frete", "Alimentação", "Outro"]
STATUS_PAGAMENTO = ["Reembolso", "Em Aberto", "Pago"]

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "gestor@obra.local")
ADMIN_SENHA = os.environ.get("ADMIN_SENHA", "obra123")
ADMIN_NOME = os.environ.get("ADMIN_NOME", "Gestor")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# ---------------------------------------------------------------- banco de dados
#
# Sem DATABASE_URL, o app roda em SQLite (arquivo local) — é o modo de
# desenvolvimento e continua funcionando exatamente como antes. Com
# DATABASE_URL apontando para o Postgres do Supabase, o mesmo código passa a
# falar com o Postgres; nenhum blueprint muda.

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
BACKEND = "postgres" if DATABASE_URL.startswith(("postgres://", "postgresql://")) else "sqlite"

# ---------------------------------------------------------------- anexos
#
# Fotos e notas fiscais podem ficar no disco (padrão) ou no Supabase Storage.
# No Supabase, o bucket deve ser PRIVADO: o app gera uma URL assinada de curta
# duração a cada acesso, mantendo a mesma regra de sessão da rota /arquivos.

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "anexos")
SUPABASE_URL_VALIDADE = int(os.environ.get("SUPABASE_URL_VALIDADE", "3600"))  # segundos

STORAGE = "supabase" if (SUPABASE_URL and SUPABASE_SERVICE_KEY) else "local"


def carregar_secret_key() -> str:
    """Recupera (ou cria) a chave de sessão.

    Gerar em memória faria todo mundo cair no login a cada restart do
    servidor; por isso persistimos em disco quando não há SECRET_KEY no
    ambiente.
    """
    chave = os.environ.get("SECRET_KEY")
    if chave:
        return chave

    caminho = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(caminho):
        with open(caminho, "r", encoding="utf-8") as fp:
            conteudo = fp.read().strip()
            if conteudo:
                return conteudo

    nova = secrets.token_hex(32)
    with open(caminho, "w", encoding="utf-8") as fp:
        fp.write(nova)
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass  # sistemas de arquivo sem suporte a permissão POSIX
    return nova
