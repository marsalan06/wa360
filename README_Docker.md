# WhatsApp 360dialog Integration - Docker Setup

## Quick Start

1. **Clone and navigate to project directory**
2. **Create environment file** (copy from .env.example if needed)
3. **Run with Docker Compose**

```bash
# Build and start all services
docker-compose up --build

# Run in background
docker-compose up -d --build
```

## Services

- **web**: Django application (port 8000)
- **db**: PostgreSQL database (port 5432)
- **rabbitmq**: RabbitMQ message broker (ports 5672, 15672)
- **celery**: Background task worker
- **celery-beat**: Periodic task scheduler

## Environment Variables

Create a `.env` file with:

```env
# Database
DB_NAME=wa360
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=db
DB_PORT=5432

# RabbitMQ
RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672//
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest

# WhatsApp
D360_ENCRYPTION_KEY=your_key_here
D360_WEBHOOK_URL=https://your-ngrok-url.ngrok.io/webhook/

# Django
DEBUG=1
SECRET_KEY=your-secret-key
```

## Commands

```bash
# View logs
docker-compose logs -f

# Run Django commands
docker-compose exec web python manage.py migrate
docker-compose exec web python manage.py createsuperuser

# Access Django shell
docker-compose exec web python manage.py shell

# Stop services
docker-compose down

#restart on change in ngrok
docker-compose restart web celery celery-beat

# Stop containers completely
docker-compose down web celery celery-beat

# Start them fresh  
docker-compose up -d web celery celery-beat
```

## Features

- **Periodic Messaging**: Automated client outreach via Celery Beat
- **AI Summarization**: Background LLM processing
- **Scalable**: RabbitMQ + Celery for task queuing
- **Production Ready**: PostgreSQL + proper logging
- **Management UI**: RabbitMQ management interface at http://localhost:15672
