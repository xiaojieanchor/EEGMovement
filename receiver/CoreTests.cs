using System;
using System.Collections.Generic;
using System.Text;
using EEGMovement.Core;

public static class CoreTests
{
    static int passed;
    static readonly List<string> failed = new List<string>();
    static void Check(bool condition, string text) { if (!condition) throw new Exception(text); }
    static MotionCore Core(bool allow = true)
    {
        var c = new MotionCore("receiver", "csharp_harness", allow);
        long ticket = c.IssueTicket(0);
        Check(c.Accept(new Packet { version = 1, kind = "welcome", receiver_id = c.ReceiverId, nonce = c.Nonce, ticket = ticket, session = new string('a', 32) }, 0), "pairing failed");
        return c;
    }
    static Packet Command(MotionCore c, long seq, double now, int direction = 1)
    {
        return new Packet { version = 1, kind = "command", receiver_id = c.ReceiverId, nonce = c.Nonce, session = c.Session, ticket = c.IssueTicket(now), seq = seq, decision_id = seq, epoch = 1, ttl_ms = 180, mode = "TEST", active = direction != 0, input_valid = true, model_ready = true, direction = direction, p_active = .95, confidence = .95, speed = .6, yaw_rate = 40 };
    }
    static void Move(MotionCore c)
    {
        c.Accept(Command(c, 1, 0), 0); c.Step(.01, 0);
        c.Accept(Command(c, 2, 50), 50); c.Step(.01, 50);
        Check(c.Speed > 0, "did not enter confirmed motion");
    }
    static Packet Axes(MotionCore c,long seq,double now,int linear=1,int turn=-1)
    {
        var p=Command(c,seq,now);p.mode="LIVE";p.motion_schema=2;p.linear_axis=linear;p.turn_axis=turn;p.enter_active_threshold=.75;p.enter_direction_threshold=.7;p.linear_confidence=p.turn_confidence=.9;p.linear_mass=p.turn_mass=.5;p.linear_strength=p.turn_strength=.8;p.axis_mass_threshold=.2;return p;
    }
    static void Test(string name, Action test)
    {
        try { test(); passed++; }
        catch (Exception e) { failed.Add(name + ": " + e.Message); }
    }
    public static int Main()
    {
        Test("relay heartbeat uses signed receiver ticket and starts latched",()=>{
            var codec=new DesktopJson();byte[] key=new byte[32];byte[] sent=null;
            using(var client=new ReceiverClient(System.IO.Path.Combine(System.IO.Path.GetTempPath(),Guid.NewGuid().ToString("N")+".json"),0,"csharp_harness",false,codec)){
                client.SetRelay(raw=>sent=raw,key);client.Pulse();Check(sent!=null,"relay heartbeat absent");var p=Wire.Decode(sent,key,codec);
                Check(p.kind=="heartbeat"&&p.ticket>0,"receiver-issued ticket absent");Check(client.Core.EmergencyLatched,"relay enabled motion implicitly");
                string nonce=client.Core.Nonce;client.RelayDisconnected();Check(client.Core.EmergencyLatched&&client.Core.Speed==0,"disconnect did not stop");Check(nonce!=client.Core.Nonce,"old relay nonce retained");
            }
        });
        Test("relay rejects malformed wire without entering motion",()=>{
            using(var client=new ReceiverClient(System.IO.Path.Combine(System.IO.Path.GetTempPath(),Guid.NewGuid().ToString("N")+".json"),0,"csharp_harness",false,new DesktopJson())){
                client.SetRelay(raw=>{},new byte[32]);client.AcceptRelay(new byte[5000]);Check(client.Snapshot().invalid_wire==1&&client.Core.Speed==0,"bad relay wire accepted");
            }
        });
        Test("Android bootstrap is gated even before XR reports an active device",()=>{
            Check(WorkspaceSafety.RequiresPlacementRegistration(true,false),"Android non-XR boot bypass");
            Check(WorkspaceSafety.RequiresPlacementRegistration(true,true),"Android XR bypass");
            Check(WorkspaceSafety.RequiresPlacementRegistration(false,true),"desktop XR bypass");
            Check(!WorkspaceSafety.RequiresPlacementRegistration(false,false),"plain desktop compatibility lost");
        });
        Test("unregistered app cannot execute even authenticated fresh commands",()=>{
            var c=Core();Move(c);c.SetExecutionPermission(false);Check(c.Speed==0&&c.YawRate==0,"gate did not brake");
            c.Accept(new Packet{version=1,kind="welcome",receiver_id=c.ReceiverId,nonce=c.Nonce,ticket=c.IssueTicket(100),session=new string('b',32)},100);
            for(int i=1;i<=6;i++){c.Accept(Axes(c,i,100+i*50),100+i*50);c.Step(.02,100+i*50);Check(c.Speed==0&&c.YawRate==0,"blocked app moved");}
        });
        Test("registration gate transition invalidates queued old commands and preserves emergency",()=>{
            var c=Core();c.SetExecutionPermission(false);var old=Command(c,1,0);string nonce=c.Nonce;
            c.EmergencyLatched=true;c.SetExecutionPermission(true);
            Check(c.Nonce!=nonce&&!c.Accept(old,1),"old command survived registration");Check(c.EmergencyLatched,"registration cleared user emergency");
        });
        Test("compatible motion axes all four combinations",()=>{
            foreach(int linear in new[]{-1,1})foreach(int turn in new[]{-1,1}){
                var c=Core();double previous=0;for(int i=1;i<=12;i++){c.Accept(Axes(c,i,i*50,linear,turn),i*50);c.Step(.02,i*50);Check(Math.Abs(c.Speed-previous)<=.018001,"speed acceleration jump");previous=c.Speed;}
                Check(linear*c.Speed>0&&turn*c.YawRate>0,"did not execute both axes");Check(Math.Abs(c.TargetSpeed-.48*linear)<1e-6,"probability strength missing");
            }
        });
        Test("opposite change crosses zero without frame reversal",()=>{
            var c=Core();for(int i=1;i<=15;i++){c.Accept(Axes(c,i,i*50),i*50);c.Step(.02,i*50);}
            bool zero=false,reverse=false;double previous=c.Speed;
            for(int i=16;i<=35;i++){c.Accept(Axes(c,i,i*50,-1,-1),i*50);c.Step(.02,i*50);Check(!(previous>0&&c.Speed<0),"jumped through zero");zero|=c.Speed==0;reverse|=c.Speed<0;previous=c.Speed;}
            Check(zero&&reverse,"reverse transition not completed");
        });
        Test("bad composite axis refuses and invalid data stops both axes",()=>{
            var c=Core();c.Accept(Axes(c,1,0),0);c.Accept(Axes(c,2,50),50);c.Step(.02,50);Check(c.Speed>0&&c.YawRate<0,"precondition");
            var p=Axes(c,3,100);p.linear_axis=2;c.Accept(p,100);Check(c.Speed==0&&c.YawRate==0,"invalid axis did not brake");
            c.Accept(Axes(c,4,150),150);c.Accept(Axes(c,5,200),200);c.Step(.02,200);p=Axes(c,6,250);p.input_valid=false;p.reason="source_stale";c.Accept(p,250);Check(c.Speed==0&&c.YawRate==0,"invalid input did not stop axes");
        });
        Test("LAN requires explicit private literal", () => {
            Check(PairingAddress.Parse("172.29.30.38", "lan").ToString() == "172.29.30.38", "private LAN denied");
            foreach (string host in new[] { "8.8.8.8", "example.com", "198.18.0.1", "::1" }) {
                bool denied = false; try { PairingAddress.Parse(host, "lan"); } catch (ArgumentException) { denied = true; } Check(denied, "nonprivate address accepted");
            }
            bool explicitDenied = false; try { PairingAddress.Parse("172.29.30.38", null); } catch (ArgumentException) { explicitDenied = true; }
            Check(explicitDenied, "LAN enabled without explicit provision");
            Check(PairingAddress.Parse("127.0.0.1", null).ToString() == "127.0.0.1", "local compatibility lost");
        });
        Test("valid confirmed motion", () => { var c = Core(); Move(c); Check(c.Z > 0 && c.Speed <= .009001, "initial acceleration discontinuity"); });
        Test("single confidence spike", () => { var c = Core(); c.Accept(Command(c, 1, 0), 0); c.Step(.02, 20); Check(c.Speed == 0, "single update moved"); });
        Test("same-frame burst cannot confirm", () => { var c = Core(); for (int i = 1; i < 9; i++) c.Accept(Command(c, i, 50), 50); c.Step(.01, 50); Check(c.Speed == 0, "burst bypassed confirmation"); });
        Test("explicitly enrolled campus IPv4 only", () => {
            Check(PairingAddress.Parse("101.6.10.20","lan",true).ToString()=="101.6.10.20","enrolled campus denied");
            foreach(string host in new[]{"0.0.0.0","127.0.0.2","169.254.1.2","224.0.0.1","255.255.255.255"}){
                bool denied=false;try{PairingAddress.Parse(host,"lan",true);}catch(ArgumentException){denied=true;}
                Check(denied,"invalid enrolled endpoint accepted");
            }
        });
        Test("same ticket across frames cannot confirm", () => { var c = Core(); var p = Command(c, 1, 0); c.Accept(p, 0); p.seq = 2; c.Accept(p, 50); c.Step(.01, 50); Check(c.Speed == 0, "replayed ticket satisfied temporal confirmation"); });
        Test("one classifier decision repeated cannot confirm", () => { var c = Core(); var a = Command(c, 1, 0); c.Accept(a, 0); var b = Command(c, 2, 50); b.decision_id = a.decision_id; c.Accept(b, 50); c.Step(.01, 50); Check(c.Speed == 0, "one decision counted twice"); });
        Test("uncertain classifier cannot start", () => { var c = Core(); for (int i = 1; i < 20; i++) { var p = Command(c, i, i * 50); p.p_active = .79; c.Accept(p, i * 50); c.Step(.01, i * 50); } Check(c.Speed == 0, "uncertainty moved"); });
        Test("invalid signal immediate brake", () => { var c = Core(); Move(c); var p = Command(c, 3, 80); p.input_valid = false; c.Accept(p, 80); Check(c.Speed == 0 && c.TargetSpeed == 0, "invalid signal coasted"); });
        Test("model unready immediate brake", () => { var c = Core(); Move(c); var p = Command(c, 3, 80); p.model_ready = false; c.Accept(p, 80); Check(c.Speed == 0, "unready model coasted"); });
        Test("watchdog stops within lease", () => { var c = Core(); Move(c); c.Step(.01, 231); Check(c.Speed == 0 && c.WatchdogStops == 1, "watchdog failed"); });
        Test("delayed packet does not refresh TTL", () => { var c = Core(); Move(c); var p = Command(c, 3, 100); Check(c.Accept(p, 220), "still-valid delayed packet rejected"); c.Step(.01, 281); Check(c.Speed == 0, "lease restarted on arrival"); });
        Test("late packet rejected without poisoning", () => { var c = Core(); Move(c); var p = Command(c, 100, 0); Check(!c.Accept(p, 200), "expired accepted"); Check(c.LastSeq == 2, "late packet poisoned seq"); });
        Test("duplicate and reverse order rejected", () => { var c = Core(); Move(c); Check(!c.Accept(Command(c, 2, 60), 60), "duplicate accepted"); Check(!c.Accept(Command(c, 1, 70), 70), "old packet accepted"); });
        Test("wrong receiver rejected", () => { var c = Core(); var p = Command(c, 1, 0); p.receiver_id = "foreign"; Check(!c.Accept(p, 0), "foreign receiver accepted"); });
        Test("wrong session rejected", () => { var c = Core(); var p = Command(c, 1, 0); p.session = new string('b', 32); Check(!c.Accept(p, 0), "foreign session accepted"); });
        Test("old nonce rejected", () => { var c = Core(); var p = Command(c, 1, 0); p.nonce = "old"; Check(!c.Accept(p, 0), "foreign nonce accepted"); });
        Test("unissued receiver ticket rejected", () => { var c = Core(); var p = Command(c, 1, 0); p.ticket = 98765; Check(!c.Accept(p, 0), "unissued ticket accepted"); });
        Test("future sequence jump rejected", () => { var c = Core(); Check(!c.Accept(Command(c, 10001, 0), 0), "huge sequence accepted"); Check(c.LastSeq == 0, "sequence poisoned"); });
        Test("NaN command rejected", () => { var c = Core(); var p = Command(c, 100, 0); p.speed = Double.NaN; Check(!c.Accept(p, 0) && c.LastSeq == 0, "nonfinite poisoned state"); });
        Test("overspeed rejected", () => { var c = Core(); var p = Command(c, 1, 0); p.speed = 1.21; Check(!c.Accept(p, 0), "overspeed accepted"); });
        Test("excess yaw rejected", () => { var c = Core(); var p = Command(c, 1, 0); p.yaw_rate = 61; Check(!c.Accept(p, 0), "excess yaw accepted"); });
        Test("TTL cannot be extended", () => { var c = Core(); var p = Command(c, 1, 0); p.ttl_ms = 1000; Check(!c.Accept(p, 0), "excess lease accepted"); });
        Test("TEST requires receiver consent", () => { var c = Core(false); Check(!c.Accept(Command(c, 1, 0), 0), "TEST enabled implicitly"); });
        Test("LIVE uncalibrated never moves", () => { var c = Core(); for (int i = 1; i < 10; i++) { var p = Command(c, i, 50 * i); p.mode = "LIVE"; p.model_ready = false; c.Accept(p, i * 50); c.Step(.01, i * 50); } Check(c.Z == 0 && c.Speed == 0, "untrained motion"); });
        Test("LIVE explicit experimental thresholds execute",()=>{var c=Core();for(int i=1;i<8;i++){var p=Command(c,i,50*i);p.mode="LIVE";p.enter_active_threshold=.7;p.enter_direction_threshold=.5;p.p_active=.78;p.confidence=.58;c.Accept(p,50*i);c.Step(.01,50*i);}Check(c.Speed>0&&c.Z>0,"valid LIVE decision did not execute");});
        Test("stream change forces reconfirmation", () => { var c = Core(); Move(c); var p = Command(c, 3, 80); p.epoch = 2; c.Accept(p, 80); c.Step(.01, 80); Check(c.Speed == 0, "cross-source state leaked"); });
        Test("old epoch rejected", () => { var c = Core(); Move(c); var p = Command(c, 3, 80); p.epoch = 0; Check(!c.Accept(p, 80), "old epoch accepted"); });
        Test("replayed welcome cannot reset seq", () => { var c = Core(); Move(c); var w = new Packet { version = 1, kind = "welcome", receiver_id = c.ReceiverId, nonce = c.Nonce, session = c.Session, ticket = c.IssueTicket(70) }; c.Accept(w, 70); Check(c.LastSeq == 2, "welcome reset history"); });
        Test("authenticated NAT rebind stops and invalidates old tickets", () => {
            var c = Core(); Move(c); string oldNonce = c.Nonce;
            var old = Command(c, 3, 70);
            var p = new Packet {version=1,kind="rebind",receiver_id=c.ReceiverId,nonce=c.Nonce,
                session=c.Session,ticket=c.IssueTicket(70)};
            c.EmergencyLatched=true;
            Check(c.Accept(p,70),"rebind was ignored");
            Check(c.Speed==0&&c.YawRate==0&&c.Session==""&&c.Nonce!=oldNonce,"mapping change retained execution state");
            Check(c.EmergencyLatched&&!c.Accept(old,71),"rebind bypassed emergency or kept old ticket");
        });
        Test("expired rebind cannot reset a live receiver", () => {
            var c=Core();Move(c);string nonce=c.Nonce;
            var p=new Packet{version=1,kind="rebind",receiver_id=c.ReceiverId,nonce=nonce,session=c.Session,ticket=c.IssueTicket(0)};
            Check(!c.Accept(p,201)&&c.Nonce==nonce,"old rebind altered pairing");
        });
        Test("normal idle speed remains continuous", () => { var c = Core(); Move(c); c.Accept(Command(c, 3, 60, 0), 60); c.Step(.01, 60); Check(c.Speed == 0, "idle failed"); });
        Test("frame stall brakes", () => { var c = Core(); Move(c); c.Step(.2, 60); Check(c.Speed == 0 && c.Reason == "frame_stall", "stall advanced motion"); });
        Test("local emergency latches", () => { var c = Core(); Move(c); c.EmergencyLatched = true; c.Step(.01, 70); c.Accept(Command(c, 3, 80), 80); Check(c.Speed == 0, "network bypassed emergency latch"); });
        Test("bad engine pose never acknowledged", () => { var c = Core(); Move(c); c.ConfirmApplied(Double.NaN, 0, 0, 0, 0); Check(c.AppliedSeq == 0 && c.Speed == 0, "invalid pose acknowledged"); });
        Test("ack follows engine application", () => { var c = Core(); Move(c); Check(c.AppliedSeq == 0, "received was reported applied"); c.ConfirmApplied(c.X, c.Z, c.Yaw, c.Speed, c.YawRate); Check(c.AppliedSeq == 2, "applied ack absent"); });
        Test("wire signature and interop DTO", () => { var codec = new DesktopJson(); byte[] key = new byte[32]; var c = Core(); var p = Command(c, 1, 0); byte[] wire = Wire.Encode(p, key, codec); var q = Wire.Decode(wire, key, codec); Check(q.seq == 1 && q.receiver_id == c.ReceiverId, "DTO did not roundtrip"); });
        Test("bad wire signature rejected", () => { var codec = new DesktopJson(); byte[] key = new byte[32]; var p = Command(Core(), 1, 0); var data = Wire.Encode(p, key, codec); byte[] badKey = new byte[32]; badKey[0] = 4; bool denied = false; try { Wire.Decode(data, badKey, codec); } catch { denied = true; } Check(denied, "wrong key accepted"); });
        Test("oversized wire rejected", () => { bool denied = false; try { Wire.Decode(new byte[5000], new byte[32], new DesktopJson()); } catch { denied = true; } Check(denied, "unbounded wire accepted"); });
        Console.WriteLine(new DesktopJson().Encode(new { passed = passed, failed = failed.ToArray(), scope = "shared C# core, not Unity engine rendering" }));
        return failed.Count == 0 ? 0 : 1;
    }
}
