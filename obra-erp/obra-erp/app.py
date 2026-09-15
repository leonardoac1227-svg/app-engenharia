# -*- coding: utf-8 -*-
"""Ponto de entrada do Obra ERP.

Local:      python app.py
Produção:   gunicorn app:app --bind 0.0.0.0:$PORT
"""

import os

from obra import create_app

app = create_app()

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=porta, debug=debug)
