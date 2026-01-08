import os
import json
import subprocess
import re
import sys


def run_command(command):
    """Execute shell command and return output"""
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Error executing {' '.join(command)}: {e.stderr}", file=sys.stderr)
        return ""


def detect_platform():
    """Detect if running on GitHub or Bitbucket"""
    if os.environ.get('GITHUB_ACTIONS'):
        return 'github'
    elif os.environ.get('BITBUCKET_PIPELINE_UUID'):
        return 'bitbucket'
    else:
        return None


def get_repository_structure(root_dir=".", max_depth=2):
    """Generate a simple tree structure string of the repository."""
    structure = []
    root_dir = os.path.abspath(root_dir)
    exclude_dirs = {'.git', '__pycache__', 'node_modules', 'dist', 'build', 'venv', '.venv'}

    for root, dirs, files in os.walk(root_dir):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in exclude_dirs]

        level = root.replace(root_dir, '').count(os.sep)
        if level >= max_depth:
            continue

        indent = '  ' * level
        structure.append(f"{indent}{os.path.basename(root)}/")
        subindent = '  ' * (level + 1)

        # Limit files showing to avoid huge payload
        for i, f in enumerate(files):
            if i > 10:
                structure.append(f"{subindent}... (+{len(files)-10} files)")
                break
            structure.append(f"{subindent}{f}")

    return "\n".join(structure)


def get_dependencies_content():
    """Read content of common dependency files to help AI understand the tech stack."""
    interesting_files = [
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "go.mod",
        "Cargo.toml",
        "composer.json",
        "Gemfile"
    ]

    content_acc = []

    for filename in interesting_files:
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f:
                    content_acc.append(f"--- {filename} ---\n{f.read(2000)}")  # Limit size
            except Exception:
                pass

    return "\n\n".join(content_acc) if content_acc else None


def extract_linked_issues(pr_body, pr_url):
    """Simple regex to find 'Closes #123' etc in PR body."""
    if not pr_body:
        return []

    # Regex for standard GitHub/Bitbucket closure keywords
    regex = r"(?i)(close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+#(\d+)"
    matches = re.findall(regex, pr_body)

    linked_issues = []
    # Derive base repo URL from PR URL for issue links
    base_issue_url = pr_url.replace("/pull-requests/", "/issues/").replace("/pull/", "/issues/")
    base_issue_url = base_issue_url.rsplit('/', 1)[0]  # remove PR number

    seen_ids = set()

    for kw, issue_id in matches:
        if issue_id in seen_ids:
            continue
        seen_ids.add(issue_id)

        linked_issues.append({
            "title": f"Issue #{issue_id}",
            "body": f"Linked via keyword '{kw}'",
            "url": f"{base_issue_url}/{issue_id}"
        })

    return linked_issues


# ============================================================================
# GITHUB API FUNCTIONS
# ============================================================================

def fetch_github_pr_data():
    """Fetch PR data using GitHub API"""
    import requests
    
    # Get environment variables
    github_token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    repo = os.environ.get('GITHUB_REPOSITORY')
    pr_number = os.environ.get('PR_NUMBER')
    
    if not all([github_token, repo, pr_number]):
        print("Warning: Missing GitHub credentials, falling back to git commands")
        return None
    
    headers = {
        'Authorization': f'Bearer {github_token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    
    api_base = f'https://api.github.com/repos/{repo}/pulls/{pr_number}'
    
    try:
        # Get PR files from GitHub API
        files_url = f'{api_base}/files'
        files_response = requests.get(files_url, headers=headers)
        files_response.raise_for_status()
        files_data = files_response.json()
        
        files = []
        for file_info in files_data:
            status_map = {
                'added': 'added',
                'removed': 'deleted',
                'modified': 'modified',
                'renamed': 'renamed'
            }
            
            files.append({
                'filename': file_info['filename'],
                'status': status_map.get(file_info['status'], 'modified'),
                'patch': file_info.get('patch', '')
            })
        
        # Get commit messages
        commits_url = f'{api_base}/commits'
        commits_response = requests.get(commits_url, headers=headers)
        commits_response.raise_for_status()
        commits_data = commits_response.json()
        commit_messages = [commit['commit']['message'].split('\n')[0] for commit in commits_data]
        
        print(f"✅ Fetched {len(files)} files from GitHub API")
        return {'files': files, 'commit_messages': commit_messages}
        
    except Exception as e:
        print(f"Error fetching from GitHub API: {e}", file=sys.stderr)
        return None


# ============================================================================
# BITBUCKET API FUNCTIONS
# ============================================================================

def fetch_bitbucket_pr_data():
    """Fetch PR data using Bitbucket API"""
    import requests
    
    # Get environment variables
    workspace = os.environ.get('BITBUCKET_WORKSPACE')
    repo_slug = os.environ.get('BITBUCKET_REPO_SLUG')
    pr_id = os.environ.get('BITBUCKET_PR_ID')
    access_token = os.environ.get('BITBUCKET_ACCESS_TOKEN')
    
    if not all([workspace, repo_slug, pr_id]):
        print("Warning: Missing Bitbucket environment variables")
        return None
    
    # Setup authentication
    headers = {}
    auth = None
    
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    elif os.environ.get('BITBUCKET_USERNAME') and os.environ.get('BITBUCKET_APP_PASSWORD'):
        auth = (os.environ.get('BITBUCKET_USERNAME'), os.environ.get('BITBUCKET_APP_PASSWORD'))
    else:
        print("Warning: Missing Bitbucket authentication")
        return None
    
    api_base = f'https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}'
    
    try:
        # Get diffstat (list of files changed)
        diffstat_url = f'{api_base}/diffstat'
        diffstat_response = requests.get(diffstat_url, headers=headers, auth=auth)
        diffstat_response.raise_for_status()
        diffstat_data = diffstat_response.json()
        
        files = []
        for item in diffstat_data.get('values', []):
            # Determine filename and status
            if item.get('new'):
                filename = item['new']['path']
                if not item.get('old'):
                    status = 'added'
                elif item['old']['path'] != filename:
                    status = 'renamed'
                else:
                    status = 'modified'
            elif item.get('old'):
                filename = item['old']['path']
                status = 'deleted'
            else:
                continue
            
            # Get patch for this file from diff API
            diff_url = f'{api_base}/diff'
            params = {'path': filename}
            diff_response = requests.get(diff_url, headers=headers, auth=auth, params=params)
            
            if diff_response.status_code == 200:
                patch = diff_response.text
            else:
                patch = ''
            
            files.append({
                'filename': filename,
                'status': status,
                'patch': patch
            })
        
        # Get commit messages
        commits_url = f'{api_base}/commits'
        commits_response = requests.get(commits_url, headers=headers, auth=auth)
        commits_response.raise_for_status()
        commits_data = commits_response.json()
        commit_messages = [commit['message'].split('\n')[0] for commit in commits_data.get('values', [])]
        
        print(f"✅ Fetched {len(files)} files from Bitbucket API")
        return {'files': files, 'commit_messages': commit_messages}
        
    except Exception as e:
        print(f"Error fetching from Bitbucket API: {e}", file=sys.stderr)
        return None


# ============================================================================
# FALLBACK: GIT COMMANDS
# ============================================================================

def fetch_pr_data_via_git():
    """Fallback: Use git commands to get PR data"""
    print("⚠️ Using git commands as fallback...")
    
    base_ref = os.environ.get('BASE_REF')
    head_ref = os.environ.get('HEAD_REF')
    
    if not base_ref or not head_ref:
        print("Error: BASE_REF and HEAD_REF must be set")
        return None
    
    # Try to find merge-base
    print(f"Finding merge-base between {base_ref} and {head_ref}...")
    merge_base = run_command(['git', 'merge-base', base_ref, head_ref]).strip()
    
    if not merge_base:
        print(f"Warning: Could not find merge-base, using {base_ref} directly")
        merge_base = base_ref
    else:
        print(f"Merge-base: {merge_base}")
    
    # Get list of changed files
    print(f"Calculating diff between {merge_base} and {head_ref}...")
    output = run_command(['git', 'diff', '--name-status', merge_base, head_ref])
    
    files = []
    for line in output.strip().split('\n'):
        if not line:
            continue
        parts = line.split('\t')
        status_code = parts[0][0]
        filename = parts[-1]
        
        status_map = {'A': 'added', 'M': 'modified', 'D': 'deleted', 'R': 'renamed'}
        status = status_map.get(status_code, 'modified')
        
        # Get patch
        patch = run_command(['git', 'diff', '-U10', merge_base, head_ref, '--', filename])
        
        files.append({
            "filename": filename,
            "status": status,
            "patch": patch
        })
    
    # Get commit messages
    log_output = run_command(['git', 'log', '--format=%s', f"{merge_base}..{head_ref}"])
    commit_messages = [msg for msg in log_output.strip().split('\n') if msg]
    
    return {'files': files, 'commit_messages': commit_messages}


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("🚀 Starting payload generation...")
    
    # Detect platform
    platform = detect_platform()
    print(f"📍 Detected platform: {platform or 'unknown'}")
    
    # Get PR data based on platform
    pr_data = None
    
    if platform == 'github':
        pr_data = fetch_github_pr_data()
    elif platform == 'bitbucket':
        pr_data = fetch_bitbucket_pr_data()
    
    # Fallback to git commands if API fails
    if not pr_data:
        print("⚠️ API fetch failed, falling back to git commands...")
        pr_data = fetch_pr_data_via_git()
    
    if not pr_data or not pr_data['files']:
        print("❌ Error: Could not fetch PR data")
        sys.exit(1)
    
    files = pr_data['files']
    commit_messages = pr_data['commit_messages']
    
    # Get other metadata from environment
    pr_body = os.environ.get('PR_BODY', '')
    pr_url = os.environ.get('PR_URL', '')
    
    # Enhanced Context Collection
    repo_structure = get_repository_structure()
    dependencies = get_dependencies_content()
    linked_issues = extract_linked_issues(pr_body, pr_url)
    custom_instructions = os.environ.get('CUSTOM_INSTRUCTIONS')
    
    # Parse reviewers
    try:
        reviewers_json = os.environ.get('REVIEWERS_JSON', '[]')
        reviewers_data = json.loads(reviewers_json)
        reviewers = [r.get('login') or r.get('nickname') for r in reviewers_data if r.get('login') or r.get('nickname')]
    except Exception:
        reviewers = []
    
    # Build Payload
    payload = {
        "pr_title": os.environ.get('PR_TITLE'),
        "pr_number": os.environ.get('PR_NUMBER'),
        "commit_sha": os.environ.get('COMMIT_SHA'),
        "pr_description": pr_body,
        "branch_name": os.environ.get('BRANCH_NAME'),
        "pr_url": pr_url,
        "creator": os.environ.get('CREATOR'),
        "output_lang": os.environ.get('OUTPUT_LANG', 'vi'),
        "reviewers": reviewers,
        "files": files,
        "commit_messages": commit_messages,
        "linked_issues": linked_issues,
        "repository_structure": repo_structure,
        "dependencies": dependencies,
        "custom_instructions": custom_instructions
    }
    
    # Write payload
    output_path = os.environ.get('PAYLOAD_OUTPUT_PATH', 'payload.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Payload generated successfully!")
    print(f"   - Files: {len(files)}")
    print(f"   - Commits: {len(commit_messages)}")
    print(f"   - Output: {output_path}")


if __name__ == "__main__":
    main()
