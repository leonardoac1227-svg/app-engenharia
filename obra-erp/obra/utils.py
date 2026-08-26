# -*- coding: utf-8 -*-
"""Funções de apoio compartilhadas: erros de API, parsing seguro de entrada,
datas, formatação de moeda e tratamento de anexos.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from datetime import date, datetime, timedelta

from werkzeug.utils import secure_filename

from . import config, storage
from .services.compressao import otimizar

try:  # Pillow é opcional: sem ele, salvamos o arquivo original.
    from PIL import Image, ImageOps
    PIL_OK = True
except Exception:  # pragma: no cover
    PIL_OK = False


class ApiErro(Exception):
    """Erro de negócio devolvido como JSON com status HTTP adequado."""

    def __init__(self, mensagem: str, status: int = 400, codigo: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.codigo = codigo


# ---------------------------------------------------------------- entrada

def texto(valor, maximo: int = 500, obrigatorio: bool = False, campo: str = "campo") -> str:
    v = ("" if valor is None else str(valor)).strip()
    if obrigatorio and not v:
        raise ApiErro(f"Informe {campo}.")
    return v[:maximo]


def numero(valor, padrao: float = 0.0, minimo: float | None = None, maximo: float | None = None,
           campo: str = "valor") -> float:
    if valor in (None, ""):
        v = padrao
    else:
        try:
            # aceita "1.234,56" (formato brasileiro digitado à mão)
            if isinstance(valor, str):
                limpo = valor.strip().replace("R$", "").replace(" ", "")
                if "," in limpo and "." in limpo:
                    limpo = limpo.replace(".", "").replace(",", ".")
                elif "," in limpo:
                    limpo = limpo.replace(",", ".")
                v = float(limpo)
            else:
                v = float(valor)
        except (TypeError, ValueError):
            raise ApiErro(f"O campo {campo} precisa ser um número.")
    if minimo is not None and v < minimo:
        raise ApiErro(f"O campo {campo} não pode ser menor que {minimo:g}.")
    if maximo is not None and v > maximo:
        raise ApiErro(f"O campo {campo} não pode ser maior que {maximo:g}.")
    return round(v, 2)


def inteiro(valor, padrao: int = 0) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def booleano(valor) -> int:
    return 1 if valor in (True, 1, "1", "true", "True", "on", "sim") else 0


DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def data_iso(valor, padrao=None, campo: str = "data"):
    """Valida uma data no formato YYYY-MM-DD (o formato nativo do <input type=date>)."""
    if valor in (None, ""):
        return padrao
    v = str(valor).strip()[:10]
    if not DATA_RE.match(v):
        raise ApiErro(f"Data inválida em {campo}. Use o formato AAAA-MM-DD.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ApiErro(f"Data inexistente em {campo}.")
    return v


def hoje_iso() -> str:
    return date.today().isoformat()


def agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def segunda_da_semana(d: date | None = None) -> str:
    d = d or date.today()
    return (d - timedelta(days=d.weekday())).isoformat()


def somar_dias(data_str: str, dias: int) -> str:
    return (date.fromisoformat(data_str) + timedelta(days=dias)).isoformat()


def data_br(valor) -> str:
    if not valor:
        return ""
    try:
        return date.fromisoformat(str(valor)[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)


def moeda(v) -> str:
    v = float(v or 0)
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def numero_br(v, casas: int = 2) -> str:
    """Formata número no padrão pt-BR, sem casas decimais inúteis (42,5 / 32)."""
    v = float(v or 0)
    texto_formatado = f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if "," in texto_formatado:
        texto_formatado = texto_formatado.rstrip("0").rstrip(",")
    return texto_formatado or "0"


def slug(texto_livre: str, tamanho: int = 40) -> str:
    limpo = re.sub(r"[^a-zA-Z0-9]+", "-", (texto_livre or "").strip()).strip("-").lower()
    return (limpo or "obra")[:tamanho]


# ---------------------------------------------------------------- anexos

def extensao(nome: str) -> str:
    return nome.rsplit(".", 1)[-1].lower() if "." in (nome or "") else ""


def salvar_anexo(arquivo, apenas_imagem: bool = False) -> dict | None:
    """Salva um upload em DATA_DIR/uploads (ou no bucket) e devolve os metadados.

    Todo anexo passa pela compressão antes de ser gravado (ver
    `services/compressao.py`): foto de celular sai com 4–12 MB e nota fiscal
    escaneada em PDF passa fácil dos 5 MB. O arquivo original nunca é perdido:
    se a compressão falhar ou não compensar, gravamos os bytes como vieram.
    """
    if not arquivo or not arquivo.filename:
        return None

    nome_original = secure_filename(arquivo.filename) or "anexo"
    ext = extensao(nome_original)
    permitidas = config.EXT_IMAGEM if apenas_imagem else config.EXT_PERMITIDAS
    if ext not in permitidas:
        raise ApiErro(
            "Formato não aceito. Envie JPG, PNG, WEBP" + ("." if apenas_imagem else " ou PDF.")
        )

    arquivo.stream.seek(0)
    dados_originais = arquivo.stream.read()
    if not dados_originais:
        return None

    dados, relatorio = otimizar(dados_originais, ext)

    # Imagem comprimida sempre sai como JPEG; PDF mantém a extensão.
    if ext in config.EXT_IMAGEM and relatorio.get("comprimido"):
        ext_final, tipo = "jpg", "image/jpeg"
    else:
        ext_final = ext
        tipo = "application/pdf" if ext == "pdf" else None

    destino_nome = f"{uuid.uuid4().hex}.{ext_final}"
    storage.gravar(destino_nome, dados, tipo)

    largura = altura = 0
    if ext in config.EXT_IMAGEM and PIL_OK:
        try:
            with Image.open(io.BytesIO(dados)) as img:
                largura, altura = img.size
        except Exception:  # noqa: BLE001 — dimensão é informação acessória
            pass

    return {
        "arquivo": destino_nome,
        "nome_original": nome_original,
        "largura": largura,
        "altura": altura,
        "is_imagem": ext in config.EXT_IMAGEM,
        "tamanho": len(dados),
        "tamanho_original": relatorio["tamanho_original"],
        "reducao_percentual": relatorio["reducao_percentual"],
        "comprimido": relatorio["comprimido"],
    }


def caminho_anexo(nome_arquivo: str) -> str | None:
    """Caminho físico de um anexo no disco (com fallback para a pasta legada).

    Usado pelo gerador de PDF, que precisa dos bytes da imagem. No modo
    Supabase, um anexo pode não existir localmente — nesse caso o chamador usa
    `bytes_anexo()`.
    """
    if not nome_arquivo:
        return None
    return storage.caminho_local(nome_arquivo)


def bytes_anexo(nome_arquivo: str) -> bytes | None:
    """Conteúdo do anexo, venha ele do disco ou do bucket."""
    if not nome_arquivo:
        return None
    caminho = storage.caminho_local(nome_arquivo)
    if caminho:
        with open(caminho, "rb") as arquivo:
            return arquivo.read()
    if storage.usando_supabase():
        return storage.baixar(os.path.basename(nome_arquivo))
    return None


def remover_anexo(nome_arquivo: str) -> None:
    storage.remover(nome_arquivo)


def formatar_bytes(quantidade: int) -> str:
    """Tamanho legível: 1,2 MB em vez de 1258291 bytes."""
    valor = float(quantidade or 0)
    for unidade in ("B", "KB", "MB", "GB"):
        if valor < 1024 or unidade == "GB":
            casas = 0 if unidade in ("B", "KB") else 1
            return f"{valor:.{casas}f}".replace(".", ",") + f" {unidade}"
        valor /= 1024
    return f"{valor:.1f} GB"
