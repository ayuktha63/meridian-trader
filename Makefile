.PHONY: setup backend frontend dev desktop test lint clean docker

PY ?= python3
VENV := backend/.venv
PYBIN := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

setup: $(VENV) node_modules_stamp
	@echo ""
	@echo "  Setup complete."
	@echo "  1. cp .env.example .env   (optional: add ANTHROPIC_API_KEY)"
	@echo "  2. make dev"
	@echo ""

$(VENV):
	$(PY) -m venv $(VENV)
	$(PIP) install --upgrade pip wheel setuptools
	$(PIP) install -r backend/requirements.txt

node_modules_stamp:
	cd frontend && npm install
	@touch node_modules_stamp

backend:
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

# Runs API + UI together; Ctrl-C stops both.
dev:
	@trap 'kill 0' INT TERM; \
	( cd backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 ) & \
	( cd frontend && npm run dev ) & \
	wait

desktop:
	@trap 'kill 0' INT TERM; \
	( cd backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 ) & \
	( cd frontend && npm run electron:dev ) & \
	wait

test:
	cd backend && .venv/bin/python -m pytest tests -q

lint:
	cd frontend && npm run typecheck

docker:
	docker compose up -d

clean:
	rm -rf $(VENV) frontend/node_modules node_modules_stamp frontend/dist backend/data/meridian.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
