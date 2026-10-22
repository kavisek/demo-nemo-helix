# Template Python App

A Python application template with Docker support and comprehensive testing setup.

## Setup

```bash
cd app
poetry install
```

## Testing

Run tests inside Docker container:

```bash
make test
```

This will:
1. Build a Docker image with all dependencies
2. Run pytest with coverage reporting
3. Display test results and coverage metrics

## Development

- Add your application code to `app/`
- Add tests to `tests/`
- Use `make format` to format code with black and isort
- Use `make test` to run tests in Docker
