NEMO_DATA_DIR ?= $(CURDIR)/.nemo-data
DOCKER_GID ?= $(shell stat -c %g /var/run/docker.sock 2>/dev/null || echo 0)
dotenv = $(shell grep -E '^$(1)=' .env 2>/dev/null | cut -d= -f2-)
HELIX_DB_USER ?= $(or $(call dotenv,HELIX_DB_USER),helix)
HELIX_DB_PASSWORD ?= $(or $(call dotenv,HELIX_DB_PASSWORD),helix)
HELIX_DB_NAME ?= $(or $(call dotenv,HELIX_DB_NAME),helix)
HELIX_DB_PORT ?= $(or $(call dotenv,HELIX_DB_PORT),7450)

LONG_RUNNING_SERVICES = helix-postgres clickhouse nemo-helix pgadmin
BOOTSTRAP_SERVICES = data-generator model-setup
NEMO_CLI = NEMO_DATA_DIR=$(NEMO_DATA_DIR) docker compose -f $(CURDIR)/docker-compose.yaml exec nemo-helix nemo

export NEMO_DATA_DIR
export DOCKER_GID

.PHONY: start stop logs ps build nemo nemo-shell alias bootstrap-wait summary status helix-up chat

build:
	docker compose build

# Starts NeMo Helix, then Airflow and Grafana ($(ENV), dev by default), and prints one summary.
start: helix-up airflow-up grafana-up
	@echo ""
	@echo "=========================================="
	@echo "Services Started Successfully!"
	@echo "=========================================="
	@echo ""
	@echo "Helix Postgres:"
	@$(call row,host,localhost:$(HELIX_DB_PORT))
	@$(call row,username,$(HELIX_DB_USER))
	@$(call row,password,$(HELIX_DB_PASSWORD))
	@$(call row,database,$(HELIX_DB_NAME))
	@echo ""
	@echo "Airflow $(ENV) Postgres:"
	@$(call row,host,localhost:$(or $(call envval,POSTGRES_PORT),7432))
	@$(call row,username,airflow)
	@$(call row,password,$(call envval,POSTGRES_PASSWORD))
	@$(call row,database,airflow)
	@echo ""
	@echo "Grafana $(ENV) Postgres:"
	@$(call row,host,localhost:$(or $(call genvval,POSTGRES_PORT),7440))
	@$(call row,username,postgres)
	@$(call row,password,$(call genvval,POSTGRES_PASSWORD))
	@$(call row,database,postgres)
	@echo ""
	@echo "Service Ports:"
	@$(call row,nemo-helix API,http://localhost:7001)
	@$(call row,nemo-helix Studio,http://localhost:7001/studio/)
	@$(call row,clickhouse,http://localhost:7123)
	@$(call row,helix postgres,localhost:$(HELIX_DB_PORT))
	@$(call row,helix pgadmin,http://localhost:7006)
	@$(call row,airflow,http://localhost:$(or $(call envval,AIRFLOW_PORT),7002))
	@$(call row,airflow postgres,localhost:$(or $(call envval,POSTGRES_PORT),7432))
	@$(call row,grafana,$(GRAFANA_URL))
	@$(call row,pgadmin,http://localhost:$(or $(call genvval,PGADMIN_PORT),7004))
	@$(call row,grafana postgres,localhost:$(or $(call genvval,POSTGRES_PORT),7440))
	@echo ""
	@$(MAKE) -s summary
	@echo ""
	@echo "Airflow $(ENV) Login:"
	@$(call row,username,$(or $(call envval,ADMIN_USERNAME),airflow))
	@$(call row,password,$(call envval,ADMIN_PASSWORD))
	@echo ""
	@echo "Grafana $(ENV) Login:"
	@$(call row,username,$(GRAFANA_USER))
	@$(call row,password,$(GRAFANA_PASSWORD))
	@echo ""
	@echo "Useful Commands:"
	@$(call row,View logs,make logs)
	@$(call row,Stop,make stop)
	@$(call row,Single-turn eval,make airflow-trigger)
	@$(call row,Multi-turn eval,make airflow-trigger AIRFLOW_DAG=nemo_helix_multi_turn_eval_pipeline)
	@$(call row,Traced chat,make chat PROMPT="Hello")
	@echo "  • NeMo CLI:          make nemo ARGS=\"models list\""
	@echo "  • CLI alias:         eval \"\$$(make -s alias)\""

helix-up:
	mkdir -p $(NEMO_DATA_DIR)
	docker compose up -d --build --wait $(LONG_RUNNING_SERVICES)
	docker compose up -d $(BOOTSTRAP_SERVICES)
	@$(MAKE) -s bootstrap-wait

# Stops Grafana, Airflow, then NeMo Helix. All keep their data volumes.
stop: grafana-stop airflow-stop
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps -a

bootstrap-wait:
	@echo "Waiting for bootstrap containers: $(BOOTSTRAP_SERVICES)"
	@docker compose wait $(BOOTSTRAP_SERVICES) >/dev/null; \
	failed=0; \
	for svc in $(BOOTSTRAP_SERVICES); do \
		code=$$(docker inspect -f '{{.State.ExitCode}}' $$(docker compose ps -aq $$svc)); \
		if [ "$$code" != "0" ]; then \
			echo "$$svc exited with code $$code, last log lines:"; \
			docker compose logs --no-log-prefix --tail 20 $$svc; \
			failed=1; \
		fi; \
	done; \
	exit $$failed

summary:
	@docker compose exec -T nemo-helix python -m app.summary

status: ps summary

nemo:
	$(NEMO_CLI) $(ARGS)

nemo-shell:
	docker compose exec nemo-helix bash

# Chat through the Helix gateway and record the call on the Traces page: make chat PROMPT="Hello" [SESSION=<id>]
chat:
	@docker compose exec -T nemo-helix python -m app.traced_chat "$(PROMPT)" $(if $(SESSION),--session $(SESSION))

alias:
	@echo "export NEMO_DATA_DIR=$(NEMO_DATA_DIR)"
	@echo "alias nemo='$(NEMO_CLI)'"

# ---- airflow (docker compose) -----------------------------------------------
# Separate project per environment: make airflow-start [ENV=dev|staging|prod] [TAG=<sha>]
ENV ?= dev
AIRFLOW_IMAGE ?= nemo-helix-airflow
TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)$(shell git diff --quiet 2>/dev/null || echo -dirty)
AIRFLOW_DAG ?= nemo_helix_single_turn_eval_pipeline
# airflow-init exits once done, and `up --wait` counts any exit as a failure, so wait only on long-running services.
AIRFLOW_SERVICES = airflow-apiserver airflow-scheduler airflow-dag-processor airflow-worker airflow-triggerer

ifeq ($(filter $(ENV),dev staging prod),)
$(error ENV must be dev, staging, or prod, got "$(ENV)")
endif

AIRFLOW_COMPOSE = AIRFLOW_IMAGE_NAME=$(AIRFLOW_IMAGE):$(TAG) docker compose -p airflow-$(ENV) \
	--env-file airflow/envs/$(ENV).env \
	-f airflow/docker-compose.yml $(if $(filter dev,$(ENV)),-f airflow/docker-compose.dev.yml)
AIRFLOW_EXEC = docker exec airflow-$(ENV)-airflow-worker-1

envval = $(shell grep -E '^$(1)=' airflow/envs/$(ENV).env 2>/dev/null | cut -d= -f2-)
row = printf "  • %-18s %s\n" "$(1):" "$(2)"

.PHONY: airflow-build airflow-start airflow-stop airflow-status airflow-logs airflow-shell \
	airflow-summary airflow-trigger airflow-up privacy-spec airflow-secret

airflow-build:
	docker build -t $(AIRFLOW_IMAGE):$(TAG) --build-context examples=examples airflow

airflow-start: airflow-up
	@$(MAKE) -s airflow-summary ENV=$(ENV) TAG=$(TAG)

# Dev gets its env file from the example on first run; staging and prod need real secrets.
airflow-up: airflow-build
	@test -f airflow/envs/$(ENV).env || [ "$(ENV)" != dev ] || cp airflow/envs/dev.env.example airflow/envs/dev.env
	@test -f airflow/envs/$(ENV).env || (echo "missing airflow/envs/$(ENV).env; copy airflow/envs/$(ENV).env.example" && false)
	mkdir -p airflow/logs/$(ENV)
	$(AIRFLOW_COMPOSE) up -d --wait $(AIRFLOW_SERVICES)

# Keeps the DB volume. `docker compose -p airflow-<env> down -v` deletes it.
airflow-stop:
	docker compose -p airflow-$(ENV) down

airflow-status:
	docker compose -p airflow-$(ENV) ps -a

airflow-logs:
	docker compose -p airflow-$(ENV) logs -f --tail=100

airflow-shell:
	docker exec -it airflow-$(ENV)-airflow-worker-1 bash

# Unpauses and triggers the single-turn eval DAG; extra conf with CONF='{"evaluation_name": "gemma4-run2"}'.
# Pick another DAG with AIRFLOW_DAG=nemo_helix_multi_turn_eval_pipeline or nemo_helix_privacy_pipeline.
airflow-trigger:
	$(AIRFLOW_EXEC) airflow dags unpause $(AIRFLOW_DAG)
	$(AIRFLOW_EXEC) airflow dags trigger $(AIRFLOW_DAG) $(if $(CONF),--conf '$(CONF)')

# Store a secret value as an Airflow Variable for the sync-secrets DAG, prompting so it stays out of shell history.
airflow-secret:
	@test -n "$(VAR)" || (echo "usage: make airflow-secret VAR=helix_secret_<name>" && false)
	@printf "Value for $(VAR): "; stty -echo; read value; stty echo; echo; \
		$(AIRFLOW_EXEC) airflow variables set "$(VAR)" "$$value" >/dev/null && echo "Set Airflow Variable $(VAR)"

# Re-render the Data Designer spec the privacy DAG submits, after editing examples/customer_notes.py.
privacy-spec:
	cd app && uv run python -m app.dd_spec ../examples/customer_notes.py > ../examples/privacy-data-designer.yaml

airflow-summary:
	@echo ""
	@echo "=========================================="
	@echo "Airflow $(ENV) Started Successfully!"
	@echo "=========================================="
	@echo ""
	@$(call row,image,$(AIRFLOW_IMAGE):$(TAG))
	@echo ""
	@echo "Service Ports:"
	@$(call row,airflow,http://localhost:$(or $(call envval,AIRFLOW_PORT),7002))
	@$(call row,postgres,localhost:$(or $(call envval,POSTGRES_PORT),7432))
	@echo ""
	@echo "Airflow Login:"
	@$(call row,username,$(or $(call envval,ADMIN_USERNAME),airflow))
	@$(call row,password,$(call envval,ADMIN_PASSWORD))
	@echo ""
	@echo "Useful Commands:"
	@$(call row,Single-turn eval,make airflow-trigger ENV=$(ENV))
	@$(call row,Multi-turn eval,make airflow-trigger AIRFLOW_DAG=nemo_helix_multi_turn_eval_pipeline ENV=$(ENV))
	@$(call row,View logs,make airflow-logs ENV=$(ENV))
	@$(call row,Stop,make airflow-stop ENV=$(ENV))

# ---- grafana (docker compose) -----------------------------------------------
# Separate project per environment: make grafana-start [ENV=dev|staging|prod] [TAG=<sha>]
GRAFANA_IMAGE = nemo-helix-grafana:$(TAG)
FLYWAY_IMAGE = nemo-helix-flyway:$(TAG)
DASHBOARDS_DIR = grafana/dashboards-as-code

GRAFANA_COMPOSE = GRAFANA_IMAGE=$(GRAFANA_IMAGE) FLYWAY_IMAGE=$(FLYWAY_IMAGE) docker compose -p grafana-$(ENV) \
	--env-file grafana/envs/$(ENV).env \
	-f grafana/docker-compose.yml $(if $(filter dev,$(ENV)),-f grafana/docker-compose.dev.yml)

genvval = $(shell grep -E '^$(1)=' grafana/envs/$(ENV).env 2>/dev/null | cut -d= -f2-)
GRAFANA_URL ?= http://localhost:$(or $(call genvval,GRAFANA_PORT),7003)
GRAFANA_USER ?= $(or $(call genvval,GRAFANA_ADMIN_USER),admin)
GRAFANA_PASSWORD ?= $(call genvval,GRAFANA_ADMIN_PASSWORD)

.PHONY: grafana-build grafana-up grafana-start grafana-stop grafana-status grafana-logs grafana-shell \
	grafana-summary dashboards dashboards-check star-dashboards

# Render the Python dashboards in grafana/dashboards-as-code to JSON.
dashboards:
	cd $(DASHBOARDS_DIR) && uv run python build.py

# Fail if the committed dashboard JSON is out of date with the Python source.
dashboards-check:
	cd $(DASHBOARDS_DIR) && uv run python build.py --check

# Star the code-managed dashboards for the Grafana admin user.
star-dashboards:
	@cd $(DASHBOARDS_DIR) && GRAFANA_URL=$(GRAFANA_URL) GRAFANA_USER=$(GRAFANA_USER) GRAFANA_PASSWORD=$(GRAFANA_PASSWORD) uv run python star.py

grafana-build: dashboards
	docker build -t $(GRAFANA_IMAGE) grafana
	docker build -t $(FLYWAY_IMAGE) -f database/Dockerfile.flyway database

grafana-start: grafana-up
	@$(MAKE) -s grafana-summary ENV=$(ENV) TAG=$(TAG)

# Dev gets its env file from the example on first run; staging and prod need real secrets.
grafana-up: grafana-build
	@test -f grafana/envs/$(ENV).env || [ "$(ENV)" != dev ] || cp grafana/envs/dev.env.example grafana/envs/dev.env
	@test -f grafana/envs/$(ENV).env || (echo "missing grafana/envs/$(ENV).env; copy grafana/envs/$(ENV).env.example" && false)
	$(GRAFANA_COMPOSE) up -d --wait grafana postgres pgadmin
	$(GRAFANA_COMPOSE) up flyway --exit-code-from flyway
	-@$(MAKE) -s star-dashboards ENV=$(ENV)

# Keeps the volumes. `docker compose -p grafana-<env> down -v` deletes Grafana's and Postgres' data.
grafana-stop:
	docker compose -p grafana-$(ENV) down

grafana-status:
	docker compose -p grafana-$(ENV) ps -a

grafana-logs:
	docker compose -p grafana-$(ENV) logs -f --tail=100

grafana-shell:
	docker exec -it grafana-$(ENV)-postgres-1 psql -U postgres

grafana-summary:
	@echo ""
	@echo "=========================================="
	@echo "Grafana $(ENV) Started Successfully!"
	@echo "=========================================="
	@echo ""
	@$(call row,images,$(GRAFANA_IMAGE) $(FLYWAY_IMAGE))
	@echo ""
	@echo "Service Ports:"
	@$(call row,grafana,$(GRAFANA_URL))
	@$(call row,pgadmin,http://localhost:$(or $(call genvval,PGADMIN_PORT),7004))
	@$(call row,postgres,localhost:$(or $(call genvval,POSTGRES_PORT),7440))
	@echo ""
	@echo "Grafana Login:"
	@$(call row,username,$(GRAFANA_USER))
	@$(call row,password,$(GRAFANA_PASSWORD))
	@echo ""
	@echo "pgAdmin Login:"
	@$(call row,email,$(or $(call genvval,PGADMIN_EMAIL),pgadmin4@pgadmin.org))
	@$(call row,password,$(call genvval,PGADMIN_PASSWORD))
	@$(call row,db password,$(call genvval,POSTGRES_PASSWORD))
	@echo ""
	@echo "Useful Commands:"
	@$(call row,Dashboards,make dashboards)
	@$(call row,View logs,make grafana-logs ENV=$(ENV))
	@$(call row,Stop,make grafana-stop ENV=$(ENV))
