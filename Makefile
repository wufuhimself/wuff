VENV ?= .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: install clean auth-server auth token run cli web web-debug web-network gems lint install-dev

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

install-dev:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

lint: install-dev
	$(VENV)/bin/pylint app

# CLI commands
cli: install
	$(PYTHON) -m app.cli

run: cli

# Web server targets
web: install
	@echo "Running at http://127.0.0.1:5001"
	FLASK_APP=app.web $(PYTHON) -m flask run --port 5001

web-debug: install
	FLASK_APP=app.web FLASK_ENV=development $(PYTHON) -m flask run --port 5001 --reload

web-network: install
	FLASK_APP=app.web FLASK_ENV=development $(PYTHON) -m flask run --host 0.0.0.0 --port 5001 --reload

# Alias for backwards compatibility
gems: web-debug

# Auth
auth-server: install
	$(PYTHON) -m app.cli auth-server

token: install
	$(PYTHON) -m app.cli token $(code)

clean:
	rm -rf $(VENV)
