:PHONY: install
install:
	poetry install

:PHONY: run-server
run-server:
	poetry run python -m core.manage runserver

:PHONY: migrate
migrate:
	poetry run python -m core.manage migrate

:PHONY: makemigrations
makemigrations:
	poetry run python -m core.manage makemigrations

:PHONY: superuser
superuser:
	poetry run python -m core.manage createsuperuser

:PHONY: shell
shell:
	poetry run python -m core.manage shell

:PHONY: update
update: install migrate;
