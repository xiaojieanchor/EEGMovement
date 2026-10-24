using System;
using System.Text;
using System.Security.Cryptography;

namespace EEGMovement.Core
{
    [Serializable] public sealed class PairingConfig
    {
        public int version;
        public string key;
        public string host;
        public int port;
        public string hub_session;
        public string transport;
        public bool explicit_host;
    }
    public static class PairingAddress
    {
        public static System.Net.IPAddress Parse(string host, string transport, bool explicitHost = false)
        {
            System.Net.IPAddress address;
            if (!System.Net.IPAddress.TryParse(host, out address) || address.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork)
                throw new ArgumentException("Literal IPv4 required");
            byte[] b = address.GetAddressBytes();
            if (host == "127.0.0.1") return address;
            bool privateAddress = b[0] == 10 || (b[0] == 172 && b[1] >= 16 && b[1] <= 31) || (b[0] == 192 && b[1] == 168);
            bool unicast = b[0] != 0 && b[0] != 127 && b[0] < 224 && !(b[0] == 169 && b[1] == 254);
            if (transport != "lan" || !unicast || !(privateAddress || explicitHost))
                throw new ArgumentException("Explicit enrolled IPv4 required");
            return address;
        }
    }
    [Serializable] public sealed class Envelope { public string payload; public string mac; }
    [Serializable] public sealed class Packet
    {
        public int version;
        public string kind;
        public string receiver_id;
        public string nonce;
        public string session;
        public long ticket;
        public long seq;
        public long decision_id;
        public double enter_active_threshold,enter_direction_threshold;
        public int motion_schema,linear_axis,turn_axis;
        public double linear_confidence,turn_confidence,linear_mass,turn_mass,linear_strength,turn_strength,axis_mass_threshold;
        public int epoch;
        public int ttl_ms;
        public string mode;
        public bool active;
        public bool input_valid;
        public bool model_ready;
        public int direction;
        public double p_active;
        public double confidence;
        public double speed;
        public double yaw_rate;
        public string reason;
        public string role;
        public bool allow_test;
        public ReceiverState telemetry;
    }
    [Serializable] public sealed class ReceiverState
    {
        public int motion_schema=3,linear_axis,turn_axis;
        public string app_version = "", build_id = "", ui_state = "";
        public double frame_ms, frame_p95_ms;
        public long rendered_frames;
        public bool focused, paused, xr_active;
        public bool paired;
        public string role;
        public string mode;
        public string state;
        public string reason;
        public long received_seq;
        public long applied_seq;
        public double x;
        public double z;
        public double yaw;
        public double speed;
        public double yaw_rate;
        public double actual_speed;
        public double actual_yaw_rate;
        public double target_speed;
        public double target_yaw_rate;
        public double command_roundtrip_ms;
        public double command_age_ms;
        public int accepted;
        public int rejected;
        public int watchdog_stops;
        public double watchdog_brake_ms;
        public int queue_drops;
        public int invalid_wire;
        public int direction;
    }
    public interface IJsonCodec
    {
        T Decode<T>(string json);
        string Encode(object value);
    }
    public static class Wire
    {
        public const int Version = 1;
        public const int MaxBytes = 4096;
        public static bool Finite(double x) { return !Double.IsNaN(x) && !Double.IsInfinity(x); }
        public static byte[] Key(string hex)
        {
            if (hex == null || hex.Length != 64) throw new ArgumentException("pairing key must be32 bytes");
            byte[] bytes = new byte[32];
            for (int i = 0; i < bytes.Length; i++) bytes[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
            return bytes;
        }
        public static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
        public static byte[] Encode(Packet packet, byte[] key, IJsonCodec codec)
        {
            byte[] payload = Encoding.UTF8.GetBytes(codec.Encode(packet));
            byte[] signature;
            using (var hmac = new HMACSHA256(key)) signature = hmac.ComputeHash(payload);
            byte[] result = Encoding.UTF8.GetBytes(codec.Encode(new Envelope { payload = Convert.ToBase64String(payload), mac = Hex(signature) }));
            if (result.Length > MaxBytes) throw new ArgumentException("oversized packet");
            return result;
        }
        public static Packet Decode(byte[] data, byte[] key, IJsonCodec codec)
        {
            if (data.Length == 0 || data.Length > MaxBytes) throw new ArgumentException("oversized/empty packet");
            var envelope = codec.Decode<Envelope>(new UTF8Encoding(false, true).GetString(data));
            if (envelope == null || envelope.mac == null || envelope.mac.Length != 64 || envelope.payload == null) throw new ArgumentException("invalid envelope");
            byte[] payload = Convert.FromBase64String(envelope.payload);
            if (payload.Length > 3000) throw new ArgumentException("oversized payload");
            byte[] signature;
            using (var hmac = new HMACSHA256(key)) signature = hmac.ComputeHash(payload);
            string expected = Hex(signature); int difference = 0;
            for (int i = 0; i < expected.Length; i++) difference |= expected[i] ^ envelope.mac[i];
            if (difference != 0) throw new ArgumentException("bad signature");
            var packet = codec.Decode<Packet>(new UTF8Encoding(false, true).GetString(payload));
            if (packet == null || packet.version != Version) throw new ArgumentException("unsupported protocol");
            return packet;
        }
    }
}
