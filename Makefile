.PHONY: verify doctor test clean-runtime

PYTHON ?= python3

verify:
	$(PYTHON) -m py_compile wissenswerk.py
	./wissenswerk.py doctor --json
	./wissenswerk.py test --json
	./wissenswerk.py design lint --json
	./wissenswerk.py hygiene reports --json
	git diff --check

doctor:
	./wissenswerk.py doctor --json

test:
	./wissenswerk.py test --json

clean-runtime:
	./wissenswerk.py reset generated --dry-run --json
	./wissenswerk.py reset index --dry-run --json
