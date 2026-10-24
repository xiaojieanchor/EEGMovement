using System;

namespace EEGMovement.Core
{
    public sealed class ReceiverOptions
    {
        public int Port = 19770;
        public bool AllowTests;
        public static ReceiverOptions Parse(string[] args)
        {
            var options = new ReceiverOptions();
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--eeg-allow-test") options.AllowTests = true;
                if (args[i] == "--eeg-port")
                {
                    int value;
                    if (++i >= args.Length || !Int32.TryParse(args[i], out value) || value < 1024 || value > 65535)
                        throw new ArgumentException("--eeg-port must be 1024..65535");
                    options.Port = value;
                }
            }
            return options;
        }
    }
    // Shared, engine-independent interaction rules. UI actions never synthesize motion packets.
    public sealed class WorkspaceState
    {
        public string Page { get; private set; } = "experience";
        public bool AllowTests { get; private set; }
        public bool EmergencyLatched { get; private set; }
        public void Navigate(string page) { Page = page == "link" || page == "settings" || page == "collection" ? page : "experience"; if (Page != "link") AllowTests = false; }
        public void SetAllowTests(bool allow) { AllowTests = allow && Page == "link" && !EmergencyLatched; }
        public void EmergencyStop() { EmergencyLatched = true; AllowTests = false; }
        public void ResetEmergency() { EmergencyLatched = false; AllowTests = false; }
        public void Reconnected() { AllowTests = false; }
    }
    public static class WorkspaceSafety
    {
        public static bool RequiresPlacementRegistration(bool android,bool xrActive){return android||xrActive;}
        public static void Reset(MotionCore core)
        {
            // A previous session and its signed packets cannot survive a local manual reset.
            core.ResetPairing(); core.EmergencyLatched = false; core.Brake("manual_reset");
        }
    }
    public sealed class RuntimeHealth
    {
        public string app_version = "", build_id = "", ui_state = "";
        public double frame_ms, frame_p95_ms;
        public long rendered_frames;
        public bool focused, paused, xr_active;
        static string Bounded(string value) { return value == null ? "" : value.Substring(0, Math.Min(120, value.Length)); }
        static double Timing(double value) { return Wire.Finite(value) ? Math.Max(0, Math.Min(10000, value)) : 0; }
        public void Apply(ReceiverState state)
        {
            state.app_version = Bounded(app_version); state.build_id = Bounded(build_id); state.ui_state = Bounded(ui_state);
            state.frame_ms = Timing(frame_ms); state.frame_p95_ms = Timing(frame_p95_ms); state.rendered_frames = Math.Max(0, rendered_frames);
            state.focused = focused; state.paused = paused; state.xr_active = xr_active;
        }
    }
}
