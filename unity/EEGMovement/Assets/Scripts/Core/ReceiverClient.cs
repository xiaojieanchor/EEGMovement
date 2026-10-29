using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Collections.Generic;
using System.Diagnostics;
using System.Threading;

namespace EEGMovement.Core
{
    public sealed class ReceiverClient : IDisposable
    {
        public readonly MotionCore Core;
        readonly object gate = new object();
        readonly Queue<Packet> pending = new Queue<Packet>();
        readonly string configPath;
        readonly IJsonCodec codec;
        readonly UdpClient udp;
        readonly Thread receiveThread;
        readonly int hubPortOverride;
        PairingConfig config;
        byte[] key;
        volatile bool closed;
        double lastConfigCheck = -1000, lastPulse = -1000;
        DateTime configTime;
        int invalidWire, queueDrops;
        double quotaStart;
        int quotaCount;
        readonly RuntimeHealth health = new RuntimeHealth();
        bool hasHealth;
        Action<byte[]> relaySend;
        bool managedRoute;
        public string Transport {get {lock(gate)return relaySend!=null?"公网中继":config!=null?(config.host=="127.0.0.1"?"内网 · 本机":"内网直连"):"等待连接";}}
        public void SetRelay(Action<byte[]> sender,byte[] relayKey) { lock(gate){managedRoute=true;relaySend=sender;key=(byte[])relayKey.Clone();pending.Clear();Core.ResetPairing();Core.EmergencyLatched=true;Core.Brake("relay_manual_resume_required");} }
        public void SetLan(PairingConfig next) { PairingAddress.Parse(next.host,"lan",next.explicit_host);byte[] nextKey=Wire.Key(next.key);if(next.port<1024||next.port>65535)throw new ArgumentException("Invalid LAN port");lock(gate){managedRoute=true;relaySend=null;config=next;key=nextKey;pending.Clear();Core.ResetPairing();Core.EmergencyLatched=true;Core.Brake("lan_manual_resume_required");LastError=null;} }
        public void RelayDisconnected() { lock(gate){pending.Clear();Core.ResetPairing();Core.EmergencyLatched=true;Core.Brake("relay_disconnected");} }
        public void AcceptRelay(byte[] data) { try{lock(gate){if(relaySend==null)return;Packet p=Wire.Decode(data,key,codec);if(pending.Count>=8){pending.Dequeue();queueDrops++;}pending.Enqueue(p);}}catch(Exception){Interlocked.Increment(ref invalidWire);} }
        public string LastError { get; private set; }
        public static double Now { get { return Stopwatch.GetTimestamp() * 1000.0 / Stopwatch.Frequency; } }
        public ReceiverClient(string path, int listenPort, string role, bool allowTest, IJsonCodec json, int hubPort = 0)
        {
            configPath = path; codec = json; hubPortOverride = hubPort;
            Core = new MotionCore(Guid.NewGuid().ToString("N"), role, allowTest);
            udp = new UdpClient(new IPEndPoint(IPAddress.Any, listenPort));
            udp.Client.ReceiveTimeout = 250; udp.Client.ReceiveBufferSize = 65536;
            ReloadConfig();
            receiveThread = new Thread(Receive) { IsBackground = true, Name = "EEG command inbox" }; receiveThread.Start();
        }
        void ReloadConfig()
        {
            if(managedRoute)return;
            double now = Now;
            if (now - lastConfigCheck < 1000) return;
            lastConfigCheck = now;
            try
            {
                DateTime modified = File.GetLastWriteTimeUtc(configPath);
                if (config != null && modified == configTime) return;
                var next = codec.Decode<PairingConfig>(File.ReadAllText(configPath));
                if (next.version != 1 || next.port < 1024 || next.port > 65535) throw new ArgumentException("pairing configuration required");
                PairingAddress.Parse(next.host, next.transport, next.explicit_host);
                byte[] nextKey = Wire.Key(next.key);
                lock (gate) { config = next; configTime = modified; key = nextKey; pending.Clear(); Core.ResetPairing(); }
                LastError = null;
            }
            catch (Exception error) { LastError = error.GetType().Name + ": pairing unavailable"; lock (gate) Core.Brake("pairing_unavailable"); }
        }
        void Receive()
        {
            while (!closed)
            {
                try
                {
                    var source = new IPEndPoint(IPAddress.Any, 0); byte[] data = udp.Receive(ref source);
                    PairingConfig cfg; byte[] currentKey;
                    lock (gate) { cfg = config; currentKey = key; }
                    if (relaySend!=null || cfg == null || currentKey == null || !source.Address.Equals(PairingAddress.Parse(cfg.host, cfg.transport, cfg.explicit_host)) || source.Port != (hubPortOverride == 0 ? cfg.port : hubPortOverride)) { Interlocked.Increment(ref invalidWire); continue; }
                    double now = Now;
                    if (now - quotaStart >= 1000) { quotaStart = now; quotaCount = 0; }
                    if (++quotaCount > 160) { Interlocked.Increment(ref invalidWire); continue; }
                    Packet packet = Wire.Decode(data, currentKey, codec);
                    lock (gate)
                    {
                        if(relaySend!=null||!ReferenceEquals(cfg,config))continue;
                        if (pending.Count >= 8) { pending.Dequeue(); queueDrops++; }
                        pending.Enqueue(packet);
                    }
                }
                catch (SocketException e) { if (!closed && e.SocketErrorCode != SocketError.TimedOut && e.SocketErrorCode != SocketError.Interrupted) LastError = "UDP receive unavailable"; }
                catch (ObjectDisposedException) { break; }
                catch (Exception) { Interlocked.Increment(ref invalidWire); }
            }
        }
        public void BeginFrame(double dt,bool executionPermitted=true)
        {
            ReloadConfig();
            lock (gate)
            {
                double now = Now;
                if(Core.ExecutionPermitted!=executionPermitted){pending.Clear();Core.SetExecutionPermission(executionPermitted);}
                while (pending.Count > 0) Core.Accept(pending.Dequeue(), now);
                Core.Step(dt, now);
            }
        }
        public void ConfirmFrame(double x, double z, double yaw, double speed, double yawRate)
        {
            lock (gate) Core.ConfirmApplied(x, z, yaw, speed, yawRate);
        }
        public ReceiverState Snapshot()
        {
            lock (gate)
            {
                var state = Core.Snapshot(Now); state.invalid_wire = invalidWire; state.queue_drops = queueDrops;
                if (hasHealth) health.Apply(state);
                return state;
            }
        }
        public void Pulse()
        {
            double now = Now;
            if (now - lastPulse < 50) return;
            lastPulse = now;
            lock (gate)
            {
                if ((config == null && relaySend==null) || key == null) return;
                try
                {
                    var packet = new Packet { version = 1, kind = "heartbeat", receiver_id = Core.ReceiverId, nonce = Core.Nonce, session = Core.Session, ticket = Core.IssueTicket(now), role = Core.Role, allow_test = Core.AllowTest, telemetry = Snapshot() };
                    byte[] bytes = Wire.Encode(packet, key, codec);
                    if(relaySend!=null)relaySend(bytes);
                    else udp.Send(bytes, bytes.Length, new IPEndPoint(PairingAddress.Parse(config.host, config.transport, config.explicit_host), hubPortOverride == 0 ? config.port : hubPortOverride));
                }
                catch (Exception) { LastError = "UDP heartbeat unavailable"; Core.Brake("transport_error"); }
            }
        }
        // Values are copied from the Unity main thread; the inbox never reads UnityEngine objects.
        public void UpdateHealth(RuntimeHealth next) { lock (gate) {
            health.app_version = next.app_version; health.build_id = next.build_id; health.ui_state = next.ui_state;
            health.frame_ms = next.frame_ms; health.frame_p95_ms = next.frame_p95_ms; health.rendered_frames = next.rendered_frames;
            health.focused = next.focused; health.paused = next.paused; health.xr_active = next.xr_active; hasHealth = true;
        } }
        public void EmergencyStop() { lock (gate) { pending.Clear(); Core.EmergencyLatched = true; Core.Brake("local_emergency_stop"); } }
        public void ResetEmergency() { lock (gate) { pending.Clear(); WorkspaceSafety.Reset(Core); } }
        public void Dispose()
        {
            closed = true; udp.Close(); if (receiveThread != null) receiveThread.Join(500);
            lock (gate) Core.Brake("receiver_closed");
        }
    }
}

