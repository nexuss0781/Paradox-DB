.PHONY: dev install lint typecheck test format clean migrate

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

install:
	pip install -r requirements.txt

lint:
	ruff check app/ tests/

typecheck:
	mypy app/

test:
	pytest tests/ -m "not integration" -v

test-integration:
	pytest tests/ -m integration -v

test-all:
	pytest tests/ -v

format:
	ruff format app/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .mypy_cache .pytest_cache htmlcov

migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(msg)"
