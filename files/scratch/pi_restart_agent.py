import paramiko
import sys

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    print(f"Connecting to Raspberry Pi ({host})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    print("Connected!")
    
    # Run audio fix first to ensure audio is unmuted and the source is active!
    print("Executing fix_pi_audio.sh script on Pi...")
    stdin, stdout, stderr = ssh.exec_command(f"echo '{password}' | sudo -S /opt/content_platform/scripts/fix_pi_audio.sh")
    sys.stdout.buffer.write(stdout.read())
    sys.stdout.buffer.write(stderr.read())
    
    # Check status of the service
    print("\nChecking crp-edge.service status...")
    stdin, stdout, stderr = ssh.exec_command("systemctl status crp-edge --no-pager")
    sys.stdout.buffer.write(stdout.read())
    
    ssh.close()
    print("\nRaspberry Pi agent restarted successfully!")
except Exception as e:
    print("Error:", e)
