PYTHON ?= python3

.PHONY: test typecheck check stress soakcheck

test:
	$(PYTHON) -m unittest

typecheck:
	./scripts/dev/run_typecheck.sh

check:
	./scripts/dev/check_all.sh

stress:
	bash ./scripts/dev/run_stress_tests.sh

soakcheck:
	bash ./scripts/ops/cerbo_soak_check.sh
