# Polari Suite Testing Guide

This document explains how to run tests for the Polari Suite project, covering both frontend (Angular) and backend (Python/Polari Framework) components.

## Overview

The Polari Suite has three testing modes:

1. **Backend Isolation Testing** - Tests only the Python backend framework
2. **Frontend Isolation Testing** - Tests only the Angular frontend without backend
3. **Full-Stack Testing** - Tests frontend and backend together with real API integration

## Backend Testing (Polari Framework)

### Run Backend Tests in Isolation

```bash
cd polari-framework
docker-compose -f docker-compose.test.yml up --build --abort-on-container-exit
```

This will:
- Build the test Docker image
- Run all Python unit tests
- Display test results
- Exit when tests complete

### Backend Test Results

Test results are displayed in the console output. Look for:
- Total tests run
- Number of successes
- Number of failures
- Number of errors

## Frontend Testing (Polari Platform Angular)

### Run Frontend Tests in Isolation (Default)

```bash
cd polari-platform-angular
docker-compose -f docker-compose.test.yml up --build --abort-on-container-exit
```

This will:
- Build the test Docker image with Chrome Headless
- Run Karma/Jasmine tests without connecting to backend
- Generate code coverage report
- Exit when tests complete

### Frontend Test Results

- Test results are displayed in the console
- Coverage reports are generated in `polari-platform-angular/coverage/`
- Open `coverage/polari-platform/index.html` in a browser to view detailed coverage

## Full-Stack Testing

### Run Full-Stack Tests

From the `polari-node` directory:

```bash
docker-compose -f docker-compose.fullstack-test.yml up --build --abort-on-container-exit
```

This will:
1. **Backend Tests**: Run backend tests first
2. **Backend Server**: Start backend server if tests pass
3. **Frontend Tests**: Run frontend tests with real API calls to backend
4. Exit when all tests complete

### Full-Stack Test Sequence

1. Backend tests must pass before server starts
2. Server must be healthy before frontend tests run
3. Frontend tests run in `fullstack` mode with `BACKEND_URL` set
4. All containers exit when tests complete

## Environment Variables

### Frontend Testing

- `TEST_MODE` - Set to `isolation` (default) or `fullstack`
- `BACKEND_URL` - Backend API URL (used in fullstack mode)

### Backend Testing

No special environment variables needed for basic testing.

## Test Configuration Files

### Backend

- `polari-framework/Dockerfile.test` - Backend test container
- `polari-framework/docker-compose.test.yml` - Backend test orchestration
- `polari-framework/run_tests.py` - Python test runner
- `polari-framework/tests/` - Test files

### Frontend

- `polari-platform-angular/Dockerfile.test` - Frontend test container
- `polari-platform-angular/docker-compose.test.yml` - Frontend test orchestration
- `polari-platform-angular/karma.conf.js` - Karma test configuration
- `polari-platform-angular/src/**/*.spec.ts` - Test files

### Full-Stack

- `docker-compose.fullstack-test.yml` - Orchestrates both test suites

## Running Tests Locally (Without Docker)

### Backend

```bash
cd polari-framework
python run_tests.py
```

### Frontend

```bash
cd polari-platform-angular
npm test
```

## Continuous Integration

For CI/CD pipelines, use:

```bash
# Backend only
docker-compose -f polari-framework/docker-compose.test.yml up --build --abort-on-container-exit

# Frontend only
docker-compose -f polari-platform-angular/docker-compose.test.yml up --build --abort-on-container-exit

# Full-stack
docker-compose -f docker-compose.fullstack-test.yml up --build --abort-on-container-exit
```

All commands exit with appropriate status codes for CI integration.

## Troubleshooting

### Backend Tests Fail

- Check Python dependencies are installed correctly
- Verify database connections if tests use database
- Check test output for specific error messages

### Frontend Tests Fail

- Ensure Chrome is available in the container
- Check karma.conf.js configuration
- Verify all Angular dependencies are installed
- Look for test-specific errors in console output

### Full-Stack Tests Fail

- Ensure backend tests pass first
- Check backend server starts successfully (health check)
- Verify network connectivity between containers
- Check `BACKEND_URL` is correctly set for frontend

## Coverage Reports

### Backend Coverage

Coverage reports can be added to the backend by modifying `run_tests.py` to use coverage tools like `pytest-cov`.

### Frontend Coverage

Frontend coverage is automatically generated in `polari-platform-angular/coverage/`:
- HTML reports: `coverage/polari-platform/index.html`
- Text summary: Displayed in console output

## Adding New Tests

### Backend

Add test files in `polari-framework/tests/` following the pattern `test_*.py`.

### Frontend

Add test files alongside components following the pattern `*.spec.ts`.

## Test Development Tips

- **Isolation Tests**: Use mocks and stubs for external dependencies
- **Full-Stack Tests**: Test real API integration and end-to-end flows
- **Keep Tests Fast**: Isolation tests should run quickly
- **Clear Test Names**: Use descriptive test names that explain what is being tested
