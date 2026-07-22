VENV ?= .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: install clean auth-server auth token run cli web web-debug gems

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# CLI commands
cli: install
	$(PYTHON) -m app.cli

run: cli

# Web server targets
web: install
	FLASK_APP=app.web $(PYTHON) -m flask run --port 5001

web-debug: install
	FLASK_APP=app.web FLASK_ENV=development $(PYTHON) -m flask run --port 5001 --reload

# Alias for backwards compatibility
gems: web-debug

# Auth
auth-server: install
	$(PYTHON) -m app.cli auth-server

token: install
	$(PYTHON) -m app.cli token $(code)

clean:
	rm -rf $(VENV)
