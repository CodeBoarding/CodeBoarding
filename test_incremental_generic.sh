#!/bin/bash
# Generic incremental analysis test script
# Usage: ./test_incremental_generic.sh <start_commit> <end_commit> [--incremental]
# Example: ./test_incremental_generic.sh e03132c97997a6dabf68cd5d2df6432601360edb HEAD
# Example (incremental only): ./test_incremental_generic.sh e03132c97997a6dabf68cd5d2df6432601360edb HEAD --incremental
# 

set -e

# Parse arguments
INCREMENTAL_ONLY=false
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
    case $1 in
        --incremental)
            INCREMENTAL_ONLY=true
            shift
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Usage: $0 <start_commit> <end_commit> [--incremental]"
            exit 1
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Validate positional arguments
if [ ${#POSITIONAL_ARGS[@]} -ne 2 ]; then
    echo "Usage: $0 <start_commit> <end_commit> [--incremental]"
    echo "Example: $0 e03132c97997a6dabf68cd5d2df6432601360edb HEAD"
    echo "Example (incremental only): $0 e03132c97997a6dabf68cd5d2df6432601360edb HEAD --incremental"
    exit 1
fi

START_COMMIT="${POSITIONAL_ARGS[0]}"
END_COMMIT="${POSITIONAL_ARGS[1]}"
SHORT_START=$(echo "$START_COMMIT" | cut -c1-8)
SHORT_END=$(echo "$END_COMMIT" | cut -c1-8)

# Configuration
REPO_DIR="/home/ivan/StartUp/CodeBoarding/repos/CodeBoarding"
PROJECT_NAME="CodeBoarding"
DEPTH_LEVEL=2

# Create result directory
RESULT_DIR="${SHORT_START}_${SHORT_END}"
mkdir -p "$RESULT_DIR"
mkdir -p "$RESULT_DIR/init_resources"
mkdir -p "$RESULT_DIR/incr_resources"

echo "Results will be saved in: $RESULT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  Generic Incremental Analysis Test   ${NC}"
echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  Start: $START_COMMIT${NC}"
echo -e "${BLUE}  End:   $END_COMMIT${NC}"
echo ""

# ============================================
# STEP 0: Ensure clean state in test repository
# ============================================
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}STEP 0: Ensuring clean repository state${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

if [ -d "$REPO_DIR/.git" ]; then
    cd "$REPO_DIR"
    
    # Check for uncommitted changes
    if ! git diff-index --quiet HEAD -- || [ -n "$(git status --porcelain)" ]; then
        echo -e "${YELLOW}⚠ Uncommitted changes detected, cleaning...${NC}"
        # Preserve the static analysis cache
        if [ -d ".codeboarding/cache" ]; then
            echo -e "${YELLOW}Preserving static analysis cache...${NC}"
            mv .codeboarding/cache /tmp/codeboarding_cache_backup
        fi
        git reset --hard HEAD
        git clean -fd
        # Restore the cache
        if [ -d "/tmp/codeboarding_cache_backup" ]; then
            mkdir -p .codeboarding
            mv /tmp/codeboarding_cache_backup .codeboarding/cache
            echo -e "${GREEN}✓ Cache restored${NC}"
        fi
        echo -e "${GREEN}✓ Repository cleaned${NC}"
    fi
    
    # Checkout to main
    echo -e "${YELLOW}Checking out main branch...${NC}"
    git checkout main --quiet 2>/dev/null || git checkout master --quiet 2>/dev/null || true
    git pull origin main --quiet 2>/dev/null || git pull origin master --quiet 2>/dev/null || true
    echo -e "${GREEN}✓ Now on main branch: $(git rev-parse --short HEAD)${NC}"
    
    cd - > /dev/null
else
    echo -e "${YELLOW}Repository not found at $REPO_DIR${NC}"
fi

echo ""

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    echo -e "${GREEN}✓ Virtual environment activated${NC}"
else
    echo -e "${RED}✗ Virtual environment not found at .venv${NC}"
    exit 1
fi

# Check if repo exists
if [ ! -d "$REPO_DIR" ]; then
    echo -e "${YELLOW}Repository not found at $REPO_DIR, cloning...${NC}"
    mkdir -p repos
    git clone https://github.com/CodeBoarding/CodeBoarding.git "$REPO_DIR"
fi

# Store current branch/commit of the test repo
cd "$REPO_DIR"
ORIGINAL_REF=$(git rev-parse HEAD)
ORIGINAL_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "detached")
echo -e "${BLUE}Original state: $ORIGINAL_BRANCH ($ORIGINAL_REF)${NC}"
cd - > /dev/null

# ============================================
# STEP 1: Checkout start commit and run full analysis
# ============================================
if [ "$INCREMENTAL_ONLY" = false ]; then
    echo ""
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}STEP 1: Full Analysis on start commit${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""

    cd "$REPO_DIR"
    echo -e "${YELLOW}Checking out start commit: $START_COMMIT${NC}"
    git checkout "$START_COMMIT" --quiet
    CURRENT_COMMIT=$(git rev-parse --short HEAD)
    echo -e "${GREEN}✓ Now at commit: $CURRENT_COMMIT${NC}"
    cd - > /dev/null

    # Clean up any existing analysis
    echo ""
    echo -e "${YELLOW}Cleaning up existing analysis...${NC}"
    rm -rf "$RESULT_DIR/init_resources"
    mkdir -p "$RESULT_DIR/init_resources"

    echo ""
    echo -e "${YELLOW}Starting FULL analysis...${NC}"
    echo -e "${YELLOW}This will take several minutes. Time tracking started.${NC}"
    echo ""

    FULL_START=$(date +%s.%N)

    # Run full analysis directly to init_resources
    python main.py \
        --local "$REPO_DIR" \
        --project-name "$PROJECT_NAME" \
        --output-dir "$RESULT_DIR/init_resources" \
        --depth-level "$DEPTH_LEVEL" \
        --load-env-variables \
        --full \
        2>&1 | tee "${RESULT_DIR}/init_analys_${SHORT_START}.txt"

    FULL_END=$(date +%s.%N)
    FULL_DURATION=$(echo "$FULL_END - $FULL_START" | bc)

    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}Full analysis completed!${NC}"
    echo -e "${GREEN}Duration: ${FULL_DURATION} seconds${NC}"
    echo -e "${GREEN}============================================${NC}"

    # Show what was generated
    echo ""
    echo -e "${BLUE}Generated files:${NC}"
    ls -la "$RESULT_DIR/init_resources/"*.json 2>/dev/null | head -20

    # Count files in manifest
    if [ -f "$RESULT_DIR/init_resources/analysis_manifest.json" ]; then
        FILE_COUNT=$(python -c "import json; m=json.load(open('$RESULT_DIR/init_resources/analysis_manifest.json')); print(len(m['file_to_component']))")
        echo -e "${BLUE}Files tracked in manifest: $FILE_COUNT${NC}"
    fi
else
    echo ""
    echo -e "${YELLOW}============================================${NC}"
    echo -e "${YELLOW}  INCREMENTAL-ONLY MODE${NC}"
    echo -e "${YELLOW}  Skipping full analysis, using existing${NC}"
    echo -e "${YELLOW}  analysis from previous run${NC}"
    echo -e "${YELLOW}============================================${NC}"
    echo ""
    
    # Use placeholder for full duration when in incremental-only mode
    FULL_DURATION=0
    
    # Check that existing analysis exists
    if [ -f "$RESULT_DIR/init_resources/analysis_manifest.json" ]; then
        echo -e "${GREEN}✓ Using existing analysis from $RESULT_DIR/init_resources/${NC}"
        FILE_COUNT=$(python -c "import json; m=json.load(open('$RESULT_DIR/init_resources/analysis_manifest.json')); print(len(m['file_to_component']))")
        echo -e "${BLUE}Files tracked in manifest: $FILE_COUNT${NC}"
    else
        echo -e "${RED}✗ ERROR: No existing analysis found${NC}"
        echo -e "${RED}  Checked: $RESULT_DIR/init_resources/analysis_manifest.json${NC}"
        echo -e "${RED}  Run without --incremental flag first to generate initial analysis${NC}"
        exit 1
    fi
fi

# ============================================
# STEP 2: Checkout end commit and run incremental
# ============================================
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}STEP 2: Incremental Analysis on end commit${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

cd "$REPO_DIR"
echo -e "${YELLOW}Checking out end commit: $END_COMMIT${NC}"
git checkout "$END_COMMIT" --quiet
END_COMMIT_SHORT=$(git rev-parse --short HEAD)
echo -e "${GREEN}✓ Now at commit: $END_COMMIT_SHORT${NC}"
cd - > /dev/null

# Show what changed between commits
echo ""
echo -e "${BLUE}Changes between $START_COMMIT and $END_COMMIT:${NC}"
cd "$REPO_DIR"
git diff --stat "$START_COMMIT" "$END_COMMIT" | tail -5
COMMIT_COUNT=$(git rev-list --count "$START_COMMIT".."$END_COMMIT")
echo -e "${BLUE}Number of commits: $COMMIT_COUNT${NC}"
cd - > /dev/null

# Clean up previous incremental results and prepare fresh directory
echo ""
echo -e "${YELLOW}Preparing clean incremental output directory...${NC}"
rm -rf "$RESULT_DIR/incr_resources"
mkdir -p "$RESULT_DIR/incr_resources"

# Copy initial analysis to incremental directory for the incremental run to use
echo -e "${YELLOW}Copying initial analysis for incremental update...${NC}"
cp -r "$RESULT_DIR/init_resources/"* "$RESULT_DIR/incr_resources/"
echo -e "${GREEN}✓ Initial analysis copied to incr_resources/${NC}"

# Verify manifest exists
if [ -f "$RESULT_DIR/incr_resources/analysis_manifest.json" ]; then
    echo -e "${GREEN}✓ Manifest ready for incremental analysis${NC}"
    echo -e "${BLUE}Manifest base_commit:${NC}"
    grep -o '"base_commit": "[^"]*"' "$RESULT_DIR/incr_resources/analysis_manifest.json"
else
    echo -e "${RED}✗ ERROR: Manifest not found in incr_resources!${NC}"
    exit 1
fi

echo ""
echo -e "${YELLOW}Starting INCREMENTAL analysis...${NC}"
echo -e "${YELLOW}This should be much faster. Time tracking started.${NC}"
echo ""

INCR_START=$(date +%s.%N)

# Run incremental analysis on the copied directory
python main.py \
    --local "$REPO_DIR" \
    --project-name "$PROJECT_NAME" \
    --output-dir "$RESULT_DIR/incr_resources" \
    --depth-level "$DEPTH_LEVEL" \
    --incremental \
    --load-env-variables \
    2>&1 | tee "${RESULT_DIR}/iterative_analys_${SHORT_END}.txt"

INCR_END=$(date +%s.%N)
INCR_DURATION=$(echo "$INCR_END - $INCR_START" | bc)

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}Incremental analysis completed!${NC}"
echo -e "${GREEN}Duration: ${INCR_DURATION} seconds${NC}"
echo -e "${GREEN}============================================${NC}"

# ============================================
# STEP 3: Summary
# ============================================
echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║           PERFORMANCE SUMMARY            ║${NC}"
echo -e "${BLUE}╠══════════════════════════════════════════╣${NC}"
if [ "$INCREMENTAL_ONLY" = false ]; then
    printf "${BLUE}║${NC} %-20s ${YELLOW}%18.2f s${NC} ${BLUE}║${NC}\n" "Full Analysis:" "$FULL_DURATION"
    printf "${BLUE}║${NC} %-20s ${GREEN}%18.2f s${NC} ${BLUE}║${NC}\n" "Incremental:" "$INCR_DURATION"
    SPEEDUP=$(echo "scale=1; $FULL_DURATION / $INCR_DURATION" | bc)
    printf "${BLUE}║${NC} %-20s ${GREEN}%17.1fx faster${NC} ${BLUE}║${NC}\n" "Speedup:" "$SPEEDUP"
else
    printf "${BLUE}║${NC} %-20s ${YELLOW}%18s${NC} ${BLUE}║${NC}\n" "Mode:" "INCREMENTAL-ONLY"
    printf "${BLUE}║${NC} %-20s ${GREEN}%18.2f s${NC} ${BLUE}║${NC}\n" "Incremental:" "$INCR_DURATION"
fi
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"

# Check if LLM was called during incremental
echo ""
if grep -q "HTTP Request: POST" "${RESULT_DIR}/iterative_analys_${SHORT_END}.txt"; then
    LLM_CALLS=$(grep -c "HTTP Request: POST" "${RESULT_DIR}/iterative_analys_${SHORT_END}.txt" || echo "0")
    echo -e "${YELLOW}⚠ LLM calls during incremental: $LLM_CALLS${NC}"
else
    echo -e "${GREEN}✓ No LLM calls during incremental update!${NC}"
fi

# Restore original state
echo ""
echo -e "${YELLOW}Restoring original state...${NC}"
cd "$REPO_DIR"
if [ "$ORIGINAL_BRANCH" != "detached" ]; then
    git checkout "$ORIGINAL_BRANCH" --quiet
else
    git checkout "$ORIGINAL_REF" --quiet
fi
echo -e "${GREEN}✓ Restored to: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || git rev-parse --short HEAD)${NC}"
cd - > /dev/null

echo ""
echo -e "${GREEN}Test complete!${NC}"
echo -e "${BLUE}Results saved in: $RESULT_DIR/${NC}"
echo -e "${BLUE}  - Initial analysis log: init_analys_${SHORT_START}.txt${NC}"
echo -e "${BLUE}  - Incremental analysis log: iterative_analys_${SHORT_END}.txt${NC}"
echo -e "${BLUE}  - Initial resources: init_resources/${NC}"
echo -e "${BLUE}  - Incremental resources: incr_resources/${NC}"
