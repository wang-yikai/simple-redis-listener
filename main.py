"""
Redis keyspace listener: subscribes to key expiration events and calls the
GCP Cloud Function for each expired key.

Optimized for Google Compute Engine deployment with systemd.

Requires env vars:
  REDIS_HOST, REDIS_PORT, REDIS_PASSWORD (optional),
  GCP_FUNCTION_URL, ON_KEY_EXPIRED_SECRET.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
from threading import Event

import redis  # type: ignore
import requests  # type: ignore
from requests.adapters import HTTPAdapter  # type: ignore
from urllib3.util.retry import Retry  # type: ignore

# ---------------------------------------------------------------------------
# Logging - simple console logging for systemd/journald
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunable constants
# ---------------------------------------------------------------------------
RECONNECT_BASE_DELAY = 1        # Initial retry delay (seconds) after a Redis disconnect
RECONNECT_MAX_DELAY = 60        # Cap for exponential backoff (seconds)
GCP_CALL_RETRIES = 3            # Number of automatic retries for the Cloud Function call
GCP_CALL_BACKOFF_FACTOR = 0.5   # urllib3 backoff factor between retries (0.5 -> 0.5s, 1s, 2s)
GCP_CALL_TIMEOUT = 30           # Per-request timeout for the Cloud Function call (seconds)
REDIS_SOCKET_TIMEOUT = 30       # Timeout on blocking Redis read operations (seconds)
REDIS_SOCKET_CONNECT_TIMEOUT = 10  # Timeout for the initial TCP connection to Redis (seconds)
REDIS_HEALTH_CHECK_INTERVAL = 15   # How often redis-py sends a PING to detect stale connections (seconds)

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
# Set by signal handlers (SIGTERM / SIGINT) to break the listener loop cleanly
shutdown_event = Event()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _require_env(name: str) -> str:
    """Return an env var's value or exit immediately if it's missing."""
    value = os.getenv(name)
    if not value:
        logger.error("Missing required env: %s", name)
        sys.exit(1)
    return value


def _parse_int_env(name: str, value: str) -> int:
    """Parse a string as an integer or exit with a clear error message."""
    try:
        return int(value)
    except ValueError:
        logger.error("%s must be a valid integer, got: %s", name, value)
        sys.exit(1)


def _build_http_session() -> requests.Session:
    """
    Build a requests Session with automatic retry on transient failures.

    Retries on 429 (rate-limit) and 5xx server errors with exponential backoff
    so a momentary Cloud Function hiccup doesn't lose the event.
    """
    session = requests.Session()
    retries = Retry(
        total=GCP_CALL_RETRIES,
        backoff_factor=GCP_CALL_BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _handle_expired_key(
    session: requests.Session,
    expired_key: str,
    gcp_function_url: str,
    on_key_expired_secret: str,
) -> None:
    """POST the expired key to the GCP Cloud Function (with built-in retries)."""
    try:
        response = session.post(
            gcp_function_url,
            json={"expired_key": expired_key},
            headers={"x-on-key-expired-secret": on_key_expired_secret},
            timeout=GCP_CALL_TIMEOUT,
        )
        response.raise_for_status()
        logger.info("Called function for key '%s': %s", expired_key, response.status_code)
    except requests.RequestException as exc:
        logger.error("Error calling GCP function for key '%s': %s", expired_key, exc)


# ---------------------------------------------------------------------------
# Core listener - runs in the main thread
# ---------------------------------------------------------------------------
def listen_for_expirations(
    redis_host: str,
    redis_port: int,
    redis_password: str | None,
    gcp_function_url: str,
    on_key_expired_secret: str,
) -> None:
    """
    Subscribe to Redis keyspace expiration events and forward them to the
    Cloud Function. Automatically reconnects with exponential backoff if the
    connection drops (network blip, Redis restart, idle timeout, etc.).
    """
    session = _build_http_session()
    delay = RECONNECT_BASE_DELAY

    # Outer loop: keeps reconnecting until a shutdown signal is received
    while not shutdown_event.is_set():
        pubsub = None
        redis_client = None
        try:
            # --- Connect ---------------------------------------------------
            redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password,
                decode_responses=True,
                socket_timeout=REDIS_SOCKET_TIMEOUT,
                socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
                # Periodically PINGs Redis so stale connections are detected
                # before a real message is missed
                health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
                retry_on_timeout=True,
            )
            # Verify the connection is actually alive before subscribing
            redis_client.ping()
            delay = RECONNECT_BASE_DELAY  # Reset backoff on successful connect
            logger.info("Connected to Redis at %s:%s", redis_host, redis_port)

            # --- Subscribe -------------------------------------------------
            pubsub = redis_client.pubsub()
            # __keyevent@0__:expired fires for every key that expires in DB 0.
            # Requires Redis to have notify-keyspace-events set to include "Ex".
            pubsub.psubscribe("__keyevent@0__:expired")
            logger.info("Subscribed - listening for expired keys...")

            # --- Listen (blocks until disconnect or shutdown) --------------
            for message in pubsub.listen():
                if shutdown_event.is_set():
                    break
                # pubsub.listen() yields subscribe confirmations, pongs, etc.
                # We only care about actual pattern-matched messages.
                if message["type"] == "pmessage":
                    expired_key: str = message["data"]
                    logger.info("Key expired: %s", expired_key)
                    _handle_expired_key(
                        session, expired_key, gcp_function_url, on_key_expired_secret
                    )

        except (redis.ConnectionError, redis.TimeoutError, OSError) as exc:
            logger.warning("Redis connection lost: %s - retrying in %ss", exc, delay)
        except Exception as exc:
            logger.exception("Unexpected error in listener: %s", exc)
        finally:
            # Always clean up the subscription and connection so we don't leak
            # file descriptors across reconnection cycles
            if pubsub:
                try:
                    pubsub.punsubscribe()
                    pubsub.close()
                except Exception as e:
                    logger.debug("Error during pubsub cleanup: %s", e)
            if redis_client:
                try:
                    redis_client.close()
                except Exception as e:
                    logger.debug("Error during Redis client cleanup: %s", e)

        # --- Back off before reconnecting ----------------------------------
        if not shutdown_event.is_set():
            # .wait() returns immediately if the event is set (i.e. shutdown),
            # otherwise sleeps for `delay` seconds - avoids tight-looping.
            shutdown_event.wait(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    logger.info("Listener stopped")


# ---------------------------------------------------------------------------
# Signal handling - lets systemd (SIGTERM) and Ctrl-C (SIGINT) trigger a
# clean shutdown instead of an abrupt kill.
# ---------------------------------------------------------------------------
def _shutdown_handler(signum, _frame):
    try:
        sig_name = signal.Signals(signum).name
    except ValueError:
        sig_name = str(signum)
    logger.info("Received %s - shutting down gracefully", sig_name)
    shutdown_event.set()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Register signal handlers before doing anything else so we can catch
    # signals even during startup
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # Validate required environment variables upfront - fail fast
    redis_host = _require_env("REDIS_HOST")
    redis_port = _parse_int_env("REDIS_PORT", _require_env("REDIS_PORT"))
    redis_password = os.getenv("REDIS_PASSWORD")  # Optional - some Redis instances have no auth
    gcp_function_url = _require_env("GCP_FUNCTION_URL")
    on_key_expired_secret = _require_env("ON_KEY_EXPIRED_SECRET")

    logger.info("Starting Redis keyspace listener service")
    logger.info("Starting Redis expiration listener...")
    
    # The Redis listener runs in the main thread
    # If it crashes, systemd will restart it based on the service configuration
    listen_for_expirations(
        redis_host,
        redis_port,
        redis_password,
        gcp_function_url,
        on_key_expired_secret,
    )
    
    logger.info("Service shut down")