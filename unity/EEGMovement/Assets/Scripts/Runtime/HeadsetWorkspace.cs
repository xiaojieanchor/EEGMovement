using UnityEngine;
using UnityEngine.UI;
using UnityEngine.Events;

namespace EEGMovement.Unity
{
    // A headset layout, rather than a distant desktop-sized dashboard.
    public sealed class HeadsetWorkspace : MonoBehaviour
    {
        EEGAvatarController controller; WorkspaceUI owner; GuidedAcquisition acquisition;
        Font font; Sprite round; Text state, title, body, fixation, collectionState;
        Button reset, calibrateHeight, placementButton, newPlacementButton; PlacementCalibration placement;
        readonly Button[] tabs = new Button[4], actions = new Button[3];
        readonly string[] pages = { "experience", "link", "settings", "collection" };
        public void Initialize(WorkspaceUI ui, EEGAvatarController avatar, Font textFont, Sprite rounded)
        {
            owner = ui; controller = avatar; acquisition = avatar.Acquisition; font = textFont; round = rounded;
            placement=controller.GetComponent<PlacementCalibration>();if(!placement)placement=controller.gameObject.AddComponent<PlacementCalibration>();
            var header = Panel(transform, "按住标题拖动", 32, -10, 976, 60); header.GetComponent<Image>().raycastTarget = true;
            var drag = header.gameObject.AddComponent<HeadsetPanelDrag>(); drag.Panel = transform.parent;
            var rig = owner.GetComponent<SpatialWorkspaceRig>(); drag.Head = rig ? rig.Head : null;
            Label(transform, "EEGMovement · 拖动标题栏", 30, 46, -16, 460, 48, true);
            state = Label(transform, "等待中枢", 24, 510, -18, 310, 44);
            Action(transform, "隐藏 ×", 840, -12, 168, 56, owner.HidePanel);
            string[] names = { "体验", "链路", "设置", "脑电采集" };
            for (int i = 0; i < 4; i++) { string page = pages[i]; tabs[i] = Action(transform, names[i], 32 + i * 246, -82, 238, 74, () => Navigate(page)); }
            var card = Panel(transform, "Readable headset content", 32, -176, 976, 390);
            title = Label(card, "", 38, 28, -16, 920, 62, true);
            body = Label(card, "", 30, 28, -86, 920, 180);
            fixation = Label(card, "+", 76, 380, -160, 210, 84); fixation.alignment = TextAnchor.MiddleCenter;
            collectionState = Label(card, "", 24, 28, -250, 920, 32);
            for (int i = 0; i < 3; i++) { int index = i; actions[i] = Action(card, "", 28 + i * 310, -288, 300, 76, () => Run(index)); }
            calibrateHeight = Action(card, "校准视高 · 1.65m", 28, -194, 400, 70, () => { var currentRig = owner.GetComponent<SpatialWorkspaceRig>(); if (currentRig) currentRig.CalibrateViewHeight(); });
            placementButton=Action(card,"开始实时推理",28,-194,440,70,()=>placement.StartNormal());
            newPlacementButton=Action(card,"可选模型校验 · 1分钟",490,-194,440,70,()=>{if(placement.State!=null&&placement.State.active)placement.Cancel();else placement.BeginDirections();});
            Action(transform, "重新居中", 32, -592, 220, 92, owner.Recenter);
            Action(transform, "重新连接", 270, -592, 220, 92, () => controller.Reconnect());
            reset = Action(transform, "手动复位", 508, -592, 220, 92, () => controller.ResetEmergency());
            var stop = Action(transform, "立即急停", 746, -592, 262, 92, () => controller.EmergencyStop());
            stop.GetComponent<SpatialButtonFeedback>().BaseColor = new Color(1, .73f, .74f);
            stop.gameObject.AddComponent<ImmediateStopPress>().controller = controller;
            Label(transform, "A开关设置 · 按住B监控 · 长按Meta重置视角", 23, 36, -710, 970, 38);
        }
        RectTransform Panel(Transform parent, string name, float x, float y, float w, float h)
        {
            var go = new GameObject(name, typeof(RectTransform), typeof(Image)); go.transform.SetParent(parent, false);
            var rect = go.GetComponent<RectTransform>(); rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0, 1); rect.anchoredPosition = new Vector2(x, y); rect.sizeDelta = new Vector2(w, h);
            var image = go.GetComponent<Image>(); image.sprite = round; image.type = Image.Type.Sliced; image.color = new Color(.965f, .98f, .99f, .99f); image.raycastTarget = false; return rect;
        }
        Text Label(Transform parent, string value, int size, float x, float y, float w, float h, bool bold = false)
        {
            var go = new GameObject("Readable text", typeof(RectTransform), typeof(Text)); go.transform.SetParent(parent, false);
            var r = go.GetComponent<RectTransform>(); r.anchorMin = r.anchorMax = r.pivot = new Vector2(0, 1); r.anchoredPosition = new Vector2(x, y); r.sizeDelta = new Vector2(w, h);
            var t = go.GetComponent<Text>(); t.font = font; t.fontSize = size; t.text = value; t.fontStyle = bold ? FontStyle.Bold : FontStyle.Normal; t.color = new Color(.055f, .10f, .15f); t.raycastTarget = false; t.supportRichText = false; return t;
        }
        Button Action(Transform parent, string value, float x, float y, float w, float h, UnityAction callback)
        {
            var r = Panel(parent, value, x, y, w, h); r.GetComponent<Image>().raycastTarget = true;
            var b = r.gameObject.AddComponent<Button>(); b.onClick.AddListener(callback); r.gameObject.AddComponent<SpatialButtonFeedback>();
            var text = Label(r, value, 30, 10, 0, w - 20, h); text.alignment = TextAnchor.MiddleCenter; return b;
        }
        void Navigate(string page) { if (page != "collection" && acquisition) acquisition.Abort("page_exit"); controller.Navigate(page); }
        void Run(int index)
        {
            switch (controller.Workspace.Page)
            {
                case "collection": if (index == 0) controller.SelfPaced.Begin(); else if (index == 1) acquisition.Begin(2); else {acquisition.Abort();controller.SelfPaced.Finish();} break;
                case "link": if (index == 0) controller.AllowTests(!controller.Workspace.AllowTests); else if (index == 1) controller.Reconnect(); else if(!QuestNetwork.Ready){if(QuestNetwork.EnrollmentRunning)QuestNetwork.CancelEnrollment();else QuestNetwork.RetryConnection();}else if(QuestUpdater.Instance)QuestUpdater.Instance.Confirm(); break;
                case "settings": if (index == 0) controller.ReduceMotion = !controller.ReduceMotion; else if (index == 1) controller.ReduceTransparency = !controller.ReduceTransparency; else controller.LargeText = !controller.LargeText; controller.SavePreferences(); break;
                default: if(index==0)controller.ToggleModelMotion();else Navigate(index==1?"collection":"settings");break;
            }
        }
        void Update()
        {
            if (!controller) return;
            string page = controller.Workspace.Page; var s = controller.State; bool fresh = s != null && s.paired && s.command_age_ms >= 0 && s.command_age_ms <= 180;
            state.text = controller.Workspace.EmergencyLatched ? "已急停 · 保持锁止" : fresh ? "中枢已连接 · LIVE" : "中枢连接待确认";
            if (fresh && !controller.Workspace.EmergencyLatched && s.reason == "eeg_stream_stale") state.text = "已连接 · 脑电待连接";
            for (int i = 0; i < 4; i++) tabs[i].GetComponent<SpatialButtonFeedback>().Selected = page == pages[i];
            fixation.gameObject.SetActive(false);
            collectionState.gameObject.SetActive(page == "collection");
            calibrateHeight.gameObject.SetActive(page == "settings");
            placementButton.gameObject.SetActive(page=="experience");newPlacementButton.gameObject.SetActive(page=="experience");
            if(page=="experience"){placementButton.GetComponentInChildren<Text>().text="开始实时推理";newPlacementButton.GetComponentInChildren<Text>().text=placement.State!=null&&placement.State.active?"结束并保存校验":"可选模型校验 · 1分钟";}
            reset.interactable = controller.Workspace.EmergencyLatched && !acquisition.Busy && !acquisition.Active;
            string[] labels;
            for (int i = 0; i < 3; i++) actions[i].interactable = true;
            body.fontSize = controller.LargeText ? 32 : 30;
            if (page == "collection")
            {
                title.text = "脑电采集 · 自主 / 引导"; body.text = "自主模式：十字五键 + 四角组合，按住标注。\n时长由你决定，结束后保存在电脑中枢。";
                body.rectTransform.sizeDelta = new Vector2(920, 72); fixation.text = acquisition.Fixation;
                collectionState.text = fresh && s.reason == "eeg_stream_stale" ? "电脑尚未收到脑电，请检查原厂共享采集会话与独立采集端。" : acquisition.Status;
                labels = new[] { "自主持续采集", "白底引导约13分", "停止并保存" };
                bool ready=!acquisition.Active&&!acquisition.Busy&&!controller.SelfPaced.Active&&!controller.SelfPaced.Busy&&fresh&&s.reason!="eeg_stream_stale";
                actions[0].interactable=actions[1].interactable=ready;actions[2].interactable=acquisition.Active||acquisition.Busy||controller.SelfPaced.Active||controller.SelfPaced.Busy;
            }
            else
            {
                body.rectTransform.sizeDelta = new Vector2(920, 180);
                if (page == "link") { title.text = "链路与执行"; body.text = fresh ? "中枢正常 · 指令年龄 " + s.command_age_ms.ToString("F0") + " ms\n实际速度 " + s.actual_speed.ToString("F2") + " m/s · 转向 " + s.actual_yaw_rate.ToString("F1") + " °/s\nTEST仅为合成联调，不代表脑电解码" : (QuestNetwork.Issue??"等待中枢或新鲜指令。\n请检查电脑中枢与Wi-Fi。"); labels = new[] { controller.Workspace.AllowTests ? "撤销TEST" : "允许TEST", "重新连接", QuestNetwork.Ready?"软件更新":QuestNetwork.EnrollmentRunning?"取消自动连接":"重试自动连接" }; body.text += "\n" + QuestUpdater.Status; actions[0].interactable = !controller.Workspace.EmergencyLatched; }
                else if (page == "settings") { title.text = "视角与阅读"; body.rectTransform.sizeDelta = new Vector2(920, 100); body.text = "坐姿视高基准1.65m，仍保留真实转头和俯身。\n佩戴姿势改变后，可在下方重新校准。"; labels = new[] { "减少动态 " + (controller.ReduceMotion ? "开" : "关"), "减少透明 " + (controller.ReduceTransparency ? "开" : "关"), "大字 " + (controller.LargeText ? "开" : "关") }; }
                else { title.text = "运动反馈体验 · 实验模型"; body.rectTransform.sizeDelta=new Vector2(920,100);body.fontSize=24;body.text = placement.Message+"\n"+controller.ModelStatus;labels=new[]{"开关模型动作","自主采集","体验设置"}; }
            }
            for (int i = 0; i < 3; i++) actions[i].GetComponentInChildren<Text>().text = labels[i];
        }
    }
}
