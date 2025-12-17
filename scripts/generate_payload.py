import os
import json
import subprocess
import re


def run_command(command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Error executing {' '.join(command)}: {e.stderr}")
        return ""


def get_repository_structure(root_dir=".", max_depth=2):
    """
    Generate a simple tree structure string of the repository.
    """
    structure = []
    root_dir = os.path.abspath(root_dir)
    exclude_dirs = {'.git', '__pycache__',
                    'node_modules', 'dist', 'build', 'venv', '.venv'}

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
    """
    Read content of common dependency files to help AI understand the tech stack.
    """
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
                    content_acc.append(
                        f"--- {filename} ---\n{f.read(2000)}")  # Limit size
            except Exception:
                pass

    return "\n\n".join(content_acc) if content_acc else None


def extract_linked_issues(pr_body, pr_url):
    """
    Simple regex to find 'Closes #123' etc in PR body.
    """
    if not pr_body:
        return []

    # Regex for standard GitHub closure keywords
    # format: keyword #number
    regex = r"(?i)(close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+#(\d+)"
    matches = re.findall(regex, pr_body)

    linked_issues = []
    # Derive base repo URL from PR URL for issue links
    # PR URL: https://github.com/owner/repo/pull/123
    # Issue URL: https://github.com/owner/repo/issues/123
    base_issue_url = pr_url.replace("/pull/", "/issues/")
    base_issue_url = base_issue_url.rsplit('/', 1)[0]  # remove PR number

    seen_ids = set()

    for kw, issue_id in matches:
        if issue_id in seen_ids:
            continue
        seen_ids.add(issue_id)

        linked_issues.append({
            # We don't have title unless we fetch via API, this is a fallback placeholder
            "title": f"Issue #{issue_id}",
            "body": f"Linked via keyword '{kw}'",
            "url": f"{base_issue_url}/{issue_id}"
        })

    return linked_issues


def main():
    # ENV from action.yml
    base_ref = os.environ.get('BASE_REF')
    head_ref = os.environ.get('HEAD_REF')
    pr_body = os.environ.get('PR_BODY', '')
    pr_url = os.environ.get('PR_URL', '')

    # 1. Lấy danh sách file thay đổi
    print(f"Calculating diff between {base_ref} and {head_ref}...")
    output = run_command(['git', 'diff', '--name-status', base_ref, head_ref])

    files = []
    for line in output.strip().split('\n'):
        if not line:
            continue
        parts = line.split('\t')
        status_code = parts[0][0]
        filename = parts[-1]

        status_map = {'A': 'added', 'M': 'modified',
                      'D': 'deleted', 'R': 'renamed'}
        status = status_map.get(status_code, 'modified')

        # Get patch
        patch = run_command(
            ['git', 'diff', '-U10', base_ref, head_ref, '--', filename])

        files.append({
            "filename": filename,
            "status": status,
            "patch": patch
        })

    # 2. Get commit messages
    log_output = run_command(
        ['git', 'log', '--format=%s', f"{base_ref}..{head_ref}"])
    commit_messages = [msg for msg in log_output.strip().split('\n') if msg]

    # 3. Enhanced Context Collection (CodeRabbit-style features)
    repo_structure = get_repository_structure()
    dependencies = get_dependencies_content()
    linked_issues = extract_linked_issues(pr_body, pr_url)
    custom_instructions = os.environ.get('CUSTOM_INSTRUCTIONS')

    # 4. Build Payload
    try:
        reviewers_json = os.environ.get('REVIEWERS_JSON', '[]')
        reviewers_data = json.loads(reviewers_json)
        reviewers = [r['login'] for r in reviewers_data]
    except:
        reviewers = []

    payload = {
        "pr_title": os.environ.get('PR_TITLE'),
        "pr_number": os.environ.get('PR_NUMBER'),
        "commit_sha": os.environ.get('COMMIT_SHA'),
        "pr_description": pr_body,
        "branch_name": os.environ.get('BRANCH_NAME'),
        "pr_url": pr_url,
        "creator": os.environ.get('CREATOR'),
        "output_lang": os.environ.get('OUTPUT_LANG', 'en'),
        "reviewers": reviewers,
        "files": files,
        "commit_messages": commit_messages,
        "linked_issues": linked_issues,
        "repository_structure": repo_structure,
        "dependencies": dependencies,
        "custom_instructions": custom_instructions
    }

    output_path = os.environ.get('PAYLOAD_OUTPUT_PATH', 'payload.json')
    with open(output_path, 'w') as f:
        json.dump(payload, f)

    print(f"Payload generated at {output_path}")


if __name__ == "__main__":
    main()
