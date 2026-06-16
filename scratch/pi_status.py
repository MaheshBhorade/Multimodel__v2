import paramiko
import sys

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    
    # Check service status and recent journal logs
    print("Checking crp-edge.service systemctl status...")
    stdin, stdout, stderr = ssh.exec_command("sudo systemctl status crp-edge --no-pager")
    sys.stdout.buffer.write(stdout.read())
    
    print("\nRecent journalctl logs for crp-edge...")
    stdin, stdout, stderr = ssh.exec_command("sudo journalctl -u crp-edge -n 25 --no-pager")
    sys.stdout.buffer.write(stdout.read())
    
    ssh.close()
except Exception as e:
    print("Error:", e)
