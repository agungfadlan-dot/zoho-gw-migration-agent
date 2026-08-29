# ☁️ Google Cloud Run Deployment Guide
## Zoho to Google Workspace Migration Agent

This guide walks you through deploying and running the **Zoho to Google Workspace Migration Agent** as a secure, serverless web application on **Google Cloud Run**.

---

## 📌 Why Run on Cloud Run?
* **Zero Office Network Impact:** 100% of data flows directly between Zoho servers and Google’s datacenter backbone.
* **Cost-Effective:** Scales to zero when idle; total compute cost for a full migration (800+ GB) is **under $5.00** (or **$0.00** on GCP Free Tier).
* **Enterprise Security:** In-memory encryption (AES-256-GCM), automatic HTTPS, and role-based IAM authentication.

---

## 🛠️ Prerequisites

Before you start, ensure you have:
1. A **Google Cloud Project** with Billing enabled.
2. The **`gcloud` CLI** installed on your computer. ([Install gcloud](https://cloud.google.com/sdk/docs/install)).
3. Permissions to deploy Cloud Run services (`roles/run.admin` or `roles/owner`).

---

## 🚀 Step-by-Step Deployment Instructions

### Step 1: Log In & Configure Your GCP Project
Open your local terminal and run:

```bash
# 1. Authenticate gcloud with your Google account
gcloud auth login

# 2. Set your active Google Cloud Project ID
gcloud config set project YOUR_PROJECT_ID
```

---

### Step 2: Enable Required GCP APIs
Enable Cloud Run, Cloud Build, and Cloud Storage APIs:

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com storage.googleapis.com
```

---

### Step 3: Deploy the Web App to Cloud Run

You can deploy using either the automated script or the direct `gcloud` command:

#### Option A: Using the Included Deploy Script (Recommended)
```bash
# Make script executable and run
chmod +x deploy_cloudrun.sh
./deploy_cloudrun.sh
```

#### Option B: Direct `gcloud run deploy` Command
```bash
gcloud run deploy zoho-gw-migration-agent \
    --source . \
    --region asia-southeast2 \
    --platform managed \
    --cpu 1 \
    --memory 2Gi \
    --timeout 3600 \
    --min-instances 0 \
    --max-instances 1 \
    --no-allow-unauthenticated
```

> [!NOTE]
> * `--region asia-southeast2`: Deploys in Jakarta (use `us-central1` or your preferred region if desired).
> * `--no-allow-unauthenticated`: Ensures only authorized Google Cloud administrators can access the Web UI.
> * Build and deployment takes approximately **1 to 2 minutes**.

---

### Step 4: Access the Migration Web UI Securely

Because the service is protected by IAM authentication, you can access the Web UI securely from your computer without making it public to the internet:

#### Option 1: Local Proxy via `gcloud` (Easiest & Most Secure)
Run this command in your terminal:
```bash
gcloud run services proxy zoho-gw-migration-agent --region asia-southeast2 --port 8080
```
Then open your browser to:
👉 **`http://localhost:8080`**

*(All traffic is authenticated through your `gcloud` admin credentials and encrypted via Google Cloud).*

#### Option 2: Grant Web Access to Specific Admins
To let other IT administrators open the Cloud Run HTTPS URL directly in their browser:
```bash
gcloud run services add-iam-policy-binding zoho-gw-migration-agent \
    --region asia-southeast2 \
    --member="user:admin@andhika.com" \
    --role="roles/run.invoker"
```

---

### Step 5: Execute the Migration via Web UI

Once the Web UI is open in your browser:

1. **Step 1 (Credentials)**: Enter Zoho OAuth credentials and upload your Google Service Account JSON key.
2. **Step 2 (Pre-Flight Audit)**: Click **"Run Security & Scope Audit"** to verify Domain-Wide Delegation and Zoho token permissions.
3. **Step 3 (Discovery & Sizing)**: Click **"Discover Organization & Users"** to scan mailboxes, folders, and storage volume.
4. **Step 4 (Scope Selection)**:
   - Select **"Quick Pilot (5 Users)"** for initial testing, or **"Migrate All Users"** for the full tenant migration.
   - Toggle **Dry-Run Mode** on first to simulate without making changes.
5. **Step 5 (Live Migration)**:
   - Click **"Start Migration Pipeline"**.
   - Watch real-time progress, stage transitions, and log streams.
   - Download the generated **One-Time Passwords CSV** upon completion.

---

### Step 6: Teardown & Cost Cleanup

* **Automatic Zero Cost When Idle:** Once the migration finishes, Cloud Run automatically scales down to 0 active instances, incurring **$0.00** compute charges.
* **Optional Service Deletion:** When you no longer need the migration web app:
  ```bash
  gcloud run services delete zoho-gw-migration-agent --region asia-southeast2 --quiet
  ```

---

## ❓ Frequently Asked Questions (FAQ)

#### Q: Will closing my laptop interrupt a Cloud Run migration?
**No.** Cloud Run executes inside Google Cloud's datacenters. Once a migration job starts, closing your browser or laptop will not terminate the worker. When you reconnect and open the UI, it will automatically resume displaying the live progress stream.

#### Q: How are secrets protected on Cloud Run?
Secrets are entered directly into browser memory at runtime and decrypted inside the container's RAM via an ephemeral AES-256-GCM vault. No passwords or tokens are stored in container image layers or plaintext files.
