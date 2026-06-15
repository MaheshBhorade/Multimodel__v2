from __future__ import annotations

import argparse
import os
import posixpath
import tempfile
from pathlib import Path

import paramiko


DEFAULT_REMOTE_ROOT = "/opt/content_platform"


def ssh_connect(host: str, username: str, password: str) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # Try using the default private key
    key_path = os.path.expanduser("~/.ssh/id_rsa")
    if os.path.exists(key_path):
        try:
            print(f"Attempting key-based authentication with key: {key_path}")
            pkey = paramiko.RSAKey.from_private_key_file(key_path)
            ssh.connect(host, username=username, pkey=pkey, timeout=15)
            return ssh
        except Exception as key_err:
            print(f"Key-based authentication failed: {key_err}. Falling back to password.")
            
    ssh.connect(host, username=username, password=password, timeout=15)
    return ssh


def run(ssh: paramiko.SSHClient, command: str) -> str:
    _, stdout, stderr = ssh.exec_command(command)
    out = stdout.read().decode("utf-8", errors="ignore")
    err = stderr.read().decode("utf-8", errors="ignore")
    if err.strip():
        print(err.strip().encode("ascii", "ignore").decode("ascii"))
    return out


def upload_tree(sftp: paramiko.SFTPClient, local_root: Path, remote_root: str) -> None:
    for root, dirs, files in os.walk(local_root):
        dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "manual_ingestion", "docs", "tests", "videos"}]
        relative = Path(root).relative_to(local_root)
        remote_dir = posixpath.join(remote_root, relative.as_posix()).rstrip("/")
        if remote_dir:
            try:
                sftp.mkdir(remote_dir)
            except OSError:
                pass
        for file_name in files:
            ignored_endings = (".pyc", ".db", ".mp4", ".mkv", ".wav", ".zip", ".xlsx", ".png", ".jpg", ".jpeg")
            ignored_names = {"verify_implementation.py", "check.py", "PROJECT_STRUCTURE.json", "docker-compose.yml", "clip_vit_b32_vision.onnx"}
            if file_name.endswith(ignored_endings) or file_name in ignored_names:
                continue
            local_path = Path(root) / file_name
            remote_path = posixpath.join(remote_dir, file_name)
            sftp.put(str(local_path), remote_path)


def write_remote_file(sftp: paramiko.SFTPClient, remote_path: str, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        sftp.put(tmp_path, remote_path)
    finally:
        os.unlink(tmp_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy the content platform edge agent to a Raspberry Pi.")
    parser.add_argument("--host", default="100.85.114.118")
    parser.add_argument("--user", default="indi")
    parser.add_argument("--password", default="iNdI#@R-71!0")
    parser.add_argument("--device-id", default="pi-agent")
    parser.add_argument("--server-url", default="http://100.78.128.49:8000")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    ssh = ssh_connect(args.host, args.user, args.password)
    sftp = ssh.open_sftp()

    try:
        run(ssh, f"sudo mkdir -p {args.remote_root}")
        run(ssh, f"sudo chown -R {args.user}:{args.user} {args.remote_root}")
        upload_tree(sftp, repo_root, args.remote_root)

        env_content = "\n".join(
            [
                f"CRP_EDGE_SERVER_URL={args.server_url}",
                f"CRP_DEVICE_ID={args.device_id}",
                "CRP_EDGE_MODE=pi",
                "CRP_CAPTURE_INTERVAL_SECONDS=10",
            ]
        )
        write_remote_file(sftp, posixpath.join(args.remote_root, ".env"), env_content)

        run(ssh, "sudo apt-get update")
        run(ssh, "sudo apt-get install -y tesseract-ocr libsndfile1")
        run(ssh, f"cd {args.remote_root} && python3 -m venv .venv")
        run(ssh, f"cd {args.remote_root} && . .venv/bin/activate && python -m pip install --upgrade pip")
        run(ssh, f"cd {args.remote_root} && . .venv/bin/activate && python -m pip install -e '.[pi]'")

        service = f"""[Unit]
Description=Content Recognition Platform Edge Agent
After=network.target tailscale.service

[Service]
Type=simple
User={args.user}
WorkingDirectory={args.remote_root}
EnvironmentFile={posixpath.join(args.remote_root, '.env')}
ExecStart={args.remote_root}/.venv/bin/crp-edge
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1 XDG_RUNTIME_DIR=/run/user/1000

[Install]
WantedBy=multi-user.target
"""
        write_remote_file(sftp, "/tmp/crp-edge.service", service)
        run(ssh, "sudo mv /tmp/crp-edge.service /etc/systemd/system/crp-edge.service")
        run(ssh, "sudo systemctl disable --now tv_capture.service || true")
        run(ssh, "sudo systemctl daemon-reload")
        run(ssh, "sudo systemctl enable crp-edge.service")
        run(ssh, "sudo systemctl restart crp-edge.service")
        status = run(ssh, "sudo systemctl is-active crp-edge.service").strip()
        print(f"crp-edge.service status: {status}")
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    main()
