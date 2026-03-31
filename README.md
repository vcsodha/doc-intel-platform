# 📄 Distributed AI Document Intelligence

A polyglot, event-driven microservices architecture built to ingest unstructured document images and use a local Vision AI model to extract structured financial data.
It features a decoupled ingestion gateway, a persistent message queue, and a reactive frontend dashboard.

This project explores enterprise-level system design, asynchronous worker pipelines, and local AI integration without relying on paid APIs.

## ✨ Features

🚀 High-concurrency file ingestion API
📡 Asynchronous task queuing and message brokering
🧠 Local Vision AI (Ollama + LLaVA) for data extraction
🖼️ Automated image sanitization via OpenCV
💾 Persistent structured data storage in PostgreSQL
📊 Reactive REST API for serving processed analytics
🖥️ Modern, dark-mode frontend dashboard
🐳 Fully Dockerized microservices mesh

## 🧠 Architecture Highlights

* Clean separation of API ingestion and background AI processing
* Event-driven pipeline utilizing a message broker to prevent bottlenecks
* Polyglot implementation matching the best tool to the specific job (Go for speed, Python for AI, Java for enterprise data serving)
* Local AI inference to ensure zero-cost scaling and complete data privacy

## 📂 Project Structure

```text
doc-intel-platform/
|--- go-gateway/        # Go API for high-speed file ingestion
|--- python-worker/     # Python script connecting OpenCV, Redis, and Ollama
|--- spring-analytics/  # Java Spring Boot API for data retrieval
|--- dashboard.html     # Vanilla JS & Tailwind UI
|--- docker-compose.yml # Infrastructure orchestration
```

## 🛠 Tech Stack

### Frontend

* HTML5 / Vanilla JavaScript

* Tailwind CSS

### Backend Microservices

* Go (Ingestion Gateway)

* Python (AI Processing Worker)

* Java / Spring Boot (Analytics API)

### AI & Data Engineering

* Ollama (LLaVA Vision Model)

* OpenCV (Image Preprocessing)

* Redis (Message Broker / Queue)

* PostgreSQL (Relational Database)

### DevOps

* Docker

* Docker Compose

## 🎯 Project Motivation

This project was built to:

Learn how to architect and orchestrate a polyglot microservices mesh

Design decoupled, event-driven backend systems

Work with state-of-the-art local multimodal LLMs (Vision AI)

Practice full-stack integration from file upload to data visualization


## ⚙️ Core Components

1️⃣ Ingestion Gateway (Go)

Accepts multipart file uploads, writes the raw file to a shared Docker volume, and pushes a task UUID to the Redis queue. Returns an immediate 202 Accepted to the client.

2️⃣ Message Broker (Redis)

Acts as the asynchronous buffer, decoupling the fast web gateway from the slower, resource-intensive AI processing worker.

3️⃣ AI Processing Worker (Python)

Constantly polls Redis for new tasks. Uses OpenCV to clean the image, prompts the local LLaVA model to extract a strict JSON payload (Vendor, Total, Date), and inserts the result into PostgreSQL.

4️⃣ Analytics API (Java)

Serves the structured database records via a clean REST endpoint to be consumed by the frontend dashboard.


## 🚀 Getting Started (Local)

1️⃣ Install and Start Ollama

Ensure Ollama is installed on your host machine.

Bash
ollama serve

2️⃣ Download the Vision Model

```
ollama pull llava
```

3️⃣ Clone the repository

```
git clone https://github.com/vcsodha/doc-intel-platform.git
```

4️⃣ Spin up the infrastructure

```
docker compose up -d --build
```

5️⃣ Open the Dashboard

Open dashboard.html in your web browser to upload receipts and watch the data flow.

## 🔮 Roadmap

* 🛡️ Implement a Dead Letter Queue (DLQ) in Redis for failed image processing

* 🔄 Add Server-Sent Events (SSE) to the dashboard for live table updates without refreshing

* ⚙️ Create a GitHub Actions CI/CD pipeline for automated testing

## 📸 Screenshot

<img width="500" height="500" alt="Screenshot 2026-03-31 at 13 29 47" src="https://github.com/user-attachments/assets/d667bb15-63c2-4992-b769-55e1964bd779" />


## 🧑‍💻 Author

Vidisha Sodha

Software Engineer • AI Engineer

Built as a hands-on project exploring distributed systems, AI integration, and enterprise backend architecture.
