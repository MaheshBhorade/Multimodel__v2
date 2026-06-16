import paramiko
import sys

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    
    # Check service status without sudo
    print("Checking crp-edge.service status...")
    stdin, stdout, stderr = ssh.exec_command("systemctl status crp-edge --no-pager")
    sys.stdout.buffer.write(stdout.read())
    sys.stdout.buffer.write(stderr.read())
    
    # Check journalctl logs with sudo and -S
    print("\nChecking crp-edge.service logs...")
    stdin, stdout, stderr = ssh.exec_command(f"echo '{password}' | sudo -S journalctl -u crp-edge -n 15 --no-pager")
    sys.stdout.buffer.write(stdout.read())
    sys.stdout.buffer.write(stderr.read())
    
    ssh.close()
except Exception as e:
    print("Error:", e)
