#!/usr/bin/env python3
"""Mock VictoriaLogs API server for local development."""

import sys

from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/select/logsql/query", methods=["GET", "POST"])
def query():
    """Return empty log results."""
    return jsonify({"status": "success", "data": {"result": []}})


@app.route("/healthz")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("Mock VictoriaLogs starting on http://0.0.0.0:19471", file=sys.stderr)
    app.run(host="0.0.0.0", port=19471, debug=False)
