# -*- coding: utf-8 -*-
"""Roda todas as suítes do projeto e resume o resultado.

    python ferramentas/rodar_testes.py

Cada suíte é um processo separado, com o seu próprio banco temporário — assim
uma não contamina a outra, e uma que trave não leva as demais junto.
Código de saída 1 se qualquer uma falhar, para encaixar em CI.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPO_LIMITE = 300


def main() -> None:
    suites = sorted(glob.glob(os.path.join(RAIZ, "ferramentas", "teste_*.py")))
    if not suites:
        raise SystemExit("Nenhuma suíte encontrada em ferramentas/teste_*.py")

    falharam, total_tempo = [], time.perf_counter()

    for caminho in suites:
        nome = os.path.basename(caminho)[:-3]
        with tempfile.TemporaryDirectory(prefix=f"{nome}-") as pasta:
            ambiente = dict(os.environ, DATA_DIR=os.path.join(pasta, "dados"))
            inicio = time.perf_counter()
            try:
                processo = subprocess.run(
                    [sys.executable, caminho], capture_output=True, text=True,
                    timeout=TEMPO_LIMITE, env=ambiente, cwd=RAIZ)
                saiu_bem = processo.returncode == 0
                saida = processo.stdout
            except subprocess.TimeoutExpired:
                saiu_bem, saida = False, "(estourou o tempo limite)"

        duracao = time.perf_counter() - inicio
        resumo = next((l for l in reversed(saida.splitlines()) if l.strip()), "")
        print(f"{'  OK  ' if saiu_bem else ' FALHA'} {nome:26} {duracao:5.1f}s  {resumo[:60]}")

        if not saiu_bem:
            falharam.append(nome)
            for linha in saida.splitlines():
                if "FALHA" in linha:
                    print(f"          {linha.strip()[:100]}")

    print(f"\n{len(suites)} suíte(s) em {time.perf_counter() - total_tempo:.1f}s · "
          + ("todas passaram" if not falharam else f"falharam: {', '.join(falharam)}"))
    sys.exit(1 if falharam else 0)


if __name__ == "__main__":
    main()
