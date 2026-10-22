"""Single-turn eval: one question per row, scored against the reference answer.

Runs examples/eval-qa.yaml, which asks default/gemma4-e4b-mlx 20 questions from
sample-qa-dataset and scores the answers with exact-match and F1. Built by
helix_eval.build_eval_pipeline; see that module for the steps.
"""

from airflow.sdk import DAG
from helix_eval import build_eval_pipeline

single_turn_eval: DAG = build_eval_pipeline(
    dag_id="nemo_helix_single_turn_eval_pipeline",
    doc_md=__doc__,
    experiment_description="gemma4 on sample QA, single turn",
    spec_file="eval-qa.yaml",
    filesets=["sample-qa-dataset", "single-turn-evals"],
    name_tag="single-turn",
    tags=["single-turn"],
)
