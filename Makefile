.PHONY: dev install lint typecheck test format clean up down

dev:
	docker compose up -d
	cd client && bun run dev &
	cd gateway && make dev &

install:
	cd client && bun install
	cd gateway && make install
	cd shared && npm install

lint:
	cd client && bun run lint
	cd gateway && make lint

typecheck:
	cd client && bun run typecheck
	cd gateway && make typecheck

test:
	cd client && bun test
	cd gateway && make test
	cd shared && npm run validate-schemas

format:
	cd client && bun run format
	cd gateway && make format

clean:
	rm -rf client/node_modules client/dist
	rm -rf gateway/.mypy_cache gateway/__pycache__
	rm -rf shared/node_modules shared/dist

up:
	docker compose up -d

down:
	docker compose down
