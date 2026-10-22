"""Builder for the NeMo Helix eval pipeline dashboards, one per eval DAG.

Reads Airflow's metadata DB. Scores come from the DAG's XComs: get_scores
returns the mean per metric and wait_for_job returns the evaluator's row
counts. single_turn_eval.py and multi_turn_eval.py build one dashboard each.
"""

from grafanalib.core import Dashboard, GridPos, Stat, Table, Time, TimeSeries

from .common import AIRFLOW, airflow_sql

RUN_COUNTS = """
SELECT
  count(*) FILTER (WHERE state = 'success') AS succeeded,
  count(*) FILTER (WHERE state = 'failed') AS failed,
  count(*) FILTER (WHERE state IN ('queued', 'running')) AS in_progress
FROM dag_run
WHERE dag_id = '{dag_id}' AND $__timeFilter(run_after);
"""

LATEST_SCORES = """
SELECT
  (x.value ->> 'exact-match.exact-match')::float AS "exact-match",
  (x.value ->> 'f1.f1')::float AS f1
FROM xcom x JOIN dag_run dr ON dr.id = x.dag_run_id
WHERE x.dag_id = '{dag_id}' AND x.task_id = 'get_scores' AND x.key = 'return_value'
ORDER BY dr.run_after DESC
LIMIT 1;
"""

SCORES_OVER_TIME = """
SELECT dr.run_after AS time, s.key AS metric, s.value::float AS value
FROM xcom x
JOIN dag_run dr ON dr.id = x.dag_run_id
CROSS JOIN LATERAL jsonb_each_text(x.value) s
WHERE x.dag_id = '{dag_id}' AND x.task_id = 'get_scores' AND x.key = 'return_value'
  AND $__timeFilter(dr.run_after)
ORDER BY 1;
"""

RECENT_RUNS = """
SELECT
  dr.run_id,
  dr.state,
  dr.start_date,
  round(extract(epoch FROM dr.end_date - dr.start_date)) AS duration_s,
  (scores.value ->> 'exact-match.exact-match')::float AS exact_match,
  (scores.value ->> 'f1.f1')::float AS f1,
  (job.value ->> 'scored')::int AS rows_scored,
  (job.value ->> 'errored')::int AS rows_errored
FROM dag_run dr
LEFT JOIN xcom scores
  ON scores.dag_run_id = dr.id AND scores.task_id = 'get_scores' AND scores.key = 'return_value'
LEFT JOIN xcom job
  ON job.dag_run_id = dr.id AND job.task_id = 'wait_for_job' AND job.key = 'return_value'
WHERE dr.dag_id = '{dag_id}' AND $__timeFilter(dr.run_after)
ORDER BY dr.run_after DESC
LIMIT 50;
"""

TASK_DURATIONS = """
SELECT task_id, round(avg(duration)::numeric, 1) AS avg_seconds, count(*) AS runs
FROM task_instance
WHERE dag_id = '{dag_id}' AND state = 'success' AND $__timeFilter(start_date)
GROUP BY task_id
ORDER BY avg_seconds DESC;
"""


def eval_pipeline_dashboard(dag_id: str, title: str, uid: str) -> Dashboard:
    """Build the dashboard for one eval DAG."""

    def query(template: str) -> str:
        return template.format(dag_id=dag_id)

    return Dashboard(
        title=title,
        uid=uid,
        description=f"Runs of the {dag_id} Airflow DAG and the evaluation scores they published.",
        tags=["nemo-helix", "airflow"],
        time=Time("now-7d", "now"),
        refresh="30s",
        panels=[
            Stat(
                title="DAG Runs",
                dataSource=AIRFLOW,
                targets=[airflow_sql(query(RUN_COUNTS))],
                reduceCalc="lastNotNull",
                gridPos=GridPos(h=5, w=12, x=0, y=0),
            ),
            Stat(
                title="Latest Run Scores",
                dataSource=AIRFLOW,
                targets=[airflow_sql(query(LATEST_SCORES))],
                reduceCalc="lastNotNull",
                decimals=3,
                gridPos=GridPos(h=5, w=12, x=12, y=0),
            ),
            TimeSeries(
                title="Mean Score per Run",
                dataSource=AIRFLOW,
                targets=[airflow_sql(query(SCORES_OVER_TIME), fmt="time_series")],
                showPoints="always",
                gridPos=GridPos(h=9, w=24, x=0, y=5),
            ),
            Table(
                title="Recent Runs",
                dataSource=AIRFLOW,
                targets=[airflow_sql(query(RECENT_RUNS))],
                gridPos=GridPos(h=9, w=24, x=0, y=14),
            ),
            Table(
                title="Average Task Duration",
                dataSource=AIRFLOW,
                targets=[airflow_sql(query(TASK_DURATIONS))],
                gridPos=GridPos(h=9, w=24, x=0, y=23),
            ),
        ],
    ).auto_panel_ids()
