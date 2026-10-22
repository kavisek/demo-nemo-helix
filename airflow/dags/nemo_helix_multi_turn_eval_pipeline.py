"""Multi-turn eval: the model answers the last turn of a scripted conversation.

Runs examples/eval-multi-turn.yaml on multi-turn-evals. Each row's messages
(user, assistant, user) are sent as the chat history, so the final question can
only be answered from earlier turns, and the reply is scored against the row's
reference with exact-match and F1. Built by helix_eval.build_eval_pipeline; see
that module for the steps.
"""

from airflow.sdk import DAG
from helix_eval import build_eval_pipeline

multi_turn_eval: DAG = build_eval_pipeline(
    dag_id="nemo_helix_multi_turn_eval_pipeline",
    doc_md=__doc__,
    experiment_description="gemma4 on scripted multi-turn conversations",
    spec_file="eval-multi-turn.yaml",
    filesets=["multi-turn-evals"],
    name_tag="multi-turn",
    tags=["multi-turn"],
)
