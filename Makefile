PYTHON ?= .venv/bin/python
PIP_AUDIT ?= pip-audit
DB_CONTAINER ?= pydebuprep-portal-testdb
TEST_DB_URL ?= postgresql+psycopg://portal_test:portal_test@127.0.0.1:55432/portal_test

.PHONY: venv lock lint test audit db-up db-down image

venv:
	python3 -m venv .venv
	.venv/bin/pip install -q --require-hashes -r requirements-dev.txt
	.venv/bin/pip install -q --no-deps -e .

lock:
	uv pip compile -q --python-version 3.12 --generate-hashes requirements.in -o requirements.txt
	uv pip compile -q --python-version 3.12 --generate-hashes requirements-dev.in -o requirements-dev.txt
	uv pip compile -q --python-version 3.12 --generate-hashes requirements-build.in -o requirements-build.txt
	uv pip compile -q --python-version 3.12 --generate-hashes requirements-audit.in -o requirements-audit.txt

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests
	$(PYTHON) -m mypy
	$(PYTHON) -m bandit -q -r src

# Known vulnerabilities in every pinned dependency. pydebuprep is pinned by URL (pip-audit
# can't look that up); it is audited in its own repository.
AUDIT_FILES = requirements.txt requirements-build.txt requirements-dev.txt requirements-audit.txt
audit:
	@mkdir -p .audit
	@for f in $(AUDIT_FILES); do \
	  awk '/^pydebuprep @/ {skip = 1; next} skip && /^[[:space:]]/ {next} {skip = 0; print}' \
	    $$f > .audit/$$f; done
	$(PIP_AUDIT) --strict --disable-pip $(addprefix -r .audit/,$(AUDIT_FILES))

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
