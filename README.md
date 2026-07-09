# LendingMind AI

This is the **LendingMind AI** platform, an AI Lending Decision Intelligence Platform for MSMEs. It is built as a complete monorepo encompassing both a FastAPI backend (handling the AI agent workflows) and a Next.js frontend (providing a glassmorphism UI for visualization).

## Running the Application

To run the full stack, you need to start both the frontend and the backend.

### 1. Start the Backend
Open a terminal and run the following commands from the root directory:
```bash
# Navigate to backend directory
cd backend

# Activate virtual environment (Windows)
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server
uvicorn main:app --reload --port 8000
```

### 2. Start the Frontend
Open a new terminal and run from the root directory:
```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Run the development server
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to view the application.

> **Note:** For a complete, step-by-step walkthrough of how to use this platform, please refer to the @[HOW_TO_USE.txt](HOW_TO_USE.txt) file located in this directory.

## Tech Stack

**Backend (AI Workflow & API):**
- FastAPI & Python 3
- Data Science tools (Pandas, Numpy, Scikit-learn, XGBoost, SHAP, Prophet, NetworkX)
- Orchestration frameworks for the multi-agent AI setup

**Frontend (User Interface):**
- [Next.js](https://nextjs.org) (App Router)
- [Tailwind CSS](https://tailwindcss.com/)
- [shadcn/ui](https://ui.shadcn.com/)
- [React Flow](https://reactflow.dev/) for pipeline visualization
- [Recharts](https://recharts.org/) for data visualization

## Project Structure

### Backend (`/backend`)
Handles the heavy lifting of the AI OS.
- `/agents`: Contains the specialized AI modules (`financial_health.py`, `fraud.py`, `recommendation.py`, etc.).
- `/engines`: Feature engineering and AI explainability services.
- `/orchestrator`: Manages the step-by-step pipeline execution and monitoring.
- `/services`: Core logic handling scenarios, coaching, and document processing.

### Frontend (`/frontend`)
Visualizes the AI decision pipeline.
- `src/app/page.tsx`: The main Dashboard page.
- `src/app/company/`: MSME company profile creation.
- `src/app/financial-data/`: Alternative financial data import flow.
- `src/app/pipeline/`: Visualizes the execution of the multi-agent AI pipeline.
- `src/app/decision-center/`: Displays the aggregated final decision and risk vectors.
- `src/app/recommendation/`: Shows AI-generated loan structuring.
- `src/app/scenario-simulator/`: Interactive tool to stress-test the applicant under various economic scenarios.
