.PHONY: check

check:
	uv run mypy app 2>&1 | grep -E 'error' || true
	uv run pytest -q --tb=short 2>&1 | grep -E 'FAIL|Error|passed|failed'
