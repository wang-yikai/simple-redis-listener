# simple-redis-listener

A lightweight Redis keyspace listener that monitors key expirations and triggers Google Cloud Functions. Runs on Google Compute Engine for reliable 24/7 operation.

## Quick Deploy

### 1. Enable Redis Keyspace Notifications
```bash
redis-cli CONFIG SET notify-keyspace-events Ex
```

### 2. Create Compute Engine VM
```bash
gcloud compute instances create redis-listener \
  --machine-type=e2-micro \
  --zone=us-central1-a \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --scopes=cloud-platform
```

### 3. SSH and Install
```bash
# SSH into VM
gcloud compute ssh redis-listener --zone=us-central1-a

# Install dependencies
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git

# Clone repository
cd /home/$USER
git clone https://github.com/wang-yikai/simple-redis-listener.git
cd simple-redis-listener

# Setup virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure Systemd Service
```bash
sudo nano /etc/systemd/system/redis-listener.service
```

Paste this configuration (replace `YOUR_USERNAME` and environment values):
```ini
[Unit]
Description=Redis Keyspace Listener
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/simple-redis-listener
Environment="REDIS_HOST=your-redis-host.com"
Environment="REDIS_PORT=6379"
Environment="REDIS_PASSWORD=your-password"
Environment="GCP_FUNCTION_URL=https://your-function-url.cloudfunctions.net/function"
Environment="ON_KEY_EXPIRED_SECRET=your-secret-key"
ExecStart=/home/YOUR_USERNAME/simple-redis-listener/venv/bin/python /home/YOUR_USERNAME/simple-redis-listener/main.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Replace:**
- `YOUR_USERNAME` - Run `whoami` to find your username
- `REDIS_HOST` - Your Redis hostname
- `REDIS_PORT` - Your Redis port (usually 6379)
- `REDIS_PASSWORD` - Your Redis password (remove line or set to empty string if no auth)
- `GCP_FUNCTION_URL` - Your Cloud Function URL
- `ON_KEY_EXPIRED_SECRET` - Secret key for function authentication

### 5. Start the Service
```bash
sudo systemctl daemon-reload
sudo systemctl enable redis-listener
sudo systemctl start redis-listener

# Check status
sudo systemctl status redis-listener

# View logs
sudo journalctl -u redis-listener -f
```

### 6. Test It Works
```bash
# Set a key that expires in 5 seconds
redis-cli -h YOUR_REDIS_HOST -p YOUR_REDIS_PORT -a YOUR_PASSWORD SET test:expire "value" EX 5

# Watch logs - you should see:
# "Key expired: test:expire"
# "Called function for key 'test:expire': 200"
```

Done! ✅

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `REDIS_HOST` | Yes | Redis server hostname |
| `REDIS_PORT` | Yes | Redis server port |
| `REDIS_PASSWORD` | No | Redis password (if auth enabled) |
| `GCP_FUNCTION_URL` | Yes | Cloud Function endpoint URL |
| `ON_KEY_EXPIRED_SECRET` | Yes | Secret for authenticating with Cloud Function |

---

## Management
```bash
# View logs
sudo journalctl -u redis-listener -f

# Restart service
sudo systemctl restart redis-listener

# Stop service
sudo systemctl stop redis-listener

# Start service
sudo systemctl start redis-listener
```

---

## How It Works

1. Redis expires a key (e.g., `SET session:abc "data" EX 3600`)
2. Redis publishes to `__keyevent@0__:expired` channel
3. Listener receives the event and calls your Cloud Function
4. Cloud Function processes the expiration (cleanup, notifications, etc.)

**Architecture:**
```
Redis → pub/sub → Compute Engine VM → HTTPS → Cloud Function
```

---

## Troubleshooting

### Not receiving expiration events?
```bash
# Check Redis keyspace notifications are enabled
redis-cli CONFIG GET notify-keyspace-events
# Should include "Ex"

# Check listener is connected
sudo journalctl -u redis-listener | grep "Connected to Redis"
```

### Cloud Function not being called?
```bash
# Test function directly
curl -X POST $GCP_FUNCTION_URL \
  -H "x-on-key-expired-secret: $ON_KEY_EXPIRED_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"expired_key": "test:key"}'

# Grant VM permission to invoke function
# For 1st-gen Cloud Functions:
gcloud functions add-iam-policy-binding YOUR_FUNCTION_NAME \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/cloudfunctions.invoker"

# For 2nd-gen Cloud Functions (Cloud Run based):
gcloud run services add-iam-policy-binding YOUR_SERVICE_NAME \
  --region=YOUR_REGION \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### Service won't start?
```bash
# Check logs for errors
sudo journalctl -u redis-listener -n 50

# Verify file paths in service file
ls -la /home/$USER/simple-redis-listener/main.py
ls -la /home/$USER/simple-redis-listener/venv/bin/python
```

---

## Updating
```bash
# Pull latest changes
cd /home/$USER/simple-redis-listener
git pull

# Restart service
sudo systemctl restart redis-listener
```

---

## Why Compute Engine?

- ✅ **Reliable** - No unexpected platform restarts
- ✅ **Always-on** - Persistent Redis connection
- ✅ **Free tier** - $0/month for e2-micro in eligible regions
- ✅ **Simple** - Basic VM, no containers or orchestration needed
