using System;
using System.Collections.Concurrent;
using System.Text;
using UnityEngine;

namespace EEGMovement.Unity {
    public sealed class QuestUpdater:MonoBehaviour {
        public static QuestUpdater Instance {get;private set;}
        public static string Status {get;private set;}="更新等待检查";
        AndroidJavaObject native;Listener callback;readonly ConcurrentQueue<string> reports=new ConcurrentQueue<string>();
        bool stopIssued;float nextTick;
        sealed class Listener:AndroidJavaProxy {
            readonly QuestUpdater owner;public Listener(QuestUpdater value):base("org.eegmovement.relay.UpdateBridge$Listener"){owner=value;}
            public void progress(string state,string message){owner.reports.Enqueue(state+"|"+message);}
        }
        void Start(){
            Instance=this;
            if(Application.platform!=RuntimePlatform.Android)return;
            try{var config=Resources.Load<TextAsset>("relay_public");callback=new Listener(this);using(var player=new AndroidJavaClass("com.unity3d.player.UnityPlayer"))native=new AndroidJavaObject("org.eegmovement.relay.UpdateBridge",player.GetStatic<AndroidJavaObject>("currentActivity"),config.text,callback);Status="启动后自动检查";}
            catch(Exception e){Status="更新初始化需要检查："+e.GetType().Name;}
        }
        bool Idle(){var c=GetComponent<EEGAvatarController>();return c&&c.Workspace.EmergencyLatched&&(!c.Acquisition||!c.Acquisition.Active&&!c.Acquisition.Busy)&&(!c.SelfPaced||!c.SelfPaced.Active&&!c.SelfPaced.Busy)&&(c.State==null||Math.Abs(c.State.actual_speed)<.001&&Math.Abs(c.State.actual_yaw_rate)<.001);}
        void Update(){
            if(native==null)return;
            if(QuestNetwork.Relay!=null&&QuestNetwork.Relay.TakeUpdateNotice())native.Call("check");
            bool safe=Idle()&&Application.isFocused;native.Call("setSafe",safe);
            if(Time.unscaledTime>=nextTick){nextTick=Time.unscaledTime+5;native.Call("tick");Status=native.Call<string>("status");}
            string report;while(reports.TryDequeue(out report)){
                Status=report.Substring(report.IndexOf('|')+1);
                if(report.StartsWith("install_requested|")&&!stopIssued){GetComponent<EEGAvatarController>().EmergencyStop();stopIssued=true;}
                try{if(QuestNetwork.Relay!=null&&QuestNetwork.Relay.Connected)QuestNetwork.Relay.Send("update_state",Encoding.UTF8.GetBytes(JsonUtility.ToJson(new State{state=report.Split('|')[0],message=Status,version=Application.version})));}catch(Exception){}
            }
        }
        [Serializable] sealed class State {public string state,message,version;}
        public void Confirm(){if(native!=null){GetComponent<EEGAvatarController>().EmergencyStop();native.Call("setSafe",Idle()&&Application.isFocused);native.Call("confirm");}}
        void OnDestroy(){if(native!=null){native.Call("stop");native.Dispose();}if(Instance==this)Instance=null;}
    }
}
