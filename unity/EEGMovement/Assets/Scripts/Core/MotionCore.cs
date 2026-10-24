using System;
using System.Collections.Generic;

namespace EEGMovement.Core
{
    // Pure C#: the exact file is compiled into both the Unity player and local test receiver.
    // All deadlines use the receiver's own monotonic clock and locally issued tickets.
    public sealed class MotionCore
    {
        public const double MaxSpeed = 1.2;
        public const double MaxYawRate = 60;
        public readonly string ReceiverId;
        public string Nonce { get; private set; }
        public string Session { get; private set; }
        public readonly string Role;
        public readonly bool AllowTest;
        public double X, Z, Yaw;
        public double Speed { get; private set; }
        public double YawRate { get; private set; }
        public double TargetSpeed { get; private set; }
        public double TargetYawRate { get; private set; }
        public long LastSeq { get; private set; }
        public long AppliedSeq { get; private set; }
        public string Mode { get; private set; }
        public string Reason { get; private set; }
        public int Accepted, Rejected, WatchdogStops;
        public double LastWatchdogBrakeMs;
        public int Direction { get; private set; }
        public bool EmergencyLatched;
        public bool ExecutionPermitted { get; private set; } = true;
        readonly Dictionary<long, double> tickets = new Dictionary<long, double>();
        long ticketCounter;
        long candidateDecision;
        int epoch = -1, candidate, confirmations;
        double expires = -1, candidateSince, lastTicketTime, roundtrip, actualSpeed, actualYaw;
        int linearAxis,turnAxis;
        sealed class AxisGate {public int current,candidate,count;public long decision;public double since;public void Clear(){current=candidate=count=0;decision=0;since=0;}}
        readonly AxisGate linearGate=new AxisGate(),turnGate=new AxisGate();

        public MotionCore(string receiverId, string role, bool allowTest)
        {
            ReceiverId = receiverId; Role = role; AllowTest = allowTest; ResetPairing();
        }
        public void ResetPairing()
        {
            Nonce = Guid.NewGuid().ToString("N"); Session = ""; LastSeq = 0; AppliedSeq = 0;
            epoch = -1; tickets.Clear(); expires = -1; Mode = "LIVE"; Brake("waiting_pairing");
        }
        public void SetExecutionPermission(bool permitted)
        {
            if(ExecutionPermitted==permitted)return;
            ExecutionPermitted=permitted;
            // Neither direction may inherit queued packets/tickets from before
            // the gate transition. Keep the user's emergency latch intact.
            ResetPairing();if(!permitted)Brake("application_registration_pending");
        }
        public long IssueTicket(double now)
        {
            var expired = new List<long>();
            foreach (var item in tickets) if (now - item.Value > 1000) expired.Add(item.Key);
            foreach (var key in expired) tickets.Remove(key);
            if (tickets.Count > 80) tickets.Clear();
            long ticket = ++ticketCounter; tickets[ticket] = now; return ticket;
        }
        bool Reject() { Rejected++; return false; }
        public bool Accept(Packet p, double now)
        {
            double issued;
            if (p == null || p.version != 1 || p.receiver_id != ReceiverId || p.nonce != Nonce || !tickets.TryGetValue(p.ticket, out issued)) return Reject();
            double age = now - issued;
            if (!Wire.Finite(now) || age < 0 || age > 200) return Reject();
            if (p.kind == "rebind")
            {
                // A signed reply still needs a current nonce and locally issued,
                // unexpired ticket. Mapping changes never renew a motion lease.
                ResetPairing(); Brake("network_rebind_required"); return true;
            }
            if (p.kind == "welcome")
            {
                if (String.IsNullOrEmpty(p.session) || p.session.Length != 32) return Reject();
                if (Session == p.session) return true; // A replayed welcome never resets sequence history.
                Session = p.session; LastSeq = 0; AppliedSeq = 0; epoch = -1; expires = -1; Brake("paired_waiting_command");
                return true;
            }
            if (p.kind != "command" || String.IsNullOrEmpty(Session) || p.session != Session || p.seq <= LastSeq || p.seq - LastSeq > 10000) return Reject();
            if (p.ttl_ms < 50 || p.ttl_ms > 180 || age > p.ttl_ms - 25 || p.epoch < epoch) return Reject();
            if ((p.mode != "LIVE" && p.mode != "TEST") || (p.mode == "TEST" && !AllowTest)) return Reject();
            if (p.active && p.input_valid && p.model_ready && p.decision_id <= 0) return Reject();
            if (!Wire.Finite(p.speed) || !Wire.Finite(p.yaw_rate) || !Wire.Finite(p.p_active) || !Wire.Finite(p.confidence) || p.speed < 0 || p.speed > MaxSpeed || p.yaw_rate < 0 || p.yaw_rate > MaxYawRate || p.p_active < 0 || p.p_active > 1 || p.confidence < 0 || p.confidence > 1 || p.direction < 0 || p.direction > 4) return Reject();
            if (p.epoch != epoch || p.mode != Mode) Brake("source_transition");
            epoch = p.epoch; Mode = p.mode; LastSeq = p.seq; Accepted++;
            lastTicketTime = issued; roundtrip = age; expires = issued + p.ttl_ms;
            if (EmergencyLatched) { Brake("local_emergency_stop"); return true; }
            if (!ExecutionPermitted) { Brake("application_registration_pending"); return true; }
            if (!p.input_valid || !p.model_ready) { Brake(p.reason ?? "invalid_or_unready"); return true; }
            double enterActive=p.mode=="LIVE"&&p.enter_active_threshold>0?p.enter_active_threshold:.8;
            double enterDirection=p.mode=="LIVE"&&p.enter_direction_threshold>0?p.enter_direction_threshold:.7;
            if(!Wire.Finite(enterActive)||!Wire.Finite(enterDirection)||enterActive<.5||enterActive>.995||enterDirection<.35||enterDirection>.995){Brake("invalid_model_threshold");return true;}
            if(p.mode=="LIVE"&&p.motion_schema!=0&&p.motion_schema!=2&&p.motion_schema!=3){Brake("unsupported_motion_schema");return true;}
            if(p.mode=="LIVE"&&(p.motion_schema==2||p.motion_schema==3))return AcceptAxes(p,issued,enterActive,enterDirection);
            if (!p.active || p.direction == 0 || p.p_active < .35 || p.confidence < (p.mode=="LIVE"?.3:.45))
            {
                Direction = 0; TargetSpeed = 0; TargetYawRate = 0; candidate = 0; confirmations = 0; Reason = "idle"; return true;
            }
            // Two separate, well-supported updates are required to enter or change direction.
            // Multiple queued packets in one frame cannot satisfy the temporal confirmation.
            if (Direction != p.direction)
            {
                if (p.p_active < enterActive || p.confidence < enterDirection)
                {
                    candidate = 0; confirmations = 0; TargetSpeed = 0; TargetYawRate = 0; Reason = "uncertain"; return true;
                }
                if (candidate != p.direction) { candidate = p.direction; confirmations = 1; candidateSince = issued; candidateDecision = p.decision_id; }
                else if (issued - candidateSince >= 30 && p.decision_id != candidateDecision) { confirmations++; candidateSince = issued; candidateDecision = p.decision_id; }
                if (confirmations < 2) { TargetSpeed = 0; TargetYawRate = 0; Reason = "confirming"; return true; }
                Direction = p.direction; confirmations = 0; candidate = 0;
            }
            if (p.p_active < (p.mode=="TEST"?.55:Math.Max(.35,enterActive-.2)) || p.confidence < (p.mode=="TEST"?.6:Math.Max(.3,enterDirection-.15))) { TargetSpeed = 0; TargetYawRate = 0; Reason = "uncertain"; return true; }
            TargetSpeed = Direction == 1 ? p.speed : Direction == 2 ? -p.speed : 0;
            TargetYawRate = Direction == 3 ? -p.yaw_rate : Direction == 4 ? p.yaw_rate : 0;
            Reason = "active"; return true;
        }
        static bool Unit(double value){return Wire.Finite(value)&&value>=0&&value<=1;}
        int SelectAxis(AxisGate gate,int requested,double confidence,double mass,Packet p,double issued,double enterActive,double enterDirection)
        {
            if(requested==0||p.p_active<Math.Max(.35,enterActive-.2)||confidence<Math.Max(.52,enterDirection-.1)||mass<Math.Max(.1,p.axis_mass_threshold-.06)){gate.Clear();return 0;}
            // Schema3 is negotiated: the authenticated hub owns probability and
            // temporal confirmation. Ticket TTL/watchdog/physical ramp stay here.
            if(p.motion_schema==3){gate.current=requested;gate.candidate=gate.count=0;return requested;}
            if(gate.current==requested)return requested;
            gate.current=0;
            if(p.p_active<enterActive||confidence<enterDirection||mass<p.axis_mass_threshold){gate.Clear();return 0;}
            if(gate.candidate!=requested){gate.candidate=requested;gate.count=1;gate.decision=p.decision_id;gate.since=issued;}
            else if(issued-gate.since>=30&&p.decision_id!=gate.decision){gate.count++;gate.decision=p.decision_id;gate.since=issued;}
            if(gate.count>=2){gate.current=requested;gate.candidate=0;gate.count=0;}
            return gate.current;
        }
        bool AcceptAxes(Packet p,double issued,double enterActive,double enterDirection)
        {
            if(p.linear_axis< -1||p.linear_axis>1||p.turn_axis< -1||p.turn_axis>1||!Unit(p.linear_confidence)||!Unit(p.turn_confidence)||!Unit(p.linear_mass)||!Unit(p.turn_mass)||!Unit(p.linear_strength)||!Unit(p.turn_strength)||!Wire.Finite(p.axis_mass_threshold)||p.axis_mass_threshold<.15||p.axis_mass_threshold>.45){Brake("invalid_axis_command");return true;}
            if(!p.active){linearGate.Clear();turnGate.Clear();linearAxis=turnAxis=Direction=0;TargetSpeed=TargetYawRate=0;Reason="idle";return true;}
            linearAxis=SelectAxis(linearGate,p.linear_axis,p.linear_confidence,p.linear_mass,p,issued,enterActive,enterDirection);
            turnAxis=SelectAxis(turnGate,p.turn_axis,p.turn_confidence,p.turn_mass,p,issued,enterActive,enterDirection);
            TargetSpeed=linearAxis*p.speed*p.linear_strength;TargetYawRate=turnAxis*p.yaw_rate*p.turn_strength;
            Direction=linearAxis!=0?(linearAxis>0?1:2):turnAxis!=0?(turnAxis<0?3:4):0;
            Reason=Direction==0?"confirming":"active";return true;
        }
        public void Brake(string reason)
        {
            Speed = 0; YawRate = 0; TargetSpeed = 0; TargetYawRate = 0;
            Direction = 0; confirmations = 0; candidate = 0; Reason = reason;
            actualSpeed = 0; actualYaw = 0;
            linearGate.Clear();turnGate.Clear();linearAxis=turnAxis=0;
        }
        static double Towards(double a, double b, double max) { return a < b ? Math.Min(a + max, b) : Math.Max(a - max, b); }
        public void Step(double dt, double now)
        {
            if(!ExecutionPermitted){Brake("application_registration_pending");return;}
            if (!Wire.Finite(dt) || dt < 0 || dt > .1) { Brake("frame_stall"); return; }
            if (EmergencyLatched) { Brake("local_emergency_stop"); return; }
            if (expires < 0 || now > expires)
            {
                if (Speed != 0 || YawRate != 0 || TargetSpeed != 0 || TargetYawRate != 0) { WatchdogStops++; LastWatchdogBrakeMs = now - lastTicketTime; }
                Brake(String.IsNullOrEmpty(Session) ? "waiting_pairing" : "watchdog_timeout"); return;
            }
            double accel = TargetSpeed == 0 || (Speed != 0 && Math.Sign(TargetSpeed) != Math.Sign(Speed)) ? 4.0 : .9;
            Speed = Towards(Speed, Speed*TargetSpeed<0?0:TargetSpeed, accel * dt);
            YawRate = Towards(YawRate, YawRate*TargetYawRate<0?0:TargetYawRate, (TargetYawRate == 0||YawRate*TargetYawRate<0 ? 360 : 180) * dt);
            Yaw = (Yaw + YawRate * dt + 360) % 360;
            double x = X + Math.Sin(Yaw * Math.PI / 180) * Speed * dt;
            double z = Z + Math.Cos(Yaw * Math.PI / 180) * Speed * dt;
            if (Math.Abs(x) > 10 || Math.Abs(z) > 10) { Brake("arena_boundary"); return; }
            X = x; Z = z;
        }
        // Must run AFTER transform/physics application, before publishing telemetry.
        public void ConfirmApplied(double x, double z, double yaw, double measuredSpeed, double measuredYaw)
        {
            if (!Wire.Finite(x) || !Wire.Finite(z) || !Wire.Finite(yaw) || !Wire.Finite(measuredSpeed) || !Wire.Finite(measuredYaw)) { Brake("invalid_engine_pose"); return; }
            X = x; Z = z; Yaw = yaw; actualSpeed = measuredSpeed; actualYaw = measuredYaw; AppliedSeq = LastSeq;
        }
        public ReceiverState Snapshot(double now)
        {
            return new ReceiverState { paired = !String.IsNullOrEmpty(Session), role = Role, mode = Mode, state = Math.Abs(Speed)>1e-6||Math.Abs(YawRate)>1e-6?"MOVING":"STOP", reason = Reason, direction = Direction,linear_axis=linearAxis,turn_axis=turnAxis, received_seq = LastSeq, applied_seq = AppliedSeq, x = X, z = Z, yaw = Yaw, speed = Speed, yaw_rate = YawRate, actual_speed = actualSpeed, actual_yaw_rate = actualYaw, target_speed = TargetSpeed, target_yaw_rate = TargetYawRate, command_roundtrip_ms = roundtrip, command_age_ms = now - lastTicketTime, accepted = Accepted, rejected = Rejected, watchdog_stops = WatchdogStops, watchdog_brake_ms = LastWatchdogBrakeMs };
        }
    }
}
