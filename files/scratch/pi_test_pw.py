import paramiko
import sys

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    
    cmd = "sudo -u indi XDG_RUNTIME_DIR=/run/user/1000 pactl list sources short"
    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    print("STDOUT:")
    sys.stdout.buffer.write(stdout.read())
    print("\nSTDERR:")
    sys.stdout.buffer.write(stderr.read())
    
    ssh.close()
except Exception as e:
    print("Error:", e)
