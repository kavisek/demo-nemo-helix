# Custom Airflow image: dependencies, DAGs, and plugins baked in.
# Every environment runs this image. Dev bind-mounts dags/ and plugins/ over the
# baked copies; staging and prod run exactly what was built into the tag.
FROM apache/airflow:3.3.2

COPY requirements.txt /requirements.txt
# Pin apache-airflow to the base image's version so a dependency can't upgrade
# or downgrade Airflow itself. uv ships in the official image.
RUN uv pip install --no-cache "apache-airflow==${AIRFLOW_VERSION}" -r /requirements.txt

COPY --chown=airflow:root dags/ /opt/airflow/dags/
COPY --chown=airflow:root plugins/ /opt/airflow/plugins/
# Evaluator specs the DAGs submit, from the repo's examples/ (make airflow-build passes it as a named build context).
COPY --from=examples --chown=airflow:root *.yaml /opt/airflow/examples/
