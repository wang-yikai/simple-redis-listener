FROM python:3.14.2-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Expose the port the app runs on
EXPOSE 8080

# Set environment variables
ENV PORT=8080

# Run the application
CMD ["python", "main.py"]
