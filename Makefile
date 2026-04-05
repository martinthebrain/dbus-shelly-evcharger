PYTHON ?= python3

.PHONY: test typecheck check stress soakcheck

test:
	$(PYTHON) -m unittest

typecheck:
	./run_typecheck.sh

check:
	./check_all.sh

stress:
	bash ./run_stress_tests.sh

soakcheck:
	bash ./cerbo_soak_check.sh
