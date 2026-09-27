PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help install ingest app test lint clean

help:
	@echo "install  create venv and install dependencies"
	@echo "ingest   build the Chroma vector store from data/corpus (run once after clone)"
	@echo "app      start the Streamlit UI"
	@echo "test     run the pytest suite"
	@echo "lint     run ruff over the repo"
	@echo "clean    remove caches and the local vector store"

install:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r requirements.txt

ingest:
	$(BIN)/python -m src.ingest.pipeline --rebuild

app:
	$(BIN)/streamlit run src/app.py

test:
	$(BIN)/pytest

lint:
	$(BIN)/ruff check .

clean:
	rm -rf $(VENV)/.pytest_cache .pytest_cache .ruff_cache
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -print0 | xargs -0 rm -rf
	rm -rf data/chroma
