#!/usr/bin/env python3
"""Mock Alertmanager API server for local development."""

import sys

from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/api/v2/alerts", methods=["GET"])
def alerts():
    """Return empty alerts list."""
    return jsonify([])


@app.route("/healthz")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("Mock Alertmanager starting on http://0.0.0.0:19093", file=sys.stderr)
    app.run(host="0.0.0.0", port=19093, debug=False)
