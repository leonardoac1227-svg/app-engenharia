# -*- coding: utf-8 -*-
"""Compressão de anexos antes de gravar — imagens e PDFs.

Por que isso importa numa obra: a nota fiscal fotografada pelo celular sai com
4 a 12 MB. Num mês de obra são centenas de anexos, e o custo (e o tempo de
upload no 4G do canteiro) cresce rápido. O objetivo é reduzir megabytes sem
perder a legibilidade do documento — uma nota que não dá para ler não serve
como comprovante.

Três garantias que valem para tudo aqui:

1. **Nunca aumentar.** Se a "compressão" resultar em arquivo maior que o
   original (acontece com imagens já otimizadas), devolvemos o original.
2. **Nunca perder o anexo.** Qualquer falha na compressão devolve os bytes
   originais. Um PDF exótico que quebre a biblioteca não pode fazer o gestor
   perder o comprovante.
3. **Nunca ilegível.** Os limites de tamanho e qualidade são conservadores;
   texto de nota fiscal continua legível.
"""

from __future__ import annotations

import io
import logging

from .. import config

log = logging.getLogger("obra-erp")

# 1600px no lado maior: uma nota A4 fotografada continua legível para leitura
# e para conferência de valores, e cabe bem na tela do celular.
LADO_MAXIMO = 1600
QUALIDADE_JPEG = 82

# Dentro de PDFs somos um pouco mais generosos: o documento pode ter texto
# pequeno (dados do emitente, chave de acesso da NF-e).
LADO_MAXIMO_PDF = 2000
QUALIDADE_JPEG_PDF = 75
LARGURA_MINIMA_PARA_REDUZIR = 1000  # abaixo disso, mexer só piora

try:
    from PIL import Image, ImageOps
    PIL_OK = True

    # Teto contra "bomba de descompressão": um PNG de poucos KB pode declarar
    # 60.000 × 60.000 pixels e consumir gigabytes ao ser aberto, derrubando o
    # processo. 40 MP cobre com folga qualquer celular (um iPhone tira 12 MP)
    # e recusa o resto.
    Image.MAX_IMAGE_PIXELS = 40_000_000
except ImportError:  # pragma: no cover
    PIL_OK = False


def _resultado(original: bytes, final: bytes, formato: str, detalhe: str = "") -> tuple[bytes, dict]:
    """Escolhe entre original e comprimido, e devolve o relatório da operação."""
    if not final or len(final) >= len(original):
        return original, {
            "comprimido": False, "formato": formato,
            "tamanho_original": len(original), "tamanho_final": len(original),
            "reducao_percentual": 0.0, "detalhe": detalhe or "já estava otimizado",
        }
    return final, {
        "comprimido": True, "formato": formato,
        "tamanho_original": len(original), "tamanho_final": len(final),
        "reducao_percentual": round((1 - len(final) / len(original)) * 100, 1),
        "detalhe": detalhe,
    }


# ---------------------------------------------------------------- imagens

def comprimir_imagem(dados: bytes) -> tuple[bytes, dict]:
    """Redimensiona para no máximo 1600px e reencoda em JPEG."""
    if not PIL_OK:
        return _resultado(dados, b"", "imagem", "Pillow indisponível")

    try:
        imagem = Image.open(io.BytesIO(dados))
        imagem = ImageOps.exif_transpose(imagem)  # respeita a rotação do celular

        # Transparência vira fundo branco: JPEG não tem canal alfa, e sem isso
        # um PNG de recibo com fundo transparente viraria um borrão preto.
        if imagem.mode in ("RGBA", "LA", "P"):
            imagem = imagem.convert("RGBA")
            fundo = Image.new("RGB", imagem.size, (255, 255, 255))
            fundo.paste(imagem, mask=imagem.split()[-1])
            imagem = fundo
        elif imagem.mode != "RGB":
            imagem = imagem.convert("RGB")

        largura_original = imagem.width
        imagem.thumbnail((LADO_MAXIMO, LADO_MAXIMO), Image.LANCZOS)

        buffer = io.BytesIO()
        imagem.save(buffer, format="JPEG", quality=QUALIDADE_JPEG, optimize=True, progressive=True)

        detalhe = (f"{largura_original}px → {imagem.width}px"
                   if imagem.width < largura_original else "reencodada em JPEG")
        return _resultado(dados, buffer.getvalue(), "imagem", detalhe)
    except Exception as erro:  # noqa: BLE001 — anexo nunca pode se perder
        log.warning("Falha ao comprimir imagem: %s", erro)
        return _resultado(dados, b"", "imagem", "arquivo mantido como veio")


# ---------------------------------------------------------------- PDFs

def _reduzir_imagens_do_pdf(pdf) -> int:
    """Reamostra as imagens embutidas — é onde mora o peso de nota escaneada."""
    import pikepdf

    trocadas = 0
    for pagina in pdf.pages:
        recursos = pagina.get("/Resources")
        if recursos is None or "/XObject" not in recursos:
            continue

        for nome, objeto in dict(recursos["/XObject"]).items():
            try:
                if objeto.get("/Subtype") != "/Image":
                    continue
                largura = int(objeto.get("/Width", 0))
                if largura < LARGURA_MINIMA_PARA_REDUZIR:
                    continue

                imagem = pikepdf.PdfImage(objeto).as_pil_image()
                if imagem.mode not in ("RGB", "L"):
                    imagem = imagem.convert("RGB")
                if imagem.width > LADO_MAXIMO_PDF or imagem.height > LADO_MAXIMO_PDF:
                    imagem.thumbnail((LADO_MAXIMO_PDF, LADO_MAXIMO_PDF), Image.LANCZOS)

                buffer = io.BytesIO()
                imagem.save(buffer, format="JPEG", quality=QUALIDADE_JPEG_PDF, optimize=True)
                bytes_novos = buffer.getvalue()

                # Só troca se realmente compensar (>10% menor).
                if len(bytes_novos) >= len(bytes(objeto.read_raw_bytes())) * 0.9:
                    continue

                nova = pikepdf.Stream(pdf, bytes_novos)
                nova.Type = pikepdf.Name("/XObject")
                nova.Subtype = pikepdf.Name("/Image")
                nova.Width = imagem.width
                nova.Height = imagem.height
                nova.ColorSpace = pikepdf.Name(
                    "/DeviceGray" if imagem.mode == "L" else "/DeviceRGB")
                nova.BitsPerComponent = 8
                nova.Filter = pikepdf.Name("/DCTDecode")

                recursos["/XObject"][nome] = pdf.make_indirect(nova)
                trocadas += 1
            except Exception:  # noqa: BLE001 — imagem problemática fica como está
                continue
    return trocadas


def comprimir_pdf(dados: bytes) -> tuple[bytes, dict]:
    """Comprime PDF em camadas, da mais eficaz para a mais conservadora.

    1. **pikepdf**: reamostra as imagens embutidas (o grosso do peso em nota
       escaneada) e recomprime os fluxos.
    2. **pypdf**: recomprime apenas os fluxos de conteúdo — ganho menor, mas
       puro Python, roda em qualquer lugar.
    3. Sem nenhuma das duas, o arquivo segue como veio.
    """
    try:
        import pikepdf

        with pikepdf.open(io.BytesIO(dados)) as pdf:
            trocadas = _reduzir_imagens_do_pdf(pdf) if PIL_OK else 0
            buffer = io.BytesIO()
            pdf.save(
                buffer,
                compress_streams=True,
                recompress_flate=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                normalize_content=True,
            )
            detalhe = (f"{trocadas} imagem(ns) reamostrada(s)" if trocadas
                       else "fluxos recomprimidos")
            resultado = _resultado(dados, buffer.getvalue(), "pdf", detalhe)
            if resultado[1]["comprimido"]:
                return resultado
    except ImportError:
        pass
    except Exception as erro:  # noqa: BLE001
        log.warning("pikepdf falhou, tentando pypdf: %s", erro)

    try:
        from pypdf import PdfReader, PdfWriter

        leitor = PdfReader(io.BytesIO(dados))
        if leitor.is_encrypted:
            return _resultado(dados, b"", "pdf", "PDF protegido, mantido como veio")

        escritor = PdfWriter()
        for pagina in leitor.pages:
            pagina.compress_content_streams()
            escritor.add_page(pagina)

        buffer = io.BytesIO()
        escritor.write(buffer)
        return _resultado(dados, buffer.getvalue(), "pdf", "fluxos recomprimidos")
    except ImportError:
        return _resultado(dados, b"", "pdf", "sem biblioteca de PDF instalada")
    except Exception as erro:  # noqa: BLE001
        log.warning("Falha ao comprimir PDF: %s", erro)
        return _resultado(dados, b"", "pdf", "arquivo mantido como veio")


# ---------------------------------------------------------------- fachada

def otimizar(dados: bytes, extensao: str) -> tuple[bytes, dict]:
    """Ponto único de entrada: decide o tratamento pela extensão do arquivo."""
    extensao = (extensao or "").lower().lstrip(".")

    if extensao in config.EXT_IMAGEM:
        return comprimir_imagem(dados)
    if extensao == "pdf":
        return comprimir_pdf(dados)
    return _resultado(dados, b"", extensao or "arquivo", "tipo sem compressão aplicável")
