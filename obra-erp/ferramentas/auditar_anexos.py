# -*- coding: utf-8 -*-
"""Auditoria e recuperação de anexos.

Responde três perguntas, nesta ordem:

1. Quantos anexos o banco tem registrados?
2. Quantos deles realmente abrem hoje?
3. Dos que estão só no disco do servidor, quantos consigo salvar no bucket?

Uso:

    # só conferir (não altera nada)
    python ferramentas/auditar_anexos.py

    # conferir e subir para o Supabase o que ainda existir no disco
    python ferramentas/auditar_anexos.py --enviar

Rode com as mesmas variáveis de ambiente do app (DATABASE_URL, SUPABASE_URL,
SUPABASE_SERVICE_KEY, SUPABASE_BUCKET, DATA_DIR).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    enviar_para_bucket = "--enviar" in sys.argv

    from obra import config, create_app, storage
    from obra.db import todos

    app = create_app()

    print(f"Banco       : {config.BACKEND}")
    print(f"Anexos      : {config.STORAGE}"
          + (f" (bucket '{config.SUPABASE_BUCKET}')" if storage.usando_supabase() else ""))
    print(f"Pasta local : {config.UPLOAD_DIR}")
    print()

    with app.app_context():
        registros = todos(
            """SELECT arquivo AS nome, 'foto' AS origem FROM fotos WHERE arquivo != ''
               UNION ALL
               SELECT imagem_path AS nome, 'despesa' AS origem FROM despesas
                WHERE imagem_path IS NOT NULL AND imagem_path != ''""")

        total = len(registros)
        no_bucket = no_disco = perdidos = enviados = 0
        lista_perdidos = []

        for indice, linha in enumerate(registros, 1):
            nome = os.path.basename(linha["nome"])
            caminho = storage.caminho_local(nome)

            if caminho and storage.usando_supabase():
                no_disco += 1
                if enviar_para_bucket:
                    try:
                        with open(caminho, "rb") as arquivo:
                            storage.enviar(nome, arquivo.read())
                        enviados += 1
                    except storage.ErroStorage as erro:
                        print(f"  falha ao enviar {nome}: {erro}")
            elif caminho:
                no_disco += 1
            else:
                situacao = storage.verificar(nome)
                if situacao["encontrado"]:
                    no_bucket += 1
                else:
                    perdidos += 1
                    lista_perdidos.append((linha["origem"], nome, situacao["detalhe"]))

            if indice % 50 == 0:
                print(f"  ... {indice}/{total} conferidos")

    print(f"\nRegistrados no banco : {total}")
    print(f"Disponíveis no bucket: {no_bucket}")
    print(f"Ainda no disco local : {no_disco}")
    if enviar_para_bucket:
        print(f"Enviados agora       : {enviados}")
    print(f"Não encontrados      : {perdidos}")

    if lista_perdidos:
        print("\nArquivos que não abrem (até 15):")
        for origem, nome, motivo in lista_perdidos[:15]:
            print(f"  [{origem}] {nome} — {motivo[:110]}")
        print("\nSe o motivo for disco temporário, esses arquivos foram apagados"
              "\nnum deploy anterior e não há como recuperá-los pelo app. O registro"
              "\nda despesa/foto continua no banco; só o arquivo se perdeu.")

    if no_disco and storage.usando_supabase() and not enviar_para_bucket:
        print(f"\n→ {no_disco} anexo(s) ainda estão só no disco (que é temporário)."
              "\n  Rode de novo com --enviar para copiá-los para o Supabase.")


if __name__ == "__main__":
    main()
