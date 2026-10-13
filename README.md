
# HROne Employee Attendance & Analytics API

## Project Overview

This project is a REST API for employee management, attendance tracking, attendance regularization, and workforce analytics.

## Technologies Used

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo
- Uvicorn

## Setup

### 1. Create and activate a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure MongoDB

Create a local `.env` file in the project root with your MongoDB connection settings:

```env
MONGO_URI=your_mongodb_connection_string
MONGO_DB=your_database_name
```

Replace the example values with your actual settings. Do not commit `.env` or database credentials to GitHub.

### 4. Run the application

```powershell
python -m uvicorn app.main:app --port 8000
```

## API Documentation

After starting the application, open:

- Swagger UI: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Main Features

- Employee creation and listing
- Attendance punch-in and punch-out
- Attendance listing with filtering and pagination
- Attendance regularization with history
- Monthly employee attendance analytics
- Department attendance summary
- Late-arrival leaderboard
- Department attendance trends
- MongoDB query execution-plan inspection

## Submission

The repository includes the application source code, dependency list, and documentation files required for the assignment.

No `.env` file, passwords, or other secrets should be included in the public repository.