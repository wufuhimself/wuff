VENV ?= .venv
PYTHON := $(VENV)/bin/python3
PIP := $(VENV)/bin/pip

.PHONY: install clean auth-server auth token run web gems web-dev

install:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run:
	$(PYTHON) -m app.cli

web: install
	FLASK_APP=app.web $(PYTHON) -m flask run

web-dev: install
	FLASK_APP=app.web FLASK_ENV=development $(PYTHON) -m flask run --reload

gems: web-dev

auth-server: install
	$(PYTHON) -m app.cli auth-server

token: install
	$(PYTHON) -m app.cli token $(code)

clean:
	rm -rf $(VENV)
