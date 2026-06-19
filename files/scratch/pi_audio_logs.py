import paramiko
import sys

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    
    cmd = "journalctl -u crp-edge -n 50 --no-pager"
    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    # Read output bytes and decode using utf-8, replace unknown characters
    out_bytes = stdout.read()
    err_bytes = stderr.read()
    
    print("STDOUT:")
    sys.stdout.buffer.write(out_bytes)
    print("\nSTDERR:")
    sys.stdout.buffer.write(err_bytes)
    
    ssh.close()
except Exception as e:
    print("Error:", e)
