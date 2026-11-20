"""
Configuration Gunicorn (serveur Ubuntu), chargée par le service systemd :
    gunicorn -c deploiement/gunicorn.conf.py config.wsgi

Workers « gthread » : l'impression d'un ticket ouvre une connexion vers
l'imprimante (jusqu'à 4 s si elle est éteinte). Avec plusieurs fils par
worker, une imprimante qui ne répond pas ne bloque pas les autres postes
(écran TV, guichets) pendant ce temps.
"""

import multiprocessing
import os

bind = os.getenv("GUNICORN_BIND", "127.0.0.1:8000")   # Nginx est devant
worker_class = "gthread"
workers = int(os.getenv("GUNICORN_WORKERS", str(min(4, multiprocessing.cpu_count() * 2 + 1))))
threads = int(os.getenv("GUNICORN_THREADS", "8"))
timeout = 30
graceful_timeout = 30
keepalive = 5
# Redémarre régulièrement les workers (fuites mémoire éventuelles).
max_requests = 2000
max_requests_jitter = 200
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "warning")
