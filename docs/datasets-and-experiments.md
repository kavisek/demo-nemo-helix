# Viewing Datasets and Running Experiments

A walkthrough of browsing the bootstrapped datasets in NeMo Helix and running one as an experiment. The outputs below were captured from a local run on 2026-10-19, so IDs and timestamps will differ on your machine.

## Prerequisites

- `make start` has finished and `make ps` shows `clickhouse` and `nemo-helix` as `healthy`, and `data-generator` and `model-setup` as `Exited (0)`
- Ollama is running on the host with `gemma4:e4b-mlx` pulled

Run the commands from one of these:

```bash
make nemo-shell          # shell inside the nemo-helix container (used for the outputs below)
eval "$(make -s alias)"  # or load the `nemo` alias into your host shell
```

The `curl` commands below use `localhost:8080`, which is Helix's port inside the container. From a host shell, use `localhost:7001` instead.

Paste only the command lines. Pasting output lines back into the shell fails with errors like `bash: files: command not found` or `bash: multi-turn-evals: command not found`.

## 1. View Datasets

Datasets are stored as filesets. List them:

```bash
nemo files filesets list -f table
```

```text
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ name              ┃ workspace ┃ created_at                 ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ multi-turn-evals  │ default   │ 2026-10-19T08:00:25.457673 │
│ single-turn-evals │ default   │ 2026-10-19T08:00:25.433731 │
│ sample-qa-dataset │ default   │ 2026-10-19T08:00:25.366534 │
└───────────────────┴───────────┴────────────────────────────┘


Warnings:
  • Use --no-truncate to see full values.
```

List the files in a fileset:

```bash
nemo files list single-turn-evals
```

```text
┏━━━━━━━━━━━━━┳━━━━━━┓
┃ PATH        ┃ SIZE ┃
┡━━━━━━━━━━━━━╇━━━━━━┩
│ evals.jsonl │ 6261 │
└─────────────┴──────┘


Warnings:
  • Use --no-truncate to see full values.
```

Add `-f json` to see each file's `file_url`:

```bash
nemo files list single-turn-evals -f json
```

```json
[
  {
    "file_ref": "default/single-turn-evals#evals.jsonl",
    "file_url": "/apis/files/v2/workspaces/default/filesets/single-turn-evals/-/evals.jsonl",
    "path": "evals.jsonl",
    "size": 6261,
    "cache_status": "not_cacheable"
  }
]
```

Read rows through the `file_url`:

```bash
curl -s http://localhost:8080/apis/files/v2/workspaces/default/filesets/single-turn-evals/-/evals.jsonl | head -3
```

```json
{"id": "st-0000", "category": "capital", "input": "What is the capital of France? Reply with just the city.", "reference": "Paris"}
{"id": "st-0001", "category": "capital", "input": "What is the capital of Kenya? Reply with just the city.", "reference": "Nairobi"}
{"id": "st-0002", "category": "capital", "input": "What is the capital of Japan? Reply with just the city.", "reference": "Tokyo"}
```

Other ways to view datasets:

| Method            | Command                                                 |
| ----------------- | ------------------------------------------------------- |
| Download a copy   | `nemo files download multi-turn-evals -o /tmp/mt/`      |
| Row-count summary | `make status` (from the host)                           |
| Browser           | NeMo Helix Studio at <http://localhost:7001/studio/>    |

## 2. Create the Experiment

An experiment is a leaderboard of evaluations, and each evaluation is one scored run. Create the experiment and keep its ID:

```bash
nemo experiments create qa-baseline --description "gemma4 on sample QA" --exist-ok
EXP_ID=$(nemo experiments get qa-baseline -f json | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
```

```json
{
  "id": "experiment-group-NqFFSJdK2vHoD7qAmCwYKD",
  "name": "qa-baseline",
  "workspace": "default",
  "description": "gemma4 on sample QA",
  "insight_id": null,
  "summary": null,
  "metadata": null,
  "default_sort": "-created_at",
  "pareto": {
    "x_metric": "cost_usd",
    "y_metric": "latency_ms"
  },
  "column_layout": null,
  "is_favorite": false,
  "show_evaluations_over_time": false,
  "baseline_evaluation_name": null,
  "created_at": "2026-10-19T08:55:46.682662",
  "updated_at": "2026-10-19T08:55:46.682665",
  "evaluation_count": 0
}
```

`evaluation_count` is `0` until you add an evaluation in the next step. Running the command again after step 3 returns the same body with `"evaluation_count": 1`.

## 3. Create an Evaluation

```bash
EXP_ID=$(nemo experiments get qa-baseline -f json | jq -r .id)
curl -s -X POST http://localhost:8080/apis/intake/v2/workspaces/default/evaluations \
  -H 'Content-Type: application/json' \
  -d '{"name": "gemma4-run1", "experiment_ids": ["'"$EXP_ID"'"], "dataset_name": "sample-qa-dataset", "dataset_version": "v1"}' | jq
```

The first line sets `$EXP_ID` again, because shell variables do not survive a new `make nemo-shell` session or a container restart. The image includes `curl` and `jq`; piping to `jq` pretty-prints the JSON response.

Check that the evaluation is linked to the experiment:

```bash
curl -s http://localhost:8080/apis/intake/v2/workspaces/default/evaluations/gemma4-run1 | jq
```

```json
{
  "id": "experiment-KZKJxovpSwMUYM5ZTms3bM",
  "name": "gemma4-run1",
  "workspace": "default",
  "experiment_ids": [
    "experiment-group-NqFFSJdK2vHoD7qAmCwYKD"
  ],
  "dataset_name": "sample-qa-dataset",
  "dataset_version": "v1",
  "source_link": null,
  "metadata": {},
  "description": null,
  "parent_evaluation_id": null,
  "status": null,
  "root_cause": null,
  "created_at": "2026-10-19T09:00:07.160955",
  "updated_at": "2026-10-19T09:00:07.160959",
  "pinned_at": null,
  "evaluator_names": [],
  "model_names": [],
  "agent_names": [],
  "agent_versions": [],
  "aggregate_scores": null,
  "run_count": 0,
  "test_case_count": 0,
  "cost_usd": null,
  "latency_ms": null,
  "tokens": null,
  "experiment_group_id": "experiment-group-NqFFSJdK2vHoD7qAmCwYKD"
}
```

`run_count` and `aggregate_scores` stay empty until the evaluation job in step 4 publishes its results. Running the POST again returns `{"detail": "Evaluation 'default/gemma4-run1' already exists."}`, which is safe to ignore.

If `$EXP_ID` is empty, the request fails with:

```json
{
  "detail": "ExperimentGroup '' must be created before an Evaluation can reference it."
}
```

## 4. Run the Evaluation Job

[`examples/eval-qa.yaml`](../examples/eval-qa.yaml) sends 20 questions from `sample-qa-dataset` (validation split) to `default/gemma4-e4b-mlx` and scores the answers with exact-match and F1.

```bash
nemo evaluator evaluate --spec-file /examples/eval-qa.yaml \
  --publication.intake.evaluation-id gemma4-run1 \
  --publication.intake.agent-name gemma4-e4b-mlx
```

The command submits a platform job and prints it. The job `name` (here `nemo-evaluator-kypasech`) is what the `nemo jobs` commands take. The job starts with `"status": "created"`.

<details>
<summary>Full response</summary>

```json
{
  "id": "platform-job-EwSrpPVMHyQJitu3r3PcuZ",
  "name": "nemo-evaluator-kypasech",
  "description": null,
  "project": null,
  "workspace": "default",
  "created_at": "2026-10-19T09:07:50.681002",
  "updated_at": "2026-10-19T09:07:50.693341",
  "spec": {
    "dataset": "default/sample-qa-dataset#validation/validation.jsonl",
    "params": {
      "parallelism": 2,
      "limit_samples": 20,
      "ignore_request_failure": false,
      "request_timeout": null,
      "max_retries": 3,
      "inference": null,
      "system_prompt": null,
      "reasoning": null,
      "structured_output": null
    },
    "target": {
      "url": "http://localhost:8080/apis/inference-gateway/v2/workspaces/default/openai/-/v1/chat/completions",
      "name": "default/gemma4-e4b-mlx",
      "host_url": null,
      "served_model_name": null,
      "inference": null,
      "api_key_secret": null,
      "format": "openai"
    },
    "prompt_template": {
      "messages": [
        {
          "role": "system",
          "content": "Answer with only the final answer. For capitals reply exactly: The capital of <country> is <city>."
        },
        {
          "role": "user",
          "content": "{{item.messages[0].content}}"
        }
      ]
    },
    "field_mapping": null,
    "publication": {
      "intake": {
        "evaluation_id": "gemma4-run1",
        "agent_name": "gemma4-e4b-mlx",
        "agent_version": "unknown",
        "required": true,
        "test_case_id_field": null
      }
    },
    "metrics": [
      {
        "bundle_kind": "metric-bundle",
        "bundle_format_version": "v1",
        "metric_type": "exact-match",
        "metadata": {
          "description": null,
          "labels": {}
        },
        "outputs": [
          {
            "name": "exact-match",
            "description": null,
            "value_json_schema": {
              "description": "Continuous numeric metric value.",
              "title": "ContinuousScore",
              "type": "number"
            }
          }
        ],
        "secrets": {},
        "payload": {
          "kind": "inline",
          "metric": {
            "type": "exact-match",
            "description": null,
            "labels": {},
            "supported_job_types": [
              "online",
              "offline"
            ],
            "reference": "{{item.messages[1].content}}",
            "candidate": "{{sample.output_text}}"
          },
          "digest": "6daef7ba398971c39bc52d44aacb3115e818240a6ee349b0b5ad264a387de590"
        }
      },
      {
        "bundle_kind": "metric-bundle",
        "bundle_format_version": "v1",
        "metric_type": "f1",
        "metadata": {
          "description": null,
          "labels": {}
        },
        "outputs": [
          {
            "name": "f1",
            "description": null,
            "value_json_schema": {
              "description": "Continuous numeric metric value.",
              "title": "ContinuousScore",
              "type": "number"
            }
          }
        ],
        "secrets": {},
        "payload": {
          "kind": "inline",
          "metric": {
            "type": "f1",
            "description": null,
            "labels": {},
            "supported_job_types": [
              "online",
              "offline"
            ],
            "reference": "{{item.messages[1].content}}",
            "candidate": "{{sample.output_text}}"
          },
          "digest": "c169060652af4f2e153a8b30a3bcf7513be59feeb5ef2241f06c304900d92f69"
        }
      }
    ]
  },
  "status": "created",
  "status_details": {},
  "error_details": null,
  "ownership": null,
  "custom_fields": {
    "_nemo_telemetry": {
      "plugins": [
        "evaluator"
      ]
    }
  }
}
```

</details>

### Track the Job

`get-status` needs the job name. Without it, the command fails with `Error: Missing required argument: <NAME>`.

```bash
nemo jobs get-status nemo-evaluator-kypasech
```

The job finished in about 17 seconds. `status_details.evaluation` reports `20 of 20 rows scored; 0 reported errors.`

<details>
<summary>Full response</summary>

```json
{
  "id": "platform-job-EwSrpPVMHyQJitu3r3PcuZ",
  "name": "nemo-evaluator-kypasech",
  "status": "completed",
  "status_details": {
    "message": "Job completed successfully with exit code 0",
    "subprocess_work_dir": "/tmp/nhx-subprocess-jobs/default/nemo-evaluator-kypasech/platform-job-attempt-9e2mogBBvMeZc7BB9jCUmF/evaluate/task-b08e586e86c848ba95eabf3915db8c69",
    "subprocess_persistent_storage_path": "/tmp/nhx-subprocess-jobs/default/nemo-evaluator-kypasech/platform-job-attempt-9e2mogBBvMeZc7BB9jCUmF/job-storage",
    "pid": 464,
    "pgid": 464,
    "input_tokens": 939,
    "output_tokens": 422,
    "evaluation": {
      "unit": "rows",
      "total": 20,
      "errored": 0,
      "scored": 20,
      "failed": false,
      "message": "20 of 20 rows scored; 0 reported errors."
    },
    "exit_code": 0
  },
  "error_details": null,
  "steps": [
    {
      "id": "platform-job-step-9AA8AKj5B4KtQoUapWER4P",
      "name": "evaluate",
      "status": "completed",
      "status_details": {
        "message": "Job completed successfully with exit code 0",
        "subprocess_work_dir": "/tmp/nhx-subprocess-jobs/default/nemo-evaluator-kypasech/platform-job-attempt-9e2mogBBvMeZc7BB9jCUmF/evaluate/task-b08e586e86c848ba95eabf3915db8c69",
        "subprocess_persistent_storage_path": "/tmp/nhx-subprocess-jobs/default/nemo-evaluator-kypasech/platform-job-attempt-9e2mogBBvMeZc7BB9jCUmF/job-storage",
        "pid": 464,
        "pgid": 464,
        "exit_code": 0
      },
      "error_details": {},
      "tasks": [
        {
          "id": "platform-job-task-8XhEnpM8v7XXBchmBUM46V",
          "name": "task-b08e586e86c848ba95eabf3915db8c69",
          "status": "completed",
          "status_details": {
            "message": "Job completed successfully with exit code 0",
            "subprocess_work_dir": "/tmp/nhx-subprocess-jobs/default/nemo-evaluator-kypasech/platform-job-attempt-9e2mogBBvMeZc7BB9jCUmF/evaluate/task-b08e586e86c848ba95eabf3915db8c69",
            "subprocess_persistent_storage_path": "/tmp/nhx-subprocess-jobs/default/nemo-evaluator-kypasech/platform-job-attempt-9e2mogBBvMeZc7BB9jCUmF/job-storage",
            "pid": 464,
            "pgid": 464,
            "exit_code": 0
          },
          "error_details": {},
          "error_stack": null,
          "created_at": "2026-10-19T09:07:51.602907",
          "updated_at": "2026-10-19T09:08:07.517994"
        }
      ],
      "created_at": "2026-10-19T09:07:50.698068",
      "updated_at": "2026-10-19T09:08:07.564979"
    }
  ],
  "created_at": "2026-10-19T09:07:50.681002",
  "updated_at": "2026-10-19T09:07:50.693341"
}
```

</details>

View the job logs:

```bash
nemo jobs get-logs nemo-evaluator-kypasech
```

The table cuts off long messages. Add `--no-truncate` to see them in full.

<details>
<summary>Full response</summary>

```text
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ timestamp                  ┃ message                                    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 2026-10-19T09:07:52.692621 │  NHX_PRINCIPAL not set; task NemoClient... │
│ 2026-10-19T09:07:52.695509 │  NHX_PRINCIPAL not set; async task Nemo... │
│ 2026-10-19T09:07:52.750757 │  2026-10-19T09:07:52.750 [...    │
│ 2026-10-19T09:07:53.114740 │  2026-10-19T09:07:53.114 [...    │
│ 2026-10-19T09:07:58.927910 │  2026-10-19T09:07:58.927 [...    │
│ 2026-10-19T09:07:59.958281 │  2026-10-19T09:07:59.958 [...    │
│ 2026-10-19T09:08:00.059348 │  2026-10-19T09:08:00.059 [...    │
│ 2026-10-19T09:08:00.159524 │  2026-10-19T09:08:00.159 [...    │
│ 2026-10-19T09:08:00.289502 │  2026-10-19T09:08:00.289 [...    │
│ 2026-10-19T09:08:00.398433 │  2026-10-19T09:08:00.398 [...    │
│ 2026-10-19T09:08:00.970238 │  2026-10-19T09:08:00.969 [...    │
│ 2026-10-19T09:08:01.060728 │  2026-10-19T09:08:01.060 [...    │
│ 2026-10-19T09:08:01.176520 │  2026-10-19T09:08:01.176 [...    │
│ 2026-10-19T09:08:01.321422 │  2026-10-19T09:08:01.320 [...    │
│ 2026-10-19T09:08:01.450140 │  2026-10-19T09:08:01.442 [...    │
│ 2026-10-19T09:08:01.587526 │  2026-10-19T09:08:01.582 [...    │
│ 2026-10-19T09:08:01.669209 │  2026-10-19T09:08:01.667 [...    │
│ 2026-10-19T09:08:02.171641 │  2026-10-19T09:08:02.170 [...    │
│ 2026-10-19T09:08:02.245792 │  2026-10-19T09:08:02.245 [...    │
│ 2026-10-19T09:08:02.365176 │  2026-10-19T09:08:02.364 [...    │
│ 2026-10-19T09:08:02.436382 │  2026-10-19T09:08:02.436 [...    │
│ 2026-10-19T09:08:02.575301 │  2026-10-19T09:08:02.574 [...    │
│ 2026-10-19T09:08:03.116325 │  2026-10-19T09:08:03.115 [...    │
│ 2026-10-19T09:08:03.120321 │  2026-10-19T09:08:03.119 [...    │
│ 2026-10-19T09:08:03.225479 │  2026-10-19T09:08:03.225 [...    │
│ 2026-10-19T09:08:03.261796 │  2026-10-19T09:08:03.261 [...    │
│ 2026-10-19T09:08:03.297715 │  2026-10-19T09:08:03.297 [...    │
│ 2026-10-19T09:08:03.344110 │  2026-10-19T09:08:03.339 [...    │
│ 2026-10-19T09:08:03.400452 │  2026-10-19T09:08:03.400 [...    │
│ 2026-10-19T09:08:03.436957 │  2026-10-19T09:08:03.436 [...    │
│ 2026-10-19T09:08:05.295666 │  2026-10-19T09:08:05.295 [...    │
│ 2026-10-19T09:08:05.296045 │  2026-10-19T09:08:05.295 [...    │
└────────────────────────────┴────────────────────────────────────────────┘


Warnings:
  • Use --no-truncate to see full values.
```

</details>

## 5. Read the Scores

```bash
nemo experiments list
```

```text
┏━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ name        ┃ workspace ┃ created_at                 ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ qa-baseline │ default   │ 2026-10-19T08:55:46.682662 │
└─────────────┴───────────┴────────────────────────────┘


Warnings:
  • Use --no-truncate to see full values.
```

```bash
curl -s http://localhost:8080/apis/intake/v2/workspaces/default/evaluations/gemma4-run1 | jq
```

The scores are in `aggregate_scores`. In this run, gemma4 scored a mean of `0.95` on both exact-match and F1 across 20 rows (`run_count`). Compared with the response in step 3, the evaluation now also lists `evaluator_names`, `model_names` and `agent_names`, and `metadata` records the eval and publish durations.

<details>
<summary>Full response</summary>

```json
{
  "id": "experiment-KZKJxovpSwMUYM5ZTms3bM",
  "name": "gemma4-run1",
  "workspace": "default",
  "experiment_ids": [
    "experiment-group-NqFFSJdK2vHoD7qAmCwYKD"
  ],
  "dataset_name": "sample-qa-dataset",
  "dataset_version": "v1",
  "source_link": null,
  "metadata": {
    "eval_duration_sec": "10.7",
    "publish_duration_sec": "1.7"
  },
  "description": null,
  "parent_evaluation_id": null,
  "status": null,
  "root_cause": null,
  "created_at": "2026-10-19T09:00:07.160955",
  "updated_at": "2026-10-19T09:08:11.025958",
  "pinned_at": null,
  "evaluator_names": [
    "exact-match.exact-match",
    "f1.f1"
  ],
  "model_names": [
    "default/gemma4-e4b-mlx"
  ],
  "agent_names": [
    "gemma4-e4b-mlx"
  ],
  "agent_versions": [
    "unknown"
  ],
  "aggregate_scores": {
    "exact-match.exact-match": {
      "sum": 0.95,
      "mean": 0.95,
      "median": 0.95,
      "p90": 0.95,
      "p95": 0.95,
      "p99": 0.95,
      "count": 1
    },
    "f1.f1": {
      "sum": 0.95,
      "mean": 0.95,
      "median": 0.95,
      "p90": 0.95,
      "p95": 0.95,
      "p99": 0.95,
      "count": 1
    }
  },
  "run_count": 20,
  "test_case_count": 1,
  "cost_usd": null,
  "latency_ms": {
    "sum": 0.0,
    "mean": 0.0,
    "median": 0.0,
    "p90": 0.0,
    "p95": 0.0,
    "p99": 0.0,
    "count": 1
  },
  "tokens": {
    "sum": 68.05,
    "mean": 68.05,
    "median": 68.05,
    "p90": 68.05,
    "p95": 68.05,
    "p99": 68.05,
    "count": 1
  },
  "experiment_group_id": "experiment-group-NqFFSJdK2vHoD7qAmCwYKD"
}
```

</details>

To pull just the scores:

```bash
curl -s http://localhost:8080/apis/intake/v2/workspaces/default/evaluations/gemma4-run1 \
  | jq '.aggregate_scores | map_values(.mean)'
```

## Comparing Runs

Create another evaluation in the same experiment (for example `gemma4-run2`), edit `target`, `prompt_template` or `params` in the spec, and rerun step 4. Do not hand-edit the `metrics` block: each metric carries a digest of its payload.

## Using the Eval Datasets

`eval-qa.yaml` reads `item.messages[...]`, which matches the `sample-qa-dataset` rows. The other datasets use different fields, so each needs its own spec with matching `dataset` and `prompt_template` values:

| Fileset             | Fields                   |
| ------------------- | ------------------------ |
| `single-turn-evals` | `input`, `reference`     |
| `multi-turn-evals`  | `messages`, `reference`  |

`eval-multi-turn.yaml` is the spec for `multi-turn-evals`. It sends each row's whole conversation with `prompt_template: {messages: "{{ item.messages }}"}` and scores against `{{item.reference}}`. Its metric digests were recomputed for the new `reference`: each digest is the SHA-256 of the metric's `payload.metric` as canonical JSON (sorted keys, no spaces).

## Automating with Airflow

The Airflow DAGs `nemo_helix_single_turn_eval_pipeline` (`eval-qa.yaml`) and `nemo_helix_multi_turn_eval_pipeline` (`eval-multi-turn.yaml`) run steps 1 to 5 in one go and create a new evaluation on every run. See the [Airflow section of the README](../README.md#airflow). The Grafana dashboards **NeMo Helix Single-Turn Eval Pipeline** and **NeMo Helix Multi-Turn Eval Pipeline** chart each run's scores; see the [Grafana section](../README.md#grafana).
