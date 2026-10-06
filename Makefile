.PHONY: install seed demo s1 s2 s2-noreply s3 api test eval

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

api: seed
	uvicorn app.api.main:app --reload

test:
	python -m pytest -q

eval:
	python cli.py eval
