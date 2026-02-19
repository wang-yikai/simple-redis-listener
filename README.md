# simple-redis-listener

A Redis keyspace listener service that subscribes to key expiration events and calls a Google Cloud Function when keys expire. Deployed on Google Cloud Run for cost-effective, serverless operation.

## Overview

This service listens for Redis key expiration events using Redis keyspace notifications and automatically triggers a GCP Cloud Function for each expired key. It's designed to run continuously as a lightweight containerized service.

**Architecture:**
- The Redis listener runs in the main thread (primary function) to ensure continuous operation
- A lightweight HTTP health check server runs in a daemon thread on port 8080 for Cloud Run health checks
- This design ensures the Redis listener keeps running even if the health check thread has issues

## Prerequisites

- Google Cloud Platform account
- Redis instance (can be Google Cloud Memorystore, Redis Cloud, or self-hosted)
- GCP Cloud Function endpoint URL
- Docker (for local testing, optional)

## Local Development

### Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd simple-redis-listener
```

2. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Set environment variables:
```bash
export REDIS_HOST=your-redis-host
export REDIS_PORT=6379
export REDIS_PASSWORD=your-redis-password  # Optional
export GCP_FUNCTION_URL=https://your-function-url.run.app
export ON_KEY_EXPIRED_SECRET=your-secret-key  # Required - must match Firebase function's ON_KEY_EXPIRED_SECRET
export PORT=8080  # Optional, defaults to 8080
```

5. Run the service:
```bash
python main.py
```

### Testing Locally with Docker

1. Build the Docker image:
```bash
docker build -t redis-listener .
```

2. Run the container:
```bash
docker run -p 8080:8080 \
  -e REDIS_HOST=your-redis-host \
  -e REDIS_PORT=6379 \
  -e REDIS_PASSWORD=your-redis-password \
  -e GCP_FUNCTION_URL=https://your-function-url.run.app \
  -e ON_KEY_EXPIRED_SECRET=your-secret-key \
  redis-listener
```

## Google Cloud Run Deployment

### Cost Considerations

**Free Tier (Monthly):**
- 180,000 vCPU-seconds
- 360,000 GiB-seconds
- 2 million requests

**Recommended Configuration:**
- **Billing:** Request-based (charged only when processing requests, CPU limited outside requests)
- **Minimum instances:** **1** (required - see important note below)
- **Maximum instances:** Leave empty or set based on expected load
- **CPU:** 1 vCPU (default, sufficient for Redis pub/sub)
- **Memory:** 512 MiB (default, can be reduced to 256 MiB if needed)

**⚠️ Important:** The Redis listener must run continuously to process expiration events. Setting minimum instances to 0 will cause the service to scale to zero when idle, which means:
- The Redis listener will stop running
- Expired keys will NOT be processed until a request wakes the service
- This defeats the purpose of the listener

**Therefore, you MUST set minimum instances to 1** to keep the Redis listener running continuously. This will incur continuous costs (approximately $0.00002400 per vCPU-second and $0.00000250 per GiB-second), but it's necessary for the service to function correctly.

### Step-by-Step Deployment via Google Cloud Console

#### 1. Enable Required APIs

1. Navigate to [APIs & Services](https://console.cloud.google.com/apis/library)
2. Enable the following APIs:
   - Cloud Run API
   - Cloud Build API
   - Cloud Logging API

#### 2. Navigate to Cloud Run

1. Go to [Cloud Run Console](https://console.cloud.google.com/run)
2. Click **"Create service"**

#### 3. Configure Service Settings

**Authentication:**
- Select **"Allow public access"** (for now, to allow health checks and simplify access)
  - The health endpoint (`/` or `/health`) will be publicly accessible
  - You can change this later if you need to restrict access
- Alternatively, if you prefer authentication:
  - Select **"Require authentication"**
  - Check **"Identity and Access Management (IAM)"** (for service-to-service communication)
  - Do NOT check "Identity Aware Proxy (IAP)" (not needed for this service)

**Billing:**
- Select **"Request-based"** (charged only when processing requests, CPU limited outside requests)

**Service Scaling:**
- Select **"Auto scaling"**
- **Minimum number of instances:** **`1`** (required - the Redis listener must run continuously)
- **Maximum number of instances:** Leave empty or set based on expected load
- **⚠️ Important:** Do NOT set minimum to 0. The Redis listener needs to be running continuously to process expiration events. If the service scales to zero, expired keys will not be processed.

**Ingress:**
- Select **"All"** (Allow direct access to your service from the internet)
  - This allows the service to connect to external Redis instances and call Cloud Functions

**Container Configuration:**
- **Container port:** `8080`
- **Note:** The service listens on the `$PORT` environment variable (defaults to 8080)

#### 4. Configure Container Source

In the **"Containers"** tab:

1. **Source repository:** Click "Select" and choose your source:
   - If using Cloud Source Repositories: Select your repository
   - If using GitHub: Connect your repository
   - If using a container image: Select "Deploy one revision from an existing container image" and provide the image URL

2. **Cloud Build trigger:** A Cloud Build trigger will be created automatically to build and deploy your code

3. **Container port:** Set to `8080`
   - Note: The service listens on the `$PORT` environment variable (defaults to 8080)

#### 5. Set Environment Variables

Click on the **"Variables & Secrets"** tab under container settings:

**Required Environment Variables:**
- `REDIS_HOST`: Your Redis host (e.g., `redis-13492.c238.us-central1-2.gce.cloud.redislabs.com`)
- `REDIS_PORT`: Redis port (e.g., `13492`)
- `REDIS_PASSWORD`: Redis password (if required)
- `GCP_FUNCTION_URL`: Full URL of your Cloud Function endpoint (e.g., `https://your-function-name-xxxxx.run.app`)
- `ON_KEY_EXPIRED_SECRET`: Secret key that must match the `ON_KEY_EXPIRED_SECRET` parameter in your Firebase function. This is sent in the `x-on-key-expired-secret` header for authentication.

**Optional Environment Variables:**
- `PORT`: Port to listen on (defaults to 8080, should match container port)

#### 6. Configure Resource Limits (Optional)

Click on the **"Settings"** tab:
- **CPU:** 1 vCPU (default, sufficient for Redis pub/sub)
- **Memory:** 512 MiB (default, can be reduced to 256 MiB for cost savings)
- **Timeout:** 300 seconds (default, adjust if needed)
- **Concurrency:** 80 (default, adjust based on load)

#### 7. Deploy

Click **"Create"** at the bottom of the page to deploy the service.

### Post-Deployment

1. **Get the Service URL:**
   - After deployment, you'll see the service URL displayed (e.g., `https://redis-listener-xxxxx.us-central1.run.app`)
   - You can also find it in the Cloud Run service list by clicking on your service name

2. **Test the Health Endpoint:**
   - The service URL is shown at the top of the service details page
   - You can test it by visiting the URL in your browser or using a tool like `curl`
   - Example: `https://your-service-url.run.app/` should return "Listening for Redis events"

3. **Check Logs:**
   - Navigate to your service in the Cloud Run console
   - Click on the **"Logs"** tab to view real-time logs
   - Logs are automatically sent to Google Cloud Logging

### IAM Permissions

The service needs permissions to:
- Write logs to Cloud Logging (automatically granted to Cloud Run services)
- Call your Cloud Function (you need to grant the Cloud Run service account permission to invoke your Cloud Function)

**Grant Cloud Function Invocation Permission via Console:**

1. Navigate to your Cloud Function in the [Cloud Functions Console](https://console.cloud.google.com/functions)
2. Click on your function name
3. Go to the **"Permissions"** tab
4. Click **"Add Principal"**
5. In the "New principals" field, enter the Cloud Run service account:
   - Format: `PROJECT_NUMBER-compute@developer.gserviceaccount.com`
   - To find your project number: Go to [Project Settings](https://console.cloud.google.com/iam-admin/settings) and look for "Project number"
6. Select the role: **"Cloud Functions Invoker"**
7. Click **"Save"**

**Alternative: Find the Service Account from Cloud Run:**
1. In your Cloud Run service, go to the **"Security"** tab
2. Look for "Service account" - this shows the service account being used
3. Use this service account email when granting permissions to your Cloud Function

## Redis Configuration

Your Redis instance must have keyspace notifications enabled:

```bash
# Enable keyspace notifications for expired keys
redis-cli CONFIG SET notify-keyspace-events Ex
```

Or add to your Redis configuration file:
```
notify-keyspace-events Ex
```

## Monitoring

### View Logs

All logs are automatically sent to Google Cloud Logging. View them via:

1. **Cloud Run Console:**
   - Navigate to [Cloud Run](https://console.cloud.google.com/run)
   - Click on your service name
   - Click on the **"Logs"** tab to view real-time logs

2. **Cloud Logging Console:**
   - Navigate to [Cloud Logging](https://console.cloud.google.com/logs)
   - Filter by resource type: `cloud_run_revision`
   - Filter by service name: Your service name

### Key Metrics to Monitor

- **Request count:** Number of expired keys processed
- **Error rate:** Failed Cloud Function calls
- **Latency:** Time to process expired keys
- **Instance count:** Number of running instances (should stay at 1 for continuous operation)
- **Redis connection status:** Check logs to ensure Redis connection is maintained

### Set Up Alerts

Create alerts in Cloud Monitoring for:
- High error rates
- Service unavailability
- Unusual request patterns

## Troubleshooting

### Service Not Starting

1. Check logs for missing environment variables
2. Verify Redis connection (check REDIS_HOST, REDIS_PORT, REDIS_PASSWORD)
3. Ensure Redis keyspace notifications are enabled

### Not Receiving Expiration Events

1. Verify Redis configuration: `CONFIG GET notify-keyspace-events` should include `Ex`
2. Check Redis connection logs
3. Verify the service is running and connected to Redis

### Cloud Function Not Being Called

1. Check GCP_FUNCTION_URL is correct
2. Verify IAM permissions for Cloud Function invocation
3. Check Cloud Function logs for errors
4. Verify network connectivity (service can reach Cloud Function)

### High Costs

1. Review instance count (should be 1 for continuous operation)
2. Verify minimum instances is set to 1 (required for Redis listener to work)
3. Verify request-based billing is enabled
4. Monitor vCPU-seconds and GiB-seconds usage
5. Consider reducing memory allocation if possible (e.g., from 512 MiB to 256 MiB)

**Note:** With minimum instances = 1, you will incur continuous costs. This is necessary for the Redis listener to function. Estimated monthly cost for 1 vCPU + 512 MiB running 24/7: approximately $62-65/month (varies by region).

## Architecture

```
Redis Instance → Redis Keyspace Notifications → Cloud Run Service → GCP Cloud Function
```

### Service Architecture

The service uses a dual-threaded architecture:

1. **Main Thread (Primary):** Runs the Redis expiration listener
   - Subscribes to Redis keyspace notifications (`__keyevent@0__:expired`)
   - Continuously listens for key expiration events
   - Calls the configured GCP Cloud Function when keys expire
   - This thread must remain running for the service to function

2. **Daemon Thread (Secondary):** Runs a lightweight HTTP health check server
   - Listens on port 8080 for health check requests
   - Responds to `GET /` and `GET /health` endpoints
   - Allows Cloud Run to monitor service health
   - Runs as a daemon thread so it doesn't block shutdown

### Deployment and Revision Updates

When you deploy a new revision:
1. Cloud Run starts a new container instance with the updated code
2. The old container receives a termination signal (SIGTERM)
3. The old container has a grace period (default 10 seconds) to shut down gracefully
4. The Redis listener thread in the old container stops when the container terminates
5. The new container starts fresh with the new code and new threads
6. There may be a brief gap (seconds) during the transition where expired keys are not processed

**Best Practice:** Deploy during low-traffic periods to minimize missed expiration events during the transition.
