FROM python:3.13-slim

# Build arguments
ARG BUILD_ENV=production
ARG BUILD_VERSION=1.0.0

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Set working directory to the root of the project
WORKDIR /app

# Copy requirements first for better caching
COPY apps/user_service/requirements.txt ./requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project structure
COPY . .

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV BUILD_ENV=${BUILD_ENV}
ENV BUILD_VERSION=${BUILD_VERSION}

# Expose port
EXPOSE 5000

# Start the FastAPI app - assuming main.py is in apps/user_service/app/
#changed
CMD ["uvicorn", "apps.user_service.app.main:app", "--host", "0.0.0.0", "--port", "5000", "--log-level", "info", "--access-log"]
