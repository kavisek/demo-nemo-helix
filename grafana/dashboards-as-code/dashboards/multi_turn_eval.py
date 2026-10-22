"""Dashboard for the nemo_helix_multi_turn_eval_pipeline DAG."""

from .eval_pipeline import eval_pipeline_dashboard

dashboard = eval_pipeline_dashboard(
    dag_id="nemo_helix_multi_turn_eval_pipeline",
    title="NeMo Helix Multi-Turn Eval Pipeline",
    uid="nemo_helix_multi_turn_eval",
)
