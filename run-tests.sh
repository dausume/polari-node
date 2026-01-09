#!/bin/bash

# Test runner script for Polari Suite
# Usage: ./run-tests.sh [backend|frontend|fullstack]

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Function to run backend tests
run_backend_tests() {
    print_info "Running backend (Polari Framework) tests..."
    cd polari-framework
    docker-compose -f docker-compose.test.yml up --build --abort-on-container-exit
    local exit_code=$?
    cd ..

    if [ $exit_code -eq 0 ]; then
        print_success "Backend tests passed!"
    else
        print_error "Backend tests failed with exit code $exit_code"
        exit $exit_code
    fi
}

# Function to run frontend tests
run_frontend_tests() {
    print_info "Running frontend (Angular) tests in isolation mode..."
    cd polari-platform-angular
    docker-compose -f docker-compose.test.yml up --build --abort-on-container-exit
    local exit_code=$?
    cd ..

    if [ $exit_code -eq 0 ]; then
        print_success "Frontend tests passed!"
        print_info "Coverage report available at: polari-platform-angular/coverage/polari-platform/index.html"
    else
        print_error "Frontend tests failed with exit code $exit_code"
        exit $exit_code
    fi
}

# Function to run full-stack tests
run_fullstack_tests() {
    print_info "Running full-stack tests (backend + frontend with integration)..."
    docker-compose -f docker-compose.fullstack-test.yml up --build --abort-on-container-exit
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        print_success "Full-stack tests passed!"
    else
        print_error "Full-stack tests failed with exit code $exit_code"
        exit $exit_code
    fi
}

# Function to clean up test containers
cleanup() {
    print_info "Cleaning up test containers..."
    cd polari-framework
    docker-compose -f docker-compose.test.yml down -v 2>/dev/null || true
    cd ../polari-platform-angular
    docker-compose -f docker-compose.test.yml down -v 2>/dev/null || true
    cd ..
    docker-compose -f docker-compose.fullstack-test.yml down -v 2>/dev/null || true
    print_success "Cleanup complete!"
}

# Main script logic
main() {
    local test_type="${1:-all}"

    case "$test_type" in
        backend)
            run_backend_tests
            ;;
        frontend)
            run_frontend_tests
            ;;
        fullstack)
            run_fullstack_tests
            ;;
        all)
            print_info "Running all test suites..."
            run_backend_tests
            run_frontend_tests
            run_fullstack_tests
            print_success "All test suites passed!"
            ;;
        clean)
            cleanup
            ;;
        help|--help|-h)
            echo "Usage: $0 [backend|frontend|fullstack|all|clean|help]"
            echo ""
            echo "Options:"
            echo "  backend    - Run only backend (Python) tests"
            echo "  frontend   - Run only frontend (Angular) tests in isolation"
            echo "  fullstack  - Run full-stack integration tests"
            echo "  all        - Run all test suites sequentially (default)"
            echo "  clean      - Clean up test containers and volumes"
            echo "  help       - Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 backend    # Run backend tests only"
            echo "  $0 fullstack  # Run full-stack integration tests"
            echo "  $0            # Run all tests"
            exit 0
            ;;
        *)
            print_error "Unknown test type: $test_type"
            echo "Run '$0 help' for usage information"
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"
