.PHONY: setup backend frontend dev desktop test lint clean docker

# The newest Python available, 3.11 or later preferred. On 3.11+ the pinned
# requirements.lock is installed (the set CI tests); older Pythons, which several
# of those releases no longer support, resolve requirements.txt instead.
PY ?= $(shell command -v python3.13 || command -v python3.12 || command -v python3.11 || command -v python3)
VENV := backend/.venv
PYBIN := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
REQS := $(shell $(PY) -c "import sys; print('requirements.lock' if sys.version_info >= (3, 11) else 'requirements.txt')")

setup: $(VENV) node_modules_stamp
	@echo ""
	@echo "  Setup complete."
	@echo "  1. cp .env.example .env   (optional: add ANTHROPIC_API_KEY)"
	@echo "  2. make dev"
	@echo ""

$(VENV):
	$(PY) -m venv $(VENV)
	$(PIP) install --upgrade pip wheel setuptools
	$(PIP) install -r backend/$(REQS)

node_modules_stamp:
	cd frontend && npm install
	@touch node_modules_stamp

# The API binds 127.0.0.1 unless API_HOST says otherwise, and refuses a network
# address without APP_ACCESS_TOKEN — it can place real orders. See .env.example.
backend:
	cd backend && .venv/bin/python -m app.serve --reload

frontend:
	cd frontend && npm run dev

# Runs API + UI together; Ctrl-C stops both.
dev:
	@trap 'kill 0' INT TERM; \
	( cd backend && .venv/bin/python -m app.serve ) & \
	( cd frontend && npm run dev ) & \
	wait

desktop:
	@trap 'kill 0' INT TERM; \
	( cd backend && .venv/bin/python -m app.serve ) & \
	( cd frontend && npm run electron:dev ) & \
	wait

test:
	cd backend && .venv/bin/python -m pytest tests -q

lint:
	cd backend && .venv/bin/ruff check app tests
	cd frontend && npm run typecheck

docker:
	docker compose up -d

clean:
	rm -rf $(VENV) frontend/node_modules node_modules_stamp frontend/dist backend/data/meridian.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
