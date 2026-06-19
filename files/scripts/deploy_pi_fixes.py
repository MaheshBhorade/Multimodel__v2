import paramiko
import os
from pathlib import Path

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"
remote_root = "/opt/content_platform"

repo_root = Path(__file__).resolve().parents[1]

try:
    print(f"Connecting to Raspberry Pi ({host})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    sftp = ssh.open_sftp()
    print("Connected!")
    
    # 1. Upload updated hardware.py
    local_hardware_py = repo_root / "src" / "content_platform" / "edge" / "hardware.py"
    remote_hardware_py = f"{remote_root}/src/content_platform/edge/hardware.py"
    print(f"Uploading {local_hardware_py} -> {remote_hardware_py}...")
    sftp.put(str(local_hardware_py), remote_hardware_py)
    
    # 2. Upload fix_pi_audio.sh
    local_fix_sh = repo_root / "scripts" / "fix_pi_audio.sh"
    remote_fix_sh = f"{remote_root}/scripts/fix_pi_audio.sh"
    print(f"Uploading {local_fix_sh} -> {remote_fix_sh}...")
    sftp.put(str(local_fix_sh), remote_fix_sh)
    
    # 3. Make the script executable
    print("Making fix script executable...")
    ssh.exec_command(f"chmod +x {remote_fix_sh}")
    
    # 4. Run the fix script
    print("Executing fix script on Pi...")
    # Using password for sudo execution
    stdin, stdout, stderr = ssh.exec_command(f"echo '{password}' | sudo -S {remote_fix_sh}")
    
    print("\n=== Script Output ===")
    for line in stdout:
        print(line.rstrip())
        
    err = stderr.read().decode("utf-8", errors="ignore")
    if err.strip():
        # filter out the sudo password warning
        filtered_err = "\n".join([line for line in err.splitlines() if "password" not in line.lower()])
        if filtered_err.strip():
            print("\n=== Script Error/Warnings ===")
            print(filtered_err.strip())
            
    # Check if edge agent has restarted and is active
    stdin, stdout, stderr = ssh.exec_command("sudo systemctl is-active crp-edge.service")
    status = stdout.read().decode("utf-8").strip()
    print(f"\ncrp-edge.service status: {status}")
    
    sftp.close()
    ssh.close()
    print("\nDeployment of audio fix complete!")
except Exception as e:
    print("Error during deployment:", e)
