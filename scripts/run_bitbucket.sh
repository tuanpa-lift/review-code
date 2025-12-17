#!/bin/bash
set -e

# ==============================================================================
# SCRIPT CONFIGURATION
# ==============================================================================
# Ensure we have necessary environment variables
if [ -z "$BITBUCKET_PR_ID" ]; then
  echo "Error: This script must be run within a Bitbucket Pipeline on a Pull Request."
  echo "Make sure the pipeline is triggered by a PR or 'pull-requests' specific trigger."
  exit 0 # Exit gracefully if not a PR to avoid failing build
fi

if [ -z "$LIFT_API_KEY" ]; then
  echo "Error: LIFT_API_KEY is missing."
  exit 1
fi

if [ -n "$BITBUCKET_ACCESS_TOKEN" ]; then
  AUTH_HEADER="Authorization: Bearer $BITBUCKET_ACCESS_TOKEN"
elif [ -n "$BITBUCKET_USERNAME" ] && [ -n "$BITBUCKET_APP_PASSWORD" ]; then
  # Fallback to App Password (Basic Auth) - construct header manually or use curl -u
  # We will use a variable for curl arguments to keep it clean
  AUTH_USER="$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD"
else
  echo "Error: Missing authentication credentials."
  echo "Please set BITBUCKET_ACCESS_TOKEN (Recommended) OR BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD in Repository Variables."
  exit 1
fi

# Define API URL
if [ -z "$API_URL" ]; then
  API_URL="https://jurisdiction-generates-environmental-nails.trycloudflare.com/api/v1/review-code/"
fi

# ==============================================================================
# 1. SETUP & FETCH PR DETAILS
# ==============================================================================
echo "🔧 Setting up environment..."

# Workspace/Repo derived from predefined vars
REPO_FULL_SLUG="${BITBUCKET_WORKSPACE}/${BITBUCKET_REPO_SLUG}"

echo "📥 Fetching Pull Request #$BITBUCKET_PR_ID Details from Bitbucket API..."

# Helper function to run curl with correct auth
curl_api() {
  if [ -n "$BITBUCKET_ACCESS_TOKEN" ]; then
    curl -s -H "Authorization: Bearer $BITBUCKET_ACCESS_TOKEN" "$@"
  else
    curl -s -u "$AUTH_USER" "$@"
  fi
}

PR_RESPONSE=$(curl_api "https://api.bitbucket.org/2.0/repositories/${REPO_FULL_SLUG}/pullrequests/${BITBUCKET_PR_ID}")

# Extract fields using Python (avoiding extra jq dependency if possible, but jq is safer)
# We will assume python3 is available since we need it for the generator anyway.
export PR_TITLE=$(echo "$PR_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['title'])")
export PR_BODY=$(echo "$PR_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('description', ''))")
export CREATOR=$(echo "$PR_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['author']['nickname'])")
export PR_URL=$(echo "$PR_RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin)['links']['html']['href'])")

export BASE_REF=$BITBUCKET_PR_DESTINATION_BRANCH
export HEAD_REF=$BITBUCKET_COMMIT
export BRANCH_NAME=$BITBUCKET_BRANCH
export COMMIT_SHA=$BITBUCKET_COMMIT
export PR_NUMBER=$BITBUCKET_PR_ID
export CUSTOM_INSTRUCTIONS="${CUSTOM_INSTRUCTIONS:-Check for security and performance issues}"
export OUTPUT_LANG="${OUTPUT_LANG:-vi}"

# Extract Reviewers (List of objects in Bitbucket API)
export REVIEWERS_JSON=$(echo "$PR_RESPONSE" | python3 -c "import sys, json; reviewers = json.load(sys.stdin).get('reviewers', []); print(json.dumps([{'login': r['nickname']} for r in reviewers]))")

echo "  - Title: $PR_TITLE"
echo "  - Branch: $HEAD_REF -> $BASE_REF"

# ==============================================================================
# 2. PREPARE GIT
# ==============================================================================
# Bitbucket does a shallow clone/checkout. We need to fetch the target branch to diff.
echo "🔄 Fetching base branch ($BASE_REF) for diff..."
git fetch origin ${BASE_REF}:${BASE_REF} --depth=100 || git fetch origin ${BASE_REF}

# ==============================================================================
# 3. GENERATE PAYLOAD
# ==============================================================================
echo "📝 Generating Review Payload..."

# Determine script path. Assumes this script is in [REPO]/scripts/run_bitbucket.sh
# and generate_payload.py is in [REPO]/scripts/generate_payload.py
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/generate_payload.py"

python3 "$PYTHON_SCRIPT"

# ==============================================================================
# 4. SEND TO API
# ==============================================================================
echo "🚀 Sending payload to LiftSoft API..."
HTTP_CODE=$(curl -s -o api_response.json -w "%{http_code}" -X POST "$API_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $LIFT_API_KEY" \
  -d @payload.json)

echo "API Response Status: $HTTP_CODE"

if [ "$HTTP_CODE" -ne 200 ]; then
  echo "Error: API returned status $HTTP_CODE"
  cat api_response.json
  exit 1
fi

# ==============================================================================
# 5. COMMENT ON PR
# ==============================================================================
REVIEW_TEXT=$(cat api_response.json | python3 -c "import sys, json; print(json.load(sys.stdin).get('review_text', ''))")
ISSUES_TEXT=$(cat api_response.json | python3 -c "import sys, json; print(json.load(sys.stdin).get('issues_text', ''))")

if [ -z "$REVIEW_TEXT" ] || [ "$REVIEW_TEXT" == "None" ]; then
  echo "⚠️ No review text generated."
else
  echo "💬 Posting review comment to Bitbucket..."
  
  # Escape content for JSON is tricky in bash. Using python to construct the json payload safely.
  python3 -c "
import json
import os

review = '''$REVIEW_TEXT'''
issues = '''$ISSUES_TEXT'''

full_comment = review
if issues and issues != 'None':
    full_comment += '\n\n' + issues

data = {'content': {'raw': full_comment}}
print(json.dumps(data))
" > comment_payload.json

  curl_api \
    -X POST \
    -H "Content-Type: application/json" \
    -d @comment_payload.json \
    "https://api.bitbucket.org/2.0/repositories/${REPO_FULL_SLUG}/pullrequests/${BITBUCKET_PR_ID}/comments"
    
  echo "✅ Comment posted successfully."
fi
