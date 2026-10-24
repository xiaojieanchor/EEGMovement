package org.eegmovement.relay;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.CertificateFactory;
import java.util.*;
import java.util.concurrent.*;
import java.util.regex.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import javax.net.ssl.*;

/** Dedicated application trust and bounded RFC6455 lanes. No permissive TLS callback. */
public final class RelayBridge {
    public interface Listener { void binary(String lane,String base64); void state(boolean connected,String error); }
    private final String url,ca,workspace,device,auth,boot;
    private final Listener listener;
    private final ConcurrentHashMap<String,Lane> lanes=new ConcurrentHashMap<>();
    private volatile boolean stopped=false,ready=false;
    private volatile String serverIdentity;
    public String serverIdentity(){return serverIdentity;}
    private final SecureRandom random=new SecureRandom();
    private static final int MAX=5*1024*1024;
    public RelayBridge(String u,String c,String w,String d,String a,String b,Listener l) {url=u;ca=c;workspace=w;device=d;auth=a;boot=b;listener=l;}
    public static byte[] hexBytes(String hex) {
        if(hex.length()%2!=0)throw new IllegalArgumentException("hex");byte[] b=new byte[hex.length()/2];
        for(int i=0;i<b.length;i++){int a=Character.digit(hex.charAt(2*i),16),c=Character.digit(hex.charAt(2*i+1),16);if(a<0||c<0)throw new IllegalArgumentException("hex");b[i]=(byte)((a<<4)|c);}return b;
    }
    public static String hex(byte[] b){StringBuilder s=new StringBuilder();for(byte x:b)s.append(String.format(Locale.ROOT,"%02x",x&255));return s.toString();}
    public static SSLContext context(String pem)throws Exception {
        java.security.cert.Certificate cert=CertificateFactory.getInstance("X.509").generateCertificate(new ByteArrayInputStream(pem.getBytes(StandardCharsets.US_ASCII)));
        KeyStore store=KeyStore.getInstance(KeyStore.getDefaultType());store.load(null,null);store.setCertificateEntry("eeg-app-ca",cert);
        TrustManagerFactory tm=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());tm.init(store);
        SSLContext ctx=SSLContext.getInstance("TLS");ctx.init(null,tm.getTrustManagers(),new SecureRandom());return ctx;
    }
    public void start(){new Thread(()->{
        try{
            for(String name:new String[]{"control","data","backfill","preview"}){if(stopped)return;Lane lane=new Lane(name);lanes.put(name,lane);lane.open();}
            ready=true;listener.state(true,"");for(Lane lane:lanes.values()){new Thread(lane::read,"EEG relay "+lane.name).start();new Thread(lane::write,"EEG relay send "+lane.name).start();}
        }catch(Exception e){fail(e.getClass().getSimpleName()+": "+e.getMessage());}
    },"EEG relay connect").start();}
    public boolean send(String lane,String base64){
        if(!ready||stopped)return false;
        byte[] data=Base64.getDecoder().decode(base64);if(data.length>MAX)throw new IllegalArgumentException("frame bound");
        Lane l=lanes.get(lane);if(l==null)return false;
        if(!l.queue.offer(data)){if(!lane.equals("preview"))fail("bounded send queue full");return false;}return true;
    }
    public void close(){stopped=true;ready=false;for(Lane l:lanes.values())try{l.socket.close();}catch(Exception ignored){} }
    private void fail(String message){if(stopped)return;close();listener.state(false,message==null?"connection closed":message);}
    private final class Lane {
        final String name;SSLSocket socket;InputStream in;OutputStream out;ArrayBlockingQueue<byte[]> queue=new ArrayBlockingQueue<>(64);
        Lane(String n){name=n;}
        void open()throws Exception{
            URI u=new URI(url);if(!u.getScheme().equals("wss")||!u.getPath().equals("/eeg/v1/ws"))throw new IOException("invalid relay URL");
            Socket raw=new Socket();raw.connect(new InetSocketAddress(u.getHost(),u.getPort()<0?443:u.getPort()),5000);raw.setTcpNoDelay(true);
            socket=(SSLSocket)context(ca).getSocketFactory().createSocket(raw,u.getHost(),u.getPort()<0?443:u.getPort(),true);
            SSLParameters params=socket.getSSLParameters();params.setEndpointIdentificationAlgorithm("HTTPS");socket.setSSLParameters(params);socket.setSoTimeout(10000);socket.startHandshake();
            in=new BufferedInputStream(socket.getInputStream());out=socket.getOutputStream();byte[] nonce=new byte[16];random.nextBytes(nonce);String key=Base64.getEncoder().encodeToString(nonce);
            String host=u.getHost()+":"+(u.getPort()<0?443:u.getPort());String req="GET "+u.getRawPath()+" HTTP/1.1\r\nHost: "+host+"\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: "+key+"\r\n\r\n";
            out.write(req.getBytes(StandardCharsets.US_ASCII));out.flush();ByteArrayOutputStream headers=new ByteArrayOutputStream();int state=0;
            while(state<4){int x=in.read();if(x<0||headers.size()>16384)throw new IOException("upgrade response");headers.write(x);state=(state==0&&x==13)?1:(state==1&&x==10)?2:(state==2&&x==13)?3:(state==3&&x==10)?4:0;}
            String response=headers.toString("US-ASCII");String accept=Base64.getEncoder().encodeToString(MessageDigest.getInstance("SHA-1").digest((key+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").getBytes(StandardCharsets.US_ASCII)));
            boolean acceptMatched=false;for(String line:response.split("\r\n")){int colon=line.indexOf(':');if(colon>0&&line.substring(0,colon).equalsIgnoreCase("Sec-WebSocket-Accept")&&line.substring(colon+1).trim().equals(accept))acceptMatched=true;}
            if(!response.startsWith("HTTP/1.1 101")||!acceptMatched)throw new IOException("WebSocket upgrade rejected");
            String challenge=new String(frame(),StandardCharsets.UTF_8);Matcher m=Pattern.compile("\"challenge\"\\s*:\\s*\"([0-9a-f]{64})\"").matcher(challenge);if(!m.find())throw new IOException("challenge missing");
            Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(hexBytes(auth),"HmacSHA256"));String value=String.join("\n","eegmovement.relay.v1",m.group(1),workspace,device,boot,name);
            String json="{\"kind\":\"auth\",\"workspace\":\""+workspace+"\",\"device_id\":\""+device+"\",\"boot\":\""+boot+"\",\"lane\":\""+name+"\",\"mac\":\""+hex(mac.doFinal(value.getBytes(StandardCharsets.UTF_8)))+"\"}";
            frameOut(1,json.getBytes(StandardCharsets.UTF_8));String result=new String(frame(),StandardCharsets.UTF_8);if(!result.contains("authenticated"))throw new IOException("authentication rejected");
            Matcher identity=Pattern.compile("\"route_server\"\\s*:\\s*\"([0-9a-f]{32})\"").matcher(result);if(!identity.find())throw new IOException("relay process identity missing");
            if(serverIdentity!=null&&!serverIdentity.equals(identity.group(1)))throw new IOException("relay lanes disagree");serverIdentity=identity.group(1);socket.setSoTimeout(30000);
        }
        byte[] exact(int n)throws IOException{byte[] b=new byte[n];int at=0;while(at<n){int got=in.read(b,at,n-at);if(got<0)throw new EOFException();at+=got;}return b;}
        byte[] frame()throws Exception{
            while(true){int first=in.read(),second=in.read();if(first<0||second<0)throw new EOFException();int opcode=first&15;
                if((first&128)==0||(first&112)!=0||(second&128)!=0)throw new IOException("fragmented/masked server frame rejected");
                long n=second&127;if(n==126){byte[] b=exact(2);n=((b[0]&255)<<8)|(b[1]&255);}else if(n==127){n=0;for(byte b:exact(8)){n=(n<<8)|(b&255);if(n>MAX||n<0)throw new IOException("frame too large");}}
                if(n>MAX)throw new IOException("frame too large");byte[] payload=exact((int)n);
                if(opcode==8)throw new EOFException("relay closed");if(opcode==9){frameOut(10,payload);continue;}if(opcode==10)continue;if(opcode!=1&&opcode!=2)throw new IOException("unsupported frame");return payload;
            }
        }
        synchronized void frameOut(int opcode,byte[] data)throws IOException{
            out.write(128|opcode);int n=data.length;if(n<126)out.write(128|n);else if(n<=65535){out.write(128|126);out.write(n>>>8);out.write(n);}else{out.write(128|127);for(int i=7;i>=0;i--)out.write((int)(((long)n)>>(8*i)));}
            byte[] mask=new byte[4];random.nextBytes(mask);out.write(mask);byte[] encoded=new byte[n];for(int i=0;i<n;i++)encoded[i]=(byte)(data[i]^mask[i&3]);out.write(encoded);out.flush();
        }
        void read(){try{while(!stopped){byte[] data=frame();listener.binary(name,Base64.getEncoder().encodeToString(data));}}catch(Exception e){fail(e.getClass().getSimpleName());}}
        void write(){try{while(!stopped){byte[] b=queue.poll(1,TimeUnit.SECONDS);if(b!=null)frameOut(2,b);}}catch(Exception e){fail(e.getClass().getSimpleName());}}
    }
}
