.PHONY: verify doctor test export-plan export-candidate export-verify clean-runtime

PYTHON ?= python3
EXPORT_TARGET ?= /tmp/wissenswerk-public-candidate

verify:
	$(PYTHON) -m py_compile wissenswerk.py
	./wissenswerk.py doctor --json
	./wissenswerk.py export plan --strict --json
	./wissenswerk.py test --json
	git diff --check

doctor:
	./wissenswerk.py doctor --json

test:
	./wissenswerk.py test --json

export-plan:
	./wissenswerk.py export plan --strict --json

export-candidate:
	./wissenswerk.py export materialize --target "$(EXPORT_TARGET)" --apply --json

export-verify:
	./wissenswerk.py export verify --target "$(EXPORT_TARGET)" --json

clean-runtime:
	./wissenswerk.py reset generated --dry-run --json
	./wissenswerk.py reset index --dry-run --json
