import org.eegmovement.relay.RelayBridge;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.*;

public class RelayProbe {
    public static void main(String[] args)throws Exception {
        Properties p=new Properties();try(java.io.InputStream in=Files.newInputStream(Paths.get(args[0]))){p.load(in);}
        CountDownLatch connected=new CountDownLatch(1),reply=new CountDownLatch(1);String output=args[1];
        RelayBridge bridge=new RelayBridge(p.getProperty("url"),Files.readString(Paths.get(p.getProperty("ca"))),p.getProperty("workspace"),p.getProperty("device"),p.getProperty("auth"),p.getProperty("boot"),new RelayBridge.Listener(){
            public void state(boolean state,String error){if(state)connected.countDown();}
            public void binary(String lane,String data){try{Files.writeString(Paths.get(output),data);reply.countDown();}catch(Exception e){throw new RuntimeException(e);}}
        });
        bridge.start();if(!connected.await(20,TimeUnit.SECONDS))throw new Exception("native Java TLS/authentication failed");
        if(!bridge.send("control",p.getProperty("request")))throw new Exception("send not accepted");
        if(!reply.await(8,TimeUnit.SECONDS))throw new Exception("Hub reply missing");bridge.close();System.out.println("JAVA_TLS_AUTH_AND_HUB_REPLY_OK");
    }
}
