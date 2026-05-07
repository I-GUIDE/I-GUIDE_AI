# Notebook Validator

Flask microservice that validates I-GUIDE platform notebooks by executing them via [Papermill](https://papermill.readthedocs.io/) in isolated Docker containers, using the same CVMFS conda environments available on the platform.

Runs on port **5003**.

---

## How it works

```
Node.js backend  ──POST /validate/notebook──▶  validator_api.py
                                                      │
                                               git clone repo
                                                      │
                                          match kernel → CVMFS env
                                                      │
                                         docker run notebook-validator
                                              papermill <notebook>
                                                      │
                                         ◀──PUT /api/notebooks/:id/validation
                                                      │
                                               Neo4j updated
```

1. The Node.js backend fires a non-blocking `POST /validate/notebook` after a notebook is registered.
2. The validator clones the notebook's GitHub repo into a temp directory.
3. It reads the notebook's kernel metadata and matches it to a CVMFS conda environment from `cvmfs_environments.json`.
4. It runs Papermill inside the `notebook-validator` Docker container with the CVMFS env bind-mounted.
5. On completion it calls back `PUT /api/notebooks/:id/validation` on the backend, which writes the result to Neo4j.
6. The temp directory is cleaned up regardless of outcome.

A weekly cron job re-validates all platform notebooks every Sunday at 2 AM by running `python validator.py --download`.

---

## Validation statuses

| Status | Meaning |
|--------|---------|
| `PASS` | Notebook executed without errors |
| `FAIL` | Notebook raised an error during execution |
| `TIMEOUT` | Notebook or a cell exceeded the timeout |
| `NO_ENV` | No matching CVMFS environment found for the notebook's kernel |
| `ENV_UNAVAILABLE` | CVMFS environment matched but repo is not mounted on the host |
| `ERROR` | Unexpected error (e.g. git clone failed, file not found) |

---

## Files

| File | Purpose |
|------|---------|
| `validator_api.py` | Flask API wrapper — copied into the cloned notebook-validation repo at container build time |
| `Dockerfile.validator` | Builds the API service container; clones [I-GUIDE/notebook-validation](https://github.com/I-GUIDE/notebook-validation) |
| `Dockerfile.runner` | Builds the `notebook-validator` Papermill execution image that validator.py launches via `docker run` |
| `crontab` | Weekly re-validation cron schedule |

---

## Environment variables

Copy `.env.example` to `.env` in the repo root and fill in values:

| Variable | Description | Default |
|----------|-------------|---------|
| `BACKEND_URL` | URL of the Node.js backend | `http://localhost:3501` |
| `AUTH_API_KEY` | Auth header name | `x-auth-key` |
| `AUTH_API_KEY_VALUE` | Auth header secret — must match the backend's value | `dev-auth-key` |
| `PORT` | Port for the validator API | `5003` |

In production, set `BACKEND_URL` to the backend's Docker internal hostname, e.g. `http://backend-server:3501`.

---

## Running locally (WSL2)

### Prerequisites
- Docker Desktop with WSL2 integration enabled
- CVMFS mounted (see below)
- Node.js backend running

### 1. Set up CVMFS (one-time)

Add the public keys to the `.env` in the [notebook-validation](https://github.com/I-GUIDE/notebook-validation) repo, then:

```bash
sudo bash setup_cvmfs_client.sh
```

After each WSL2 restart:
```bash
sudo pkill -x automount || true
sudo pkill cvmfs2 || true
sudo cvmfs_config wsl2_start
```

Verify:
```bash
ls /cvmfs/cybergis.illinois.edu
ls /cvmfs/iguide.purdue.edu
```

### 2. Build the Papermill execution image

```bash
cd /path/to/notebook-validation
docker build -t notebook-validator .
```

### 3. Start the validator API

```bash
cd /path/to/notebook-validation
pip install flask requests psutil
python validator_api.py
```

### 4. Trigger a test validation

```bash
curl -X POST http://localhost:5003/validate/notebook \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/alexandermichels/SpatialDataScience",
    "notebook_path": "WorkingWithData/WorkingWithData.ipynb",
    "notebook_id": "<neo4j-notebook-id>"
  }'
```

Poll for the result:
```bash
curl http://localhost:5003/jobs/<job_id>
```

---

## Running via Docker Compose

```bash
# Build all images including the runner
docker compose build

# Start the validator service
docker compose up notebook-validator
```

Requires `/cvmfs` to be mounted on the host and a populated `.env` file.
