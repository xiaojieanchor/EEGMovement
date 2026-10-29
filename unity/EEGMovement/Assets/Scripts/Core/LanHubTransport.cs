using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace EEGMovement.Core
{
    // Discovery only supplies hints. Both the HTTP response and UDP nonce proof
    // must authenticate with this device's existing end-to-end identity.
    public sealed class LanHubTransport
    {
        public sealed class Route { public string Host; public int HttpPort; public PairingConfig Pairing; }
        [Serializable] public sealed class Hint { public string service; }
        readonly string identity, boot = Guid.NewGuid().ToString("N");
        readonly byte[] secret, wireKey;
        readonly IJsonCodec codec;
        long sequence;
        static readonly HttpClient http = new HttpClient(new HttpClientHandler { UseProxy=false, AllowAutoRedirect=false })
            { Timeout=TimeSpan.FromSeconds(2), MaxResponseContentBufferSize=262144 };
        public LanHubTransport(string deviceId, string key, IJsonCodec json)
        {
            if(deviceId==null||deviceId.Length!=32)throw new ArgumentException("Device identity required");
            identity=deviceId; secret=Wire.Key(key);codec=json;
            using(var mac=new HMACSHA256(secret))wireKey=mac.ComputeHash(Encoding.UTF8.GetBytes("EEGMovement UDP v1"));
        }
        public static bool PrivateHost(string host)
        {
            IPAddress ip;if(!IPAddress.TryParse(host,out ip)||ip.AddressFamily!=AddressFamily.InterNetwork)return false;
            byte[] b=ip.GetAddressBytes();return b[0]==10||(b[0]==172&&b[1]>=16&&b[1]<=31)||(b[0]==192&&b[1]==168);
        }
        static string Hex(byte[] bytes){return BitConverter.ToString(bytes).Replace("-","").ToLowerInvariant();}
        string Mac(string method,string path,string seq,byte[] bytes)
        {
            string digest;using(var sha=SHA256.Create())digest=Hex(sha.ComputeHash(bytes));
            using(var mac=new HMACSHA256(secret))return Hex(mac.ComputeHash(Encoding.UTF8.GetBytes(string.Join("\n",method,path,identity,boot,seq,digest))));
        }
        static bool Equal(string a,string b){if(a==null||b==null||a.Length!=b.Length)return false;int d=0;for(int i=0;i<a.Length;i++)d|=a[i]^b[i];return d==0;}
        public async Task<string> Send(Route route,string path,byte[] body=null,string contentType="application/json",int timeoutMs=2000)
        {
            PairingAddress.Parse(route.Host,"lan",route.Pairing!=null&&route.Pairing.explicit_host);
            if(route.HttpPort<1024||route.HttpPort>65535||!path.StartsWith("/v1/"))throw new IOException("Invalid LAN endpoint");
            string seq=Interlocked.Increment(ref sequence).ToString(System.Globalization.CultureInfo.InvariantCulture);
            byte[] payload=body??new byte[0];
            using(var cancel=new CancellationTokenSource(timeoutMs))
            using(var request=new HttpRequestMessage(body==null?HttpMethod.Get:HttpMethod.Post,"http://"+route.Host+":"+route.HttpPort+path))
            {
                request.Headers.Add("X-EEG-Device",identity);request.Headers.Add("X-EEG-Boot",boot);request.Headers.Add("X-EEG-Seq",seq);
                request.Headers.Add("X-EEG-MAC",Mac(body==null?"GET":"POST",path,seq,payload));
                if(body!=null){request.Content=new ByteArrayContent(body);request.Content.Headers.ContentType=new System.Net.Http.Headers.MediaTypeHeaderValue(contentType);}
                using(var response=await http.SendAsync(request,cancel.Token).ConfigureAwait(false))
                {
                    byte[] reply=await response.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
                    IEnumerable<string> signatures;string signature=response.Headers.TryGetValues("X-EEG-MAC",out signatures)?signatures.FirstOrDefault():null;
                    if(reply.Length>262144||!Equal(signature,Mac(((int)response.StatusCode).ToString(),"response",seq,reply)))throw new IOException("LAN response signature rejected");
                    if(!response.IsSuccessStatusCode)throw new IOException("LAN request rejected: "+(int)response.StatusCode);
                    return Encoding.UTF8.GetString(reply);
                }
            }
        }
        public async Task<Route> Probe(string host,int port=8766)
        {
            if(!PrivateHost(host))return null;
            try
            {
                var route=new Route{Host=host,HttpPort=port};
                var pair=codec.Decode<PairingConfig>(await Send(route,"/v1/pairing",timeoutMs:700).ConfigureAwait(false));
                if(pair.version!=1||pair.transport!="lan"||pair.host!=host||pair.port<1024||pair.port>65535||string.IsNullOrEmpty(pair.hub_session))return null;
                // A signed HTTP reply alone does not establish the action return path.
                using(var udp=new UdpClient())
                {
                    udp.Client.ReceiveTimeout=600;udp.Connect(host,pair.port);
                    string nonce=Guid.NewGuid().ToString("N");
                    byte[] proof=Wire.Encode(new Packet{version=1,kind="route_probe",nonce=nonce},wireKey,codec);
                    udp.Send(proof,proof.Length);IPEndPoint source=null;
                    Packet reply=Wire.Decode(udp.Receive(ref source),wireKey,codec);
                    if(reply.kind!="route_probe_reply"||reply.nonce!=nonce||reply.session!=pair.hub_session)return null;
                }
                pair.key=Hex(wireKey);route.Pairing=pair;return route;
            }
            catch(Exception){return null;}
        }
        public async Task<Route> DiscoverAndProbe(IEnumerable<string> hints,int port=8766)
        {
            var hosts=await Task.Run(()=>Discover(hints)).ConfigureAwait(false);
            // Bound requests and the total search time; no scanning of arbitrary ports.
            var tasks=hosts.Take(12).Select(host=>Probe(host,port)).ToList();
            while(tasks.Count>0){var done=await Task.WhenAny(tasks).ConfigureAwait(false);tasks.Remove(done);var route=await done.ConfigureAwait(false);if(route!=null)return route;}
            return null;
        }
        List<string> Discover(IEnumerable<string> hints)
        {
            var hosts=new List<string>((hints??new string[0]).Where(PrivateHost).Distinct().Take(12));
            using(var udp=new UdpClient(new IPEndPoint(IPAddress.Any,0)))
            {
                udp.EnableBroadcast=true;udp.Client.ReceiveTimeout=150;
                byte[] query=Encoding.UTF8.GetBytes("{\"discover\":\"eegmovement_hub_v1\"}");
                var broadcasts=new HashSet<string>{"255.255.255.255"};
                try{foreach(var nic in NetworkInterface.GetAllNetworkInterfaces())foreach(var u in nic.GetIPProperties().UnicastAddresses)
                {if(!PrivateHost(u.Address.ToString())||u.IPv4Mask==null)continue;var a=u.Address.GetAddressBytes();var m=u.IPv4Mask.GetAddressBytes();for(int i=0;i<4;i++)a[i]=(byte)(a[i]|~m[i]);broadcasts.Add(new IPAddress(a).ToString());}}
                catch(NetworkInformationException){}catch(NotSupportedException){}
                foreach(var broadcast in broadcasts.Take(12))try{udp.Send(query,query.Length,new IPEndPoint(IPAddress.Parse(broadcast),8768));}catch(SocketException){}
                var elapsed=System.Diagnostics.Stopwatch.StartNew();
                while(elapsed.ElapsedMilliseconds<450&&hosts.Count<12)try{IPEndPoint source=null;byte[] reply=udp.Receive(ref source);if(reply.Length>2048||!PrivateHost(source.Address.ToString()))continue;
                    var hint=codec.Decode<Hint>(Encoding.UTF8.GetString(reply));if(hint.service=="eegmovement_hub_v1"&&!hosts.Contains(source.Address.ToString()))hosts.Add(source.Address.ToString());}
                catch(SocketException){}catch(ArgumentException){}catch(InvalidOperationException){}
            }
            return hosts;
        }
    }
    public sealed class LanPreference
    {
        public bool UsingLan {get;private set;}
        int successes,failures;
        public bool Observe(bool authenticated)
        {
            successes=authenticated?successes+1:0;failures=authenticated?0:failures+1;
            bool next=UsingLan?failures<2:successes>=2;
            if(next==UsingLan)return false;UsingLan=next;successes=failures=0;return true;
        }
        public void Initial(bool authenticated){UsingLan=authenticated;successes=failures=0;}
    }
}
