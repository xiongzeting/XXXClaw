import socket

sock = socket.socket()
sock.settimeout(1)
result = sock.connect_ex(("1.1.1.1", 53))
print("BLOCKED" if result else "CONNECTED")
