"""Single ingestion entrance for ALL extractors.

The platform form assigns a knowledge-element id and POSTs a submission here; the
submission triggers the ingestion pipeline (routes by element_type to the right
extractor). Extracted contents land in agent-only OpenSearch indices.

Routes:
  POST /ingest   — form submission: {element_id, element_type, source{...}, fields{...}, targets[]}
  POST /webhook  — legacy MinIO/S3 PUT event → mapped to a dataset/publication submission
  GET  /health
"""

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

# Make the repo-root `extractors` package importable from this standalone service.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

app = Flask(__name__)


def _run(submission):
    from extractors.ingest import ingest_submission
    try:
        manifest = ingest_submission(submission)
    except Exception as exc:  # clone/IO/etc. — stub extractors are swallowed as warnings, not raised
        return jsonify({"status": "error",
                        "element_id": submission.element_id,
                        "element_type": submission.element_type,
                        "detail": f"{type(exc).__name__}: {exc}"}), 500
    d = manifest.to_dict()
    produced = len(d.get("assets", []))
    # "no_assets" makes a stubbed/empty extraction honest (vs. a misleading "ingested")
    status = "ingested" if produced else "no_assets"
    return jsonify({"status": status,
                    "element_id": submission.element_id,
                    "element_type": submission.element_type,
                    "assets": produced,
                    "warnings": d.get("warnings", []),
                    "summary": d}), 200


@app.route("/ingest", methods=["POST"])
def ingest():
    from extractors.submission import Submission
    try:
        submission = Submission.from_payload(request.json or {})
        submission.validate()
    except ValueError as exc:
        return jsonify({"status": "invalid", "detail": str(exc)}), 400
    return _run(submission)


@app.route("/webhook", methods=["POST"])
def webhook():
    """Legacy MinIO/S3 event → dataset/publication submission (code/notebook arrive
    via /ingest with a GitHub URL)."""
    from extractors.fileclass import classify_upload
    from extractors.submission import Submission

    event = request.json or {}
    try:
        record = event["Records"][0]["s3"]
        bucket = record["bucket"]["name"]
        key = record["object"]["key"]
    except (KeyError, IndexError, TypeError):
        return jsonify({"error": "unrecognized event shape"}), 400

    etype = classify_upload(key)
    if etype == "unknown":
        return jsonify({"status": "skipped", "reason": "unrecognized file type", "key": key}), 200

    submission = Submission(element_id=key, element_type=etype, bucket=bucket, key=key, file_path=key)
    return _run(submission)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")))
