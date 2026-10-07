.PHONY: install seed demo s1 s2 s2-noreply s3 api web web-build console test eval

install:
	pip install -r requirements-dev.txt

seed:
	python cli.py seed

demo: seed           ## all scenarios, all five claim types, auto-approver
	python cli.py run all

s1: seed
	python cli.py run s1
s2: seed
	python cli.py run s2
s2-noreply: seed     ## claimant never answers -> escalation after two rounds
	python cli.py run s2 --no-reply
s3: seed
	python cli.py run s3
interactive: seed    ## you are the approver
	python cli.py run s2 --approve interactive

api: seed           ## API only, docs at :8000/docs
	uvicorn app.api.main:app --reload

web-build:
	cd frontend && npm install && npm run build

console: seed web-build   ## console + API on :8000 (API under /api), steps paced so you can watch
	DEMO_STEP_DELAY_MS=300 uvicorn app.api.server:server --port 8000

web:                ## frontend dev server on :5173 (run `make console` or the server in another terminal)
	cd frontend && npm run dev

test:
	python -m pytest -q

eval:
	python cli.py eval
