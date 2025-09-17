# Get Roles API Tests

Simple test suite for the Get Roles API endpoint with **15+ comprehensive test cases**.

## 🚀 Quick Start

### **1. Setup Virtual Environment**
```bash
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r apps/requirements.txt
```

### **2. Run All Tests**
```bash
cd tests && PYTHONPATH=.. python -m pytest test_get_roles_api.py -v
```

### **3. Run All Tests with Coverage**
```bash
PYTHONPATH=.. python -m pytest apps/user_service/tests/test_users_api_comprehensive.py --cov=apps --cov-report=html --cov-report=term-missing -v -s

PYTHONPATH=.. python -m pytest . --cov=apps --cov-report=html --cov-report=term-missing -v



cd tests && PYTHONPATH=.. python -m pytest . --cov=apps.api.admin_management.roles --cov-report=html --cov-report=term-missing -v
```

## 📋 Test Commands

| Command | Description |
|---------|-------------|
| `PYTHONPATH=.. python -m pytest test_get_roles_api.py -v` | Run all tests |
| `PYTHONPATH=.. python -m pytest . --cov=apps.api.admin_management.roles --cov-report=html -v` | Run with coverage |
| `PYTHONPATH=.. python -m pytest test_get_roles_api.py::TestGetRolesAPI -v` | Run unit tests only |
| `PYTHONPATH=.. python -m pytest test_get_roles_api.py -k "auth" -v` | Run authentication tests |

## 📊 Coverage Report
After running coverage tests, open `htmlcov/index.html` in your browser.

## ✅ Test Coverage
- **Success Scenarios**: Default params, search, filters, pagination
- **Authentication**: JWT validation, permissions
- **Validation**: Invalid inputs, edge cases
- **Database Errors**: Connection failures, query errors 
