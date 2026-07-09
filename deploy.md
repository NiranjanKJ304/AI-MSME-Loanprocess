# Deployment Guide: LendingMind AI

This guide explains how to deploy the LendingMind AI monorepo on **Render** (FastAPI Backend) and **Vercel** (Next.js Frontend).

> [!NOTE]
> **Database Requirement**: A database is **not** required to run or deploy this project in its current state. The application runs entirely in-memory using simulated datasets, making it extremely easy to host for demos and hackathons without any database configuration.

---

## 1. Deploying the Backend on Render
[Render.com](https://render.com) is ideal for hosting Python FastAPI applications.

### Step 1: Create a Render Web Service
1. Log in to **Render** and click **New +** -> **Web Service**.
2. Connect your GitHub repository (`NiranjanKJ304/AI-MSME-Loanprocess`).

### Step 2: Configure Settings
Configure the service with these settings:
* **Name**: `lendingmind-api` (or any preferred name)
* **Region**: Choose the closest one to your users (e.g., Singapore or Oregon)
* **Branch**: `main`
* **Root Directory**: `backend` (This is critical since the backend is in a subfolder!)
* **Runtime**: `Python`
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
* **Plan**: `Free` (or custom tier)

### Step 3: Deploy
Click **Create Web Service**. Render will build the environment and deploy your FastAPI app. Once complete, copy the public URL provided by Render (e.g., `https://lendingmind-api.onrender.com`).

---

## 2. Deploying the Frontend on Vercel
[Vercel](https://vercel.com) is the native hosting platform for Next.js.

### Step 1: Create a Vercel Project
1. Log in to Vercel and click **Add New** -> **Project**.
2. Import your GitHub repository (`NiranjanKJ304/AI-MSME-Loanprocess`).

### Step 2: Configure Project Settings
Before clicking deploy, adjust the following configurations:
* **Framework Preset**: `Next.js`
* **Root Directory**: Click *Edit* and select the `frontend` folder.
* **Build & Development Settings**: Keep defaults.
* **Environment Variables**:
  Add an environment variable to link your frontend to the live backend:
  * **Key**: `NEXT_PUBLIC_API_URL`
  * **Value**: Your Render Backend URL (e.g., `https://lendingmind-api.onrender.com`)

### Step 3: Deploy
Click **Deploy**. Vercel will automatically build the Next.js site and provide a production URL (e.g., `https://lendingmind-ai.vercel.app`).

---

## 3. Configuring CORS (Cross-Origin Resource Sharing)
By default, the backend (`backend/main.py`) has CORS middleware configured to allow all origins (`allow_origins=["*"]`). 

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allows Vercel frontend to query the Render API
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
This is suitable for hackathons and ensures your frontend on Vercel can talk to your backend on Render without facing CORS blockages.
