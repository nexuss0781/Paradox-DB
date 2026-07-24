.PHONY: install build typecheck lint validate-schemas

install:
	npm install

build:
	npx tsc

typecheck:
	npx tsc --noEmit

lint:
	npx eslint src/

validate-schemas:
	node scripts/validate-schemas.js
