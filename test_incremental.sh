#!/bin/bash
# Test script to compare full analysis vs incremental analysis times
# Usage: ./test_incremental.sh

set -e

# Configuration
REPO_DIR="repos/CodeBoarding"
OUTPUT_DIR="repos/CodeBoarding/.codeboarding"
OLD_COMMIT="e03132c97997a6dabf68cd5d2df6432601360edb"
PROJECT_NAME="CodeBoarding"
DEPTH_LEVEL=2

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  Incremental Analysis Test Script   ${NC}"
echo -e "${BLUE}======================================${NC}"
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

# Clean up any existing analysis
echo ""
echo -e "${YELLOW}Cleaning up existing analysis...${NC}"
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# ============================================
# STEP 1: Checkout old commit and run full analysis
# ============================================
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}STEP 1: Full Analysis on old commit${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

cd "$REPO_DIR"
echo -e "${YELLOW}Checking out old commit: $OLD_COMMIT${NC}"
git checkout "$OLD_COMMIT" --quiet
CURRENT_COMMIT=$(git rev-parse --short HEAD)
echo -e "${GREEN}✓ Now at commit: $CURRENT_COMMIT${NC}"
cd - > /dev/null

echo ""
echo -e "${YELLOW}Starting FULL analysis...${NC}"
echo -e "${YELLOW}This will take several minutes. Time tracking started.${NC}"
echo ""

FULL_START=$(date +%s.%N)

# Run full analysis (no --incremental flag)
python main.py \
    --local "$REPO_DIR" \
    --project-name "$PROJECT_NAME" \
    --output-dir "$OUTPUT_DIR" \
    --depth-level "$DEPTH_LEVEL" \
    --load-env-variables \
    -- full \
    2>&1 | tee /tmp/full_analysis.log

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
ls -la "$OUTPUT_DIR"/*.json 2>/dev/null | head -20

# Count files in manifest
if [ -f "$OUTPUT_DIR/analysis_manifest.json" ]; then
    FILE_COUNT=$(python -c "import json; m=json.load(open('$OUTPUT_DIR/analysis_manifest.json')); print(len(m['file_to_component']))")
    echo -e "${BLUE}Files tracked in manifest: $FILE_COUNT${NC}"
fi

# ============================================
# STEP 2: Checkout main and run incremental
# ============================================
echo ""
echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}STEP 2: Incremental Analysis on main${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# Move .codeboarding out of repo before checkout (git would block otherwise)
echo -e "${YELLOW}Preserving analysis files...${NC}"
TEMP_BACKUP="/tmp/codeboarding_backup_$$"
mv "$OUTPUT_DIR" "$TEMP_BACKUP"
echo -e "${GREEN}✓ Analysis backed up to $TEMP_BACKUP${NC}"

# Verify manifest was backed up
if [ -f "$TEMP_BACKUP/analysis_manifest.json" ]; then
    echo -e "${GREEN}✓ Manifest file verified in backup${NC}"
else
    echo -e "${RED}✗ ERROR: Manifest file NOT in backup!${NC}"
    exit 1
fi

cd "$REPO_DIR"
echo -e "${YELLOW}Checking out main branch...${NC}"
git checkout main --quiet
MAIN_COMMIT=$(git rev-parse --short HEAD)
echo -e "${GREEN}✓ Now at commit: $MAIN_COMMIT${NC}"
cd - > /dev/null

# Remove any .codeboarding that git might have restored from main
rm -rf "$OUTPUT_DIR"

# Restore our backed up .codeboarding with the manifest
mv "$TEMP_BACKUP" "$OUTPUT_DIR"
echo -e "${GREEN}✓ Analysis restored${NC}"

# Verify manifest exists after restore
if [ -f "$OUTPUT_DIR/analysis_manifest.json" ]; then
    echo -e "${GREEN}✓ Manifest verified at $OUTPUT_DIR/analysis_manifest.json${NC}"
    # Show manifest contents for debugging
    echo -e "${BLUE}Manifest base_commit:${NC}"
    grep -o '"base_commit": "[^"]*"' "$OUTPUT_DIR/analysis_manifest.json"
else
    echo -e "${RED}✗ ERROR: Manifest NOT restored!${NC}"
    ls -la "$OUTPUT_DIR/"
    exit 1
fi

# Show what changed between commits
echo ""
echo -e "${BLUE}Changes between $OLD_COMMIT and main:${NC}"
cd "$REPO_DIR"
git diff --stat "$OLD_COMMIT" HEAD | tail -5
COMMIT_COUNT=$(git rev-list --count "$OLD_COMMIT"..HEAD)
echo -e "${BLUE}Number of commits: $COMMIT_COUNT${NC}"
cd - > /dev/null

echo ""
echo -e "${YELLOW}Starting INCREMENTAL analysis...${NC}"
echo -e "${YELLOW}This should be much faster. Time tracking started.${NC}"
echo ""

INCR_START=$(date +%s.%N)

# Run incremental analysis
python main.py \
    --local "$REPO_DIR" \
    --project-name "$PROJECT_NAME" \
    --output-dir "$OUTPUT_DIR" \
    --depth-level "$DEPTH_LEVEL" \
    --incremental \
    --load-env-variables \
    2>&1 | tee /tmp/incremental_analysis.log

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
printf "${BLUE}║${NC} %-20s ${YELLOW}%18.2f s${NC} ${BLUE}║${NC}\n" "Full Analysis:" "$FULL_DURATION"
printf "${BLUE}║${NC} %-20s ${GREEN}%18.2f s${NC} ${BLUE}║${NC}\n" "Incremental:" "$INCR_DURATION"
SPEEDUP=$(echo "scale=1; $FULL_DURATION / $INCR_DURATION" | bc)
printf "${BLUE}║${NC} %-20s ${GREEN}%17.1fx faster${NC} ${BLUE}║${NC}\n" "Speedup:" "$SPEEDUP"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"

# Check if LLM was called during incremental
echo ""
if grep -q "HTTP Request: POST" /tmp/incremental_analysis.log; then
    LLM_CALLS=$(grep -c "HTTP Request: POST" /tmp/incremental_analysis.log || echo "0")
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
echo -e "${BLUE}Full log: /tmp/full_analysis.log${NC}"
echo -e "${BLUE}Incremental log: /tmp/incremental_analysis.log${NC}"
