"""
Redis keyspace listener: subscribes to key expiration events and calls the
GCP Cloud Function for each expired key.
Requires: REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, GCP_FUNCTION_URL.
"""
from __future__ import annotations

import logging
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

import google.cloud.logging  # type: ignore
import redis  # type: ignore
import requests  # type: ignore

# Initialize Google Cloud Logging
client = google.cloud.logging.Client()
client.setup_logging()
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        logger.error("Missing required env: %s", name)
        sys.exit(1)
    return value


def listen_for_expirations(
    redis_host: str,
    redis_port: int,
    redis_password: str | None,
    gcp_function_url: str,
) -> None:
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        decode_responses=True,
    )
    pubsub = redis_client.pubsub()
    pubsub.psubscribe("__keyevent@0__:expired")

    logger.info("Listening for expired keys...")
    for message in pubsub.listen():
        if message["type"] == "pmessage":
            expired_key: str = message["data"]
            logger.info("Key expired: %s", expired_key)

            try:
                response = requests.post(
                    gcp_function_url,
                    json={"expired_key": expired_key},
                    timeout=30,
                )
                logger.info("Called function: %s", response.status_code)
            except requests.RequestException as exc:
                logger.error("Error calling GCP function: %s", exc)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks."""

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Listening for Redis events")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, _format, *args):
        """Suppress default HTTP server logging."""
        # Suppress default HTTP server logging - we use Cloud Logging instead
        # Parameter name uses underscore prefix to indicate intentionally unused


def run_health_server(server_port: int) -> None:
    """Run a simple HTTP server for health checks."""
    server = HTTPServer(("0.0.0.0", server_port), HealthCheckHandler)
    logger.info("Health check server started on port %s", server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Health check server shutting down")
        server.shutdown()


if __name__ == "__main__":
    # Validate all required environment variables upfront
    redis_host = _require_env("REDIS_HOST")
    redis_port_str = _require_env("REDIS_PORT")
    try:
        redis_port = int(redis_port_str)
    except ValueError:
        logger.error("REDIS_PORT must be a valid integer, got: %s", redis_port_str)
        sys.exit(1)

    # Optional - some Redis instances don't require passwords
    redis_password = os.getenv("REDIS_PASSWORD")

    gcp_function_url = _require_env("GCP_FUNCTION_URL")

    logger.info("Starting Redis keyspace listener service")

    port = int(os.getenv("PORT", "8080"))

    # Start health check server in a daemon thread
    # This allows Cloud Run to keep the container alive via health checks
    health_thread = Thread(target=run_health_server, args=(port,), daemon=True)
    health_thread.start()

    # Run Redis listener in main thread (this is the primary function)
    # This ensures the listener keeps running even if health check thread has issues
    logger.info("Starting Redis expiration listener...")
    listen_for_expirations(redis_host, redis_port, redis_password, gcp_function_url)
