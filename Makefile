SHELL := bash
.SHELLFLAGS := -o pipefail -c
.PHONY: check

# Each step prints only errors and summary lines, and any failure fails the target.
check:
	uv run ruff check --quiet app/ tests/
	uv run mypy app | grep -E 'error|Success'
	uv run pytest -q --tb=short | grep -E 'FAIL|Error|passed|failed'
