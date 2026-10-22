"""Shared building blocks for dashboards."""

from grafanalib.core import SqlTarget

# Matches the provisioned data source in grafana/provisioning/datasources/datasource.yaml.
# Referenced by uid, not name, so renaming the data source can't break panels.
POSTGRES = {"type": "grafana-postgresql-datasource", "uid": "postgres_ds"}


def sql(query: str, ref_id: str = "A", fmt: str = "table") -> SqlTarget:
    return SqlTarget(rawSql=query, refId=ref_id, format=fmt, datasource=POSTGRES)

# Airflow's metadata DB (uid airflow_ds in the same provisioning file).
AIRFLOW = {"type": "grafana-postgresql-datasource", "uid": "airflow_ds"}


def airflow_sql(query: str, ref_id: str = "A", fmt: str = "table") -> SqlTarget:
    return SqlTarget(rawSql=query, refId=ref_id, format=fmt, datasource=AIRFLOW)
