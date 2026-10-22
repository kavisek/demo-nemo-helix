# Contributing to demo-nemo-helix

Thanks for your interest in contributing!

## Development setup

You need Docker (with Compose v2), GNU Make, and [uv](https://docs.astral.sh/uv/). [Ollama](https://ollama.com/) is used as the default model provider.

```sh
git clone https://github.com/kavisek/demo-nemo-helix.git
cd demo-nemo-helix
cp .env.example .env          # optional; fill in only the keys you need
make start                    # NeMo Helix, Airflow, and Grafana; prints every port and login
```

## Running tests and linters

```sh
cd app
uv sync
uv run pytest                 # or `make test` to run them in Docker
uv run black . && uv run isort .
```

Grafana dashboards are Python (grafanalib); run `make dashboards-check` after changing them.

## Making a change

1. Open an issue first for anything bigger than a small fix, so we can agree on the approach.
2. Fork the repo and create a branch: `git switch -c feat/my-change`.
3. Write commits in [Conventional Commits](https://www.conventionalcommits.org/) style (`feat:`, `fix:`, `docs:`, `chore:`).
4. Add or update tests, and make sure the checks above pass.
5. Open a pull request against `main` and fill in the template.

Never commit `.env`, `airflow/envs/*.env`, `grafana/envs/*.env`, or anything under `.nemo-data/`.

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you agree to uphold it.

## License

By contributing, you agree that your contributions are licensed under the project's [license](LICENSE).
