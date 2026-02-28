#!/bin/bash
set -a
source .env
set +a
poetry run python main.py --serve-webhook --host 0.0.0.0 --port 8080
