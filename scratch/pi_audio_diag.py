import paramiko

host = "100.85.114.118"
user = "indi"
password = "iNdI#@R-71!0"

try:
    print(f"Connecting to Pi ({host})...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    print("Connected successfully!")
    
    commands = [
        "uname -a",
        "arecord -l",
        "pactl list sources short 2>/dev/null || echo 'pactl not available'",
        "amixer -c 1 scontents 2>/dev/null || amixer scontents 2>/dev/null || echo 'amixer not available'",
        "ls -l /dev/snd",
        "systemctl status crp-edge --no-pager",
        "journalctl -u crp-edge -n 20 --no-pager"
    ]
    
    for cmd in commands:
        print(f"\n================ Running: {cmd} ================")
        stdin, stdout, stderr = ssh.exec_command(cmd)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        if out.strip():
            print("STDOUT:")
            print(out.strip())
        if err.strip():
            print("STDERR:")
            print(err.strip())
            
    ssh.close()
except Exception as e:
    print("Error connecting/executing:", e)
