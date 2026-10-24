using System;
using System.IO;
using System.Text;
using System.Security.Cryptography;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;
using EEGMovement.Core;
using UnityEngine;

namespace EEGMovement.Unity {
    // Transport only: receiver-issued tickets and Wire validation stay in ReceiverClient.
    public sealed class PublicRelay : IDisposable {
        [Serializable] public sealed class Enrollment {public string mode,url,workspace,device_id,hub_id,secret,auth_secret,ca_pem;}
        [Serializable] public sealed class Header {public string schema="eegmovement.relay.v1",workspace,@from,to,boot,kind,stream="",digest,mac,delivery,route_server;public long seq,epoch,block_seq,route_generation;}
        [Serializable] sealed class Notice {public string notice,platform;}
        [Serializable] sealed class Clock {public string nonce,phase;public double t0,t1,t2,t3;}
        [Serializable] sealed class RpcRequest {public string request_id,method,path,body;}
        [Serializable] sealed class Reply {public string request_id,body,error;public int status;}
        [Serializable] sealed class Archived {public string event_id,cache_id;}
        [Serializable] sealed class Offline {public string event_id,request;}
        public readonly Enrollment Config;
        public bool Connected {get;private set;}
        public string Error {get;private set;}
        public event Action<bool> ConnectionChanged;
        public event Action<byte[]> WireReceived;
        public bool UpdateAvailable {get;private set;}
        string routeServer;long hubGeneration;string hubBoot;double nextClock;
        readonly object gate=new object();readonly Dictionary<string,TaskCompletionSource<string>> pending=new Dictionary<string,TaskCompletionSource<string>>();
        readonly Dictionary<string,long> highs=new Dictionary<string,long>();readonly Dictionary<string,HashSet<long>> seen=new Dictionary<string,HashSet<long>>();
        readonly string boot=Guid.NewGuid().ToString("N"),offlineRoot;long sequence;
        AndroidJavaObject bridge;Callback callback;bool closed,rpcLogged;
        sealed class Callback:AndroidJavaProxy {
            readonly PublicRelay owner;public Callback(PublicRelay o):base("org.eegmovement.relay.RelayBridge$Listener"){owner=o;}
            public void binary(string lane,string data){owner.Receive(lane,Convert.FromBase64String(data));}
            public void state(bool connected,string error){owner.State(connected,error);}
        }
        public PublicRelay(string json,string root){Config=JsonUtility.FromJson<Enrollment>(json);offlineRoot=Path.Combine(root,"relay_pending_events");Directory.CreateDirectory(offlineRoot);}
        public void Start(){callback=new Callback(this);bridge=new AndroidJavaObject("org.eegmovement.relay.RelayBridge",Config.url,Config.ca_pem,Config.workspace,Config.device_id,Config.auth_secret,boot,callback);bridge.Call("start");}
        void State(bool connected,string error){if(connected)routeServer=bridge.Call<string>("serverIdentity");Connected=connected;Error=error;lock(gate){if(!connected){foreach(var t in pending.Values)t.TrySetException(new IOException("公网断开"));pending.Clear();}}ConnectionChanged?.Invoke(connected);}
        static string Hex(byte[] b){return BitConverter.ToString(b).Replace("-","").ToLowerInvariant();}
        static string Digest(byte[] b){using(var sha=SHA256.Create())return Hex(sha.ComputeHash(b));}
        string Mac(Header h){string text=string.Join("\n",h.workspace,h.@from,h.to,h.boot,h.seq.ToString(System.Globalization.CultureInfo.InvariantCulture),h.kind,h.stream,h.epoch.ToString(System.Globalization.CultureInfo.InvariantCulture),h.block_seq.ToString(System.Globalization.CultureInfo.InvariantCulture),h.digest);using(var mac=new HMACSHA256(Wire.Key(Config.secret)))return Hex(mac.ComputeHash(Encoding.UTF8.GetBytes(text)));}
        static bool Equal(string a,string b){if(a==null||b==null||a.Length!=b.Length)return false;int d=0;for(int i=0;i<a.Length;i++)d|=a[i]^b[i];return d==0;}
        public void Send(string kind,byte[] body,string lane="control"){
            if(!Connected||closed)throw new IOException("公网连接尚未认证");
            var h=new Header{workspace=Config.workspace,@from=Config.device_id,to=Config.hub_id,boot=boot,seq=Interlocked.Increment(ref sequence),kind=kind,digest=Digest(body)};h.mac=Mac(h);
            byte[] header=Encoding.UTF8.GetBytes(JsonUtility.ToJson(h));if(header.Length>65536||body.Length>4194304)throw new IOException("消息过大");byte[] raw=new byte[4+header.Length+body.Length];int n=header.Length;raw[0]=(byte)(n>>24);raw[1]=(byte)(n>>16);raw[2]=(byte)(n>>8);raw[3]=(byte)n;Buffer.BlockCopy(header,0,raw,4,n);Buffer.BlockCopy(body,0,raw,4+n,body.Length);
            if(!bridge.Call<bool>("send",lane,Convert.ToBase64String(raw)))throw new IOException("公网发送未接受");
        }
        void Receive(string lane,byte[] raw){try{
            if(raw.Length>0&&raw[0]=='{'){var notice=JsonUtility.FromJson<Notice>(Encoding.UTF8.GetString(raw));if(notice.notice=="update_available"&&notice.platform=="quest-arm64")UpdateAvailable=true;return;}
            if(raw.Length<6||raw.Length>5*1024*1024)throw new IOException("frame bound");int n=(raw[0]<<24)|(raw[1]<<16)|(raw[2]<<8)|raw[3];if(n<2||n>65536||n+4>raw.Length)throw new IOException("header bound");
            Header h=JsonUtility.FromJson<Header>(Encoding.UTF8.GetString(raw,4,n));byte[] body=new byte[raw.Length-4-n];Buffer.BlockCopy(raw,4+n,body,0,body.Length);
            if(h.schema!="eegmovement.relay.v1"||h.workspace!=Config.workspace||h.@from!=Config.hub_id||h.to!=Config.device_id||!Equal(h.digest,Digest(body))||!Equal(h.mac,Mac(h)))throw new IOException("中枢签名拒绝");
            lock(gate){if(h.route_server!=routeServer||h.route_generation<1)throw new IOException("authenticated route mismatch");if(h.route_generation<hubGeneration)return;if(h.route_generation==hubGeneration&&hubBoot!=h.boot)throw new IOException("boot conflict");if(h.route_generation>hubGeneration){hubGeneration=h.route_generation;hubBoot=h.boot;highs.Clear();seen.Clear();}long high;highs.TryGetValue(h.boot,out high);HashSet<long> s;if(!seen.TryGetValue(h.boot,out s)){s=new HashSet<long>();seen[h.boot]=s;}if(h.seq<1||h.seq<=high-512||s.Contains(h.seq))return;s.Add(h.seq);high=Math.Max(high,h.seq);s.RemoveWhere(x=>x<=high-512);highs[h.boot]=high;}
            if(h.kind=="rpc_reply"){
                Reply r=JsonUtility.FromJson<Reply>(Encoding.UTF8.GetString(body));TaskCompletionSource<string> t;lock(gate){if(!pending.TryGetValue(r.request_id,out t))return;pending.Remove(r.request_id);}if(r.status==200){if(!rpcLogged){rpcLogged=true;Debug.Log("EEG_RELAY_RPC signed=true status=200");}t.TrySetResult(Encoding.UTF8.GetString(Convert.FromBase64String(r.body)));}else t.TrySetException(new IOException(r.error??"中枢拒绝"));
            }else if(h.kind=="vr_wire"){WireReceived?.Invoke(body);}
            else if(h.kind=="clock_reply"){var clock=JsonUtility.FromJson<Clock>(Encoding.UTF8.GetString(body));clock.t3=ReceiverClient.Now;clock.phase="report";Send("clock",Encoding.UTF8.GetBytes(JsonUtility.ToJson(clock)));}
            else if(h.kind=="event_ack"){Archived a=JsonUtility.FromJson<Archived>(Encoding.UTF8.GetString(body));Guid id;if(Guid.TryParseExact(a.event_id,"N",out id)){string p=Path.Combine(offlineRoot,a.event_id+".json");if(File.Exists(p))File.Delete(p);}}
        }catch(Exception e){Error=e.GetType().Name+": "+e.Message;State(false,Error);}}
        public async Task<string> Request(string path,byte[] body=null){
            string id=Guid.NewGuid().ToString("N");var r=new RpcRequest{request_id=id,path=path,method=body==null?"GET":"POST",body=Convert.ToBase64String(body??new byte[0])};string request=JsonUtility.ToJson(r);var completion=new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
            try{
                lock(gate){if(pending.Count>=32)throw new IOException("请求队列已满");pending[id]=completion;}
                Send(path=="/v1/mirror"?"preview":"rpc",path=="/v1/mirror"?body:Encoding.UTF8.GetBytes(request),path=="/v1/mirror"?"preview":"control");
                if(path=="/v1/mirror")return "{}";
                for(int attempt=0;attempt<2;attempt++){if(await Task.WhenAny(completion.Task,Task.Delay(2500)).ConfigureAwait(false)==completion.Task)return await completion.Task.ConfigureAwait(false);if(attempt==0&&Connected)Send("rpc",Encoding.UTF8.GetBytes(request));}
                throw new IOException("公网已连接，但本机中枢回包超时");
            }catch{
                if(path=="/v1/acquisition/event")SaveOffline(request,id);throw;
            }finally{lock(gate)pending.Remove(id);}
        }
        void SaveOffline(string request,string id){if(Directory.GetFiles(offlineRoot,"*.json").Length>=10000){Error="离线事件日志已满，请导出检查";return;}var o=new Offline{event_id=id,request=request};string path=Path.Combine(offlineRoot,id+".json");File.WriteAllText(path+".tmp",JsonUtility.ToJson(o));File.Move(path+".tmp",path);}
        public void FlushOffline(){if(!Connected)return;int n=0;foreach(string p in Directory.GetFiles(offlineRoot,"*.json")){if(n++>=8)break;Send("quest_event",File.ReadAllBytes(p),"backfill");}}
        public void TickClock(){if(!Connected||ReceiverClient.Now<nextClock)return;nextClock=ReceiverClient.Now+5000;Send("clock",Encoding.UTF8.GetBytes(JsonUtility.ToJson(new Clock{t0=ReceiverClient.Now})));}
        public bool TakeUpdateNotice(){bool value=UpdateAvailable;UpdateAvailable=false;return value;}
        public byte[] ExecutionKey(){using(var mac=new HMACSHA256(Wire.Key(Config.secret)))return mac.ComputeHash(Encoding.UTF8.GetBytes("EEGMovement UDP v1"));}
        public void Dispose(){closed=true;State(false,"closed");if(bridge!=null){bridge.Call("close");bridge.Dispose();bridge=null;}}
    }
}
