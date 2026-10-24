using System;
using System.IO;
using System.Diagnostics;
using System.Threading;
using System.Web.Script.Serialization;
using EEGMovement.Core;

public sealed class DesktopJson : IJsonCodec
{
    // Separate serializers per call; the UDP reader and frame loop run on different threads.
    public T Decode<T>(string text) { return new JavaScriptSerializer { MaxJsonLength = 10000 }.Deserialize<T>(text); }
    public string Encode(object value) { return new JavaScriptSerializer { MaxJsonLength = 10000 }.Serialize(value); }
}
public static class Harness
{
    static string Arg(string[] args, string name, string fallback)
    {
        for (int i = 0; i < args.Length - 1; i++) if (args[i] == name) return args[i + 1];
        return fallback;
    }
    public static int Main(string[] args)
    {
        try
        {
            string config = Arg(args, "--config", Path.GetFullPath("hub/runtime/vr_pairing.json"));
            int port = Int32.Parse(Arg(args, "--port", "19770"));
            int hubPort = Int32.Parse(Arg(args, "--hub-port", "0"));
            int seconds = Int32.Parse(Arg(args, "--seconds", "0"));
            string logPath = Arg(args, "--telemetry", "");
            bool allowTest = Array.IndexOf(args, "--allow-test") >= 0;
            using (var client = new ReceiverClient(config, port, "csharp_harness", allowTest, new DesktopJson(), hubPort))
            using (var log = String.IsNullOrEmpty(logPath) ? null : new StreamWriter(logPath))
            {
                var codec = new DesktopJson(); double before = ReceiverClient.Now, started = before, lastLog = before;
                Console.CancelKeyPress += (s, e) => { client.EmergencyStop(); };
                Console.WriteLine("EEGMovement C# receiver ready; NOT a Unity engine run. port=" + port + " allow_test=" + allowTest);
                while (seconds == 0 || ReceiverClient.Now - started < seconds * 1000.0)
                {
                    double now = ReceiverClient.Now, dt = (now - before) / 1000; before = now;
                    client.BeginFrame(dt);
                    var core = client.Core;
                    client.ConfirmFrame(core.X, core.Z, core.Yaw, core.Speed, core.YawRate);
                    client.Pulse();
                    if (log != null && now - lastLog >= 10) { log.WriteLine(codec.Encode(client.Snapshot())); lastLog = now; }
                    Thread.Sleep(5);
                }
            }
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error.GetType().Name + ": " + error.Message); return 1; }
    }
}
