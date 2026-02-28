#!/bin/bash
set -a
source .env
set +a
poetry run python main.py --run-worker
