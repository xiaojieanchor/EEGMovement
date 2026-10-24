package org.eegmovement.relay;

import android.content.Context;
import android.os.SystemClock;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.MGF1ParameterSpec;
import java.util.*;
import javax.crypto.*;
import javax.crypto.spec.*;
import javax.net.ssl.*;

/** Automatic role enrollment. Private key never leaves AndroidKeyStore or enters Unity. */
public final class EnrollmentBridge {
    private static final String ALIAS="eegmovement-enrollment-v1";
    private static final int MAX=16384;
    private final Context context;
    private final String base,ca;
    private final AtomicFile identity;
    private volatile String state="idle";
    private volatile boolean cancelled,running;
    private volatile HttpsURLConnection active;
    private Thread worker;

    public EnrollmentBridge(Context value,String endpoint,String certificate)throws Exception {
        context=value.getApplicationContext();base=endpoint;ca=certificate;
        URI uri=new URI(base);
        if(!"https".equals(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getQuery()!=null||uri.getFragment()!=null||!"/eeg/".equals(uri.getPath())||(uri.getPort()!=443&&uri.getPort()!=-1))throw new IOException("enrollment endpoint invalid");
        identity=new AtomicFile(new File(context.getFilesDir(),"eeg_identity.json"));
    }
    public String status(){return state;}
    public boolean running(){return running;}
    public String read()throws Exception {
        if(!identity.getBaseFile().exists())return "";
        String json=new String(bounded(identity.openRead()),StandardCharsets.UTF_8);validate(json);return json;
    }
    public void store(String json)throws Exception {
        validate(json);byte[] raw=json.getBytes(StandardCharsets.UTF_8);if(raw.length>MAX)throw new IOException("identity bound");
        FileOutputStream out=null;try{out=identity.startWrite();out.write(raw);out.getFD().sync();identity.finishWrite(out);}catch(Exception e){if(out!=null)identity.failWrite(out);throw e;}
    }
    private void validate(String json)throws Exception {
        JSONObject value=new JSONObject(json);URI target=new URI(value.getString("url"));URI origin=new URI(base);
        if(!"relay".equals(value.getString("mode"))||!"wss".equals(target.getScheme())||!origin.getHost().equals(target.getHost())||(target.getPort()!=443&&target.getPort()!=-1)||!"/eeg/v1/ws".equals(target.getPath())||target.getUserInfo()!=null||target.getQuery()!=null||target.getFragment()!=null)throw new IOException("identity route invalid");
        for(String field:new String[]{"device_id","hub_id","workspace"})if(!value.getString(field).matches("[0-9a-f]{32}"))throw new IOException("identity field invalid");
        for(String field:new String[]{"secret","auth_secret"})if(!value.getString(field).matches("[0-9a-f]{64}"))throw new IOException("identity key invalid");
        if(!ca.equals(value.getString("ca_pem")))throw new IOException("identity trust mismatch");
        if(value.has("role")&&!"quest".equals(value.getString("role")))throw new IOException("identity role invalid");
    }
    public synchronized void start(){
        if(running)return;cancelled=false;running=true;state="starting";
        worker=new Thread(this::enroll,"EEG automatic enrollment");worker.start();
    }
    public void cancel(){cancelled=true;HttpsURLConnection connection=active;if(connection!=null)connection.disconnect();Thread thread=worker;if(thread!=null)thread.interrupt();state="cancelled";}
    private KeyPair keyPair()throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias(ALIAS)){
            KeyPairGenerator generator=KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_RSA,"AndroidKeyStore");
            generator.initialize(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_DECRYPT).setKeySize(3072).setDigests(KeyProperties.DIGEST_SHA256,KeyProperties.DIGEST_SHA1).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_OAEP).build());generator.generateKeyPair();
        }
        return new KeyPair(store.getCertificate(ALIAS).getPublicKey(),(PrivateKey)store.getKey(ALIAS,null));
    }
    private static String pem(PublicKey key){return "-----BEGIN PUBLIC KEY-----\n"+Base64.getMimeEncoder(64,new byte[]{10}).encodeToString(key.getEncoded())+"\n-----END PUBLIC KEY-----\n";}
    private static String hex(byte[] bytes){StringBuilder value=new StringBuilder();for(byte b:bytes)value.append(String.format(Locale.ROOT,"%02x",b&255));return value.toString();}
    private void enroll(){
        long deadline=SystemClock.elapsedRealtime()+600000;boolean started=false;
        try{
            KeyPair key=keyPair();String request=UUID.randomUUID().toString().replace("-","");byte[] tokenBytes=new byte[32];new SecureRandom().nextBytes(tokenBytes);String token=hex(tokenBytes);
            JSONObject begin=new JSONObject().put("request_id",request).put("token",token).put("role","quest").put("public_key",pem(key.getPublic())).put("name","Quest 3S");
            JSONObject poll=new JSONObject().put("request_id",request).put("token",token);
            while(!cancelled&&SystemClock.elapsedRealtime()<deadline){
                try{
                    JSONObject reply=request(started?"v1/enroll/poll":"v1/enroll/start",started?poll:begin);
                    String next=reply.optString("state","");
                    if("role_occupied".equals(next)){state="role_occupied";started=false;}
                    else if("waiting_hub".equals(next)){state="waiting_hub";started=true;}
                    else if("ready".equals(next)){
                        JSONObject sealed=reply.getJSONObject("sealed");
                        byte[] plain=openSealed(key.getPrivate(),request,Base64.getDecoder().decode(sealed.getString("key")),Base64.getDecoder().decode(sealed.getString("nonce")),Base64.getDecoder().decode(sealed.getString("ciphertext")));
                        if(cancelled)return;String json=new String(plain,StandardCharsets.UTF_8);store(json);state="ready";return;
                    }else{state="server_response_invalid";started=false;}
                }catch(SSLException e){state="tls_verification_failed";}
                catch(SocketTimeoutException e){state="network_timeout";}
                catch(GeneralSecurityException e){state="envelope_rejected";started=false;}
                catch(Exception e){state="network_or_response_failed";}
                if(!cancelled)try{Thread.sleep(3000);}catch(InterruptedException e){if(cancelled)return;}
            }
            if(!cancelled)state="timed_out";
        }catch(Exception e){if(!cancelled)state="keystore_or_config_failed";}
        finally{running=false;if(cancelled)state="cancelled";}
    }
    private JSONObject request(String suffix,JSONObject body)throws Exception {
        if(cancelled)throw new InterruptedIOException();
        HttpsURLConnection connection=(HttpsURLConnection)new URL(base+suffix).openConnection();active=connection;
        try{
            connection.setSSLSocketFactory(RelayBridge.context(ca).getSocketFactory());
            // Keep the platform hostname verifier; redirects must never carry the enrollment token.
            connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(5000);connection.setReadTimeout(5000);connection.setRequestMethod("GET");connection.setUseCaches(false);
            connection.setRequestProperty("X-EEG-Request",Base64.getEncoder().encodeToString(body.toString().getBytes(StandardCharsets.UTF_8)));
            int code=connection.getResponseCode();
            if(code!=200&&code!=409)throw new IOException("enrollment HTTP rejected");
            InputStream stream=code==200?connection.getInputStream():connection.getErrorStream();if(stream==null)throw new IOException("enrollment response missing");
            JSONObject reply=new JSONObject(new String(bounded(stream),StandardCharsets.UTF_8));if(code==409&&!"role_occupied".equals(reply.optString("state")))throw new IOException("enrollment conflict");return reply;
        }finally{connection.disconnect();if(active==connection)active=null;}
    }
    private static byte[] bounded(InputStream stream)throws IOException {
        try(InputStream in=stream;ByteArrayOutputStream out=new ByteArrayOutputStream()){
            byte[] buffer=new byte[1024];int count;while((count=in.read(buffer))!=-1){if(out.size()+count>MAX)throw new IOException("enrollment response bound");out.write(buffer,0,count);}return out.toByteArray();
        }
    }
    public static byte[] openSealed(PrivateKey privateKey,String request,byte[] wrapped,byte[] nonce,byte[] ciphertext)throws GeneralSecurityException {
        if(wrapped.length!=384||nonce.length!=12||ciphertext.length<16||ciphertext.length>MAX)throw new GeneralSecurityException("envelope bounds");
        Cipher rsa=Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");rsa.init(Cipher.DECRYPT_MODE,privateKey,new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA1,PSource.PSpecified.DEFAULT));byte[] key=rsa.doFinal(wrapped);
        try{if(key.length!=32)throw new GeneralSecurityException("envelope key size");Cipher aes=Cipher.getInstance("AES/GCM/NoPadding");aes.init(Cipher.DECRYPT_MODE,new SecretKeySpec(key,"AES"),new GCMParameterSpec(128,nonce));aes.updateAAD(request.getBytes(StandardCharsets.UTF_8));return aes.doFinal(ciphertext);}finally{Arrays.fill(key,(byte)0);}
    }
}
