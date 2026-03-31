.PHONY: up down build logs clean

up:
	docker-compose up -d

build:
	docker-compose up --build -d

down:
	docker-compose down

logs:
	docker-compose logs -f

clean:
	docker-compose down -v
	rm -rf shared_data/*
