#!/bin/bash
set -e

# ==============================================================================
# SCRIPT CONFIGURATION
# ==============================================================================
if [ -z "$BITBUCKET_PR_ID" ]; then
  echo "Error: Not a PR context."
  exit 0
fi

if [ -z "$LIFT_AI_API_KEY" ]; then
  echo "Error: LIFT_AI_API_KEY is missing."
  exit 1
fi

# Auth logic (như cũ)
if [ -n "$BITBUCKET_ACCESS_TOKEN" ]; then
  AUTH_HEADER="Authorization: Bearer $BITBUCKET_ACCESS_TOKEN"
elif [ -n "$BITBUCKET_USERNAME" ] && [ -n "$BITBUCKET_APP_PASSWORD" ]; then
  AUTH_USER="$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD"
else
  echo "Error: Missing authentication credentials."
  exit 1
fi

if [ -z "$API_URL" ]; then
  # API_URL="https://api.freedl.blog/api/v1/review-code/" # Local or Prod URL
  API_URL="https://api.freedl.blog/api/v1/review-code/"
fi

REPO_FULL_SLUG="${BITBUCKET_WORKSPACE}/${BITBUCKET_REPO_SLUG}"

# Helper function
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
# 2. INSTALL DEPENDENCIES
# ==============================================================================
echo "📦 Installing Python dependencies..."
pip3 install requests --quiet

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
  -H "X-API-Key: $LIFT_AI_API_KEY" \
  -d @payload.json)

echo "API Response Status: $HTTP_CODE"

if [ "$HTTP_CODE" -ne 200 ]; then
  echo "Error: API returned status $HTTP_CODE"
  cat api_response.json
  exit 1
fi

# ==============================================================================
# 5. COMMENT ON PR (SAFE VERSION)
# ==============================================================================
echo "💬 Preparing comment..."

# SỬ DỤNG PYTHON ĐỂ ĐỌC FILE JSON TRỰC TIẾP
# Cách này an toàn tuyệt đối với mọi ký tự đặc biệt, unicode, code, quotes...
python3 -c "
import json
import sys
try:
    with open('api_response.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    review = data.get('review_text', '')
    issues = data.get('issues_text', '')
    if not review or review == 'None':
        print('NO_CONTENT')
        sys.exit(0)
    # Ghép nội dung
    full_comment = review
    if issues and issues != 'None' and issues.strip():
        full_comment += '\n\n' + issues
    # Tạo payload cho Bitbucket
    payload = {'content': {'raw': full_comment}}
    
    with open('comment_payload.json', 'w', encoding='utf-8') as out:
        json.dump(payload, out, ensure_ascii=False)
        
except Exception as e:
    print(f'Error processing response: {e}')
    sys.exit(1)
" > processing_output.txt

# Kiểm tra output từ python script
PROCESS_RESULT=$(cat processing_output.txt)

if [ "$PROCESS_RESULT" == "NO_CONTENT" ]; then
  echo "⚠️ No review text generated. Skipping comment."
else
  echo "💬 Posting review comment to Bitbucket..."
  
  curl_api \
    -X POST \
    -H "Content-Type: application/json" \
    -d @comment_payload.json \
    "https://api.bitbucket.org/2.0/repositories/${REPO_FULL_SLUG}/pullrequests/${BITBUCKET_PR_ID}/comments"
    
  echo "✅ Comment posted successfully."
fi
