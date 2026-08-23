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

from . import config

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
    """Salva um upload em DATA_DIR/uploads e devolve os metadados.

    Fotos de canteiro chegam com 6–12 MB direto da câmera do celular. Elas são
    reamostradas para no máximo 1600px e recomprimidas em JPEG: o relatório
    carrega rápido no 4G do canteiro e o disco do servidor não estoura.
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

    destino_nome = f"{uuid.uuid4().hex}.{ext}"
    largura = altura = 0

    if ext in config.EXT_IMAGEM and PIL_OK:
        try:
            img = Image.open(arquivo.stream)
            img = ImageOps.exif_transpose(img)  # respeita a orientação do celular
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((1600, 1600), Image.LANCZOS)
            largura, altura = img.size
            destino_nome = f"{uuid.uuid4().hex}.jpg"
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=82, optimize=True)
            with open(os.path.join(config.UPLOAD_DIR, destino_nome), "wb") as fp:
                fp.write(buffer.getvalue())
        except Exception:
            arquivo.stream.seek(0)
            arquivo.save(os.path.join(config.UPLOAD_DIR, destino_nome))
    else:
        arquivo.save(os.path.join(config.UPLOAD_DIR, destino_nome))

    return {
        "arquivo": destino_nome,
        "nome_original": nome_original,
        "largura": largura,
        "altura": altura,
        "is_imagem": ext in config.EXT_IMAGEM,
    }


def caminho_anexo(nome_arquivo: str) -> str | None:
    """Resolve o caminho físico de um anexo (com fallback para a pasta legada)."""
    if not nome_arquivo:
        return None
    nome = os.path.basename(nome_arquivo)
    for pasta in (config.UPLOAD_DIR, config.LEGACY_UPLOAD_DIR):
        caminho = os.path.join(pasta, nome)
        if os.path.exists(caminho):
            return caminho
    return None


def remover_anexo(nome_arquivo: str) -> None:
    caminho = caminho_anexo(nome_arquivo)
    if caminho:
        try:
            os.remove(caminho)
        except OSError:
            pass
