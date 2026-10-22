# Grafana with provisioning and the generated dashboards baked in.
# Every environment runs this image. Dev bind-mounts provisioning/ and
# dashboards/ over the baked copies; staging and prod run what was built.
FROM grafana/grafana:13.2.4

COPY provisioning/ /etc/grafana/provisioning/
COPY dashboards/ /etc/grafana/dashboards/
