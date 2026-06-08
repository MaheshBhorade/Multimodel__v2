# Manual Run Guide - Complete Step-by-Step

## 📋 Prerequisites

Make sure you have installed:
- **Python 3.12+** - Download from python.org
- **Git** (optional, for version control)
- **pip** (comes with Python)

Verify:
```powershell
python --version      # Should show Python 3.12+
pip --version         # Should show pip version
```

---

## 🚀 Step 1: Extract & Navigate to Project

```powershell
# Navigate to project directory
cd d:\Multimodel_Fingerprint

# Verify you're in the right place (should see these files)
ls
# Expected output:
# - README.md
# - pyproject.toml
# - src/
# - tests/
# - content_platform.db
# - docker-compose.yml
# - etc.
```

---

## 🔧 Step 2: Create Virtual Environment

```powershell
# Create virtual environment (isolated Python environment for this project)
python -m venv venv

# Activate it
.\venv\Scripts\Activate.ps1

# You should see (venv) in your terminal prompt now
# Example: (venv) PS d:\Multimodel_Fingerprint>
```

**If activation fails with execution policy error:**
```powershell
# Run this once to allow scripts:
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
# Then try activation again:
.\venv\Scripts\Activate.ps1
```

---

## 📦 Step 3: Install Dependencies

```powershell
# Make sure virtual environment is activated (check for (venv) prompt)
# Install project in development mode (includes all dependencies)
pip install -e .

# This will install:
# - FastAPI
# - SQLAlchemy
# - Pydantic
# - numpy
# - pytest
# - (and other dependencies listed in pyproject.toml)

# Installation takes 1-2 minutes. You'll see output like:
# "Successfully installed content-platform-0.1.0"
```

---

## ✅ Step 4: Verify Installation

```powershell
# Check if installation was successful
python -c "import content_platform; print('✓ Installation successful')"

# Expected output:
# ✓ Installation successful

# Also check key dependencies
python -c "import fastapi, sqlalchemy, pydantic; print('✓ All dependencies installed')"
```

---

## 🧪 Step 5: Run Tests (Optional but Recommended)

```powershell
# Run all tests
pytest -v

# Expected output (should see 12 passed):
# ============== test session starts ==============
# tests/test_api.py::test_create_device PASSED          [ 8%]
# tests/test_api.py::test_list_devices PASSED           [16%]
# tests/test_api.py::test_create_device_already_exists PASSED [25%]
# ... (more tests)
# ============== 12 passed in 2.34s ===============

# If any test fails, check:
# 1. Virtual environment is activated
# 2. All dependencies installed: pip install -e .
# 3. No other process using port 8000
```

---

## 🌐 Step 6: Start the Server

```powershell
# Start FastAPI server
python -m content_platform.server.main

# Expected output:
# INFO:     Uvicorn running on http://0.0.0.0:8000
# INFO:     Application startup complete
# Press CTRL+C to stop

# Server is now running at http://localhost:8000
```

**Keep this terminal window open!**

---

## 📡 Step 7: Test the Server (New Terminal)

Open a **NEW PowerShell window** while server is running:

```powershell
# Navigate to project (no need to activate venv in this terminal just for curl)
cd d:\Multimodel_Fingerprint

# Test 1: Health check
curl http://localhost:8000/api/v1/health

# Expected response:
# {"status":"healthy"}

# Test 2: List devices
curl http://localhost:8000/api/v1/devices

# Expected response:
# {"total":3,"items":[{"device_id":"PI001",...}, ...]}

# Test 3: View library
curl http://localhost:8000/api/v1/library

# Expected response:
# {"total":5,"items":[{"title":"Dilwale",...}, ...]}
```

---

## 📤 Step 8: Send a Test Capture

Create a file `test_capture.ps1` with this content:

```powershell
# Save as: test_capture.ps1

$payload = @{
    device_id = "PI001"
    timestamp = [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    visual_fp = @(0.88, 0.23, 0.61, 0.79, 0.15, 0.34, 0.92, 0.41) + @(0.0) * 56  # 64 floats
    audio_fp = "dilwale-main-track"
    logo_fp = @(0.95, 0.15, 0.41, 0.81) + @(0.0) * 12  # 16 floats
    ocr_text = "dilwale shah rukh kajol sony max"
    snapshot_url = "http://localhost/snapshot.jpg"
} | ConvertTo-Json

# Send to server
Invoke-RestMethod `
    -Uri http://localhost:8000/api/v1/captures `
    -Method Post `
    -Body $payload `
    -ContentType "application/json" | ConvertTo-Json

# Expected response (after 1 second):
# {
#   "capture_id": 37,
#   "result": {
#     "content_name": "Matching in progress...",
#     ...
#   }
# }
```

Run it:
```powershell
.\test_capture.ps1
```

Wait 1 second, then check result:
```powershell
curl http://localhost:8000/api/v1/captures | ConvertFrom-Json | Select -ExpandProperty items | Select -First 1
```

Should show matched content:
```
content_name : Dilwale
confidence   : 0.95
```

---

## 🎨 Step 9: View API Documentation (Interactive)

Open browser and go to:
```
http://localhost:8000/docs
```

You'll see **Swagger UI** with:
- All 12 API endpoints
- Request/response schemas
- **Try it out** button to test directly in browser

Alternative OpenAPI JSON:
```
http://localhost:8000/openapi.json
```

---

## 💾 Step 10: Inspect Database

```powershell
# Install sqlite3 (if not already installed)
choco install sqlite -y
# OR get from https://www.sqlite.org/download.html

# Open database
sqlite3 content_platform.db

# Useful queries:
# Show all content:
sqlite3 content_platform.db "SELECT id, title, category, channel_name FROM content_library;"

# Count captures:
sqlite3 content_platform.db "SELECT COUNT(*) as total_captures FROM capture;"

# Show recent captures with results:
sqlite3 content_platform.db "SELECT c.id, r.content_name, r.confidence FROM capture c LEFT JOIN recognition_result_record r ON c.id = r.capture_id ORDER BY c.captured_at DESC LIMIT 5;"

# Exit database: .quit
```

---

## 🛑 Step 11: Stop the Server

In the **server terminal** (where it's running):
```
Press CTRL+C
```

Server will stop gracefully.

---

## 📊 Complete Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    RUNNING THE PROJECT                          │
└─────────────────────────────────────────────────────────────────┘

Terminal 1 (Server):
    cd d:\Multimodel_Fingerprint
    .\venv\Scripts\Activate.ps1
    python -m content_platform.server.main
    (Keep running)

                         ↓
                    Server starts on
                   http://localhost:8000
                         ↓

Terminal 2 (Tests/Requests):
    cd d:\Multimodel_Fingerprint
    curl http://localhost:8000/api/v1/health
    (Send captures, queries, etc.)
                         ↓
                   Gets JSON responses
```

---

## 🔍 Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'content_platform'"
**Solution:** 
```powershell
# Deactivate and reactivate venv
deactivate
.\venv\Scripts\Activate.ps1

# Reinstall
pip install -e .
```

### Issue: "Address already in use" (port 8000)
**Solution:**
```powershell
# Kill process using port 8000
netstat -ano | findstr :8000
# Find the PID in the output, then:
taskkill /PID <PID> /F

# Or use different port:
python -m content_platform.server.main --port 8001
```

### Issue: Tests fail with "database is locked"
**Solution:**
```powershell
# Restart everything
# 1. Stop server (CTRL+C)
# 2. Delete database: rm content_platform.db
# 3. Run tests again: pytest -v
```

### Issue: Virtual environment won't activate
**Solution:**
```powershell
# Try using python directly without venv (not recommended but works)
pip install -e .
python -m content_platform.server.main

# Or use conda if available
conda create -n crp python=3.12
conda activate crp
pip install -e .
```

---

## 📋 Quick Command Reference

| Command | Purpose |
|---------|---------|
| `cd d:\Multimodel_Fingerprint` | Navigate to project |
| `.\venv\Scripts\Activate.ps1` | Activate virtual environment |
| `deactivate` | Deactivate virtual environment |
| `pip install -e .` | Install project dependencies |
| `pytest -v` | Run all tests |
| `python -m content_platform.server.main` | Start server |
| `curl http://localhost:8000/api/v1/health` | Test server |
| `sqlite3 content_platform.db` | Open database |

---

## 🎯 What Each Step Does

| Step | What | Why |
|------|------|-----|
| 1 | Navigate to project | Ensure we're in the right folder |
| 2 | Create virtual environment | Isolate project dependencies |
| 3 | Install dependencies | Get all required libraries |
| 4 | Verify installation | Check everything installed correctly |
| 5 | Run tests | Validate the system works |
| 6 | Start server | Run the API |
| 7 | Test server | Verify server responds |
| 8 | Send capture | Test full matching flow |
| 9 | View API docs | Understand available endpoints |
| 10 | Inspect database | See stored data |

---

## ✨ That's It!

You now have:
- ✅ Virtual environment set up
- ✅ All dependencies installed
- ✅ Server running on localhost:8000
- ✅ Tests passing
- ✅ API documented and tested
- ✅ Database accessible

**Next steps:**
- Review `PROJECT_GUIDE.md` for architecture details
- Review `CODE_REFERENCE.md` for code examples
- Make changes and test with `pytest -v`
- Send captures and check results via API

Happy developing! 🚀

