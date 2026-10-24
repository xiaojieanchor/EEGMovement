using System;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Collections;
using EEGMovement.Core;
using UnityEngine;

namespace EEGMovement.Unity
{
    // Identities are enrolled into Android private storage; no secret is baked into APK/resources.
    public sealed class QuestNetwork : MonoBehaviour
    {
        [Serializable] public sealed class Enrollment { public int version; public string device_id, secret, hub_host; public int port = 8766; public bool explicit_host; }
        [Serializable] sealed class Bootstrap {public string base_url,ca_pem;}
        public static QuestNetwork Instance {get;private set;}
        public static bool EnrollmentRunning {get;private set;}
        public static string Issue { get; private set; }
        public static PublicRelay Relay {get;private set;}
        public static bool IsPublic {get{return Relay!=null;}}
        public static bool Ready { get { return enrollment != null || Relay!=null; } }
        static ReceiverClient receiver;
        public static void AttachReceiver(ReceiverClient next){receiver=next;if(Relay!=null)BindReceiver();}
        static void BindReceiver(){if(receiver==null||Relay==null)return;receiver.SetRelay(bytes=>Relay.Send("vr_wire",bytes),Relay.ExecutionKey());}
        volatile bool reconnectPending,emergencyPending,connectionPending;float nextReconnect,nextFlush,nextEnrollmentPoll;
        AndroidJavaObject registration;string publicJson,lastEnrollmentState;bool connectedState;string connectionError;
        static Enrollment enrollment;
        static byte[] secret;
        static string boot = Guid.NewGuid().ToString("N");
        static long sequence;
        static readonly HttpClient client = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(2) };
        public static string PairingPath { get { return Path.Combine(Application.persistentDataPath, "eeg_pairing.json"); } }
        static string Hex(byte[] value) { return BitConverter.ToString(value).Replace("-", "").ToLowerInvariant(); }
        static string Mac(string method, string path, string seq, byte[] body)
        {
            string hash; using (var sha = SHA256.Create()) hash = Hex(sha.ComputeHash(body));
            string value = string.Join("\n", method, path, enrollment.device_id, boot, seq, hash);
            using (var hmac = new HMACSHA256(secret)) return Hex(hmac.ComputeHash(Encoding.UTF8.GetBytes(value)));
        }
        static bool ConstantEquals(string a, string b)
        {
            if (a == null || b == null || a.Length != b.Length) return false;
            int different = 0; for (int i = 0; i < a.Length; i++) different |= a[i] ^ b[i]; return different == 0;
        }
        public static async Task<string> Send(string path, byte[] body = null, string contentType = "application/json")
        {
            if(Relay!=null)return await Relay.Request(path,body).ConfigureAwait(false);
            if (!Ready) throw new InvalidOperationException("Quest is not USB-paired");
            string method = body == null ? "GET" : "POST", seq = Interlocked.Increment(ref sequence).ToString(System.Globalization.CultureInfo.InvariantCulture);
            byte[] payload = body ?? new byte[0];
            using (var message = new HttpRequestMessage(body == null ? HttpMethod.Get : HttpMethod.Post, "http://" + enrollment.hub_host + ":" + enrollment.port + path))
            {
                message.Headers.Add("X-EEG-Device", enrollment.device_id); message.Headers.Add("X-EEG-Boot", boot);
                message.Headers.Add("X-EEG-Seq", seq); message.Headers.Add("X-EEG-MAC", Mac(method, path, seq, payload));
                if (body != null) { message.Content = new ByteArrayContent(body); message.Content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue(contentType); }
                using (var response = await client.SendAsync(message).ConfigureAwait(false))
                {
                    byte[] reply = await response.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
                    if (reply.Length > 65536) throw new IOException("Oversized LAN response");
                    System.Collections.Generic.IEnumerable<string> signatures;
                    string received = response.Headers.TryGetValues("X-EEG-MAC", out signatures) ? System.Linq.Enumerable.FirstOrDefault(signatures) : null;
                    string expected = Mac(((int)response.StatusCode).ToString(), "response", seq, reply);
                    if (!ConstantEquals(received, expected)) throw new IOException("LAN response signature rejected");
                    if (!response.IsSuccessStatusCode) throw new IOException("LAN request rejected: " + (int)response.StatusCode);
                    return Encoding.UTF8.GetString(reply);
                }
            }
        }
        void Awake()
        {
            Instance=this;
            try
            {
                var config=Resources.Load<TextAsset>("relay_public");var bootstrap=JsonUtility.FromJson<Bootstrap>(config.text);
                using(var player=new AndroidJavaClass("com.unity3d.player.UnityPlayer"))registration=new AndroidJavaObject("org.eegmovement.relay.EnrollmentBridge",player.GetStatic<AndroidJavaObject>("currentActivity"),bootstrap.base_url,bootstrap.ca_pem);
                string stored="";try{stored=registration.Call<string>("read");}catch(Exception){Issue="本机身份不可用，正在重新登记";}
                if(!string.IsNullOrEmpty(stored)){StartPublic(stored);return;}
            }
            catch(Exception e){Issue="自动连接初始化失败："+e.GetType().Name;Debug.Log("EEG_ENROLLMENT state=initialization_failed type="+e.GetType().Name);}
            try
            {
                string legacyPath=Path.Combine(Application.persistentDataPath,"eeg_enrollment.json");string json=File.ReadAllText(legacyPath);
                var relayConfig=JsonUtility.FromJson<PublicRelay.Enrollment>(json);
                if(relayConfig.mode=="relay") {if(registration==null)throw new IOException();registration.Call("store",json);File.Delete(legacyPath);StartPublic(json);return;}
                enrollment = JsonUtility.FromJson<Enrollment>(json);
                if (enrollment.version != 1 || enrollment.device_id == null || enrollment.device_id.Length != 32 || enrollment.port < 1024 || enrollment.port > 65535) throw new IOException();
                PairingAddress.Parse(enrollment.hub_host, "lan", enrollment.explicit_host); secret = Wire.Key(enrollment.secret);
                Issue = "正在与电脑中枢配对"; StartCoroutine(Pair());
            }
            catch (Exception) { enrollment = null; BeginEnrollment(); }
        }
        void BeginEnrollment(){if(registration==null){Issue="自动连接暂不可用，请重新打开应用";return;}registration.Call("start");EnrollmentRunning=true;Issue="正在自动连接本机中枢";}
        public static void RetryConnection(){if(!Instance)return;if(Relay!=null)Instance.reconnectPending=true;else if(enrollment==null)Instance.BeginEnrollment();}
        public static void CancelEnrollment(){if(!Instance||Instance.registration==null)return;Instance.registration.Call("cancel");EnrollmentRunning=false;Issue="自动连接已取消，可点重新连接重试";}
        static string EnrollmentIssue(string state){switch(state){case "starting":return "正在安全登记Quest身份";case "waiting_hub":return "已登记，等待本机中枢上线";case "role_occupied":return "已有Quest在线，等待其断开后自动连接";case "tls_verification_failed":return "公网证书验证失败，稍后自动重试";case "network_timeout":return "公网请求超时，请检查头显Wi-Fi";case "network_or_response_failed":return "公网暂不可达，正在自动重试";case "envelope_rejected":return "身份加密校验未通过，正在重新领取";case "server_response_invalid":return "公网登记响应无效，正在重试";case "timed_out":return "自动连接已等待10分钟，可点重新连接重试";case "cancelled":return "自动连接已取消，可点重新连接重试";case "keystore_or_config_failed":return "本机安全密钥暂不可用，请重开应用";default:return "正在自动连接本机中枢";}}
        void StartPublic(string json){
            publicJson=json;EnrollmentRunning=false;if(Relay!=null)Relay.Dispose();Relay=new PublicRelay(json,Application.persistentDataPath);var current=Relay;
            Relay.ConnectionChanged+=connected=>{if(Relay!=current)return;Issue=connected?"公网已认证；等待本机中枢":"公网断开；动作停止";connectedState=connected;connectionError=current.Error;connectionPending=true;if(receiver!=null)receiver.RelayDisconnected();emergencyPending=true;if(!connected)reconnectPending=true;};
            Relay.WireReceived+=bytes=>{if(receiver!=null)receiver.AcceptRelay(bytes);};BindReceiver();reconnectPending=false;Relay.Start();Issue="正在连接公网转发";
        }
        void Update(){
            if(registration!=null&&Relay==null&&enrollment==null&&Time.unscaledTime>=nextEnrollmentPoll){
                nextEnrollmentPoll=Time.unscaledTime+.25f;
                try{string state=registration.Call<string>("status");EnrollmentRunning=registration.Call<bool>("running");if(state!=lastEnrollmentState){lastEnrollmentState=state;Issue=EnrollmentIssue(state);Debug.Log("EEG_ENROLLMENT state="+state);}if(state=="ready")StartPublic(registration.Call<string>("read"));}
                catch(Exception e){Issue="自动连接状态读取失败："+e.GetType().Name;}
            }
            if(Relay==null)return;
            if(connectionPending){connectionPending=false;Debug.Log("EEG_RELAY_STATE connected="+connectedState+" reason="+(connectionError??"none"));}
            try{Relay.TickClock();}catch(Exception){Issue="时钟测量等待连接恢复";}
            if(emergencyPending){emergencyPending=false;GetComponent<EEGAvatarController>().EmergencyStop();}
            if(reconnectPending&&Time.unscaledTime>=nextReconnect){reconnectPending=false;nextReconnect=Time.unscaledTime+3;try{StartPublic(publicJson);}catch(Exception){reconnectPending=true;}}
            if(Relay.Connected&&Time.unscaledTime>=nextFlush){nextFlush=Time.unscaledTime+3;try{Relay.FlushOffline();}catch(Exception){Issue="离线事件等待补交";}}
        }
        void OnDestroy(){if(registration!=null){registration.Call("cancel");registration.Dispose();registration=null;}if(Relay!=null){Relay.Dispose();Relay=null;}receiver=null;EnrollmentRunning=false;if(Instance==this)Instance=null;}
        IEnumerator Pair()
        {
            while (true)
            {
                var task = Send("/v1/pairing"); while (!task.IsCompleted) yield return null;
                if (task.IsFaulted || task.IsCanceled) Issue = "局域网连接未确认；检查Wi-Fi与中枢";
                else
                {
                    try
                    {
                        var config = JsonUtility.FromJson<PairingConfig>(task.Result);
                        if (config.version != 1 || config.host != enrollment.hub_host || config.transport != "lan") throw new IOException();
                        config.explicit_host = enrollment.explicit_host;
                        using (var hmac = new HMACSHA256(secret)) config.key = Hex(hmac.ComputeHash(Encoding.UTF8.GetBytes("EEGMovement UDP v1")));
                        string json = JsonUtility.ToJson(config);
                        if (!File.Exists(PairingPath) || File.ReadAllText(PairingPath) != json)
                        { File.WriteAllText(PairingPath + ".tmp", json); if (File.Exists(PairingPath)) File.Delete(PairingPath); File.Move(PairingPath + ".tmp", PairingPath); }
                        Issue = null;
                    }
                    catch (Exception) { Issue = "中枢配对响应无效"; }
                }
                yield return new WaitForSecondsRealtime(3);
            }
        }
    }
}
