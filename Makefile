PY ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.PHONY: setup check install seed demo s1 s2 s2-noreply s3 api web web-build console test eval

setup:              ## create .venv and install everything (Python and Node)
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install --no-audit --no-fund
	@echo "Done. Run: make console"

check:
	@$(PY) -m app.deps

install:
	$(PY) -m pip install -r requirements-dev.txt

seed: check
	$(PY) cli.py seed

demo: seed           ## all scenarios, all five claim types, auto-approver
	$(PY) cli.py run all

s1: seed
	$(PY) cli.py run s1
s2: seed
	$(PY) cli.py run s2
s2-noreply: seed     ## claimant never answers -> escalation after two rounds
	$(PY) cli.py run s2 --no-reply
s3: seed
	$(PY) cli.py run s3
interactive: seed    ## you are the approver
	$(PY) cli.py run s2 --approve interactive

api: seed           ## API only, docs at :8000/docs
	$(PY) -m uvicorn app.api.main:app --reload

web-build:
	@if [ -f frontend/dist/index.html ] && ! command -v npm >/dev/null; then echo "npm not found; using the prebuilt console"; else cd frontend && npm install --no-audit --no-fund && npm run build; fi

console: seed web-build   ## console + API on :8000 (API under /api), steps paced so you can watch
	DEMO_STEP_DELAY_MS=300 $(PY) -m uvicorn app.api.server:server --port 8000

web:                ## frontend dev server on :5173 (run `make console` or the server in another terminal)
	cd frontend && npm run dev

test:
	$(PY) -m pytest -q

eval:
	$(PY) cli.py eval
