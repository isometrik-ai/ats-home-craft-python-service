FROM python:3.13-slim

# Add Maintainer Info
LABEL maintainer="LegalAI Team"
LABEL service="legalai-fast-api"

# Build arguments
ARG BUILD_ENV=production
ARG BUILD_VERSION=1.0.0

# Set working directory to the root of the project
WORKDIR /app

# Copy requirements first for better caching
COPY apps/user_service/requirements.txt ./requirements.txt

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project structure
COPY . .

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENV}
ENV BUILD_VERSION=${BUILD_VERSION}

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Start the FastAPI app
# CMD ["uvicorn", "apps.user_service.app.main:app", "--host", "0.0.0.0", "--port", "5000", "--log-level", "info", "--access-log"]
CMD ["gunicorn", "apps.user_service.app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:5000", "--log-level", "info", "--capture-output", "--access-logfile", "/dev/null", "--error-logfile", "-"]
