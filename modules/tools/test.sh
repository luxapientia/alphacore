#!/bin/bash
#
# Comprehensive Test Runner for AlphaCore
# Runs all unit tests, integration tests, and miner tests
#

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"  # tools/../.. = repo root

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}AlphaCore Test Suite${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Change to project root
cd "$PROJECT_ROOT"

# Set ALPHACORE_CONFIG if not already set
if [ -z "$ALPHACORE_CONFIG" ]; then
    ALPHACORE_CONFIG="$PROJECT_ROOT/modules/task_config.yaml"
    export ALPHACORE_CONFIG
    echo -e "${YELLOW}Set ALPHACORE_CONFIG=$ALPHACORE_CONFIG${NC}"
fi

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo -e "${YELLOW}Warning: Virtual environment not detected${NC}"
    echo -e "${YELLOW}Attempting to activate .venv...${NC}"
    if [ -d ".venv" ]; then
        source .venv/bin/activate
        echo -e "${GREEN}✓ Virtual environment activated${NC}"
    elif [ -d "venv" ]; then
        echo -e "${YELLOW}.venv not found, attempting to activate venv...${NC}"
        source venv/bin/activate
        echo -e "${GREEN}✓ Virtual environment activated${NC}"
    else
        echo -e "${RED}✗ No .venv or venv found. Please create and activate a virtual environment first.${NC}"
        exit 1
    fi
fi

# Function to run test suite
run_tests() {
    local description="$1"
    shift
    # All remaining arguments are pytest arguments

    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}Running: $description${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    python -m pytest "$@"
    EXIT_CODE=$?

    # Exit code 0 = all passed, 5 = all skipped (treat as pass)
    if [ $EXIT_CODE -eq 0 ] || [ $EXIT_CODE -eq 5 ]; then
        echo -e "${GREEN}✓ $description PASSED${NC}"
        return 0
    else
        echo -e "${RED}✗ $description FAILED${NC}"
        return 1
    fi
}

# Track results
FAILED_SUITES=()
TOTAL_SUITES=0
PASSED_SUITES=0

# Parse command line arguments
RUN_ALL=true
RUN_UNIT=false
RUN_INTEGRATION=false
RUN_MINER=false
RUN_VALIDATOR=false
RUN_REPOSITORY=false
RUN_GCP=false
RUN_SANDBOX=false
RUN_QUICK=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --unit)
            RUN_ALL=false
            RUN_UNIT=true
            shift
            ;;
        --integration)
            RUN_ALL=false
            RUN_INTEGRATION=true
            shift
            ;;
        --miner)
            RUN_ALL=false
            RUN_MINER=true
            shift
            ;;
        --validator)
            RUN_ALL=false
            RUN_VALIDATOR=true
            shift
            ;;
        --repository)
            RUN_ALL=false
            RUN_REPOSITORY=true
            shift
            ;;
        --gcp)
            RUN_ALL=false
            RUN_GCP=true
            shift
            ;;
        --sandbox)
            RUN_ALL=false
            RUN_SANDBOX=true
            shift
            ;;
        --quick)
            RUN_QUICK=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --unit          Run only unit tests"
            echo "  --integration   Run only integration tests (requires OpenAI API key)"
            echo "  --miner         Run only miner tests"
            echo "  --validator     Run only validator tests"
            echo "  --repository    Run only repository tests"
            echo "  --gcp           Run only GCP provider tests"
            echo "  --sandbox       Run only sandbox tests"
            echo "  --quick         Run quick smoke test (subset of tests)"
            echo "  --help, -h      Show this help message"
            echo ""
            echo "If no options are specified, all tests will be run."
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}Test Configuration:${NC}"
echo "  Project Root: $PROJECT_ROOT"
echo "  Python: $(python --version)"
echo "  Pytest: $(python -m pytest --version)"
echo ""

# Quick smoke test mode
if [ "$RUN_QUICK" = true ]; then
    echo -e "${YELLOW}Running quick smoke test...${NC}"

    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Quick Smoke Test" -v --tb=short modules/tests/test_gcp_tasks.py modules/tests/test_miner.py::test_miner_forward_success; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Quick Smoke Test")
    fi

    # Skip to summary
    RUN_ALL=false
fi

# Run selected test suites
if [ "$RUN_ALL" = true ] || [ "$RUN_UNIT" = true ]; then
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    # Run all unit tests from alphacore_core/tests/ directory
    # Exclude integration tests that require external services
    if run_tests "Unit Tests (excluding integration)" -v --tb=short \
        modules/tests/ \
        modules/generation/test/ \
        --ignore=modules/tests/test_integration.py \
        --ignore=modules/tests/test_miner.py \
        --ignore=modules/tests/test_miner_advanced.py \
        --ignore=modules/tests/test_validator.py \
        --ignore=modules/tests/test_validator_advanced.py \
        --ignore=modules/tests/test_validator_sandbox_controller.py \
        --ignore=modules/tests/test_validator_sandbox_linting.py \
        --ignore=modules/tests/test_validator_sandbox_rootfs.py \
        --ignore=modules/tests/test_instruction_generator_integration.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Unit Tests")
    fi
fi

if [ "$RUN_ALL" = true ] || [ "$RUN_MINER" = true ]; then
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Miner Tests" -v --tb=short modules/tests/test_miner.py modules/tests/test_miner_advanced.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Miner Tests")
    fi
fi

if [ "$RUN_ALL" = true ] || [ "$RUN_VALIDATOR" = true ]; then
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Validator Tests" -v --tb=short modules/tests/test_validator.py modules/tests/test_validator_advanced.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Validator Tests")
    fi
fi

if [ "$RUN_ALL" = true ] || [ "$RUN_REPOSITORY" = true ]; then
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Repository Tests" -v --tb=short modules/tests/test_task_repository.py modules/tests/test_task_bank_repository_integration.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Repository Tests")
    fi
fi

if [ "$RUN_ALL" = true ] || [ "$RUN_GCP" = true ]; then
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "GCP Provider Tests" -v --tb=short modules/tests/test_gcp_*.py modules/tests/test_terraform_gcp_tasks.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("GCP Provider Tests")
    fi
fi

if [ "$RUN_ALL" = true ] || [ "$RUN_SANDBOX" = true ]; then
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Sandbox Tests" -v --tb=short modules/tests/test_validator_sandbox_*.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Sandbox Tests")
    fi
fi

if [ "$RUN_ALL" = true ]; then
    # Task Configuration Tests removed - now using YAML config
    # Skipping this test suite entirely

    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Instruction Generator Tests" -v --tb=short modules/tests/test_instruction_generator.py modules/tests/test_instruction_generator_integration.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Instruction Generator Tests")
    fi
    TOTAL_SUITES=$((TOTAL_SUITES + 1))
    if run_tests "Full Integration Tests" -v --tb=short modules/tests/test_integration.py; then
        PASSED_SUITES=$((PASSED_SUITES + 1))
    else
        FAILED_SUITES+=("Full Integration Tests")
    fi
fi

# Run integration tests if explicitly requested
if [ "$RUN_INTEGRATION" = true ]; then
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Integration Tests (require OpenAI API key)${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    if [ -z "$OPENAI_API_KEY" ]; then
        echo -e "${YELLOW}Warning: OPENAI_API_KEY not set${NC}"
        echo -e "${YELLOW}Integration tests will be skipped${NC}"
    else
        TOTAL_SUITES=$((TOTAL_SUITES + 1))
        if run_tests "OpenAI Integration Tests" -v --tb=short -m integration; then
            PASSED_SUITES=$((PASSED_SUITES + 1))
        else
            FAILED_SUITES+=("OpenAI Integration Tests")
        fi
    fi
fi

# Print summary
echo ""
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo "Total Test Suites: $TOTAL_SUITES"
echo -e "${GREEN}Passed: $PASSED_SUITES${NC}"

if [ ${#FAILED_SUITES[@]} -gt 0 ]; then
    echo -e "${RED}Failed: ${#FAILED_SUITES[@]}${NC}"
    echo ""
    echo -e "${RED}Failed Suites:${NC}"
    for suite in "${FAILED_SUITES[@]}"; do
        echo -e "${RED}  ✗ $suite${NC}"
    done
    echo ""
    exit 1
else
    echo -e "${GREEN}Failed: 0${NC}"
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}All tests passed! ✓${NC}"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    exit 0
fi
