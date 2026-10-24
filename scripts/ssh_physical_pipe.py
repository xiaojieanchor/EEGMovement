"""OpenSSH ProxyCommand: bind this transport's socket to an active physical adapter."""
import os,socket,struct,sys,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eeg_relay.network import physical_interfaces

def main():
    host,port=sys.argv[1],int(sys.argv[2])
    if host!='106.55.251.83' or port!=22:raise ValueError('Deployment SSH endpoint only')
    raw=None
    for index in physical_interfaces()+[None]:
        attempt=socket.socket(socket.AF_INET,socket.SOCK_STREAM);attempt.settimeout(10)
        try:
            if index is not None:attempt.setsockopt(socket.IPPROTO_IP,31,struct.pack('!I',index))
            attempt.connect((host,port));raw=attempt;break
        except OSError:attempt.close()
    if raw is None:raise ConnectionError('SSH endpoint unavailable')
    raw.settimeout(None)
    def receive():
        try:
            while True:
                data=raw.recv(65536)
                if not data:break
                offset=0
                while offset<len(data):offset+=os.write(sys.stdout.fileno(),data[offset:])
        except OSError:pass
    thread=threading.Thread(target=receive,daemon=True);thread.start()
    try:
        while True:
            data=os.read(sys.stdin.fileno(),65536)
            if not data:break
            raw.sendall(data)
    finally:
        try:raw.shutdown(socket.SHUT_WR)
        except OSError:pass
        thread.join(5);raw.close()

if __name__=='__main__':main()
