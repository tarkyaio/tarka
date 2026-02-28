#!/usr/bin/env python3
"""Mock Prometheus API server for local development."""

import sys

from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/api/v1/query", methods=["GET", "POST"])
@app.route("/api/v1/query_range", methods=["GET", "POST"])
def query():
    """Return empty query results."""
    return jsonify({"status": "success", "data": {"resultType": "vector", "result": []}})


@app.route("/api/v1/series", methods=["GET", "POST"])
def series():
    """Return empty series."""
    return jsonify({"status": "success", "data": []})


@app.route("/api/v1/labels", methods=["GET"])
@app.route("/api/v1/label/<label_name>/values", methods=["GET"])
def labels(label_name=None):
    """Return empty label values."""
    return jsonify({"status": "success", "data": []})


@app.route("/healthz")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    print("Mock Prometheus starting on http://0.0.0.0:18481", file=sys.stderr)
    app.run(host="0.0.0.0", port=18481, debug=False)
