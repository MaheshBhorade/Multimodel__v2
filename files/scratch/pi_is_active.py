import paramiko

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    print("Connecting...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # Connect with 5 second timeout
    ssh.connect(host, username=user, password=password, timeout=5)
    print("Connected!")
    
    # Run is-active
    stdin, stdout, stderr = ssh.exec_command("systemctl is-active crp-edge", timeout=5)
    status = stdout.read().decode("utf-8").strip()
    print("Service active status:", status)
    
    # Run ps aux
    stdin, stdout, stderr = ssh.exec_command("ps aux | grep crp-edge", timeout=5)
    ps_out = stdout.read().decode("utf-8").strip()
    print("Process details:\n", ps_out)
    
    ssh.close()
except Exception as e:
    print("Error:", e)
