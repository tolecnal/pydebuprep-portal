PYTHON ?= .venv/bin/python
DB_CONTAINER ?= pydebuprep-portal-testdb
TEST_DB_URL ?= postgresql+psycopg://portal_test:portal_test@127.0.0.1:55432/portal_test

.PHONY: venv lock lint test db-up db-down image

venv:
	python3 -m venv .venv
	.venv/bin/pip install -q --require-hashes -r requirements-dev.txt
	.venv/bin/pip install -q --no-deps -e .

lock:
	uv pip compile -q --python-version 3.12 --generate-hashes requirements.in -o requirements.txt
	uv pip compile -q --python-version 3.12 --generate-hashes requirements-dev.in -o requirements-dev.txt
	uv pip compile -q --python-version 3.12 --generate-hashes requirements-build.in -o requirements-build.txt

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests
	$(PYTHON) -m mypy
	$(PYTHON) -m bandit -q -r src

test:
	PORTAL_TEST_DATABASE_URL=$${PORTAL_TEST_DATABASE_URL:-$(TEST_DB_URL)} \
	  $(PYTHON) -m pytest -q --cov --cov-report=term-missing

db-up:
	docker run -d --rm --name $(DB_CONTAINER) -p 127.0.0.1:55432:5432 \
	  -e POSTGRES_DB=portal_test -e POSTGRES_USER=portal_test -e POSTGRES_PASSWORD=portal_test \
	  postgres:17
	until docker exec $(DB_CONTAINER) pg_isready -q -U portal_test; do sleep 0.5; done

db-down:
	docker stop $(DB_CONTAINER)

image:
	docker build -t pydebuprep-portal .
