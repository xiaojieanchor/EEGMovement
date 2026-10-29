using System;
using System.Collections;
using UnityEngine;
using UnityEngine.UI;

namespace EEGMovement.Unity
{
    // Read-only, world-placed glass panel. It never consumes rays or creates commands.
    public sealed class QuestStatusOverlay : MonoBehaviour
    {
        [Serializable] public class Channel {public string id,name,kind,unit,color,quality_label;public bool training_eligible;}
        [Serializable] public class Signal { public bool connected, fresh, quality_valid,contact_quality_known; public float sample_rate, nominal_rate,age_ms; public int good_channels,channel_count,eeg_channel_count; public string device,quality_state,transport_label;public Channel[] channels; }
        [Serializable] public class Model { public string name, status, note,inference_state,candidate_kind,score_semantics; public bool calibrated,has_training_accuracy,has_development_accuracy; public int output_schema; public int parameters; public float compute_ms,active_probability,idle_probability,result_age_ms,training_accuracy,development_accuracy,roundtrip_ms,evidence_ms,hop_ms; public float[] probabilities,direction_probabilities;public int[] usable_channels; public bool result_fresh,inference_enabled; }
        [Serializable] public class Decision {public bool active,model_ready;public string action_label,reason,score_semantics;public float p_active,linear_confidence,turn_confidence;public int linear_axis,turn_axis;}
        [Serializable] public class Control {public bool enabled,fresh,calibrated;public float active_threshold,direction_threshold;public Decision decision;}
        [Serializable] public class Gpu { public int index; public string name; public float used_mb,total_mb,utilization,temperature; }
        [Serializable] public class Compute { public bool connected; public string device; public float health_age_ms; public Gpu[] gpus; }
        [Serializable] public class Execution { public bool known; public string state,mode; public float speed,yaw_rate; }
        [Serializable] public class Training { public bool formal_ready; public string checked_at,status; }
        [Serializable] public class Acquisition { public bool active; }
        [Serializable] public class Status { public int version; public PlacementCalibration.StatusData calibration; public Signal source; public Model model; public Compute server; public Execution execution; public Training training; public Acquisition acquisition;public Control control; }
        GameObject panel;
        public EEGAvatarController Controller;
        Transform head;
        Text freshness,model,probabilities,fusion,signal,compute,execution,training;
        Status latest;
        bool linkLogged;
        float receivedAt=-100;
        public bool Visible { get { return panel && panel.activeSelf; } }
        public void Initialize(Transform cameraHead)
        {
            head=cameraHead;
            panel=new GameObject("只读实时状态 · 按住B",typeof(RectTransform),typeof(Canvas));panel.transform.SetParent(transform,false);
            var canvas=panel.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=head.GetComponent<Camera>();canvas.sortingOrder=30;
            var rect=panel.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(1200,1020);rect.localScale=Vector3.one*.0012f;
            var scaler=panel.AddComponent<CanvasScaler>();scaler.dynamicPixelsPerUnit=2;
            var glass=panel.AddComponent<Image>();glass.color=new Color(.025f,.065f,.10f,.24f);glass.raycastTarget=false;
            Label("实时推理与执行",38,32,-22,1136,58,true);
            freshness=Label("连接中…",25,32,-88,1136,42);
            model=Label("模型状态待确认",26,32,-142,1136,106);
            probabilities=Label("动静 / 四方向概率待确认",25,32,-266,550,216,true);
            fusion=Label("融合状态待确认",24,32,-500,550,96);
            signal=Label("脑电状态待确认",24,636,-266,532,320);
            compute=Label("服务器状态待确认",25,636,-610,532,132);
            execution=Label("执行回执待确认",27,32,-610,550,120);
            training=Label("训练状态待确认",24,32,-768,1136,132);
            Label("按住B查看 · 松开隐藏 · 在电脑联调页启动推理\n角色：水色の描 · 动作：Quaternius（CC0）",22,32,-932,1136,64);
            panel.SetActive(false);StartCoroutine(Poll());
        }
        Text Label(string value,int size,float x,float y,float width,float height,bool bold=false)
        {
            var go=new GameObject("只读信息",typeof(RectTransform),typeof(Text));go.transform.SetParent(panel.transform,false);
            var rect=go.GetComponent<RectTransform>();rect.anchorMin=rect.anchorMax=rect.pivot=new Vector2(0,1);rect.anchoredPosition=new Vector2(x,y);rect.sizeDelta=new Vector2(width,height);
            var text=go.GetComponent<Text>();text.font=Resources.Load<Font>("Fonts/NotoSansCJKsc-Regular");text.fontSize=size;text.fontStyle=bold?FontStyle.Bold:FontStyle.Normal;text.text=value;text.color=new Color(.93f,.98f,1);text.raycastTarget=false;text.supportRichText=false;
            var shadow=go.AddComponent<Shadow>();shadow.effectColor=new Color(0,.025f,.05f,.85f);shadow.effectDistance=new Vector2(1,-1);
            var outline=go.AddComponent<Outline>();outline.effectColor=new Color(.01f,.025f,.04f,.95f);outline.effectDistance=new Vector2(1.5f,1.5f);return text;
        }
        public void SetVisible(bool show)
        {
            if (!panel || show == Visible) return;
            panel.SetActive(show);
            if(show)
            {
                if(Controller&&Controller.Acquisition&&(Controller.Acquisition.Active||Controller.Acquisition.Busy))
                {Controller.Acquisition.Abort("page_exit");Debug.Log("EEG_STATUS_OVERLAY guided_capture_aborted=true");}
                Recenter();
            }
            Debug.Log("EEG_STATUS_OVERLAY visible="+show+" readonly=true interaction=hold_B");
        }
        public void Recenter()
        {
            if (!Visible || !head) return;
            Vector3 forward=Vector3.ProjectOnPlane(head.forward,Vector3.up).normalized;if(forward.sqrMagnitude<.1f)forward=Vector3.forward;
            Vector3 right=Vector3.Cross(Vector3.up,forward);
            panel.transform.position=head.position+forward*1.45f+right*.8f;
            panel.transform.rotation=Quaternion.LookRotation((panel.transform.position-head.position).normalized,Vector3.up);
        }
        IEnumerator Poll()
        {
            while(true)
            {
                if(Visible||QuestNetwork.Ready)
                {
                    var task=QuestNetwork.Ready?QuestNetwork.Send("/v1/status"):SelfPacedAcquisition.Send("/api/vr/panel");while(!task.IsCompleted)yield return null;
                    if(!task.IsFaulted&&!task.IsCanceled)
                    {
                        try { var value=JsonUtility.FromJson<Status>(task.Result);if(value.version!=1||value.model==null||value.source==null||value.server==null||value.execution==null||value.training==null||value.acquisition==null)throw new Exception();latest=value;receivedAt=Time.unscaledTime;if(!linkLogged){linkLogged=true;Debug.Log("EEG_STATUS_LINK signed=true model="+value.model.status+" source_fresh="+value.source.fresh); } }
                        catch(Exception){receivedAt=-100;}
                    }
                }
                yield return new WaitForSecondsRealtime(Visible?.15f:1f);
            }
        }
        void Update()
        {
            if(!Visible)return;
            float age=Time.unscaledTime-receivedAt;
            if(latest==null||age>.75f)
            {
                freshness.text="连接待确认 · 旧数据已失效";model.text="模型：未知";probabilities.text="动 / 不动：—\n前 / 后 / 左 / 右：—";fusion.text="融合输出：未知";signal.text="脑电：未知";compute.text="服务器：未知";execution.text="执行：未知（不沿用旧回执）";training.text="训练准备：待刷新";return;
            }
            freshness.text=(Controller?Controller.ConnectionPath:QuestNetwork.ConnectionPath)+" · 中枢更新 "+age.ToString("F1")+"秒前";
            string mode=latest.model.name!=null&&latest.model.name.Contains("diagnostic")?"频谱 / CCA诊断":latest.model.name;
            bool scoresFresh=latest.model.inference_enabled&&latest.model.result_fresh&&latest.model.result_age_ms+age*1000<500;
            string inference=latest.model.inference_enabled?InferenceName(latest.model.inference_state):"已停止";
            model.text=(latest.model.parameters>0?"模型 · "+(latest.model.parameters/1000000f).ToString("F2")+"M":"模型尚未配置")+" · "+inference+"\n"+(mode??"模型身份未知")+"\nGPU "+(scoresFresh?latest.model.compute_ms.ToString("F1")+" ms":"—")+" · 返回 "+(scoresFresh?latest.model.roundtrip_ms.ToString("F1")+" ms":"—")+" · 证据 "+latest.model.evidence_ms.ToString("F0")+" ms / 更新 "+latest.model.hop_ms.ToString("F0")+" ms";
            var pd=latest.model.direction_probabilities;
            probabilities.text=scoresFresh&&pd!=null&&pd.Length==4?"动 "+Percent(latest.model.active_probability)+"    不动 "+Percent(latest.model.idle_probability)+"\n前 "+Percent(pd[0])+"    后 "+Percent(pd[1])+"\n左 "+Percent(pd[2])+"    右 "+Percent(pd[3])+"\n"+(latest.model.calibrated?"本次佩戴已校准":"模型原始分数"):"动 / 不动：—\n前 / 后 / 左 / 右：—\n"+(latest.model.note??"等待有效通道及实时脑电");
            if(scoresFresh&&latest.model.probabilities!=null&&latest.model.probabilities.Length==9){var joint=latest.model.probabilities;probabilities.text+="\n前左 "+Percent(joint[5])+"  前右 "+Percent(joint[6])+"\n后左 "+Percent(joint[7])+"  后右 "+Percent(joint[8]);}
            var c=latest.control;var d=c==null?null:c.decision;
            fusion.text=c==null?"融合状态：未知":!c.enabled?"模型动作关闭":!scoresFresh||!c.fresh||d==null?"融合输出：等待实时输入":"融合输出："+(d.active?ActionName(d.action_label):"停止 / 待确认")+"\n连续动 "+Percent(d.p_active)+" · 前后轴 "+Percent(d.linear_confidence)+" · 转向轴 "+Percent(d.turn_confidence)+"\n门限 动 "+Percent(c.active_threshold)+" / 轴内 "+Percent(c.direction_threshold)+" · 双轴融合映射";
            var src=latest.source;var columns=src.channels??new Channel[0];int count=columns.Length;
            signal.text=(src.device??"脑电")+" · "+(src.transport_label??"通路未知")+"\n"+(src.fresh?src.sample_rate.ToString("F0")+" Hz · "+src.age_ms.ToString("F0")+" ms":"无新鲜样本");
            signal.text+="\nEEG "+src.eeg_channel_count+" 路 / 数据列 "+count+" · 接触质量"+(src.contact_quality_known?"已测量":"未测量");
            int pages=Math.Max(1,(count+7)/8),page=(int)(Time.unscaledTime/4)%pages,begin=page*8,end=Math.Min(count,begin+8);
            if(pages>1)signal.text+="\n通道 "+(begin+1)+"–"+end+" / "+count+"（每4秒换页）";
            for(int i=begin;i<end;i++){var ch=columns[i];string label=ch.quality_label??"未测量";if(label.Length>10)label=label.Substring(0,10);signal.text+="\n"+(ch.name??"CH?").ToUpperInvariant()+" · "+(ch.kind??"EEG")+" · "+label;}
            compute.text="GPU服务："+(latest.server.connected?latest.server.device:"未确认");
            if(latest.server.connected&&latest.server.gpus!=null)foreach(var gpu in latest.server.gpus)compute.text+="\nGPU"+gpu.index+" · "+(gpu.used_mb/1024).ToString("F1")+" / "+(gpu.total_mb/1024).ToString("F0")+" GB · "+gpu.utilization.ToString("F0")+"% · "+gpu.temperature.ToString("F0")+"°C";
            execution.text=latest.execution.known?"实际执行："+latest.execution.mode+" · "+latest.execution.state+"\n"+latest.execution.speed.ToString("F2")+" m/s · "+latest.execution.yaw_rate.ToString("F1")+" °/s":"实际执行：回执未知";
            training.text="训练内 "+(latest.model.has_training_accuracy?Percent(latest.model.training_accuracy):"—")+" · 开发数据 "+(latest.model.has_development_accuracy?Percent(latest.model.development_accuracy):"—")+"\n当前佩戴准确率待实测\n可选校验："+(latest.calibration==null?"未执行":latest.calibration.reason)+"\n训练准备："+(latest.training.formal_ready?"软件门控通过":"待核验 / 待个人数据")+" · VR采集："+(latest.acquisition.active?"进行中":"未进行");
        }
        static string Percent(float value){return (100*Mathf.Clamp01(value)).ToString("F1")+"%";}
        static string InferenceName(string value){switch(value){case "running":return "运行中";case "starting":return "启动中";case "calibration_required":return "需要手动校准";case "diagnostic_ready":return "回放诊断就绪";case "waiting_source":return "等待采集数据";case "waiting_server":return "等待计算服务器";case "waiting_input":return "等待连续输入";case "filter_warming":return "滤波预热";case "model_warming":return "重建模型状态";case "warming":return "预热中";case "rejected":return "质量门控";case "error":return "服务异常";default:return "等待输入";}}
        static string ActionName(string label){switch(label){case "FORWARD":return "前进";case "BACKWARD":return "后退";case "LEFT":return "左转";case "RIGHT":return "右转";case "FORWARD_LEFT":return "前进 + 左转";case "FORWARD_RIGHT":return "前进 + 右转";case "BACKWARD_LEFT":return "后退 + 左转";case "BACKWARD_RIGHT":return "后退 + 右转";default:return "停止";}}
    }
}
