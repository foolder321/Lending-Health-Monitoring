FROM python:3.11-slim

# Create working directory
WORKDIR /app

# Copy requirements first for caching
COPY project/requirements.txt ./requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY project/app ./app
COPY project/.env.example ./

# Ensure the runtime directory for SQLite exists
RUN mkdir -p /app/data

# Expose no specific ports – this application communicates outbound only

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Default command runs the monitoring application
CMD ["python", "app/main.py"]