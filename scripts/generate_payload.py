import os
import json
import subprocess


def run_command(command):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Error executing {' '.join(command)}: {e.stderr}")
        return ""


def main():
    # ENV from action.yml
    base_ref = os.environ.get('BASE_REF')
    head_ref = os.environ.get('HEAD_REF')

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

    # 3. Build Payload
    # Parse reviewers JSON safely
    try:
        reviewers_json = os.environ.get('REVIEWERS_JSON', '[]')
        reviewers_data = json.loads(reviewers_json)
        reviewers = [r['login'] for r in reviewers_data]
    except:
        reviewers = []

    payload = {
        "pr_title": os.environ.get('PR_TITLE'),
        "pr_description": os.environ.get('PR_BODY', ''),
        "branch_name": os.environ.get('BRANCH_NAME'),
        "pr_url": os.environ.get('PR_URL'),
        "creator": os.environ.get('CREATOR'),
        "output_lang": os.environ.get('OUTPUT_LANG', 'en'),
        "reviewers": reviewers,
        "files": files,
        "commit_messages": commit_messages
    }

    output_path = os.environ.get('PAYLOAD_OUTPUT_PATH', 'payload.json')
    with open(output_path, 'w') as f:
        json.dump(payload, f)

    print(f"Payload generated at {output_path}")


if __name__ == "__main__":
    main()
