package org.eegmovement.relay;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;
import org.json.JSONObject;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;
import javax.net.ssl.*;

/** APK self-update. Installation is confirmed by Android and subsequent startup. */
public final class UpdateBridge {
    public interface Listener {void progress(String state,String message);}
    private final Activity activity;private final Listener listener;private final JSONObject config;
    private volatile boolean safe=false,stopped=false,busy=false;private File ready;private JSONObject manifest;private long nextCheck=0;
    private static final String CERT="656c3b5b180f1cac817a881ed65642c04ac3f6b39fba2351f632094791e549ce";
    public UpdateBridge(Activity a,String json,Listener l)throws Exception {activity=a;config=new JSONObject(json);listener=l;}
    void report(String state,String text){listener.progress(state,text);activity.getSharedPreferences("eeg_update",0).edit().putString("state",state).putString("message",text).apply();}
    public void setSafe(boolean value){safe=value;}
    public String status(){android.content.SharedPreferences p=activity.getSharedPreferences("eeg_update",0);return p.getString("message","更新等待检查");}
    public void stop(){stopped=true;}
    public void check(){nextCheck=0;tick();}
    public void tick(){if(stopped||busy||System.currentTimeMillis()<nextCheck)return;nextCheck=System.currentTimeMillis()+600000;busy=true;new Thread(()->{try{if(ready!=null&&ready.exists()){install();}else inspect();}catch(Exception e){report("failed",e.getClass().getSimpleName()+": "+e.getMessage());}finally{busy=false;}},"EEG signed APK update").start();}
    HttpsURLConnection connection(String path)throws Exception {
        URL base=new URL(config.getString("base_url")),url=new URL(base,path);
        if(!url.getProtocol().equals("https")||!url.getHost().equals(base.getHost())||url.getPort()!=base.getPort())throw new IOException("foreign update URL");
        HttpsURLConnection c=(HttpsURLConnection)url.openConnection();c.setSSLSocketFactory(RelayBridge.context(config.getString("ca_pem")).getSocketFactory());c.setInstanceFollowRedirects(false);c.setConnectTimeout(8000);c.setReadTimeout(15000);return c;
    }
    byte[] bounded(InputStream in,int maximum)throws Exception{ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[8192];int n;while((n=in.read(b))>=0){if(out.size()+n>maximum)throw new IOException("response bound");out.write(b,0,n);}return out.toByteArray();}
    void inspect()throws Exception {
        report("checking","正在检查签名更新");HttpsURLConnection c=connection("updates/quest-arm64.json");byte[] bytes;
        try{if(c.getResponseCode()!=200)throw new IOException("manifest HTTP "+c.getResponseCode());try(InputStream in=c.getInputStream()){bytes=bounded(in,262144);}}finally{c.disconnect();}
        JSONObject wrapper=new JSONObject(new String(bytes,StandardCharsets.UTF_8));byte[] payload=Base64.getDecoder().decode(wrapper.getString("payload"));
        String pem=config.getString("release_public_key").replace("-----BEGIN PUBLIC KEY-----","").replace("-----END PUBLIC KEY-----","").replaceAll("\\s","");
        PublicKey publicKey=KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(pem)));
        java.security.Signature verifier=java.security.Signature.getInstance("SHA256withRSA");verifier.initVerify(publicKey);verifier.update(payload);if(!verifier.verify(Base64.getDecoder().decode(wrapper.getString("signature"))))throw new IOException("manifest signature rejected");
        JSONObject m=new JSONObject(new String(payload,StandardCharsets.UTF_8));long installed=activity.getPackageManager().getPackageInfo(activity.getPackageName(),0).getLongVersionCode();
        if(!m.getString("schema").equals("eegmovement.update.v1")||!m.getString("platform").equals("quest-arm64")||m.getInt("protocol")!=1||!m.getString("package_name").equals(activity.getPackageName())||!m.getString("certificate_sha256").equals(CERT))throw new IOException("platform/package/signature identity");
        if(m.getLong("version_code")<=installed){report("current","当前已是最新版本");return;}
        long high=activity.getSharedPreferences("eeg_update",0).getLong("release_seq",0);if(m.getLong("release_seq")<high)throw new IOException("release replay");
        if(!m.getString("path").matches("releases/[A-Za-z0-9._-]+\\.apk")||m.getLong("size")<1||m.getLong("size")>1073741824L||!m.getString("sha256").matches("[0-9a-f]{64}"))throw new IOException("artifact bounds");
        manifest=m;activity.getSharedPreferences("eeg_update",0).edit().putLong("release_seq",m.getLong("release_seq")).apply();
        if(!safe){report("waiting_idle","采集和动作停止后自动下载");nextCheck=System.currentTimeMillis()+10000;return;}
        File folder=new File(activity.getFilesDir(),"signed_updates");folder.mkdirs();File file=new File(folder,m.getString("sha256")+".apk.part");long offset=file.exists()?file.length():0,size=m.getLong("size");if(offset>size){if(!file.delete())throw new IOException("partial reset");offset=0;}
        if(folder.getUsableSpace()<size-offset+268435456L)throw new IOException("磁盘空间不足");
        if(offset<size){
            c=connection(m.getString("path"));if(offset>0)c.setRequestProperty("Range","bytes="+offset+"-");
            try{
                int status=c.getResponseCode();if(offset>0&&status!=206)offset=0;if(status!=200&&status!=206)throw new IOException("download HTTP "+status);
                if(offset>0&&!c.getHeaderField("Content-Range").startsWith("bytes "+offset+"-"))throw new IOException("range mismatch");
                try(InputStream in=c.getInputStream();FileOutputStream out=new FileOutputStream(file,offset>0)){
                    byte[] b=new byte[65536];int n;long count=offset;
                    while((n=in.read(b))>=0){if(stopped||!safe){report("waiting_idle","下载已暂停，空闲后断点恢复");nextCheck=System.currentTimeMillis()+10000;return;}count+=n;if(count>size)throw new IOException("download exceeded signed size");out.write(b,0,n);report("downloading","下载更新 "+count*100/size+"%");Thread.sleep(Math.max(1,n*1000L/98304));}out.getFD().sync();
                }
            }finally{c.disconnect();}
        }
        if(file.length()!=size||!hash(file).equals(m.getString("sha256")))throw new IOException("APK size/hash mismatch");
        PackageInfo apk=activity.getPackageManager().getPackageArchiveInfo(file.getPath(),PackageManager.GET_SIGNING_CERTIFICATES);
        if(apk==null||!apk.packageName.equals(activity.getPackageName())||apk.getLongVersionCode()!=m.getLong("version_code")||apk.signingInfo==null)throw new IOException("APK package identity mismatch");
        android.content.pm.Signature[] signatures=apk.signingInfo.getApkContentsSigners();if(signatures.length!=1||!RelayBridge.hex(MessageDigest.getInstance("SHA-256").digest(signatures[0].toByteArray())).equals(CERT))throw new IOException("APK signer rejected");
        ready=file;report("verified","APK签名与SHA256已通过");install();
    }
    static String hash(File file)throws Exception{MessageDigest d=MessageDigest.getInstance("SHA-256");try(InputStream in=new FileInputStream(file)){byte[] b=new byte[65536];int n;while((n=in.read(b))>=0)d.update(b,0,n);}return RelayBridge.hex(d.digest());}
    public void confirm(){
        if(!safe){report("waiting_idle","请先结束采集并停止动作");return;}
        if(!activity.getPackageManager().canRequestPackageInstalls())activity.startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+activity.getPackageName())));
        else {nextCheck=0;tick();}
    }
    void install()throws Exception {
        if(!safe||stopped){report("waiting_idle","等待停止采集与动作");nextCheck=System.currentTimeMillis()+10000;return;}
        if(!activity.getPackageManager().canRequestPackageInstalls()){report("needs_permission","更新已下载：在链路页点击软件更新，允许本应用安装更新");nextCheck=System.currentTimeMillis()+10000;return;}
        PackageInstaller installer=activity.getPackageManager().getPackageInstaller();PackageInstaller.SessionParams params=new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);params.setAppPackageName(activity.getPackageName());params.setSize(ready.length());params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
        int id=installer.createSession(params);
        try(PackageInstaller.Session session=installer.openSession(id)){
            try(InputStream in=new FileInputStream(ready);OutputStream out=session.openWrite("base.apk",0,ready.length())){
                byte[] b=new byte[65536];int n;while((n=in.read(b))>=0){if(!safe)throw new IOException("安装前状态改变");out.write(b,0,n);}session.fsync(out);
            }
            if(!safe)throw new IOException("安装前状态改变");Intent intent=new Intent(activity,InstallResult.class).setAction(activity.getPackageName()+".EEG_UPDATE");PendingIntent result=PendingIntent.getBroadcast(activity,id,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_MUTABLE);
            report("install_requested","正在请求系统安装；若系统提示，请在头显确认");session.commit(result.getIntentSender());nextCheck=Long.MAX_VALUE;
        }catch(Exception e){installer.abandonSession(id);throw e;}
    }
    public static final class InstallResult extends BroadcastReceiver {
        @Override public void onReceive(Context context,Intent intent){
            int status=intent.getIntExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE);String state="failed",text=intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
            if(status==PackageInstaller.STATUS_PENDING_USER_ACTION){state="awaiting_system_confirmation";text="请在系统安装窗口确认更新";Intent confirm=intent.getParcelableExtra(Intent.EXTRA_INTENT);if(confirm!=null){confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);context.startActivity(confirm);}}
            else if(status==PackageInstaller.STATUS_SUCCESS){state="installed_waiting_launch";text="安装成功，等待新版本启动";}
            context.getSharedPreferences("eeg_update",0).edit().putString("state",state).putString("message",text==null?state:text).apply();
        }
    }
}
