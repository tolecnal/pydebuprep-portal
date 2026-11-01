PYTHON ?= .venv/bin/python
PIP_AUDIT ?= pip-audit
DB_CONTAINER ?= pydebuprep-portal-testdb
TEST_DB_URL ?= postgresql+psycopg://portal_test:portal_test@127.0.0.1:55432/portal_test

.PHONY: venv lock lint test audit db-up db-down image preview preview-down

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
	docker build -t ghcr.io/tolecnal/pydebuprep-portal:$$(.venv/bin/python -c 'from pydebuprep_portal import __version__ as v; print(v)') .

# A local preview: the portal with a ready-made Keycloak and demo data.
# PREVIEW_HOST: the name or address browsers use; anything but localhost publishes the
# ports on every interface (for a headless machine).
PREVIEW_HOST ?= localhost
PREVIEW_BIND = $(if $(filter localhost,$(PREVIEW_HOST)),127.0.0.1,0.0.0.0)
PREVIEW = POSTGRES_PASSWORD=preview PORTAL_ENV_FILE=preview/preview.env PORTAL_PORT=8400 \
  PORTAL_BIND=$(PREVIEW_BIND) PREVIEW_HOST=$(PREVIEW_HOST) \
  docker compose -p pydebuprep-preview -f compose.yaml -f compose.preview.yaml

preview:
	$(PREVIEW) up -d --build --wait
	$(PREVIEW) exec -T portal python -m pydebuprep_portal.manage demo-data || true
	@echo
	@echo "The portal: http://$(PREVIEW_HOST):8400"
	@echo "Sign in as admin, lead, member or auditor; the password is the name."
	@echo "Keycloak's console: http://$(PREVIEW_HOST):8480 (admin/admin). Stop with: make preview-down"

preview-down:
	$(PREVIEW) down -v
