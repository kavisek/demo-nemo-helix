# demo-runai-nemo-helix

Local NeMo Helix deployment using Docker Compose.

## Quick Start

```bash
make start   # start NeMo Helix, then Airflow and Grafana (dev), and print every port and login
make status  # Helix container status plus the dataset and provider summary
make logs    # follow Helix logs
make stop    # stop Grafana, Airflow, and NeMo Helix (data volumes are kept)
```

`make start` returns only after the Helix bootstrap containers finish, Airflow is healthy, and Grafana's migrations have run. It ends with a summary of the ports, the logins, and what is in Helix:

```text
Datasets (workspace: default):
  • sample-qa-dataset: training/train.jsonl (200 rows), validation/validation.jsonl (50 rows)
  • single-turn-evals: evals.jsonl (50 rows)
  • multi-turn-evals:  evals.jsonl (50 rows)

Model Provider:
  • ollama: READY -> gemma4:e4b-mlx
```

If a bootstrap container fails, or a dataset or the provider is missing, `make start` exits non-zero and prints the failing container's last log lines.

## Architecture

```mermaid
flowchart LR
    user([Browser / CLI])

    subgraph airflow[airflow-ENV compose project]
        dag[single-turn and multi-turn<br/>eval DAGs, UI :7002]
        privacy[nemo_helix_privacy_pipeline]
        syncs[sync-members<br/>sync-secrets]
        tracing[helix_tracing<br/>task + DAG callbacks]
        airflowdb[(Airflow Postgres<br/>:7432)]
    end

    subgraph helix[nemo-helix compose project]
        chat[make chat<br/>app.traced_chat]
        studio[NeMo Studio<br/>:7001/studio]
        evaluator[Evaluator jobs<br/>/apis/evaluator]
        designer[Data Designer jobs<br/>/apis/data-designer]
        anonymizer[Anonymizer jobs<br/>/apis/anonymizer]
        nss[Safe Synthesizer jobs<br/>/apis/safe-synthesizer<br/>GPU only]
        files[(Filesets<br/>privacy-pipeline)]
        access[Members + Secrets<br/>/apis/entities, /apis/secrets]
        gateway[Inference gateway<br/>/apis/inference-gateway]
        intake[Intake<br/>/apis/intake]
        clickhouse[(ClickHouse<br/>spans, traces<br/>:7123)]
        helixdb[(Helix Postgres<br/>platform records<br/>:7450)]
        helixpgadmin[pgAdmin :7006]
    end

    subgraph host[Docker host]
        ollama[Ollama<br/>gemma4:e4b-mlx<br/>:11434]
    end

    subgraph grafana[grafana-ENV compose project]
        grafanaui[Grafana :7003]
        pgadmin[pgAdmin :7004]
        grafanadb[(Grafana Postgres<br/>:7440)]
    end

    user --> dag
    user --> privacy
    user --> chat
    user --> studio
    user --> grafanaui

    dag -->|create experiment,<br/>submit eval job| evaluator
    dag --- tracing
    privacy --- tracing
    syncs -->|members.yaml,<br/>secrets.yaml| access
    privacy -->|1. generate notes| designer
    privacy -->|2. redact PII| anonymizer
    privacy -.->|3. optional| nss
    privacy -->|generated, redacted,<br/>synthetic tables| files
    designer --> gateway
    anonymizer --> gateway
    dag --> airflowdb
    evaluator -->|one call per row| gateway
    chat -->|chat completion| gateway
    gateway --> ollama

    tracing -->|run trace:<br/>root + task spans| intake
    evaluator -->|trial traces + scores| intake
    chat -->|LLM span| intake
    studio -->|Traces, Experiments| intake
    intake --> clickhouse
    access --> helixdb
    helixpgadmin --> helixdb
    user --> helixpgadmin

    grafanaui -->|pipeline dashboard| airflowdb
    grafanaui --> grafanadb
    pgadmin --> grafanadb
```

Helix stores traces in ClickHouse through Intake. Three producers send them: evaluator jobs (one trace per dataset row, with its scores), every Airflow DAG (one trace per run), and `make chat` (one span per call). See [Traces](#traces).

## Services and Ports

Every service publishes on a 7000-series host port. Inside the containers the usual ports still apply: `make nemo-shell` reaches Helix at `localhost:8080`, and other containers reach it at `host.docker.internal:7001`.

| Service                    | dev                                     | staging          | prod             |
| -------------------------- | --------------------------------------- | ---------------- | ---------------- |
| NeMo Helix API             | <http://localhost:7001>                 | shared           | shared           |
| NeMo Helix Studio          | <http://localhost:7001/studio/>         | shared           | shared           |
| ClickHouse (HTTP)          | <http://localhost:7123>                 | shared           | shared           |
| Helix Postgres             | `localhost:7450`                        | shared           | shared           |
| Helix pgAdmin              | <http://localhost:7006>                 | shared           | shared           |
| Airflow UI                 | <http://localhost:7002>                 | `localhost:7012` | `localhost:7022` |
| Airflow Postgres           | `localhost:7432`                        | `localhost:7433` | `localhost:7434` |
| Airflow Flower (`flower` profile) | `localhost:7005`                 | `localhost:7015` | `localhost:7025` |
| Grafana                    | <http://localhost:7003>                 | `localhost:7013` | `localhost:7023` |
| pgAdmin                    | <http://localhost:7004>                 | `localhost:7014` | `localhost:7024` |
| Grafana Postgres           | `localhost:7440`                        | `localhost:7441` | `localhost:7442` |

Health check: `curl http://localhost:7001/health/ready`

## Bootstrap Containers

Two one-shot containers run after `nemo-helix` is healthy. Each waits for three consecutive healthy `/status` checks, does its job and exits with code 0. Both are idempotent, so they are safe to rerun on every `make start`. Datasets that already exist are reused and their files overwritten.

| Container        | What it does                                                                                                                                                                                                             |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `data-generator` | Generates synthetic datasets and syncs them into Helix: `default/sample-qa-dataset` (chat Q&A train/validation splits), `default/single-turn-evals` and `default/multi-turn-evals` (see [Eval Datasets](#eval-datasets)) |
| `model-setup`    | Registers the host's Ollama as the `default/ollama` model provider with `gemma4:e4b-mlx` enabled                                                                                                                         |

Check what they did with `make status`, `make logs` or `docker compose logs data-generator model-setup` (prefixed with `NEMO_DATA_DIR=$PWD/.nemo-data`).

### Healthchecks

| Service          | Healthcheck                              | Healthy when                    |
| ---------------- | ---------------------------------------- | ------------------------------- |
| `clickhouse`     | `wget http://localhost:8123/ping`        | ClickHouse answers `Ok.`        |
| `nemo-helix`     | `GET http://localhost:8080/health/ready` | Helix reports ready             |
| `data-generator` | `/tmp/healthy` exists                    | every dataset has been synced   |
| `model-setup`    | `/tmp/healthy` exists                    | the Ollama provider is upserted |

While a bootstrap container runs, `docker compose ps` shows it as `health: starting`. When it finishes it writes `/tmp/healthy`, which turns it `healthy`, and then exits. Docker reports any stopped container's health as `unhealthy`, so check the exit code (`Exited (0)` in `make ps`) once it has finished. `make start` waits for `clickhouse` and `nemo-helix` with `docker compose up --wait`, then waits for the bootstrap containers with `docker compose wait`, because `--wait` treats any container exit, even code 0, as a failure.

### Eval Datasets

The `data-generator` container also loads two seeded eval datasets, each with 50 rows in `evals.jsonl`. Every row has a short `reference` answer, so exact-match or contains-style metrics work well.

| Fileset             | Row shape                                                                                                                                                       | Categories                                         |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `single-turn-evals` | `{"id", "category", "input", "reference"}`: one question per row                                                                                                | `arithmetic`, `capital`                            |
| `multi-turn-evals`  | `{"id", "category", "messages", "reference"}`: `messages` is user, assistant, user, and `reference` answers the final user turn, which depends on earlier turns | `arithmetic-followup`, `memory`, `context-capital` |

```json
{"id": "st-0000", "category": "capital", "input": "What is the capital of France? Reply with just the city.", "reference": "Paris"}
{"id": "mt-0000", "category": "memory", "messages": [{"role": "user", "content": "Remember the number 301."}, {"role": "assistant", "content": "Got it, I will remember 301."}, {"role": "user", "content": "What is double that number? Reply with just the number."}], "reference": "602"}
```

Override the names or the row count with `SINGLE_TURN_DATASET`, `MULTI_TURN_DATASET` and `EVAL_ROWS` on the `data-generator` service. Inspect them with `nemo files list single-turn-evals` or download them with `nemo files download multi-turn-evals -o /tmp/mt/`.

## Ollama Model

Ollama must be running on the host (port 11434) with the model pulled:

```bash
ollama pull gemma4:e4b-mlx
```

Helix exposes it through its OpenAI-compatible inference gateway as `default/gemma4-e4b-mlx`:

```bash
curl http://localhost:7001/apis/inference-gateway/v2/workspaces/default/openai/-/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "default/gemma4-e4b-mlx", "messages": [{"role": "user", "content": "Hello"}]}'
```

To use a different Ollama URL or other models, set `OLLAMA_URL` (default `http://host.docker.internal:11434/v1`) and `OLLAMA_MODELS` (comma-separated, default `gemma4:e4b-mlx`) before running `make start`.

## NeMo CLI

Run the `nemo` CLI inside the `nemo-helix` container. The container has every plugin installed (Data Designer, Anonymizer, Evaluator), and `./examples` is mounted read-only at `/examples`. The Makefile defines the CLI command (`NEMO_CLI`) and `NEMO_DATA_DIR`, so you can use them three ways:

```bash
# 1. Load the `nemo` alias and NEMO_DATA_DIR into your current shell (works from any directory)
eval "$(make -s alias)"
nemo --help

# 2. One-off commands through make
make nemo ARGS="models list"

# 3. Shell inside the container, with `nemo` on the PATH
make nemo-shell
```

The alias takes precedence over any `nemo` installed on your host, such as `~/.local/bin/nemo`. To have the alias in every new terminal, add `eval "$(make -s -C /path/to/demo-runai-nemo-helix alias)"` to `~/.zshrc`. The examples below assume the alias is loaded.

Every command defaults to the `default` workspace. Add `-f json` for JSON output.

### Chat with Models

```bash
nemo models list                                         # models Helix can route to
nemo chat -m gemma4-e4b-mlx "What is the capital of France?"
nemo chat -m gemma4-e4b-mlx --interactive                # interactive chat UI
nemo chat --provider ollama -m gemma4:e4b-mlx "Hello"    # bypass model routing, call the provider directly
nemo inference providers list
```

### Populate Datasets

Upload your own data into a fileset:

```bash
nemo files filesets create my-dataset --purpose dataset --exist-ok
nemo files upload /examples/customers.csv my-dataset     # a file or a whole directory
nemo files list my-dataset
nemo files download my-dataset -o /tmp/my-dataset/
```

Or generate synthetic data with Data Designer. `examples/support_tickets.py` generates support tickets with the local Ollama model:

```bash
nemo data-designer validate /examples/support_tickets.py       # config and provider check, no inference
nemo data-designer check-models /examples/support_tickets.py   # one test call per model
nemo data-designer preview /examples/support_tickets.py        # a few rows inline
nemo data-designer create /examples/support_tickets.py -n 50   # submits a job and prints its name
```

The generated dataset lands in the job's results (see [Submit and Track Jobs](#submit-and-track-jobs)).

### Submit and Track Jobs

`data-designer create`, `anonymizer run` and `evaluator evaluate` all submit platform jobs. Manage them with `nemo jobs`:

```bash
nemo jobs list
nemo jobs get-status <job-name>
nemo jobs get-logs <job-name>
nemo jobs results list <job-name>
nemo jobs results download artifacts --job <job-name> --output-file /tmp/artifacts.tar.gz
nemo jobs cancel <job-name>
```

Downloaded files stay inside the container. Copy them out with `docker compose cp nemo-helix:/tmp/artifacts.tar.gz .` (prefixed with `NEMO_DATA_DIR=$PWD/.nemo-data`).

> `nemo jobs watch` and `nemo jobs tail` fail in nemo-helix 0.7.3 (`'NemoClient' object has no attribute '_get_workspace_path_param'`). Poll with `nemo jobs get-status` instead.

### Run Experiments

An experiment is a leaderboard of evaluations. Each evaluation is one scored run. `examples/eval-qa.yaml` asks `default/gemma4-e4b-mlx` 20 questions from `sample-qa-dataset` and scores the answers with exact-match and F1.

```bash
# 1. Create the experiment and an evaluation inside it
nemo experiments create qa-baseline --description "gemma4 on sample QA" --exist-ok
EXP_ID=$(nemo experiments get qa-baseline -f json | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -X POST http://localhost:7001/apis/intake/v2/workspaces/default/evaluations \
  -H 'Content-Type: application/json' \
  -d '{"name": "gemma4-run1", "experiment_ids": ["'"$EXP_ID"'"], "dataset_name": "sample-qa-dataset", "dataset_version": "v1"}'

# 2. Run the evaluation job and publish its scores to the evaluation
nemo evaluator evaluate --spec-file /examples/eval-qa.yaml \
  --publication.intake.evaluation-id gemma4-run1 \
  --publication.intake.agent-name gemma4-e4b-mlx

# 3. Once the job completes, read the scores
nemo experiments list
curl http://localhost:7001/apis/intake/v2/workspaces/default/evaluations/gemma4-run1
```

For a full walkthrough with example outputs, see [docs/datasets-and-experiments.md](docs/datasets-and-experiments.md).

To compare a change, create another evaluation in the same experiment (for example `gemma4-run2`), edit `target`, `prompt_template` or `params` in the spec, and rerun. Do not hand-edit the `metrics` block: each metric carries a digest of its payload.

### Anonymize Data

The Anonymizer detects PII with a GLiNER span-detection model, then uses LLMs to validate and replace it. Ollama has no GLiNER model, so detection uses NVIDIA Build's hosted `nvidia/gliner-pii`, and `examples/anonymizer-run.yaml` sends every LLM role to Ollama. First register NVIDIA Build once, with an API key from <https://build.nvidia.com/settings/api-keys>:

```bash
echo "$NVIDIA_API_KEY" | nemo secrets create nvidia-api-key --from-file -
nemo inference providers create nvidia-build \
  --host-url https://integrate.api.nvidia.com/v1 \
  --api-key-secret-name nvidia-api-key
```

Then upload the input and run the job. `examples/customers.csv` is synthetic: the names, emails, and phone numbers are made up.

```bash
nemo files filesets create anonymizer-inputs --purpose dataset --exist-ok
nemo files upload /examples/customers.csv anonymizer-inputs
nemo anonymizer run --spec-file /examples/anonymizer-run.yaml
nemo jobs get-status <job-name>
nemo jobs results download artifacts --job <job-name> --output-file /tmp/anonymized.tar.gz
```

The artifacts include `dataset.parquet` (the redacted text), `trace.parquet` (detected entities) and `failed_records.json`, which is only present if some records failed. Use `nemo anonymizer preview --spec-file ...` to try a config on a few rows without starting a job.

## Airflow

`airflow/` runs Apache Airflow 3 (CeleryExecutor) with Docker Compose, using the reference setup from the `airflow` skill. Two eval DAGs automate [Run Experiments](#run-experiments): each checks the datasets, creates the experiment and a new evaluation, submits the evaluator job, waits for it, and logs the job output and scores. Each task calls the Helix REST API behind the matching `nemo` command, through `host.docker.internal:7001`. `make start` starts Airflow along with Helix and Grafana; use the `airflow-*` targets to manage it on its own.

| DAG                                    | Spec                   | Dataset                                   | What the model sees                                              |
| -------------------------------------- | ---------------------- | ----------------------------------------- | ---------------------------------------------------------------- |
| `nemo_helix_single_turn_eval_pipeline` | `eval-qa.yaml`         | `sample-qa-dataset` (validation split)    | one question per row, with a system prompt                       |
| `nemo_helix_multi_turn_eval_pipeline`  | `eval-multi-turn.yaml` | `multi-turn-evals`                        | the row's whole conversation (user, assistant, user); it answers the last turn |

Both score the reply against the reference with exact-match and F1, and both are built by `airflow/dags/helix_eval.py`, so they run the same tasks. The multi-turn spec sends the history with `prompt_template: {messages: "{{ item.messages }}"}`: a bare expression keeps `messages` a list, so the gateway receives the full conversation. Default evaluation names include `single-turn` or `multi-turn`, so the two never collide.

Every evaluator job is named after the DAG run that submitted it, so it is easy to find on Studio's **Models > Evaluations** page: run `manual__2026-10-22T09:04:10.551009+00:00` of the single-turn DAG submits job `single-turn-manual__2026-10-22t09-04-10.551009`. Helix's log store and job filesets limit names to lowercase letters, digits and `._-`, at most 51 characters, so the run ID is lowercased, its `+00:00` offset is dropped, and `:` becomes `-`. A resubmit after clearing the task adds `-try<n>`. The exact run ID is also stored on the job (`description` and `custom_fields.airflow_run_id`) and on the evaluation (`metadata.airflow_run_id`), and the evaluation's `source_link` opens the run in the Airflow UI (`AIRFLOW_UI_URL`, from `AIRFLOW_PORT`).

```bash
make airflow-start                                     # build the image, start dev, print ports and login (creates envs/dev.env)
make airflow-trigger                                   # unpause and trigger the single-turn eval DAG
make airflow-trigger AIRFLOW_DAG=nemo_helix_multi_turn_eval_pipeline
make airflow-trigger CONF='{"evaluation_name": "gemma4-run2"}'
make airflow-status | airflow-logs | airflow-shell
make airflow-stop                                      # keeps the Airflow DB volume
```

| Environment | Airflow UI              | Postgres         | Login                             |
| ----------- | ----------------------- | ---------------- | --------------------------------- |
| `dev`       | <http://localhost:7002> | `localhost:7432` | `airflow` / `airflow`             |
| `staging`   | <http://localhost:7012> | `localhost:7433` | from `airflow/envs/staging.env`   |
| `prod`      | <http://localhost:7022> | `localhost:7434` | from `airflow/envs/prod.env`      |

Pick the environment with `ENV=staging` or `ENV=prod`, and promote an image tag that passed dev with `TAG=<sha>`. Dev bind-mounts `airflow/dags/` and `examples/`, so DAG edits show up within 30 seconds. Staging and prod run what is baked into the image.

Eval DAG params (set them in the trigger form or with `CONF`). Defaults that differ are shown as single-turn / multi-turn:

| Param                    | Default                                    | Purpose                                          |
| ------------------------ | ------------------------------------------ | ------------------------------------------------ |
| `experiment_name`        | the DAG ID                                 | experiment to create or reuse                    |
| `evaluation_name`        | blank: `<agent_name>-<single-turn or multi-turn>-<time>` | evaluation to create or reuse      |
| `spec_file`              | `eval-qa.yaml` / `eval-multi-turn.yaml`    | evaluator spec in `examples/`                    |
| `agent_name`             | `gemma4-e4b-mlx`                           | agent name the scores are published under        |
| `dataset_version`        | `v1`                                       | dataset version recorded on the evaluation       |
| `filesets`               | `sample-qa-dataset`, `single-turn-evals` / `multi-turn-evals` | filesets that must exist; their files are listed |

## Privacy Pipeline

The third DAG, `nemo_helix_privacy_pipeline`, chains three Helix services on fake customer data:

1. **Data Designer** generates customer support notes from `examples/customer_notes.py`. The prompt asks the model to mention a Faker customer's name, phone number, email, and city in each note, alongside `product`, `plan`, `sentiment`, `age`, and `state` columns.
2. **Anonymizer** finds the PII in the `note` column and redacts it (`[REDACTED_FIRST_NAME]`, `[REDACTED_PHONE_NUMBER]`, ...). Every detection role runs on the local Ollama model (`examples/privacy-anonymizer.yaml`), so no NVIDIA Build key is needed. The Anonymizer only returns the text columns, so the DAG joins the redacted notes back onto the generated rows.
3. **Safe Synthesizer** trains on the redacted table and generates new, privacy-safe records (`examples/privacy-safe-synthesizer.yaml`). It only runs when `run_safe_synthesizer` is `true`.

```bash
make airflow-trigger AIRFLOW_DAG=nemo_helix_privacy_pipeline
make airflow-trigger AIRFLOW_DAG=nemo_helix_privacy_pipeline CONF='{"num_records": 50}'
make privacy-spec          # re-render examples/privacy-data-designer.yaml after editing customer_notes.py
nemo files list privacy-pipeline
```

| Param                  | Default | Purpose                                                                     |
| ---------------------- | ------- | --------------------------------------------------------------------------- |
| `num_records`          | `20`    | notes to generate (1-500); the Anonymizer takes about 15 seconds per note   |
| `run_safe_synthesizer` | `false` | train Safe Synthesizer on the redacted table; needs a CUDA GPU              |
| `synthetic_records`    | `100`   | records Safe Synthesizer generates                                          |

Each run writes to its own folder in the `privacy-pipeline` fileset: `generated.parquet` (the notes with PII), `customer_notes_redacted.csv` (the redacted table), and `synthetic.csv` when Safe Synthesizer runs. The Airflow task logs show sample notes before and after redaction and the count of each redacted entity type.

The Airflow image has no `data_designer` library, so the DAG submits the pre-rendered spec in `examples/privacy-data-designer.yaml`. Regenerate it with `make privacy-spec` whenever `customer_notes.py` changes.

> **Safe Synthesizer needs an NVIDIA GPU.** Every Safe Synthesizer job runs in a GPU container that checks for CUDA before it starts. On Docker Desktop for Mac, Helix accepts the job, then fails it with `No GPUs available on this system`, so `run_safe_synthesizer` is off by default. A fake GPU operator on kind gets the pod scheduled but still fails the CUDA check. Turn it on when Helix runs on a cluster with real GPUs, such as Run:ai.

## Workspace Sync

Two small DAGs keep NeMo Studio's **Settings > Members** and **Secrets** pages in line with files in `examples/`. Each run adds what is missing and fixes what differs. A second run with no file changes does nothing. Anything in Studio that the file does not list is kept unless you trigger with `prune: true`.

| DAG            | Source file                | Studio page | What it syncs                                             |
| -------------- | -------------------------- | ----------- | --------------------------------------------------------- |
| `sync-members` | `examples/members.yaml`    | Members     | each workspace's members (`principal`) and roles (`Viewer`, `Editor`, `Admin`) |
| `sync-secrets` | `examples/secrets.yaml`    | Secrets     | each workspace's secrets: name and description from the file, value from an Airflow Variable |

```bash
make airflow-trigger AIRFLOW_DAG=sync-members
make airflow-secret VAR=helix_secret_nvidia_api_key   # prompts for the value, stores it as an Airflow Variable
make airflow-trigger AIRFLOW_DAG=sync-secrets
make airflow-trigger AIRFLOW_DAG=sync-secrets CONF='{"prune": true}'   # also delete secrets the file doesn't list
```

Secret values never go in the repo. `secrets.yaml` names the Airflow Variable that holds each value (`helix_secret_<name>`). The DAG reads, compares, and writes values inside one task and returns only secret names, so values stay out of XCom. Airflow masks Variables whose key contains `secret` in task logs. Secrets whose Variable is unset are skipped with a warning; trigger with `require_all: true` to fail instead. Dev's `FERNET_KEY` is blank, so Variables are stored unencrypted in the dev Airflow database. Set `FERNET_KEY` in `airflow/envs/<env>.env` for anything real.

The members in `members.yaml` are `example.com` placeholders. Replace them with real accounts once Helix runs with authentication (`NHX_AUTH_ENABLED=true`).

## Grafana

`grafana/` runs Grafana with its own Postgres, Flyway migrations (`database/migrations/`), and pgAdmin, using the reference setup from the `grafana` skill. Dashboards are code: edit `grafana/dashboards-as-code/dashboards/*.py`, run `make dashboards`, and commit the Python with the generated `grafana/dashboards/*.json`. Never edit the JSON or save from the UI.

| Dashboard                  | Data source                             | Shows                                                                         |
| -------------------------- | --------------------------------------- | ----------------------------------------------------------------------------- |
| NeMo Helix Single-Turn Eval Pipeline | `Airflow` (Airflow metadata DB) | the single-turn DAG's run counts, latest and per-run exact-match/F1, recent runs, task durations |
| NeMo Helix Multi-Turn Eval Pipeline  | `Airflow` (Airflow metadata DB) | the same panels for the multi-turn DAG                                        |
| PostgreSQL Monitoring      | `PostgreSQL` (Grafana's Postgres)       | connections, locks, slow queries, storage                                     |

The eval pipeline dashboards are built by `dashboards/eval_pipeline.py` and read the scores each DAG stores as XComs (`get_scores` and `wait_for_job`), from the `airflow-<env>` Postgres on its published port. Each Grafana environment reads the matching Airflow environment (`AIRFLOW_DB_*` in `grafana/envs/<env>.env`).

```bash
make grafana-start           # dashboards -> build images -> up -> migrate -> star, then print ports and login
make dashboards              # render the Python dashboards; dev Grafana picks them up within 10s
make dashboards-check        # fail if the committed JSON is stale
make grafana-status | grafana-logs | grafana-shell   # shell = psql in Grafana's Postgres
make grafana-stop            # keeps Grafana's DB and Postgres volumes
```

Logins for dev are `admin` / `admin` for Grafana and `pgadmin4@pgadmin.org` / `admin` for pgAdmin (database password `postgres`). Staging and prod read them from `grafana/envs/<env>.env`. Schema changes go in a new `database/migrations/V<x.y.z>__<what>.sql`; never edit an applied `V` file.

## Traces

The **Traces** page in NeMo Studio (<http://localhost:7001/studio/workspaces/default/intake/traces>) shows spans stored by Helix's Intake service in ClickHouse. A span is one timed step, a trace is one run made of spans, and a session groups related traces. Nothing is traced automatically by Ollama or Airflow. This repo sends three kinds of traces:

| Producer                    | Agent column               | Trace contents                                                                                                   |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Evaluator job               | the DAG's `agent_name`     | one trace per dataset row: the model call (input, output, tokens) and its `exact-match` and `f1` scores, linked to the evaluation |
| the eval and privacy DAGs | the DAG ID | one trace per DAG run: a root span with the run's params and state, and a span per task attempt with its return value or error |
| `make chat`                 | `nemo-helix-chat`          | one LLM span per call: messages, reply, model, tokens and latency                                                |

The DAG traces come from `airflow/dags/helix_tracing.py`. Its task and DAG callbacks post spans to Intake's `ingest/spans` endpoint, so the tasks have no tracing code, and a failed post only logs a warning. Failed and retried attempts appear as error spans. A DAG run's trace and session ID is `<dag_id>/<run_id>`.

```bash
make chat PROMPT="What is the capital of France?"            # new session per call
make chat PROMPT="And of Japan?" SESSION=geo-quiz             # group calls into one session
curl -g 'http://localhost:7001/apis/intake/v2/workspaces/default/spans?filter[agent_name]=nemo_helix_multi_turn_eval_pipeline'
```

`make chat` runs `app/app/traced_chat.py` inside the `nemo-helix` container, so rebuild with `make start` (or `make build`) after changing it. Calls made with `nemo chat` or straight to the gateway are not traced.

Intake keeps traces for 90 days, and they live in the `clickhouse-data` volume. Removing that volume deletes them. Intake stores traces only, not logs: Airflow task logs stay in `airflow/logs/` and the Airflow UI, and Ollama's logs stay on the host.

## Helix Database

Helix stores its platform records (workspaces, jobs, filesets, experiments and evaluations, models and providers, secrets, members) in the `helix-postgres` container. `DATABASE_URL` on the `nemo-helix` service points it there. Without that setting Helix falls back to SQLite in `NEMO_DATA_DIR`. Helix creates and migrates its tables on startup (`alembic_version` records the revision). Traces and scores stay in ClickHouse, and files stay in `NEMO_DATA_DIR`.

| Table                | Holds                                                                                          |
| -------------------- | ---------------------------------------------------------------------------------------------- |
| `entities`           | every platform record, one row per record: `entity_type` (for example `platform_job`, `fileset`, `experiment`, `role_binding`, `platform_secret`), `name`, `workspace`, `parent`, and the fields in a JSON `data` column |
| `workspaces`         | workspaces                                                                                     |
| `accounts`, `account_identities` | users; empty while authentication is off                                           |
| `alembic_version`    | schema migration revision                                                                      |

Browse it in pgAdmin at <http://localhost:7006>. The `nemo-helix` server is pre-registered; enter the database password (`helix`) the first time you open it. Or connect from the host with `psql postgresql://helix:helix@localhost:7450/helix`. Override the credentials and port with `HELIX_DB_USER`, `HELIX_DB_PASSWORD`, `HELIX_DB_NAME`, and `HELIX_DB_PORT` in `.env`.

```bash
docker compose exec helix-postgres psql -U helix -c "select entity_type, count(*) from entities group by 1 order by 2 desc"
```

Secret values in `entities` are encrypted with the key in `NEMO_DATA_DIR/nhx-encryption-key.txt`, so keep that file with any database backup.

Two fixes make Helix 0.7.3 run on Postgres:

- `psycopg[binary]` is in `app/pyproject.toml`. Helix runs its migrations with a plain `postgresql://` URL, which SQLAlchemy 2.1 maps to `psycopg` (v3), but Helix only installs `psycopg2`.
- `app/runtime/sitecustomize.py` (loaded through `PYTHONPATH=/app/runtime`) turns off connection pooling for the asyncpg engine. Helix shares one engine across event loops, so its workspace-cleanup controller reuses a pooled connection from another loop and asyncpg segfaults the server (exit code 139). Remove the file once a Helix release fixes this.

The SQLite database from before the switch is still in `.nemo-data/nhx-platform.db`; Helix no longer reads it.

## Default Credentials

| Service    | Username  | Password  |
| ---------- | --------- | --------- |
| NeMo Helix | none      | none      |
| ClickHouse | `default` | *(empty)* |
| Helix Postgres | `helix` | `helix` |
| Helix pgAdmin | `pgadmin4@pgadmin.org` | `admin` |
| Airflow    | `airflow` | `airflow` |
| Grafana    | `admin`   | `admin`   |
| pgAdmin    | `pgadmin4@pgadmin.org` | `admin` |

NeMo Helix runs with authentication disabled by default (`NHX_AUTH_ENABLED=false`), so the API and Studio need no login. You can confirm with `curl http://localhost:7001/apis/auth/discovery`, which returns `"auth_enabled": false`. To turn authentication on, set `NHX_AUTH_ENABLED=true` and `NHX_AUTH_ADMIN_EMAIL=<email>` in `.env`.

These defaults are for local development only. Do not expose these ports publicly.

## Configuration

- `.env` is loaded into the `nemo-helix` container (for example `NGC_API_KEY`). Copy `.env.example` to start; do not commit it.
- `NEMO_DATA_DIR` defaults to `./.nemo-data` and holds dataset files, job results, and job logs. It is set by the Makefile, so prefix any direct `docker compose` command with `NEMO_DATA_DIR=$PWD/.nemo-data`.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, tests, and the PR process, and [SECURITY.md](SECURITY.md) to report a vulnerability privately.

## License

[MIT](LICENSE)

## References

- https://www.piwheels.org/project/nemo-helix/
