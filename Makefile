.PHONY: install
install:
	poetry install

.PHONY: install-pre-commit
install-pre-commit:
	poetry run pre-commit uninstall; poetry run pre-commit install

.PHONY: lint
lint:
	poetry run pre-commit run --all-files

:PHONY: startapp
startapp:
	poetry run python -m core.manage startapp ${name}

:PHONY: run-server
run-server:
	poetry run python -m core.manage runserver

.PHONY: migrate
migrate:
	poetry run python -m core.manage migrate

.PHONY: makemigrations
makemigrations:
	poetry run python -m core.manage makemigrations

.PHONY: superuser
superuser:
	poetry run python -m core.manage createsuperuser

.PHONY: shell
shell:
	poetry run python -m core.manage shell

.PHONY: import-subs
import-subs:
	poetry run python -m core.manage process_subs $(id) media/subtitles/$(file) $(if $(ep),--episode $(ep),)

.PHONY: import-trans
import-trans:
	poetry run python -m core.manage import_transcript --source $(id) --srt media/subtitles/$(file) $(if $(replace),--replace,)

.PHONY: generate-scenes
generate-scenes:
	poetry run python -m core.manage generate_scenes --source $(id) $(if $(ep),--episode $(ep),) $(if $(interval),--interval $(interval),) $(if $(replace),--replace,)

.PHONY: setup-source
setup-source: import-subs import-trans
	poetry run python -m core.manage generate_scenes --source $(id) $(if $(ep),--episode $(ep),) --replace $(if $(interval),--interval $(interval),)

.PHONY: import-words
import-words:
	poetry run python -m core.manage import_translations --source $(source) --season $(s) --episode $(e) --file media/translation/$(file)

.PHONY: import-words-all
import-words-all:
	@for f in media/translation/friends.s*.srt; do \
		ep=$$(echo $$f | sed -E 's/.*s([0-9]+)\.e([0-9]+).*/\1 \2/'); \
		s=$$(echo $$ep | cut -d' ' -f1 | sed 's/^0*//'); \
		e=$$(echo $$ep | cut -d' ' -f2 | sed 's/^0*//'); \
		echo "Importing $$f (S$${s}E$${e})..."; \
		poetry run python -m core.manage import_translations --source 1 --season $$s --episode $$e --file $$f; \
	done

.PHONY: update
update: install migrate install-pre-commit;
